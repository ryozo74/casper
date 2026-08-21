#!/usr/bin/env python3
"""週次品質測定(cmd_502) — 将軍の手作業(週次ログ分析)の機械化。

物差しは二つ、意味が違う(混同禁止・AC3):
  (a)週次ログ分析 = 普通の使われ方(conversation_log.jsonlの人発話)。
  (b)holdout実走(cmd_500・run_holdout.py) = わざと難所ばかり集めた16問。
このモジュールは(a)のみを扱う。(b)はrun_holdout.pyを外部cronから叩いた結果(json)を
そのまま受け取って別立てで並べるだけで、holdout.json/run_holdout.pyの判定基準には
一切触れない。

抽出手順(将軍が実際に用いたものをそのまま機械化・軍師実測で完全再現・
gunshi_report.yaml subtask_502_strategy1 2026-08-06確定):
  1. conversation_log.jsonlを読む
  2. ip=='172.17.0.1'で人の端末からの発話のみ抽出(127.0.0.1は足軽/軍師の試験)
  3. role=='user'を問い、直後のrole=='casper'を答えとして対にする(同一sid内)
  4. 答えを型で数える(重複除去した実数がつまずき件数)

★AC1の但し書き(軍師の進言をそのまま踏襲): 母数(問い数)と重複除去の作法は
将軍手作業と完全一致する。然れど型別内訳のうち「エラー」は現行regexで
将軍の数(8件)を再現できていない(4件のみ捕捉)。原因は空応答0件・極短応答0件を
足しても8にならぬこと——数を合わせるためのregex緩和は禁じられているため、
合わぬまま正直にこの値(4件)を返す。
"""
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(HERE, "reports")
STATE_PATH = os.path.join(REPORTS_DIR, "_state.json")
SNAPSHOT_PATH = os.path.join(REPORTS_DIR, "_last_snapshot.json")
LOG_PATH = os.path.join(HERE, "conversation_log.jsonl")

HUMAN_IP = "172.17.0.1"

# 型別regex(将軍手作業の定義そのまま・brief記載)。
TYPE_PATTERNS = {
    "聞き返し": re.compile(r"どの(プロジェクト|PJ|資料|ファイル|案件|カット)|具体的な.*名|文脈から特定でき|対象が(不明|特定でき)"),
    "できません": re.compile(r"できません|できかね|対応しており(ま)?せん|機能(は|が)(あり|ござい)ません"),
    "不在断言": re.compile(r"(は|が)(あり|ござい)ません|見当たりません|登録されていません|記載されていません"),
    "エラー": re.compile(r"\[error\]|失敗しました"),
}
TYPE_ORDER = ["不在断言", "聞き返し", "できません", "エラー"]


def _read_jsonl(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def _extract_pairs(log_path, start, end, ip_filter=HUMAN_IP):
    """ip==ip_filter(Noneなら選り分けなし=突然変異(i)用)の発話をstart<=ts<=endで抽出し、
    同一sid内でuser直後のcasperを対にする。file順=chronological前提(既存ログの実態)。"""
    recs = _read_jsonl(log_path)
    filtered = []
    for r in recs:
        if ip_filter is not None and r.get("ip") != ip_filter:
            continue
        try:
            ts = datetime.datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        if not (start <= ts <= end):
            continue
        filtered.append(r)

    by_sid = {}
    for r in filtered:
        by_sid.setdefault(r.get("sid"), []).append(r)

    pairs = []
    for sid, msgs in by_sid.items():
        i = 0
        while i < len(msgs):
            if msgs[i].get("role") == "user":
                j = i + 1
                if j < len(msgs) and msgs[j].get("role") == "casper":
                    pairs.append({"sid": sid, "q": msgs[i].get("content", ""),
                                  "a": msgs[j].get("content", ""), "ts": msgs[i].get("ts")})
                    i = j + 1
                    continue
            i += 1
    return pairs


def classify(answer):
    """1答えに当たる型のlist(複数当たれば重複=延べカウント側で使う)。"""
    text = answer or ""
    return [t for t in TYPE_ORDER if TYPE_PATTERNS[t].search(text)]


def analyze_window(log_path, start, end, dedup=True):
    """(a)週次ログ分析。dedup=Falseは突然変異(ii)検証用(延べ数のまま返す)。"""
    pairs = _extract_pairs(log_path, start, end, ip_filter=HUMAN_IP)
    by_type = {t: 0 for t in TYPE_ORDER}
    stumble_flags = []
    dedup_delta = 0
    for p in pairs:
        hit_types = classify(p["a"])
        p["stumble_types"] = hit_types
        if hit_types:
            stumble_flags.append(True)
            if dedup:
                if len(hit_types) > 1:
                    dedup_delta += len(hit_types) - 1
                for t in hit_types:
                    by_type[t] += 1
            else:
                for t in hit_types:
                    by_type[t] += 1
        else:
            stumble_flags.append(False)

    n_questions = len(pairs)
    n_stumbles = sum(1 for f in stumble_flags if f) if dedup else sum(len(p["stumble_types"]) for p in pairs)
    rate = round(n_stumbles / n_questions, 4) if n_questions else 0.0

    return {
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "totals": {"questions": n_questions, "stumbles": n_stumbles, "rate": rate},
        "by_type": by_type,
        "dedup_delta": dedup_delta,
        "pairs": pairs,
        "human_zero": n_questions == 0,
    }


def _normalize_q(q):
    return re.sub(r"[\s\W_]+", "", q or "", flags=re.UNICODE)


def diff_from_prev(current, prev):
    """point_c: 前週との差(数の増減でなく型と実例)。前週が無ければNone(初回)。
    improved/regressedは正規化文字列の完全一致でのみ結びつける(推測で結び付けない)。"""
    if prev is None:
        return None

    q_diff = current["totals"]["questions"] - prev["totals"]["questions"]
    s_diff = current["totals"]["stumbles"] - prev["totals"]["stumbles"]
    by_type_diff = {t: current["by_type"].get(t, 0) - prev["by_type"].get(t, 0) for t in TYPE_ORDER}

    prev_by_norm = {}
    for p in prev.get("pairs", []):
        prev_by_norm[_normalize_q(p["q"])] = p

    improved, regressed = [], []
    for p in current.get("pairs", []):
        key = _normalize_q(p["q"])
        pv = prev_by_norm.get(key)
        if pv is None:
            continue
        was_stumble = bool(pv.get("stumble_types"))
        now_stumble = bool(p.get("stumble_types"))
        if was_stumble and not now_stumble:
            improved.append({"type": (pv.get("stumble_types") or [None])[0], "q": p["q"],
                              "was": pv.get("a", ""), "now": p.get("a", "")})
        elif not was_stumble and now_stumble:
            regressed.append({"type": (p.get("stumble_types") or [None])[0], "q": p["q"],
                               "now": p.get("a", "")})

    return {
        "questions": q_diff,
        "stumbles": s_diff,
        "by_type": by_type_diff,
        "improved_examples": improved,
        "regressed_examples": regressed,
    }


def holdout_diff(current_holdout, prev_holdout):
    """holdout側の差分(AC3: 週次分析の差分とは別立て)。id単位の変化(fail→pass等)を出す。"""
    if prev_holdout is None or current_holdout is None:
        return None
    prev_ids = {i["id"]: i["final"] for i in prev_holdout.get("ids", [])} if "ids" in prev_holdout else {}
    cur_ids = {i["id"]: i["final"] for i in current_holdout.get("ids", [])} if "ids" in current_holdout else {}
    changes = []
    for iid, cur_v in cur_ids.items():
        prev_v = prev_ids.get(iid)
        if prev_v is not None and prev_v != cur_v:
            changes.append({"id": iid, "from": prev_v, "to": cur_v})
    return {"changes": changes}


# ── state管理(point_d) ──────────────────────────────────────────
def _read_state(state_path=STATE_PATH):
    try:
        return json.load(open(state_path, encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state, state_path=STATE_PATH):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def should_run_weekly_from_date(last_run_iso, today):
    """前回実施日(YYYY-MM-DD)から7日以上経っておれば発火。曜日固定でなく経過で判ずる
    (曜日固定はその日サーバが落ちておれば丸一週飛ぶ・経過判定は遅れても必ず回る)。"""
    if not last_run_iso:
        return True
    try:
        last = datetime.date.fromisoformat(last_run_iso)
    except Exception:
        return True
    return (today - last).days >= 7


def should_run_weekly(state_path=STATE_PATH, today=None):
    today = today or datetime.date.today()
    state = _read_state(state_path)
    return should_run_weekly_from_date(state.get("weekly_last_run"), today)


def human_zero_warning(n_questions):
    """人の問いが0件だった週は異常(選り分け誤りの疑い)として文言を返す。0件でなければNone。"""
    if n_questions == 0:
        return "測定に人の発話が1件も無し(選り分けの誤りの疑い)"
    return None


def dashboard_line(ctx):
    """dashboard.mdへ載せる1行を組み立てて返す(dashboard.md自体の書き換えはしない)。
    ctx: {weekly_last_run, days_since, questions, stumbles, diff_stumbles,
          holdout_pass, holdout_total, last_error}
    掟「失敗とゼロを別出口へ」: last_errorが在れば「測って0件」ではなく「測れなかった」と示す。"""
    if ctx.get("last_error"):
        return f"品質測定: 測定失敗: {ctx['last_error']} (最終成功 {ctx.get('weekly_last_run') or '無し'})"

    days = ctx.get("days_since")
    if days is not None and days > 7:
        run_desc = f"{days}日前(測定が止まっておりまする)"
    elif days is not None:
        run_desc = f"{ctx.get('weekly_last_run', '')}({days}日前)"
    else:
        run_desc = "未実施"

    q = ctx.get("questions")
    s = ctx.get("stumbles")
    diff_s = ctx.get("diff_stumbles")
    weekly_part = "週次 未実施" if q is None else f"週次 問い{q}件/つまずき{s}件"
    if diff_s is not None:
        sign = "+" if diff_s >= 0 else ""
        weekly_part += f"(前週比{sign}{diff_s})"

    hp, ht = ctx.get("holdout_pass"), ctx.get("holdout_total")
    holdout_part = f"holdout {hp}/{ht}({round(hp/ht*100) if ht else 0}%)" if (hp is not None and ht) else "holdout 未実施"

    return f"品質測定: {weekly_part} ・ {holdout_part} 最終実施 {run_desc}"


# ── レポート組み立て(AC3: weekly/holdoutを別出口に保つ) ──────────────
def build_report(weekly, weekly_diff, holdout, holdout_diff_result, _mutation_merge=False):
    """weeklyとholdoutを別キーで保持する。_mutation_merge=Trueは突然変異(iii)検証専用
    (混ぜてはならぬことをゲートで示すため、意図的に混入させるバックドア)。"""
    report = {
        "weekly": {"totals": weekly.get("totals"), "by_type": weekly.get("by_type"),
                   "period": weekly.get("period"), "diff_from_prev": weekly_diff},
        "holdout": holdout if holdout is not None else {},
    }
    if holdout_diff_result is not None:
        report["holdout"] = dict(report["holdout"])
        report["holdout"]["diff_from_prev"] = holdout_diff_result
    if _mutation_merge:
        # 突然変異(iii)専用: weeklyのtotalsをholdout側にも書き込み、混入を発生させる。
        report["holdout"]["questions"] = weekly.get("totals", {}).get("questions")
        report["weekly"]["pass"] = holdout.get("pass") if holdout else None
    return report


def _to_md(report, days_since, last_error):
    w = report["weekly"]
    h = report["holdout"]
    lines = [f"# 週次品質測定 {w.get('period', {}).get('to', '')}", ""]
    lines.append("## (a) 週次ログ分析(普通の使われ方)")
    if w.get("totals"):
        t = w["totals"]
        lines.append(f"- 問い: {t.get('questions')}件 / つまずき: {t.get('stumbles')}件 (rate={t.get('rate')})")
        for k, v in (w.get("by_type") or {}).items():
            lines.append(f"  - {k}: {v}")
    diff = w.get("diff_from_prev")
    if diff is None:
        lines.append("- 前週比: 初回のため無し")
    else:
        lines.append(f"- 前週比: questions{diff['questions']:+d} stumbles{diff['stumbles']:+d}")
        for e in diff.get("improved_examples", []):
            lines.append(f"  - 改善: 「{e['q']}」({e.get('type')})")
        for e in diff.get("regressed_examples", []):
            lines.append(f"  - 悪化: 「{e['q']}」({e.get('type')})")
    lines.append("")
    lines.append("## (b) holdout実走(わざと難所ばかり集めた16問・週次分析とは別物・AC3)")
    if h:
        lines.append(f"- pass={h.get('pass')} fail={h.get('fail')} review={h.get('review')} "
                      f"untestable={h.get('untestable')} total={h.get('total')}")
        hd = h.get("diff_from_prev")
        if hd:
            for c in hd.get("changes", []):
                lines.append(f"  - {c['id']}: {c['from']} → {c['to']}")
    else:
        lines.append("- 未実施(外部cron待ち)")
    if last_error:
        lines.append("")
        lines.append(f"## 測定失敗: {last_error}")
    return "\n".join(lines) + "\n"


def run_weekly(log_path=LOG_PATH, reports_dir=REPORTS_DIR, state_path=STATE_PATH,
               snapshot_path=SNAPSHOT_PATH, today=None):
    """週次分析を1回実施し、reports/weekly_<date>.json/.md を書き、stateを更新して返す。
    例外は握り潰さず、last_errorへ記録してstateへ残す(掟「失敗とゼロを別出口へ」)。

    diff_from_prevの土台には公開レポート(weekly_<date>.json・build_report後のweekly/holdout
    分離構造)ではなく、analyze_window()の生スナップショット(pairs付き)を別ファイル
    (_last_snapshot.json)に保持して使う。公開レポートの構造を後で変えても差分ロジックが
    壊れないようにするため。"""
    today = today or datetime.date.today()
    state = _read_state(state_path)
    last_error = None
    dashboard_txt = None
    try:
        end = datetime.datetime.combine(today, datetime.time(0, 0, 0))
        start = end - datetime.timedelta(days=7)
        weekly = analyze_window(log_path, start, end)

        prev_summary = None
        if os.path.exists(snapshot_path):
            try:
                prev_summary = json.load(open(snapshot_path, encoding="utf-8"))
            except Exception:
                prev_summary = None
        w_diff = diff_from_prev(weekly, prev_summary)

        if weekly["human_zero"]:
            last_error = human_zero_warning(0)

        report = build_report(weekly, w_diff, None, None)
        os.makedirs(reports_dir, exist_ok=True)
        date_str = today.isoformat()
        json_path = os.path.join(reports_dir, f"weekly_{date_str}.json")
        md_path = os.path.join(reports_dir, f"weekly_{date_str}.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({**report, "note": "初回は前週が無いゆえdiff_from_prevはnullとし「初回」と明示する" if w_diff is None else None},
                       f, ensure_ascii=False, indent=1)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_to_md(report, 0, None))
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(weekly, f, ensure_ascii=False, indent=1)

        state["weekly_last_run"] = date_str
        state["weekly_last_ok"] = True
        state["weekly_last_summary_path"] = json_path
        state["weekly_last_totals"] = weekly["totals"]
        state["last_error"] = None
        dashboard_txt = dashboard_line({
            "weekly_last_run": date_str, "days_since": 0,
            "questions": weekly["totals"]["questions"], "stumbles": weekly["totals"]["stumbles"],
            "diff_stumbles": (w_diff["stumbles"] if w_diff else None),
            "holdout_pass": state.get("holdout_last_pass"), "holdout_total": state.get("holdout_last_total"),
            "last_error": None,
        })
    except Exception as e:
        last_error = str(e)[:200]
        state["weekly_last_ok"] = False
        state["last_error"] = last_error
        dashboard_txt = dashboard_line({
            "weekly_last_run": state.get("weekly_last_run"), "days_since": None,
            "questions": None, "stumbles": None, "diff_stumbles": None,
            "holdout_pass": state.get("holdout_last_pass"), "holdout_total": state.get("holdout_last_total"),
            "last_error": last_error,
        })
    _write_state(state, state_path)
    return {"state": state, "dashboard_line": dashboard_txt, "error": last_error}


if __name__ == "__main__":
    r = run_weekly()
    print(r["dashboard_line"])
    if r["error"]:
        print(f"[weekly_report] エラー: {r['error']}")
