#!/usr/bin/env python3
"""語彙検問(pack_lint.py)の回帰ゲート(cmd_504)。全PASSで exit 0。

pack_lintという道具は在るのに誰も走らせておらぬゆえ、三度(cmd_489→495→504)崩れた。
「崩した者がその場で気づく」ため、gate群(足軽がcmdの終わりに必ず全件走らせる)に
本ゲートを加える(軍師設計・本命)。

守る掟:
 ① pack_lint.py(bokan)がexit=0(engineにpack固有語ゼロ)であること。
 ② pack_lint.py --pack toystudio でもexit=0であること(cmd_489の証明の維持=
    bokanを直してもtoystudioが壊れぬこと)。
 ③ 語彙を1件わざと書き込めば pack_lint.py が exit!=0 で捉えること(=検問が本当に
    機能している証拠。骨抜き実装や、pack.yamlを読まずいつも0を返す実装を防ぐ)。

★絶対パスで呼ぶ(cwdの違いで走らぬ恐れがある・軍師の教訓)。
③はscratchpad上の一時コピーに対してのみ行い、本番ファイルには一切触れない。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK_LINT = os.path.join(HERE, "pack_lint.py")

sys.path.insert(0, HERE)
import pack_config as _pc
# 本ゲート自身がpack語彙をengineに直書きすればpack_lintに引っ掛かる(自己言及の罠)。
# 注入語はpack.yaml(examples.project_names)から取る(=本ゲートも語の文字を持たない)。
_PJ = (_pc.get("examples", {}).get("project_names") or ["<PJ名>"])[0]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def _run(pack):
    """pack_lint.pyを絶対パス+絶対cwdで実行しreturncodeを返す(cwd差異で走らぬ事故を防ぐ)。"""
    r = subprocess.run([sys.executable, PACK_LINT, "--pack", pack],
                        cwd=HERE, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── ① bokan: engineにpack語彙ゼロ ──────────────────────────────────
rc1, out1 = _run("bokan")
chk("① pack_lint.py(bokan) が exit=0(engineにpack語彙ゼロ)", rc1, 0)
if rc1 != 0:
    print("  " + out1.replace("\n", "\n  "))

# ── ② toystudio: bokanを直してもtoystudioが壊れぬこと(cmd_489の証明の維持・AC2) ──
rc2, out2 = _run("toystudio")
chk("② pack_lint.py --pack toystudio が exit=0", rc2, 0)
if rc2 != 0:
    print("  " + out2.replace("\n", "\n  "))

# ── ③ 突然変異: 違反を1件わざと入れれば検問が赤化すること(AC3・実証) ────────
# 本番ファイルには一切触れない。pack_lint.scan()はHERE配下を再帰走査する実装ゆえ、
# HERE直下に一時ファイルを置いて注入する(=本物のスキャン経路を通す)。既存の本番.pyには
# 一切書き込まず、本ゲート終了時に必ず削除する(try/finallyで保証)。
_scratch = os.path.join(HERE, "_gate_pack_lint_mutant_scratch.py")
try:
    with open(_scratch, "w", encoding="utf-8") as f:
        f.write("# gate_pack_lint.py AC3実証専用の一時ファイル(本ゲート終了時に自動削除)\n")
        f.write(f'VIOLATION = "{_PJ}の状況は？"\n')
    rc3, out3 = _run("bokan")
    chk("③ 違反を1件わざと入れると pack_lint.py が exit!=0 で捉える", rc3 != 0, True)
    chk("③' 検知内容に注入ファイル名が含まれる",
        os.path.basename(_scratch) in out3, True)
finally:
    if os.path.exists(_scratch):
        os.remove(_scratch)

# 後始末の確認: 一時ファイルを消した後は再びPASSへ戻ること(本番状態を汚さぬ証拠)
rc4, out4 = _run("bokan")
chk("③'' 一時ファイル削除後は pack_lint.py(bokan) が再び exit=0 へ戻る", rc4, 0)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
