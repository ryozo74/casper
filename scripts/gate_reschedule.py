#!/usr/bin/env python3
"""M4 Phase2 日程変更の回帰ゲート（純機構・インメモリ・書込ゼロ）。
resolve_new_due（相対/絶対の日付解決・解けねば聞き返し）＋impact_of/preview（下流衝突・PJ超過・増減日数）を検証。全PASSで exit 0。"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_reschedule as R

TODAY = datetime.date(2026, 7, 17)
results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── resolve_new_due ──
chk("絶対 YYYY-MM-DD", R.resolve_new_due("2026-07-20", "2026-08-05に", TODAY), ("2026-08-05", None))
chk("絶対 M月D日", R.resolve_new_due("2026-07-20", "8月5日に変更", TODAY), ("2026-08-05", None))
chk("絶対 M/D 過去→翌年", R.resolve_new_due("2026-07-20", "1/10", TODAY), ("2027-01-10", None))
chk("相対 N日延ばす(現due基準)", R.resolve_new_due("2026-07-20", "3日延ばして", TODAY), ("2026-07-23", None))
chk("相対 N日前倒し(現due基準)", R.resolve_new_due("2026-07-20", "2日前倒し", TODAY), ("2026-07-18", None))
chk("相対 1週間延長", R.resolve_new_due("2026-07-20", "1週間延長", TODAY), ("2026-07-27", None))
chk("キーワード 明日(today基準)", R.resolve_new_due("2026-07-20", "明日まで", TODAY), ("2026-07-18", None))
chk("キーワード 来週(today基準)", R.resolve_new_due("2026-07-20", "来週に", TODAY), ("2026-07-24", None))
chk("現due無し+N日=today基準", R.resolve_new_due(None, "5日後", TODAY), ("2026-07-22", None))
chk("解けない→date_unresolved", R.resolve_new_due("2026-07-20", "いい感じに", TODAY), (None, "date_unresolved"))

# ── impact_of / preview ──
TASKS = [
    {"id": 1, "name": "Layout", "due_date": "2026-07-20", "start_date": "2026-07-10", "dependsOn": []},
    {"id": 2, "name": "Anim", "due_date": "2026-07-30", "start_date": "2026-07-21", "dependsOn": ["1"]},   # 1の後続(開始7/21)
    {"id": 3, "name": "Comp", "due_date": "2026-08-10", "start_date": "2026-08-01", "dependsOn": ["2"]},   # 2の後続
    {"id": 4, "name": "無関係", "due_date": "2026-07-25", "start_date": "2026-07-15", "dependsOn": []},
]
PROJ = {"end_date": "2026-07-31"}

# task1 を 7/20→7/25 に延ばす: 後続task2(開始7/21)は 7/25 より前で始まる→衝突
imp = R.impact_of(TASKS[0], TASKS, "2026-07-25", PROJ)
chk("delta=+5日", imp["delta_days"], 5)
chk("下流=task2 のみ(直接後続)", [d["id"] for d in imp["downstream"]], [2])
chk("task2は衝突(開始7/21<新7/25)", imp["downstream"][0]["collides"], True)
chk("衝突数=1", imp["collision_count"], 1)
chk("PJ末日(7/31)未超過", imp["project_overrun"], False)

# task1 を 7/20→8/3 に延ばす: PJ末日(7/31)超過 ＋ task2衝突
imp2 = R.impact_of(TASKS[0], TASKS, "2026-08-03", PROJ)
chk("PJ末日超過", imp2["project_overrun"], True)
chk("PJ末日値", imp2["project_end"], "2026-07-31")

# 前倒し(7/20→7/15): 後続task2(開始7/21)は 7/15 より後→衝突なし
imp3 = R.impact_of(TASKS[0], TASKS, "2026-07-15", PROJ)
chk("前倒しdelta=-5", imp3["delta_days"], -5)
chk("前倒しは衝突なし", imp3["collision_count"], 0)
chk("下流は在るが衝突ゼロ", (len(imp3["downstream"]), imp3["collision_count"]), (1, 0))

# 後続を持たぬタスク(task4)は下流ゼロ
chk("下流なしタスク", R.impact_of(TASKS[3], TASKS, "2026-07-28", PROJ)["downstream"], [])

# preview の注記
pv = R.preview(TASKS[0], TASKS, "2026-08-03", PROJ)
chk("preview 衝突注記あり", any("押し出され" in n for n in pv["notes"]), True)
chk("preview PJ超過注記あり", any("PJ末日" in n for n in pv["notes"]), True)
chk("preview は書込フィールド持たぬ", "execute" not in pv and "written" not in pv, True)

# ── execute: W2ガード＋scope=own の経路分岐（IO注入・副作用ゼロ）──
import casper_authority as A
snap = {"fetched_at": "2999-01-01T00:00:00", "roles": {"28": "admin", "29": "pm", "30": "lead", "31": "user", "50": "user"},
        "projects": {"100": {"pm": ["29"], "lead": ["30"]}}}

def _read_own(_tid):   # PJ100・担当31・wip
    return {"id": _tid, "project_id": "100", "status": "wip", "assigned_to": "31"}
_W = {}
def _wr(tid, nd, actor):
    _W["c"] = (tid, nd, actor); return True

# 本人(31)が自分のタスクのdue変更 → MCP即書き
chk("本人reschedule→written", R.execute("5", "2026-08-01", "31", snap=snap, live_read=_read_own, live_write=_wr), (True, "written"))
chk("本人経路で live_write 呼ばれた", _W.get("c"), ("5", "2026-08-01", "31"))
# admin(28) → 他者タスクでも即書き
chk("admin reschedule→written", R.execute("5", "2026-08-01", "28", snap=snap, live_read=_read_own, live_write=_wr)[0], True)
# lead(30・PJ100)が他者(31)のタスク → allowed(scope=own は自PJのpm/leadを含む)を通り MCP で書ける（actor gate 無し）
chk("lead他者reschedule→written(MCP)", R.execute("5", "2026-08-01", "30", snap=snap, live_read=_read_own, live_write=_wr), (True, "written"))
# 無関係user(50) → scope外
chk("無関係user→not_allowed(scope)", R.execute("5", "2026-08-01", "50", snap=snap, live_read=_read_own, live_write=_wr), (False, "not_allowed:out_of_scope"))
# 完了タスク(deliver) → W2で拒否(書込まない)
chk("完了タスクは拒否", R.execute("5", "2026-08-01", "28", snap=snap,
    live_read=lambda t: {"id": t, "project_id": "100", "status": "deliver", "assigned_to": "31"}, live_write=_wr), (False, "task_closed:deliver"))
# 読み戻し不可 → 実行しない
chk("読み戻し不可→実行しない", R.execute("5", "2026-08-01", "28", snap=snap, live_read=lambda t: None, live_write=_wr)[0], False)

n = len(results)
p = sum(results)
print(f"\n=== gate_reschedule: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
