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


# 進捗の真実源は Calendar。過去のレガシー記録(2022 DBM2)は"進捗/現況"に混ぜない(殿指摘2026-07-13)。
# vault側にも進捗は入らない設計ゆえ、legacy_score を RAG から常時除外し current な回答の汚染を断つ。
_EXCLUDE_SRC = ("80_legacy_score",)


def _excluded(src):
    s = str(src or "").lstrip("./")
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
        if _excluded(e.get("src")):                # legacy_score(過去DBM2)は進捗汚染源ゆえ除外
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
        if _excluded(e.get("src")):                # legacy_score(過去DBM2)は進捗汚染源ゆえ除外
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
