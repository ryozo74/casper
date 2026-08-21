#!/usr/bin/env python3
"""cmd_499 裸の列挙選択(装置なしで①②を並べて選ばせる形)の回帰ゲート(純機構・インメモリ・書込ゼロ)。
全PASSで exit 0。

守る掟(4系統・正常系のみの緑は不可):
 ① AC1: kiyotomo殿の実際の応答文(①②形式)を出口検問に通し、捉えられること
    (問いかけ行+列挙行削除→事実申告保持→次の一手の文言添付)。
 ② AC2: 過剰検出の退行検査。正当な箇条書き(候補提示・手順一覧)が消えぬこと。
 ③ AC3: 番号返答が行き止まりにならぬこと(_LAST_ENUM突合/鮮度切れ/別人/範囲外)。
 ④ AC4: 既存のsay型選択カード(choices/pending_actions随伴)は素通しされること(退行検査)。

chat_server.py を import すると server が起動してしまうゆえ、gate_context_handoff.py と同じ手法
(ast で当該機構のみを抜いて exec)で検査する(名前が変わった/消えたらゲートが落ちる=機構の在処も守る)。
"""
import ast
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

WANT = ["_NAKED_CHOICE_RE", "_ENUM_LINE_RE", "_ASK_RE", "_THIN_OPTION_RE",
        "_bare_enum_choice", "_validate_choices",
        "_NUM_ONLY_RE", "_ENUM_FRESH_SEC", "_CIRCLED_NUM", "_FULLWIDTH_DIGITS",
        "_enum_num_to_index", "_resolve_number_reply", "_LAST_ENUM",
        "_bg", "_enum_line_is_quoted", "_QUOTE_NGRAM_MIN_OVERLAP"]

_orig_src = open(SRC, encoding="utf-8").read()
tree = ast.parse(_orig_src)
picked, seen = [], set()
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef,)) else
             [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
    for nm in names:
        if nm in WANT:
            picked.append(node)
            seen.add(nm)
missing = [w for w in WANT if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

M = {}
exec("import re, time", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)

_bare_enum_choice = M["_bare_enum_choice"]
_validate_choices = M["_validate_choices"]
_resolve_number_reply = M["_resolve_number_reply"]
_LAST_ENUM = M["_LAST_ENUM"]
_ENUM_FRESH_SEC = M["_ENUM_FRESH_SEC"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


def reset():
    _LAST_ENUM.clear()


WHO = {"uid": "u1"}
WHO_OTHER = {"uid": "u2"}
THR = "gate_thr"

# ══════════════════════════════════════════════════════════════════════════
# ① AC1: kiyotomo殿の実害の再現(実failure文をそのまま試験データに)
# ══════════════════════════════════════════════════════════════════════════
KIYOTOMO_FAIL = (
    "資料の具体的な中身が現在取得できておりません。Vault検索で該当ファイルの"
    "内容を抽出し、要約・簡略化いたしますか？\n"
    "① はい、内容を確認してわかりやすくまとめてほしい\n"
    "② いいえ、別の用件がある"
)

chk_true("① AC1 _bare_enum_choiceが実害文を捉える(構造三条件成立)",
         _bare_enum_choice(KIYOTOMO_FAIL) is True)

out = _validate_choices(KIYOTOMO_FAIL, pending_actions=None, choices=None)
chk_true("① AC1 事実申告(資料の具体的な中身が...)は保持される", "資料の具体的な中身が現在取得できておりません" in out)
# ★cmd_508病四是正: 導入文(問いかけ行)はもう削らぬ(削ると列挙が孤児化するF01事故の元)。
# 問いかけ行(いたしますか？)自体は事実申告と同一文中にあり保持される。
chk_true("① AC1(cmd_508是正後) 問いかけ行は導入文の一部として保持される(削除しない)",
         "いたしますか" in out)
chk_true("① AC1 列挙行(①はい/②いいえ)は削除される", "① はい" not in out and "② いいえ" not in out)
# ★cmd_508病四是正: 非空の成功本文への失敗文appendを廃止(詫び文は空出口専用)。
# ここは本文が非空(事実申告が残る)ゆえ、旧来の「お出しできませなんだ」は付かぬのが正。
chk_true("① AC1(cmd_508是正後) 非空本文には詫び文をappendしない",
         "お出しできませなんだ" not in out)
print(f"   [AC1 応答全文]\n{out}\n")

# ══════════════════════════════════════════════════════════════════════════
# ② AC2: 過剰検出の退行検査(軍師が実ログから拾った実データ・合成データのみで済ませない)
# ══════════════════════════════════════════════════════════════════════════
CANDIDATE_LIST = (
    "類似する名前として以下のメンバーがいます。お探しの人物はこれらでしょうか？\n"
    "1. 黒丸クロマル(役割: Animator / 得意: Animation / ソフト: Maya…)\n"
    "2. 新井アライ…"
)
chk_true("② AC2 候補提示(中身つき)は_bare_enum_choiceが捉えない(False)",
         _bare_enum_choice(CANDIDATE_LIST) is False)
out2 = _validate_choices(CANDIDATE_LIST, pending_actions=None, choices=None)
chk("② AC2 候補提示は素通し(改変なし)", out2, CANDIDATE_LIST)

TOPIC_LIST = (
    "どの項目について知りたいでしょうか？ 1. メンバーの得意分野 2. プロジェクトの状況…"
)
chk_true("② AC2 同一行の列挙(改行なし)は列挙の直後性条件を満たさず捉えない(False)",
         _bare_enum_choice(TOPIC_LIST) is False)
out2b = _validate_choices(TOPIC_LIST, pending_actions=None, choices=None)
chk("② AC2 同一行列挙も素通し", out2b, TOPIC_LIST)

PROCEDURE_LIST = (
    "作業手順は以下の通りです。\n"
    "1. Vaultから資料を取得する\n"
    "2. 要約を作成する\n"
    "3. 承認カードを提示する"
)
chk_true("② AC2 手順一覧(問いかけ行なし)は捉えない(False)", _bare_enum_choice(PROCEDURE_LIST) is False)
out2c = _validate_choices(PROCEDURE_LIST, pending_actions=None, choices=None)
chk("② AC2 手順一覧も素通し", out2c, PROCEDURE_LIST)

# ══════════════════════════════════════════════════════════════════════════
# ③ AC3: 番号返答が行き止まりにならぬこと
# ══════════════════════════════════════════════════════════════════════════
reset()
_LAST_ENUM[THR] = {"lines": ["はい、内容を確認してわかりやすくまとめてほしい", "いいえ、別の用件がある"],
                    "ts": time.time(), "uid": "u1"}
chk("③ AC3 番号一致(①)で該当行を返す", _resolve_number_reply("①", THR, WHO),
    "はい、内容を確認してわかりやすくまとめてほしい")
chk("③ AC3 番号一致(半角2)で該当行を返す", _resolve_number_reply("2", THR, WHO),
    "いいえ、別の用件がある")
chk("③ AC3 番号一致(全角２)で該当行を返す", _resolve_number_reply("２", THR, WHO),
    "いいえ、別の用件がある")

reset()
chk("③ AC3 _LAST_ENUMが無ければNone(通常処理へ)", _resolve_number_reply("1", THR, WHO), None)

reset()
_LAST_ENUM[THR] = {"lines": ["a", "b"], "ts": time.time() - _ENUM_FRESH_SEC - 60, "uid": "u1"}
chk("③ AC3 鮮度切れはNone", _resolve_number_reply("1", THR, WHO), None)

reset()
_LAST_ENUM[THR] = {"lines": ["a", "b"], "ts": time.time(), "uid": "u1"}
chk("③ AC3 別人はNone", _resolve_number_reply("1", THR, WHO_OTHER), None)

reset()
_LAST_ENUM[THR] = {"lines": ["a", "b"], "ts": time.time(), "uid": "u1"}
chk("③ AC3 範囲外番号はNone", _resolve_number_reply("9", THR, WHO), None)

reset()
_LAST_ENUM[THR] = {"lines": ["a", "b"], "ts": time.time(), "uid": "u1"}
chk("③ AC3 数字のみでない入力はNone(通常検索語として処理)", _resolve_number_reply("黒丸について", THR, WHO), None)

# ══════════════════════════════════════════════════════════════════════════
# ④ AC4: 既存のsay型選択カード(choices/pending_actions随伴)は壊れておらぬこと
# ══════════════════════════════════════════════════════════════════════════
out4 = _validate_choices(KIYOTOMO_FAIL, pending_actions=None, choices={"prompt": "x", "options": []})
chk("④ AC4 choices随伴なら素通し(検問をすり抜けさせない)", out4, KIYOTOMO_FAIL)
out4b = _validate_choices(KIYOTOMO_FAIL, pending_actions=[{"tool": "x"}], choices=None)
chk("④ AC4 pending_actions随伴なら素通し", out4b, KIYOTOMO_FAIL)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ AC6(cmd_508病四): 検索が発火せぬturnの応答にURLが押印されぬこと(casper_web側はgate_web_search.py
#    が別途検査)。ここでは chat_server 側の関連ACとして F01(引用列挙の孤児化防止)を検査する。
# ══════════════════════════════════════════════════════════════════════════
# F01: vault引用資料内の列挙(a)(b)(c)を「Casperが裸の選択を迫った」と誤認せぬこと。
# 注入素材(injected)に列挙本文と同一文言が在れば引用とみなし、列挙数から除外→3条件目
# (問いかけ+列挙2件以上)が不成立になり検問対象から外れる。導入文も列挙も孤児化せぬ。
F01_INJECTED = (
    "## 該当資料(右脳vault)\n"
    "選定基準はよろしいでしょうか？\n"
    "a) はい、この基準で進める\n"
    "b) いいえ、基準を見直したい\n"
)
F01_ANSWER = (
    "資料によれば選定基準はよろしいでしょうか？\n"
    "a) はい、この基準で進める\n"
    "b) いいえ、基準を見直したい\n"
)
chk_true("⑤ AC6(F01) 引用列挙はinjected注入時_bare_enum_choiceが捉えない(False)",
         _bare_enum_choice(F01_ANSWER, injected=F01_INJECTED) is False)
out5 = _validate_choices(F01_ANSWER, pending_actions=None, choices=None, injected=F01_INJECTED)
chk("⑤ AC6(F01) 引用列挙は導入文・列挙とも孤児化せず素通し(改変なし)", out5, F01_ANSWER)
# 対照: injected を渡さねば(旧来通り)裸の選択として捉えられる退行が無いことを確認
# (thin-option条件も満たす文面ゆえ、injected無しでは正しくTrueに倒れることを確かめる)
chk_true("⑤ AC6(F01対照) injected無しなら同文面は裸選択として捉えられる(検問機構自体は生きている)",
         _bare_enum_choice(F01_ANSWER, injected="") is True)

# 空出口(kept後に本文が空)は詫び文が付くこと(cmd_508是正: 詫び文は空出口専用)
EMPTY_AFTER_STRIP = "選択してください。\n① a\n② b"
out6 = _validate_choices(EMPTY_AFTER_STRIP, pending_actions=None, choices=None)
chk_true("⑤ AC6 kept後に空なら『ございませぬ』文言が付く(空出口専用の詫び文)",
         "お選びいただける項目が今はございませぬ" in out6)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異検証(3種): scratchpadのコピー上で(_orig_srcの文字列置換)行い本番md5不変を保つ。
# 対照実験(変異なしで直近チェックが緑)は上記①②③で既に確認済み。
# ══════════════════════════════════════════════════════════════════════════

def _run_mutant(label, patched_src, fn_names, check_fn):
    applied = patched_src != _orig_src
    if not applied:
        chk(f"★{label} 突然変異パッチが対象コードに一致せず適用不能(要目視確認)", False, True)
        return
    mtree = ast.parse(patched_src)
    mpicked = [n for n in mtree.body if getattr(n, "name", None) in fn_names
               or (hasattr(n, "targets") and any(getattr(t, "id", None) in fn_names for t in getattr(n, "targets", [])))]
    MM = dict(M)
    exec(compile(ast.Module(body=mpicked, type_ignores=[]), SRC, "exec"), MM)
    check_fn(MM)


# (i) _THIN_OPTION_REの条件を外す → 候補提示まで消える(過剰検出)ことを検知
_mut1_src = _orig_src.replace(
    "        thin = sum(1 for b in enum_lines if _THIN_OPTION_RE.match(b.strip()))\n"
    "        if thin * 2 >= len(enum_lines):                  # 過半が中身の薄い選択肢\n"
    "            return True\n",
    "        return True  # MUTATED: thin-option guard disabled\n")


def _chk_mut1(MM):
    _fn = MM["_bare_enum_choice"]
    leaked = _fn(CANDIDATE_LIST)
    chk("★(i) 突然変異(_THIN_OPTION_RE無効化)で候補提示まで誤検出される(過剰検出を検知)",
        leaked, True)


_run_mutant("(i)", _mut1_src, {"_bare_enum_choice"}, _chk_mut1)

# (ii) _ASK_REの条件を外す → 手順一覧まで消えることを検知
_mut2_src = _orig_src.replace(
    "    lines = text.split(\"\\n\")\n"
    "    for i, ln in enumerate(lines):\n"
    "        if not _ASK_RE.search(ln):\n"
    "            continue\n",
    "    lines = text.split(\"\\n\")\n"
    "    for i, ln in enumerate(lines):\n"
    "        pass  # MUTATED: ask-line guard disabled\n")


THIN_LIST_NO_QUESTION = (
    "承知いたした。以下の対応を進め申す。\n"
    "① はい、内容を確認してわかりやすくまとめる\n"
    "② いいえ、この件は保留する"
)


def _chk_mut2(MM):
    _fn = MM["_bare_enum_choice"]
    # 対照実験: 変異なし(本番の_bare_enum_choice)では問いかけ行が無いため捉えない
    control = _bare_enum_choice(THIN_LIST_NO_QUESTION)
    chk("★(ii)対照 変異なしでは問いかけ行が無い列挙は捉えない(False)", control, False)
    leaked = _fn(THIN_LIST_NO_QUESTION)
    chk("★(ii) 突然変異(_ASK_RE無効化)で問いかけ行の無い薄い列挙まで誤検出される(過剰検出を検知)",
        leaked, True)


_run_mutant("(ii)", _mut2_src, {"_bare_enum_choice"}, _chk_mut2)

# (iii) _LAST_ENUMの鮮度/uid判定を外す → 別人・古い列挙を誤って引き継ぐことを検知
_mut3_src = _orig_src.replace(
    "    if rec.get(\"uid\") != (who or {}).get(\"uid\"):          # 別人の列挙は引き継がない\n"
    "        return None\n"
    "    if time.time() - float(rec.get(\"ts\") or 0) > _ENUM_FRESH_SEC:   # 鮮度切れ\n"
    "        return None\n",
    "    pass  # MUTATED: uid/freshness guard disabled\n")


def _chk_mut3(MM):
    MM["_LAST_ENUM"]["mut_thr"] = {"lines": ["old-a", "old-b"], "ts": time.time() - _ENUM_FRESH_SEC - 999, "uid": "someone-else"}
    _fn = MM["_resolve_number_reply"]
    leaked = _fn("1", "mut_thr", WHO)
    chk("★(iii) 突然変異(uid/鮮度ガード無効化)で別人・古い列挙を誤って引き継ぐ(検知)",
        leaked, "old-a")


_run_mutant("(iii)", _mut3_src, {"_resolve_number_reply"}, _chk_mut3)

n = len(results); p = sum(results)
print(f"\n=== gate_choice_enum: {p}/{n} = {p*100//n if n else 0}% ===")
sys.exit(0 if p == n else 1)
