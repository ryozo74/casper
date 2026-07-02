#!/usr/bin/env python3
"""Casper 人物trait(癖)レジストリ — 人の癖を構造化フィールドで保持。

Fable5 #5(2026-07-02): 「hori=作業中報告」という知見が u_*.md の散文に居る限りプロンプト祈り。
per-person trait として構造化し、verify_digest 等が決定的に消費する(低リテラシー/報告癖のある相手には
機構的に裏取り・手Bを促す)。蒸留(distill_activity)が育て、応答層が消費する共有フィールド。

STORE: person_traits.json = {uid: [{trait, note, evidence, created}]}
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "person_traits.json")


def _load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(d):
    tmp = f"{STORE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STORE)


def add(uid, trait, note, evidence=""):
    """人物traitを登録/更新(同一traitは上書き)。trait=短いキー(例:report_timing)・note=消費する指示文。"""
    d = _load(); uid = str(uid)
    lst = [t for t in d.get(uid, []) if t.get("trait") != trait]
    lst.append({"trait": trait, "note": note, "evidence": evidence,
                "created": datetime.datetime.now().isoformat(timespec="seconds")})
    d[uid] = lst
    _save(d)
    return d[uid]


def for_uid(uid):
    return _load().get(str(uid), [])


def for_text(text, name_to_uid):
    """text中に名が現れる人物のtraitを集める。name_to_uid={名:uid}(roster)。返り=[(name, [traits])]。
    ASCII名(ou/tim/li 等)は語境界で照合(about/timing/list への部分一致誤爆を防ぐ・Fable指摘)。日本語名は部分一致。"""
    import re
    out = []
    t = text or ""
    for name, uid in (name_to_uid or {}).items():
        if not name or len(name) < 2:
            continue
        if name.isascii():
            hit = re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", t, re.I)
        else:
            hit = name in t
        if hit:
            tr = for_uid(uid)
            if tr:
                out.append((name, tr))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 5 and sys.argv[1] == "add":
        print(add(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else ""))
    else:
        d = _load()
        for uid, ts in d.items():
            for t in ts:
                print(f"  uid{uid}: [{t['trait']}] {t['note']}")
