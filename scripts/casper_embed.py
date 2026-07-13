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
import struct
import sqlite3
import sys
import urllib.request

import casper_rag

HERE = os.path.dirname(os.path.abspath(__file__))
EMB_INDEX = os.path.join(HERE, "casper_embed_index.json")
EMB_DB = os.path.join(HERE, "casper_embed.db")     # Fable M2: 411MB JSON→sqlite(候補だけ引く・26s全読込を消す)
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


def _atomic_dump(obj, path):
    """一時ファイルへ書いてから rename(=アトミック置換)。並行 reindex による半端書き込み破損を防ぐ。"""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)                                  # 同一FS内 rename はアトミック


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
    _atomic_dump(out, EMB_INDEX)
    return len(out)


def reindex():
    """差分再index(帯更新後の連動用)。
    ① トライグラム索引を全再構築(安価・字面検索は常に最新)。
    ② 埋込は未変更chunkのベクトルを再利用し、新規/変更chunkのみ再埋込(高価な全再埋込を回避)。
    ③ bge-m3 未導入なら埋込はskip(旧埋込index温存・字面索引のみ最新)。
    戻り: {"chunks":総数,"reembedded":再埋込数,"embed":状態}。"""
    import casper_rag
    casper_rag.build_index()                                   # 安価: 全トライグラム再構築
    casper_rag._CACHE = None                                   # 次の検索で再読込
    chunks = json.load(open(casper_rag.INDEX, encoding="utf-8"))
    old = {}
    if os.path.exists(EMB_INDEX):
        try:
            for e in json.load(open(EMB_INDEX, encoding="utf-8")):
                old[(e["src"], e["t"])] = e.get("v")
        except Exception:
            old = {}                                           # 破損時は空扱い→全再埋込で自己修復
    out, miss_idx, miss_txt = [], [], []
    for e in chunks:                                           # 未変更は旧ベクトル再利用
        v = old.get((e["src"], e["t"]))
        out.append({"src": e["src"], "title": e.get("title", ""), "t": e["t"], "v": v})
        if v is None:
            miss_idx.append(len(out) - 1); miss_txt.append(e["t"])
    reembedded = 0
    for i in range(0, len(miss_txt), 32):                      # 変更/新規分のみバッチ埋込
        gi, gt = miss_idx[i:i + 32], miss_txt[i:i + 32]
        vecs = embed_batch(gt)
        if not vecs:                                           # bge-m3 未導入 → 埋込skip(旧index温存)
            return {"chunks": len(chunks), "reembedded": 0, "embed": "skipped(bge-m3不在・字面索引のみ最新)"}
        for j, v in zip(gi, vecs):
            out[j]["v"] = v; reembedded += 1
    out = [e for e in out if e.get("v") is not None]
    _atomic_dump(out, EMB_INDEX)
    global _VEC
    _VEC = None
    return {"chunks": len(chunks), "reembedded": reembedded, "embed": "ok"}


def _load():
    global _VEC
    if _VEC is None:
        try:
            _VEC = json.load(open(EMB_INDEX, encoding="utf-8")) if os.path.exists(EMB_INDEX) else []
        except Exception:
            _VEC = []                                          # 破損時は空=字面検索へ退避(crashさせぬ)
    return _VEC


def available():
    return db_available()          # Fable M2: 411MB全読込でなくsqliteの有無で判定(hybridの発火条件)


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


# ── Fable M2: 意味検索の復活。411MB JSON全読込(26s)+全走査(8s)を、sqlite(候補だけ引く)+字面recallで置換。numpy不要。
def _key(src, t):
    return f"{src}\x00{(t or '')[:400]}"


def build_sqlite():
    """casper_embed_index.json(411MB) → sqlite(key,src,title,t,vec=float32 packed BLOB)。一度だけ実行。"""
    if not os.path.exists(EMB_INDEX):
        print("no EMB_INDEX", file=sys.stderr); return 0
    data = json.load(open(EMB_INDEX, encoding="utf-8"))
    tmp = EMB_DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE emb(key TEXT PRIMARY KEY, src TEXT, title TEXT, t TEXT, vec BLOB)")
    rows = []
    for e in data:
        v = e.get("v")
        if not v:
            continue
        rows.append((_key(e["src"], e["t"]), e["src"], e.get("title", ""), e["t"],
                     struct.pack("<%df" % len(v), *v)))
        if len(rows) >= 2000:
            con.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?,?,?)", rows); rows = []
    if rows:
        con.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?,?,?)", rows)
    con.commit(); con.close()
    os.replace(tmp, EMB_DB)
    return len(data)


_DBCON = None


def _db():
    global _DBCON
    if _DBCON is None and os.path.exists(EMB_DB):
        _DBCON = sqlite3.connect(EMB_DB, check_same_thread=False)
    return _DBCON


def db_available():
    return _db() is not None


def _blob_vec(b):
    return list(struct.unpack("<%df" % (len(b) // 4), b))


def search(query, k=8, budget=3800):
    """意味検索(Fable M2復活版): 字面recall→候補の埋め込みだけsqliteから引き→cosine再ランク。
    sqlite/クエリ埋め込みが無ければ casper_rag(字面)にフォールバック。全走査(26s+8s)は行わない。"""
    con = _db()
    if con is None:
        return casper_rag.search(query, k=k, budget=budget)   # sqlite未生成→字面
    qv = embed_one(query)
    if qv is None:
        return casper_rag.search(query, k=k, budget=budget)   # bge-m3不在→字面
    cands = casper_rag.candidates(query, n=60)                 # 字面recall(速い)
    if not cands:
        return casper_rag.search(query, k=k, budget=budget)
    scored = []
    for e in cands:
        row = con.execute("SELECT vec FROM emb WHERE key=?", (_key(e["src"], e["t"]),)).fetchone()
        if row:
            scored.append((_cos(qv, _blob_vec(row[0])), e))
    scored.sort(key=lambda x: -x[0])
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
    return res or casper_rag.search(query, k=k, budget=budget)


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
