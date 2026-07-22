#!/usr/bin/env python3
"""M4 権限層の回帰ゲート（gate_authority・Fable「goldenで先に緑にする」）。
casper_authority の純関数（tier_of/audience_for/allowed）＋casper_outbox の audience 拡張を、
インメモリの test snapshot で検証。書き込みは最小(1件proposeして即reject掃除)。全PASSで exit 0。
"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_authority as A
import casper_outbox as O

NOW = datetime.datetime.now()
FRESH = NOW.isoformat(timespec="seconds")
OLD = (NOW - datetime.timedelta(hours=72)).isoformat(timespec="seconds")

SNAP = {
    "fetched_at": FRESH,
    "roles": {"28": "admin", "29": "pm", "30": "lead", "31": "user", "40": "director", "50": "user"},
    "projects": {"100": {"pm": ["29"], "lead": ["30"]}},
}
STALE = dict(SNAP, fetched_at=OLD)

TASK = {"project_id": "100", "assignee": "31"}          # PJ100・担当31
MTG = {"project_id": "100", "organizer": "29"}          # 主催29

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── tier_of ──
chk("tier admin", A.tier_of("28", SNAP), "admin")
chk("tier pm", A.tier_of("29", SNAP), "pm")
chk("tier unknown→user", A.tier_of("999", SNAP), "user")

# ── allowed: tier / scope / from_status ──
chk("assign pm ok", A.allowed("assign", "29", TASK, "mk", SNAP)[0], True)
chk("assign user→tier_too_low", A.allowed("assign", "31", TASK, "mk", SNAP), (False, "tier_too_low"))
chk("assign lead from wip→deny", A.allowed("assign", "30", TASK, "wip", SNAP)[0], False)
chk("omit director ok", A.allowed("omit_task", "40", TASK, "wip", SNAP)[0], True)
chk("omit pm→tier_too_low", A.allowed("omit_task", "29", TASK, "wip", SNAP), (False, "tier_too_low"))
chk("reschedule own(assignee) ok", A.allowed("reschedule", "31", TASK, "wip", SNAP)[0], True)
chk("reschedule other user→out_of_scope", A.allowed("reschedule", "50", TASK, "wip", SNAP), (False, "out_of_scope"))
chk("mark_delivered from ap ok", A.allowed("mark_delivered", "29", TASK, "ap", SNAP)[0], True)
chk("mark_delivered from wip→deny", A.allowed("mark_delivered", "29", TASK, "wip", SNAP)[0], False)
chk("create_meeting pm ok", A.allowed("create_meeting", "29", MTG, "", SNAP)[0], True)
chk("unknown verb→deny", A.allowed("frobnicate", "28", TASK, "", SNAP), (False, "unknown_verb"))
# fail-closed（Fable監査 綻びA）: from を要求する verb は from_status 空だと拒否（default-allow の穴を塞ぐ）
chk("assign from空→unknown_from_status", A.allowed("assign", "29", TASK, "", SNAP), (False, "unknown_from_status"))
chk("omit(from=any)は空でも通る", A.allowed("omit_task", "40", TASK, "", SNAP)[0], True)

# ── audience_for ──
chk("aud assign=pm/lead+admin", A.audience_for("assign", TASK, SNAP), ["28", "29", "30"])
chk("aud omit=director+admin", A.audience_for("omit_task", TASK, SNAP), ["28", "40"])
chk("aud create_meeting=organizer+admin", A.audience_for("create_meeting", MTG, SNAP), ["28", "29"])

# ── degrade（snapshot 古い→admin のみ）──
chk("stale: pm assign→admin_only", A.allowed("assign", "29", TASK, "mk", STALE), (False, "snapshot_stale_admin_only"))
chk("stale: admin assign ok", A.allowed("assign", "28", TASK, "mk", STALE)[0], True)
chk("stale: audience→admins only", A.audience_for("assign", TASK, STALE), ["28"])

# ── outbox audience（後方互換＋フィルタ）──
chk("outbox _audience backcompat", O._audience({"uid": "5"}), ["5"])
chk("outbox _audience set", O._audience({"uid": "5", "audience": ["7", "8"]}), ["7", "8"])

# 統合: propose(audience=[29,30]) → pending 可視/不可視 → reject 掃除
rec = O.propose("update_task", {"task_id": 1, "_test": "gate_authority"}, "28",
                "テスト", verb="assign", audience=["29", "30"])
pid = rec["id"]
chk("pending in-audience sees", any(r["id"] == pid for r in O.pending("29")), True)
chk("pending out-audience hidden", any(r["id"] == pid for r in O.pending("31")), False)
chk("approve by out-audience blocked", O._transition(pid, "approved", expect="proposed", uid="31") is None, True)
O.reject(pid, uid="29")          # 掃除(audience内の29でreject)
chk("cleanup rejected", (O.get(pid) or {}).get("state"), "rejected")

n = len(results); p = sum(results)
print(f"\n=== gate_authority: {p}/{n} = {p*100//n if n else 0}% ===")
sys.exit(0 if p == n else 1)
