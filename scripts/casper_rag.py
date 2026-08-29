#!/usr/bin/env python3
"""Casper RAG — vault 全文を依存ゼロで検索 (文字トライグラム + 語一致)。
埋め込みモデル不要。日本語/英語混在に対応。

CLI:
  python3 casper_rag.py index            # インデックス構築
  python3 casper_rag.py search "<query>" # 検索テスト
モジュール:
  import casper_rag; casper_rag.search(query, k=8) -> [str,...]
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(os.path.join(HERE, "..", "vault"))
INDEX = os.path.join(HERE, "casper_rag_index.json")
_CACHE = None


def _chunks(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    title = ""
    m = re.search(r"^(?:project|name):\s*(.+)$", txt, re.M)
    if m:
        title = m.group(1).strip()
    out, infence = [], False
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("```"):
            infence = not infence
            continue
        if infence or s.startswith("---"):
            continue
        s = re.sub(r"^[-*#>\s]+", "", s)        # markdown 記号除去
        if len(s) >= 10:
            out.append(s)
    return title, out


def build_index():
    idx = []
    for p in sorted(glob.glob(os.path.join(VAULT, "**", "*.md"), recursive=True)):
        if os.path.basename(p) == "README.md":
            continue
        rel = os.path.relpath(p, VAULT)
        title, cs = _chunks(p)
        for c in cs:
            idx.append({"src": rel, "title": title, "t": c})
    json.dump(idx, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False)
    return len(idx)


def _tri(s):
    s = re.sub(r"\s+", "", s.lower())
    return set(s[i:i + 3] for i in range(len(s) - 2)) if len(s) >= 3 else ({s} if s else set())


def _segs(query):
    """クエリを助詞/記号/空白で分割し、2字以上の語片を返す(日本語サブストリング照合用)。"""
    parts = re.split(r"[\sの に を は が と で も や へ から まで、。,.!?！？・/（）()\[\]【】「」]+", query)
    return [s for s in parts if len(s) >= 2]


def _seg_boost(segs, text):
    t = text.lower()
    return sum(min(len(s), 6) * 0.18 for s in segs if s.lower() in t)


# 進捗の真実源は Calendar。過去のレガシー記録(2022 DBM2/旧Score/LINEログ)は"進捗/現況"に混ぜない
# (殿指摘2026-07-13/14: LINEチャットログはPJ完了段階で入る=そのタスクはクローズ扱い。現行の停滞タスクに legacy を出すな)。
# パスに "legacy" を含む資料を除外: 80_legacy_score/ の生データ＋ persona_rnd_legacy_score.md の分析成果物 両方を捕捉
# (ファイル列挙=腐る定数を避け、機構的signal=名前の"legacy"で。人物profile(_kiyotomo等)は名にlegacyを持たず温存され、
#  正当な人物情報は残る。legacyは根拠引用として profile 内に留まり、current回答の一次には出ない)。
# 病二(鏡): Casperが自動生成する社員個性プロファイル(20_people/profile_u_*.md)は本人の発話を
# 原文引用しているため、字面検索の索引に混じると本人が同じ言葉を打つと必ず自分の調書が釣れる
# (実害2026-08-17: tetsuo発話「この内容を共有」→自身のprofile_u_30が候補上位に混入)。
# ★前方一致(startswith)ゆえ "20_people" とだけ書くと人物ノート全体(正当な人物情報)が消える。
# 必ず "20_people/profile_" と限定する(profile_*以外の20_people配下ノートは温存)。
# ★2026-08-29 復旧: この一行は cmd_508 で書かれながら commit されず、8/22 の実装消失事案で
#   失われていた(門 gate_byoni_mirror だけが復旧commitで戻り、機構は戻らず赤のままであった)。
_EXCLUDE_SRC = ("80_legacy_score", "20_people/profile_")
# legacy を主題/引用する chunk の本文マーカー。人物profile(_kiyotomo等)が legacy_score/DBM2 を"源"として
# 大量引用しRAGで拾われる漏れ(殿指摘2026-07-14)を、chunk単位で断つ。profile内の非legacy部分(役割/氏名)は残る。
# legacyの明確なマーカーのみ(Fable審査2026-07-14: 「Score(入力|記録|上)」は現行PJの正当文「Scoreに記録済み」等まで
# 常時消す危険=腐る定数ゆえ撤去)。status質問のlegacy漏れは二軸classifier(vault抑制)が担い、ここは knowledge経路で
# legacy生データ/分析成果物を落とす最小限に留める。
# cmd_490: asset_20260701.md 内「7/18 Launch」記述は陳腐化した社内メモ断片で、Casper自身の提供状況(モバイル
# 未実装/7/18ローンチ待ち)と誤読されRAGに混入した(tetsuo殿への誤案内の実害・2026-07-31)。ファイル単位除外は
# 同ファイル内の他の有用な案件記録(取引先/制作パートナー等)を巻き添えにするため、chunk単位(本文マーカー)に限定。
_EXCLUDE_TEXT_RE = re.compile(r"legacy_score|80_legacy_score|persona_rnd_legacy|旧スコア|7/18\s*Launch", re.I)


def _excluded(src, text=""):
    s = str(src or "").lstrip("./").lower()
    if "legacy" in s:                                     # legacy専用資料(生データ＋分析成果物)を一括除外
        return True
    if text and _EXCLUDE_TEXT_RE.search(str(text)):       # 本文がlegacyを引用するchunkも除外(どのファイルでも)
        return True
    return any(s == p or s.startswith(p + "/") or s.startswith(p) for p in _EXCLUDE_SRC)


def candidates(query, n=60):
    """字面recallの上位n候補チャンク(dict: src/title/t)を返す。意味再ランク(casper_embed)の入力用。
    search()と同一スコアリングだが整形せず生chunkを返す=意味検索復活の土台(Fable M2)。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else []
    qg = _tri(query)
    qtok = set(re.findall(r"[A-Za-z0-9]{2,}", query.lower()))
    segs = _segs(query)
    scored = []
    for e in _CACHE:
        if _excluded(e.get("src"), e.get("t")):    # legacy(80_legacy_score/DBM2/旧スコア引用)をsrc+本文で除外
            continue
        cg = _tri(e["t"])
        if not cg:
            continue
        sc = (len(qg & cg) / (len(qg) + 1) + sum(0.4 for t in qtok if t in e["t"].lower())
              + _seg_boost(segs, e["t"]))
        if sc > 0.02:
            scored.append((sc, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:n]]


def search(query, k=8, budget=3800):
    global _CACHE
    if _CACHE is None:
        _CACHE = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else []
    qg = _tri(query)
    qtok = set(re.findall(r"[A-Za-z0-9]{2,}", query.lower()))
    segs = _segs(query)
    scored = []
    for e in _CACHE:
        if _excluded(e.get("src"), e.get("t")):    # legacy(80_legacy_score/DBM2/旧スコア引用)をsrc+本文で除外
            continue
        cg = _tri(e["t"])
        if not cg:
            continue
        ov = len(qg & cg) / (len(qg) + 1)
        boost = sum(0.4 for t in qtok if t in e["t"].lower()) + _seg_boost(segs, e["t"])
        sc = ov + boost
        if sc > 0.02:
            scored.append((sc, e))
    scored.sort(key=lambda x: -x[0])
    res, used, seen = [], 0, set()
    for sc, e in scored:
        norm = re.sub(r"\s+", "", e["t"])[:80]
        if norm in seen:
            continue
        seen.add(norm)
        line = f"[{e['title'] or e['src']}] {e['t']}"
        if used + len(line) > budget:
            break
        res.append(line)
        used += len(line)
        if len(res) >= k:
            break
    return res


def top_source(query, threshold=0.32):
    """クエリに最も合致する『1つのノート』の全文を返す (rel, fulltext)。
    最良チャンクの max スコアで選ぶ(サイズ非依存)。断片でなくノート全体を文脈に入れるため。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else []
    qg = _tri(query)
    qtok = set(re.findall(r"[A-Za-z0-9]{2,}", query.lower()))
    segs = _segs(query)
    agg = {}
    for e in _CACHE:
        if _excluded(e.get("src"), e.get("t")):    # search()/candidates()と同じ除外(全文注入がexclude回避せぬよう)
            continue
        cg = _tri(e["t"])
        if not cg:
            continue
        sc = (len(qg & cg) / (len(qg) + 1) + sum(0.4 for t in qtok if t in e["t"].lower())
              + _seg_boost(segs, e["t"]))
        agg[e["src"]] = max(agg.get(e["src"], 0.0), sc)   # 最良チャンク (サイズ非依存)
    if not agg:
        return None, None
    best = max(agg, key=agg.get)
    if agg[best] < threshold:
        return None, None
    try:
        return best, open(os.path.join(VAULT, best), encoding="utf-8").read()
    except Exception:
        return None, None


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "index":
        print("indexed chunks:", build_index(), "->", INDEX)
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        for r in search(sys.argv[2]):
            print(" •", r[:160])
    else:
        print(__doc__)
