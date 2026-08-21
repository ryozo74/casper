#!/usr/bin/env python3
"""cmd_519第1便 AC1〜AC6回帰試験: probe三値化(ok/cold/fail)+keep_alive明示の合成検証。

★守秘: 実ネットワーク呼出は一切行わない。urllib.request.urlopenをmonkeypatchし、
合成応答(timeout/正常/psレスポンス)で分岐を検査する(赤の証明・当陣の掟)。
★AC5(反証照会)が最重要: 「三値化がcoldを導入した結果、本物の不調まで隠す」ことが
起きていないかを、自モデルがps在+timeoutの合成状況で確かめる。

Usage: python3 test_casper_failover_tristate.py
"""
import io
import json
import os
import shutil
import sys
import time
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return io.BytesIO(json.dumps(self._payload).encode())

    def __exit__(self, *a):
        return False


def _install_fake_env(tmp_dir, active_endpoint="http://fake-active:11434"):
    """casper_endpoints.envの代わりに一時的なENV_FILE/STATE_FILE/COLD_STATE_FILE/breaker.jsonを差し込む。"""
    import casper_failover as F
    import casper_breaker as B

    env_path = os.path.join(tmp_dir, "casper_endpoints.env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"CASPER_OLLAMA={active_endpoint}\n")
        f.write("CASPER_MODEL=qwen3.6:27b\n")
        f.write(f"CASPER_HOME_OLLAMA={active_endpoint}\n")

    F.ENV_FILE = env_path
    F.STATE_FILE = os.path.join(tmp_dir, "casper_failover_state.json")
    F.FAILOVER_LOG = os.path.join(tmp_dir, "casper_failover.log")
    F.COLD_STATE_FILE = os.path.join(tmp_dir, "casper_cold_state.json")
    B.STORE = os.path.join(tmp_dir, "breaker.json")
    for p in (F.STATE_FILE, F.COLD_STATE_FILE, B.STORE):
        if os.path.exists(p):
            os.remove(p)
    return F, B


def _reset_tmp(name):
    d = os.path.join(HERE, f".test_tristate_tmp_{name}")
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    return d


# ─────────────────────────────────────────────────────────────────
# AC1: probe_generateのbodyにkeep_alive:"10m"が明示されていること
# ─────────────────────────────────────────────────────────────────
def test_ac1_keep_alive_explicit():
    import casper_failover as F

    captured = {}
    real_urlopen = F.urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp({"done": True})

    F.urllib.request.urlopen = fake_urlopen
    try:
        F.probe_generate("http://fake:11434", "qwen3.6:27b", 5)
    finally:
        F.urllib.request.urlopen = real_urlopen

    check("AC1: keep_alive=10mがbodyに明示されている",
          captured.get("body", {}).get("keep_alive") == "10m", str(captured.get("body")))


# ─────────────────────────────────────────────────────────────────
# AC2: timeout直後に/api/ps(timeout=2秒)が叩かれ、自モデル不在時にverdict=coldとなり
#      breakerのfailカウンタに計上されないこと
# ─────────────────────────────────────────────────────────────────
def test_ac2_cold_not_counted_as_breaker_fail():
    tmp = _reset_tmp("ac2")
    import casper_failover as F
    import casper_breaker as B
    F, B = _install_fake_env(tmp)

    ps_calls = []
    generate_calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/api/generate"):
            generate_calls.append(timeout)
            raise urllib.error.URLError("timed out")
        if url.endswith("/api/ps"):
            ps_calls.append(timeout)
            return _FakeResp({"models": [{"name": "other-model:latest"}]})   # 自モデル不在
        raise AssertionError(f"unexpected url {url}")

    real_urlopen = F.urllib.request.urlopen
    F.urllib.request.urlopen = fake_urlopen
    try:
        class Args:
            pass
        rc = F.cmd_probe_active(Args())

        check("AC2: cmd_probe_activeが正常終了", rc == 0)
        check("AC2: /api/psがtimeout=2秒で叩かれた", ps_calls == [F.PS_PROBE_TIMEOUT], str(ps_calls))
        key = B.gen_key("fake-active", "11434")
        # ★初回coldは即confirm(120秒)を発射する仕様(rate-limit窓が空いているため)。
        #   confirmも失敗した場合は「本物のfail」としてbreakerへ刻まれるのが正しい仕様
        #   (confirmはcoldの結果そのものの確認であり、その失敗まで隠せばAC5の反証照会に反する)。
        #   ゆえにここでは「rate-limit内の2回目(confirm発射なし)」ではfailが刻まれない
        #   ことを確認する——coldそのものはbreakerへfailを刻まない、が本試験の核心。
        generate_calls.clear()
        rc2 = F.cmd_probe_active(Args())
        check("AC2: rate-limit内の2回目(confirm発射なし)ではgenerateは定常probeの1回のみ",
              generate_calls == [F.ACTIVE_PROBE_TIMEOUT], str(generate_calls))
        fails = B._load().get(key, {}).get("fails", 0)
        check("AC2: confirm不発射の間はbreakerのfailsカウンタがconfirm失敗1回分のみ(2回目のcold自体はfailを刻まない)",
              fails == 1, str(fails))
        check("AC2: confirm不発射の間はbreakerがgreenのまま(coldはfailに計上されない)",
              B.state(key) == "green", B.state(key))
        cold_d = F._load_cold_state()
        check("AC2: 専用coldカウンタへ計上された(2回分)",
              cold_d.get(key, {}).get("cold_count", 0) == 2, str(cold_d))
    finally:
        F.urllib.request.urlopen = real_urlopen


# ─────────────────────────────────────────────────────────────────
# AC3: 自モデル在+timeoutの場合はverdict=failとなりbreakerへ刻まれること
# ─────────────────────────────────────────────────────────────────
def test_ac3_true_fail_recorded_to_breaker():
    tmp = _reset_tmp("ac3")
    import casper_failover as F
    import casper_breaker as B
    F, B = _install_fake_env(tmp)

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/api/generate"):
            raise urllib.error.URLError("timed out")
        if url.endswith("/api/ps"):
            return _FakeResp({"models": [{"name": "qwen3.6:27b"}]})   # 自モデル在
        raise AssertionError(f"unexpected url {url}")

    real_urlopen = F.urllib.request.urlopen
    F.urllib.request.urlopen = fake_urlopen
    try:
        class Args:
            pass
        for _ in range(F.B.FAIL_TO_OPEN):   # FAIL_TO_OPEN回連続failさせてbreakerをredへ
            F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen

    key = B.gen_key("fake-active", "11434")
    check("AC3: 自モデル在+timeoutはbreakerへ刻まれredへ落ちる",
          B.state(key) == "red", B.state(key))
    cold_d = F._load_cold_state()
    check("AC3: coldカウンタは増えていない(本物のfailはcold扱いされない)",
          key not in cold_d or cold_d[key].get("cold_count", 0) == 0, str(cold_d))


# ─────────────────────────────────────────────────────────────────
# AC4: coldと判定された場合、10分に1回のrate-limitでconfirm probe(120秒)が発射されること
#      (rate-limit境界: 9分59秒では発射されず、10分経過後は発射される)
# ─────────────────────────────────────────────────────────────────
def test_ac4_confirm_rate_limit_boundary():
    tmp = _reset_tmp("ac4")
    import casper_failover as F
    import casper_breaker as B
    F, B = _install_fake_env(tmp)

    generate_calls = []

    def make_fake_urlopen(fail_generate=True):
        def fake_urlopen(req, timeout=None):
            url = req.full_url
            if url.endswith("/api/generate"):
                generate_calls.append(timeout)
                if fail_generate:
                    raise urllib.error.URLError("timed out")
                return _FakeResp({"done": True})
            if url.endswith("/api/ps"):
                return _FakeResp({"models": [{"name": "other-model:latest"}]})   # 常にcold
            raise AssertionError(f"unexpected url {url}")
        return fake_urlopen

    real_urlopen = F.urllib.request.urlopen
    class Args:
        pass

    # 1回目: 定常probe(timeout)→cold判定→confirm発射(初回はrate-limit対象外)
    F.urllib.request.urlopen = make_fake_urlopen(fail_generate=True)
    try:
        F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen
    check("AC4: 初回coldでconfirm probeが発射された(定常+confirmで計2回のgenerate呼出)",
          len(generate_calls) == 2, str(generate_calls))
    check("AC4: confirm probeはSWITCH_GEN_TIMEOUT(120秒)で発射された",
          generate_calls[-1] == F.SWITCH_GEN_TIMEOUT, str(generate_calls))

    key = B.gen_key("fake-active", "11434")
    cold_d = F._load_cold_state()
    last_confirm_ts = cold_d[key]["last_confirm_ts"]

    # 2回目: 9分59秒後 → rate-limit境界内 → confirm発射されない
    generate_calls.clear()
    cold_d[key]["last_confirm_ts"] = last_confirm_ts - (F.COLD_CONFIRM_RATE_LIMIT_SEC - 1)
    F._save_cold_state(cold_d)
    F.urllib.request.urlopen = make_fake_urlopen(fail_generate=True)
    try:
        F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen
    check("AC4: 9分59秒後はconfirmが発射されない(定常probeの1回のみ)",
          len(generate_calls) == 1, str(generate_calls))

    # 3回目: 10分経過後 → confirm発射される
    generate_calls.clear()
    cold_d = F._load_cold_state()
    cold_d[key]["last_confirm_ts"] = time.time() - F.COLD_CONFIRM_RATE_LIMIT_SEC - 1
    F._save_cold_state(cold_d)
    F.urllib.request.urlopen = make_fake_urlopen(fail_generate=True)
    try:
        F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen
    check("AC4: 10分経過後はconfirmが発射される(定常+confirmで計2回)",
          len(generate_calls) == 2, str(generate_calls))

    # confirm成功時はbreakerへok記録される(副作用として温まる)ことも確認
    generate_calls.clear()
    cold_d = F._load_cold_state()
    cold_d[key]["last_confirm_ts"] = time.time() - F.COLD_CONFIRM_RATE_LIMIT_SEC - 1
    F._save_cold_state(cold_d)
    F.urllib.request.urlopen = make_fake_urlopen(fail_generate=False)
    try:
        rc = F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen
    check("AC4: confirm成功でbreakerがgreen(ok)として記録される",
          B.state(key) == "green", B.state(key))


# ─────────────────────────────────────────────────────────────────
# AC5(★最重要・反証照会): 本物の不調(自モデルがpsに在り続けかつ生成が実際に詰まっている)を
#   合成試験で再現し、三値化後も依然failとしてbreakerへ刻まれ、redへ正しく落ちることを実測。
#   coldの導入が「本物の不調を隠す」結果になっていないことを証明する。
# ─────────────────────────────────────────────────────────────────
def test_ac5_real_failure_still_reds_out():
    tmp = _reset_tmp("ac5")
    import casper_failover as F
    import casper_breaker as B
    F, B = _install_fake_env(tmp)

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/api/generate"):
            raise urllib.error.URLError("timed out")   # 生成は常にtimeout(詰まっている)
        if url.endswith("/api/ps"):
            return _FakeResp({"models": [{"name": "qwen3.6:27b"}]})   # 自モデルは在り続ける
        raise AssertionError(f"unexpected url {url}")

    real_urlopen = F.urllib.request.urlopen
    F.urllib.request.urlopen = fake_urlopen
    try:
        class Args:
            pass
        results = []
        for _ in range(F.B.FAIL_TO_OPEN):
            F.cmd_probe_active(Args())
        key = F.B.gen_key("fake-active", "11434")
        final_state = F.B.state(key)
    finally:
        F.urllib.request.urlopen = real_urlopen

    check("AC5(★反証照会): 自モデル在+timeout連続でbreakerがredへ落ちる(coldに隠されない)",
          final_state == "red", final_state)
    cold_d = F._load_cold_state()
    check("AC5: この経路ではcoldカウンタが増えない(coldとfailの取り違えが無い)",
          key not in cold_d or cold_d[key].get("cold_count", 0) == 0, str(cold_d))


# ─────────────────────────────────────────────────────────────────
# AC6: 既存probe動作(成功時の記録)が壊れていないこと(段階的実装・退行なし)
# ─────────────────────────────────────────────────────────────────
def test_ac6_existing_success_path_unaffected():
    tmp = _reset_tmp("ac6")
    import casper_failover as F
    import casper_breaker as B
    F, B = _install_fake_env(tmp)

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/api/generate"):
            return _FakeResp({"done": True})
        raise AssertionError(f"unexpected url {url} (ps should not be called on success)")

    real_urlopen = F.urllib.request.urlopen
    F.urllib.request.urlopen = fake_urlopen
    try:
        class Args:
            pass
        rc = F.cmd_probe_active(Args())
    finally:
        F.urllib.request.urlopen = real_urlopen

    key = B.gen_key("fake-active", "11434")
    check("AC6: 成功時はrc=0", rc == 0)
    check("AC6: 成功時はbreakerがgreen(既存動作のまま)", B.state(key) == "green", B.state(key))
    check("AC6: 成功時は/api/psを叩かない(退行なし・timeout時のみ発火)", True)


def _cleanup():
    for name in ("ac2", "ac3", "ac4", "ac5", "ac6"):
        d = os.path.join(HERE, f".test_tristate_tmp_{name}")
        if os.path.exists(d):
            shutil.rmtree(d)


def main():
    test_ac1_keep_alive_explicit()
    test_ac2_cold_not_counted_as_breaker_fail()
    test_ac3_true_fail_recorded_to_breaker()
    test_ac4_confirm_rate_limit_boundary()
    test_ac5_real_failure_still_reds_out()
    test_ac6_existing_success_path_unaffected()
    _cleanup()

    print()
    if _failures:
        print(f"FAIL: {len(_failures)}件 -> {_failures}")
        return 1
    print("PASS: 全件合格")
    return 0


if __name__ == "__main__":
    sys.exit(main())
