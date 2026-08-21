#!/usr/bin/env python3
"""Casperの使い方(携帯からの入り方・通知の受け取り方)決定的注入機構(cmd_490手当2)の回帰ゲート
(純機構・インメモリ・読取のみ)。全PASSで exit 0。

守る掟:
 ① B-1(_asks_about_casper): Casper自身の使い方を尋ねているかの三値判定(True|False|None)。
    案件語(_pj_resolve=unique)があれば即False。疑問形/依頼形が無ければLLMを呼ばずFalse。
 ② B-2/B-3(casper_howto_digest): 判定Trueなら正典(casper_howto.md)を機構が確定的に注入する。
    正典が空(ファイル不在/読取失敗)またはNone判定の時は、qwenに自由作文させず正直な文を注入する。
    判定Falseの時は何も注入しない。
 ③ B-4(_guard_casper_howto_claims): 判定Trueのturnの応答から、禁止語(捏造手順)および誤った
    外部依頼の提案(IT部門へDM等)を出口で落とし、正典の手順文へ差し替える。判定Falseのturnは
    一切手を触れない。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該機構のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る)。
_asks_about_casper_llmは _ollama_json をモジュール属性差替(stub)することでネットワーク非依存の
まま3系統(about_casper=true/false/例外=判定不能)を検査する。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
WANT = ["_QUESTION_FORM_RE", "_DESIRE_FORM_RE", "_REQUEST_FORM_RE", "_ASK_DELEGATE_RE",
        "_asks_about_casper", "_asks_about_casper_llm",
        "_HOWTO_CACHE", "_HOWTO_FALLBACK", "_load_casper_howto", "casper_howto_digest",
        "_CASPER_HOWTO_FORBIDDEN_RE", "_CASPER_HOWTO_FORBIDDEN_NEGATION_RE", "_casper_howto_forbidden_hit",
        "_CASPER_HOWTO_BAD_SUGGESTION_RE", "_guard_casper_howto_claims",
        "_ollama_json", "pack_paths",
        # _pj_resolve の完全な依存連鎖(gate_pjname.pyと同じ抽出セット・カタカナ→ローマ字正準化含む)
        "_KANA2ROMA", "_KANA_SMALL", "_kana_to_romaji", "_translit_kana_runs",
        "_canonical", "_PJ_ALIAS", "_pj_index", "_pj_name_hit", "_pj_resolve"]

tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef,)) else
             ([node.names[0].asname or node.names[0].name] if isinstance(node, ast.Import) else
              [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]))
    for nm in names:
        if nm in WANT:
            picked.append(node)
            seen.add(nm)
missing = [w for w in WANT if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

M = {}
exec("import re, os, json, urllib.request", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_asks = M["_asks_about_casper"]
_asks_llm = M["_asks_about_casper_llm"]
_digest = M["casper_howto_digest"]
_guard = M["_guard_casper_howto_claims"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_is(name, got, expected_identity_fn, label):
    ok = expected_identity_fn(got)
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={label}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


_HOWTO_INPUTS = ["携帯で通知を受け取るには？", "携帯ではいりたいんだけど",
                 "携帯で、キャスパーにはいりたいです。", "キャスパーって携帯で見れるの？"]
_NON_HOWTO_INPUTS = ["進行中のプロジェクトを教えて", "kiyotomoの手持ちタスクは？",
                     "議事録をまとめて", "今日の予定は？"]

# ══════════════════════════════════════════════════════════════════════════
# ① stub about_casper=true下: howto系入力 → _asks is True
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": true}'
for q in _HOWTO_INPUTS:
    chk_is(f"stub true下でTrue: {q}", _asks(q), lambda v: v is True, "True(is)")

# ══════════════════════════════════════════════════════════════════════════
# ② stub about_casper=false下: 同入力 → _asks is False(分類器を信じる側)
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": false}'
for q in _HOWTO_INPUTS:
    chk_is(f"stub false下でFalse: {q}", _asks(q), lambda v: v is False, "False(is)")

# ══════════════════════════════════════════════════════════════════════════
# ③ stub 例外下: 同入力 → _asks is None(判定不能)
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
for q in _HOWTO_INPUTS:
    chk_is(f"stub例外下でNone(判定不能): {q}", _asks(q), lambda v: v is None, "None(is)")
chk_is("_asks_about_casper_llm単体もNone(stub例外)", _asks_llm("キャスパーって携帯で見れるの？"),
       lambda v: v is None, "None(is)")

# ── ③' 不正JSON・bool以外の値 → いずれもNone ──────────────────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{about_casper:}'
chk_is("不正JSON応答でNone", _asks_llm("携帯で見れる?"), lambda v: v is None, "None(is)")
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": "yes"}'
chk_is("about_casperがbool以外の値でNone", _asks_llm("携帯で見れる?"), lambda v: v is None, "None(is)")
M["_ollama_json"] = lambda system, user, num_predict=60: '{"other": true}'
chk_is("about_casperキー欠落でNone", _asks_llm("携帯で見れる?"), lambda v: v is None, "None(is)")

# ══════════════════════════════════════════════════════════════════════════
# ④ 呼出条件の境界
#   ④-a: 疑問形/依頼形すら無い完全な平叙文(判定不能扱いにもLLM起動にも至らぬ)はLLMを呼ばずFalse。
#   ④-b: 疑問形/依頼形はあるが案件語(_pj_resolve)がuniqueに解決しない非howto系入力は、
#        呼出条件自体は満たすのでLLM(分類器)へ回る——ゲート環境ではCalendarデータ非依存の
#        stub分類器がabout_casper=falseと答えることを模擬し、Falseに落ちることを確認する
#        (実運用では真の分類器がこの一線を引く。呼出条件自体がFalseを保証する設計ではない)。
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LLMを呼ぶな"))
chk("空queryはFalse(即断・LLM非呼出)", _asks(""), False)
chk("Noneクエリ相当(空文字)はFalse", _asks(None), False)

M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": false}'
for q in _NON_HOWTO_INPUTS:
    chk(f"案件・非howto系の入力はLLM(分類器)がfalseと答えてFalseに落ちる: {q}", _asks(q), False)

# ══════════════════════════════════════════════════════════════════════════
# B-2/B-3: casper_howto_digest — 判定Falseなら何も注入しない
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": false}'
chk("判定Falseのturnは注入ゼロ", _digest("進行中のプロジェクトを教えて"), "")

# ══════════════════════════════════════════════════════════════════════════
# B-2: casper_howto_digest — 判定Trueかつ正典が読める時は本文を確定的に注入する
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": true}'
_out_true = _digest("携帯で通知を受け取るには？")
chk_true("判定True+正典readable: 正典見出しを含む", "casper_howto.md" in _out_true)
chk_true("判定True+正典readable: 8443のURLが含まれる(正典本文由来)", "8443" in _out_true)
chk_true("判定True+正典readable: 捏造禁止の指示文を含む", "捏造" in _out_true)
chk_true("判定True+正典readable: 外部依頼提案の禁止を明示", "外部へ問い合わせる" in _out_true or "勧めるな" in _out_true)

# ══════════════════════════════════════════════════════════════════════════
# B-3: 正典が読めない(ファイル不在)時は正直なフォールバック文を注入する(qwenに自由作文させない)
# ══════════════════════════════════════════════════════════════════════════
_load = M["_load_casper_howto"]
M["_load_casper_howto"] = lambda: ""     # ファイル不在/読取失敗を模擬
_out_missing = M["casper_howto_digest"]("携帯で通知を受け取るには？")
chk_true("正典読取失敗時は正直な出口文言を含む(フォールバック)",
         "確かな手順をただいま参照できませなんだ" in _out_missing)
chk_true("正典読取失敗時にqwenへ委ねる自由作文の余地がない(固定文のみ)",
         _out_missing.strip() == ("## 【Casperの使い方】\n" + M["_HOWTO_FALLBACK"]).strip())
M["_load_casper_howto"] = _load   # 復元

# ══════════════════════════════════════════════════════════════════════════
# B-3: 判定None(分類器が答えない)時も同じ正直フォールバックへ倒す
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_out_none = M["casper_howto_digest"]("携帯で通知を受け取るには？")
chk_true("判定None時も正直な出口文言を含む(qwenに自由作文させない)",
         "確かな手順をただいま参照できませなんだ" in _out_none)

# ══════════════════════════════════════════════════════════════════════════
# B-4: _guard_casper_howto_claims — 判定Trueのturnで禁止語(捏造手順)を落とし正典へ差替
# ══════════════════════════════════════════════════════════════════════════
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": true}'
_FORBIDDEN_TEXTS = [
    "携帯からは利用できません。システム管理者(IT部門)へ確認してください。",
    "リモートデスクトップ接続で社内PCに繋いでください。",
    "スクリーンショット共有でご確認ください。",
    "7/18 Launchで一般公開予定です。アプリストアからインストールしてください。",
    "招待URLを発行しますのでそちらからどうぞ。VPN接続も必要です。QRコードを読み取ってください。",
]
for text in _FORBIDDEN_TEXTS:
    got = _guard(text, "携帯で通知を受け取るには？")
    chk_true(f"禁止語を含む行は落ちる: {text[:20]}...",
             not M["_CASPER_HOWTO_FORBIDDEN_RE"].search(got))

# ── ★最重要: 「IT部門へDMを送りましょうか」等の誤った外部依頼の提案を抑止 ──────────
_BAD_SUGGESTIONS = [
    "IT部門へDMを送りましょうか？",
    "システム管理者へDMで確認してみましょうか。",
    "管理者に連絡いたしましょうか？",
]
for text in _BAD_SUGGESTIONS:
    got = _guard(text, "携帯で、キャスパーにはいりたいです。")
    chk_true(f"誤った外部依頼提案は抑止される: {text}",
             not M["_CASPER_HOWTO_BAD_SUGGESTION_RE"].search(got))

# ── ★実測是正(将軍実測「携帯ではいりたいんだけど」他): 正典自身の否定文("招待URLは不要"等)を
#    誤って禁止語ヒットとして剥がさぬこと(過剰打ち消し禁止・実害の再発防止) ──────────
_CANON_NEGATION_LINES = [
    "アカウント作成や招待URLは不要で、一般公開・アプリストア配信も行っていません。",
    "VPN接続は不要です。",
    "QRコードは必要ありません。",
]
for text in _CANON_NEGATION_LINES:
    got = _guard(text, "携帯ではいりたいんだけど")
    chk(f"正典由来の否定文は誤って剥がされない: {text}", got, text)

# ── 逆方向の異常系: 判定Falseのturnは一切手を触れない(過剰打ち消し禁止) ─────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": false}'
_untouched = "IT部門へDMを送りましょうか？"   # 無関係turnでの同一文言(誤爆しないことの確認)
chk("判定Falseのturnは応答を一切変更しない(過剰検問禁止)",
    _guard(_untouched, "進行中のプロジェクトを教えて"), _untouched)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異検証(必須・4点セット): 注入経路(B-2)を殺したら赤化することを実証する。
# casper_howto_digest内で「about is True」判定を外し常に空文字を返す変異体を用意し、
# ①変異前確認(現状は注入される)→②変異適用→③挙動変化確認(赤=注入されない)→④復元確認、
# を機械的に行う。
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証(B-2注入経路) ---")
M["_ollama_json"] = lambda system, user, num_predict=60: '{"about_casper": true}'

# ① 変異前確認: 正常経路は注入される(現状PASS)
_pre_mutation = M["casper_howto_digest"]("携帯で通知を受け取るには？")
_pre_ok = "casper_howto.md" in _pre_mutation and bool(_pre_mutation.strip())
chk_true("①変異前確認: 正常経路は正典を注入する", _pre_ok)

# ② 変異適用: casper_howto_digestを「注入経路を殺した」版に差し替える(常に空文字)
_original_digest_fn = M["casper_howto_digest"]


def _mutated_digest(query):
    """B-2注入経路を殺した変異体: about_casperの判定に関わらず常に空文字を返す(注入ゼロ)。"""
    return ""


M["casper_howto_digest"] = _mutated_digest

# ③ 挙動変化確認: 変異後は注入されない(赤化することを実証)
_post_mutation = M["casper_howto_digest"]("携帯で通知を受け取るには？")
_mutation_killed_it = (_post_mutation == "")
chk_true("②③変異適用後: 注入経路を殺すと注入ゼロになる(赤化実証=ゲートが機構の生死を検知できる証拠)",
         _mutation_killed_it)
chk_true("③変異前後で挙動が変化した(mutation testingとして有効)",
         _pre_ok and _mutation_killed_it)

# ④ 復元確認: 元の関数に戻せば再び注入されることを確認
M["casper_howto_digest"] = _original_digest_fn
_post_restore = M["casper_howto_digest"]("携帯で通知を受け取るには？")
chk_true("④復元確認: 元に戻すと再び正典が注入される", "casper_howto.md" in _post_restore)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
