#!/usr/bin/env python3
"""Casper OPEN LOOP レジストリ — 未了の約束を⚙一次レコード化。

Fable5診断(2026-07-02): 帯の散文に埋めた「未了」はLLMが思い出せた時しか効かない。
表(レコード)にし、完了プローブ(Vimeo/asset/Calendar照会)でCasperが自ら観測して閉じる。hori事件の恒久解。

レコード: {id, created_at, who(依頼元uid), title(約束), referent(対象), assignee(相手),
          probe:{type:vimeo|asset|manual, q/name, baseline[]}, notify(uid), status:open|closed,
          closed_at, evidence}
"""
import datetime
import json
import os
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "open_loops.jsonl")
_LOCK = threading.Lock()                                   # check()の再load-merge書込を直列化(lost-update防止)


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
    os.replace(tmp, STORE)                                  # アトミック


def _vimeo_ids(q):
    """q にマッチする Vimeo 動画の uri list。**エラー時は None を返す(空[]と区別)** — 障害中に
    baseline=[]で登録し回復後 全件を"新規"と偽closeする事故(Fable指摘)を防ぐ。per_page大で churn 回避。"""
    try:
        import casper_vimeo
        r = casper_vimeo.search(q, per_page=100)
        items = r if isinstance(r, list) else (r.get("data") or r.get("items") or [])
        return [str(v.get("uri") or v.get("link") or v.get("name")) for v in items if (v.get("uri") or v.get("link") or v.get("name"))]
    except Exception:
        return None                                        # エラー=不明(空でない)


def add(who, title, probe, referent=None, assignee=None, notify=None):
    """未了の約束を登録。probe例: {"type":"vimeo","q":"TKP"} / {"type":"asset","name":"X.png"} / {"type":"manual"}。
    vimeo/asset は登録時の"現状"をbaselineに取り、それを超える新出現で完了判定(=新規アップ/新規ファイル)。"""
    probe = dict(probe or {"type": "manual"})
    if probe.get("type") == "vimeo" and "baseline" not in probe:
        probe["baseline"] = _vimeo_ids(probe.get("q", ""))     # None(エラー)なら check() で後日 re-baseline
    if probe.get("type") == "asset" and "baseline" not in probe:
        try:
            import casper_manifest
            probe["baseline"] = sorted(m["name"] for m in casper_manifest.search(probe.get("q", probe.get("name", ""))))
        except Exception:
            probe["baseline"] = None
    rec = {"id": uuid.uuid4().hex[:12], "created_at": _now(), "who": who, "title": title,
           "referent": referent, "assignee": assignee, "probe": probe,
           "notify": notify or who, "status": "open", "closed_at": None, "evidence": None}
    with _LOCK:
        with open(STORE, "a", encoding="utf-8") as f:          # 追記(O_APPEND・check()の再load-mergeと直列化)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _probe_now(probe):
    """現状のマッチID集合を返す。エラー時 None(=不明・空と区別)。"""
    t = (probe or {}).get("type")
    if t == "vimeo":
        ids = _vimeo_ids(probe.get("q", ""))
        return set(ids) if ids is not None else None
    if t == "asset":
        try:
            import casper_manifest
            return set(m["name"] for m in casper_manifest.search(probe.get("q", probe.get("name", ""))))
        except Exception:
            return None
    return None                                            # manual 等は自動完了しない


def check():
    """全open loopのprobeを走らせ、満たされたら閉じる。probeは lock外(遅い網羅照会)→書込のみ lock内で
    再load-mergeし id指定反映(check中の add を消さない=lost-update防止・Fable指摘)。返り=今回閉じたreclist。"""
    snapshot = _load()
    updates = []                                           # (id, kind, data): kind=close|rebaseline
    for r in snapshot:
        if r.get("status") != "open":
            continue
        probe = r.get("probe") or {}
        if probe.get("type") not in ("vimeo", "asset"):
            continue
        now = _probe_now(probe)
        if now is None:                                    # エラー=不明 → 触らない(偽closeしない)
            continue
        base = probe.get("baseline")
        if base is None:                                   # 登録時エラーで未baseline → 今 baseline化(closeしない)
            updates.append((r["id"], "rebaseline", sorted(now)))
            continue
        new = now - set(base)
        if new:
            kind = "Vimeoに新規動画" if probe["type"] == "vimeo" else "新規資産ファイル"
            updates.append((r["id"], "close", f"{kind}を検知(q={probe.get('q')}): {sorted(new)[:2]}"))
    if not updates:
        return []
    closed = []
    with _LOCK:                                            # 書込は最新を再loadしてから id指定で反映
        recs = _load()
        byid = {r["id"]: r for r in recs}
        for lid, kind, data in updates:
            r = byid.get(lid)
            if not r or r.get("status") != "open":
                continue
            if kind == "rebaseline":
                r.setdefault("probe", {})["baseline"] = data
            elif kind == "close":
                r["status"] = "closed"; r["closed_at"] = _now(); r["evidence"] = data
                closed.append(r)
        _save_all(recs)
    return closed


def open_for(who=None):
    """open な loop 一覧。who指定時は who が依頼元 or 通知先のものだけ(digest用)。"""
    recs = [r for r in _load() if r.get("status") == "open"]
    if who is None:
        return recs
    return [r for r in recs if str(r.get("who")) == str(who) or str(r.get("notify")) == str(who)]


def recently_closed(who=None, hours=48):
    """直近hours以内にclosedになったloop(利用者への"完了しました"先読み報告用)。"""
    cut = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")
    out = []
    for r in _load():
        if r.get("status") == "closed" and str(r.get("closed_at") or "") >= cut:
            if who is None or str(r.get("who")) == str(who) or str(r.get("notify")) == str(who):
                out.append(r)
    return out


def close_manual(loop_id, evidence="手動クローズ"):
    with _LOCK:
        recs = _load()
        for r in recs:
            if r.get("id") == loop_id and r.get("status") == "open":
                r["status"] = "closed"; r["closed_at"] = _now(); r["evidence"] = evidence
                _save_all(recs)
                return r
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        print("closed:", [r["title"] for r in check()])
    else:
        for r in open_for():
            print(f"  [{r['id']}] {r['title']} (probe={r['probe'].get('type')}, who={r['who']})")
