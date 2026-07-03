#!/usr/bin/env python3
"""Casper 節構造ドキュメント — チャット介した資料作りの実体(Fable5 UI設計)。

『良い資料作り』の欠けていた2性質=参照可能性と版 を足す唯一のアーティファクト形式。
文書はサーバー側に節(section)の配列として持ち、フロントはそれを描画するだけ(vanilla・ビルド無し)。
- 節単位の編集/再生成 → 弱いqwen対策(全文再生成は崩れるが1節なら安定)。
- 版スナップショット → body_orig と同じ機構: 人の修正前後ペアが自己改善ループの教師信号になる。

レコード: {id, title, project, author, sections:[{id, heading, body}], version, created, updated,
          versions:[{v, at, full, note}]}   # versions=直近N件の全文スナップショット
"""
import datetime
import json
import os
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(HERE, "..", "vault", "70_docs")
_LOCK = threading.Lock()
_MAX_VERSIONS = 20                                          # 版スナップショットの保持数(直近)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _path(doc_id):
    return os.path.join(STORE_DIR, f"{doc_id}.json")


def _save(doc):
    os.makedirs(STORE_DIR, exist_ok=True)
    p = _path(doc["id"])
    tmp = f"{p}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)                                     # アトミック
    return doc


def get(doc_id):
    p = _path(doc_id)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _sid():
    return "s" + uuid.uuid4().hex[:8]


def create(title, sections, project="", author="casper"):
    """sections=[{heading, body}] or [{id,heading,body}]。id無しは自動付与。"""
    secs = [{"id": s.get("id") or _sid(), "heading": s.get("heading", ""), "body": s.get("body", "")}
            for s in (sections or [])]
    doc = {"id": uuid.uuid4().hex[:12], "title": title, "project": project, "author": author,
           "sections": secs, "version": 1, "created": _now(), "updated": _now(), "versions": []}
    with _LOCK:
        _save(doc)
    return doc


def to_markdown(doc):
    """全文markdown(スナップショット/Aurora起票/RAG用)。"""
    out = [f"# {doc.get('title', '')}", ""]
    for s in doc.get("sections", []):
        if s.get("heading"):
            out.append(f"## {s['heading']}")
        out.append(s.get("body", ""))
        out.append("")
    return "\n".join(out).strip()


def _snapshot(doc, note=""):
    """現在の全文を版スナップショットに退避(直近N件)。返り=snapshotした版番号。"""
    snap = {"v": doc.get("version", 1), "at": _now(), "full": to_markdown(doc), "note": note}
    doc.setdefault("versions", []).append(snap)
    doc["versions"] = doc["versions"][-_MAX_VERSIONS:]
    return snap["v"]


def save_section(doc_id, section_id, body, orig=None, instruction=None):
    """節本文を保存。保存前に全文を版退避(人の修正前後=教師信号)。orig/instruction は
    再生成由来の教師信号(モデル案→人の完成形 or 指示)を残す為に任意記録。"""
    with _LOCK:
        doc = get(doc_id)
        if not doc:
            return None
        _snapshot(doc, note=f"edit {section_id}")
        for s in doc["sections"]:
            if s["id"] == section_id:
                if orig is not None and orig != body:      # 教師信号: モデル案(orig)→人の完成形(body)
                    s["_prev"] = orig
                if instruction:
                    s["_instruction"] = instruction
                s["body"] = body
                break
        doc["version"] = doc.get("version", 1) + 1
        doc["updated"] = _now()
        _save(doc)
        return doc


def add_section(doc_id, heading, body, after=None):
    with _LOCK:
        doc = get(doc_id)
        if not doc:
            return None
        _snapshot(doc, note="add section")
        ns = {"id": _sid(), "heading": heading, "body": body}
        if after:
            idx = next((i for i, s in enumerate(doc["sections"]) if s["id"] == after), len(doc["sections"]) - 1)
            doc["sections"].insert(idx + 1, ns)
        else:
            doc["sections"].append(ns)
        doc["version"] += 1
        doc["updated"] = _now()
        _save(doc)
        return doc


def section(doc, section_id):
    return next((s for s in doc.get("sections", []) if s["id"] == section_id), None)


def restore(doc_id, v):
    """版 v の全文へ戻す(戻す前の現状も版退避=戻す操作自体も可逆)。全文を1節として復元。"""
    with _LOCK:
        doc = get(doc_id)
        if not doc:
            return None
        target = next((s for s in doc.get("versions", []) if s["v"] == int(v)), None)
        if not target:
            return None
        _snapshot(doc, note=f"before restore to v{v}")
        # 全文markdownを節に再パース(## 見出しで分割)
        doc["sections"] = _parse_sections(target["full"])
        doc["version"] += 1
        doc["updated"] = _now()
        _save(doc)
        return doc


def _parse_sections(md):
    """markdown全文を節配列へ(## 見出しで分割・先頭#タイトルは除く)。"""
    import re
    secs = []
    cur = {"id": _sid(), "heading": "", "body": ""}
    for ln in (md or "").splitlines():
        m = re.match(r"^##\s+(.+)$", ln)
        if m:
            if cur["heading"] or cur["body"].strip():
                secs.append(cur)
            cur = {"id": _sid(), "heading": m.group(1).strip(), "body": ""}
        elif re.match(r"^#\s+", ln):
            continue                                       # タイトル行は捨てる
        else:
            cur["body"] += ln + "\n"
    if cur["heading"] or cur["body"].strip():
        cur["body"] = cur["body"].strip()
        secs.append(cur)
    return secs or [{"id": _sid(), "heading": "", "body": (md or "").strip()}]


def versions(doc_id):
    doc = get(doc_id)
    return [{"v": s["v"], "at": s["at"], "note": s.get("note", ""), "chars": len(s.get("full", ""))}
            for s in (doc.get("versions", []) if doc else [])]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        d = get(sys.argv[1])
        print(json.dumps(d, ensure_ascii=False, indent=1)[:1500] if d else "(なし)")
    else:
        n = len([f for f in os.listdir(STORE_DIR) if f.endswith(".json")]) if os.path.isdir(STORE_DIR) else 0
        print(f"casper_doc: {n} 文書 @ {STORE_DIR}")
