#!/usr/bin/env python3
"""書きかけ遺物の掃除機の回帰ゲート(殿御裁可2026-08-24)。全PASSで exit 0。

守る掟:
 ① 死んだPIDの遺物は掃く(これが目的)。
 ② ★生きたPIDの物には絶対に触らぬ——書込中かもしれぬ。ここを誤ると
   「掃除機が現に書いている索引を食う」という、残骸放置より遥かに重い事故になる。
 ③ ★本体(索引そのもの)には決して手を出さぬ。掃除機が索引を食う事故を機構で不可能にする。
 ④ dry_run は数えるだけで消さぬ(消す前に見られる)。
 ⑤ 配線: _atomic_dump が書く前に掃除機を必ず呼ぶ(呼ばねば遺物はまた積もる)。
 ★突然変異: _pid_alive の検査を外すと②が赤化する(=生死の門が効いている証拠)。

本番の索引には一切触れぬ(すべて一時ディレクトリ上で行う)。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_embed  # noqa: E402

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


TMP = tempfile.mkdtemp(prefix="gate_tmp_sweeper_")
BASE = os.path.join(TMP, "index.json")

DEAD_PID = 999999          # 存在せぬPID(遺物の主は既に死んでいる)
LIVE_PID = os.getpid()     # 生きているPID(自分自身)


def _setup():
    for p in list(os.listdir(TMP)):
        os.remove(os.path.join(TMP, p))
    open(BASE, "w").write("本体(索引そのもの)")
    open(f"{BASE}.tmp.{DEAD_PID}", "w").write("死んだPIDの書きかけ" * 10)
    open(f"{BASE}.tmp.{LIVE_PID}", "w").write("生きたPIDが今まさに書いている途中")


# ── ①②③ 掃除の本体 ─────────────────────────────────────────────────────────
_setup()
r = casper_embed.sweep_stale_tmp(BASE)
chk("① 死んだPIDの遺物は掃かれる", [os.path.basename(x) for x in r["removed"]],
    [f"index.json.tmp.{DEAD_PID}"])
chk_true("① 実際にファイルが消えている", not os.path.exists(f"{BASE}.tmp.{DEAD_PID}"))
chk("② ★生きたPIDの物は残す(書込中かもしれぬ)", [os.path.basename(x) for x in r["kept"]],
    [f"index.json.tmp.{LIVE_PID}"])
chk_true("② 実際にファイルが残っている", os.path.exists(f"{BASE}.tmp.{LIVE_PID}"))
chk_true("③ ★本体(索引そのもの)は無傷", os.path.exists(BASE) and open(BASE).read() == "本体(索引そのもの)")
chk_true("① 回収した量を申告する", r["bytes"] > 0)

# ── ④ dry_run は消さぬ ──────────────────────────────────────────────────────
_setup()
r2 = casper_embed.sweep_stale_tmp(BASE, dry_run=True)
chk("④ dry_run: 消す予定として数える", len(r2["removed"]), 1)
chk_true("④ dry_run: 実際には消えていない", os.path.exists(f"{BASE}.tmp.{DEAD_PID}"))

# ── ⑤ 配線: _atomic_dump が書く前に掃除機を呼ぶ ──────────────────────────────
_setup()
_calls = {"n": 0}
_orig_sweep = casper_embed.sweep_stale_tmp


def _counting_sweep(path=None, dry_run=False):
    _calls["n"] += 1
    return _orig_sweep(path, dry_run)


casper_embed.sweep_stale_tmp = _counting_sweep
try:
    casper_embed._atomic_dump({"a": 1}, BASE)
    chk("⑤ 配線: _atomic_dump は書く前に掃除機を呼ぶ", _calls["n"], 1)
    chk_true("⑤ 配線: 書込自体は成功している(掃除が本業を壊さぬ)",
             os.path.exists(BASE) and "a" in open(BASE, encoding="utf-8").read())
    chk_true("⑤ 配線: そのついでに死んだPIDの遺物も消えている",
             not os.path.exists(f"{BASE}.tmp.{DEAD_PID}"))
finally:
    casper_embed.sweep_stale_tmp = _orig_sweep

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異: 生死の検査を外すと、生きたPIDの書きかけまで消える(=②が赤化)
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証(生死の門を殺す) ---")
_setup()
_orig_alive = casper_embed._pid_alive
casper_embed._pid_alive = lambda pid: False          # 全部「死んでいる」と見なす変異
try:
    r3 = casper_embed.sweep_stale_tmp(BASE)
    chk_true("★変異(生死の門を殺す): 生きたPIDの書きかけまで消える(赤化実証)",
             not os.path.exists(f"{BASE}.tmp.{LIVE_PID}"))
finally:
    casper_embed._pid_alive = _orig_alive
_setup()
r4 = casper_embed.sweep_stale_tmp(BASE)
chk_true("★復元確認: 門を戻せば生きたPIDの物は再び守られる",
         os.path.exists(f"{BASE}.tmp.{LIVE_PID}"))

for p in list(os.listdir(TMP)):
    os.remove(os.path.join(TMP, p))
os.rmdir(TMP)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
