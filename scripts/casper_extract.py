#!/usr/bin/env python3
"""資料ファイル → テキスト抽出 (PDF/xlsx/テキスト/画像)。chat_server の取り込みで使用。"""
import os, re, subprocess, zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX = 20000


def _si_text(si):
    """sharedStrings の <si> から本文のみ抽出 (ルビ <rPh> は除外)。"""
    parts = []
    for r in si.findall(f"{NS}r"):          # rich-text run。<t> は本文、<rPh>/<t> はルビ
        for t in r.findall(f"{NS}t"):
            parts.append(t.text or "")
    for t in si.findall(f"{NS}t"):          # 装飾なしセル
        parts.append(t.text or "")
    return "".join(parts)


def _xlsx(path):
    try:
        z = zipfile.ZipFile(path)
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
                shared.append(_si_text(si))
        out = []
        for sp in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n)):
            for row in ET.fromstring(z.read(sp)).iter(f"{NS}row"):
                cells = []
                for c in row.findall(f"{NS}c"):
                    v = c.find(f"{NS}v"); val = v.text if v is not None else ""
                    if c.get("t") == "s" and val:
                        val = shared[int(val)]
                    cells.append(str(val or ""))
                if any(cells):
                    out.append("\t".join(cells))
        return "\n".join(out) or "(xlsx: 空)"
    except Exception as e:
        return f"(xlsx抽出失敗: {e})"


def _ooxml(path, part_re, tag):
    """OOXML(pptx/docx) から本文テキストを抽出 (zip+XML・依存ゼロ)。"""
    try:
        z = zipfile.ZipFile(path)
        parts = sorted(n for n in z.namelist() if re.match(part_re, n))
        out = []
        for p in parts:
            try:
                for t in ET.fromstring(z.read(p)).iter(tag):
                    if t.text and t.text.strip():
                        out.append(t.text)
            except Exception:
                pass
        return " ".join(out)
    except Exception as e:
        return f"(抽出失敗: {e})"


def _html(path):
    import html as _h
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    txt = _h.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", txt)).strip()


def _col_idx(ref):
    """セル参照 'C12' -> 列0始まり index (2)。"""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(z):
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append(_si_text(si))
    return shared


def _grid_of(z, shared, sheet_path):
    grid = []
    for row in ET.fromstring(z.read(sheet_path)).iter(f"{NS}row"):
        cells = {}
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v")
            val = v.text if v is not None else ""
            if c.get("t") == "s" and val not in (None, ""):
                val = shared[int(val)]
            elif c.get("t") == "inlineStr":
                isn = c.find(f"{NS}is")
                val = _si_text(isn) if isn is not None else ""
            val = re.sub(r"\s*\n\s*", " ", str(val or "")).strip()
            cells[_col_idx(c.get("r", "A"))] = val
        width = (max(cells) + 1) if cells else 0
        grid.append([cells.get(i, "") for i in range(width)])
    return grid


_RELNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def xlsx_sheets(path):
    """ブックの全シートを [(tab名, grid), ...] でタブ順に返す。"""
    z = zipfile.ZipFile(path)
    shared = _shared_strings(z)
    out = []
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"',
                               z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")))
        for s in wb.iter(f"{NS}sheet"):
            tgt = rels.get(s.get(f"{_RELNS}id"), "")
            sp = "xl/" + tgt.replace("../", "") if tgt else ""
            if sp and sp in z.namelist():
                out.append((s.get("name") or sp, _grid_of(z, shared, sp), sp))
    except Exception:
        pass
    if not out:                                   # フォールバック: sheetN を番号順
        for sp in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n)):
            out.append((sp.split("/")[-1], _grid_of(z, shared, sp), sp))
    return out


def xlsx_grid(path):
    """xlsx 1枚目シートのグリッド (後方互換)。"""
    sh = xlsx_sheets(path)
    return sh[0][1] if sh else []


def _sheet_images(z, sheet_path, out_dir, prefix):
    """指定シートに紐づく drawing から {row: filename} を返す (シート別に正しく対応)。"""
    names = z.namelist()
    srels = sheet_path.replace("worksheets/", "worksheets/_rels/") + ".rels"
    if srels not in names:
        return {}
    smap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', z.read(srels).decode("utf-8", "replace")))
    dm = re.search(r'<drawing[^>]*r:id="(rId\d+)"', z.read(sheet_path).decode("utf-8", "replace"))
    if not dm:
        return {}
    dtgt = smap.get(dm.group(1))
    dpath = "xl/" + dtgt.replace("../", "") if dtgt else ""
    if not dpath or dpath not in names:
        return {}
    drel = dpath.replace("drawings/", "drawings/_rels/") + ".rels"
    relmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"',
                  z.read(drel).decode("utf-8", "replace"))) if drel in names else {}
    row2img = {}
    for a in re.findall(r"<xdr:(?:two|one)CellAnchor.*?</xdr:(?:two|one)CellAnchor>",
                        z.read(dpath).decode("utf-8", "replace"), re.S):
        mr = re.search(r"<xdr:from>.*?<xdr:row>(\d+)</xdr:row>", a, re.S)
        me = re.search(r'embed="(rId\d+)"', a)
        if not (mr and me) or int(mr.group(1)) in row2img:
            continue
        tgt = relmap.get(me.group(1))
        media = "xl/" + tgt.replace("../", "") if tgt else None
        if not media or media not in names:
            continue
        fn = f"{prefix}_r{mr.group(1)}{os.path.splitext(media)[1].lower()}"
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(z.read(media))
        row2img[int(mr.group(1))] = fn
    return row2img


def xlsx_images(path, out_dir, prefix):
    """xlsx 埋め込み画像を out_dir に保存し {row(0-based): filename} を返す。
    drawing アンカーの from-row で行に対応づけ (1行に複数あれば最初=カット画像列を優先)。"""
    os.makedirs(out_dir, exist_ok=True)
    z = zipfile.ZipFile(path)
    names = z.namelist()
    row2img = {}
    for drel in [n for n in names if re.match(r"xl/drawings/_rels/drawing\d+\.xml\.rels", n)]:
        relmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', z.read(drel).decode("utf-8", "replace")))
        dname = drel.replace("_rels/", "").replace(".rels", "")
        if dname not in names:
            continue
        x = z.read(dname).decode("utf-8", "replace")
        for a in re.findall(r"<xdr:(?:two|one)CellAnchor.*?</xdr:(?:two|one)CellAnchor>", x, re.S):
            mr = re.search(r"<xdr:from>.*?<xdr:row>(\d+)</xdr:row>", a, re.S)
            me = re.search(r'embed="(rId\d+)"', a)
            if not (mr and me):
                continue
            row = int(mr.group(1))
            if row in row2img:
                continue
            tgt = relmap.get(me.group(1))
            media = "xl/" + tgt.replace("../", "") if tgt else None
            if not media or media not in names:
                continue
            ext = os.path.splitext(media)[1].lower()
            fn = f"{prefix}_r{row}{ext}"
            with open(os.path.join(out_dir, fn), "wb") as f:
                f.write(z.read(media))
            row2img[row] = fn
    return row2img


def _norm_h(c):
    return c.strip().lower().replace(" ", "").replace("　", "")


def _detect_header(grid):
    """ヘッダ行 index を推定: 先頭10行で『短いラベルが最も多く埋まる行』。"""
    best_i, best = None, 1
    for i, r in enumerate(grid[:10]):
        labels = [c for c in r if c.strip() and len(c) <= 24]
        if len(labels) > best:
            best_i, best = i, len(labels)
    return best_i


def _grid_to_md(grid, imgs, asset_url):
    """1グリッド -> markdown 表 (原ヘッダ保持・空列/残骸行除外・画像はサムネ列)。"""
    if not grid:
        return None
    hdr_i = _detect_header(grid)
    if hdr_i is None:
        return None
    hdr, data = grid[hdr_i], grid[hdr_i + 1:]
    ncol = max((len(r) for r in [hdr] + data), default=0)
    imgph = {_norm_h(x) for x in ("thumnail", "thumbnail", "カットイメージ", "画像", "サムネ", "サムネイル",
             "image", "サムネイル枠")}
    ndata = max(len(data), 1)
    keep = []
    for ci in range(ncol):
        name = (hdr[ci] if ci < len(hdr) else "").strip()
        fill = sum(1 for r in data if ci < len(r) and r[ci].strip()) / ndata
        if _norm_h(name) in imgph or fill == 0:
            continue
        if name:                                      # 名前付き列は中身があれば採用
            keep.append((name, ci))
        elif fill >= 0.3:                             # 無名列は十分埋まっている時のみ (Gantt 空列ノイズ除去)
            keep.append((f"列{ci + 1}", ci))
    # 実リストでないシート(名前付き列が乏しい=Gantt/カレンダー)はスキップ
    named = [n for n, _ in keep if not n.startswith("列")]
    if len(named) < 2:
        return None
    # 曜日/数字ばかりの見出し=カレンダー(Gantt) はスキップ
    wd = {"sat", "sun", "mon", "tue", "wed", "thu", "fri", "月", "火", "水", "木", "金", "土", "日"}
    cal = sum(1 for n in named if _norm_h(n) in wd or n.isdigit())
    if cal >= max(3, len(named) * 0.5):
        return None
    if not keep:
        return None
    has_img = bool(imgs)
    labels = [n for n, _ in keep] + (["画像"] if has_img else [])
    out = ["| " + " | ".join(labels) + " |", "|" + "---|" * len(labels)]
    n = 0
    for ri in range(hdr_i + 1, len(grid)):
        row = grid[ri]

        def g(ci):
            return row[ci].replace("|", "／").replace("\n", " ") if ci < len(row) else ""
        vals = [g(ci) for _, ci in keep]
        img = f"![]({asset_url}/{imgs[ri]})" if ri in imgs else ""
        if has_img:
            vals.append(img)
        content = any(v and len(v) >= 3 and not
                      v.replace(".", "").replace(":", "").replace("~", "").replace("-", "").isdigit()
                      for v in (vals[:-1] if has_img else vals))
        if not (img or content):
            continue
        out.append("| " + " | ".join(vals) + " |")
        n += 1
    return "\n".join(out) if n >= 1 else None


def xlsx_to_markdown(path, assets_dir=None, prefix="img", asset_url="/asset"):
    """汎用: どんなリスト xlsx でも markdown 表へ。**全シート対応**(各シートを見出し付き表に)。
    ショットリスト/AnimList/香盤表/スケジュール等、PJ毎に列もシートも違っても吸収。"""
    z = zipfile.ZipFile(path)
    sheets = xlsx_sheets(path)
    parts = []
    for si, (name, grid, sp) in enumerate(sheets):
        imgs = _sheet_images(z, sp, assets_dir, f"{prefix}_s{si}") if assets_dir else {}
        md = _grid_to_md(grid, imgs, asset_url)
        if not md:
            continue
        if len(sheets) > 1:
            parts.append(f"### {name}\n\n{md}")
        else:
            parts.append(md)
    return "\n\n".join(parts) if parts else None


def list_to_markdown(path, assets_dir=None, prefix="img", asset_url="/asset"):
    """リスト xlsx を表化。ショットリスト(cut/番号列を持つ)なら整形版、それ以外は汎用版。"""
    try:
        hdr = xlsx_grid(path)
        hdr = next((r for r in hdr[:10] if any(_norm_h(c) in {"cut", "カット", "番号", "no.", "no", "sl no."} for c in r)), None)
    except Exception:
        hdr = None
    if hdr is not None:
        md = shotlist_to_markdown(path, assets_dir or ".", prefix, asset_url)
        if md:
            return md
    return xlsx_to_markdown(path, assets_dir, prefix, asset_url)


# 列見出し別名 (日英)。完全一致(小文字/空白除去)で照合し誤検出を防ぐ。
_SHOT_COLS = [
    ("No",      {"番号", "no", "no.", "sl no.", "cut no."}),
    ("カット",   {"cut", "カット", "カット番号", "cut id"}),
    ("秒数",     {"秒数", "duration", "second", "尺"}),
    ("アクション", {"action", "アクション", "内容", "カット内容"}),
    ("セリフ/ナレ", {"dialogue", "セリフ", "ナレーション", "日本語ナレーション", "narration"}),
    ("備考",     {"備考", "note", "メモ", "テキスト", "日本語テキスト"}),
]


def shotlist_to_markdown(path, assets_dir, prefix, asset_url="/asset"):
    """ショットリスト/カット表 xlsx -> サムネ付き markdown 表 (日英ヘッダ対応)。
    ヘッダ行を検出し、主要列(No/カット/秒数/アクション/セリフ/備考のうち在るもの)+各カット画像で表化。"""
    grid = xlsx_grid(path)
    imgs = xlsx_images(path, assets_dir, prefix)

    def norm(c):
        return c.strip().lower().replace(" ", "").replace("　", "")
    aliases = {a for _, s in _SHOT_COLS for a in s}
    anorm = {norm(a) for a in aliases}
    # ヘッダ行 = 別名に一致するセルが最も多い行 (cut/カット/番号 を含むこと)
    hdr_i, best = None, 0
    for i, r in enumerate(grid[:8]):
        hit = sum(1 for c in r if norm(c) in anorm)
        has_cut = any(norm(c) in {"cut", "カット", "番号", "no.", "no"} for c in r)
        if has_cut and hit > best:
            hdr_i, best = i, hit
    if hdr_i is None:
        return None
    hdr = grid[hdr_i]

    data = grid[hdr_i + 1:]

    def find(keyset):
        ks = {norm(k) for k in keyset}
        cands = [i for i, c in enumerate(hdr) if norm(c) in ks]
        if not cands:
            return None
        # 同名候補が複数なら『データが最も埋まっている列』を選ぶ(空のラベル列を避ける)
        return max(cands, key=lambda i: sum(1 for r in data if i < len(r) and r[i].strip()))
    chosen = [(label, find(keys)) for label, keys in _SHOT_COLS]
    chosen = [(l, i) for l, i in chosen if i is not None]
    chosen.sort(key=lambda x: x[1])                       # スプレッドシート列順
    ids = [c for c in chosen if c[0] in ("No", "カット")]  # 識別子は先頭、画像はその直後
    rest = [c for c in chosen if c[0] not in ("No", "カット")]
    ordered = ids + [("画像", None)] + rest

    labels = [l for l, _ in ordered]
    head = "| " + " | ".join(labels) + " |"
    sep = "|" + "---|" * len(labels)
    out, n = [head, sep], 0
    for ri in range(hdr_i + 1, len(grid)):
        row = grid[ri]
        img = f"![]({asset_url}/{imgs[ri]})" if ri in imgs else ""

        def g(ci):
            if ci is None:
                return img
            return row[ci].replace("|", "／").replace("\n", " ") if ci < len(row) else ""
        vals = [g(i) for _, i in ordered]
        # 実カット行のみ: 画像か4字以上の非数値テキスト (フッタ/合計の残骸を除外)
        content = any(v and len(v) >= 4 and not v.replace(".", "").replace(":", "").replace("~", "").isdigit()
                      for (l, i), v in zip(ordered, vals) if l != "画像")
        if not (img or content):
            continue
        out.append("| " + " | ".join(vals) + " |")
        n += 1
    return "\n".join(out) if n else None


def _rtf(path):
    r"""RTF → 素のテキスト(依存ゼロ)。

    【殿御下命2026-08-26】kiyotomo殿が「sorafune 様　MTG.rtf」を投じ「Auroraにアップ」と
    書き添えたが、.rtf は本表に載っておらず「(非対応形式)」で落ちていた。macOS/テキストエディットの
    既定書式ゆえ社内で日常的に出る。

    ★破棄すべき群(fonttbl/colortbl/stylesheet/info/pict 等)は本文でないゆえ中身ごと捨てる。
      これを捨てぬと書体名や色定義が『資料の本文』として Aurora に載る。
    ★日本語は \'xx の連なり(cp932)か \uNNNN で来る。前者は \ansicpg を見て復号する。
      復号に失敗した文字は握り潰さず replace で残す(読めた分は返す=失敗とゼロを別出口へ)。
    """
    try:
        raw = open(path, "rb").read().decode("latin-1")
    except Exception as e:
        return "(rtf読取失敗: %s)" % e
    m = re.search(r"\\ansicpg(\d+)", raw)          # 既定 cp1252・日本語RTFは cp932 が多い
    codec = "cp" + m.group(1) if m else "cp1252"
    try:
        "".encode(codec)
    except Exception:
        codec = "cp1252"

    SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
            "colorschememapping", "latentstyles", "datastore", "generator", "listtable",
            "listoverridetable", "rsidtbl", "xmlnstbl", "filetbl", "revtbl", "mmathPr"}
    out, buf = [], bytearray()
    depth, skip_to = 0, None                       # skip_to = 破棄群に入った時の深さ

    def flush():
        if buf:
            out.append(bytes(buf).decode(codec, errors="replace"))
            buf.clear()

    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "{":
            flush(); depth += 1; i += 1; continue
        if c == "}":
            flush()
            if skip_to is not None and depth <= skip_to:
                skip_to = None
            depth -= 1; i += 1; continue
        if c == "\\":
            if raw.startswith("\\'", i) and i + 3 < n:          # \'xx = 16進1バイト
                try:
                    if skip_to is None:
                        buf.append(int(raw[i + 2:i + 4], 16))
                    i += 4; continue
                except ValueError:
                    i += 2; continue
            if raw.startswith("\\*", i):                          # \* = 「解さぬなら群ごと捨てよ」の印
                # ★RTF仕様の無視可能destination。expandedcolortbl 等を一つずつ表に足して
                #   追いかけると必ず取りこぼす(実測: 『*;;;』が本文の先頭に混じった)。
                #   語を数えるのでなく、印を見て群ごと捨てる。
                skip_to = depth
                i += 2; continue
            if i + 1 < n and raw[i + 1] in "\r\n":                 # \+改行 = 段落区切り(TextEditの既定)
                flush(); out.append("\n"); i += 2
                while i < n and raw[i] in "\r\n":
                    i += 1
                continue
            m2 = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", raw[i:])
            if not m2:                                          # \{ \} \\ のエスケープ
                if i + 1 < n and raw[i + 1] in "{}\\":
                    if skip_to is None:
                        buf.append(ord(raw[i + 1]))
                    i += 2; continue
                i += 1; continue
            word, param = m2.group(1), m2.group(2)
            i += m2.end()
            if word in SKIP:                                    # 破棄群 → 閉じ括弧まで丸ごと捨てる
                skip_to = depth
                continue
            if skip_to is not None:
                continue
            if word == "u" and param is not None:               # \uNNNN(直後の代替1文字は読み飛ばす)
                flush()
                cp = int(param)
                out.append(chr(cp if cp >= 0 else cp + 65536))
                if i < n and raw[i] == "?":
                    i += 1
                continue
            if word in ("par", "line", "sect"):
                flush(); out.append("\n"); continue
            if word == "tab":
                flush(); out.append("\t"); continue
            continue
        if skip_to is None and c not in "\r\n":
            buf.append(ord(c) if ord(c) < 256 else 63)
        i += 1
    flush()
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pptx":
        return _ooxml(path, r"ppt/slides/slide\d+\.xml",
                      "{http://schemas.openxmlformats.org/drawingml/2006/main}t")[:MAX] or "(pptx: 空)"
    if ext == ".docx":
        return _ooxml(path, r"word/document\.xml",
                      "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")[:MAX] or "(docx: 空)"
    if ext in (".html", ".htm"):
        return _html(path)[:MAX] or "(html: 空)"
    if ext == ".pdf":
        if os.path.exists(VENV_PY):
            code = "import fitz,sys;d=fitz.open(sys.argv[1]);print(chr(10).join(p.get_text() for p in d))"
            try:
                r = subprocess.run([VENV_PY, "-c", code, path], capture_output=True, text=True, timeout=120)
                return (r.stdout or "").strip()[:MAX] or "(PDF: テキスト無し=画像PDFの可能性)"
            except Exception as e:
                return f"(PDF抽出失敗: {e})"
        return "(PDF抽出環境なし)"
    if ext == ".xlsx":
        try:                                  # 汎用: どんなリストも見やすい markdown 表に (画像は別経路で付与)
            md = xlsx_to_markdown(path)
            if md:
                return md[:MAX]
        except Exception:
            pass
        return _xlsx(path)[:MAX]
    if ext == ".rtf":
        return _rtf(path)[:MAX] or "(rtf: 空)"
    if ext in (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".tsv"):
        try:
            return open(path, encoding="utf-8", errors="replace").read()[:MAX]
        except Exception as e:
            return f"(読取失敗: {e})"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return "(画像ファイル: 自動テキスト抽出不可。説明文と確認質問で内容を把握する)"
    return f"(非対応形式 {ext}: 説明文で判断)"
