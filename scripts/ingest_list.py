#!/usr/bin/env python3
"""リスト xlsx(vault/50_asset_shadows/files 配下) -> サムネ付き表の asset ノート化。
使い方: python3 ingest_list.py <vault_filename> <project> <prefix> "<friendly title>"
list_to_markdown(ショットリスト特化/汎用 自動振分け・全シート対応)で表化し、
vault/50_asset_shadows/asset_<prefix>_list.md を生成する。"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_extract as ce  # noqa: E402

VAULT = os.path.abspath(os.path.join(HERE, "..", "vault"))
FILES = os.path.join(VAULT, "50_asset_shadows", "files")
ASSETS = os.path.join(HERE, "assets")


def ingest(filename, project, prefix, title):
    src = os.path.join(FILES, filename)
    if not os.path.exists(src):
        return f"NG (not found): {filename}"
    md = ce.list_to_markdown(src, ASSETS, prefix)
    if not md:
        return f"NG (表化できず): {filename}"
    rows = sum(1 for ln in md.splitlines() if ln.strip().startswith("|")) - md.count("\n|---")
    sheets = md.count("### ") or 1
    note = (
        f"---\nname: {title}\ntype: list\nproject: {project}\nsource: {filename}\n---\n\n"
        f"# {title}\n\n"
        f"{project} プロジェクトのリスト。元ファイル `{filename}`。"
        f"キーワード: {project} ショットリスト shotlist カット表 リスト list スケジュール schedule。\n\n"
        f"## 一覧（サムネ付き）\n\n{md}\n"
    )
    out = os.path.join(VAULT, "50_asset_shadows", f"asset_{prefix}_list.md")
    open(out, "w", encoding="utf-8").write(note)
    return f"OK: {filename} -> asset_{prefix}_list.md (sheets={sheets}, rows~{rows})"


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        print(ingest(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
    else:
        print(__doc__)
