#!/usr/bin/env python3
"""Casper 資産台帳(Asset Manifest) — 資料ファイルの唯一の決定的真実源。

Fable5診断(2026-07-02)の処方: 識別子(ファイル名)を LLM に"生成"させず"選択"させる基盤。
- asset_shadows/files の実ファイル＋知識md(asset影武者)のメタを走査。
- exists(): 実在確認(存在は SQL/走査の事実であって、意味検索の意見ではない)。
- search(): 決定的な部分一致＋別名展開で該当資産の"全件"を返す(網羅漏れ・誤断を防ぐ)。
- real_names(): 出口バリデータ用の実ファイル名集合。
"""
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_FILES = os.path.join(HERE, "..", "vault", "50_asset_shadows", "files")
ASSET_MD_DIR = os.path.join(HERE, "..", "vault", "50_asset_shadows")
ASSETS_DIR = os.path.join(HERE, "assets")     # 従来配信元

# 別名展開(汎用ドメイン語は engine、bokan 固有の別名は pack から差込む・Fable処方)
ALIASES = {
    "コンテ": ["絵コンテ", "storyboard"], "絵コンテ": ["コンテ"],
    "動画": ["ムービー", "movie"], "ムービー": ["動画", "movie"],
}
try:
    import pack_config
    ALIASES.update(pack_config.aliases())   # pack固有別名(PJ間の呼称ゆれ等)を pack/bokan/pack.yaml から合流
except Exception:
    pass

_CACHE = {"key": None, "files": {}, "names": set()}


def _scan():
    files = {}
    for root in (ASSET_FILES, ASSETS_DIR):
        if not os.path.isdir(root):
            continue
        for fn in os.listdir(root):
            p = os.path.join(root, fn)
            if os.path.isfile(p) and not fn.startswith("."):
                files[fn] = {"name": fn, "path": p, "root": root,
                             "ext": os.path.splitext(fn)[1].lower(), "desc": "", "md": ""}
    # 知識md(asset_*.md)の説明を該当ファイルに紐付け
    for md in glob.glob(os.path.join(ASSET_MD_DIR, "*.md")):
        try:
            txt = open(md, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        m = re.search(r"^asset:\s*(.+)$", txt, re.M)
        fn = m.group(1).strip() if m else None
        if fn and fn in files:
            dm = re.search(r"##\s*説明[^\n]*\n(.+?)(?:\n##|\Z)", txt, re.S)
            files[fn]["desc"] = (dm.group(1).strip() if dm else "")[:800]
            files[fn]["md"] = os.path.basename(md)
    return files


def _load():
    try:
        key = tuple(sorted((d, round(os.path.getmtime(d), 1))
                           for d in (ASSET_FILES, ASSET_MD_DIR, ASSETS_DIR) if os.path.isdir(d)))
    except Exception:
        key = None
    if _CACHE["key"] != key or not _CACHE["files"]:
        _CACHE["files"] = _scan()
        _CACHE["names"] = set(_CACHE["files"].keys())
        _CACHE["key"] = key
    return _CACHE["files"]


def real_names():
    """実在するファイル名(basename)の集合。出口バリデータ用。"""
    _load()
    return set(_CACHE["names"])


def exists(filename):
    """実ファイルか(basename一致・決定的)。"""
    return os.path.basename(filename or "") in _load()


# 汎用語(意図語・種別語・助詞)は識別性ゼロゆえ検索語から除く(これらで全件マッチする雑音を防ぐ)
_STOP = {
    "ある", "あります", "ありますか", "ありませんか", "ない", "無い", "どこ", "見せて", "見せ", "見たい",
    "探して", "探し", "教えて", "教え", "欲しい", "ほしい", "全部", "全て", "すべて", "これ", "それ",
    "この", "その", "です", "ます", "など", "とか", "って", "一覧", "リスト", "情報", "もの", "こと",
    "資料", "画像", "動画", "データ", "ファイル", "素材", "静止画", "映像", "写真", "ドキュメント", "議事録",
    "について", "関する", "たい", "して", "した", "から", "まで",
}


def _terms(query):
    q = (query or "").lower()
    raw = [t for t in re.split(r"[\s、,　。・？\?！!の（）()「」]+", q) if len(t) >= 2]
    terms = [t for t in raw if t not in _STOP]
    for t in list(terms):
        for k, vs in ALIASES.items():
            if k in t:
                terms.extend(vs)
    return terms or raw                                    # 全て除かれたら生語で退避


def search(query, exts=None, limit=60):
    """決定的検索: ファイル名＋説明への語の部分一致を"一致語数"でランク付け(別名展開込み)。
    汎用語は除外し distinctive な語(TKP/LED等)を重視。exts で種別を絞れる。返り値=[{name,...}]。"""
    files = _load()
    terms = _terms(query)
    scored = []
    for fn, meta in files.items():
        if exts and meta["ext"] not in exts:
            continue
        hay = (fn + " " + meta.get("desc", "")).lower()
        score = sum(1 for t in set(terms) if t in hay)
        if not terms or score > 0:
            scored.append((score, meta))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    return [m for _, m in scored][:limit]


def count(query="", exts=None):
    return len(search(query, exts=exts, limit=100000))


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "search":
        rs = search(sys.argv[2] if len(sys.argv) > 2 else "")
        print(f"{len(rs)}件:")
        for m in rs[:40]:
            print(f"  {m['name']}  — {m['desc'][:50]}")
    else:
        print("real files:", len(real_names()))
