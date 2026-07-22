#!/usr/bin/env python3
"""M4 Phase2' MTG助言の回帰ゲート（純機構・インメモリ・読取のみ）。
upcoming_meetings / agenda_for（集合演算・空=積み残しなし）/ meeting_cadence / meetings_due（中央値・根拠不足は黙る）。全PASSで exit 0。"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_meeting as M

NOW = datetime.datetime(2026, 7, 17, 9, 0, 0)
TODAY = "2026-07-17"
results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── upcoming_meetings（48h窓）──
EVENTS = [
    {"id": 1, "type": "Meeting", "project_id": "100", "start_time": "2026-07-17T15:00:00", "user_ids": [29, 30]},   # 今日午後=窓内
    {"id": 2, "type": "Meeting", "project_id": "100", "start_time": "2026-07-25T15:00:00", "user_ids": [29]},        # 8日後=窓外
    {"id": 3, "type": "Milestone", "project_id": "100", "start_time": "2026-07-17T16:00:00"},                        # Meetingでない
    {"id": 4, "type": "Meeting", "project_id": "100", "start_time": "2026-07-01T15:00:00"},                          # 前回会議(過去)
    {"id": 5, "type": "Meeting", "project_id": "100", "start_time": "2026-06-15T15:00:00"},
    {"id": 6, "type": "Meeting", "project_id": "100", "start_time": "2026-06-01T15:00:00"},
]
up = M.upcoming_meetings(EVENTS, NOW)
chk("upcoming=会議1のみ(48h窓・Milestone除外)", [e["id"] for e in up], [1])

# ── last_meeting_before ──
lm = M.last_meeting_before(EVENTS, "100", NOW)
chk("前回会議=7/1(NOWより前の直近)", lm.date().isoformat() if lm else None, "2026-07-01")

# ── agenda_for（集合演算）──
TASKS = [
    {"id": 10, "project_id": "100", "name": "A確認待ち", "status": "qc", "assigned_to": "30", "due_date": "2026-07-30", "updated_at": "2026-06-25"},
    {"id": 11, "project_id": "100", "name": "B納期超過", "status": "wip", "assigned_to": "31", "due_date": "2026-07-10", "updated_at": "2026-06-20"},
    {"id": 12, "project_id": "100", "name": "C前回以降更新", "status": "wip", "assigned_to": "30", "due_date": "2026-08-20", "updated_at": "2026-07-14"},
    {"id": 13, "project_id": "100", "name": "D完了", "status": "deliver", "assigned_to": "30", "due_date": "2026-07-01", "updated_at": "2026-07-16"},
    {"id": 14, "project_id": "999", "name": "別PJ", "status": "qc", "assigned_to": "30", "due_date": "2026-07-01", "updated_at": "2026-07-16"},
    {"id": 15, "project_id": "100", "name": "E静穏", "status": "wip", "assigned_to": "30", "due_date": "2026-08-30", "updated_at": "2026-06-01"},
]
ag = M.agenda_for(up[0], TASKS, TODAY, last_meeting_dt=lm)
ids = [a["id"] for a in ag]
chk("議題=確認待ち/納期超過/前回以降更新の3件", sorted(ids), [10, 11, 12])
chk("完了(13)は議題に出ぬ", 13 not in ids, True)
chk("別PJ(14)は出ぬ", 14 not in ids, True)
chk("静穏(15)は出ぬ", 15 not in ids, True)
chk("確認待ち(10)の理由", [a["reasons"] for a in ag if a["id"] == 10][0], ["確認待ち"])
chk("納期超過(11)の理由に納期超過", "納期超過" in [a["reasons"] for a in ag if a["id"] == 11][0], True)
# 空=積み残しなし（別PJの会議で当該PJに注意タスクが無い時）
chk("該当PJ無しの会議→空(積み残しなし)", M.agenda_for({"project_id": "777"}, TASKS, TODAY, lm), [])

# ── meeting_cadence / meetings_due ──
# 開催済のみで測る: 6/1,6/15,7/1 の3回(間隔14,16→中央値16)。NOW=7/17 09:00 ゆえ 7/17T15:00・7/25 は未開催で除外。
cad = M.meeting_cadence(EVENTS, NOW)
chk("PJ100は履歴3回で算出対象", "100" in cad, True)
chk("中央値16日", cad["100"]["median"], 16)
chk("最終開催=7/1(未来の7/17午後/7/25は除外)", cad["100"]["last"], "2026-07-01")
chk("経過16日(NOW7/17 - 最終7/1)", cad["100"]["elapsed"], 16)

# 履歴2回のPJは判定しない（根拠不足→黙る）
EV2 = [{"id": 20, "type": "Meeting", "project_id": "200", "start_time": "2026-06-01T10:00"},
       {"id": 21, "type": "Meeting", "project_id": "200", "start_time": "2026-06-20T10:00"}]
chk("履歴2回のPJは cadence 対象外", "200" not in M.meeting_cadence(EV2, NOW), True)

# meetings_due: PJ100 閾=max(1.5×16,14)=24 → 経過16<24 ゆえ"まだ"(due でない)
due_a = M.meetings_due(EVENTS, TASKS, NOW)
chk("経過16<閾24→そろそろ会議 対象外", "100" not in [d["project_id"] for d in due_a], True)

# NOW を後ろへ倒し経過を伸ばす: now=8/12 なら 最終開催=7/25(この時点では開催済)・経過18… ではなく
# 閾値超えを確実にするため 9/1 で: 最終7/25・経過38>閾24 ∧ activeタスク有 → due
due_b = M.meetings_due(EVENTS, TASKS, datetime.datetime(2026, 9, 1, 9, 0))
chk("経過38>閾24 ∧ active→そろそろ会議に載る", "100" in [d["project_id"] for d in due_b], True)

# active タスクが無ければ due にしない（完了PJに会議を促さない）
DONE_TASKS = [{"id": 90, "project_id": "100", "status": "deliver"}]
chk("未完タスク無しなら due にせぬ", M.meetings_due(EVENTS, DONE_TASKS, datetime.datetime(2026, 9, 1)), [])

n = len(results)
p = sum(results)
print(f"\n=== gate_meeting: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
