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

# 別名展開(TKP↔Nina 等・実データで両称が混在する為の補助)
ALIASES = {
    "tkp": ["nina"], "nina": ["tkp"],
    "コンテ": ["絵コンテ", "storyboard"], "絵コンテ": ["コンテ"],
    "動画": ["ムービー", "movie"], "ムービー": ["動画", "movie"],
}

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


def _terms(query):
    q = (query or "").lower()
    terms = [t for t in re.split(r"[\s、,　。・]+", q) if len(t) >= 2]
    exp = set(terms)
    for t in list(terms):
        for k, vs in ALIASES.items():
            if k in t:
                exp.update(vs)
    return exp


def search(query, exts=None, limit=60):
    """決定的検索: ファイル名＋説明に対する語の部分一致(別名展開込み)。該当資産を全件(上限内)返す。
    exts=('.png','.jpg') 等で種別を絞れる。返り値=[{name,path,ext,desc,md}, ...]。"""
    files = _load()
    terms = _terms(query)
    out = []
    for fn, meta in files.items():
        if exts and meta["ext"] not in exts:
            continue
        hay = (fn + " " + meta.get("desc", "")).lower()
        # 語のいずれかを含めば候補(存在確認=網羅重視ゆえ OR)
        if not terms or any(t in hay for t in terms):
            out.append(meta)
    out.sort(key=lambda m: m["name"])
    return out[:limit]


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
