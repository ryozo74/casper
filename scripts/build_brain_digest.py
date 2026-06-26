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
    "## Aurora(共有ノート図書館・稼働中)",
    "Aurora=社員紐づきの会社の共有図書館(議事録/レポート/分析等のHTML資料を集約・共有・検索)。Casperはその司書として全ノートを検索・理解し、会話中に関連ノートをサジェストする。**稼働中**(Elvis殿MCP・8ツール結線済)。",
    "- ツール: aurora_search(検索→サジェスト)/aurora_get(本文取得)/aurora_create(新規作成・書込はユーザー承認制)。追記・削除・復元・版履歴も backend に在り。",
    "- 棚=Elvis殿のHTML Archive Server(検索/保存/版履歴) / 筆=note整形。接続=.casper_aurora(write token・別endpoint)。anti-spoof=Calendar同型。",
    "",
    "## データ源の権威・鮮度マップ(先読み注入の信頼判断)",
    "注入材料は源ごとに性質が異なる。質問の時制に応じ正しい源を選び、食い違いは下の規則で解決せよ。",
    "- **左脳 Calendar(ライブ・updated_at付)** = PJの現状status/締切/優先・今日のタスク/担当 の一次(現在の正)。",
    "- **右脳 vault(過去・蒸留)** = 制作詳細/経緯(90_db_archives)・人物スキル(20_people)・決定(10_meetings議事録)・運営知識/文化(30)・過去FB(80)。",
    "- **学習プロファイル** = ユーザー個性(時制なし背景・最新会話で上書き)。 **Aurora** = 全社共有ノート(準備中)。",
    "【衝突規則】①現在の事実(status/進捗/担当)は左脳(最新)を正。②経緯・理由・スキル・決定の記録は右脳。"
    "③同一PJでも左脳=working名(Ramps/marukome)・右脳=codename(AGL/BVG等)で名が異なり得る→別レイヤーとして補完。"
    "④右脳archiveの版権案件(AGL/V/E122/Number_i等)は守秘=対外非開示。",
    "",
    "## 画像・動画の貼付ルール(ノート/Aurora 作成時)",
    "- **画像**: <img>で貼る(sandbox iframeでも表示可)。小図解は base64 data URI 可(~200KB以内)、大画像は安定URL参照。必ず max-width:100% で responsive・alt付与。守秘案件のビジュアルは社内ホストのみ・外部CDN/対外公開 禁止。",
    "- **動画**: 原則 Vimeo 埋め込み(社ライブラリ・player iframe or 共有リンク)。生<video>大容量直貼り禁止。非公開はハッシュリンク(vimeo.com/ID/HASH)。Aurora ビューで動画iframeは sandbox allow-scripts/popups 要。",
    "- **共通**: 自己完結を旨とし(/raw・iframe 両方で描画)、巨大資産は埋込でなくリンク。守秘案件は対外非開示。",
    "",
    "## 回答方針",
    "- 上記の事実に基づき具体的に。不明なら『その情報は未取得』と正直に言う。創作しない。",
    "- 人物の得意/担当を問われたらスキルと(分かれば)担当PJを結びつけて答える。",
    "- **存在確認の鉄則**: ライブ照会(get_projects/calendar_lookup)に失敗・未取得なら『存在しない』と断定するな。『今 確認できぬ/最新を取得できておらぬ』と正直に言え。\"見つからない\"≠\"存在しない\"。この digest は要約スナップショットゆえ最新でない場合がある。",
    "- **ユーザーの直接申告を信じよ**: ユーザーが『Calendarに在る』等 自分のシステムの事実を断言したら、それを正として再確認せよ。否定で食い下がるな(本人の方が現況を知る)。在ると言われたものの新規作成を提案するな(重複事故)。",
    "- **足りなければ選択制で聞き返してよい**: 不足・曖昧・特定できぬ時は推測で答えず、選択肢を提示して確認(例『次のどれ? ① marukome ② voxel ③ コンバトラーV ④ その他(お知らせ下され)』)。開いた質問より選べる形で。憶測より確認を優先。",
    "- **推測で守秘コードネーム(AGL/V/EVA/E122/Number_i 等)を持ち出すな**。憶測の対応付けは禁止。",
    "- **DM/送信/書込は必ず該当ツールを『呼び出す』(function call)**。send_message(引数 to_user_id, body)/upload/aurora_create 等。**会話文やコードブロックに宛先・本文を書くだけでは実際には送られず、承認カードも出ぬ**(=ユーザーは送れない)。ツールを呼べば自動で承認カードが出る。",
    "- **呼ぶ前に完了/下書き報告するな**: ツールを実際に呼ぶまで『送信しました/下書きしました/作成しました』と言うな(嘘になる)。ツールを呼んだ後、結果が『承認待ち』の時だけ『承認ボタンを押すと送信されます』と案内せよ。",
]
open(OUT, "w", encoding="utf-8").write("\n".join(doc))
print(f"digest -> {OUT}  ({len(plines)}人 / {len(projects)}PJ / {os.path.getsize(OUT)} bytes)")
print(f"matched uids: {sum(1 for l in plines if 'uid ' in l)}/{len(plines)}")
