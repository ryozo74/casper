#!/usr/bin/env python3
"""Casper セルフヘルス — トレースからの定点監視(Fable5 北極星 柱2)。

『人が張り付かなくても健全を保つ』最小構成。casper_trace.jsonl の各率を過去の頑健統計
(中央値 ± k·MAD)と比べ、逸脱を検知する。ML は使わない(それ自体が保守対象になる)。

出力2系統:
  ① vault/00_health/health.md … 日次ヘルス帯。RAG索引に載るので **Casper自身が『調子どう?』に答えられる**。
  ② 逸脱アラート … queue/casper_alerts.jsonl に追記(逸脱時のみ)。

監視する率(接地/アクションの健全性の代理指標):
  guarded_claim(既成事実化の打消)・abstained(棄権=上流障害の代理)・salvaged(ツール漏れ)・
  rag_zero(RAG空振り)・gen_p95(冷間/高負荷)。棄権率の急騰は Calendar 等 接地ソース異常の早期警報。

CLI:
  python3 casper_health.py            # 監視を1回実行→health.md 更新＋逸脱表示
  python3 casper_health.py --show     # 現在の health.md を表示
"""
import datetime
import json
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(HERE, "casper_trace.jsonl")
HEALTH_DIR = os.path.join(HERE, "..", "vault", "00_health")
HEALTH_MD = os.path.join(HEALTH_DIR, "health.md")
ALERTS = os.path.join(HERE, "..", "..", "..", "queue", "casper_alerts.jsonl")
K = 3.5                                                     # 逸脱閾値(中央値 ± K·MAD)
MIN_BASELINE_DAYS = 4                                       # これ未満はベースライン不足→逸脱判定せず観測のみ


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


def _day(r):
    return str(r.get("ts", ""))[:10]


def _rates(rows):
    """1グループ(=1日 or 直近窓)の率を算出。"""
    n = len(rows) or 1
    def rate(pred):
        return round(sum(1 for r in rows if pred(r)) / n, 3)
    gs = sorted(r.get("gen_sec", 0) or 0 for r in rows)
    p95 = gs[min(len(gs) - 1, int(len(gs) * 0.95))] if gs else 0
    return {"n": len(rows),
            "guarded_claim": rate(lambda r: r.get("guarded_claim")),
            "abstained": rate(lambda r: r.get("abstained")),
            "salvaged": rate(lambda r: r.get("salvaged")),
            "rag_zero": rate(lambda r: (r.get("rag_hits") or 0) == 0),
            "routed": rate(lambda r: r.get("routed")),
            "gen_p95": round(p95, 1)}


def _mad(xs):
    """中央絶対偏差(頑健なばらつき)。"""
    if len(xs) < 2:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs]) or 0.0


def analyze():
    """返り: {today, baseline_days, deviations[], today_rates, warming}。"""
    rows = _load()
    if not rows:
        return {"today": None, "baseline_days": 0, "deviations": [], "today_rates": {}, "n": 0}
    today = datetime.date.today().isoformat()
    by_day = {}
    for r in rows:
        by_day.setdefault(_day(r), []).append(r)
    today_rows = by_day.get(today, [])
    hist_days = [d for d in by_day if d < today]
    tr = _rates(today_rows) if today_rows else _rates(rows[-50:])   # 当日ゼロ件なら直近50件で代替
    deviations = []
    if len(hist_days) >= MIN_BASELINE_DAYS:
        for key in ("guarded_claim", "abstained", "salvaged", "rag_zero", "gen_p95"):
            series = [_rates(by_day[d])[key] for d in sorted(hist_days)]
            med = statistics.median(series)
            mad = _mad(series)
            cur = tr[key]
            if mad > 0 and abs(cur - med) > K * mad and cur > med:      # 悪化方向のみ警報
                deviations.append({"metric": key, "current": cur, "baseline_median": round(med, 3),
                                   "threshold": round(med + K * mad, 3)})
    return {"today": today, "baseline_days": len(hist_days), "deviations": deviations,
            "today_rates": tr, "n": len(rows)}


def write_health_md(a):
    os.makedirs(HEALTH_DIR, exist_ok=True)
    tr = a.get("today_rates", {})
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    status = "🔴 逸脱あり" if a.get("deviations") else ("🟢 健全" if a.get("baseline_days", 0) >= MIN_BASELINE_DAYS else "🟡 観測中(ベースライン蓄積中)")
    lines = [f"# Casper セルフヘルス — {a.get('today')}",
             f"> 更新 {stamp} / 総トレース {a.get('n', 0)}件 / ベースライン {a.get('baseline_days', 0)}日分",
             "", f"## 状態: {status}", "",
             "## 本日の指標（対話 {} 件）".format(tr.get("n", 0)),
             f"- 既成事実化の打消 (guarded_claim): {tr.get('guarded_claim', 0):.0%}",
             f"- 棄権率 (abstained): {tr.get('abstained', 0):.0%}  ← 急騰は接地ソース(Calendar等)異常の代理指標",
             f"- ツール漏れ掃除 (salvaged): {tr.get('salvaged', 0):.0%}",
             f"- RAG空振り (rag_zero): {tr.get('rag_zero', 0):.0%}",
             f"- 先回り/ルーティング率 (routed): {tr.get('routed', 0):.0%}",
             f"- 生成時間 p95: {tr.get('gen_p95', 0)}s", ""]
    if a.get("deviations"):
        lines.append("## 🔴 逸脱（過去中央値から悪化）")
        for d in a["deviations"]:
            lines.append(f"- **{d['metric']}**: 現在 {d['current']} > 閾値 {d['threshold']}（平常 {d['baseline_median']}）")
        lines.append("")
    open(HEALTH_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return HEALTH_MD


def _alert(a):
    if not a.get("deviations"):
        return
    try:
        os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "source": "casper_health", "deviations": a["deviations"]}
        with open(ALERTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run():
    a = analyze()
    write_health_md(a)
    _alert(a)
    return a


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        print(open(HEALTH_MD, encoding="utf-8").read() if os.path.exists(HEALTH_MD) else "(health.md 未生成)")
    else:
        a = run()
        st = "🔴 逸脱" if a["deviations"] else ("🟢 健全" if a["baseline_days"] >= MIN_BASELINE_DAYS else "🟡 観測中")
        print(f"{st} / 本日 {a['today_rates'].get('n', 0)}件 / ベースライン {a['baseline_days']}日 / 逸脱 {len(a['deviations'])}件")
        for d in a["deviations"]:
            print(f"  🔴 {d['metric']}: {d['current']} > {d['threshold']}")
        print(f"→ {HEALTH_MD}")
