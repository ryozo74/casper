#!/usr/bin/env python3
"""Casper: 完了 project を左脳DBから vault へ BK 兼知識化 (御構想④ ポストモーテム基盤)。

完了と判断された project の DB レコードを集め、vault/90_db_archives/ へ
人間可読サマリ + 生 JSON(完全 BK) の markdown を書き出す。
※ローカル間コピーのみ (外部送信なし)。完了 PJ に限り DB→vault を許す運用。

Usage:
  python3 archive_project.py <project_id>            # 実行 (vault へ書込)
  python3 archive_project.py <project_id> --dry-run  # 内容を表示するのみ
"""
import argparse, datetime, json, os, sqlite3

HERE = os.path.dirname(__file__)
DB = os.path.join(HERE, "..", "..", "..", "score_be", "score.db")
VAULT = os.path.abspath(os.path.join(HERE, "..", "vault"))
ARCH_DIR = os.path.join(VAULT, "90_db_archives")


def gather(project_id):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    roles = [dict(r) for r in c.execute(
        "SELECT * FROM score_user_roles WHERE project_id=?", (project_id,))]
    member_ids = sorted({r["user_id"] for r in roles})
    # 運用ログは user_id 単位 (project 直接紐付け無し)。member の user_id で best-effort 収集。
    logs = {}
    for t in ("timecard_logs", "routine_logs"):
        rows = [dict(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()]
        logs[t] = [r for r in rows if str(r.get("user_id")) in member_ids]
    c.close()
    return {"project_id": project_id, "members": roles,
            "member_user_ids": member_ids, "linked_logs": logs}


def render_md(data):
    pid = data["project_id"]
    today = datetime.date.today().isoformat()
    L = [f"---", f"type: db_archive", f"project: {pid}",
         f"archived: {today}", f"source: score_be/score.db (left-brain)",
         f"---", "", f"# 📦 DB アーカイブ — project `{pid}`", "",
         f"> 完了 project の左脳DBスナップショット (BK 兼知識化)。生 JSON は末尾に保全。", "",
         "## メンバー / ロール"]
    if data["members"]:
        L.append("| user_id | role | 登録日 |")
        L.append("|---|---|---|")
        for m in data["members"]:
            L.append(f"| {m.get('user_id')} | {m.get('role')} | {m.get('created_at','')} |")
    else:
        L.append("(なし)")
    L += ["", "## 紐付くログ (member user_id ベース・best-effort)"]
    for t, rows in data["linked_logs"].items():
        L.append(f"- **{t}**: {len(rows)} 件")
    L += ["", "> ⚠️ 注: 運用ログは project に直接紐付かず user_id 単位。"
          "roles はメール形式・ログは数値ID のため名寄せ未確立 "
          "(完全な project 一式は Calendar DB 開通後に統合)。", "",
          "## 生データ (完全 BK)", "```json",
          json.dumps(data, ensure_ascii=False, indent=2, default=str), "```", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = gather(a.project_id)
    md = render_md(data)
    if a.dry_run:
        print(md)
        return
    os.makedirs(ARCH_DIR, exist_ok=True)
    path = os.path.join(ARCH_DIR, f"project_{a.project_id}.md")
    open(path, "w", encoding="utf-8").write(md)
    print(f"archived -> {os.path.relpath(path, VAULT)} ({len(data['members'])} members)")


if __name__ == "__main__":
    main()
