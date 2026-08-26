#!/usr/bin/env python3
"""cmd_509第2便: 自動退避 + casper_breaker唯一台帳化(supervisor側consumer)。

★台帳を読む口はcasper_breaker.pyただ一つ。本モジュールはbreakerのrecord()/allow()/state()
を呼ぶだけで、独自の死活閾値を持たない(唯一台帳の趣旨・軍師point_a/breaker_single_ledger_design)。

★HOME(CASPER_HOME_OLLAMA)とACTIVE(CASPER_OLLAMA)は別概念:
  HOME  = 本来の宛先。固定台帳(機構は書き換えない・casper_endpoints.envの運用者記入)。
  ACTIVE= 現在chat_serverが実際に使っている宛先(機構が書き換え得る)。
  ACTIVE!=HOME の間は「退避中」とみなす。★退避後もHOMEを常時probeし続けねば、
  HOMEが復旧したことを機構が二度と知り得なくなる(実装初版の欠陥・sandbox実測で発覚)。

三層probe:
  ・定常(30秒間隔): ACTIVEへ1トークン生成probe(max_tokens=1・最短prompt・keep_alive="10m"明示・
    ACTIVEの温存は殿の体感レイテンシのための意図された政策[Fable第三診])。timeout時は/api/ps(2秒)で
    三値化(ok/cold/fail)——cold(自モデル不在)はbreakerへfailを刻まず専用カウンタへ、
    rate-limit(10分に1回)でconfirm probe(120秒)を発射する。
  ・候補(30秒間隔): ACTIVE!=HOMEの間、HOMEへ/api/tagsのみ(在庫照合つき・冷間ロード誘発せぬ・温めない)
  ・切替判断時のみ: 退避/復帰先へ生成probe(timeout 120秒・冷間ロード51秒/17.3GBを見切らぬ)

退避=state red(breaker.FAIL_TO_OPEN連続失敗・breaker任せ)。
復帰=三条件AND(①breaker green(HOME) ②連続健全30分 ③無通信窓5分)。
切替回数上限=毎時1回。超過時は現状固定+報せ(no silent caps)。

呼び出し方(casper_supervisor.shから):
  python3 casper_failover.py probe-active        # 定常probe(ACTIVEへ生成probe)→record()
  python3 casper_failover.py probe-home          # HOMEへ/api/tags在庫照合→record() (ACTIVE!=HOMEの間のみ意味を持つ)
  python3 casper_failover.py decide               # 退避/復帰の判断のみ(record済の状態を読む)
  python3 casper_failover.py probe-generate --target <host:port>   # 切替判断時のみ(120秒待つ生成probe)
  python3 casper_failover.py switch --to <host:port> --reason <text>   # 実際の切替(env書換+通知)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_breaker as B

ENV_FILE = os.path.join(HERE, "casper_endpoints.env")
STATE_FILE = os.path.join(HERE, "casper_failover_state.json")   # 本モジュール専用の付帯状態(唯一台帳=breaker.jsonとは別物)
FAILOVER_LOG = os.path.join(HERE, "casper_failover.log")
TRAFFIC_HEARTBEAT = os.path.join(HERE, "casper_traffic.heartbeat")

# ── 定数(★閾値そのものはbreaker.pyに集約。ここは「三条件AND」の②③と切替上限のみ) ──
# ★環境変数での上書きは隔離試験専用(CASPER_ROOT同様の後方互換パターン)。未設定時は本番既定値。
HEALTHY_STREAK_REQUIRED_SEC = int(os.environ.get("CASPER_FAILOVER_HEALTHY_STREAK_SEC", 30 * 60))     # ②連続健全30分
NO_TRAFFIC_WINDOW_SEC = int(os.environ.get("CASPER_FAILOVER_NO_TRAFFIC_SEC", 5 * 60))                # ③無通信窓5分
SWITCH_CAP_PER_HOUR = 1
ACTIVE_PROBE_TIMEOUT = 5                  # 定常probe(常駐モデルへ1トークン)
TAGS_PROBE_TIMEOUT = 5                    # 候補probe(/api/tagsのみ・生成せず)
SWITCH_GEN_TIMEOUT = 120                  # ★切替判断時のみ。冷間ロード(51秒/17.3GB)を見切らぬ値
PS_PROBE_TIMEOUT = 2                      # ★三値化判定: timeout直後の/api/ps照会(軽い・病んでる時に叩くため短く)
COLD_CONFIRM_RATE_LIMIT_SEC = 10 * 60     # ★cold判定時のみ、confirm probe(120秒)をこの間隔で1回に制限
COLD_STATE_FILE = os.path.join(HERE, "casper_cold_state.json")   # ★coldカウンタ+confirm rate-limitの専用台帳(breaker.jsonとは別・cold≠failをbreakerへ持ち込まない)


def log(msg):
    with open(FAILOVER_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"healthy_since": None, "switches": []}


def _save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def _read_env():
    """casper_endpoints.envの現行の宛先行を読む。"""
    cur = {}
    if not os.path.exists(ENV_FILE):
        return cur
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" in s:
                k, v = s.split("=", 1)
                cur[k.strip()] = v.strip()
    return cur


def _hostport(url):
    return url.rstrip("/").split("://", 1)[-1]


def _http_json(url, timeout, method="GET", body=None):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"} if body else {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return True, data, int((time.time() - t0) * 1000)
    except Exception as e:
        return False, str(e), int((time.time() - t0) * 1000)


def probe_generate(endpoint, model, timeout):
    """1トークン生成probe。max_tokens=1・最短prompt。
    ★keep_alive="10m"を明示(Fable第三診)。ACTIVEの温存は殿の体感レイテンシのための
    意図された政策——旧実装は未指定(Ollama既定5分)で32秒毎のprobeが偶然温めていたが、
    「明示せぬ温存」は最悪の形(probe間隔や実装を変えた日に気づかず体感が悪化する)ゆえ
    ここで政策として刻む。HOMEはprobe_tags(/api/tagsのみ)で温めない設計は変更せず。"""
    body = json.dumps({"model": model, "stream": False, "prompt": "ping",
                       "keep_alive": "10m", "options": {"num_predict": 1}}).encode()
    url = endpoint.rstrip("/") + "/api/generate"
    ok, data, ms = _http_json(url, timeout, method="POST", body=body)
    return ok, ms


def probe_tags(endpoint, required_models, timeout):
    """/api/tagsのみ(生成せず・冷間ロード誘発せぬ)。required_models全ての在庫有無を返す。"""
    url = endpoint.rstrip("/") + "/api/tags"
    ok, data, ms = _http_json(url, timeout)
    if not ok:
        return False, ms, {}
    names = [m.get("name", "") for m in (data.get("models") or [])]
    have = {rm: any(rm in n for n in names) for rm in required_models}
    return True, ms, have


def probe_ps(endpoint, model, timeout):
    """timeout直後の三値化判定に使う/api/ps照会(軽い・在ってこそ生成中と言える口)。
    到達不可の場合は「不在と確証できない」ため present=None(unknown寄り)を返す
    (到達不可をcoldと決め打つと『占有』を『冷間』に読み違える恐れがあるため)。"""
    url = endpoint.rstrip("/") + "/api/ps"
    ok, data, ms = _http_json(url, timeout)
    if not ok:
        return None, ms
    names = [m.get("name", "") for m in (data.get("models") or [])]
    base = str(model).split(":")[0]
    present = any(base in n for n in names)
    return present, ms


def _load_cold_state():
    if os.path.exists(COLD_STATE_FILE):
        try:
            return json.load(open(COLD_STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cold_state(d):
    tmp = COLD_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, COLD_STATE_FILE)


def _cold_rec(d, key):
    return d.setdefault(key, {"cold_count": 0, "last_confirm_ts": 0.0})


def cmd_probe_active(args):
    """定常probe: ACTIVE(CASPER_OLLAMA)へ生成probe→三値化(ok/cold/fail)→record(gen_key)。

    ★Fable第三診の処方: probeのtimeout(5秒)は冷間ロード(実測7.5秒)より短く、
    「冷たいが健やかな宛先」を原理的に観測できぬ。timeoutした直後に/api/ps(2秒・軽い)を
    叩き、自モデルが不在ならverdict=cold——breakerへfailを刻まず専用カウンタへ計上する
    (cold≠down。退避直後の新ACTIVEは必ず冷えており、これをfailと数えると
    FAIL_TO_OPEN=3×probe間隔32秒≒96秒で「また退避が要る」と判じ得る=flapping)。
    自モデルが在るのにtimeoutした場合のみ本物のfailとしてbreakerへ刻む。
    coldと判定した時だけ、rate-limit(10分に1回)でconfirm probe(120秒)を発射し、
    成功=ok(副作用として温まる)・失敗=本物のfailとしてbreakerへ刻む。
    発火条件は「N回連続」でなく「psが不在と証言した時」——調律すべき数を作らない。"""
    env = _read_env()
    endpoint = env.get("CASPER_OLLAMA", "")
    model = env.get("CASPER_MODEL", "")
    key = B.gen_key(*_hostport(endpoint).split(":", 1))
    ok, ms = probe_generate(endpoint, model, ACTIVE_PROBE_TIMEOUT)

    if ok:
        # 成功時はcold判定不要。breakerへそのまま記録(既存挙動)。
        state = B.record(key, ok=True, latency_ms=ms)
        print(json.dumps({"key": key, "ok": True, "ms": ms, "state": state, "verdict": "ok"}))
        return 0

    # ── timeout/失敗 → /api/psで三値化 ──
    present, ps_ms = probe_ps(endpoint, model, PS_PROBE_TIMEOUT)

    if present is False:
        # 自モデルがpsに不在 → cold。breakerへfailを刻まず専用カウンタへ。
        cold_d = _load_cold_state()
        rec = _cold_rec(cold_d, key)
        rec["cold_count"] = rec.get("cold_count", 0) + 1
        now = time.time()
        confirm_result = None
        since_last = now - rec.get("last_confirm_ts", 0.0)
        if since_last >= COLD_CONFIRM_RATE_LIMIT_SEC:
            rec["last_confirm_ts"] = now
            confirm_ok, confirm_ms = probe_generate(endpoint, model, SWITCH_GEN_TIMEOUT)
            if confirm_ok:
                state = B.record(key, ok=True, latency_ms=confirm_ms)
                confirm_result = {"ok": True, "ms": confirm_ms, "state": state}
            else:
                state = B.record(key, ok=False, latency_ms=confirm_ms)
                confirm_result = {"ok": False, "ms": confirm_ms, "state": state}
        _save_cold_state(cold_d)
        result = {"key": key, "ok": False, "ms": ms, "verdict": "cold",
                  "cold_count": rec["cold_count"], "ps_ms": ps_ms, "confirm": confirm_result}
        print(json.dumps(result, ensure_ascii=False))
        return 0

    # 【2026-08-24 多人数テストの予行で発覚】★自分の仕事を自分の死と数えぬ。
    # 自陣の呼出が今まさに走っている(inflight台帳に在る)なら、probeのtimeoutは
    # 「推論機が病んでいる」ではなく「自分たちが並んでいる」である。推論機は同時要求を
    # 直列に捌くゆえ、5人が話しかければ5秒のprobeは必ず溢れる。これをfailと数えると
    # 混んだ時ほどbreakerが赤へ傾き、テストの最中に退避が発火する(=自傷)。
    # ★verdict=busy として専用に名乗り、breakerへは刻まぬ(coldと同じ作法)。
    if present is True:
        try:
            import casper_llm_client as _llc
            _llc.inflight_gc()                     # 遺物を先に畳む(作った者が畳む)
            _live = []
            for x in (_llc.inflight_list() or []):
                if _hostport(str(x.get("host") or "")) != _hostport(endpoint):
                    continue
                # ★生きたPIDのものだけを数える。死んだPIDの遺物を「走行中」と読めば、
                #   busyが本物の故障を永久に覆い隠す(実測: 死んだPIDの遺物が11件残っていた)。
                try:
                    os.kill(int(x.get("pid")), 0)
                except Exception:
                    continue
                _live.append(x)
        except Exception:
            _live = []
        if _live:
            print(json.dumps({"key": key, "ok": False, "ms": ms, "verdict": "busy",
                              "inflight": len(_live), "ps_ms": ps_ms,
                              "note": "自陣の呼出が走行中ゆえ行列待ち。故障とは数えぬ"},
                             ensure_ascii=False))
            return 0

    # present is True(自モデル在・自陣の走行も無いのにtimeout) または None(ps自体に到達不可)
    # → 本物のfailとしてbreakerへ刻む(占有・停滞の信号。到達不可も安全側=failに倒す)。
    state = B.record(key, ok=False, latency_ms=ms)
    verdict = "fail"
    print(json.dumps({"key": key, "ok": False, "ms": ms, "state": state, "verdict": verdict,
                      "ps_present": present, "ps_ms": ps_ms}, ensure_ascii=False))
    return 0


def cmd_probe_home(args):
    """HOME(CASPER_HOME_OLLAMA)へ/api/tagsのみ+在庫照合→record(gen_key/emb_key)。
    ★ACTIVE==HOMEの時もrecordして構わない(record()は冪等に近い加点)——常時HOMEを見続けることが
    『退避後にHOMEの復旧を検知できない』欠陥の再発防止そのもの。
    在庫欠如は「到達したが答えられぬ」ため failとして記録する(退避の必要条件を破る)。"""
    env = _read_env()
    home = env.get("CASPER_HOME_OLLAMA", "")
    if not home:
        print(json.dumps({"error": "CASPER_HOME_OLLAMA not set"}))
        return 3
    model = env.get("CASPER_MODEL", "")
    embed_model = env.get("CASPER_EMBED_MODEL", "")
    hostport = _hostport(home)
    gk = B.gen_key(*hostport.split(":", 1))
    ek = B.emb_key(*hostport.split(":", 1))
    reach_ok, ms, have = probe_tags(home, [model, embed_model], TAGS_PROBE_TIMEOUT)
    stock_ok = reach_ok and have.get(model, False)
    embed_stock_ok = reach_ok and have.get(embed_model, False)
    gen_state = B.record(gk, ok=stock_ok, latency_ms=ms)
    emb_state = B.record(ek, ok=embed_stock_ok, latency_ms=ms)
    print(json.dumps({"gen_key": gk, "emb_key": ek, "reachable": reach_ok, "ms": ms,
                      "stock": have, "gen_state": gen_state, "emb_state": emb_state}))
    return 0


def cmd_probe_generate(args):
    """切替判断時のみ: args.target(host:port)へ生成probe(timeout120秒・冷間ロードを待つ)。
    定常probeの短timeoutを流用しない。退避先候補(=HOME以外)にも復帰先(=HOME)にも使う汎用形。"""
    if not args.target:
        print(json.dumps({"error": "target required"}))
        return 3
    env = _read_env()
    model = env.get("CASPER_MODEL", "")
    endpoint = f"http://{args.target}"
    gk = B.gen_key(*args.target.split(":", 1))
    ok, ms = probe_generate(endpoint, model, SWITCH_GEN_TIMEOUT)
    state = B.record(gk, ok=ok, latency_ms=ms)
    print(json.dumps({"key": gk, "ok": ok, "ms": ms, "state": state}))
    return 0


def _traffic_quiet_for(sec):
    """直近sec秒間、chat_serverに実トラフィックが無かったか(heartbeatファイルの更新時刻を見る)。
    ★heartbeatが存在しない=起動直後で実トラフィックが一度も無い状態を指す。この場合は
    『無通信窓が満たされている』とみなす(復帰を不当に妨げない・かつ会話中でない証拠がある)。"""
    if not os.path.exists(TRAFFIC_HEARTBEAT):
        return True
    age = time.time() - os.path.getmtime(TRAFFIC_HEARTBEAT)
    return age >= sec


def _switches_last_hour(state):
    now = time.time()
    return [s for s in state.get("switches", []) if now - s.get("ts", 0) < 3600]


def cmd_decide(args):
    """record済のbreaker状態を読み、退避/復帰いずれかの判断のみ行う(実切替はしない)。
    ★ACTIVE(CASPER_OLLAMA)とHOME(CASPER_HOME_OLLAMA)を比較して「退避中か」を機構的に判定する
    (呼び出し元からのフラグに頼らない・envが正)。
    ★supervisor側はこの出力(action)を見て、必要ならswitchサブコマンドを呼ぶ。"""
    env = _read_env()
    active_ep = env.get("CASPER_OLLAMA", "")
    home_ep = env.get("CASPER_HOME_OLLAMA", "")
    active_hostport = _hostport(active_ep)
    home_hostport = _hostport(home_ep)
    active_key = B.gen_key(*active_hostport.split(":", 1))
    active_state = B.state(active_key)
    state = _load_state()
    evacuated = bool(home_hostport) and (active_hostport != home_hostport)

    result = {"active_key": active_key, "active_state": active_state, "evacuated": evacuated,
              "action": "none", "reason": ""}

    if active_state == "red":
        caps = _switches_last_hour(state)
        if len(caps) >= SWITCH_CAP_PER_HOUR:
            result["action"] = "cap_reached"
            result["reason"] = f"毎時上限({SWITCH_CAP_PER_HOUR}回)到達済。現状を固定する。"
        else:
            result["action"] = "evacuate_needed"
            result["reason"] = "現在の宛先がred。健在な退避先の確認が要る(HOMEへ生成probeをsupervisor側で実施せよ)。"
        state["healthy_since"] = None       # 退避方向: ②連続健全カウントをリセット
        _save_state(state)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if not evacuated:
        result["action"] = "none"
        result["reason"] = "既にHOMEを使用中。退避不要。"
        state["healthy_since"] = None
        _save_state(state)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    # ── 退避中: HOMEの復帰を三条件ANDで判断 ──
    home_key = B.gen_key(*home_hostport.split(":", 1))
    home_state = B.state(home_key)
    now = time.time()
    if home_state != "green":
        state["healthy_since"] = None
        _save_state(state)
        result["reason"] = f"①未充足(HOME state={home_state})。復帰せず待機。"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not state.get("healthy_since"):
        state["healthy_since"] = now
        _save_state(state)
        result["reason"] = "①充足・②計測開始(HOME green化を検知した瞬間)。復帰せず待機。"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    healthy_elapsed = now - state["healthy_since"]
    cond2 = healthy_elapsed >= HEALTHY_STREAK_REQUIRED_SEC
    cond3 = _traffic_quiet_for(NO_TRAFFIC_WINDOW_SEC)
    if cond2 and cond3:
        caps = _switches_last_hour(state)
        if len(caps) >= SWITCH_CAP_PER_HOUR:
            result["action"] = "cap_reached"
            result["reason"] = f"毎時上限({SWITCH_CAP_PER_HOUR}回)到達済。現状を固定する。"
        else:
            result["action"] = "return_home"
            result["reason"] = f"三条件AND充足(①green ②健全{int(healthy_elapsed)}秒 ③無通信窓充足)。HOMEへ復帰可。"
    else:
        result["reason"] = f"①充足・②健全継続{int(healthy_elapsed)}秒/{HEALTHY_STREAK_REQUIRED_SEC}秒・③無通信窓={cond3}。三条件AND未充足ゆえ待機。"
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _rewrite_env(target_hostport, is_embed_too):
    """casper_endpoints.envのCASPER_OLLAMA(と必要ならCASPER_EMBED_ENDPOINT)行を書き換える。
    ★CASPER_HOME_OLLAMAは触らない(固定台帳)。機構が書く際は必ずログへ刻む(人手書換との衝突を可視化)。"""
    if not os.path.exists(ENV_FILE):
        raise FileNotFoundError(ENV_FILE)
    with open(ENV_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    new_url = f"http://{target_hostport}"
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("CASPER_OLLAMA="):
            out.append(f"CASPER_OLLAMA={new_url}\n")
        elif is_embed_too and s.startswith("CASPER_EMBED_ENDPOINT="):
            out.append(f"CASPER_EMBED_ENDPOINT={new_url}\n")
        else:
            out.append(line)
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(out)
    os.replace(tmp, ENV_FILE)


def _rewrite_env_kv(key, value):
    """casper_endpoints.envの任意の1行(key=value)を書き換える。無ければ末尾へ足す。
    ★機構が書いた事実は必ずログへ(人手書換との衝突を可視化・_rewrite_envと同じ作法)。"""
    if not os.path.exists(ENV_FILE):
        raise FileNotFoundError(ENV_FILE)
    with open(ENV_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}\n"); found = True
        else:
            out.append(line)
    if not found:
        out.append(f"\n# 【2026-08-24 機構が追記】雲への降段状態(最下段の座席)\n{key}={value}\n")
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(out)
    os.replace(tmp, ENV_FILE)
    return found


def cmd_set_backend(args):
    """【殿御裁可2026-08-24・甲】最下段の座席=雲(claude_cli)への降段/復席。
    ★GPUの席が一つも緑でない時のみ雲へ座る。雲は救命艇であって住処ではない。
    ★雲へ出た内容は casper_cloud_ledger が一件残らず帳簿へ刻む(殿御下命)。
    通知(inbox/Discord)はsupervisor側が担う(cmd_switchと同じ分担)。"""
    to = (args.to or "").strip()
    if to not in ("claude_cli", "ollama"):
        print(json.dumps({"error": "--to must be claude_cli|ollama"}))
        return 3
    env = _read_env()
    old = env.get("CASPER_BACKEND", "ollama")
    if old == to:
        print(json.dumps({"backend": to, "changed": False, "reason": "既にその座席に居る"},
                         ensure_ascii=False))
        return 0
    _rewrite_env_kv("CASPER_BACKEND", to)
    state = _load_state()
    state.setdefault("backend_switches", []).append(
        {"ts": time.time(), "from": old, "to": to, "reason": args.reason or ""})
    _save_state(state)
    log(f"[座席変更] backend {old} → {to} (reason={args.reason}) ★機構による自動書換。"
        + ("★社の情報がAnthropicを経由する状態に入る。帳簿=casper_cloud_ledger.jsonl。"
           if to == "claude_cli" else "★ローカル推論へ復席。"))
    print(json.dumps({"backend": to, "from": old, "changed": True,
                      "reason": args.reason or ""}, ensure_ascii=False))
    return 0


def cmd_switch(args):
    """実際の切替を行う: env書換+ログ+state記録。★呼び出し元(supervisor)がinbox/Discord通知を担う
    (このモジュールはPython・通知スクリプトはbash資産のため、通知はsupervisor.sh側で行う設計)。"""
    if not args.to:
        print(json.dumps({"error": "--to required"}))
        return 3
    env = _read_env()
    old = env.get("CASPER_OLLAMA", "")
    _rewrite_env(args.to, is_embed_too=True)
    state = _load_state()
    state.setdefault("switches", []).append({"ts": time.time(), "from": old, "to": f"http://{args.to}",
                                              "reason": args.reason or ""})
    if args.reason == "return_home":
        state["healthy_since"] = None
    _save_state(state)
    log(f"[env書換] {old} → http://{args.to} (reason={args.reason}) ★機構による自動書換。"
        f"人手による書換と衝突せぬよう本ログで復旧手順の起点とせよ。")
    print(json.dumps({"from": old, "to": f"http://{args.to}", "reason": args.reason}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe-active")
    sub.add_parser("probe-home")

    p_gen = sub.add_parser("probe-generate")
    p_gen.add_argument("--target", required=True, help="host:port")

    sub.add_parser("decide")

    p_bk = sub.add_parser("set-backend")
    p_bk.add_argument("--to", required=True, help="claude_cli(雲へ降段) | ollama(復席)")
    p_bk.add_argument("--reason", default="")
    p_sw = sub.add_parser("switch")
    p_sw.add_argument("--to", required=True, help="host:port")
    p_sw.add_argument("--reason", default="")

    args = ap.parse_args()
    fn = {"probe-active": cmd_probe_active, "probe-home": cmd_probe_home,
          "probe-generate": cmd_probe_generate,
          "decide": cmd_decide, "switch": cmd_switch,
          "set-backend": cmd_set_backend}[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
