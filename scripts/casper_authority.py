#!/usr/bin/env python3
"""M4 権限層（Fable設計・2026-07-17）— 純関数のみ・LLM関与ゼロ・書き込み一切なし。

「誰が・何を・誰に見せて」を機構で決める唯一の場所。全ての承認カード（アサイン/日程/status/会議）が
ここを通る＝カード種別ごとに権限コードを書かない（機構は畳む）。

- tier_of(uid)                         → tier（Score role を写した snapshot 由来・不明は最下位 user）
- audience_for(verb, target)           → 承認カードを見せる uid 集合（verb の audience 規則＋target から）
- allowed(verb, uid, target, from_status) → (ok, reason)  tier≥min_tier ∧ scope適合 ∧ from_status適合 ∧ verb存在

真実源:
- config/verbs.yaml       … 動詞レジストリ
- config/authority.yaml   … role→tier・tier順序・scope・degrade閾値
- authority_snapshot.json … Score の role/project担当を写した snapshot（{roles:{uid:role},
                            projects:{pid:{pm:[uid],lead:[uid]}}, fetched_at:iso}）※populate は別途(Elvis経路)

Fable掟: snapshot が古い（>閾値）＝「権限不明」→ admin のみに縮退（失敗とゼロを別出口）。推測で通さない/止めない。
"""
import os
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(HERE, "config")
SNAPSHOT = os.path.join(HERE, "authority_snapshot.json")


def _yaml(name):
    import yaml
    try:
        return yaml.safe_load(open(os.path.join(CONFIG_DIR, name), encoding="utf-8")) or {}
    except Exception:
        return {}


def verbs():
    return _yaml("verbs.yaml").get("verbs", {}) or {}


def _auth():
    return _yaml("authority.yaml") or {}


def _load_snapshot():
    try:
        return json.load(open(SNAPSHOT, encoding="utf-8"))
    except Exception:
        return {}


def snapshot_fresh(snap):
    """snapshot が閾値内の鮮度か。fetched_at 欠落/古い は False。"""
    fa = (snap or {}).get("fetched_at")
    if not fa:
        return False
    try:
        age_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(fa)).total_seconds() / 3600.0
    except Exception:
        return False
    return age_h <= float(_auth().get("snapshot_max_age_hours", 48))


def _ground_admins():
    """snapshot に依らず常に admin として扱う不変の identity（authority.yaml・殿等）。"""
    return set(str(u) for u in (_auth().get("ground_truth_admins") or []))


def tier_of(uid, snap=None):
    """uid の tier。ground-truth admin は無条件 admin。次に snapshot の role を role→tier で写す。role不明は 'user'。"""
    if str(uid) in _ground_admins():
        return "admin"
    snap = _load_snapshot() if snap is None else snap
    role = (snap.get("roles") or {}).get(str(uid))
    if not role:
        return "user"
    return (_auth().get("roles_to_tier") or {}).get(role, "user")


def _tier_ge(a, b):
    order = _auth().get("tier_order", ["user", "lead", "pm", "director", "admin"])
    try:
        return order.index(a) >= order.index(b)
    except ValueError:
        return False


def _pj_holders(pid, role, snap):
    return set(str(u) for u in (((snap.get("projects") or {}).get(str(pid), {}) or {}).get(role, []) or []))


def _by_role(role, snap):
    out = set(str(u) for u, r in (snap.get("roles") or {}).items() if r == role)
    if role == "admin":                                # ground-truth admin は snapshot に無くても admin 集合に含める
        out |= _ground_admins()
    return out


def audience_for(verb, target, snap=None):
    """このverbの承認カードを見せる uid 集合（ソート済リスト）。snapshot が古ければ admin のみに縮退。"""
    snap = _load_snapshot() if snap is None else snap
    if not snapshot_fresh(snap):
        return sorted(_by_role("admin", snap))
    v = verbs().get(verb, {})
    rule = v.get("audience", "scope")
    pid = str(target.get("project_id") or "")
    pm = _pj_holders(pid, "pm", snap)
    lead = _pj_holders(pid, "lead", snap)
    admins = _by_role("admin", snap)
    dirs = _by_role("director", snap)
    if rule == "pm_lead":
        out = pm | lead
    elif rule == "pm_director":
        out = pm | dirs
    elif rule == "director_admin":
        out = dirs | admins
    elif rule == "organizer_only":
        out = {str(target.get("organizer") or "")} - {""}
    else:  # scope: 担当本人＋PJのpm/lead
        out = ({str(target.get("assignee") or "")} - {""}) | pm | lead
    # admin は常に見える（統括）
    out |= admins
    return sorted(out)


def _in_scope(scope, uid, target, snap):
    uid = str(uid)
    pid = str(target.get("project_id") or "")
    t = tier_of(uid, snap)
    holders = _pj_holders(pid, "pm", snap) | _pj_holders(pid, "lead", snap)
    if scope == "own":
        if uid == str(target.get("assignee") or ""):
            return True
        if t in ("pm", "lead") and uid in holders:
            return True
        return t in ("director", "admin")
    if scope == "project":
        if t in ("director", "admin"):
            return True
        return uid in holders
    if scope == "cross":
        return t in ("director", "admin")
    return False


def allowed(verb, uid, target, from_status="", snap=None):
    """(ok, reason)。この uid が、この target に対し、この verb を（現状態 from_status から）提案/承認してよいか。"""
    snap = _load_snapshot() if snap is None else snap
    v = verbs().get(verb)
    if not v:
        return (False, "unknown_verb")
    # degrade: snapshot が古い＝admin のみ
    if not snapshot_fresh(snap):
        return (True, "ok") if tier_of(uid, snap) == "admin" else (False, "snapshot_stale_admin_only")
    # tier
    if not _tier_ge(tier_of(uid, snap), v.get("min_tier", "admin")):
        return (False, "tier_too_low")
    # scope
    if not _in_scope(v.get("scope", "project"), uid, target, snap):
        return (False, "out_of_scope")
    # from_status（遷移契約・暫定 verbs.yaml → 将来 transitions.json）。W2=default-deny の一次防壁。
    # ★fail-closed（Fable監査2026-07-17 綻びA）: 契約が特定の from を要求するなら、from_status が空/不明の時は
    #   「契約を素通りして許可」でなく **拒否**する（状態不明＝安全側で止める。default-allow の穴を塞ぐ）。
    fs = v.get("from_status", "any")
    if fs != "any":
        if not from_status:
            return (False, "unknown_from_status")
        if from_status not in fs:
            return (False, "from_status_not_allowed:%s->%s" % (from_status, v.get("to_status") or v.get("changes") or verb))
    return (True, "ok")


if __name__ == "__main__":
    snap = _load_snapshot()
    print("verbs:", list(verbs().keys()))
    print("snapshot fresh:", snapshot_fresh(snap), "/ roles:", len((snap.get("roles") or {})))
