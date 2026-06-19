#!/usr/bin/env python3
"""LINE チャット export → vault/10_meetings/<proj>_LINE_chat.md (プロジェクト communication 暗黙知)。
Usage: python3 import_line_chat.py <proj> <txt path>
"""
import os, re, sys
from collections import Counter

proj = sys.argv[1]
src = sys.argv[2]
HERE = os.path.dirname(__file__)
OUT = os.path.abspath(os.path.join(HERE, "..", "vault", "10_meetings"))

DATE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})")
TIME = re.compile(r"^(\d{1,2}:\d{2})\s+(.*)$")
# 既知話者(先頭一致で話者を切り出す)。実際の話者名に置き換えて使用。
SPEAKERS = ["メンバーA", "メンバーB", "メンバーC", "メンバーD", "メンバーE"]

raw = open(src, encoding="utf-8", errors="replace").read().splitlines()

msgs = []           # (date, time, speaker, text)
cur_date = ""
buf = None          # [date,time,speaker,textlines]
def flush():
    global buf
    if buf:
        msgs.append((buf[0], buf[1], buf[2], "\n".join(buf[3]).strip()))
    buf = None

for ln in raw:
    md = DATE.match(ln.strip())
    if md:
        flush(); cur_date = f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}"; continue
    mt = TIME.match(ln)
    if mt:
        flush()
        rest = mt.group(2)
        sp = next((s for s in SPEAKERS if rest.startswith(s)), "")
        if sp:
            text = rest[len(sp):].strip()
        else:  # 先頭トークンを話者とみなす
            parts = rest.split(None, 1)
            sp = parts[0] if parts else ""
            text = parts[1] if len(parts) > 1 else ""
        buf = [cur_date, mt.group(1), sp, [text] if text else []]
    else:
        if buf is not None and ln.strip():
            buf[3].append(ln.rstrip())
flush()

people = Counter(m[2] for m in msgs if m[2])
dates = sorted({m[0] for m in msgs if m[0]})

L = ["---", "type: chat", f"project: {proj}", "channel: LINE",
     "source: Downloads/TVCM.txt", "---", "",
     f"# 💬 {proj} — LINE チャット (制作 communication)", "",
     "> プロジェクト周りの実コミュニケーション。リテイク指示・素材受け渡し・判断の生記録(暗黙知)。", "",
     "## 概要",
     f"- 期間: {dates[0]} 〜 {dates[-1]}" if dates else "- 期間: 不明",
     f"- メッセージ数: {len(msgs)}",
     f"- 主要話者: " + ", ".join(f"{n}({c})" for n, c in people.most_common(8)),
     "", "## ログ (時系列)", "形式: `日付 HH:MM 話者: 内容`", ""]

last_date = ""
for d, t, sp, text in msgs:
    if d != last_date:
        L.append(f"\n### {d}"); last_date = d
    body = re.sub(r"\s*\n\s*", " / ", text).strip()
    L.append(f"- `{t}` **{sp}**: {body}")

L += ["", "## 教訓・ニュアンス (運用で追記)", "> "]

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, f"{proj}_LINE_chat.md")
open(path, "w", encoding="utf-8").write("\n".join(L))
print(f"-> 10_meetings/{proj}_LINE_chat.md  (msgs {len(msgs)}, {len(dates)} days, {os.path.getsize(path)} bytes)")
print("speakers:", dict(people.most_common(8)))
