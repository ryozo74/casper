#!/usr/bin/env python3
"""cmd_515手当3(警報が人に届く経路): queue/casper_alerts.jsonl(casper_health.pyが吐く逸脱行)
を読み、既存の赤の届け先(run_observation.py/discord_watchdog_stale_check.pyと同型・
inbox_write.sh -> karo, type=observation_red)へ通知する。★新しい通知経路は作らない。

実利用8回に対し警報96件(2026-08-20)の教訓——同一metricの連続超過をそのまま流すと
読む者が読まなくなる(瑕疵D)。ゆえに discord_watchdog_stale_check.py と同じ
「状態遷移時のみ一度だけ吠える」方式を採る(cmd_514手当2で確立済み・新設計にしない):
  ・平常→超過 の遷移が起きた回のみ通知(以後、超過が続く間は静かに扱う)。
  ・超過→平常 に戻った回のみ回復を一度だけ通知。
  ・再送間隔(1時間後など)は設けない——discord_watchdog_stale_check.pyの精査(本ファイル
    docstring末尾参照)の通り、状態が変わらぬ限り再送する理由が無く、周期的な再送は
    「継続中を毎回知らせる」のと同じで瑕疵Dの再発になる。状態が変化した時のみが
    真に人の判断を要する瞬間である。

Usage:
  python3 alert_dispatch.py            # 1回実行(cron向け)
  python3 alert_dispatch.py --dry-run  # 通知・state書込を行わず判定のみ表示(試験用)
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))   # multi-agent-shogun-main/
QUEUE_DIR = os.path.join(ROOT, "queue")
ALERTS_PATH = os.path.join(QUEUE_DIR, "casper_alerts.jsonl")
STATE_PATH = os.path.join(QUEUE_DIR, "casper_alerts_dispatch_state.json")
LOCK_PATH = os.path.join(HERE, "reports", "_holdout.lock")
INBOX_WRITE = os.path.join(ROOT, "scripts", "inbox_write.sh")

# ★AC8: 本番保護。run_observation.py._acquire_lock/_lock_held_by_otherと同型
# (ファイル形式のみ有効・ディレクトリ形式は casper_supervisor.sh L39 の -f 判定で無効化される教訓)。
_LOCK_TTL_SEC = 1200   # supervisor側TTLと同じ20分(run_observation.pyに揃える)


def _acquire_lock(who):
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\n")
        f.write(f"pid={os.getpid()}\n")
        f.write(f"who={who}\n")


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def _lock_held_by_other(who):
    if not os.path.isfile(LOCK_PATH):
        return False
    try:
        lines = open(LOCK_PATH, encoding="utf-8").read().splitlines()
        ts = int("".join(c for c in (lines[0] if lines else "") if c.isdigit()) or "0")
        owner = next((ln.split("=", 1)[1] for ln in lines if ln.startswith("who=")), "")
    except Exception:
        return False
    if owner == who:
        return False
    return (time.time() - ts) <= _LOCK_TTL_SEC


def _load_alerts():
    if not os.path.isfile(ALERTS_PATH):
        return []
    rows = []
    for ln in open(ALERTS_PATH, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    return rows


def _load_state(total_rows=0):
    """state未作成(初回起動)の場合、cursorを末尾(total_rows)へ寄せる。
    ★cmd_515手当3是正(軍師QC2 CONDITIONAL_PASS指摘): cursor_line=0だと初回起動時に
    過去ログ全件(39日前の残骸含む)を『今まさに閾値超過』として誤通知してしまう。
    本機構の目的は『これから起きる赤』を届けることであり、過去ログの棚卸しではないため、
    初回は『これまでの分は既読』とし、以後の新規行のみを対象とする。"""
    if not os.path.isfile(STATE_PATH):
        return {"metrics": {}, "cursor_line": total_rows}
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {"metrics": {}, "cursor_line": total_rows}


def _save_state(state, dry_run):
    if dry_run:
        return
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _notify(msg, dry_run):
    if dry_run:
        print(f"[dry-run] would notify: {msg}", file=sys.stderr)
        return
    subprocess.run(["bash", INBOX_WRITE, "karo", msg, "observation_red", "casper_alert_dispatch"],
                    check=False)


def process(rows, state, dry_run=False, synthetic_prefix=""):
    """rows全件を舐め、metricごとに『いま超過中か』を導出し、前回stateとの遷移のみ通知する。
    ★カーソル方式(cursor_line)で既読行を飛ばす——毎回全件を舐め直すとファイルが育つにつれ
    重くなるうえ、casper_alerts.jsonlは1行=1回のヘルスチェック実行(15分毎)分の逸脱スナップ
    ショット全体(deviations配列)であって、個々の逸脱イベントを積み上げる台帳ではないため、
    『前回見た行より後』だけを新規入力として扱えばよい(冪等)。
    戻り値: {metric: "notified_start"|"notified_recover"|"still_active"|"still_clear"}(試験用)。
    ★cmd_518手当7是正: gen_event_fired(cmd_518手当5がcasper_health.pyで新設した急性検知の
    赤台帳)は上のdeviationsとは別種のデータ(スナップショットでなく『今回tickで新規発火した
    エッジイベント』の配列)であるため、同一ロジックへ混ぜず独立したチェック項目として扱う。
    """
    metrics_state = state.setdefault("metrics", {})
    cursor = state.get("cursor_line", 0)
    new_rows = rows[cursor:]
    # ★2026-08-30 是正: 新しい行が一つも無い時に『復旧』を出してはならぬ。
    #   行の不在は「治った」ではなく「**健診の便りが無い**」である(健診が止まっておっても
    #   吉報に化ける読みであった)。沈黙からは何も断ぜず、状態を据え置く。
    #   復旧は casper_health が収まった時に積む一行(空の deviations)で伝わる。
    if not new_rows:
        return {m: ("still_active" if v.get("active") else "still_clear")
                for m, v in metrics_state.items()}

    # 今回の新規行から『どのmetricが超過として現れたか』を集める(1行に複数deviations可)。
    seen_this_run = {}
    for row in new_rows:
        for d in row.get("deviations", []):
            m = d.get("metric")
            if not m:
                continue
            seen_this_run[m] = d   # 直近の値で上書き(通知本文には最新の値を使う)

    result = {}
    all_known_metrics = set(metrics_state.keys()) | set(seen_this_run.keys())
    for m in all_known_metrics:
        was_active = metrics_state.get(m, {}).get("active", False)
        is_active = m in seen_this_run
        if is_active and not was_active:
            d = seen_this_run[m]
            msg = (f"{synthetic_prefix}casper_alerts赤: metric={m} current={d.get('current')} "
                   f"threshold={d.get('threshold')} baseline_median={d.get('baseline_median')} "
                   f"(閾値超過を検知・以後この指標は状態が変わるまで再通知しない)")
            _notify(msg, dry_run)
            metrics_state[m] = {"active": True, "notified_at": time.time()}
            result[m] = "notified_start"
        elif is_active and was_active:
            result[m] = "still_active"
        elif not is_active and was_active:
            msg = f"{synthetic_prefix}casper_alerts復旧: metric={m} は閾値内に戻った。"
            _notify(msg, dry_run)
            metrics_state[m] = {"active": False, "notified_at": time.time()}
            result[m] = "notified_recover"
        else:
            result[m] = "still_clear"

    # ★gen_event_fired: casper_health.py側が既にエッジ検知(前回tickからの新規発火のみ)を
    # 済ませているため、ここでは『発火があれば通知する』だけでよい(状態遷移の判定は
    # scan_gen_events側のcur_level/streakが担う・二重に持たない)。1行(=1 tick)に複数
    # fired要素が乗ることは想定しない設計だが配列全件を舐めて安全側に倒す。
    fired_events = [ev for row in new_rows for ev in (row.get("gen_event_fired") or [])]
    for ev in fired_events:
        level = ev.get("level", "?")
        msg = (f"{synthetic_prefix}casper_alerts赤(gen_p95_event): level={level} "
               f"gen_sec={ev.get('gen_sec')} ts={ev.get('ts')} "
               f"(生成時間の急性逸脱を検知)")
        _notify(msg, dry_run)
    if fired_events:
        result["gen_p95_event"] = "notified_start"
    elif "gen_p95_event" not in result:
        result["gen_p95_event"] = "still_clear"

    state["cursor_line"] = len(rows)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                     help="通知・state書込を行わず判定のみ表示(試験用)")
    ap.add_argument("--synthetic", action="store_true",
                     help="AC6合成赤試験用。通知本文に【試験】を明記する。")
    args = ap.parse_args()

    who = "cron_alert_dispatch"
    if not args.dry_run:
        if _lock_held_by_other(who):
            print("[alert_dispatch] ロック競合(他者保持中)ゆえ今回はスキップ", file=sys.stderr)
            sys.exit(3)
        _acquire_lock(who)

    try:
        rows = _load_alerts()
        state = _load_state(total_rows=len(rows))
        prefix = "【試験】" if args.synthetic else ""
        result = process(rows, state, dry_run=args.dry_run, synthetic_prefix=prefix)
        _save_state(state, args.dry_run)
    finally:
        if not args.dry_run:
            _release_lock()

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
