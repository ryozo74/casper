#!/usr/bin/env python3
"""M4 Phase4 status verb の回帰ゲート（純機構・インメモリ・IO注入・副作用ゼロ）。
mark_delivered/record_client_approval/omit_task の W2ガード・from_status契約・tier・根拠リンク必須・既達拒否。全PASSで exit 0。"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_status as S
import casper_authority as A

NOW = datetime.datetime.now()
FRESH = NOW.isoformat(timespec="seconds")
SNAP = {"fetched_at": FRESH,
        "roles": {"28": "admin", "29": "pm", "30": "lead", "31": "user", "40": "director"},
        "projects": {"100": {"pm": ["29"], "lead": ["30"]}}}
results = []
_W = {}


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def rd(status):
    return lambda t: {"id": t, "project_id": "100", "status": status, "assigned_to": "31"}


def wr(t, to, actor):
    _W["c"] = (t, to, actor); return True


# ── mark_delivered: from∈{ap,client_ap}・pm可 ──
chk("deliver: pmがap→deliver written", S.execute("mark_delivered", "5", "29", SNAP, None, rd("ap"), wr), (True, "written:deliver"))
chk("deliver: live_write が status=deliver/actor=29", _W.get("c"), ("5", "deliver", "29"))
chk("deliver: wip からは不可(from_status契約外)", S.execute("mark_delivered", "5", "29", SNAP, None, rd("wip"), wr)[0], False)
chk("deliver: user(31)は tier不足", S.execute("mark_delivered", "5", "31", SNAP, None, rd("ap"), wr), (False, "not_allowed:tier_too_low"))
chk("deliver: 既にdeliver→already", S.execute("mark_delivered", "5", "29", SNAP, None, rd("deliver"), wr), (False, "already:deliver"))

# ── record_client_approval: 根拠リンク必須 ──
chk("client_ap: 根拠リンク無し→拒否", S.execute("record_client_approval", "5", "29", SNAP, None, rd("ap"), wr), (False, "evidence_required"))
chk("client_ap: 根拠リンク有り→written", S.execute("record_client_approval", "5", "29", SNAP, "https://ok/link", rd("ap"), wr), (True, "written:client_ap"))
chk("client_ap: 空白のみの根拠→拒否", S.execute("record_client_approval", "5", "29", SNAP, "   ", rd("ap"), wr), (False, "evidence_required"))

# ── omit_task: director以上・from=any ──
chk("omit: director(40)可(from=any=wipでも)", S.execute("omit_task", "5", "40", SNAP, None, rd("wip"), wr), (True, "written:omit"))
chk("omit: pm(29)は tier不足(director要)", S.execute("omit_task", "5", "29", SNAP, None, rd("wip"), wr), (False, "not_allowed:tier_too_low"))
chk("omit: admin(28)も可", S.execute("omit_task", "5", "28", SNAP, None, rd("qc"), wr)[0], True)

# ── 共通ガード ──
chk("未知verb→unknown_verb", S.execute("frobnicate", "5", "28", SNAP, None, rd("ap"), wr), (False, "unknown_verb"))
chk("読み戻し不可→実行しない", S.execute("mark_delivered", "5", "29", SNAP, None, lambda t: None, wr)[0], False)
chk("write束縛無し→no_write_binding", S.execute("omit_task", "5", "40", SNAP, None, rd("wip"), None), (False, "no_write_binding"))

# ── Fable監査 綻びA: from_status が空/不明 → fail-closed（default-allowの穴を塞ぐ）──
chk("空statusのタスクにdeliver→unknown_from_statusで拒否", S.execute("mark_delivered", "5", "29", SNAP, None, rd(""), wr), (False, "not_allowed:unknown_from_status"))
chk("None statusにdeliver→拒否", S.execute("mark_delivered", "5", "29", SNAP, None, lambda t: {"id": t, "project_id": "100", "status": None, "assigned_to": "31"}, wr), (False, "not_allowed:unknown_from_status"))
chk("空status直接allowも拒否", A.allowed("mark_delivered", "29", {"project_id": "100", "assignee": "31"}, from_status="", snap=SNAP), (False, "unknown_from_status"))
# omit は from=any ゆえ空statusでも通る（契約が from を要求しないので fail-closed 対象外）
chk("omit(from=any)は空statusでも通る", S.execute("omit_task", "5", "40", SNAP, None, rd(""), wr)[0], True)

# ── Fable監査 綻びB: deliver→client_ap 後退は塞がれた ──
chk("deliver→client_ap後退→拒否(from契約外)", A.allowed("record_client_approval", "29", {"project_id": "100", "assignee": "31"}, from_status="deliver", snap=SNAP)[0], False)
chk("ap→client_apは通る(正当)", A.allowed("record_client_approval", "29", {"project_id": "100", "assignee": "31"}, from_status="ap", snap=SNAP)[0], True)

# ── snapshot 古い→admin縮退（deliverはpmだが停止・adminのみ）──
STALE = dict(SNAP, fetched_at=(NOW - datetime.timedelta(hours=72)).isoformat(timespec="seconds"))
chk("stale: pm deliver→admin_only", S.execute("mark_delivered", "5", "29", STALE, None, rd("ap"), wr), (False, "not_allowed:snapshot_stale_admin_only"))
chk("stale: admin deliver→可", S.execute("mark_delivered", "5", "28", STALE, None, rd("ap"), wr)[0], True)

n = len(results)
p = sum(results)
print(f"\n=== gate_status: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
