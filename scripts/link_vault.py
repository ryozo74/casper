#!/usr/bin/env python3
"""Casper vault を Obsidian らしく連結: タグ付与 + 相互リンク + MOC + グラフ色グループ。
冪等。既存の自動セクション/タグは貼り替える。
Usage: python3 link_vault.py
"""
import glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.abspath(os.path.join(HERE, "..", "vault"))
AUTO = "## 関連 (自動リンク)"
TAGMAP = {"20_people": "person", "90_db_archives": "project",
          "80_legacy_score": "legacy", "10_meetings": "comms", "30_culture_rules": "company"}


def rd(p): return open(p, encoding="utf-8").read()
def wr(p, s): open(p, "w", encoding="utf-8").write(s)
def base(p): return os.path.splitext(os.path.basename(p))[0]
def link(p): return f"[[{base(p)}]]"
def fmval(t, k):
    m = re.search(rf"^{k}:\s*(.+)$", t, re.M)
    return m.group(1).strip() if m else ""


def set_tags(t, tags):
    """frontmatter に tags 行を挿入/更新。"""
    block = "tags: [" + ", ".join(tags) + "]"
    if re.search(r"^tags:.*$", t, re.M):
        return re.sub(r"^tags:.*$", block, t, count=1, flags=re.M)
    return re.sub(r"^(type:.*)$", r"\1\n" + block, t, count=1, flags=re.M)


def set_auto(t, links):
    """## 関連(自動リンク) セクションを貼り替え/追記。"""
    body = AUTO + "\n" + "\n".join(f"- {l}" for l in links) + "\n"
    if AUTO in t:
        return re.sub(re.escape(AUTO) + r".*?(?=\n## |\Z)", body, t, flags=re.S)
    return t.rstrip() + "\n\n" + body


files = {k: sorted(glob.glob(os.path.join(V, k, "*.md"))) for k in TAGMAP}

# --- uid -> person ---
uid2p = {}
for p in files["20_people"]:
    u = re.search(r"calendar_user_id:\s*(\d+)", rd(p))
    if u:
        uid2p[u.group(1)] = p

# --- project archive: 担当uid 抽出 -> people ---
proj_people = {}      # archive path -> [person path]
person_proj = {}      # person path -> [archive path]
for a in files["90_db_archives"]:
    t = rd(a)
    uids = set(re.findall(r"^\|.*\|\s*(\d+)\s*\|\s*$", t, re.M))
    ppl = [uid2p[u] for u in uids if u in uid2p]
    proj_people[a] = ppl
    for pp in ppl:
        person_proj.setdefault(pp, []).append(a)

# --- LINE(comms) -> 対応する archive (project frontmatter 一致) ---
def archive_for(projname):
    for a in files["90_db_archives"]:
        if projname and projname.lower() in base(a).lower():
            return a
    return None

# === 書き込み ===
n = 0
for k, fl in files.items():
    for p in fl:
        t = rd(p)
        t = set_tags(t, ["casper", TAGMAP[k]])
        links = []
        if k == "90_db_archives":
            links += [link(x) for x in proj_people.get(p, [])]
            links += ["[[studio_bokan_company]]"]
        elif k == "20_people":
            links += [link(x) for x in person_proj.get(p, [])]
            links += ["[[studio_bokan_company]]"]
        elif k == "10_meetings":
            pj = fmval(t, "project")
            a = archive_for(pj)
            if a:
                links.append(link(a))
        elif k == "80_legacy_score":
            links += ["[[studio_bokan_company]]"]
        elif k == "30_culture_rules":
            links += ["[[00_Casper_Home]]"]
        if links:
            t = set_auto(t, sorted(set(links)))
        wr(p, t)
        n += 1

# === MOC ハブ ===
def moc(name, title, items, intro):
    body = ["---", "tags: [casper, moc]", "---", "", f"# {title}", "", intro, ""]
    body += [f"- {link(x)}" for x in items]
    wr(os.path.join(V, name), "\n".join(body))

moc("20_people/_People.md", "👥 People (MOC)", files["20_people"], "全メンバーのスキル票。")
moc("90_db_archives/_Projects.md", "🗂️ Projects (MOC)", files["90_db_archives"], "Calendar 完了PJアーカイブ。")
moc("80_legacy_score/_Legacy.md", "🗄️ Legacy score (MOC)", files["80_legacy_score"], "旧スコアの暗黙知。")

home = ["---", "tags: [casper, moc, home]", "---", "", "# 🫧 Casper Home",
        "", "Casper 右脳 vault の入口。", "",
        "## 会社", "- [[studio_bokan_company]]", "- [[studio_bokan_brochure]]", "",
        "## グループ (MOC)", "- [[_People]] — メンバー/スキル",
        "- [[_Projects]] — 完了PJアーカイブ", "- [[_Legacy]] — 旧スコア暗黙知", "",
        "## コミュニケーション", "- " + "\n- ".join(link(x) for x in files["10_meetings"])]
wr(os.path.join(V, "00_Casper_Home.md"), "\n".join(home))

# === グラフ カラーグループ (フォルダ別) ===
graph = {"colorGroups": [
    {"query": "path:20_people", "color": {"a": 1, "rgb": 5025616}},     # green
    {"query": "path:90_db_archives", "color": {"a": 1, "rgb": 3768042}},  # blue
    {"query": "path:80_legacy_score", "color": {"a": 1, "rgb": 14315734}},  # amber
    {"query": "path:10_meetings", "color": {"a": 1, "rgb": 15277667}},   # pink
    {"query": "path:30_culture_rules", "color": {"a": 1, "rgb": 10233776}},  # purple
    {"query": "tag:#moc", "color": {"a": 1, "rgb": 16777215}},           # white
]}
os.makedirs(os.path.join(V, ".obsidian"), exist_ok=True)
wr(os.path.join(V, ".obsidian", "graph.json"), json.dumps(graph, indent=2))

print(f"linked {n} notes / uid-matched people {len(uid2p)} / "
      f"archives with people {sum(1 for v in proj_people.values() if v)} / MOC 4 / graph groups 6")
