#!/usr/bin/env python3
r"""埋込機の生死を『人へ届く』ところまで通す回帰ゲート(2026-08-30)。全PASSで exit 0。

【病】センサー(casper_embed.embed_alive)は在ったが、結果を**記憶の中でしか**更新せず紙に
残さなんだ。落ちた刻も甦った刻も後から辿れぬ——**消費者の居らぬセンサー**(この一年で五度目の型)。

【もう一つの病(実測2026-08-30)】短probe(3秒)は冷間ロード(実測5.01秒)より短く、
**冷たいが健やかな宿を原理的に観測できぬ**。旧実装はこれを『断』と数え、
available() を偽にして意味検索を黙って切っていた。cmd_519(生成probeの三値化)と同じ病である。
  実測: 在庫True(0.03s) / 短probe×3すべてFalse(3.00s) / 直後の本番embed ok・1024次元(5.01s) /
        温まった後の短probe True(0.07s)。

守る掟:
 ① 三値でなく**四値**で名乗る: ok / cold(冷えていたが健やか) / down / unknown(訊けなんだ)。
 ② ★cold で吠えぬ(冷間は事故でなく常態——吠えれば狼少年)。
 ③ ★unknown を down と名乗らぬ。かつ直前の判定を保つ(掟: 失敗とゼロを別の出口へ)。
 ④ 対話の無い夜(窓が空)でも埋込の生死は検める(死に気づけるのは静かな夜である)。
 ⑤ ★合成の赤が**既存の届け先**(health→queue/casper_alerts.jsonl→alert_dispatch→家老)まで届く。
    新しい通知路は作らぬ(相乗り)。
 ⑥ 同じ赤で鳴り続けぬ(開始で一度)。復旧も一度知らせる。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_embed as E                                    # noqa: E402
import casper_health as H                                   # noqa: E402
import alert_dispatch as AD                                 # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


def _stub(short_ok, stock, long_ok=True):
    """短probe/在庫/確認probeを差し替える(実HTTPを叩かぬ)。"""
    calls = {"short": 0, "long": 0}

    def probe(timeout=None):
        if timeout and timeout > E._EMB_PROBE_TIMEOUT:
            calls["long"] += 1
            return long_ok
        calls["short"] += 1
        return short_ok
    E._probe = probe
    E.model_in_stock = lambda timeout=3: stock
    return calls


_orig_probe, _orig_stock = E._probe, E.model_in_stock
_orig_health = dict(E._EMB_HEALTH)


def _judge(short_ok, stock, long_ok=True, prev_ok=True):
    """観測路(embed_health_verdict)の四値を採る。要求路(embed_alive)は別に検める。"""
    E._EMB_HEALTH.clear()
    E._EMB_HEALTH.update({"ok": prev_ok, "ts": 0.0, "fails": 0})
    calls = _stub(short_ok, stock, long_ok)
    verdict, _reason = E.embed_health_verdict()
    return (verdict in ("ok", "cold")), verdict, calls


# ── ① 四値の名乗り ─────────────────────────────────────────────────────
print("── ① 何と名乗るか ──")
chk("① 短probeに即答→ok", _judge(True, True)[:2] == (True, "ok"))
_a, _v, _c = _judge(False, True, long_ok=True)
chk("① ★短probe不発でも在庫在り＋確認probe応答→cold(生存と数える)", (_a, _v) == (True, "cold"))
chk("① cold の時は確認probe(長い方)を実際に撃っておる", _c["long"] == 1)
chk("① 短probe不発＋在庫在り＋確認probeも不発→down", _judge(False, True, long_ok=False)[:2] == (False, "down"))
chk("① 在庫に無い→down", _judge(False, False)[:2] == (False, "down"))
_a2, _v2, _ = _judge(False, None, prev_ok=True)
chk("① ★在庫を訊けなんだ→unknown(断ぜぬ)", _v2 == "unknown")

# ── ★要求路と観測路を分けておること(人の番を止めぬ) ──────────────────────
print("── ★要求路は速さ、観測路は正直さ ──")
E._EMB_HEALTH.clear(); E._EMB_HEALTH.update({"ok": True, "ts": 0.0, "fails": 0})
_warm = {"n": 0}
E._warm_async = lambda: _warm.__setitem__("n", _warm["n"] + 1)
_calls_req = _stub(False, True, long_ok=True)
_alive_req = E.embed_alive()
chk("★要求路は短probeしか撃たぬ(長い確認probeで人の番を止めぬ)",
    _calls_req["short"] == 1 and _calls_req["long"] == 0)
chk("★要求路は即座に『今は使えぬ』と答える(字面検索へ退く)", _alive_req is False)
chk("★だが黙って切りっぱなしにせず、背後で温める(次の番は速い)", _warm["n"] == 1)
_src_emb = io.open(os.path.join(HERE, "casper_embed.py"), encoding="utf-8").read()
chk("★温めは本番経路(embed_one)で行う(生死は使う経路そのもので測る/温める)",
    "embed_one(\".\")" in _src_emb)

# ── ②③ health の窓へどう映るか ────────────────────────────────────────
print("── ②③ health の窓 ──")
_tmp = tempfile.mkdtemp(prefix="gate_emb_alert_")
H.HEALTH_STATE = os.path.join(_tmp, "state.json")
H.ALERTS = os.path.join(_tmp, "alerts.jsonl")
H.HEALTH_MD = os.path.join(_tmp, "health.md")
H._load = lambda: []                       # ★対話ゼロの夜を模す(掟④)


def _tick():
    a = H.analyze()
    H.write_health_md(a)
    H._alert(a)
    return a


_judge(False, True, long_ok=True)          # cold
_a_cold = _tick()
chk("② ★冷えていただけでは吠えぬ(deviations に載らぬ)",
    [d["metric"] for d in _a_cold["deviations"]] == [])
chk("② それでも『冷えていた』と正直に名乗る", "冷えていたが健やか" in _a_cold["embed"]["reason"])
_judge(False, True, long_ok=False)         # down
_a_down = _tick()
chk("③ 断は deviations に載る", [d["metric"] for d in _a_down["deviations"]] == ["embed_down"])
chk("④ ★対話ゼロの夜でも検めておる(窓が空でも判定が在る)",
    _a_down["embed"]["status"] == "down" and _a_down.get("n", 0) == 0)
chk("④ health.md に人の読める行が出る",
    "埋込機が落ちておる" in io.open(H.HEALTH_MD, encoding="utf-8").read())
_judge(False, None)                        # unknown
_a_unk = _tick()
chk("③ ★訊けなんだ は down と別の欄で載る",
    [d["metric"] for d in _a_unk["deviations"]] == ["embed_unknown"])
chk("③ health.md でも『確かめられなんだ』と名乗る",
    "確かめられなんだ" in io.open(H.HEALTH_MD, encoding="utf-8").read())

# ── ⑤⑥ 既存の届け先まで実際に届くか ───────────────────────────────────
print("── ⑤⑥ 家老まで届くか ──")
rows = [json.loads(l) for l in io.open(H.ALERTS, encoding="utf-8")]
chk("⑤ 台帳(casper_alerts.jsonl)に赤が積まれておる", len(rows) >= 2)

_sent = []
AD._notify = lambda msg, dry_run: _sent.append(msg)
_dstate = {"cursor_line": 0, "metrics": {}}
_res = AD.process(rows, _dstate, dry_run=True)
chk("⑤ ★合成の赤が家老への通知本文になった(既存経路に相乗り)",
    any("embed_down" in m for m in _sent))
chk("⑤ 訊けなんだ も別の名で届く", any("embed_unknown" in m for m in _sent))
chk("⑤ 新しい通知路を作っておらぬ(alert_dispatch の既存の口のみ)",
    "casper_alerts" in (_sent[0] if _sent else ""))
_n_first = len(_sent)
AD.process(rows, _dstate, dry_run=True)
chk("⑥ 同じ赤で鳴り続けぬ(既読は飛ばす)", len(_sent) == _n_first)

_judge(True, True)                         # 復旧
_a_ok = _tick()
rows2 = [json.loads(l) for l in io.open(H.ALERTS, encoding="utf-8")]
AD.process(rows2, _dstate, dry_run=True)
chk("⑥ ★復旧も一度知らせる", any("復旧" in m and "embed_down" in m for m in _sent))
chk("⑥ 復旧後は deviations に埋込の欄が無い", [d["metric"] for d in _a_ok["deviations"]] == [])

# ── ★突然変異 ──────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
SRC = io.open(os.path.join(HERE, "casper_embed.py"), encoding="utf-8").read()
_m1 = '''    if stock is True:
        if _probe(timeout=_EMB_CONFIRM_TIMEOUT):
            return "cold", "冷えていたが健やか(確認probeに応答・ついでに温めた)"
        return "down", "宿は在るが埋込が応じぬ(確認probeも不発)"'''
chk("★変異の錨が在る(ゲートの自己点検)", SRC.count(_m1) == 1)
_ns = {"__file__": os.path.join(HERE, "casper_embed.py"), "__name__": "casper_embed_mutant"}
exec(compile(SRC.replace(_m1, '''    if stock is True:
        return "down", "宿は在るが埋込が応じぬ"'''), "casper_embed.py", "exec"), _ns)
_ns["_probe"] = lambda timeout=None: False
_ns["model_in_stock"] = lambda timeout=3: True
chk("★変異(冷間を断と数える旧実装): 健やかな宿を断と誤る(赤化実証)",
    _ns["embed_health_verdict"]()[0] == "down")
chk("★同じ状態で本物は cold と名乗る(変異の対照)",
    (lambda: (_stub(False, True, long_ok=True), E.embed_health_verdict()[0])[1])() == "cold")

_m2 = '''    if embed["status"] == "ok":
        return None
    return {"metric": "embed_down" if embed["status"] == "down" else "embed_unknown",'''
HSRC = io.open(os.path.join(HERE, "casper_health.py"), encoding="utf-8").read()
chk("★変異の錨が在る(health側・ゲートの自己点検)", HSRC.count(_m2) == 1)
_hns = {"__file__": os.path.join(HERE, "casper_health.py"), "__name__": "casper_health_mutant"}
exec(compile(HSRC.replace(_m2, '''    if embed["status"] == "ok":
        return None
    return None
    return {"metric": "embed_down" if embed["status"] == "down" else "embed_unknown",'''),
             "casper_health.py", "exec"), _hns)
_hns["_load"] = lambda: []
_hns["HEALTH_STATE"] = os.path.join(_tmp, "state_mut.json")
_judge(False, True, long_ok=False)                      # 断の状態に据える
_mut_dev = [d["metric"] for d in _hns["analyze"]()["deviations"]]
chk("★変異(赤を台帳へ載せぬ): 断が deviations に現れず誰にも届かぬ(赤化実証)",
    "embed_down" not in _mut_dev)
chk("★同じ状態で本物は載せる(変異の対照)",
    [d["metric"] for d in H.analyze()["deviations"]] == ["embed_down"])

E._probe, E.model_in_stock = _orig_probe, _orig_stock
E._EMB_HEALTH.clear(); E._EMB_HEALTH.update(_orig_health)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
