#!/usr/bin/env python3
"""Casper 右脳コネクタ — Obsidian vault の読取/追記。左脳 read_score_db.py の対。

vault = projects/casper/vault/ 配下の markdown 群 (定性・暗黙知)。
このスクリプトが Casper 推論部と vault を繋ぐ唯一の I/O 経路。

Usage:
  python3 vault_io.py list                  # ノート一覧 (path/type/title)
  python3 vault_io.py read [--tag person]   # 全文を連結 (LLM 投入用・任意で type 絞り込み)
  python3 vault_io.py read --json           # 機械可読
  python3 vault_io.py append <folder> <slug> "<本文>"   # ノート追記 (右脳への知識固定)
"""
import argparse, glob, json, os, re, sys

VAULT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vault"))


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
    return fm


def notes(tag=None):
    out = []
    for p in sorted(glob.glob(os.path.join(VAULT, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, VAULT)
        if os.path.basename(p) == "README.md" or rel.startswith("_templates"):
            continue
        text = open(p, encoding="utf-8").read()
        fm = _frontmatter(text)
        if tag and fm.get("type") != tag:
            continue
        title = fm.get("name") or fm.get("title") or os.path.basename(p)
        out.append({"path": rel, "type": fm.get("type", ""), "title": title, "text": text})
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pr = sub.add_parser("read"); pr.add_argument("--tag"); pr.add_argument("--json", action="store_true")
    pa = sub.add_parser("append")
    pa.add_argument("folder"); pa.add_argument("slug"); pa.add_argument("body")
    a = ap.parse_args()

    if a.cmd == "list":
        ns = notes()
        print(f"vault: {VAULT}  ({len(ns)} notes)")
        for n in ns:
            print(f"  [{n['type'] or '-':12}] {n['path']}  ::  {n['title']}")
    elif a.cmd == "read":
        ns = notes(a.tag)
        if a.json:
            print(json.dumps(ns, ensure_ascii=False, indent=2))
        else:
            for n in ns:
                print(f"\n===== {n['path']} =====\n{n['text']}")
    elif a.cmd == "append":
        d = os.path.join(VAULT, a.folder)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, a.slug + ".md")
        with open(path, "a", encoding="utf-8") as f:
            f.write(a.body.rstrip() + "\n")
        print(f"appended -> {os.path.relpath(path, VAULT)}")


if __name__ == "__main__":
    main()
