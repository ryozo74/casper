#!/usr/bin/env python3
"""M4 Phase 1 アサイン候補検出の回帰ゲート（純機構・インメモリ・LLM/API非使用）。
open_slots(集合差)・precedent(実績索引)・candidates_for(実績順・でっち上げない)・proposals(audience結線)を検証。
全PASSで exit 0。書き込みゼロ。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_assign as S

# ── インメモリのタスク集合（実績: PJ100 の layout は 30 が3回・37 が1回、comp は 40 が2回）──
TASKS = [
    {"id": 1, "project_id": "100", "type": "layout", "status": "deliver", "assigned_to": "30"},
    {"id": 2, "project_id": "100", "type": "layout", "status": "deliver", "assigned_to": "30"},
    {"id": 3, "project_id": "100", "type": "layout", "status": "ap", "assigned_to": "30"},
    {"id": 4, "project_id": "100", "type": "layout", "status": "wip", "assigned_to": "37"},
    {"id": 5, "project_id": "100", "type": "comp", "status": "deliver", "assigned_to": "40"},
    {"id": 6, "project_id": "100", "type": "comp", "status": "ap", "assigned_to": "40"},
    # ── open slots（assignee空 ∧ status∈{wt,mk}）──
    {"id": 10, "project_id": "100", "type": "layout", "status": "mk", "assigned_to": None},   # 候補=30,37（同PJ×同type）
    {"id": 11, "project_id": "100", "type": "comp", "status": "wt", "assigned_to": ""},        # 候補=40
    {"id": 12, "project_id": "999", "type": "novel", "status": "mk", "assigned_to": None},     # 実績無し→候補なし
    # ── open slot でないもの（除外されること）──
    {"id": 20, "project_id": "100", "type": "layout", "status": "wip", "assigned_to": None},   # status非対象→除外
    {"id": 21, "project_id": "100", "type": "layout", "status": "mk", "assigned_to": "30"},     # 既にアサイン済→除外
]
NAMES = {"30": "tetsuo", "37": "rui", "40": "terajima"}

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── open_slots: 集合差（3件のみ・wip や アサイン済は除外）──
slots = S.open_slots(TASKS)
chk("open_slots count=3", len(slots), 3)
chk("open_slots ids", sorted(t["id"] for t in slots), [10, 11, 12])
chk("wip(assignee空)は対象外", 20 not in [t["id"] for t in slots], True)
chk("mk(アサイン済)は対象外", 21 not in [t["id"] for t in slots], True)

# ── assignable_status は verbs.yaml 由来（ハードコードでない）──
chk("assignable⊇{wt,mk}", {"wt", "mk"} <= S.assignable_status(), True)

# ── candidates_for: 実績順（同PJ×同type が最優先・頻度降順）──
prec = S.precedent(TASKS)
slot10 = next(t for t in slots if t["id"] == 10)
c10 = S.candidates_for(slot10, prec, NAMES)
chk("slot10 候補先頭=tetsuo(実績3)", c10[0]["uid"], "30")
chk("slot10 候補先頭 basis", c10[0]["basis"], "same_project_type")
chk("slot10 候補にrui(実績1)含む", any(c["uid"] == "37" for c in c10), True)
chk("slot10 頻度降順(30は id1/2/3/21 の4件・21はmkだがアサイン済ゆえ実績)", [c["count"] for c in c10][:2], [4, 1])
chk("slot10 は最強tierのみ(同PJ×同type=layoutの2名で確定・40混ざらぬ)", [c["uid"] for c in c10], ["30", "37"])

slot11 = next(t for t in slots if t["id"] == 11)
c11 = S.candidates_for(slot11, prec, NAMES)
chk("slot11 候補=terajima", [c["uid"] for c in c11], ["40"])

# ── 実績無しスロットは候補ゼロ（でっち上げない・失敗とゼロを別出口）──
slot12 = next(t for t in slots if t["id"] == 12)
chk("slot12 候補は空(捏造しない)", S.candidates_for(slot12, prec, NAMES), [])

# ── topn 制限 ──
chk("topn=1 で1件に絞る", len(S.candidates_for(slot10, prec, NAMES, topn=1)), 1)

# ── proposals: audience 結線（snapshot 無し→権限不明→admin縮退 or 空・でも例外を投げない）──
snap = {"fetched_at": "2999-01-01T00:00:00", "roles": {"28": "admin", "30": "lead", "29": "pm"},
        "projects": {"100": {"pm": ["29"], "lead": ["30"]}}}
ps = S.proposals(TASKS, NAMES, snap=snap)
chk("proposals count=open_slots", len(ps), 3)
p10 = next(p for p in ps if p["task_id"] == 10)
chk("proposal audience=pm/lead+admin", p10["audience"], ["28", "29", "30"])
chk("proposal candidates 埋まる", [c["uid"] for c in p10["candidates"]], ["30", "37"])
chk("proposal は書込フィールドを持たぬ(素材のみ)", "execute" not in p10 and "written" not in p10, True)

# ── execute: W2 実行前ガード＋tier経路分岐（IO注入・副作用ゼロ・live write しない）──
import casper_authority as A

# ground-truth admin（authority.yaml の 28）は snapshot 無しでも admin
chk("ground-truth admin(28) は snapshot無しでも admin", A.tier_of("28", {}), "admin")
chk("非登録uidは user", A.tier_of("777", {}), "user")

# mock IO: 現状 = 空きスロット（task 10 相当）
def _read_open(_tid):
    return {"id": _tid, "project_id": "100", "type": "layout", "status": "mk", "assigned_to": None}
_WROTE = {}
def _write_ok(tid, uid, actor):
    _WROTE["call"] = (tid, uid, actor); return True

# admin(28) 承認 → MCP経路で今すぐ書ける（live_write が呼ばれる）
ok, info = S.execute("10", "30", "28", snap=snap, live_read=_read_open, live_write=_write_ok)
chk("admin承認→written", (ok, info), (True, "written"))
chk("admin経路で live_write が actor=28 で呼ばれた", _WROTE.get("call"), ("10", "30", "28"))

# pm(29・PJ100のpm) 承認 → allowed を通り MCP で書ける（actor gate 無し＝BFF不要）
ok2, info2 = S.execute("10", "30", "29", snap=snap, live_read=_read_open, live_write=_write_ok)
chk("pm承認→written(MCP・actor gate無)", (ok2, info2), (True, "written"))
chk("pm経路も live_write が actor=29 で呼ばれた", _WROTE.get("call"), ("10", "30", "29"))

# user(31) 承認 → tier不足で not_allowed
ok3, info3 = S.execute("10", "30", "31", snap=snap, live_read=_read_open, live_write=_write_ok)
chk("user承認→not_allowed(tier)", ok3, False)
chk("user承認 reason=tier", "not_allowed:tier_too_low" == info3, True)

# W2: 承認後に別人が着手済（assigned_to 埋まった）→ TOCTOU default-deny
def _read_taken(_tid):
    return {"id": _tid, "project_id": "100", "status": "mk", "assigned_to": "99"}
ok4, info4 = S.execute("10", "30", "28", snap=snap, live_read=_read_taken, live_write=_write_ok)
chk("W2: 既に割当済→toctou拒否(書込まない)", (ok4, info4), (False, "toctou_already_assigned:99"))

# W2: status が assignable を外れた（wip へ進んだ）→ default-deny
def _read_moved(_tid):
    return {"id": _tid, "project_id": "100", "status": "wip", "assigned_to": None}
ok5, info5 = S.execute("10", "30", "28", snap=snap, live_read=_read_moved, live_write=_write_ok)
chk("W2: status移動→toctou拒否", (ok5, info5), (False, "toctou_status_moved:wip"))

# 読み戻し不可（task消失）→ 実行しない（安全側）
ok6, info6 = S.execute("10", "30", "28", snap=snap, live_read=lambda _t: None, live_write=_write_ok)
chk("読み戻し不可→実行しない", ok6, False)

n = len(results)
p = sum(results)
print(f"\n=== gate_assign: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
