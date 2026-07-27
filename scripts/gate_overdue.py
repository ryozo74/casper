#!/usr/bin/env python3
"""納期超過=派生事実の回帰ゲート（純機構・インメモリ・読取のみ）。全PASSで exit 0。

守る掟:
 ① 完了/対象外の判断は status_category(API単一ソース)が正。status 文字列は category 欠落時の fallback のみ。
 ② 承認済(ap / client_ap)は過去納期でも「超過」ではない（殿御指摘・ニブ殿 2026-07-24 実コード回答）。
 ③ 判定不能(None)と 超過でない(0) は別の出口——取り違えたら未知が「0件」に化ける。
 ④ 件数と一覧は同一機構（chat_server / casper_notify / casper_meeting が同じ答えを出すこと）。
異常系・境界も踏む: category欠落 / 未知status / 大文字・空白ゆれ / due欠落 / 不正日付 / 本日締切。
"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_status_rules as R

TODAY = datetime.date(2026, 7, 27)
results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── ① category が正（status 文字列より優先）──────────────────────────
chk("ap(cat=completed)は超過に非ず", R.overdue_days("2026-06-01", "ap", "completed", TODAY), 0)
chk("client_ap(cat=completed)も超過に非ず", R.overdue_days("2026-06-01", "client_ap", "completed", TODAY), 0)
chk("deliver(cat=completed)は超過に非ず", R.overdue_days("2026-06-01", "deliver", "completed", TODAY), 0)
chk("wt(cat=held)は超過に非ず", R.overdue_days("2026-06-01", "wt", "held", TODAY), 0)
chk("omit(cat=held)は超過に非ず", R.overdue_days("2026-06-01", "omit", "held", TODAY), 0)
chk("wip(cat=in_progress)は超過", R.overdue_days("2026-06-27", "wip", "in_progress", TODAY), 30)
chk("qc(cat=review)は超過", R.overdue_days("2026-07-26", "qc", "review", TODAY), 1)
chk("mk(cat=todo)は超過", R.overdue_days("2026-07-20", "mk", "todo", TODAY), 7)
# category が status と食い違う時は category を採る（APIが正）
chk("cat優先: status=wip でも cat=completed なら非超過",
    R.overdue_days("2026-06-01", "wip", "completed", TODAY), 0)

# ── ② category 欠落 → fallback（旧応答・旧データ）────────────────────
chk("fallback: ap(cat無)も非超過", R.overdue_days("2026-06-01", "ap", None, TODAY), 0)
chk("fallback: client_ap(cat無)も非超過", R.overdue_days("2026-06-01", "client_ap", "", TODAY), 0)
chk("fallback: wip(cat無)は超過", R.overdue_days("2026-06-27", "wip", None, TODAY), 30)
chk("fallback: 旧値 approved は非超過", R.overdue_days("2026-06-01", "approved", None, TODAY), 0)
chk("fallback: 旧値 in-progress は超過", R.overdue_days("2026-06-27", "in-progress", None, TODAY), 30)

# ── ③ 異常系・境界（未知/ゆれ/日付）──────────────────────────────
chk("未知statusは超過側に倒す(見逃さぬ)", R.overdue_days("2026-06-01", "zzz_unknown", None, TODAY), 56)
chk("大文字ゆれ AP も非超過", R.overdue_days("2026-06-01", "AP", None, TODAY), 0)
chk("空白ゆれ ' ap ' も非超過", R.overdue_days("2026-06-01", " ap ", None, TODAY), 0)
chk("cat も大文字ゆれ吸収", R.overdue_days("2026-06-01", "wip", "COMPLETED", TODAY), 0)
chk("due欠落=判定不能(None・0ではない)", R.overdue_days(None, "wip", "in_progress", TODAY), None)
chk("due空文字=判定不能(None)", R.overdue_days("", "wip", "in_progress", TODAY), None)
chk("due不正=判定不能(None)", R.overdue_days("2026-13-99", "wip", "in_progress", TODAY), None)
chk("本日締切は超過でない(0)", R.overdue_days("2026-07-27", "wip", "in_progress", TODAY), 0)
chk("未来納期は超過でない(0)", R.overdue_days("2026-08-01", "wip", "in_progress", TODAY), 0)
chk("is_overdue: 判定不能は False(不明を超過と名乗らぬ)", R.is_overdue(None, "wip", None, TODAY), False)
chk("None と 0 は別物", (R.overdue_days(None, "wip", None, TODAY) is None,
                        R.overdue_days("2026-08-01", "wip", None, TODAY) == 0), (True, True))

# ── ④ PJ scope（status_category を持たぬ・実測）────────────────────
chk("PJ completed は非超過", R.overdue_days("2026-06-01", "completed", None, TODAY, "pj"), 0)
chk("PJ cancelled は非超過", R.overdue_days("2026-06-01", "cancelled", None, TODAY, "pj"), 0)
chk("PJ in-progress は超過", R.overdue_days("2026-06-27", "in-progress", None, TODAY, "pj"), 30)

# ── ⑤ is_done は held を完了に数えぬ ──────────────────────────────
chk("is_done: ap は完了", R.is_done("ap", "completed"), True)
chk("is_done: wt(held)は完了に非ず", R.is_done("wt", "held"), False)
chk("is_done: omit(held)は完了に非ず", R.is_done("omit", "held"), False)
chk("is_inactive: wt は非活動(超過にはせぬ)", R.is_inactive("wt", "held"), True)

# ── ⑥ 件数と一覧は同一機構: 3モジュールが同じ答えを出す ──────────────
TASKS = [
    {"id": 1, "project_id": "100", "name": "承認済", "status": "ap", "status_category": "completed",
     "due_date": "2026-06-01", "updated_at": "2026-06-01"},
    {"id": 2, "project_id": "100", "name": "作業中", "status": "wip", "status_category": "in_progress",
     "due_date": "2026-06-27", "updated_at": "2026-06-01"},
    {"id": 3, "project_id": "100", "name": "停止", "status": "wt", "status_category": "held",
     "due_date": "2026-06-01", "updated_at": "2026-06-01"},
]
chk("超過タスク=作業中の1件のみ", [t["id"] for t in TASKS if R.task_overdue_days(t, TODAY)], [2])

import casper_meeting as M
ag = M.agenda_for({"project_id": "100"}, TASKS, TODAY.isoformat(), datetime.datetime(2026, 7, 1))
ids = sorted(i.get("id") for i in ag) if isinstance(ag, list) else ag
chk("MTG議題に承認済(ap)を載せぬ", (1 in (ids or [])), False)
chk("MTG議題に作業中(wip)は載る", (2 in (ids or [])), True)

import casper_notify as N
chk("notify も同じ機構を参照", N._sr is R, True)

print()
ng = results.count(False)
print(f"{'✅ 全PASS' if ng == 0 else '❌ FAIL'}: {results.count(True)}/{len(results)}")
sys.exit(1 if ng else 0)
