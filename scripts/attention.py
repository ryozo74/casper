#!/usr/bin/env python3
"""Casper attention — 自己改善ループ 柱3(co-working)の燃料ポンプ(Fable5設計)。

朝イチの「今日の3件」を決定的に生成: (a)滞留proposed(承認待ちの下書き=ファネルの詰まり) (b)未了の約束
(openloop) (c)納期超過PJ を単純スコアで束ね上位3件。UI描画は副作用ゼロゆえ承認不要でそこに出す
(能動DM通知は鶏卵ゆえしない=Fable)。proposed>7日は自動expired化して台帳を"生きた承認待ち"に保つ。

鉄則(Fable): LLMにトリガーを発明させない=注意キューは機構が決定的に生成。先回りは origin:casper の
proposal(既存の承認カードを相続)。UI描画のみ・能動通知はしない。
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(HERE, "casper_outbox.jsonl")
CAL_PROJECTS = "/tmp/cal_projects.json"
STATE = os.path.join(HERE, "attention_state.json")
EXPIRE_DAYS = 7
FOREVER = "9999-12-31"


# ── snooze/dismiss(Fable Q4: alert fatigue=注意機構の死因第一位への対策) ──
# 副作用ゼロの状態(DM/Calendar書込はしない)。muted={uid:ref -> {until}}。until日まで(含む)非表示。
def _load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(s):
    try:
        json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _mkey(uid, ref):
    return f"{str(uid or '')}:{str(ref or '')}"


def is_muted(uid, ref):
    rec = _load_state().get(_mkey(uid, ref))
    if not rec:
        return False
    until = rec.get("until") or ""
    return datetime.date.today().isoformat() <= until   # until日まで(含む)非表示


def snooze(uid, ref, days=0):
    """『今日は流す』=本日(+days)まで非表示。副作用ゼロ。返り=until日。"""
    until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    s = _load_state(); s[_mkey(uid, ref)] = {"until": until, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    _save_state(s)
    return until


def dismiss(uid, ref):
    """恒久的に流す(以後出さない)。副作用ゼロ。"""
    s = _load_state(); s[_mkey(uid, ref)] = {"until": FOREVER, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    _save_state(s)
    return FOREVER


def _load_jsonl(p):
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _days_since(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts)[:19])
        return (datetime.datetime.now() - d).days
    except Exception:
        return 0


def _latest_states():
    """outbox は追記型ゆえ id ごと最新 state を畳む。返り {id: rec(最新)}。"""
    latest = {}
    for r in _load_jsonl(OUTBOX):
        rid = r.get("id")
        if rid:
            latest[rid] = {**latest.get(rid, {}), **r}
    return latest


ATT_LOG = os.path.join(HERE, "attention.jsonl")


def _alog(msg):
    """接地の被覆率ログ(Fable鉄則: 開門は最も見られる面ゆえ接地劣化を残す)。"""
    try:
        with open(ATT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "msg": msg},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def _my_pids(uid):
    """本人が担当タスクを持つ project_id 集合(帰属の裏どり: あなたがこのPJに担当を持つ→あなたの事)。RO API。"""
    pids = set()
    if not uid:
        return pids
    try:
        import casper_tools
        off = 0
        while True:
            page = casper_tools._get(f"/tasks?limit=500&offset={off}").get("items", [])
            for t in page:                             # RO /tasks の assignee_id フィルタは効かぬ→client側で担当照合
                if str(t.get("assigned_to")) == str(uid) and t.get("project_id") is not None:
                    pids.add(str(t.get("project_id")))
            if len(page) < 500:
                break
            off += 500
    except Exception:
        pass
    return pids


def _company_overdue(today):
    """全社の納期超過PJ [{id,name,due}](online・完了/中止除く)。帰属でなく"事実"の集合。"""
    out = []
    try:
        for p in json.load(open(CAL_PROJECTS)).get("items", []):
            if str(p.get("display_status") or "online") != "online":
                continue
            due = str(p.get("end_date") or "")[:10]
            if due and due < today and str(p.get("status") or "") not in ("completed", "done", "cancelled"):
                out.append({"id": p.get("id"), "name": str(p.get("name", "")), "due": due})
    except Exception:
        pass
    return out


def gather(uid):
    """注意すべき候補を集める(この人向け)。返り=[{kind, title, detail, score, ref}]。"""
    cands = []
    uid = str(uid or "")
    latest = _latest_states()
    # (a) 滞留 proposed(承認待ちの下書き=ファネルの詰まり・最優先で流す)
    for rid, r in latest.items():
        if r.get("state") != "proposed" or (uid and str(r.get("uid")) != uid):
            continue
        d = _days_since(r.get("ts"))
        cands.append({"kind": "draft", "title": (r.get("summary") or "下書き").split("\n")[0][:60],
                      "detail": f"{d}日前に下書き・承認待ち", "score": 50 + d, "ref": rid})
    # (b) 未了の約束(openloop)
    try:
        import casper_openloop
        for lp in (casper_openloop.open_for(uid) or [])[:5]:
            cands.append({"kind": "loop", "title": str(lp.get("title", ""))[:60],
                          "detail": "未了の約束" + (f"(相手:{lp['assignee']})" if lp.get("assignee") else ""),
                          "score": 30 + _days_since(lp.get("created_at")), "ref": lp.get("id")})
    except Exception:
        pass
    # (c) 納期超過PJ — 帰属スコープ: 本人が担当タスクを持つPJのみ(Fable処方: 帰属できない全社事を
    #     "あなたの注意事項"の見出しで出すのは"帰属の嘘"。全社の超過は briefing_lines の降格見出しで参考提示)。
    try:
        today = datetime.date.today().isoformat()
        mine = _my_pids(uid)                                   # 本人担当PJ集合(帰属の裏どり)
        all_od = _company_overdue(today)
        my_od = [p for p in all_od if str(p["id"]) in mine]
        _alog(f"overdue uid={uid}: 全社{len(all_od)}件中 担当{len(my_od)}件を提示(帰属スコープ)")   # 被覆率ログ
        for p in my_od:
            cands.append({"kind": "overdue", "title": p["name"][:50],
                          "detail": f"納期超過(〆{p['due']})", "score": 40, "ref": p["id"]})
    except Exception:
        pass
    cands = [c for c in cands if not is_muted(uid, c.get("ref"))]   # snooze/dismiss 済は除外(alert fatigue対策)
    return cands


def today_three(uid, n=3):
    """今日の注意 上位n件(スコア降順・種類が偏らぬよう軽く分散)。"""
    cands = gather(uid)
    cands.sort(key=lambda c: -c.get("score", 0))
    out, kinds = [], {}
    for c in cands:
        k = c["kind"]
        if kinds.get(k, 0) >= 2 and len(out) >= n // 2:   # 同種は2件までに軽く抑える(偏り防止)
            continue
        out.append(c); kinds[k] = kinds.get(k, 0) + 1
        if len(out) >= n:
            break
    return out


def expire_stale():
    """proposed>7日 を expired へ(台帳を生きた承認待ちに保つ)。返り=expireした件数。"""
    try:
        import casper_outbox
    except Exception:
        return 0
    n = 0
    for rid, r in _latest_states().items():
        if r.get("state") == "proposed" and _days_since(r.get("ts")) > EXPIRE_DAYS:
            try:
                casper_outbox.expire(rid)
                n += 1
            except Exception:
                pass
    return n


def briefing_lines(uid, include_drafts=True):
    """open_briefing へ差し込む『今日の3件』テキスト(無ければ空)。
    include_drafts=False: 下書きは承認カードで直接出す為テキストから除く(一往復短縮・Fable Q4)。"""
    three = today_three(uid)
    if not include_drafts:
        three = [c for c in three if c.get("kind") != "draft"]
    icon = {"draft": "📝", "overdue": "🔴"}
    out = ""
    if three:
        # 推論項目(loop=未了の約束)は「未確認」印を機構で付す(Fable処方: 確定=担当/DM と 推論=分類ラベル を
        # 視覚的に分ける。断定形でなく"推測"と分かる印にし、確定情報と同じ重みに見せない)。
        def _fmt(c):
            if c["kind"] == "loop":
                return f"🔗未確認 {c['title']} — {c['detail']}（推測・元DM要確認）"
            return f"{icon.get(c['kind'], '・')} {c['title']} — {c['detail']}"
        out += "\n\n**今日の3件（気にかけどころ）**\n" + "\n".join(_fmt(c) for c in three)
    # 全社の納期超過は"参考(降格見出し)"で。本人の3件に無い分だけ、帰属でなく「参考・担当外」と明示して出す
    # (Fable処方: 事実として真でも"あなたの注意事項"には混ぜない。見出しが帰属を約束する)。
    try:
        today = datetime.date.today().isoformat()
        mine_refs = {str(c.get("ref")) for c in three if c["kind"] == "overdue"}
        extra = [p for p in _company_overdue(today) if str(p["id"]) not in mine_refs]
        if extra:
            names = "、".join(f"{p['name']}（〆{p['due']}）" for p in extra[:5])
            more = f" ほか{len(extra) - 5}件" if len(extra) > 5 else ""
            out += (f"\n\n**🏢 全社の納期状況（参考・あなたの担当外）**\n🔴 {len(extra)}件: {names}{more}")
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "28"
    print(f"=== 今日の3件 (uid={uid}) ===")
    for c in today_three(uid):
        print(f"  [{c['kind']}] {c['title']} — {c['detail']} (score {c['score']})")
    print(f"\n候補総数: {len(gather(uid))}件")
