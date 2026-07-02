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
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "open_loops.jsonl")


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
    """q にマッチする Vimeo 動画の uri 集合。per_page を大きく取り"全マッチ"を安定取得
    (既定8件は関連度順の境界が呼ぶ度に churn し baseline差分が誤検知する為)。"""
    try:
        import casper_vimeo
        r = casper_vimeo.search(q, per_page=100)
        items = r if isinstance(r, list) else (r.get("data") or r.get("items") or [])
        return [str(v.get("uri") or v.get("link") or v.get("name")) for v in items]
    except Exception:
        return []


def add(who, title, probe, referent=None, assignee=None, notify=None):
    """未了の約束を登録。probe例: {"type":"vimeo","q":"TKP"} / {"type":"asset","name":"X.png"} / {"type":"manual"}。
    vimeo/asset は登録時の"現状"をbaselineに取り、それを超える新出現で完了判定(=新規アップ/新規ファイル)。"""
    probe = dict(probe or {"type": "manual"})
    if probe.get("type") == "vimeo" and "baseline" not in probe:
        probe["baseline"] = _vimeo_ids(probe.get("q", ""))     # 現存動画=まだ約束は未達
    if probe.get("type") == "asset" and "baseline" not in probe:
        try:
            import casper_manifest
            probe["baseline"] = sorted(m["name"] for m in casper_manifest.search(probe.get("q", probe.get("name", ""))))
        except Exception:
            probe["baseline"] = []
    rec = {"id": uuid.uuid4().hex[:12], "created_at": _now(), "who": who, "title": title,
           "referent": referent, "assignee": assignee, "probe": probe,
           "notify": notify or who, "status": "open", "closed_at": None, "evidence": None}
    with open(STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _run_probe(probe):
    """完了していれば evidence(str)を返す。未完なら None。"""
    t = (probe or {}).get("type")
    base = set(probe.get("baseline") or [])
    if t == "vimeo":
        now_ids = set(_vimeo_ids(probe.get("q", "")))
        new = now_ids - base
        if new:
            return f"Vimeoに新規動画({len(new)}件・q={probe.get('q')})を検知: {list(new)[:2]}"
    elif t == "asset":
        try:
            import casper_manifest
            now = set(m["name"] for m in casper_manifest.search(probe.get("q", probe.get("name", ""))))
            new = now - base
            if new:
                return f"新規資産ファイルを検知: {sorted(new)[:3]}"
        except Exception:
            pass
    return None


def check():
    """全open loopのprobeを走らせ、満たされたものをclosedに。返り=今回閉じたレコードlist。"""
    recs = _load()
    closed = []
    changed = False
    for r in recs:
        if r.get("status") != "open":
            continue
        ev = _run_probe(r.get("probe"))
        if ev:
            r["status"] = "closed"; r["closed_at"] = _now(); r["evidence"] = ev
            closed.append(r); changed = True
    if changed:
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
