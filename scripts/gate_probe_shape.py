#!/usr/bin/env python3
r"""退避機構の二つの嘘の回帰ゲート(殿御下命2026-08-29)。全PASSで exit 0。

2026-08-29 03:47〜03:56、実地で二つの嘘が**互いを打ち消して**いるのを掴んだ。

【甲・probeの嘘】probeが本番と違う形で訊いていた。同じ .139 へ同じ4秒の窓で:
  ・本番(chat_server: num_ctx=12288 / keep_alive=-1) → HTTP200・0.1〜0.6秒
  ・旧probe(num_ctx **なし** / keep_alive="10m")    → HTTP503 即答 4/4
    `{"error":"server busy... maximum pending requests exceeded"}`
  num_ctx を足すだけで 4/4 が 200 になった。**唯一の差は num_ctx**。
  Ollama は (model, options) ごとに runner を持つゆえ、num_ctx 違いは別ランナーの積み直しを
  求める。17.3GB常駐の隣に二つ目は載らず行列が溢れて即503。
  ＝**健やかな座席に答えられぬ形で問い、その沈黙を病と誤診**していた。
  (chat_server:3419 が既に「num_ctx は対話/pinger と統一」と定めていた。probeだけが破っていた)

【乙・台帳の嘘】`cmd_probe_home` が **在庫(/api/tags)だけで gen の欄に ok=True** を書いていた。
  record(ok=True) は fails を 0 に戻すゆえ、ACTIVE==HOME では毎周期:
    probe-active: 生成失敗 → fails=1 / probe-home: 在庫あり → **fails=0**
  FAIL_TO_OPEN=3 に永久に届かず、**HOMEに居る限り座席は原理的に赤くなれぬ**。
  実測: `verdict:"fail"` が4周期続いても state=green・判定「退避不要」。

★片方だけ直せば害になる: 乙だけ直せば健やかな座席から週次上限のある雲へ誤退避(8/25の実害の再演)、
  甲だけ直せば本物の故障が依然隠れる。ゆえ同じ便で両方直し、ここで両方を検める。

守る掟:
 ① probeは**本番と同じ形で訊く**(num_ctx / keep_alive を本番値に揃える)。
 ② 在庫は『生成が通る』の証拠にならぬ。在庫の可否は専用キーへ。gen は触らぬ。
 ③ 在庫が**無い**時だけ gen に fail を刻む(不在は確かに生成不能の証拠・離れるのは速く)。
 ④ 退避中は**本物の生成probe**で HOME の復帰を判ずる(間引きつき・戻るのは遅く)。
 ⑤ ★合成の赤が通る: 生成が続けて失敗すれば座席は red になり、在庫緑がそれを消さず、
    decide が evacuate_needed を出す(**今夜まで原理的に起きなかった事**)。
 ★突然変異: 旧実装に戻すと⑤が起きなくなる(赤化実証)。
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_breaker as B                               # noqa: E402
import casper_failover as F                              # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


HOME = "http://10.0.0.1:11434"
HOSTPORT = "10.0.0.1:11434"
GK = B.gen_key("10.0.0.1", "11434")
EK = B.emb_key("10.0.0.1", "11434")
SK = "stock:" + HOSTPORT
MODEL = "qwen3.6:27b"
EMB = "bge-m3"


class Args:
    target = None


def fresh_store():
    """台帳を毎回まっさらに(前の検体の残りが次を化かさぬよう・過去に踏んだ轍)。"""
    fd, p = tempfile.mkstemp(prefix="gate_probe_shape_", suffix=".json")
    os.close(fd)
    open(p, "w").write("{}")
    B.STORE = p
    return p


def run(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(Args())
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def setenv(active):
    F._read_env = lambda: {"CASPER_HOME_OLLAMA": HOME, "CASPER_OLLAMA": active,
                           "CASPER_MODEL": MODEL, "CASPER_EMBED_MODEL": EMB}


# ── ① probeは本番と同じ形で訊く ──────────────────────────────────────────
print("── ① probeの形 ──")
SENT = {}
def _fake_http(url, timeout, method="GET", body=None):
    SENT["url"] = url
    SENT["body"] = json.loads(body.decode()) if body else None
    return True, {}, 12
_real_http = F._http_json
F._http_json = _fake_http
F.probe_generate(HOME, MODEL, 5)
F._http_json = _real_http
chk("① 生成probeに num_ctx が入る", (SENT["body"].get("options") or {}).get("num_ctx") == 12288)
chk("① その値は本番(chat_server)と同一(12288)",
    '"options": {"num_ctx": 12288, "num_predict": num_predict, "temperature": temperature, "top_p": 0.9}}'
    in open(os.path.join(HERE, "chat_server.py"), encoding="utf-8").read())
chk("① keep_alive も本番と同じ常駐(-1)。測る者が測られる物の寿命を縮めぬ",
    SENT["body"].get("keep_alive") == -1)
chk("① 叩く口は /api/generate のまま", SENT["url"].endswith("/api/generate"))

# ── ②③④ 在庫と生成の欄を分ける ─────────────────────────────────────────
print("── ②③④ 在庫の欄と生成の欄 ──")
fresh_store()
setenv(HOME)                                              # ACTIVE == HOME(退避しておらぬ)
F.probe_tags = lambda ep, models, to: (True, 26, {MODEL: True, EMB: True})
B.record(GK, ok=False, latency_ms=5)                      # 生成が一度失敗した状態を作る
before = json.load(open(B.STORE))[GK]["fails"]
r = run(F.cmd_probe_home)
after = json.load(open(B.STORE))[GK]["fails"]
chk("② ★在庫が在っても gen の失敗を消さぬ(fails が保たれる)", before == 1 and after == 1)
chk("② 在庫は専用キーへ刻む", json.load(open(B.STORE)).get(SK, {}).get("state") == "green")
chk("② 何をしたか名乗る(黙って触れぬ/触らぬを決めぬ)", "触らぬ" in r["gen_action"])

fresh_store()
F.probe_tags = lambda ep, models, to: (True, 26, {MODEL: False, EMB: True})
run(F.cmd_probe_home)
chk("③ 在庫が無ければ gen に fail を刻む(不在は確かに生成不能の証拠)",
    json.load(open(B.STORE))[GK]["fails"] == 1)

fresh_store()
setenv("http://10.0.0.9:11434")                           # 退避中(ACTIVE != HOME)
F.probe_tags = lambda ep, models, to: (True, 26, {MODEL: True, EMB: True})
F.probe_generate = lambda ep, m, to: (True, 900)
F._load_state = lambda: {}
_saved = {}
F._save_state = lambda s: _saved.update(s)
r = run(F.cmd_probe_home)
chk("④ 退避中は本物の生成probeで判ずる", r["gen_action"] == "本物の生成probeで判定"
    and r["gen_probe"] == {"ok": True, "ms": 900})
chk("④ その結果が gen へ刻まれる", json.load(open(B.STORE))[GK]["state"] == "green")
F._load_state = lambda: {"home_gen_probe_ts": 9e12}       # 直前に打った直後を模す
r2 = run(F.cmd_probe_home)
chk("④ 間引きが効き、理由を名乗る(no silent caps)", "間引き" in r2["gen_action"])

# ── ⑤ ★合成の赤が通る ───────────────────────────────────────────────────
print("── ⑤ 合成の赤 ──")
fresh_store()
setenv(HOME)
F.probe_tags = lambda ep, models, to: (True, 26, {MODEL: True, EMB: True})
F.probe_generate = lambda ep, m, to: (False, 5000)        # 生成が通らぬ座席
F.probe_ps = lambda ep, m, to: (True, 1)                  # モデルは常駐(cold でない)
F._load_cold_state = lambda: {}
F._save_cold_state = lambda d: None
import casper_llm_client as _llc                          # noqa: E402
_llc.inflight_list = lambda: []                           # 自陣の走行なし(busy でもない)
verdicts = []
for _ in range(3):                                        # supervisor と同じ順で3周
    verdicts.append(run(F.cmd_probe_active)["verdict"])
    run(F.cmd_probe_home)
chk("⑤ 生成probeは正直に fail と名乗る", verdicts == ["fail", "fail", "fail"])
chk("⑤ ★3回で座席が red になる(在庫緑がそれを消さぬ)",
    json.load(open(B.STORE))[GK]["state"] == "red")
d = run(F.cmd_decide)
chk("⑤ ★decide が退避を要ると判ずる", d["action"] == "evacuate_needed")

# ── ★突然変異: 旧実装(在庫で gen を緑に)へ戻す ──────────────────────────
print("\n--- 突然変異検証 ---")
SRC = open(os.path.join(HERE, "casper_failover.py"), encoding="utf-8").read()
_old = '''    B.record(sk, ok=stock_ok, latency_ms=ms)               # 在庫は在庫の欄へ(生成の欄と混ぜぬ)'''
assert SRC.count(_old) == 1, "変異が当たっていない(ゲートの自己点検)"
mut = SRC.replace(_old, '''    B.record(sk, ok=stock_ok, latency_ms=ms)
    B.record(gk, ok=stock_ok, latency_ms=ms)               # ← 旧実装: 在庫で gen を緑にする''')
MF = {"__file__": os.path.join(HERE, "casper_failover.py"), "__name__": "_mut_failover"}
exec(compile(mut.replace('if __name__ == "__main__":', 'if False:'),
             os.path.join(HERE, "casper_failover.py"), "exec"), MF)
fresh_store()
MF["B"] = B
MF["_read_env"] = lambda: {"CASPER_HOME_OLLAMA": HOME, "CASPER_OLLAMA": HOME,
                           "CASPER_MODEL": MODEL, "CASPER_EMBED_MODEL": EMB}
MF["probe_tags"] = lambda ep, models, to: (True, 26, {MODEL: True, EMB: True})
MF["probe_generate"] = lambda ep, m, to: (False, 5000)
MF["probe_ps"] = lambda ep, m, to: (True, 1)
MF["_load_cold_state"] = lambda: {}
MF["_save_cold_state"] = lambda d: None
for _ in range(6):                                        # 倍の周回を与えても
    run(MF["cmd_probe_active"])
    run(MF["cmd_probe_home"])
chk("★変異(在庫で gen を緑に戻す): 6周しても座席が赤くなれぬ(赤化実証)",
    json.load(open(B.STORE))[GK]["state"] == "green")
chk("★変異: ゆえに decide も退避を要らぬと言い続ける(今夜までの実態)",
    run(MF["cmd_decide"])["action"] == "none")

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
