#!/usr/bin/env python3
"""Casper: 左脳(Calendar projects)+右脳(vault 人物) を 1枚の社内ナレッジ digest に。
チャットの system prompt に注入し、Casper に会社の文脈を持たせる (融合 v1)。

入力(ローカル・事前取得済): /tmp/cal_projects.json /tmp/cal_users.json
                          projects/casper/vault/20_people/*.md
出力: projects/casper/scripts/casper_context.md
"""
import glob, json, os, re

HERE = os.path.dirname(__file__)
PEOPLE = os.path.join(HERE, "..", "vault", "20_people")
OUT = os.path.join(HERE, "casper_context.md")


def section(text, head):
    m = re.search(rf"##[^\n]*{re.escape(head)}[^\n]*\n(.*?)(?=\n##|\Z)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def fm(text, key):
    m = re.search(rf"^{key}:\s*(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


# --- users (名寄せ用) ---
users = []
try:
    users = json.load(open("/tmp/cal_users.json"))["items"]
except Exception:
    pass
def match_uid(name):
    low = name.lower().replace(" ", "")
    for u in users:
        un = (u.get("username") or "").lower()
        if un and (un in low or low in un):
            return u["id"]
    return None

# --- 右脳: 人物 ---
plines = []
for p in sorted(glob.glob(os.path.join(PEOPLE, "*.md"))):
    t = open(p, encoding="utf-8").read()
    name = fm(t, "name"); role = fm(t, "role")
    if not name:
        continue
    skill = section(t, "得意分野")[:80]
    soft = section(t, "使用可能ソフト")[:90]
    uid = match_uid(name)
    tag = f" (uid {uid})" if uid else ""
    plines.append(f"- {name}{tag} [{role}] 得意: {skill or '—'} / ソフト: {soft or '—'}")

# --- 左脳: projects ---
projects = []
try:
    projects = json.load(open("/tmp/cal_projects.json"))["items"]
except Exception:
    pass
by = {"completed": [], "in-progress": [], "cancelled": []}
for p in projects:
    by.setdefault(p.get("status"), []).append(p)
def plist(st):
    return ", ".join(f"{p['name']}({str(p.get('end_date'))[:7]})" for p in by.get(st, []))

# --- 右脳: 旧スコア legacy (暗黙知・要約のみ) ---
LEGACY = os.path.join(HERE, "..", "vault", "80_legacy_score")
legacy = []
for lp in sorted(glob.glob(os.path.join(LEGACY, "*.md"))):
    t = open(lp, encoding="utf-8").read()
    nm = fm(t, "project") or os.path.basename(lp)[:-3]
    m = re.search(r"フィードバック総数:\s*(\d+).*?実質\s*(\d+)", t)
    cnt = int(m.group(2)) if m else 0
    if cnt:
        legacy.append((nm, cnt))
legacy.sort(key=lambda x: -x[1])

# --- 会社概要 (自社の文脈) ---
company = ""
cpath = os.path.join(HERE, "..", "vault", "30_culture_rules", "studio_bokan_company.md")
if os.path.exists(cpath):
    ct = open(cpath, encoding="utf-8").read()
    # frontmatter 除去し本文先頭を要約として使う
    body = re.sub(r"^---.*?---\s*", "", ct, flags=re.S)
    company = re.sub(r"\n{3,}", "\n", body).strip()[:900]

doc = [
    "# Casper 社内ナレッジ (左脳=Calendar / 右脳=Obsidian vault)",
    "あなたはこの会社の伴走AI Casper。以下は社内の実データ要約。これを根拠に具体的に答えよ。",
    "",
    "## 会社 (自社)",
    company or "株式会社 studio bokan (CG・映像制作)。",
    "",
    f"## プロジェクト (Calendar・全{len(projects)})",
    f"- 完了({len(by.get('completed',[]))}): {plist('completed')}",
    f"- 進行中({len(by.get('in-progress',[]))}): {plist('in-progress')}",
    f"- 中止({len(by.get('cancelled',[]))}): {plist('cancelled')}",
    "※各完了PJの詳細(タスク/ショット)は vault/90_db_archives に保全済。",
    "",
    f"## メンバー / スキル ({len(plines)}名・スキルシート由来)",
    *plines,
    "",
    f"## 旧スコア legacy ({len(legacy)}PJ・vault/80_legacy_score)",
    "過去PJの監督フィードバック等の暗黙知を蒸留保全。具体の指摘文・品質基準は各ノート参照(RAG化は今後)。",
    "フィードバック数: " + ", ".join(f"{n}({c})" for n, c in legacy),
    "",
    "## Aurora(共有ノート図書館・準備中)",
    "Aurora=社員紐づきの会社の共有図書館(議事録/レポート/分析等のHTML資料を集約・共有・検索)。Casperはその司書として全ノートを理解し、会話中に関連ノートをサジェストする。",
    "- 作成は双方向(Casper経由/Aurora UI)。Casperは会社の知識(Calendar/vault/既存ノート)を使い軽いSTATE(JSON)を書き、Auroraレンダラで整ったHTMLに描画→保存できる(準備中)。追記・編集・削除はGit的に履歴。",
    "- 棚=Elvis殿のHTML Archive Server(検索・保存・履歴) / 筆=Auroraスキル(design_dataset準拠で非素人HTML)。現状Elvis殿の書込MCP・接続情報 到着待ち。",
    "",
    "## 回答方針",
    "- 上記の事実に基づき具体的に。不明なら『その情報は未取得』と正直に言う。創作しない。",
    "- 人物の得意/担当を問われたらスキルと(分かれば)担当PJを結びつけて答える。",
]
open(OUT, "w", encoding="utf-8").write("\n".join(doc))
print(f"digest -> {OUT}  ({len(plines)}人 / {len(projects)}PJ / {os.path.getsize(OUT)} bytes)")
print(f"matched uids: {sum(1 for l in plines if 'uid ' in l)}/{len(plines)}")
