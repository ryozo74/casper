#!/usr/bin/env python3
r"""名指しされた Aurora 資料を直せるようにする回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

実害(2026-08-26 18:18〜18:24): kiyotomo殿が Aurora の資料URLを添えて
「以下の文字を消して下さい」と頼んだ。本文は機構(aurora_url_digest)が取得して注入済で、
修正の道具(aurora_append)も結線済であった。**欠けていたのは鍵=doc_id だけ**である。
その結果 Casper は
  18:22:55「Casperは社内システムの編集機能を持っていません」← 持っているのに無いと言う嘘
  18:24:57「指定された記述を削除しました」                  ← していないのにしたと言う嘘
と、二分の間に逆向きの嘘を二つ吐いた。

守る掟:
 ① 発話中の Aurora URL / doc_id から、**機構が**資料を特定する(モデルに憶測させぬ)。
 ② 三値で答える。名指し無し=None / 名指しあり&特定できた / 名指しあり&特定できぬ。
    ★後の二つを混ぜると、特定できぬ物を黙って新規作成へ倒しかねぬ。
 ③ 『読める』と『直せる』を同じ便で渡す。鍵(doc_id)は本文と一緒に注入する。
    ★別々に渡せば片方だけ見て機構が揺れる——それが 18:22 と 18:24 の逆向きの嘘である。
 ④ 特定できぬ時は「直せぬ」と告げ、新規作成へ倒さぬ。
 ⑤ 版差し替えで本文が大きく減る時は、減る事実を承認カードの表に立てる(silent cap の禁)。
    ★append_version は名に反して中身を丸ごと入れ替える(2nd艦隊の実害記録)。
 ★突然変異: 特定の機構を殺すと①③が赤化することを実証する。

本番の Aurora は叩かない(casper_aurora を stub)。
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["aurora_doc_ref", "aurora_shrink_note"]
WANT_A = ["_AURORA_DOC_URL_RE", "_AURORA_DOC_ID_RE"]

REAL_URL = ("http://nina_notepc_02.local:8100/doc/kiyotomo/2026-08-26/"
            "sorafune-mtg-gijiroku-2026-08-26")
REAL_SLUG = "kiyotomo/2026-08-26/sorafune-mtg-gijiroku-2026-08-26"
REAL_ID = "888676ab-8547-413b-9117-5d9f682b0a41"
CUR_BODY = "<h1>SORAFUNE 様 MTG 議事録</h1>" + ("<p>本文の行。</p>" * 40)


class _Au:
    """Aurora の身代わり。台帳に在るのは REAL_SLUG の一件だけ。"""
    calls = []

    @staticmethod
    def document_exists(slug=None, title=None):
        _Au.calls.append(("document_exists", slug, title))
        if slug == REAL_SLUG:
            return {"id": REAL_ID, "title": "SORAFUNE 様 MTG 議事録", "deleted": False}
        return {}                                   # 該当無し(掟: 失敗=None と 0件={} は別)

    @staticmethod
    def get(doc_id):
        _Au.calls.append(("get", doc_id, None))
        return json.dumps({"id": doc_id, "html": CUR_BODY}) if doc_id == REAL_ID else None


def build(src_text):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in WANT_A for t in n.targets):
            picked.append(n); seen.add(n.targets[0].id)
    missing = [w for w in (WANT_F + WANT_A) if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, json", M)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)
sys.modules["casper_aurora"] = _Au

# ── ①② 三値 ─────────────────────────────────────────────────────────────
print("── ①② 資料の特定(三値) ──")
_Au.calls.clear()
r = M["aurora_doc_ref"](f"{REAL_URL}の以下の文字を消して下さい。\n技術概要: ドローンをAI化。")
chk_true("① ★実害の発話から資料を特定できる", bool(r) and r.get("found") is True)
chk("① doc_id は台帳から引く(モデルに作らせぬ)", (r or {}).get("doc_id"), REAL_ID)
chk_true("① 引く時は slug で照会している", ("document_exists", REAL_SLUG, None) in _Au.calls)
chk("① 題も併せて取れる", (r or {}).get("title"), "SORAFUNE 様 MTG 議事録")

chk("② 名指しが無ければ None(『無い』と『見つからぬ』を混ぜぬ)",
    M["aurora_doc_ref"]("GSの状況を教えて"), None)
r2 = M["aurora_doc_ref"]("http://nina_notepc_02.local:8100/doc/dare/2099-01-01/nai-shiryo を直して")
chk_true("② 名指しはあるが台帳に無い時は found=False で返る",
         bool(r2) and r2.get("found") is False)
chk("② その時 doc_id は空(でっち上げぬ)", (r2 or {}).get("doc_id"), "")

r3 = M["aurora_doc_ref"](f"doc_id {REAL_ID} を直して")
chk_true("② 生のdoc_id直書きも拾う", bool(r3) and r3.get("doc_id") == REAL_ID)
chk_true("① 末尾の句読点を巻き込まぬ",
         M["aurora_doc_ref"](f"{REAL_URL}。を見て")["doc_id"] == REAL_ID)

# ── ③④ 鍵を本文と同じ便で渡す ───────────────────────────────────────────
print("── ③④ 注入(aurora_url_digest) ──")
DIG = SRC_TEXT[SRC_TEXT.index("def aurora_url_digest("):]
DIG = DIG[:DIG.index("\ndef ", 5)]
chk_true("③ 特定できた時 doc_id を本文と同じ便で注入する", "doc_id={_ref['doc_id']}" in DIG)
chk_true("③ aurora_append を名指しで指示する", "**aurora_append**" in DIG)
chk_true("③ 『編集機能が無い』と言わせぬ(18:22:55の嘘の再発防止)",
         "『編集機能が無い/できない』とは言うな" in DIG)
chk_true("③ 全文を渡せと明記する(丸ごと入替の性質を伝える)", "修正後の全文" in DIG)
chk_true("③ 完了を断ずるなと明記する(18:24:57の嘘の再発防止)",
         "完了を断ずるな" in DIG)
chk_true("④ 特定できぬ時は『直せぬ』と告げる", "修正は掛けられぬ" in DIG)
chk_true("④ 特定できぬ時に新規作成へ倒さぬ", "勝手に新規作成へ倒すな" in DIG)

# ── ⑤ 縮む差し替えを表に立てる ───────────────────────────────────────────
print("── ⑤ 版差し替えで減る時 ──")
note = M["aurora_shrink_note"](REAL_ID, "一行だけ")
chk_true("⑤ 大きく減る時は注記が立つ", "減りまする" in note)
chk_true("⑤ 何字→何字かを数で示す", "字 →" in note and "字 に" in note)
chk_true("⑤ 丸ごと入れ替わる性質を告げる", "丸ごと入れ替え" in note)
plain = len(CUR_BODY.replace("<h1>", "").replace("</h1>", "").replace("<p>", "").replace("</p>", ""))
chk("⑤ 減っておらぬ時は黙る(過剰に騒がぬ)",
    M["aurora_shrink_note"](REAL_ID, "あ" * plain), "")
chk("⑤ 照会できぬ資料では黙る(推測で騒がぬ)",
    M["aurora_shrink_note"]("no-such-id", "短い"), "")
chk("⑤ doc_id が無ければ黙る", M["aurora_shrink_note"]("", "短い"), "")

# ── ⑥ 経路への結線(承認ゲートで実際に使われている) ─────────────────────
print("── ⑥ 結線 ──")
_route = SRC_TEXT[SRC_TEXT.index("# aurora_create / aurora_append = 書込 → 承認ゲート"):]
_route = _route[:2000]
chk_true("⑥ 承認ゲートで aurora_doc_ref を引いている", "aurora_doc_ref(ll_user" in _route)
chk_true("⑥ 名指しがスレッド紐付けより優先される", '_ref.get("found")' in _route
         and 'cur = {"doc_id": _ref["doc_id"]' in _route)
chk_true("⑥ 名指しがあれば『新規』へ倒さぬ", "new_intent = False" in _route)
chk_true("⑥ append の要約に縮み注記が付く", "aurora_shrink_note(" in _route)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証(資料特定の機構を殺す) ---")
mut = SRC_TEXT.replace('    m = _AURORA_DOC_URL_RE.search(t)', '    m = None', 1)
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M2, _ = build(mut)
chk_true("★変異(URLを見なくする): 実害の発話から資料を特定できなくなる(赤化実証)",
         M2["aurora_doc_ref"](f"{REAL_URL}の以下の文字を消して下さい") is None)
chk_true("★復元確認: 本物では依然として特定できる",
         M["aurora_doc_ref"](f"{REAL_URL}の以下の文字を消して下さい")["doc_id"] == REAL_ID)

mut2 = SRC_TEXT.replace("    if old_len <= 0 or new_len >= old_len * 0.6:", "    if True:", 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut2)
chk("★変異(縮み検問を殺す): 9割消える差し替えが黙って通る(赤化実証)",
    M3["aurora_shrink_note"](REAL_ID, "一行だけ"), "")

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
