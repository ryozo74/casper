#!/usr/bin/env python3
"""Casper: アーティスト スキルシート(xlsx) → vault/20_people/ へ人物ノート化。

右脳(定性・暗黙知)の中核データ。stdlib のみで xlsx 解析 (openpyxl 不要)。
ローカル間コピー (外部送信なし)。社内/社外問わず取り込む。

Usage: python3 import_skill_sheet.py "<xlsx path>"
"""
import os, re, sys, zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HERE = os.path.dirname(__file__)
OUT = os.path.abspath(os.path.join(HERE, "..", "vault", "20_people"))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/mnt/h/めも/2026_05_skill_sheet_v02.xlsx"


def colnum(ref):
    c = re.match(r"[A-Z]+", ref).group(); n = 0
    for ch in c:
        n = n * 26 + (ord(ch) - 64)
    return n


def read_rows(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    sp = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n))[0]
    rows = []
    for row in ET.fromstring(z.read(sp)).iter(f"{NS}row"):
        cells = {}
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v"); val = v.text if v is not None else None
            if c.get("t") == "s" and val is not None:
                val = shared[int(val)]
            cells[colnum(c.get("r"))] = val
        mx = max(cells) if cells else 0
        rows.append([cells.get(i) for i in range(1, mx + 1)])
    return rows


def slug(s):
    s = re.sub(r"[（(].*?[)）]", "", s or "")          # 読み括弧除去
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー]", "", s)
    return s[:24] or "unknown"


rows = read_rows(SRC)
# ヘッダ行 = "名前" を含む行
hi = next(i for i, r in enumerate(rows) if r and any(c == "名前" for c in r if c))
header = {c.strip(): j for j, c in enumerate(rows[hi]) if c}

def get(r, key):
    j = header.get(key)
    return (r[j].strip() if j is not None and j < len(r) and r[j] else "") if r else ""

made = []
for r in rows[hi + 1:]:
    name = get(r, "名前")
    if not name:
        continue
    no = get(r, "No.")
    fields = {k: get(r, k) for k in ("役職・職域", "ステータス", "経歴", "主な使用可能ソフト",
                                     "得意分野/アピールポイント", "過去に携わった主な作品・経歴")}
    fm = ["---", "type: person", f"name: {name}",
          f"role: {fields['役職・職域']}", f"status: {fields['ステータス']}",
          "calendar_user_id:    # 左脳 Calendar users と名寄せ (未設定)",
          "source: 2026_05_skill_sheet_v02.xlsx", "---", "",
          f"# {name}", ""]
    body = []
    if fields["役職・職域"]: body += [f"**役職・職域**: {fields['役職・職域']}　/　**ステータス**: {fields['ステータス'] or '—'}", ""]
    if fields["経歴"]: body += ["## 経歴", fields["経歴"], ""]
    if fields["主な使用可能ソフト"]: body += ["## 主な使用可能ソフト", fields["主な使用可能ソフト"], ""]
    if fields["得意分野/アピールポイント"]: body += ["## 得意分野 / アピールポイント (右脳の核)", fields["得意分野/アピールポイント"], ""]
    if fields["過去に携わった主な作品・経歴"]: body += ["## 過去の主な作品・経歴", fields["過去に携わった主な作品・経歴"], ""]
    body += ["## ニュアンス・暗黙知 (運用で追記)", "> ", ""]
    os.makedirs(OUT, exist_ok=True)
    fn = f"{no.zfill(2) if no.isdigit() else no}_{slug(name)}.md"
    open(os.path.join(OUT, fn), "w", encoding="utf-8").write("\n".join(fm + body))
    made.append((fn, name, fields["役職・職域"], fields["ステータス"]))

print(f"imported {len(made)} 人 -> 20_people/")
for fn, nm, role, st in made:
    print(f"  {fn:<22} {nm:<14} {role:<16} status={st or '-'}")
