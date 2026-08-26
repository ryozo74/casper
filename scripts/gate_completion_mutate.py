#!/usr/bin/env python3
"""「書き換えた」という嘘を止める回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

実害(2026-08-26 18:24:57): kiyotomo殿が Aurora 資料から一行消すよう頼んだ。Casperは
18:22:55 に「Casperは社内システムの編集機能を持っていません」と**正直に**答えていながら、
二分後の 18:24:57 に「指定された記述を削除しました。」と断じた。カードは無く、何も起きていない。
真因: 出口検問の語彙表に 保存/送信/作成 は在り、**削除/編集/更新/修正/追記が一つも無かった**。
同じ機構が二分で揺れたのは、片方が表に載り片方が載っていなかっただけである。

守る掟:
 ① 『資料/議事録/ノート』を『削除/編集/更新/追記』したという主張は、カードが無ければ打ち消す。
    ★チャットの中では原理的に成し得ぬ行為ゆえ、在庫の言い訳が立たぬ(汎用動詞との違い)。
 ② 過剰に打ち消さぬ。文書語を伴わぬ整形(「重複行を削除しました」)は正当な発話ゆえ通す。
 ③ 打ち消しても**役に立つ本文は残す**。嘘の一行だけを抜き、正直な注記を添える。
 ④ 雲(claude_cli)経路にも同じ検問が通っている。
    ★この分岐には道具が一つも無い(pending_actions/casper_outbox 未結線)ゆえ、
      雲に座る間の完了主張は例外なく嘘である。にも関わらず検問だけが抜けていた。
 ★突然変異: 書き換え動詞を語彙表から抜くと①が、雲経路の検問呼出を抜くと④が赤化する。
"""
import ast
import copy
import os
import sys

HERE_G = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE_G)
SRC = os.path.join(HERE_G, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["_completion_claim_line_hit", "_guard_completion_claims"]
WANT_A = ["_COMPLETION_VERB_COMM_RE", "_COMPLETION_VERB_AURORA_ONLY_RE", "_COMPLETION_VERB_GENERIC_RE",
          "_COMPLETION_VERB_MUTATE_RE", "_COMPLETION_DOC_NOUN_RE", "_COMPLETION_MUTATE_RE",
          "_COMPLETION_GAP_RE", "_COMPLETION_TAIL_RE", "_COMPLETION_READ_EXCL_RE",
          "_COMPLETION_UNDONE_EXCL_RE", "_COMPLETION_COMM_RE", "_COMPLETION_AURORA_ONLY_RE",
          "_COMPLETION_GENERIC_RE", "_AURORA_WORD_RE"]


def build(tree):
    picked, seen = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANT_F:
            picked.append(node); seen.add(node.name)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in WANT_A for t in node.targets):
            picked.append(node); seen.add(node.targets[0].id)
    missing = [w for w in (WANT_F + WANT_A) if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re", M)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


TREE = ast.parse(SRC_TEXT)
M, missing = build(TREE)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)


def hit(ln):
    return M["_completion_claim_line_hit"](ln)[0]


REAL = ("SORAFUNE様とのMTG議事録（2026年8月26日付）から、指定された"
        "「技術概要: ドローンをAI化。」という記述を削除しました。")

# ── ① 書き換え主張はカード無しで打ち消す ─────────────────────────────────
print("── ① 塞がねばならぬもの ──")
for name, ln in [
        ("★実害そのもの(18:24:57)", REAL),
        ("Aurora語つきの削除主張", "Auroraの議事録から該当箇所を削除しました。"),
        ("編集", "Auroraの資料を編集しました。"),
        ("更新", "議事録を更新しました。"),
        ("修正", "ノートを修正しました。"),
        ("追記", "Auroraの資料に追記しました。"),
        ("差し替え", "資料を差し替えました。"),
        ("上書き", "ドキュメントを上書きしました。"),
        ("復元", "資料を復元しました。"),
        ("(対照)保存=従前から塞がれている", "Auroraに保存しました。")]:
    chk_true("① " + name, hit(ln))

# ── ② 過剰に打ち消さぬ ───────────────────────────────────────────────────
print("── ② 通さねばならぬもの(過剰打消しの検査) ──")
for name, ln in [
        ("表の整形(文書語を伴わぬ)", "重複行を削除しました。以下が整理後の表です。"),
        ("誤字の指摘", "誤字を修正しました。正しくは「空間座標系」です。"),
        ("下書き告知(未実行を自ら明示)", "Auroraへ議事録の下書きを作成しました。承認カードを押すと保存されます。"),
        ("読取の完了", "Auroraの資料を読みました。"),
        ("ただの会話", "9月3週にテストフライトの予定です。")]:
    chk_true("② " + name, not hit(ln))

# ── ③ 嘘の一行だけ抜き、役に立つ本文は残す ────────────────────────────────
print("── ③ 打ち消し方 ──")
body = REAL + "\n\n【処理後の議事録】\n1. シナリオ: 火災時想定（防衛かは未定）"
out = M["_guard_completion_claims"](body, [])
chk_true("③ 嘘の一行は消える", "削除しました" not in out)
chk_true("③ 役に立つ本文は残る", "1. シナリオ: 火災時想定（防衛かは未定）" in out)
chk_true("③ 正直な注記が添う", "まだ実行しておりませぬ" in out)
chk("③ カードが在れば打ち消さぬ(裏付けのある主張は通す)",
    M["_guard_completion_claims"](body, [{"id": "x", "tool": "aurora_create"}]), body)

# ── ④ 雲経路にも検問が通っている ─────────────────────────────────────────
print("── ④ 雲(claude_cli)経路 ──")


def cloud_branch(tree):
    """/api/chat の `if BACKEND == "claude_cli":` 分岐(最長のもの)を返す。"""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                and t.left.id == "BACKEND"):
            continue
        if len(node.body) > (len(best.body) if best else 0):
            best = node
    return best


def calls_in(node):
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


br = cloud_branch(TREE)
chk_true("④ /api/chat の雲分岐が見つかる", br is not None)
_calls = calls_in(br) if br else set()
chk_true("④ 完了検問が通っている", "_guard_completion_claims" in _calls)
chk_true("④ 他の出口検問も従前どおり通っている",
         {"_validate_assets", "_guard_unrostered_person_claim"} <= _calls)
# ★この分岐に道具が無いこと自体を記録として固定する(将来結線したらこの行が落ちて気づける)
chk_true("④ 雲分岐に道具は無い=カードは必ず0件ゆえ完了主張は例外なく嘘",
         "casper_outbox" not in ast.dump(br) and "pending_actions" not in ast.dump(br))

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
mut = ast.parse(SRC_TEXT)
for node in mut.body:                                  # 書き換え動詞を語彙表から抜く
    if (isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", None) == "_COMPLETION_VERB_MUTATE_RE"):
        node.value = ast.Constant(value=r"(この語は決して現れぬ_MUTATION_KILLED)")
ast.fix_missing_locations(mut)
M2, _ = build(mut)
chk_true("★変異(書き換え動詞を抜く): 実害の嘘が素通りする(赤化実証)",
         not M2["_completion_claim_line_hit"](REAL)[0])
chk_true("★変異下でも保存主張は塞がれたまま(変異が他を壊していない)",
         M2["_completion_claim_line_hit"]("Auroraに保存しました。")[0])

mut2 = ast.parse(SRC_TEXT)                             # 雲経路の検問呼出を抜く
br2 = cloud_branch(mut2)


class _Drop(ast.NodeTransformer):
    def visit_Assign(self, n):
        v = n.value
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == "_guard_completion_claims"):
            return None
        return n


_Drop().visit(br2)
ast.fix_missing_locations(mut2)
chk_true("★変異(雲経路の検問を抜く): 検問が消えたことを検知できる(赤化実証)",
         "_guard_completion_claims" not in calls_in(cloud_branch(mut2)))
chk_true("★復元確認: 本物には依然通っている",
         "_guard_completion_claims" in calls_in(cloud_branch(ast.parse(SRC_TEXT))))

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
