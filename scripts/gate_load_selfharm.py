#!/usr/bin/env python3
"""多人数同時利用で機構が自傷せぬことの回帰ゲート(2026-08-24 予行で発覚)。全PASSで exit 0。

守る掟(実測に基づく):
 推論機は同時要求を【直列】に捌く(実測: 完了が0.9/1.7/2.6/3.4秒と階段状)。ゆえに
 5人が同時に話しかければ5人目のturnは60秒を超える(実測60.6秒)。この「行列待ち」を
 推論機の不健康と数えると、混んだ時ほどbreakerが赤へ傾き、テストの最中に退避が発火して
 声も答えも変わる——「遅いから壊れた」のではなく【遅さを故障と誤診して自ら壊しに行く】。

 ① breakerへ刻む latency は【推論機の自己申告(server_total)】であり、turnの壁時計ではない。
   壁時計には他人の順番待ちが含まれ、それは推論機の健康とは別物である。
 ② 推論機が一度も応えなかった時(申告なし)は、失敗そのものは必ず刻む
   ——「測れなかった」を「健康」と読み替えぬ。
 ③ 自陣の呼出が走行中(inflight)なら、probeのtimeoutは verdict=busy でありfailではない。
 ④ ★busyと数えてよいのは【生きたPID】の走行のみ。死んだPIDの遺物を走行と読めば、
   busyが本物の故障を永久に覆い隠す(実測: 遺物が11件残っていた)。
 ★突然変異: latencyを壁時計へ戻すと①が赤化する。
"""
import ast
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ── ①② breakerへ刻む latency の出所を静的に検める ──────────────────────────
src = open(SRC, encoding="utf-8").read()
tree = ast.parse(src)
rec_calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "record"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "casper_breaker"]
chk_true("記録箇所が存在する(検査対象があること自体の確認)", len(rec_calls) >= 1)

wall_clock_used = False
server_used = False
for c in rec_calls:
    for kw in c.keywords:
        if kw.arg != "latency_ms":
            continue
        d = ast.dump(kw.value)
        if "_t0" in d and "time" in d:
            wall_clock_used = True            # turnの壁時計をそのまま渡している
        if "_srv" in d:
            server_used = True                # 推論機の自己申告
chk("① ★turnの壁時計を latency に使っていない", wall_clock_used, False)
chk_true("① 推論機の自己申告(生成速度)を使っている", server_used)
chk_true("① ★尺度は答えの長さに依らぬ(100トークン当たりの時間)",
         "_evs / _tok * 100.0" in src)
chk_true("① tok/sの材料(eval_count)を台帳へ通している", "server_eval_count" in src)
chk_true("② 申告が無く[error]の時も失敗を刻む節が在る",
         "elif final.startswith(\"[error]\")" in src)

# ── ③④ probe の busy 三値 ───────────────────────────────────────────────────
fo = open(os.path.join(HERE, "casper_failover.py"), encoding="utf-8").read()
chk_true("③ probeに verdict=busy の出口が在る", '"verdict": "busy"' in fo)
chk_true("③ busyの時 breakerへ刻まずに返る(record前にreturn)",
         fo.index('"verdict": "busy"') < fo.index("present is True(自モデル在・自陣の走行も無い"))
chk_true("④ ★生きたPIDのみを走行と数える(os.killで確かめる)",
         "os.kill(int(x.get(\"pid\")), 0)" in fo)
chk_true("④ 数える前に遺物を畳む(inflight_gc)", "inflight_gc()" in fo)

# ── ④の実挙動: 死んだPIDの遺物は走行と数えぬ ────────────────────────────────
import casper_llm_client as llc  # noqa: E402
_before = len(llc.inflight_list() or [])
_h = llc.inflight_start("gate_load_selfharm", "m", "http://127.0.0.1:1", 10)
_mine = [x for x in (llc.inflight_list() or []) if x.get("caller") == "gate_load_selfharm"]
chk_true("④ 自分で立てた走行は台帳に載る(検査の前提)", len(_mine) == 1)


def _alive(x):
    try:
        os.kill(int(x.get("pid")), 0)
        return True
    except Exception:
        return False


chk_true("④ 自分の走行は【生きている】と判る", all(_alive(x) for x in _mine))
_fake = dict(_mine[0]); _fake["pid"] = 999999
chk_true("④ ★死んだPIDの遺物は【生きていない】と判る(busyの誤発火を防ぐ)", not _alive(_fake))
try:
    llc.inflight_end(_h)
except Exception:
    pass

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異: latency を壁時計へ戻すと①が赤化する
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証(壁時計へ戻す) ---")
_mut = src.replace("latency_ms=int(_srv * 1000)", "latency_ms=int((time.time() - _t0) * 1000)", 1)
_mtree = ast.parse(_mut)
_mut_wall = False
for c in [n for n in ast.walk(_mtree)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "record" and isinstance(n.func.value, ast.Name)
          and n.func.value.id == "casper_breaker"]:
    for kw in c.keywords:
        if kw.arg == "latency_ms" and "_t0" in ast.dump(kw.value) and "time" in ast.dump(kw.value):
            _mut_wall = True
chk("★変異(壁時計へ戻す): 他人の順番待ちを故障と数える形に戻る(赤化実証)", _mut_wall, True)
chk("★復元確認: 本番のコードは壁時計を使っていない", wall_clock_used, False)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
