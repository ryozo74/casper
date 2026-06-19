#!/usr/bin/env python3
"""Casper: 完了 project を Calendar(左脳本体)から vault へ BK 兼知識化 (御構想④)。

egress(Calendar 接続)は呼び出し側 bash の curl が肩代わりし、本スクリプトは
事前取得済 JSON (/tmp/cal_arch_*.json) を読んで vault へ書くだけ (ローカル処理)。

Usage (通常は wrapper 経由):
  python3 calendar_archive.py <project_id>
  入力: /tmp/cal_arch_project.json /tmp/cal_arch_tasks.json /tmp/cal_arch_shots.json
"""
import datetime, json, os, sys
from collections import Counter

HERE = os.path.dirname(__file__)
VAULT = os.path.abspath(os.path.join(HERE, "..", "vault"))
ARCH_DIR = os.path.join(VAULT, "90_db_archives")
pid = sys.argv[1] if len(sys.argv) > 1 else ""


def load(name):
    d = json.load(open(f"/tmp/cal_arch_{name}.json", encoding="utf-8"))
    return d.get("items", d) if isinstance(d, dict) else d


proj = load("project")
if isinstance(proj, list):
    proj = proj[0] if proj else {}
tasks = load("tasks")
shots = load("shots")
name = proj.get("name", f"project_{pid}")
today = datetime.date.today().isoformat()

st = Counter(t.get("status") for t in tasks)
done = st.get("completed", 0) + st.get("approved", 0)

L = [
    "---", "type: db_archive", f"project: {name}", f"project_id: {pid}",
    f"status: {proj.get('status')}", f"archived: {today}",
    "source: Calendar DB /api/readonly (left-brain)", "---", "",
    f"# 📦 完了プロジェクト アーカイブ — {name}", "",
    f"> Calendar(左脳本体)の完了PJスナップショット。BK 兼 知識化。生 JSON は末尾に完全保全。", "",
    "## 概要",
    f"- **状態**: {proj.get('status')} / display: {proj.get('display_status')}",
    f"- **期間**: {str(proj.get('start_date'))[:10]} 〜 {str(proj.get('end_date'))[:10]}",
    f"- **予算**: {proj.get('budget')}",
    f"- **説明**: {(proj.get('description') or '(なし)').strip()[:300]}",
    "",
    f"## タスク ({len(tasks)} 件 / 完了 {done})",
    "状態内訳: " + ", ".join(f"{k}={v}" for k, v in st.items()) if tasks else "(タスクなし)",
    "",
]
if tasks:
    L += ["| id | name | status | progress | 担当 |", "|---|---|---|---|---|"]
    for t in tasks[:40]:
        nm = (t.get("name") or "")[:40].replace("|", "/")
        L.append(f"| {t.get('id')} | {nm} | {t.get('status')} | "
                 f"{t.get('progress') if t.get('progress') is not None else '-'} | {t.get('assigned_to')} |")
    if len(tasks) > 40:
        L.append(f"| … | (他 {len(tasks)-40} 件は生JSON参照) | | | |")
L += ["", f"## ショット ({len(shots)} 件)",
      ", ".join(str(s.get("name") or s.get("id")) for s in shots[:30]) or "(なし)", "",
      "## 生データ (完全 BK)", "```json",
      json.dumps({"project": proj, "tasks": tasks, "shots": shots},
                 ensure_ascii=False, indent=2, default=str), "```", ""]

os.makedirs(ARCH_DIR, exist_ok=True)
safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40]
path = os.path.join(ARCH_DIR, f"project_{pid}_{safe}.md")
open(path, "w", encoding="utf-8").write("\n".join(L))
print(f"archived -> {os.path.relpath(path, VAULT)}  (tasks={len(tasks)}, shots={len(shots)})")
