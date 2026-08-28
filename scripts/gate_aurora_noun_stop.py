#!/usr/bin/env python3
r"""体言止めの保存指示を陰性確定させぬ回帰ゲート(殿御下命 2026-08-27)。全PASSで exit 0。

実害(2026-08-26 18:33): kiyotomo殿が「sorafune 様　MTG.rtf」を投じ、添え書き欄に
「Auroraにアップ」と書いた。届いた発話は `sorafune 様　MTG.rtf — 「Auroraにアップ」`。
依頼形の語尾が無いため step7「依頼形すら無い」に落ち、**rule_negative で陰性確定**。
「して」を一つ足すだけで immediate/True になる(実測で再現済み)。
規則はチャットの文に合わせて作られており、**投函の添え書きの体言止めに合っていなかった**。

守る掟:
 ① 体言止めの保存指示は**陰性確定させぬ**。灰色として分類器へ回す。
    ★True と即断もせぬ——「Auroraにアップ」は指示だが「資料はAuroraにアップ」は報告かもしれぬ。
      その見分けは語彙表でなく意味判定の仕事。fail-closed は起票せぬ側で保たれる。
 ② カタカナ語中一致を拾わぬ(バックアップ/セットアップ/フォローアップ/ブラッシュアップ)。
 ③ 長い文書の末尾がたまたま保存語で終わる『記述』を指示と読まぬ(長さで締める)。
    ★投函の添え書きは鉤括弧に入って届く(`file.rtf — 「Auroraにアップ」`)。行全体を測ると
      飾り(ファイル名+区切り)の分だけ閾値を緩めねばならず、38字の『記述』まで拾う隙ができる。
      鉤括弧が在るならその中身こそが人の書いた指示ゆえ、そこだけを測る。
 ④ 既存の陰性標識(読取/編集/既存物言及/受益完了)は従前どおり先に効く。順序を壊さぬ。
 ⑤ 既存の即断路(「アップして」等)は従前どおり immediate のまま(退行させぬ)。
 ★突然変異: 体言止めの入口を殺すと①が赤化する(実害の発話が rule_negative へ戻る)ことを実証する。

分類器(qwen)は呼ばない(stubで三値を差し替え、経路のみを検める)。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["_wants_aurora_save", "_aurora_noun_stop_request",
          "_aurora_read_verb_same_clause", "_aurora_edit_read_verb_same_clause",
          "_aurora_clause_delegate_form"]


def build(src_text, llm_verdict=True):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    assigns = set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
        if isinstance(n, ast.Assign):
            t = getattr(n.targets[0], "id", "")
            if t.startswith(("_AURORA", "_SAVE_HINT", "_BENEFIT", "_AU_LAST", "_ASK_", "_DELEG")):
                picked.append(n); assigns.add(t)
    missing = [w for w in WANT_F if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, os, json, time, datetime, threading", M)
    M["HERE"] = HERE
    M["_wants_aurora_save_llm"] = lambda q: llm_verdict      # 分類器は呼ばぬ(経路だけ検める)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)


def route(q, M=M):
    v = M["_wants_aurora_save"](q)
    return v, M["_AU_LAST_ROUTE"].get("route")


REAL = "sorafune 様　MTG.rtf — 「Auroraにアップ」"

# ── ① 体言止めを陰性確定させぬ ───────────────────────────────────────────
print("── ① 体言止め ──")
for name, q in [("★実害そのもの(18:33)", REAL),
                ("Auroraにアップ", "Auroraにアップ"),
                ("auroraに保存", "auroraに保存"),
                ("Auroraへ登録", "Auroraへ登録"),
                ("議事録.docx — 「Auroraに掲載」", "議事録.docx — 「Auroraに掲載」")]:
    v, r = route(q)
    chk(f"① 『{name}』が分類器へ回る(rule_negativeで確定させぬ)", r == "llm")

v, r = route(REAL)
chk("① 分類器がtrueと答えれば起票へ進む", v is True)
M2, _ = build(SRC_TEXT, llm_verdict=False)
chk("① 分類器がfalseと答えれば起票せぬ(即断していない証)",
    M2["_wants_aurora_save"](REAL) is False)
M3, _ = build(SRC_TEXT, llm_verdict=None)
chk("① 分類器が答えられねばNone透過(fail-closed・『答えぬ』と『陰性』を混ぜぬ)",
    M3["_wants_aurora_save"](REAL) is None)

# ── ② カタカナ語中一致を拾わぬ ───────────────────────────────────────────
print("── ② カタカナ複合語 ──")
for w in ["Auroraのバックアップ", "Auroraのセットアップ", "Auroraのフォローアップ",
          "Auroraの資料をブラッシュアップ"]:
    chk(f"② 『{w}』を体言止めの保存指示と読まぬ",
        M["_aurora_noun_stop_request"](w) is False)

# ── ③ 長い記述の末尾を指示と読まぬ ───────────────────────────────────────
print("── ③ 行長で締める ──")
long_line = "本日の作業結果および今後の対応方針についてまとめた資料はAuroraにアップ"   # 38字
chk("③ 長い記述行は指示と読まぬ(38字=旧閾値40では素通りしていた)",
    M["_aurora_noun_stop_request"](long_line) is False)
chk("③ 短い行なら拾う(投函の添え書きは短い)",
    M["_aurora_noun_stop_request"]("Auroraにアップ") is True)
chk("③ 見るのは末尾の行だけ(前の長文に引きずられぬ)",
    M["_aurora_noun_stop_request"]("長い議事録本文がここに続く…\n" * 5 + "Auroraにアップ") is True)
chk("③ 鉤括弧が在れば中身だけを測る(飾りの分だけ閾値を緩めぬ)",
    M["_aurora_noun_stop_request"]("とても長いファイル名_2026-08-26_v3_final.rtf — 「Auroraにアップ」") is True)
chk("③ 鉤括弧の中身が長い記述なら指示と読まぬ",
    M["_aurora_noun_stop_request"]("メモ — 「本日の作業結果および今後の対応方針をまとめた資料はAuroraにアップ」") is False)

# ── ④ 既存の陰性標識が先に効く ───────────────────────────────────────────
print("── ④ 陰性標識の順序 ──")
for name, q in [("読取(見せて)", "Auroraの資料を見せて"),
                ("既存物言及", "その資料はAuroraに保存済み"),
                ("編集動詞", "Auroraの資料を校正")]:
    v, r = route(q)
    chk(f"④ 『{name}』は従前どおり陰性(体言止めが順序を壊さぬ)", v is False)

# ── ⑤ 既存の即断路が退行せぬ ─────────────────────────────────────────────
print("── ⑤ 即断路 ──")
for q in ["これをAUroraにアップして", "このままauroraにアップして", "Auroraに保存してください"]:
    v, r = route(q)
    chk(f"⑤ 『{q}』は従前どおり immediate/True", v is True and r == "immediate")
v, r = route("GSの状況は？")
chk("⑤ Aurora語の無いturnは従前どおり素通り(route=None)", v is False and r is None)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証(体言止めの入口を殺す) ---")
mut = SRC_TEXT.replace(
    'if _aurora_clause_delegate_form(q) or _aurora_noun_stop_request(q):',
    'if _aurora_clause_delegate_form(q):', 1)
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M4, _ = build(mut)
M4["_wants_aurora_save"](REAL)
chk("★変異: 実害の発話が rule_negative へ戻る(赤化実証)",
    M4["_AU_LAST_ROUTE"].get("route") == "rule_negative")
route(REAL)
chk("★復元確認: 本物では依然として分類器へ回る",
    M["_AU_LAST_ROUTE"].get("route") == "llm")

mut2 = SRC_TEXT.replace("_AURORA_NOUN_STOP_MAX = 24", "_AURORA_NOUN_STOP_MAX = 99999", 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M5, _ = build(mut2)
chk("★変異(長さの締めを殺す): 長い記述行まで指示と読む(過剰発動の赤化実証)",
    M5["_aurora_noun_stop_request"](long_line) is True)

mut3 = SRC_TEXT.replace("    cand = q[-1].strip() if q else last", "    cand = last", 1)
assert mut3 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M6, _ = build(mut3)
chk("★変異(鉤括弧の中身を見なくする): 飾りの長いファイル名で実害型が拾えなくなる(赤化実証)",
    M6["_aurora_noun_stop_request"](
        "とても長いファイル名_2026-08-26_v3_final.rtf — 「Auroraにアップ」") is False)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
