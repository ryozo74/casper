#!/usr/bin/env python3
"""Casper 左脳リーダー — Score SQLite DB をスキーマ＋サンプルで読み出す。

用途: Casper の DB 理解検証テスト。ローカル LLM にこの出力(または --json)を渡し、
「このDBは何を保持しているか」を説明させ、人間/設計メモと突き合わせて理解の正否を確認する。

Usage:
  python3 read_score_db.py                # 人間可読サマリ
  python3 read_score_db.py --json         # 機械可読 (LLM 投入用)
  python3 read_score_db.py --db <path>    # DB パス指定 (既定: ../../../score_be/score.db)

注意: これは読み取り専用。書き込み/削除は一切行わない。
"""
import argparse, json, os, sqlite3, sys

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "..", "..", "score_be", "score.db")
SKIP_TABLES = {"alembic_version", "sqlite_sequence"}
SAMPLE_LIMIT = 3


def _truncate(v, n=80):
    s = "NULL" if v is None else str(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def read_db(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {"db_path": os.path.abspath(db_path), "tables": {}}
    for t in tables:
        if t in SKIP_TABLES:
            continue
        cols = [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in conn.execute(f"PRAGMA table_info({t})")]
        count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        samples = [dict(r) for r in conn.execute(
            f"SELECT * FROM {t} LIMIT {SAMPLE_LIMIT}").fetchall()]
        out["tables"][t] = {"row_count": count, "columns": cols, "samples": samples}
    conn.close()
    return out


def human(data):
    lines = [f"# Score DB — {data['db_path']}", ""]
    for t, info in data["tables"].items():
        lines.append(f"## {t}  (rows={info['row_count']})")
        for c in info["columns"]:
            flags = " ".join(f for f, on in
                             [("PK", c["pk"]), ("NOT NULL", c["notnull"])] if on)
            lines.append(f"  - {c['name']}: {c['type']} {flags}".rstrip())
        if info["samples"]:
            s = info["samples"][0]
            lines.append("  sample: " + ", ".join(
                f"{k}={_truncate(v, 40)}" for k, v in s.items()))
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"DB not found: {a.db}")
    data = read_db(a.db)
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str)
          if a.json else human(data))


if __name__ == "__main__":
    main()
