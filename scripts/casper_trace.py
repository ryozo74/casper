#!/usr/bin/env python3
"""Casper トレーシング — 1リクエスト=1トレース(Fable5棚卸し #7-1)。

『なぜこの捏造/失態が検問を通ったか』の事後分析基盤。重いLangfuse等を入れず、判断点(span)を
1行JSONで casper_trace.jsonl に落とす。eval(casper_eval.py)とログ形式を共有し、実ログ→テスト化を一本道に。

記録する span(判断点):
  routed(P2ルーター発火/action)・rag_hits・ctx_len・gen_sec・
  salvaged/validated(asset除去)/guarded_claim(既成事実化打消)・final_len・cards・digests(発火したdigest名)

CLI:
  python3 casper_trace.py            # 直近20トレースを要約表示
  python3 casper_trace.py <grep>     # query に一致するものだけ
  python3 casper_trace.py --stats    # 発火率の集計(検問NG率・ルーティング率等=Fable #7-5の芽)
"""
import datetime
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(HERE, "casper_trace.jsonl")
_LOCK = threading.Lock()
_MAX_LINES = 5000                                          # ローテーション(肥大防止)


def emit(rec):
    """トレース1件を追記。失敗しても本処理を妨げない(観測は副作用ゼロが鉄則)。"""
    try:
        r = dict(rec)
        r.setdefault("ts", datetime.datetime.now().isoformat(timespec="seconds"))
        with _LOCK:
            with open(TRACE, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _rotate_if_needed():
    try:
        if not os.path.exists(TRACE):
            return
        lines = open(TRACE, encoding="utf-8").readlines()
        if len(lines) > _MAX_LINES:
            with _LOCK:
                open(TRACE, "w", encoding="utf-8").writelines(lines[-_MAX_LINES:])
    except Exception:
        pass


def _load():
    out = []
    if os.path.exists(TRACE):
        for ln in open(TRACE, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    return out


def _stats(rows):
    n = len(rows) or 1
    def rate(k):
        return sum(1 for r in rows if r.get(k)) / n
    print(f"トレース {len(rows)}件の集計:")
    print(f"  ルーティング率(routed)     : {rate('routed'):.0%}")
    print(f"  ツール漏れ掃除(salvaged)    : {rate('salvaged'):.0%}")
    print(f"  asset検問発火(validated)   : {rate('validated'):.0%}")
    print(f"  既成事実化打消(guarded_claim): {rate('guarded_claim'):.0%}  ← 急増はモデル/プロンプト劣化の警報")
    gs = [r.get("gen_sec", 0) for r in rows if r.get("gen_sec")]
    if gs:
        gs.sort()
        print(f"  生成時間 中央値/p95        : {gs[len(gs)//2]:.1f}s / {gs[int(len(gs)*0.95)]:.1f}s")


def main():
    rows = _load()
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        _stats(rows)
        return
    grep = sys.argv[1] if len(sys.argv) > 1 else None
    if grep:
        rows = [r for r in rows if grep in str(r.get("query", ""))]
    for r in rows[-20:]:
        flags = "".join(c for c, k in (("R", "routed"), ("S", "salvaged"), ("V", "validated"), ("G", "guarded_claim")) if r.get(k))
        print(f"[{r.get('ts', '')[11:19]}] {flags or '·':<4} "
              f"rag{r.get('rag_hits', 0)} card{r.get('cards', 0)} {r.get('gen_sec', 0)}s "
              f"| {str(r.get('query', ''))[:60]}")


if __name__ == "__main__":
    main()
