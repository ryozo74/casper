#!/usr/bin/env python3
"""Casper embedding RAG — vault を意味ベクトル検索(bge-m3 / Ollama)。
既存 casper_rag(字面トライグラム) を壊さず上に被せるハイブリッド。
- bge-m3 が z8a に在れば意味検索、無ければ自動で字面検索にフォールバック(pull前でも動く)。
- ベクトル索引は casper_embed_index.json に保存。

CLI:
  python3 casper_embed.py build          # 全chunkをベクトル化(要 bge-m3)
  python3 casper_embed.py search "<q>"   # 意味検索テスト
モジュール:
  import casper_embed; casper_embed.search(query, k=8) -> [str,...]
  casper_embed.available() -> bool       # 埋め込み索引が使えるか
"""
import json
import math
import os
import sys
import urllib.request

import casper_rag

HERE = os.path.dirname(os.path.abspath(__file__))
EMB_INDEX = os.path.join(HERE, "casper_embed_index.json")
OLLAMA = os.environ.get("CASPER_EMBED_ENDPOINT",
                        os.environ.get("CASPER_OLLAMA", "http://192.168.44.119:11434")).rstrip("/")
MODEL = os.environ.get("CASPER_EMBED_MODEL", "bge-m3")
_VEC = None      # [{src,title,t,v:[float]}]


def embed_one(text):
    """1テキストを埋め込みベクトル化。失敗(モデル無し等)で None。"""
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/embeddings",
            data=json.dumps({"model": MODEL, "prompt": text[:2000]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        v = d.get("embedding")
        return v if v else None
    except Exception:
        return None


def embed_batch(texts):
    """複数テキストをバッチ埋め込み(/api/embed)。[[float],...] or None。"""
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/embed",
            data=json.dumps({"model": MODEL, "input": [t[:2000] for t in texts]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
        return d.get("embeddings")
    except Exception:
        return None


def build(batch=32):
    """casper_rag の全chunkをバッチでベクトル化して保存。"""
    if casper_rag._CACHE is None:
        casper_rag._CACHE = json.load(open(casper_rag.INDEX, encoding="utf-8")) \
            if os.path.exists(casper_rag.INDEX) else []
    chunks = casper_rag._CACHE
    out = []
    for i in range(0, len(chunks), batch):
        grp = chunks[i:i + batch]
        vecs = embed_batch([e["t"] for e in grp])
        if not vecs:
            print("  バッチ埋め込み失敗(モデル未導入?)。中断。", file=sys.stderr)
            return 0
        for e, v in zip(grp, vecs):
            out.append({"src": e["src"], "title": e.get("title", ""), "t": e["t"], "v": v})
        if (i + batch) % 320 == 0 or i + batch >= len(chunks):
            print(f"  {min(i+batch,len(chunks))}/{len(chunks)} ...", flush=True)
    json.dump(out, open(EMB_INDEX, "w", encoding="utf-8"), ensure_ascii=False)
    return len(out)


def _load():
    global _VEC
    if _VEC is None:
        _VEC = json.load(open(EMB_INDEX, encoding="utf-8")) if os.path.exists(EMB_INDEX) else []
    return _VEC


def available():
    return bool(_load())


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


def search(query, k=8, budget=3800):
    """意味検索。埋め込み索引が無ければ casper_rag(字面)にフォールバック。"""
    vec = _load()
    if not vec:
        return casper_rag.search(query, k=k, budget=budget)
    qv = embed_one(query)
    if qv is None:
        return casper_rag.search(query, k=k, budget=budget)
    scored = sorted(((  _cos(qv, e["v"]), e) for e in vec), key=lambda x: -x[0])
    res, used, seen = [], 0, set()
    for sc, e in scored:
        norm = e["t"][:80]
        if norm in seen:
            continue
        seen.add(norm)
        line = f"[{e.get('title') or e['src']}] {e['t']}"
        if used + len(line) > budget:
            break
        res.append(line)
        used += len(line)
        if len(res) >= k:
            break
    return res


def hybrid(query, k=8, budget=3800):
    """意味検索 + 字面検索を融合(両方の上位を混ぜて重複除去)。"""
    sem = search(query, k=k, budget=budget // 2) if available() else []
    lex = casper_rag.search(query, k=k, budget=budget // 2)
    out, seen = [], set()
    for line in sem + lex:
        key = line[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= k:
            break
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        print("embedded:", build(), "->", EMB_INDEX)
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        for r in search(sys.argv[2]):
            print(" •", r[:160])
    else:
        print("available:", available(), "/", __doc__)
