#!/usr/bin/env python3
"""Casper circuit breaker — 自己改善ループ 柱2(self-repair)の安全弁(Fable5設計)。

依存(z8a/Calendar/Aurora/Vimeo)ごとに green/yellow/red の状態機械を持ち、連続失敗/高レイテンシで
自動縮退→回復で自動復帰。単一障害点への現実解は冗長化(金)でなく『正直な縮退＋自動復帰』。

鉄則(Fable): フレームワークを作らない=1ファイル＋breaker.json。縮退も検問対象(参照できなかったのに
参照したふりを許さぬ)。フラッピング防止=half-open→green は連続N成功を要求。

状態: green(正常) → 連続3失敗 or p95超過 → red(縮退) → クールダウン後 half-open(試験) →
       連続2成功 → green / 失敗 → red へ戻る。
レコード(breaker.json): {dep: {state, fails, oks, ema_ms, opened_at, updated}}
"""
import datetime
import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "breaker.json")
_LOCK = threading.Lock()

FAIL_TO_OPEN = 3            # 連続失敗でopen(red)
OK_TO_CLOSE = 2            # half-open中の連続成功でclose(green)
COOLDOWN_SEC = 60         # red→half-open のクールダウン
SLOW_MS = {"z8a": 30000, "calendar": 8000, "aurora": 12000, "vimeo": 60000}   # これ超で"遅い"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _epoch():
    return datetime.datetime.now().timestamp()


def _load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(d):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def _rec(d, dep):
    return d.setdefault(dep, {"state": "green", "fails": 0, "oks": 0, "ema_ms": 0.0,
                              "opened_at": 0.0, "updated": _now()})


def record(dep, ok, latency_ms=0):
    """依存呼出の結果を記録し状態を更新。ok=成功可否, latency_ms=所要ミリ秒。"""
    with _LOCK:
        d = _load()
        r = _rec(d, dep)
        r["ema_ms"] = 0.7 * r.get("ema_ms", 0.0) + 0.3 * float(latency_ms or 0)
        slow = latency_ms and latency_ms > SLOW_MS.get(dep, 30000)
        eff_ok = ok and not slow
        if eff_ok:
            r["oks"] = r.get("oks", 0) + 1
            r["fails"] = 0
            if r["state"] in ("red", "half-open") and r["oks"] >= OK_TO_CLOSE:
                r["state"] = "green"                    # 連続成功で復帰(フラッピング防止)
            elif r["state"] == "green":
                pass
        else:
            r["fails"] = r.get("fails", 0) + 1
            r["oks"] = 0
            if r["fails"] >= FAIL_TO_OPEN:
                if r["state"] != "red":
                    r["opened_at"] = _epoch()
                r["state"] = "red"                      # 連続失敗でopen=縮退
        r["updated"] = _now()
        _save(d)
        return r["state"]


def allow(dep):
    """この依存を呼んでよいか。red中はクールダウン後だけ half-open(試験) を許す。"""
    d = _load()
    r = d.get(dep)
    if not r or r.get("state") == "green":
        return True
    if r.get("state") == "red":
        if _epoch() - r.get("opened_at", 0) >= COOLDOWN_SEC:
            with _LOCK:                                 # クールダウン明け→half-open(1発試験を通す)
                d2 = _load(); r2 = _rec(d2, dep)
                if r2.get("state") == "red":
                    r2["state"] = "half-open"; r2["updated"] = _now(); _save(d2)
            return True
        return False                                    # red中=縮退経路へ
    return True                                         # half-open=試験を通す


def gen_key(host, port):
    """casper_failover.py用の生成系依存キー(host:port単位でbreakerを分ける)。"""
    return f"gen:{host}:{port}"


def emb_key(host, port):
    """casper_failover.py用の埋め込み系依存キー(host:port単位でbreakerを分ける)。"""
    return f"emb:{host}:{port}"


def state(dep):
    return (_load().get(dep) or {}).get("state", "green")


def status():
    d = _load()
    return {k: {"state": v.get("state"), "fails": v.get("fails"), "ema_ms": round(v.get("ema_ms", 0))}
            for k, v in d.items()}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:                               # テスト: record <dep> <ok> [ms]
        print(record(sys.argv[2], sys.argv[1] == "ok", int(sys.argv[3]) if len(sys.argv) > 3 else 0))
    print(json.dumps(status(), ensure_ascii=False, indent=1))
