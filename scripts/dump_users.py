#!/usr/bin/env python3
"""Calendar の実ユーザー一覧(uid付き)を /tmp/cal_users.json に保存。
個性Rnd の『登場人物→アカウント紐付け』の照合元データ取得に使う。
トークンは環境変数 CASPER_RO_TOKEN から(なければ読取専用トークンファイル)。
実行: ! cd /mnt/h/multi-agent-shogun-main && python3 projects/casper/scripts/dump_users.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TOKEN_PATH = r"X:\cg\proj\research\users\nibu\read_only_token.txt"


def _token():
    tok = os.environ.get("CASPER_RO_TOKEN", "").strip()
    if tok:
        return tok
    # サーバ起動と同じく powershell で読取専用トークンファイルから取得
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"(Get-Content '{TOKEN_PATH}')"],
            capture_output=True, text=True, timeout=20).stdout
        import re
        m = re.search(r"[A-Za-z0-9]{30,}", out.replace("\r", ""))
        return m.group(0) if m else ""
    except Exception:
        return ""


def main():
    os.environ["CASPER_RO_TOKEN"] = _token()
    import importlib
    import casper_tools
    importlib.reload(casper_tools)
    d = casper_tools._get("/users?limit=200")
    items = d.get("items", d if isinstance(d, list) else [])
    out = "/tmp/cal_users.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"saved {len(items)} users -> {out}")
    for u in items:
        print(" ", u.get("id"), "|", u.get("username"), "|",
              u.get("email"), "|",
              u.get("full_name") or u.get("name") or u.get("display_name") or "")


if __name__ == "__main__":
    main()
