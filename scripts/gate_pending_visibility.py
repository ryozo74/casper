#!/usr/bin/env python3
r"""承認待ちが人の視界に届くことの回帰ゲート(Fable診断 2026-08-27)。全PASSで exit 0。

Fable の実測: カードが**実際に画面に出た時**、利用者は 36/38/54秒 で正しく承認・却下した。
**カードは分かりにくくない。問題はすべて「不在」側にある。**
 ・カードは作られた turn のストリーミング内でしか描画されず、リロード・新スレッドで
   承認待ちが視界から永久に消えた(8/26夕の二枚が翌13:38まで宙吊り)
 ・再浮上の機構は在ったが、発火語が「下書き/承認待ち」で、利用者の言う
   「承認ボタン」「承認カード」に**一つも合致しなかった**
   (15:17〜15:40、カードが実在したまま殿は九度問われ、機構は口が開かなかった)
 ・fail-closed の注記が outbox を照会せず「もう一度お申し付けを」と壊れたループへ再誘導した

守る掟:
 ① カードについての問いは**台帳の実物**で答える。モデルに答えさせぬ。
 ② 在る時は実物を再描画し、無い時は「0件」と正直に名乗る。
    ★無い時に「もう一度お申し付けを」と言えば、それは壊れたループへの再誘導である。
 ③ 注記は**指示でなく状態の申告**にする(命令文ゆえ利用者に貼り返された実害あり)。
 ④ カードに恒久の住所を与える(常設トレイ・ロード時に台帳を引く)。
 ⑤ 錨は再読込を跨いで生き延び、その生き死にが**刻まれる**。
    ★観測できぬ機構は、次に壊れた時もまた「推し量り」で終わる。
 ⑥ 錨は人ごとの控えを持つ(kiyotomo殿は一午後に5回新スレッドを開かれた)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


TMP = tempfile.mkdtemp(prefix="gate_pend_")

WANT_F = ["card_ask_answer", "_guard_completion_claims", "_completion_claim_line_hit",
          "aurora_pin_key", "aurora_pin_user_key", "aurora_pin_set", "aurora_pin_set_for",
          "aurora_pin_get", "aurora_pin_get_any", "_pin_save", "_pin_load", "_pin_log",
          "_draft_excerpt", "_draft_recipient_body", "_uid_to_name",
          "_strip_material_wrapper"]
WANT_A = ["_CARD_ASK_RE", "_AURORA_PIN", "_AURORA_PIN_TTL", "_AURORA_PIN_FILE",
          "_AURORA_PIN_LOG"]


class _OB:
    """outbox の身代わり。pending(uid) を返すだけ。"""
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
            if (t in WANT_A or t.startswith("_COMPLETION") or t == "_AURORA_WORD_RE"
                    or t in ("_MATERIAL_WRAPPER_RE", "_DECOR_META_RE", "_STRUCT_HEAD_RE")):
                picked.append(n); seen.add(t)
    missing = [w for w in (WANT_F + WANT_A) if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, os, json, time, datetime, threading", M)
    M["HERE"] = TMP
    M["casper_outbox"] = _OB
    M["PENDING_ACTIONS"] = {}
    M["_ROSTER_MAP"] = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

WHO = {"uid": "31", "sid": "s1"}
CARD = {"id": "abc123", "tool": "aurora_append", "uid": "31", "ts": "2026-08-27T15:21:41",
        "summary": "Aurora ノート修正", "args": {"doc_id": "d78e9ca6", "body": "本文" * 30}}

# ── ①② カードについての問い ─────────────────────────────────────────────
print("── ①② カードについての問い ──")
ASKS = ["承認ボタンがでてこない", "承認ボタンは？", "承認ボタン", "承認カード",
        "承認カードが表示されない", "でない", "ボタンが出ない", "カードはどこ"]
for q in ASKS:
    chk(f"① 『{q}』を問いと判ずる", bool(M["_CARD_ASK_RE"].search(q)))
chk("① 資料の修正指示は問いと判ぜぬ(過剰発動せぬ)",
    not M["_CARD_ASK_RE"].search("2. BOKAN 担当事項にUE＋コンソールを追加して"))
chk("① 普通の会話は問いと判ぜぬ",
    not M["_CARD_ASK_RE"].search("GSの状況を教えてください"))

_OB.rows = [CARD]
pa = []
ans = M["card_ask_answer"](WHO, pa)
chk("② ★カードが在る時は実物を積む(実測15:17〜15:40の穴)", len(pa) == 1 and pa[0]["id"] == "abc123")
chk("② 件数を数で告げる", "1件" in ans)
chk("② 中身の抜粋を見せる(『在る』と言うだけで済まさぬ)", "Aurora修正" in ans or "本文" in ans)
chk("② 押せと導く", "ボタン" in ans)

_OB.rows = []
pa2 = []
ans2 = M["card_ask_answer"](WHO, pa2)
chk("② 無い時は0件と正直に名乗る", "0件" in ans2 and not pa2)
chk("② ★無い時に『もう一度お申し付けを』の壊れたループへ戻さぬ",
    "もう一度お申し付けを" not in ans2)
chk("② 無い時は次の一手を具体に示す", "どの節" in ans2 or "追加" in ans2)
chk("② 他人のカードは見せぬ", M["card_ask_answer"]({"uid": "99"}, []).find("0件") >= 0)
# ★uid 空で起票された孤児カードが実在する(実測4枚)。無記名の閲覧者へ台帳を開けば
#   他人の下書きを他人に見せることになる。
_OB.rows = [CARD, dict(CARD, id="orphan", uid="")]
_pa3 = []
_ans3 = M["card_ask_answer"]({"uid": "", "sid": "x"}, _pa3)
chk("② ★誰か判らぬ相手には台帳を開かぬ(孤児カードを無記名に見せぬ)",
    not _pa3 and "承認待ちが" not in _ans3)
chk("② その時も黙らず、理由を告げる", "判じられませなんだ" in _ans3)
_OB.rows = []

# ── ③ 注記は状態の申告 ───────────────────────────────────────────────────
print("── ③ 注記 ──")
LIE = "Auroraに保存しました。"
_OB.rows = []
note0 = M["_guard_completion_claims"](LIE, [], uid="31")
chk("③ カード0件なら従来どおり打ち消す", "保存しました" not in note0)
_OB.rows = [CARD]
note1 = M["_guard_completion_claims"](LIE, [], uid="31")
chk("③ ★承認待ちが在る時は件数を申告する(嘘をつかぬ)", "1件" in note1)
chk("③ その時は『もう一度お申し付けを』と言わぬ(既に在るのだから)",
    "もう一度お申し付けを" not in note1)
chk("③ 実物の在り処を指す(語でなく画面の物)", "承認待ち" in note1)
_OB.rows = []
chk("③ uid が無い時も落ちぬ", isinstance(M["_guard_completion_claims"](LIE, []), str))

# ── ④ 常設トレイ(画面) ───────────────────────────────────────────────────
print("── ④ 画面(chat.html) ──")
HTML = open(os.path.join(HERE, "chat.html"), encoding="utf-8").read()
chk("④ トレイが在る", "pendingTray" in HTML)
chk("④ ロード時に台帳を引く", "'/api/pending'" in HTML)
chk("④ 押すと実物のカードを描く", "cache.forEach" in HTML and "renderConfirm(b,pa)" in HTML)
chk("④ 承認/却下の後にトレイを更新する(押した物が残らぬ)",
    HTML.count("refreshPendingTray") >= 3)
chk("④ サーバに /api/pending の口が在る", 'self.path == "/api/pending"' in SRC_TEXT)
_ep = SRC_TEXT[SRC_TEXT.index('self.path == "/api/pending"'):][:1400]
chk("④ 本人のものだけを返す", 'casper_outbox.pending(_w.get("uid"))' in _ep)
chk("④ 返した物は承認できる状態にする(PENDING_ACTIONSへ載せる)",
    'PENDING_ACTIONS[r["id"]]' in _ep)

# ── ⑤ 錨は再読込を跨ぐ・生き死にが刻まれる ───────────────────────────────
print("── ⑤ 錨の永続と観測 ──")
REF = {"ref": "slug", "found": True, "doc_id": "d78e9ca6-3ce5-4bc0-9ab9-2502ede67767",
       "title": "SORAFUNE 様 MTG 議事録"}
M["_AURORA_PIN"].clear()
M["aurora_pin_set"]("th:t1", REF, material="# 題\n\n## 1. 節\n本文")
chk("⑤ 錨が disk へ落ちる", os.path.exists(os.path.join(TMP, "casper_aurora_pin.json")))
chk("⑤ set が刻まれる", os.path.exists(os.path.join(TMP, "casper_aurora_pin.jsonl")))
_lines = [json.loads(x) for x in open(os.path.join(TMP, "casper_aurora_pin.jsonl"), encoding="utf-8")]
chk("⑤ 刻みに doc_id が載る", any(x.get("event") == "set" and x.get("doc_id") for x in _lines))

M2, _ = build(SRC_TEXT)          # ★別のプロセスに見立てる(=再読込)
M2["_AURORA_PIN"].clear()
n = M2["_pin_load"]()
chk("⑤ ★再読込を跨いで錨が戻る(自艦のデプロイが状態を拭う穴)", n >= 1)
chk("⑤ 戻った錨が引ける", (M2["aurora_pin_get"]("th:t1") or {}).get("doc_id") == REF["doc_id"])

M["_AURORA_PIN"]["th:t1"]["ts"] = time.time() - M["_AURORA_PIN_TTL"] - 10
chk("⑤ 期限切れは戻らぬ", M["aurora_pin_get"]("th:t1") is None)
_lines = [json.loads(x) for x in open(os.path.join(TMP, "casper_aurora_pin.jsonl"), encoding="utf-8")]
chk("⑤ expire も刻まれる(次に壊れた時に推し量りで終わらせぬ)",
    any(x.get("event") == "expire" for x in _lines))

# ── ⑥ 人ごとの控え ───────────────────────────────────────────────────────
print("── ⑥ 新スレッドを開いても見失わぬ ──")
M["_AURORA_PIN"].clear()
uk = M["aurora_pin_user_key"](WHO)
chk("⑥ 人ごとの鍵が立つ", uk == "u:31")
chk("⑥ uid が無ければ鍵は空(誰の物か判らぬ物を混ぜぬ)", M["aurora_pin_user_key"]({}) == "")
M["aurora_pin_set_for"]("th:old", uk, REF, material="# 題\n\n## 1. 節\n本文")
chk("⑥ ★新スレッド(別thread)でも人ごとの控えから引ける",
    (M["aurora_pin_get_any"]("th:BRAND_NEW", uk) or {}).get("doc_id") == REF["doc_id"])
chk("⑥ 元のスレッドでも引ける",
    (M["aurora_pin_get_any"]("th:old", uk) or {}).get("doc_id") == REF["doc_id"])
chk("⑥ 別人の控えは引かぬ", M["aurora_pin_get_any"]("th:BRAND_NEW", "u:99") is None)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
# ★規則の**一枝だけ**を差し替えると、別の枝が拾って変異が効かぬのに緑になる
#   (実測でこれを踏んだ——「承認ボタンがでてこない」は『でてこない』の枝でも拾われる)。
#   規則ごと空にする。
# ★入れ子の括弧を数える正規表現で切ろうとして失敗した(実測)。
#   定義の開始と終端を素直に索き、その区間を丸ごと差し替える。
_i = SRC_TEXT.index("_CARD_ASK_RE = re.compile(")
_j = SRC_TEXT.index(", re.I)", _i) + len(", re.I)")
mut = SRC_TEXT[:_i] + '_CARD_ASK_RE = re.compile(r"絶対に現れぬ語XYZZY")' + SRC_TEXT[_j:]
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut)
chk("★変異(問いの検知を殺す): 問いが一つも拾われなくなる(赤化実証)",
    not any(M3["_CARD_ASK_RE"].search(q) for q in ASKS))

mut2 = SRC_TEXT.replace("            if _n:\n                _tail =", "            if False:\n                _tail =", 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M4, _ = build(mut2)
_OB.rows = [CARD]
chk("★変異(注記の台帳照会を殺す): 承認待ちが在るのに『もう一度お申し付けを』と再誘導する(赤化実証)",
    "もう一度お申し付けを" in M4["_guard_completion_claims"](LIE, [], uid="31"))

mut3 = SRC_TEXT.replace("    return aurora_pin_get(user_key) if user_key else None",
                        "    return None", 1)
assert mut3 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M5, _ = build(mut3)
M5["_AURORA_PIN"].clear()
M5["aurora_pin_set_for"]("th:old", uk, REF, material="# 題\n\n## 1. 節\n本文")
chk("★変異(人ごとの控えを殺す): 新スレッドで資料を見失う(赤化実証)",
    M5["aurora_pin_get_any"]("th:BRAND_NEW", uk) is None)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
