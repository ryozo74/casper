#!/usr/bin/env python3
"""M4 Phase 1: アサイン提案の候補検出（純機構・LLM非使用・書き込みゼロ）。

「誰に振るか」を機構が決定的に出す層。qwen は生成せず、この候補から選ぶだけ（捏造の芽を断つ）。
実行（BFF 書込）と権限 snapshot の populate は別層（Elvis Q②③待ち）＝ここは "提案素材" まで。

- open_slots(tasks)                → アサイン待ちスロット＝assignee空 ∧ status∈assignable（集合差）。
- precedent(tasks)                 → 実績索引: (project,type)/project/type ごとに「過去に誰が担当したか」の頻度。
- candidates_for(task, precedent…) → そのスロットに宛てる担当候補（実績の強い順・topN）。素材が無ければ空（でっち上げない）。
- proposals(tasks, roster, snap)   → open_slots × candidates を承認カード素材へ。audience は権限層(casper_authority)が決める。

真実源: assignable status は config/verbs.yaml の assign.from_status（単一ソース・ここに数値を焼かない）。
"""
import collections

import casper_authority as A

ASSIGN_VERB = "assign"


def assignable_status():
    """アサイン提案の対象 status 集合。verbs.yaml の assign.from_status を唯一のソースに（ハードコード禁止）。"""
    fs = (A.verbs().get(ASSIGN_VERB, {}) or {}).get("from_status", ["wt", "mk"])
    if isinstance(fs, (list, tuple, set)):
        return {str(s).lower() for s in fs}
    return {"wt", "mk"}


def open_slots(tasks):
    """アサイン待ち＝assigned_to が空 ∧ status∈assignable のタスク（集合差検出・純機構）。"""
    ok = assignable_status()
    out = []
    for t in tasks or []:
        if t.get("assigned_to"):
            continue
        if str(t.get("status") or "").lower() not in ok:
            continue
        out.append(t)
    return out


def precedent(tasks):
    """実績索引を作る: 既にアサイン済のタスクから「(project,type)/project/type ごとに誰が何回担当したか」。
    返り: (pt, pj, ty) いずれも key→collections.Counter({uid:count})。"""
    pt = collections.defaultdict(collections.Counter)   # (project_id, type) → uid頻度（最強シグナル）
    pj = collections.defaultdict(collections.Counter)   # project_id       → uid頻度
    ty = collections.defaultdict(collections.Counter)   # type             → uid頻度
    for t in tasks or []:
        a = t.get("assigned_to")
        if not a:
            continue
        a = str(a)
        p = str(t.get("project_id") or "")
        k = str(t.get("type") or "")
        pt[(p, k)][a] += 1
        pj[p][a] += 1
        ty[k][a] += 1
    return pt, pj, ty


def candidates_for(task, prec, roster_names=None, topn=3):
    """このスロットに宛てる担当候補を実績から決定的に。**最も強い非空 tier で確定**する
    （同PJ×同type ▷ 同PJ ▷ 同type の順に見て、最初に候補を出せた tier だけを採る）。
    弱い tier を混ぜて水増ししない＝雑音を断つ。どの tier も空なら [] （でっち上げない・「候補なし」は上位で別扱い）。
    qwen は生成せず、この候補から選ぶだけ。"""
    roster_names = roster_names or {}
    pt, pj, ty = prec
    p = str(task.get("project_id") or "")
    k = str(task.get("type") or "")
    for counter, basis in ((pt.get((p, k)), "same_project_type"),
                           (pj.get(p), "same_project"),
                           (ty.get(k), "same_type")):
        if counter:
            return [{"uid": uid, "name": roster_names.get(uid, uid), "count": c, "basis": basis}
                    for uid, c in counter.most_common(topn)]
    return []


def proposals(tasks, roster_names=None, snap=None, topn=3):
    """アサイン提案素材の一覧（承認カードにする前段・書込しない）。
    各要素: {task_id, task_name, project_id, type, status, candidates:[…], audience:[uid…]}。
    audience は casper_authority が決める（このカードを誰に見せるか）＝権限層は一箇所。"""
    roster_names = roster_names or {}
    prec = precedent(tasks)
    out = []
    for t in open_slots(tasks):
        cands = candidates_for(t, prec, roster_names, topn=topn)
        target = {"project_id": t.get("project_id"), "assignee": ""}
        out.append({
            "task_id": t.get("id"),
            "task_name": t.get("name") or t.get("title"),
            "shot": t.get("shotID") or t.get("shot_id") or "",
            "project_id": t.get("project_id"),
            "type": t.get("type") or "",
            "status": str(t.get("status") or "").lower(),
            "candidates": cands,             # 実績由来の決定的候補（空なら「候補なし」＝人手判断へ）
            "audience": A.audience_for(ASSIGN_VERB, target, snap),
        })
    return out


def execute(task_id, assignee_uid, approver_uid, snap=None, live_read=None, live_write=None):
    """承認されたアサインを実行。書込直前の安全機構つき（Fable W2/W3）:
      W2: **実行直前に現状を読み戻し**、まだ assignee空 ∧ status∈assignable でなければ拒否（TOCTOU防御・
          承認カードを見た後に別人が着手/割当済かもしれぬ＝default-deny）。
      権限: casper_authority.allowed が唯一の守り（Calendar/Score とも書込に actor/role gate が"無い"＝
            2026-07-17 実地検証で確認。ゆえ弾かれて死ぬのでなく、不正な提案をそもそも通さぬ設計）。allowed を通れば
            誰であれ MCP update_task(actor_id=承認者) で書ける（Score BFF は不要）。
    live_read(task_id)->task / live_write(task_id, assignee_uid, actor)->ok は注入（本番=chat_server, テスト=mock）。
    返り: (ok: bool, info: str)。"""
    task_id = str(task_id)
    approver_uid = str(approver_uid)
    assignee_uid = str(assignee_uid)

    # W2-a: 現状を読み戻す（読めなければ実行しない＝安全側）
    cur = None
    if live_read:
        try:
            cur = live_read(task_id)
        except Exception as e:
            return (False, f"read_failed:{e}")
    if cur is None:
        return (False, "read_failed:task_not_found")
    # W2-b: まだアサイン待ちのままか（空き∧status∈assignable）。変わっていれば default-deny。
    if cur.get("assigned_to"):
        return (False, f"toctou_already_assigned:{cur.get('assigned_to')}")
    if str(cur.get("status") or "").lower() not in assignable_status():
        return (False, f"toctou_status_moved:{cur.get('status')}")

    # 権限＝唯一の守り（Casper側の allowed で tier/scope/from_status を判定。書込側にgateは無い）
    target = {"project_id": cur.get("project_id"), "assignee": ""}
    ok, reason = A.allowed(ASSIGN_VERB, approver_uid, target, from_status=str(cur.get("status") or "").lower(), snap=snap)
    if not ok:
        return (False, f"not_allowed:{reason}")
    if not live_write:
        return (False, "no_write_binding")
    try:
        wok = live_write(task_id, assignee_uid, approver_uid)   # MCP update_task(assignee=…, actor_id=承認者)
    except Exception as e:
        return (False, f"write_failed:{e}")
    return (bool(wok), "written" if wok else "write_rejected")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import casper_secrets
    casper_secrets.load_into_env()
    import casper_notify as N
    import json
    tasks = N._all_tasks()
    try:
        us = json.load(open("/tmp/cal_users.json")).get("items", [])
        names = {str(u.get("id")): u.get("username") for u in us}
    except Exception:
        names = {}
    ps = proposals(tasks, names)
    print(f"open slots (assignee空∧status∈{sorted(assignable_status())}): {len(ps)}")
    for p in ps:
        cs = "、".join(f"{c['name']}({c['count']}·{c['basis']})" for c in p["candidates"]) or "（候補なし=実績無し）"
        print(f"  #{p['task_id']} [{p['project_id']}/{p['type']}/{p['status']}] {p['task_name']} → 候補: {cs} / 表示対象uid:{p['audience']}")
