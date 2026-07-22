#!/usr/bin/env python3
"""M4 Phase 4: status 更新 verb の実行機構（純機構・W2ガード付き）。

対象 verb（verbs.yaml）: mark_delivered→deliver / record_client_approval→client_ap（根拠リンク必須）/ omit_task→omit（typed確認）。
※transitions.json 監査で判明: deliver/client_ap/omit は Score UI に遷移経路が無い（roles=None・未配線）＝Score は何も強制せぬ。
  ゆえ **Casper の権限層（verbs.yaml min_tier/from_status を allowed が判定）が唯一の守り**（Elvis③の結論）。
  Nibu 是正により update_task は新9値を受理（2026-07-17 実測）＝MCP 直書きで可。

- execute(verb, task_id, approver_uid, snap, evidence, live_read, live_write) → (ok, info)
  W2: 実行直前に現状読み戻し → from_status で allowed 再判定（承認後に状態が動いていれば default-deny）。
  record_client_approval は evidence（根拠リンク）未指定なら拒否（殿決定: 根拠リンク必須）。
"""
STATUS_VERBS = {"mark_delivered", "record_client_approval", "omit_task"}


def verb_to_status(verb):
    import casper_authority as A
    return (A.verbs().get(verb, {}) or {}).get("to_status")


def execute(verb, task_id, approver_uid, snap=None, evidence=None, live_read=None, live_write=None):
    """status verb を実行。返り (ok, info)。live_write(task_id, to_status, actor)->ok は注入。"""
    import casper_authority as A
    if verb not in STATUS_VERBS:
        return (False, "unknown_verb")
    v = A.verbs().get(verb) or {}
    to_status = v.get("to_status")
    if not to_status:
        return (False, "verb_misconfigured")
    task_id = str(task_id)
    approver_uid = str(approver_uid)

    cur = None
    if live_read:
        try:
            cur = live_read(task_id)
        except Exception as e:
            return (False, f"read_failed:{e}")
    if cur is None:
        return (False, "read_failed:task_not_found")
    from_status = str(cur.get("status") or "").lower()
    if from_status == to_status:
        return (False, f"already:{to_status}")

    # 権限＝唯一の守り（tier/scope/from_status を allowed が判定。書込側にgateは無い＝Casperが最終防壁）
    target = {"project_id": cur.get("project_id"), "assignee": str(cur.get("assigned_to") or "")}
    ok, reason = A.allowed(verb, approver_uid, target, from_status=from_status, snap=snap)
    if not ok:
        return (False, f"not_allowed:{reason}")

    # 根拠リンク必須（record_client_approval・殿決定）
    if v.get("require_evidence") and not (evidence and str(evidence).strip()):
        return (False, "evidence_required")

    if not live_write:
        return (False, "no_write_binding")
    try:
        wok = live_write(task_id, to_status, approver_uid)   # MCP update_task(status=to_status, actor_id=承認者)
    except Exception as e:
        return (False, f"write_failed:{e}")
    return (bool(wok), f"written:{to_status}" if wok else "write_rejected")
