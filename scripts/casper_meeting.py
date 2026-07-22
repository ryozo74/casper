#!/usr/bin/env python3
"""M4 Phase 2': MTG助言（読取のみ・書き込みゼロ・LLMは文面化のみ）。

会議まわりの"先回り"を集合演算で出す。qwen は候補を生成せず、機構が出した集合を語るだけ（捏造の芽を断つ）。
- upcoming_meetings(events, now)      … 直近(既定48h)に始まる Meeting。
- agenda_for(meeting, tasks, today)   … その会議で「この際これも確認」＝当該PJの注意タスク集合
                                         （確認待ち qc/qc_fb・納期超過・前回会議以降に動いた）。候補ゼロ＝"積み残しなし"。
- meetings_due(events, tasks, today)  … 「そろそろ定例」＝(PJ)ごとの Meeting 間隔中央値から、開くべき頃合いを過ぎたPJ。
                                         過去3回未満は判定しない（根拠不足は黙る＝失敗とゼロを別出口）。
"""
import datetime
from collections import defaultdict

MEETING_TYPE = "Meeting"
try:                                                   # 確認待ち系status＝casper_notify の停滞FB集合と同一。二重定義を畳む(Fable監査2026-07-17)
    from casper_notify import _STALL_STATUS as _CONFIRM_WAIT
except Exception:
    _CONFIRM_WAIT = {"qc", "qc_fb", "dir_wt", "v1qc", "ap_fb", "dir_fb"}   # フォールバック(import不可時)
_DONE = {"deliver", "omit"}


def _dt(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "").strip())
    except Exception:
        return None


def _d(s):
    v = _dt(s)
    return v.date() if v else None


def _is_meeting(e):
    return str(e.get("type") or e.get("event_type") or "") == MEETING_TYPE


def upcoming_meetings(events, now, within_h=48):
    """now 〜 now+within_h に始まる Meeting（開始昇順）。"""
    hi = now + datetime.timedelta(hours=within_h)
    out = [e for e in (events or []) if _is_meeting(e) and (_dt(e.get("start_time")) and now <= _dt(e.get("start_time")) <= hi)]
    return sorted(out, key=lambda e: _dt(e.get("start_time")) or now)


def last_meeting_before(events, project_id, before_dt):
    """当該PJで before_dt より前の直近 Meeting 日時（前回会議＝議題の変化起点）。無ければ None。"""
    pid = str(project_id or "")
    past = [_dt(e.get("start_time")) for e in (events or [])
            if _is_meeting(e) and str(e.get("project_id") or "") == pid
            and _dt(e.get("start_time")) and _dt(e.get("start_time")) < before_dt]
    return max(past) if past else None


def agenda_for(meeting, tasks, today, last_meeting_dt=None):
    """会議の議題候補＝当該PJの注意タスク集合（確認待ち／納期超過／前回会議以降に更新）。全て集合演算。
    返り: [{id,name,assigned_to,status,due,reasons:[…]}]（理由の多い順）。空＝積み残しなし。"""
    pid = str(meeting.get("project_id") or "")
    last_d = last_meeting_dt.date() if isinstance(last_meeting_dt, datetime.datetime) else last_meeting_dt
    items = []
    for t in tasks or []:
        if str(t.get("project_id") or "") != pid:
            continue
        st = str(t.get("status") or "").lower()
        if st in _DONE:
            continue
        reasons = []
        if st in _CONFIRM_WAIT:
            reasons.append("確認待ち")
        due = str(t.get("due_date") or "")[:10]
        if due and due < today:
            reasons.append("納期超過")
        up = _d(t.get("updated_at"))
        if last_d and up and up >= last_d:
            reasons.append("前回会議以降に更新")
        if reasons:
            items.append({"id": t.get("id"), "name": t.get("name") or t.get("title"),
                          "assigned_to": str(t.get("assigned_to") or ""), "status": st,
                          "due": due, "reasons": reasons})
    items.sort(key=lambda x: (-len(x["reasons"]), str(x["due"] or "9999")))
    return items


def _now_dt(now):
    if isinstance(now, datetime.datetime):
        return now
    if isinstance(now, datetime.date):
        return datetime.datetime(now.year, now.month, now.day)
    return _dt(now) or datetime.datetime.now()


def meeting_cadence(events, now, min_history=3):
    """(project_id)ごとの Meeting 間隔中央値・最終開催・経過日数。**開催済(start<now)のみで測る**
    （未来の予定を"最後の開催"に数えると経過が負になる）。過去 min_history 回未満のPJは含めない。now は datetime/date/str 可。"""
    ndt = _now_dt(now)
    ndate = ndt.date()
    bypj = defaultdict(list)
    for e in events or []:
        if not _is_meeting(e):
            continue
        sdt = _dt(e.get("start_time"))
        if sdt and sdt < ndt:                       # 既に開催済のみ
            bypj[str(e.get("project_id") or "")].append(sdt.date())
    out = {}
    for pid, dates in bypj.items():
        dates = sorted(set(dates))
        if len(dates) < min_history:
            continue
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates)) if (dates[i] - dates[i - 1]).days > 0]
        if not gaps:
            continue
        median = sorted(gaps)[len(gaps) // 2]
        out[pid] = {"median": median, "last": dates[-1].isoformat(),
                    "elapsed": (ndate - dates[-1]).days, "count": len(dates)}
    return out


def meetings_due(events, tasks, now, min_history=3, floor_days=14):
    """「そろそろ定例」＝経過 > max(1.5×中央値, floor_days) ∧ そのPJに未完タスクあり。経過の長い順。
    （floor_days=14≈10営業日。過去 min_history 回未満は判定せず＝根拠不足は黙る。）now は datetime/date/str 可。"""
    cad = meeting_cadence(events, now, min_history)
    active_pj = {str(t.get("project_id")) for t in (tasks or []) if str(t.get("status") or "").lower() not in _DONE}
    out = []
    for pid, c in cad.items():
        thresh = max(c["median"] * 1.5, floor_days)
        if c["elapsed"] > thresh and pid in active_pj:
            out.append({"project_id": pid, "median": c["median"], "last": c["last"],
                        "elapsed": c["elapsed"], "threshold": round(thresh)})
    return sorted(out, key=lambda x: -x["elapsed"])


if __name__ == "__main__":
    import sys
    import json
    sys.path.insert(0, ".")
    import casper_secrets
    casper_secrets.load_into_env()
    import casper_tools
    import casper_notify as N
    now = datetime.datetime.now()
    today = now.date().isoformat()
    events = casper_tools._get("/events?limit=300").get("items", [])
    tasks = N._all_tasks()
    try:
        pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        pjn = {}
    up = upcoming_meetings(events, now)
    print(f"直近48hのMeeting: {len(up)}件")
    due = meetings_due(events, tasks, now)
    print(f"『そろそろ定例』候補: {len(due)}件")
    for x in due[:8]:
        print(f"  {pjn.get(x['project_id'], 'PJ'+x['project_id'])}: 前回{x['last']}・{x['elapsed']}日経過(中央値{x['median']}日/閾{x['threshold']})")
