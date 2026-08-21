#!/usr/bin/env python3
"""cmd_508 AC9: grounding_gate証拠要求について変異前確認→赤化→復元→md5一致の四点で示す。
casper_web.py 本体は変更せず、文字列置換したコピーをexecして突然変異を検証する(本番非破壊)。
"""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(HERE, "casper_web.py")


def md5_of(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


# ① 変異前確認(対照実験): 現行の grounding_gate は未使用URLを押印しない
md5_before = md5_of(SRC_PATH)
print(f"① 変異前 md5: {md5_before}")

sys.path.insert(0, HERE)
import casper_web as W

_SRC_TEXT = (
    "RTAB-MapはSLAMの一手法であり最新版はv0.21です https://example.com/a 。"
    "別件のニュースはこちら https://example.com/b を参照(本文には無関係)。"
)
_R = {"ok": True, "urls": ["https://example.com/a", "https://example.com/b"], "text": _SRC_TEXT}
_answer = "RTAB-MapはSLAMの一手法であり最新版はv0.21です。"

note_control = W.grounding_gate(_answer, _R)
control_ok = ("https://example.com/a" in note_control) and ("https://example.com/b" not in note_control)
print(f"① 対照実験(変異なし): 使用証拠のあるaのみ付き・未使用bは付かず = {control_ok}")
print(f"   得られた注記: {note_control!r}")

# ② 赤化: 証拠要求を無効化する変異(_char_ngrams等のn-gram一致チェックを恒常Trueに差し替え)
_orig_src = open(SRC_PATH, encoding="utf-8").read()
_mut_src = _orig_src.replace(
    "        win_grams = _char_ngrams(windows.get(u, \"\"))\n"
    "        if len(ans_grams & win_grams) >= _GATE_NGRAM_MIN_OVERLAP:\n"
    "            used.append(u)\n",
    "        used.append(u)  # MUTATED: evidence check disabled(証拠要求を無効化)\n"
)
mutation_applied = _mut_src != _orig_src
print(f"② 突然変異パッチが適用可能(対象コード一致): {mutation_applied}")
if not mutation_applied:
    print("❌ 突然変異パッチが対象コードに一致せず——要目視確認")
    sys.exit(1)

import ast
tree = ast.parse(_mut_src)
WANT = ["_WEB_MARK_RE", "_GATE_NGRAM_N", "_GATE_NGRAM_MIN_OVERLAP", "_GATE_URL_WINDOW_FALLBACK",
        "_char_ngrams", "_url_context_windows", "grounding_gate"]
picked = []
for node in tree.body:
    names = ([node.name] if isinstance(node, ast.FunctionDef) else
             [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
    for nm in names:
        if nm in WANT:
            picked.append(node)
M = {}
exec("import re", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC_PATH, "exec"), M)
mutant_gate = M["grounding_gate"]

note_mutant = mutant_gate(_answer, _R)
regressed = "https://example.com/b" in note_mutant   # 未使用URLが再度押印されるはず(赤化=バグが再現)
print(f"② 赤化確認: 未使用URL(b)が再度押印される = {regressed}")
print(f"   得られた注記(変異後): {note_mutant!r}")

# ③ 復元: 変異は _mut_src という別バッファ上でのみ行っており、本番ファイルは一切書き換えていない
md5_after = md5_of(SRC_PATH)
print(f"③ 復元後(実際は無変更) md5: {md5_after}")

# ④ md5一致
match = (md5_before == md5_after)
print(f"④ md5一致(本番非破壊の証明): {match}")

all_ok = control_ok and mutation_applied and regressed and match
print(f"\n=== AC9 4点構成 全て真: {all_ok} ===")
sys.exit(0 if all_ok else 1)
