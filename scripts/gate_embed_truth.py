#!/usr/bin/env python3
r"""埋込の健康と失敗の証言を正直にする回帰ゲート(2026-08-31・Fable診断 急所3/5)。全PASSで exit 0。

【病①: 緑の看板の裏で臓器がむせていた(急所5)】
`emb:<host>` の緑は `probe-home` が **/api/tags の在庫結果**を書いたものであった。
「在庫が在る」を「埋込が健やか」と名乗る嘘で、ema が stock 欄と小数点以下まで同値なのが証拠。
その緑の裏で黒匣には 8/25 以降 **日々39〜68件の埋込失敗**が刻まれ続けていた。
——「未確認をtrueと名乗るな」の再演。実呼出だけが埋込の健康を語れる。

【病②: 失敗の自らの証言(HTTPの番号)をどの台帳も持たなんだ(急所3)】
呼び手は 503 を握っているのに `record_incident` へ渡さず、`record_call_timing` には
status の欄すら無かった。ゆえ黒匣の判定表は 503 を **cold/eviction** と誤記した——
埋込は bge-m3 が常駐せぬゆえ ps に載らず、**構造的に**その先の行へ辿り着けなかった。
「混雑を不在と名乗るな」が、判定表という別の関でまた破られていた。

守る掟:
 ① emb: の欄は **実呼出だけ**が書く。在庫は embstock: の別欄へ(捨てはせぬ)。
 ② 実呼出の成否(成功も失敗も)が breaker へ刻まれる。
 ③ ★混雑(busy)では breaker を倒さぬ——宿の死ではない。倒せば退避の判断まで狂う。
 ④ 失敗の番号(status_code)と呼び手の名づけ(reason)が台帳へ残る。
 ⑤ ★判定表に queue_full の行が在り、**最上位**で効く(行列が溢れておるなら他の証言は語らぬ)。
 ⑥ 番号が無い時は従前の判定を変えぬ(退行させぬ)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_breaker as B                                  # noqa: E402
import casper_embed as E                                    # noqa: E402
import casper_llm_client as C                               # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ── ⑤⑥ 判定表 ─────────────────────────────────────────────────────────
print("── ⑤⑥ 黒匣の判定表 ──")
_PS_EMPTY = {"models": []}                                  # bge-m3 は常駐せぬ(埋込の常態)
for _code in (503, 429):
    _j = C.judge_incident("ok", _PS_EMPTY, "error", "x", "bge-m3", [], status_code=_code)
    chk(f"⑤ {_code} → queue_full(cold/eviction へ落ちぬ)", _j["verdict"] == "queue_full")
_j = C.judge_incident("ok", _PS_EMPTY, "error", "x", "bge-m3", [], status_code=503)
chk("⑤ 番号を証跡に残す", _j["details"].get("status_code") == 503)
chk("⑤ ★宿は生きておると明言(死とも冷えたとも名乗らぬ)", "宿は生" in _j["details"].get("note", ""))
chk("⑥ 番号なしは従前どおり cold/eviction(退行させぬ)",
    C.judge_incident("ok", _PS_EMPTY, "error", "x", "bge-m3", [])["verdict"] == "cold/eviction")
chk("⑥ host不達は queue_full に食われぬ",
    C.judge_incident("error", "x", "error", "x", "m", [], status_code=None)["verdict"] == "network/host down")
chk("⑤ ★queue_full は host不達より先に効く(行列が溢れた時 ps は原因を語らぬ)",
    C.judge_incident("error", "x", "error", "x", "m", [], status_code=503)["verdict"] == "queue_full")

# ── ④ 台帳が証言を持つ ─────────────────────────────────────────────────
print("── ④ 失敗の証言 ──")
_tmpq = tempfile.mkdtemp(prefix="gate_embed_truth_")
_orig_q = C.QUEUE_DIR
C.QUEUE_DIR = _tmpq
C.record_call_timing("casper_embed", "bge-m3", "http://h:1", None, outcome="busy", status_code=503)
C.record_call_timing("casper_embed", "bge-m3", "http://h:1", None, ollama_done={"total_duration": 5},
                     outcome="ok")
_rows = [json.loads(x) for x in io.open(os.path.join(_tmpq, "ollama_call_timing.jsonl"), encoding="utf-8")]
C.QUEUE_DIR = _orig_q
chk("④ 失敗の行に番号が残る", _rows[0].get("status_code") == 503 and _rows[0].get("outcome") == "busy")
chk("④ 成功の行にも名乗りが残る", _rows[1].get("outcome") == "ok")
chk("④ ★取れぬ値は埋めぬ(成功行に番号を捏造せぬ)", "status_code" not in _rows[1])
_src_c = io.open(os.path.join(HERE, "casper_llm_client.py"), encoding="utf-8").read()
chk("④ record_incident が番号と名づけを受ける",
    "def record_incident(site, model, host, ttft_info=None, status_code=None, reason=None):" in _src_c)
chk("④ 呼び手の名づけも併記される(取り違えを後から検める)", '"caller_reason"' in _src_c)

# ── ①②③ 埋込の健康は実呼出が語る ────────────────────────────────────
print("── ①②③ 誰が emb: を書くか ──")
_src_f = io.open(os.path.join(HERE, "casper_failover.py"), encoding="utf-8").read()
chk("① ★probe-home が在庫を emb: へ書かぬ", "B.record(ek, ok=embed_stock_ok" not in _src_f)
chk("① 在庫は embstock: の別欄へ残す(捨てぬ)", 'B.record("embstock:" + hostport' in _src_f)
_src_e = io.open(os.path.join(HERE, "casper_embed.py"), encoding="utf-8").read()
chk("② 埋込が breaker へ刻む関を持つ", "def _breaker_record(" in _src_e)
chk("② 成功の路から刻む(2箇所: embed_one/embed_batch_ex)",
    _src_e.count("_breaker_record(bool(") == 2)
chk("③ ★混雑では倒さぬ枝が在る(2箇所)", _src_e.count('if _why') == 2 and _src_e.count('!= "busy"') == 2)

# 実挙動: 身代わりの推論機で成功・混雑・断を演じさせる
_store = os.path.join(_tmpq, "breaker.json")
_orig_store, B.STORE = B.STORE, _store
_key = B.emb_key("h", "1")


class _HTTP503(Exception):
    code = 503


def _run(kind):
    import urllib.request as _u
    _orig_open, _orig_stock = _u.urlopen, E.model_in_stock
    E.model_in_stock = lambda timeout=3: True

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"embedding": [0.1] * 4}).encode()

    def fake(req, timeout=None):
        if kind == "ok":
            return _R()
        raise _HTTP503() if kind == "busy" else TimeoutError("timed out")
    _u.urlopen = fake
    E.OLLAMA = "http://h:1"
    try:
        return E.embed_one("x")
    finally:
        _u.urlopen, E.model_in_stock = _orig_open, _orig_stock


_run("ok")
_st = B._load().get(_key) or {}
chk("② ★成功が emb: の欄へ刻まれる", _st.get("oks") == 1)
_n_ok = _st.get("oks")
_run("busy")
_st2 = B._load().get(_key) or {}
chk("③ ★混雑では fails を増やさぬ(宿の死ではない)", int(_st2.get("fails", 0)) == 0)
chk("③ 混雑では成功も増やさぬ(緑に化けさせぬ)", _st2.get("oks") == _n_ok)
_run("down")
_st3 = B._load().get(_key) or {}
chk("② ★本物の失敗は刻まれる(黙らせただけになっておらぬ)", int(_st3.get("fails", 0)) >= 1)
B.STORE = _orig_store

# ── ⑦ probeは本番と同じ形で訊く(2026-08-29の掟を埋込にも及ぼす) ────────
print("── ⑦ 測る者が測られる物を変えぬ ──")
# ★実測(2026-08-29/生成): 同じ .139 へ同じ窓で、本番の形(num_ctx=12288/keep_alive=-1)は
#   200・0.1〜0.6秒、形を違えた旧probe(num_ctxなし)は **503即答が4/4**。唯一の差は num_ctx。
#   Ollamaは(model, options)ごとにrunnerを持ち、形の違う要求は別ランナーの積み直しを求める。
#   ★将軍実測(2026-08-31)でも再現: 形を揃えれば http=200(0.47秒)/落とせば http=503(0.003秒)。
#   ——**問い方を違えて、返らぬのを病と読む**のが誤診の型である。埋込でも繰り返させぬ。
_pe = _src_e[_src_e.index("def _probe_ex("):]
_pe = _pe[:_pe.index("\ndef ", 5)]
_eo = _src_e[_src_e.index("def embed_one("):]
_eo = _eo[:_eo.index("\ndef ", 5)]
chk("⑦ ★probe と本番の埋込が同じ口を叩く(/api/embeddings)",
    "/api/embeddings" in _pe and "/api/embeddings" in _eo)
chk("⑦ ★probe と本番が同じ形の body(model+prompt。余計な options を足さぬ)",
    '"model": MODEL, "prompt"' in _pe and '"model": MODEL, "prompt"' in _eo
    and "options" not in _pe)
chk("⑦ 宛先も同じ(probe だけ別の宿を見ぬ)", _pe.count("OLLAMA +") == 1 and _eo.count("OLLAMA +") == 1)

# ── ★突然変異 ──────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
_m = '''    if status_code in (429, 503):'''
chk("★変異の錨が在る(ゲートの自己点検)", _src_c.count(_m) == 1)
_ns = {"__file__": os.path.join(HERE, "casper_llm_client.py"), "__name__": "llm_mutant"}
exec(compile(_src_c.replace(_m, "    if False:"), "casper_llm_client.py", "exec"), _ns)
chk("★変異(queue_full の行を消す): 混雑が cold/eviction と誤記される(赤化実証)",
    _ns["judge_incident"]("ok", _PS_EMPTY, "error", "x", "bge-m3", [],
                          status_code=503)["verdict"] == "cold/eviction")

_m2 = '''    if outcome is not None:
        rec["outcome"] = outcome
    if status_code is not None:
        rec["status_code"] = status_code'''
chk("★変異の錨が在る(台帳・ゲートの自己点検)", _src_c.count(_m2) == 1)
_ns2 = {"__file__": os.path.join(HERE, "casper_llm_client.py"), "__name__": "llm_mutant2"}
exec(compile(_src_c.replace(_m2, "    pass"), "casper_llm_client.py", "exec"), _ns2)
_ns2["QUEUE_DIR"] = _tmpq2 = tempfile.mkdtemp(prefix="gate_embed_truth_mut_")
_ns2["record_call_timing"]("casper_embed", "m", "h", None, outcome="busy", status_code=503)
_mrow = json.loads(io.open(os.path.join(_tmpq2, "ollama_call_timing.jsonl"), encoding="utf-8").readline())
chk("★変異(欄を消す): 失敗の番号が台帳から消え、混雑を後から数えられぬ(赤化実証)",
    "status_code" not in _mrow)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
