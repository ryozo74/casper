#!/usr/bin/env python3
"""M4 Phase 2: 日程変更（reschedule）の純機構（LLM非経由・書き込みゼロ）。

「◯◯の締切を△△に」を安全に扱う3部品:
- resolve_new_due(current_due, phrase)  … 発話の日付表現→新due を決定的に解く（相対=明日/来週/N日延/N日前倒し＋絶対）。
                                           解けねば (None,'date_unresolved')＝qwen に推測させず聞き返しへ回す。
- impact_of(task, tasks, new_due, proj)  … その due を動かすと何が押されるか（dependsOn の下流タスク衝突／PJ末日超過／増減日数）。
- preview(task, tasks, new_due, proj)    … 承認カードに載せる影響プレビュー（人が判断するための材料・機構が接地で出す）。

実行（MCP update_task で due_date を書く）と権限（scope=own は本人/admin・cross は要 BFF）は別層。ここは素材まで。
"""
import re
import datetime
import calendar

RESCHED_VERB = "reschedule"


def _to_date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _parse_absolute(p, today):
    """絶対日付 'YYYY-MM-DD' / 'M/D' / 'M月D日' → date or None（年省略は今年・過去なら翌年）。"""
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", p)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    m = re.search(r"(\d{1,2})\s*[/月]\s*(\d{1,2})\s*日?", p)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        try:
            d = datetime.date(today.year, mo, da)
        except Exception:
            return None
        if d < today:                         # 過ぎた月日は翌年と解す
            try:
                d = datetime.date(today.year + 1, mo, da)
            except Exception:
                return None
        return d
    return None


def resolve_new_due(current_due, phrase, today=None):
    """発話の日付表現→新due(iso文字列)。相対・絶対を決定的に。解けねば (None,'date_unresolved')。"""
    today = today or datetime.date.today()
    p = phrase or ""
    cur = _to_date(current_due)
    base = cur or today                        # 「N日延ばす」は現due基準・無ければ今日基準

    ad = _parse_absolute(p, today)             # ① 絶対日付が最優先
    if ad:
        return ad.isoformat(), None

    m = re.search(r"(\d+)\s*(日|週間|週|ヶ月|か月|カ月|月)", p)   # ② N日/N週/Nヶ月 の増減
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * (7 if "週" in unit else (30 if "月" in unit else 1))
        earlier = bool(re.search(r"(前倒し|早め|早く|繰り上げ|短縮|縮め)", p))
        later = bool(re.search(r"(延ば|延長|遅らせ|後ろ倒し|繰り下げ|伸ば|後|延)", p))
        if earlier and not later:
            return (base - datetime.timedelta(days=days)).isoformat(), None
        return (base + datetime.timedelta(days=days)).isoformat(), None   # 既定は延長(＋)

    for k, d in (("明後日", 2), ("あさって", 2), ("明日", 1), ("あした", 1),
                 ("再来週", 14), ("来週", 7), ("本日", 0), ("今日", 0)):     # ③ キーワード相対(today基準)
        if k in p:
            return (today + datetime.timedelta(days=d)).isoformat(), None
    if "来月" in p:
        y = today.year + (1 if today.month == 12 else 0)
        mo = 1 if today.month == 12 else today.month + 1
        da = min(today.day, calendar.monthrange(y, mo)[1])
        return datetime.date(y, mo, da).isoformat(), None
    if re.search(r"週末|今週末", p):
        return (today + datetime.timedelta(days=(5 - today.weekday()) % 7)).isoformat(), None   # 次の土曜
    return None, "date_unresolved"


def impact_of(task, tasks, new_due, project=None):
    """この due を new_due へ動かした時の影響（純機構）。
    返り: {delta_days, old_due, new_due, downstream:[{id,name,start_date,collides}], project_overrun, project_end}。"""
    old = str(task.get("due_date") or "")[:10]
    nd = _to_date(new_due)
    od = _to_date(old)
    delta = (nd - od).days if (nd and od) else None
    tid = str(task.get("id"))
    downstream = []
    for t in tasks or []:
        deps = [str(x) for x in (t.get("dependsOn") or [])]
        if tid in deps and str(t.get("id")) != tid:
            st = str(t.get("start_date") or "")[:10]
            sd = _to_date(st)
            collides = bool(sd and nd and sd < nd)     # 下流の開始が、動かした後の終了より前＝押し出し衝突
            downstream.append({"id": t.get("id"), "name": t.get("name") or t.get("title"),
                               "start_date": st, "collides": collides})
    pend = str((project or {}).get("end_date") or "")[:10]
    ped = _to_date(pend)
    overrun = bool(ped and nd and nd > ped)
    return {"delta_days": delta, "old_due": old, "new_due": new_due,
            "downstream": downstream, "collision_count": sum(1 for d in downstream if d["collides"]),
            "project_overrun": overrun, "project_end": pend}


def preview(task, tasks, new_due, project=None):
    """承認カード用の影響プレビュー素材（impact_of＋人に見せる注記文）。書き込みしない。"""
    imp = impact_of(task, tasks, new_due, project)
    notes = []
    d = imp["delta_days"]
    if d is not None:
        notes.append(f"{abs(d)}日{'後ろ倒し' if d > 0 else ('前倒し' if d < 0 else '据え置き')}（{imp['old_due']}→{imp['new_due']}）")
    if imp["collision_count"]:
        notes.append(f"⚠ 後続 {imp['collision_count']}件が押し出されまする（開始日が新締切より前）")
    elif imp["downstream"]:
        notes.append(f"後続 {len(imp['downstream'])}件あり（現状は衝突なし）")
    if imp["project_overrun"]:
        notes.append(f"⚠ PJ末日（{imp['project_end']}）を超えまする")
    return {**imp, "task_id": task.get("id"),
            "task_name": task.get("name") or task.get("title"),
            "shot": task.get("shotID") or task.get("shot_id") or "",
            "notes": notes}


def execute(task_id, new_due, actor_uid, snap=None, live_read=None, live_write=None):
    """承認された日程変更を実行。書込直前の安全機構つき（Fable W2）:
      W2: 実行直前に現状を読み戻し、完了(deliver/omit)なら拒否（完了タスクの期日変更は無意味）＋現状の担当/状態で権限再判定。
      権限: allowed(reschedule, scope=own)＝本人 / そのPJの pm・lead / director・admin（＝殿の「ユーザーごと階層化」）。
            これが唯一の守り（Calendar 書込に actor gate は無い＝2026-07-17 実地検証で確認）。allowed を通れば
            誰であれ MCP update_task(due=…, actor_id=承認者) で書ける（Score BFF は不要）。
    live_read(task_id)->task / live_write(task_id, new_due, actor)->ok は注入（本番=chat_server, テスト=mock）。
    返り (ok, info)。"""
    import casper_authority as A
    task_id = str(task_id)
    actor_uid = str(actor_uid)
    cur = None
    if live_read:
        try:
            cur = live_read(task_id)
        except Exception as e:
            return (False, f"read_failed:{e}")
    if cur is None:
        return (False, "read_failed:task_not_found")
    st = str(cur.get("status") or "").lower()
    if st in ("deliver", "omit"):
        return (False, f"task_closed:{st}")
    target = {"project_id": cur.get("project_id"), "assignee": str(cur.get("assigned_to") or "")}
    ok, reason = A.allowed(RESCHED_VERB, actor_uid, target, from_status=st, snap=snap)
    if not ok:
        return (False, f"not_allowed:{reason}")
    if not live_write:
        return (False, "no_write_binding")
    try:
        wok = live_write(task_id, new_due, actor_uid)   # MCP update_task(due=…, actor_id=承認者)
    except Exception as e:
        return (False, f"write_failed:{e}")
    return (bool(wok), "written" if wok else "write_rejected")


if __name__ == "__main__":
    today = datetime.date(2026, 7, 17)
    print("resolve:", resolve_new_due("2026-07-20", "3日延ばして", today))
    print("resolve:", resolve_new_due("2026-07-20", "来週", today))
    print("resolve:", resolve_new_due("2026-07-20", "8月5日", today))
    print("resolve:", resolve_new_due("2026-07-20", "適当に", today))
