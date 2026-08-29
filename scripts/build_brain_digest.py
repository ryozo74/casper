#!/usr/bin/env python3
"""Casper: 左脳(Calendar projects)+右脳(vault 人物) を 1枚の社内ナレッジ digest に。
チャットの system prompt に注入し、Casper に会社の文脈を持たせる (融合 v1)。

入力(ローカル・事前取得済): /tmp/cal_projects.json /tmp/cal_users.json
                          projects/casper/vault/20_people/*.md
出力: projects/casper/scripts/casper_context.md
"""
import glob, json, os, re
import pack_paths   # M5: vault/pack パスの単一解決点(CASPER_VAULT/CASPER_PACK env で差替可)

HERE = os.path.dirname(__file__)
PEOPLE = pack_paths.vault("20_people")
OUT = os.environ.get("CASPER_CONTEXT", os.path.join(HERE, "casper_context.md"))   # 差替検証時は別出力へ(live を汚さぬ)


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
    projects = json.load(open(os.environ.get("CASPER_PROJECTS", "/tmp/cal_projects.json")))["items"]
except Exception:
    pass
by = {"completed": [], "in-progress": [], "cancelled": []}
for p in projects:
    by.setdefault(p.get("status"), []).append(p)
def plist(st):
    return ", ".join(f"{p['name']}({str(p.get('end_date'))[:7]})" for p in by.get(st, []))

# --- 右脳: 旧スコア legacy (暗黙知・要約のみ) ---
LEGACY = pack_paths.vault("80_legacy_score")
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
cpath = pack_paths.vault("30_culture_rules", "company.md")   # 会社概要は pack ごと: 汎用 company.md 優先
if not os.path.exists(cpath):                                 # 無ければ bokan 互換名へ fallback
    cpath = pack_paths.vault("30_culture_rules", "studio_bokan_company.md")
if os.path.exists(cpath):
    ct = open(cpath, encoding="utf-8").read()
    # frontmatter 除去し本文先頭を要約として使う
    body = re.sub(r"^---.*?---\s*", "", ct, flags=re.S)
    company = re.sub(r"\n{3,}", "\n", body).strip()[:900]

# --- Casper の人格 (pack/vault 由来・M5 B: engine 定数から外出し。社ごとに差し替わる筆頭) ---
persona = ""
ppath = pack_paths.vault("30_culture_rules", "casper_persona_core.md")
if os.path.exists(ppath):
    persona = open(ppath, encoding="utf-8").read().strip()

# --- Casper の使い方(携帯からの入り方・通知の受け取り方) 正典 (cmd_490 手当2: 固定ファイル名のみ・globはしない) ---
howto = ""
hpath = pack_paths.vault("30_culture_rules", "casper_howto.md")
if os.path.exists(hpath):
    howto = open(hpath, encoding="utf-8").read().strip()

def _engine_owns_policy():
    """engine(engine_policy.md)が policy を注入しているか /health で確認。True なら本 digest は
    policy を省く(逆混入の畳み・M5 C)。不通/未所有(旧コード稼働・ロールバック)は False に倒し
    policy を出力し続ける=守秘/moat の欠落窓ゼロを機構で保証(Fable /health fail-safe)。"""
    try:
        import urllib.request
        r = urllib.request.urlopen("http://localhost:8770/health", timeout=2)
        return json.loads(r.read().decode("utf-8")).get("policy") == "engine"
    except Exception:
        return False


try:
    import pack_config as _pc
    _default_company_description = _pc.get("default_company_description", "(会社概要は未設定)")
except Exception:
    _default_company_description = "(会社概要は未設定)"

doc = [
    "# Casper 社内ナレッジ (左脳=Calendar / 右脳=Obsidian vault)",
    "あなたはこの会社の伴走AI Casper。以下は社内の実データ要約。これを根拠に具体的に答えよ。",
    "",
    "## 会社 (自社)",
    company or _default_company_description,
    "",
    persona,
    "",
    howto,
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
]
# 逆混入の畳み(M5 C): engine ポリシーは engine_policy.md が唯一の真実源。engine が受け皿を持つ時
# (=/health policy:engine)は省き casper_context.md を純・事実に。持たぬ時(旧コード/ロールバック/不通)のみ
# engine_policy.md を読んで fallback 供給=全文重複を排し drift を根絶(単一ソース・Fable処方)。
if not _engine_owns_policy():
    try:
        _pol = open(os.path.join(HERE, "engine_policy.md"), encoding="utf-8").read()
        try:
            import pack_config as _pc
            _cn = _pc.get("secrecy_codenames", []) or []       # CASPER_PACK env 駆動(差替追従)
            _pol = _pol.replace("{SECRECY_CODENAMES}", "/".join(str(c) for c in _cn))
        except Exception:
            pass
        doc += ["", _pol.rstrip()]
    except Exception as _e:
        # fail-loud: policy 欠落の digest を黙って書くのが唯一の負け筋。前世代を温存し中断。
        import sys as _sys
        print("[digest] engine未所有かつ engine_policy.md 読取不可(%s) -> casper_context.md 上書き中断(policy欠落回避)" % _e, file=_sys.stderr)
        _sys.exit(2)
open(OUT, "w", encoding="utf-8").write("\n".join(doc))
print(f"digest -> {OUT}  ({len(plines)}人 / {len(projects)}PJ / {os.path.getsize(OUT)} bytes)")
print(f"matched uids: {sum(1 for l in plines if 'uid ' in l)}/{len(plines)}")
