#!/usr/bin/env python3
"""Casper アクション outbox — 副作用アクションの永続的な状態機械(Fable5棚卸し #4-1/#4-2)。

Transactional Outbox パターン: アクションをまずDB台帳に記録し、承認→実行を状態遷移で制御する。
『送信済みか』はLLMの発話でなく **この台帳の state カラム** が唯一の真実源(P1検問の裏付けもここ)。
冪等性: approve は state=proposed の時だけ通す→二重クリック/多重承認でも二度送らない。
永続: サーバ再起動(reload多発)でも承認待ちが消えない(旧 in-memory dict の『提案が見つかりませぬ』を根治)。

状態機械:
  proposed → approved → executing → sent          (成功)
                                  → failed         (実行失敗・再試行可)
  proposed → rejected                              (却下)
レコード: {id, key(冪等キー), ts, tool, args, uid, summary, thread, state, result, updated}
"""
import datetime
import hashlib
import json
import os
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "casper_outbox.jsonl")
_LOCK = threading.Lock()
_TERMINAL = {"sent", "rejected", "expired"}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load():
    out = []
    if os.path.exists(STORE):
        for ln in open(STORE, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    return out


def _save_all(recs):
    tmp = f"{STORE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STORE)                                  # アトミック置換


def _key(tool, args, uid):
    """冪等キー: 同一(tool,args,uid)は同じキー。短時間の重複提案の検出に使える。"""
    raw = json.dumps([tool, args, str(uid or "")], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _audience(rec):
    """承認カードを見せる/承認できる uid 集合。audience 欠落(旧レコード)は所有者[uid]とみなす(後方互換)。"""
    aud = rec.get("audience")
    return [str(u) for u in aud] if aud else [str(rec.get("uid") or "")]


def propose(tool, args, uid, summary, thread=None, origin="user", query=None, trace_id=None,
            verb=None, audience=None):
    """アクションを台帳に proposed で起票。返り=レコード(id を含む)。
    origin: user(利用者の依頼)|casper(先回り提案) / query: 発端の発話 / trace_id: 対応トレース。
    verb: M4動詞名(assign/reschedule/...・任意) / audience: 承認カードを見せる uid 集合(M4権限層・
    casper_authority.audience_for で算出)。未指定は所有者[uid]のみ(後方互換)。
    query+trace_id は教師信号の三つ組(文脈,モデル案,人の完成形)の"文脈"を後で復元する為に必須(Fable5指摘)。"""
    rec = {"id": uuid.uuid4().hex[:12], "key": _key(tool, args, uid), "ts": _now(),
           "tool": tool, "args": args, "uid": str(uid or ""), "summary": summary,
           "thread": thread, "state": "proposed", "result": None, "updated": _now(),
           "origin": origin, "query": query, "trace_id": trace_id,
           "verb": verb, "audience": [str(u) for u in audience] if audience else [str(uid or "")]}
    with _LOCK:
        with open(STORE, "a", encoding="utf-8") as f:      # O_APPEND(transition の再load-merge と直列化)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def get(pid):
    return next((r for r in _load() if r.get("id") == pid), None)


def _transition(pid, to_state, expect=None, result=None, uid=None, patch=None):
    """state を expect→to_state へ原子的に遷移。expect と不一致なら None(冪等ガード=二重実行を弾く)。
    uid 指定時は本人のみ許可。patch でargs等を上書き(承認カードの本文編集)。返り=更新後レコード or None。"""
    with _LOCK:
        recs = _load()
        rec = next((r for r in recs if r.get("id") == pid), None)
        if not rec:
            return None
        if uid is not None and str(uid) not in _audience(rec):
            return None                                    # audience 外(=承認権限なし)。M4権限層の approve側チェック
        if expect is not None and rec.get("state") != expect:
            return None                                    # 状態不一致=既処理/多重クリック→冪等に弾く
        if patch:
            rec.setdefault("args", {})
            if isinstance(rec["args"], dict) and isinstance(patch.get("args"), dict):
                # 承認時に本文が編集で上書きされる時、LLM原案を body_orig へ退避。
                # (文脈,モデル案,人の完成形)の三つ組=最高品質の教師信号(Fable S級・data flywheel の燃料)。
                nb = patch["args"].get("body")
                if nb is not None and nb != rec["args"].get("body") and "body_orig" not in rec:
                    rec["body_orig"] = rec["args"].get("body")
                rec["args"].update(patch["args"])
            for k, v in patch.items():
                if k != "args":
                    rec[k] = v
        rec["state"] = to_state
        rec["updated"] = _now()
        if result is not None:
            rec["result"] = str(result)[:2000]
        _save_all(recs)
        return dict(rec)


def revise(pid, args=None, summary=None):
    """proposed のまま args/summary を改訂(状態は動かさぬ)。
    承認カードを出した後に機構が本文を整える時、台帳(=承認時の真実源)も同時に直さねばならぬ。
    これを欠くと『殿が見た文面』と『実際に送られる文面』が食い違う(実測2026-07-28: 材料を添えた
    カードを見せながら、台帳には添える前の本文が残っていた)。"""
    patch = {}
    if isinstance(args, dict):
        patch["args"] = args
    if summary is not None:
        patch["summary"] = summary
    if not patch:
        return None
    return _transition(pid, "proposed", expect="proposed", patch=patch)


def approve(pid, uid=None, body_edit=None):
    """proposed→approved(冪等: proposedでなければNone=二重承認を弾く)。body_edit で本文差替。"""
    patch = {"args": {"body": body_edit}} if body_edit is not None else None
    return _transition(pid, "approved", expect="proposed", uid=uid, patch=patch)


def mark_executing(pid):
    return _transition(pid, "executing", expect="approved")


def mark_sent(pid, result=""):
    return _transition(pid, "sent", expect="executing", result=result)


def mark_failed(pid, err=""):
    """executing→failed(再試行の為 approved へ戻せる)。冪等でなく状態記録が主眼。"""
    return _transition(pid, "failed", expect="executing", result=err)


def reject(pid, uid=None):
    return _transition(pid, "rejected", expect="proposed", uid=uid)


def expire(pid):                                       # proposed>7日を自動失効(台帳を生きた承認待ちに保つ・attention)
    return _transition(pid, "expired", expect="proposed")


def pending(uid=None):
    """まだ proposed(承認待ち)のレコード。uid 指定で「その人が見てよい分」だけ(audience に含まれる分・M4権限層)。"""
    return [r for r in _load() if r.get("state") == "proposed"
            and (uid is None or str(uid) in _audience(r))]


def recently_sent(uid=None, hours=24):
    cut = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")
    return [r for r in _load() if r.get("state") == "sent" and str(r.get("updated") or "") >= cut
            and (uid is None or str(r.get("uid")) == str(uid))]


def compact(keep=2000):
    """終端(sent/rejected)の古いレコードを間引く(肥大防止)。proposed/approved/failed は残す。"""
    with _LOCK:
        recs = _load()
        if len(recs) <= keep:
            return len(recs)
        live = [r for r in recs if r.get("state") not in _TERMINAL]
        term = [r for r in recs if r.get("state") in _TERMINAL][-(keep - len(live)):]
        merged = sorted(live + term, key=lambda r: r.get("ts", ""))
        _save_all(merged)
        return len(merged)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "pending":
        for r in pending():
            print(f"  [{r['id']}] {r['state']} {r['tool']} — {r.get('summary', '')[:50]}")
    else:
        recs = _load()
        from collections import Counter
        print("outbox:", len(recs), "件 /", dict(Counter(r.get("state") for r in recs)))
