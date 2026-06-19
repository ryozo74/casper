#!/usr/bin/env python3
"""旧スコア抽出JSON → vault/80_legacy_score/<PJ>.md (暗黙知ノート)。
Usage: python3 build_legacy_note.py <proj> <json_path>
"""
import json, os, re, sys

proj = sys.argv[1]
src = sys.argv[2]
HERE = os.path.dirname(__file__)
OUT = os.path.abspath(os.path.join(HERE, "..", "vault", "80_legacy_score"))

d = json.load(open(src, encoding="utf-8-sig"))
msgs = d.get("msgs", [])

# 自動通知(SHARE UP 等)を除き、実フィードバックだけ残す
NOISE = re.compile(r"S\s*H\s*A\s*R\s*E\s*U\s*P|SHARE\s*UP|^\s*$", re.I)
real = [m for m in msgs if (m.get("msg") or "").strip() and not NOISE.search(m.get("msg") or "")]


def fdate(s):
    s = str(s or "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else s


def topn(dct, n=12):
    return sorted(dct.items(), key=lambda x: -x[1])[:n]


L = ["---", "type: legacy_project", f"project: {proj}",
     "source: 旧スコア score_dir (X:/cg/proj/score_dir)", "---", "",
     f"# 🗄️ 旧スコア — {proj}", "",
     "> Calendar 以前のファイルベース制作管理から抽出した**過去の暗黙知**。"
     "監督フィードバック・担当・工程・品質基準の生記録。", "",
     "## 概要",
     f"- cut×工程 エントリ: {d.get('cut_dept_entries')}",
     f"- 工程: " + ", ".join(f"{k}({v})" for k, v in topn(d.get("depts", {}))),
     f"- 担当(登場回数): " + ", ".join(f"{k}×{v}" for k, v in topn(d.get("artists", {}))),
     f"- 最終ステータス分布: " + ", ".join(f"{k}={v}" for k, v in topn(d.get("status_dist", {}))),
     f"- レンダ session 数: {d.get('movie_sessions')}",
     f"- フィードバック総数: {d.get('msg_count')} (実質 {len(real)})",
     "",
     "## 監督フィードバック / やりとり (暗黙知の核)",
     "時系列。形式: `日付 [cut/工程] user: 内容`", ""]

for m in sorted(real, key=lambda x: str(x.get("date") or "")):
    msg = re.sub(r"\s*\n\s*", " / ", (m.get("msg") or "").strip())
    L.append(f"- `{fdate(m.get('date'))}` **[{m.get('cut')}/{m.get('dept')}]** {m.get('user')}: {msg}")

L += ["", "## ニュアンス・教訓 (運用で追記)", "> "]

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, f"{proj}.md")
open(path, "w", encoding="utf-8").write("\n".join(L))
print(f"-> 80_legacy_score/{proj}.md  (feedback {len(real)}/{d.get('msg_count')}, {os.path.getsize(path)} bytes)")
