#!/usr/bin/env python3
"""Casper 割り込み政策エンジン(Fable M3・push型=理念の完成)。

「聞かれたら答える」→「放っておくと向こうから教えてくれる」への転換。
判断ルール(何を通知するか)は"機構"がここで決定的に持ち、LLMは通知文の言い回しだけ担う。
配信先は DM／今後の携帯Push(殿御下命2026-07-12・Discordは開発チーム専用ゆえ使わない)。

方針:
- 朝ブリーフ(定時・1日1回): その日の要点(納期超過/本日締切/停滞FB)を要約。情報のみゆえ承認不要。
- 閾値割り込み(状態変化時): 新規の納期超過発生・停滞FB 等を"新規イベントの時だけ"通知(dedup状態管理)。
  行動を伴うもの(催促等)は承認カード下書き経由(このモジュールは検知と下書き素材の生成まで・実送信はしない)。

安全: このモジュールは通知を"ストアに積む"だけ。外部への自動送信はしない(承認/配信は呼び側=chat_server)。
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFY_LOG = os.path.join(HERE, "casper_notify.jsonl")     # 積まれた通知(未読/既読)
STATE_FILE = os.path.join(HERE, "casper_notify_state.json")  # dedup状態(最後にブリーフ出した日・既知の超過集合 等)
CAL_PROJECTS = "/tmp/cal_projects.json"

# 停滞FB とみなす status(API 19値・review系で監督/QC待ち)。完了=deliver/除外=omit は対象外。
_STALL_STATUS = {"qc_fb", "dir_wt", "qc", "v1qc", "ap_fb", "dir_fb"}
_DONE_STATUS = {"deliver", "completed", "done", "cancelled", "omit"}


def _load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    try:
        tmp = STATE_FILE + ".tmp"
        json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def _projects():
    try:
        return json.load(open(CAL_PROJECTS, encoding="utf-8")).get("items", [])
    except Exception:
        return []


def _all_tasks():
    try:
        import casper_tools
        out = []
        for off in (0, 500, 1000, 1500, 2000):
            page = casper_tools._get(f"/tasks?limit=500&offset={off}").get("items", [])
            out += page
            if len(page) < 500:
                break
        return out
    except Exception:
        return []


def _overdue_projects(today):
    """本日時点で納期超過の online PJ 名の集合＋詳細。"""
    od = []
    for p in _projects():
        if str(p.get("display_status") or "online") != "online":
            continue
        due = str(p.get("end_date") or "")[:10]
        st = str(p.get("status") or "").lower()
        if due and due < today and st not in ("completed", "done", "cancelled"):
            od.append({"name": p.get("name"), "due": due,
                       "days": (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(due)).days})
    return od


def _stalled_fb(tasks, today, days=3):
    """review系(qc_fb/dir_wt等)で updated_at が days日以上動いていないタスク＝停滞FB。"""
    out = []
    for t in tasks:
        if str(t.get("status") or "").lower() not in _STALL_STATUS:
            continue
        up = str(t.get("updated_at") or "")[:10]
        try:
            if up and (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(up)).days >= days:
                out.append({"name": t.get("name") or t.get("title"),
                            "shot": t.get("shotID") or t.get("shot_id") or "",
                            "type": t.get("type") or "",
                            "status": t.get("status"),
                            "status_label": t.get("status_label") or t.get("status"),
                            "since": up,
                            "days": (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(up)).days,
                            "project_id": t.get("project_id"),
                            "assigned_to": t.get("assigned_to")})
        except Exception:
            pass
    return out


def compute(uid, now=None):
    """通知候補を算出(純機構・LLM非使用)。返り=[{type,level,title,body,dedup_key,action?}]。dedup済。
    now は datetime(既定=現在)。"""
    now = now or datetime.datetime.now()
    today = now.date().isoformat()
    st = _load_state()
    ukey = str(uid)
    ust = st.get(ukey, {})
    notifs = []
    tasks = _all_tasks()
    overdue = _overdue_projects(today)
    stalled = _stalled_fb(tasks, today)

    # ① 朝ブリーフ(1日1回・8時以降・情報のみ)
    if now.hour >= 8 and ust.get("brief_date") != today:
        od_txt = ("、".join(f"{o['name']}（{o['days']}日超過）" for o in overdue[:5]) if overdue else "なし")
        # 本日締切
        due_today = [t for t in tasks if str(t.get("due_date") or "")[:10] == today
                     and str(t.get("status") or "").lower() not in _DONE_STATUS]
        body = (f"本日 {today} の要点。\n"
                f"・納期超過: {len(overdue)}件（{od_txt}）\n"
                f"・本日締切のタスク: {len(due_today)}件\n"
                f"・停滞中のFB/確認: {len(stalled)}件")
        notifs.append({"type": "morning_brief", "level": "info", "title": "☀️ 今朝のブリーフ",
                       "body": body, "dedup_key": f"brief:{today}"})
        ust["brief_date"] = today

    # ② 新規の納期超過 → 個別でなく"1件にまとめて"通知(N件＋一覧・氾濫防止・殿指摘2026-07-13)
    seen_od = set(ust.get("seen_overdue", []))
    new_od = [o for o in overdue if o.get("name") and o["name"] not in seen_od]
    if new_od:
        lst = "、".join(f"{o['name']}（{o['days']}日超過）" for o in new_od[:8])
        more = f" ほか{len(new_od)-8}件" if len(new_od) > 8 else ""
        notifs.append({"type": "new_overdue", "level": "warn",
                       "title": f"🔴 納期超過 {len(new_od)}件",
                       "body": f"新たに納期超過となりました：{lst}{more}。",
                       "dedup_key": "overdue:" + ",".join(sorted(o["name"] for o in new_od))})
    ust["seen_overdue"] = sorted({o["name"] for o in overdue if o["name"]})

    # ③ 停滞FB(3日+動かない確認待ち) → 同じく"1件にまとめて"通知(N件＋古い順に上位を列挙)
    seen_stall = set(ust.get("seen_stall", []))
    stalled_sorted = sorted(stalled, key=lambda s: s.get("since") or "9999")   # 古い(=長く停滞)順
    cur_stall, new_stall = [], []
    for s in stalled_sorted:
        key = f"stall:{s['shot'] or s['name']}:{s['since']}"
        cur_stall.append(key)
        if key not in seen_stall:
            new_stall.append(s)
    if new_stall:
        names = [f"{s['shot']} {s['name']}".strip() for s in new_stall[:8]]
        more = f" ほか{len(new_stall)-8}件" if len(new_stall) > 8 else ""
        notifs.append({"type": "stalled_fb", "level": "warn",
                       "title": f"⏳ FB/確認が停滞 {len(new_stall)}件",
                       "body": f"確認待ちのまま動いていません：{'、'.join(names)}{more}。まとめて催促しますか？",
                       "dedup_key": "stall:" + ",".join(sorted(f"{s['shot'] or s['name']}:{s['since']}" for s in new_stall)),
                       "action": {"tool": "send_message", "hint": "催促DM下書き(まとめ)",
                                  "subjects": [f"{s['shot']} {s['name']}".strip() for s in new_stall]}})
    ust["seen_stall"] = cur_stall

    st[ukey] = ust
    _save_state(st)
    return notifs


def store(uid, notifs, now=None):
    """算出した通知をストアに積む(未読)。既に同じ dedup_key が未処理で在れば重複を避ける。"""
    if not notifs:
        return 0
    now = now or datetime.datetime.now()
    existing = {n.get("dedup_key") for n in pending(uid)}
    added = 0
    try:
        with open(NOTIFY_LOG, "a", encoding="utf-8") as f:
            for nt in notifs:
                if nt.get("dedup_key") in existing:
                    continue
                rec = dict(nt)
                rec.update({"uid": str(uid), "ts": now.isoformat(timespec="seconds"), "read": False})
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
    except Exception:
        pass
    return added


def _all_notifs():
    out = []
    try:
        for ln in open(NOTIFY_LOG, encoding="utf-8"):
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def pending(uid, limit=20):
    """未読通知(新しい順)。停滞FBは"保存時のスナップショット"を凍結せず、表示のたび現データで作り直す
    (殿指摘2026-07-14: 完了済タスク T57/v1 等を古い通知が「停滞中」と見せ続ける陳腐化を防ぐ。完了は現status
    が _STALL_STATUS 外ゆえ自動で落ち、今ゼロなら通知自体を出さない=殿の「完了はクローズ扱い」に一致)。"""
    u = str(uid)
    out = []
    _fresh = None                                          # 停滞FBの現況(遅延計算・1回のみ)
    for n in reversed(_all_notifs()):
        if n.get("uid") != u or n.get("read"):
            continue
        if n.get("type") == "stalled_fb":
            if _fresh is None:
                try:
                    _fresh = _stalled_fb(_all_tasks(), datetime.date.today().isoformat())
                except Exception:
                    _fresh = []
            if not _fresh:
                continue                                   # 今は停滞ゼロ→古い「N件停滞」を出さない
            names = [f"{s['shot']} {s['name']}".strip()
                     for s in sorted(_fresh, key=lambda s: s.get("since") or "9999")[:8]]
            more = f" ほか{len(_fresh) - 8}件" if len(_fresh) > 8 else ""
            n = dict(n)                                     # 現データで本文を作り直す(凍結を捨てる)
            n["title"] = f"⏳ FB/確認が停滞 {len(_fresh)}件"
            n["body"] = f"確認待ちのまま動いていません：{'、'.join(names)}{more}。まとめて催促しますか？"
        out.append(n)
        if len(out) >= limit:
            break
    return out


def mark_read(uid, dedup_keys=None):
    """指定 dedup_key(または全部)を既読に。全書き換え(小規模ゆえ許容)。"""
    u = str(uid)
    recs = _all_notifs()
    ks = set(dedup_keys) if dedup_keys else None
    changed = 0
    for r in recs:
        if r.get("uid") == u and not r.get("read") and (ks is None or r.get("dedup_key") in ks):
            r["read"] = True
            changed += 1
    if changed:
        try:
            tmp = NOTIFY_LOG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, NOTIFY_LOG)
        except Exception:
            pass
    return changed


def tick(uids):
    """スケジューラから定期呼出: 各uidの通知を算出→ストアへ積む。返り={uid:added}。"""
    res = {}
    for uid in uids:
        try:
            res[str(uid)] = store(uid, compute(uid))
        except Exception as e:
            res[str(uid)] = f"err:{str(e)[:60]}"
    return res


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "28"
    n = compute(uid)
    print(f"=== compute uid={uid}: {len(n)}件 ===")
    for x in n:
        print(f"  [{x['type']}] {x['title']} — {x['body'][:80]}")
    print("stored:", store(uid, n))
    print("pending:", len(pending(uid)))
