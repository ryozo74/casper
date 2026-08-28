#!/usr/bin/env python3
r"""『承認するときはプレビューと承認ボタンを表示するように徹底』の回帰ゲート
(殿御下命 2026-08-28)。全PASSで exit 0。

実測(2026-08-28・kiyotomo殿): 「カードが出ないときがある」の正体は三つあった。
 ① 「見出しを太字にしたり表で整えたりして」で決定的経路が**一度も発火しなかった**
    (cards=0 が5turn)。修正意図の表に**整形系の語彙が一つも無かった**。
    人は「直す」とは言わぬ——「見やすくして」「表にして」「整えて」と言う。
 ② cards=0 の turn で「承認ボタンを押すと Aurora に保存されます」と告げていた(14:57:54)。
    殿は押す物を探して見つからなんだ。
    ★完了検問は「保存しました」という**済んだ嘘**を打ち消すが、
      「これから出ます」という**出る嘘**は見ていなかった。**約束も真実値である。**
 ③ doc_id と body を抱えた生JSONがそのまま画面へ流れた(14:51:26・14:52:27／838字・1188字)。

守る掟:
 ① 整形の頼み(見やすく/表に/太字/整えて…)でも資料修正の決定的経路が立つ。
 ② カードが無いのに『承認ボタンが出る』と約束させぬ。台帳を照会して本当の状態を告げる。
    ★カードが在る turn では触らぬ(約束は裏づけられている)。
    ★『もう一度お申し付けを』の壊れたループへ戻さぬ——何をすれば立つかを具体に示す。
 ③ 生の道具呼び(JSON/代入)は人に見せぬ。中身は承認カードのプレビューで見せる。
 ④ カードには**本文のプレビューと承認ボタンが必ず伴う**(画面側)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["_strip_raw_toolcall", "_guard_card_promise",
          "_guard_completion_claims", "_completion_claim_line_hit"]
WANT_PREFIX = ("_RAW_", "_CARD_PROMISE", "_COMPLETION", "_AURORA_WORD", "_AURORA_EDIT_INTENT")


class _OB:
    rows = []

    @staticmethod
    def pending(uid=None):
        return [r for r in _OB.rows if str(r.get("uid")) == str(uid)]


def build(src_text):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
        if isinstance(n, ast.Assign):
            t = getattr(n.targets[0], "id", "")
            if t.startswith(WANT_PREFIX):
                picked.append(n); seen.add(t)
    missing = [w for w in WANT_F if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, os, json", M)
    M["casper_outbox"] = _OB
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

CARD = {"id": "abc", "tool": "aurora_append", "uid": "31",
        "args": {"doc_id": "d78e9ca6", "body": "本文"}, "summary": "Aurora修正"}

# ── ① 整形の頼みも修正意図と判ずる ───────────────────────────────────────
print("── ① 整形の頼み ──")
for q in ["見出しを太字にしたり表で整えたりして", "見やすくできる？表にしたり",
          "表で整えて", "もっと読みやすくして", "箇条書きに整理して",
          "体裁を整えて", "清書して"]:
    chk(f"① 『{q}』を修正の意図と判ずる", bool(M["_AURORA_EDIT_INTENT_RE"].search(q)))
for q in ["GSの状況を教えて", "最新のDMみせて", "この資料の担当は誰ですか"]:
    chk(f"① 『{q}』は修正と判ぜぬ(過剰発動せぬ)",
        not M["_AURORA_EDIT_INTENT_RE"].search(q))

# ── ② 出る嘘を打ち消す ───────────────────────────────────────────────────
print("── ② 『これから出る』という約束 ──")
PROMISES = ["承認ボタンを押すと Aurora に保存されます。",
            "承認ボタンを押すと反映されます",
            "下のカードを押すと書き込まれます",
            "承認カードを表示しますので押してください",
            "押していただければ Aurora に保存されまする"]
_OB.rows = []
for p in PROMISES:
    out = M["_guard_card_promise"](p, [], uid="31")
    chk(f"② 『{p[:22]}…』を打ち消す", "承認カードは出ておりませぬ" in out)
chk("② ★カードが在る turn では触らぬ(裏づけられた約束を消さぬ)",
    M["_guard_card_promise"](PROMISES[0], [CARD], uid="31") == PROMISES[0])
_OB.rows = [CARD]
out2 = M["_guard_card_promise"](PROMISES[0], [], uid="31")
chk("② この応答では立てておらぬが、他に承認待ちが在れば其処へ導く",
    "1件" in out2 and "承認待ち" in out2)
_OB.rows = []
out3 = M["_guard_card_promise"]("SORAFUNE議事録の下書きです。\n" + PROMISES[0], [], uid="31")
chk("② 役に立つ本文は残す(約束の行だけ抜く)", "SORAFUNE議事録の下書きです。" in out3)
chk("② 『もう一度お申し付けを』の壊れたループへ戻さぬ", "もう一度お申し付けを" not in out3)
chk("② 何をすれば立つかを具体に示す", "どの節を・どう" in out3)
chk("② 約束の無い文には触らぬ",
    M["_guard_card_promise"]("GSは進行中にござる。", [], uid="31") == "GSは進行中にござる。")

# ── ③ 生の道具呼びを見せぬ ───────────────────────────────────────────────
print("── ③ 生の道具呼び ──")
RAW_JSON = ('承認ボタンを押すと Aurora に上書き保存されます。\n\n```json\n{\n'
            '  "doc_id": "d78e9ca6-3ce5-4bc0-9ab9-2502ede67767",\n'
            '  "body": "# SORAFUNE 様 MTG 議事録\\n\\n## 1. シナリオ"\n}\n```')
s1 = M["_strip_raw_toolcall"](RAW_JSON)
chk("③ ★実測の生JSONを剥ぐ", "doc_id" not in s1 and "d78e9ca6" not in s1)
chk("③ 地の文は残す", "承認ボタンを押すと" in s1)
RAW_ASSIGN = ('aurora_append(\n    doc_id="d78e9ca6-3ce5-4bc0-9ab9-2502ede67767",\n'
              '    body="""# SORAFUNE 様 MTG 議事録"""\n)')
s2 = M["_strip_raw_toolcall"](RAW_ASSIGN)
chk("③ ★実測の代入形も剥ぐ", "doc_id=" not in s2 and "aurora_append(" not in s2)
chk("③ 普通の本文は壊さぬ",
    M["_strip_raw_toolcall"]("## 1. シナリオ\n- 火災時想定\n\n| 項目 | 内容 |\n|---|---|\n| A | B |")
    == "## 1. シナリオ\n- 火災時想定\n\n| 項目 | 内容 |\n|---|---|\n| A | B |")
chk("③ 表や箇条書きを含む長文も壊さぬ",
    "| Flight Simulator |" in M["_strip_raw_toolcall"](
        "整えました。\n\n| 項目 | 内容 |\n|---|---|\n| Flight Simulator | 実行 |"))

# ── ④ カードにはプレビューと承認ボタンが伴う(画面) ───────────────────────
print("── ④ 画面(chat.html) ──")
HTML = open(os.path.join(HERE, "chat.html"), encoding="utf-8").read()
_rc = HTML[HTML.index("function renderConfirm(b, pa){"):]
_rc = _rc[:_rc.index("\nfunction ", 10)]
chk("④ 本文のプレビューを出す", "pa.args.body" in _rc and "textarea" in _rc)
chk("④ プレビューは編集できる", "送信前に編集できます" in _rc)
chk("④ 承認ボタンが在る", "承認して実行" in _rc)
chk("④ 却下も選べる", "却下" in _rc)
chk("④ 承認時に編集後の本文を送る(見たまま保存される)", "body: ta?ta.value" in _rc)

# ── ⑤ 結線 ───────────────────────────────────────────────────────────────
print("── ⑤ 結線 ──")
chk("⑤ 約束の検問が応答経路に入っている", SRC_TEXT.count("_guard_card_promise(") >= 3)
chk("⑤ 生の道具呼びの除去も入っている", SRC_TEXT.count("_strip_raw_toolcall(") >= 2)
chk("⑤ 剥いだ末に空になってもカードが在れば案内を出す",
    "この下の承認カードで本文をお確かめの上" in SRC_TEXT)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
_i = SRC_TEXT.index("_CARD_PROMISE_RE = re.compile(")
_j = SRC_TEXT.index("\n\n\ndef _guard_completion_claims", _i) if False else SRC_TEXT.index('re.S)\n', _i) + len('re.S)\n')
mut = SRC_TEXT[:_i] + '_CARD_PROMISE_RE = re.compile(r"絶対に現れぬ語XYZZY")\n' + SRC_TEXT[_j:]
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M2, _ = build(mut)
_OB.rows = []
chk("★変異(約束の検知を殺す): カード無き約束が素通りする(赤化実証)",
    M2["_guard_card_promise"](PROMISES[0], [], uid="31") == PROMISES[0])

_k = SRC_TEXT.index("_AURORA_EDIT_INTENT_RE = re.compile(")
_l = SRC_TEXT.index("フォーマット)\")", _k) + len("フォーマット)\")")
mut2 = SRC_TEXT[:_k] + '_AURORA_EDIT_INTENT_RE = re.compile(r"(追加|追記)")' + SRC_TEXT[_l:]
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut2)
chk("★変異(整形語彙を戻す): 『表で整えて』が修正と判ぜられなくなる(赤化実証)",
    not M3["_AURORA_EDIT_INTENT_RE"].search("見出しを太字にしたり表で整えたりして"))

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
