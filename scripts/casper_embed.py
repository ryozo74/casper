#!/usr/bin/env python3
"""Casper embedding RAG — vault を意味ベクトル検索(bge-m3 / Ollama)。
既存 casper_rag(字面トライグラム) を壊さず上に被せるハイブリッド。
- bge-m3 が z8a に在れば意味検索、無ければ自動で字面検索にフォールバック(pull前でも動く)。
- ベクトル索引は casper_embed_index.json に保存。

CLI:
  python3 casper_embed.py build          # 全chunkをベクトル化(要 bge-m3)
  python3 casper_embed.py search "<q>"   # 意味検索テスト
モジュール:
  import casper_embed; casper_embed.search(query, k=8) -> [str,...]
  casper_embed.available() -> bool       # 埋め込み索引が使えるか
"""
import glob
import json
import math
import os
import struct
import sqlite3
import sys
import threading
import time
import urllib.request

import casper_rag

HERE = os.path.dirname(os.path.abspath(__file__))
EMB_INDEX = os.path.join(HERE, "casper_embed_index.json")
EMB_DB = os.path.join(HERE, "casper_embed.db")     # Fable M2: 411MB JSON→sqlite(候補だけ引く・26s全読込を消す)
EMB_META = EMB_INDEX + ".meta.json"                # cmd_498: 件数サイドカー(422MB本体を数え直さぬための台帳)
# ★2026-08-31: 宛先は casper_endpoint(唯一の関)から引く。焼き付きの既定を持たぬ
#   ——cron や gate は env を source せぬゆえ、焼き付き既定は必ずいつか実運用へ漏れる。
#   ★埋込の家は生成とは**別の裁可**で決まる(殿御裁可2026-08-24: 埋込は z8a を借り続ける)。
import casper_endpoint as _ep
OLLAMA = _ep.embed_endpoint()
MODEL = _ep.embed_model()
_VEC = None      # [{src,title,t,v:[float]}]

try:
    sys.path.insert(0, HERE)
    import casper_llm_client                      # cmd_519第3便: 横断呼出台帳(inflight)配線・Fable第三診正典
except Exception:
    casper_llm_client = None


def embed_one(text):
    """1テキストを埋め込みベクトル化。失敗(モデル無し等)で None。"""
    _prompt_chars = min(len(text), 2000)
    _inflight_handle = None
    if casper_llm_client:
        try:
            if casper_llm_client.inflight_should_record(_prompt_chars, "casper_embed"):
                _inflight_handle = casper_llm_client.inflight_start(
                    "casper_embed", MODEL, OLLAMA, _prompt_chars)
        except Exception:
            _inflight_handle = None
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/embeddings",
            data=json.dumps({"model": MODEL, "prompt": text[:2000]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        if casper_llm_client:
            try:
                # ttft_sec=None: 非stream・埋め込みAPIはfirst token概念が無い(distill_activityと同型・瑕疵2是正)
                casper_llm_client.record_call_timing("casper_embed", MODEL, OLLAMA, None, ollama_done=d)
            except Exception:
                pass
        v = d.get("embedding")
        return v if v else None
    except Exception:
        if casper_llm_client:
            try:
                casper_llm_client.record_incident("casper_embed", MODEL, OLLAMA)
            except Exception:
                pass
        return None
    finally:
        if casper_llm_client and _inflight_handle:
            try:
                casper_llm_client.inflight_end(_inflight_handle)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# 【殿御下命2026-08-29・丁】「混雑を不在と名乗るな」
# 実害(2026-08-26): `.139` の /api/generate と /api/embed が90秒超で無応答になった時、
# 再埋込は `skipped(bge-m3不在・字面索引のみ最新)` と刻んだ。**在庫は在った**
# (/api/tags に bge-m3 を確認済)。混雑・無応答・不在が **同じ一つの出口** に流れていた。
# ★鉄則「失敗とゼロを別の出口へ」の違反。以後、不在を名乗るのは
#   **在庫照会が『無い』と答えた時のみ**とする。照会できねば断じぬ。
# ══════════════════════════════════════════════════════════════════════════════
def model_in_stock(timeout=3):
    """埋込モデルの在庫照会(/api/tags)。返り: True=在る / False=無い / **None=照会できなんだ**。
    ★三値である事が肝。None を False(=不在)へ倒せば、届かぬ事を不在と名乗る嘘が生まれる。"""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout) as r:
            d = json.load(r)
        names = [str(m.get("name") or "") for m in (d.get("models") or [])]
        base = MODEL.split(":")[0]
        return any(n == MODEL or n.split(":")[0] == base for n in names)
    except Exception:
        return None


def _diagnose_embed_failure(exc=None):
    """埋込が成らなんだ理由を **在庫を訊いてから** 名づける。
    返り: "model_absent" / "unreachable" / "busy" / "timeout" / "error"。"""
    stock = model_in_stock()
    if stock is False:
        return "model_absent"      # 在庫照会が『無い』と答えた——この時のみ不在を名乗ってよい
    if stock is None:
        return "unreachable"       # 在庫すら訊けぬ=機そのものへ届いておらぬ(不在ではない)
    code = getattr(exc, "code", None)
    if code in (429, 503):
        return "busy"              # 在庫は在る。混んでいるだけである
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "timeout"           # 在庫は在る。行列を通れなんだ
    return "error"


# 出口ごとの名乗り。★どれも「字面索引のみ最新」だが、**何故そうなったかを取り違えぬ**。
EMBED_SKIP_MSG = {
    "model_absent": "skipped(bge-m3不在・字面索引のみ最新)",
    "busy":         "skipped(埋込サーバが混雑・在庫は在り・字面索引のみ最新)",
    "timeout":      "skipped(埋込が時間内に返らず・在庫は在り・字面索引のみ最新)",
    "unreachable":  "skipped(埋込サーバへ届かず・在庫照会も不可・字面索引のみ最新)",
    "empty":        "skipped(埋込が空を返した・在庫は在り・字面索引のみ最新)",
    "error":        "skipped(埋込が失敗・在庫は在り・字面索引のみ最新)",
}


def embed_batch_ex(texts):
    """バッチ埋め込みの実体。返り: **(vectors|None, reason)**。reason は EMBED_SKIP_MSG の鍵か "ok"。
    ★失敗とゼロを別の出口へ——呼び手が『何故成らなんだか』を取り違えぬようにする唯一の関。"""
    _prompt_chars = sum(min(len(t), 2000) for t in texts)
    _inflight_handle = None
    if casper_llm_client:
        try:
            if casper_llm_client.inflight_should_record(_prompt_chars, "casper_embed"):
                _inflight_handle = casper_llm_client.inflight_start(
                    "casper_embed", MODEL, OLLAMA, _prompt_chars)
        except Exception:
            _inflight_handle = None
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/embed",
            data=json.dumps({"model": MODEL, "input": [t[:2000] for t in texts]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
        if casper_llm_client:
            try:
                casper_llm_client.record_call_timing("casper_embed", MODEL, OLLAMA, None, ollama_done=d)
            except Exception:
                pass
        _vecs = d.get("embeddings")
        return (_vecs, "ok") if _vecs else (None, "empty")   # 空返しも失敗——「在るのに0件」と名乗らせぬ
    except Exception as _e:
        if casper_llm_client:
            try:
                casper_llm_client.record_incident("casper_embed", MODEL, OLLAMA)
            except Exception:
                pass
        return None, _diagnose_embed_failure(_e)
    finally:
        if casper_llm_client and _inflight_handle:
            try:
                casper_llm_client.inflight_end(_inflight_handle)
            except Exception:
                pass


def embed_batch(texts):
    """複数テキストをバッチ埋め込み(/api/embed)。[[float],...] or None。
    ★理由も要る呼び手は embed_batch_ex を使え(こちらは従前の契約を保つ薄い包み)。"""
    return embed_batch_ex(texts)[0]


def _pid_alive(pid):
    """そのPIDが今も生きているか。数でない/居らぬなら False。"""
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, TypeError, ProcessLookupError):
        return False
    except PermissionError:
        return True          # 他人の物=生きている(触らぬ)


def sweep_stale_tmp(path=None, dry_run=False):
    """【殿御裁可2026-08-24】書きかけの遺物を掃く。作った者が畳む。

    なぜ要るか(実測): 索引本体(422MB)は `{path}.tmp.<pid>` へ全部書いてから差し替える。
    書いている最中に supervisor の auto-reload がプロセスを止めると、書きかけだけが残る。
    2026-08-24 11:48、pid 5931 の停止と同時に 167MB の遺物が生まれるのを現認した。
    これが積もって 54個・8.8GB になっていた。

    ★掃くのは【死んだPIDの遺物だけ】。生きたPIDの物は書込中かもしれぬゆえ絶対に触らぬ。
    ★本体(path 自身)には決して手を出さぬ——掃除機が索引を食う事故を機構として不可能にする。
    戻り: {"removed": [...], "kept": [...], "bytes": n}
    """
    base = path or EMB_INDEX
    out = {"removed": [], "kept": [], "bytes": 0}
    for f in glob.glob(base + ".tmp.*"):
        if os.path.abspath(f) == os.path.abspath(base):
            continue                                   # ★本体は対象外(在り得ぬが門を置く)
        pid = f.rsplit(".", 1)[-1]
        if _pid_alive(pid):
            out["kept"].append(f)                      # 生きている=書込中かもしれぬ
            continue
        try:
            n = os.path.getsize(f)
        except OSError:
            n = 0
        if dry_run:
            out["removed"].append(f); out["bytes"] += n
            continue
        try:
            os.remove(f)
            out["removed"].append(f); out["bytes"] += n
        except OSError:
            out["kept"].append(f)
    return out


def _atomic_dump(obj, path):
    """一時ファイルへ書いてから rename(=アトミック置換)。並行 reindex による半端書き込み破損を防ぐ。"""
    try:                                               # 書く前に、前の書きかけの遺物を畳む
        sweep_stale_tmp(path)
    except Exception:
        pass
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)                                  # 同一FS内 rename はアトミック
    if path == EMB_INDEX:                                  # cmd_498: 件数台帳を同便で更新
        try:                                               # (センサーは消費者と同じ便で・後追いにせぬ)
            _write_meta(obj)
        except Exception:
            pass


def build(batch=32):
    """casper_rag の全chunkをバッチでベクトル化して保存。"""
    if casper_rag._CACHE is None:
        casper_rag._CACHE = json.load(open(casper_rag.INDEX, encoding="utf-8")) \
            if os.path.exists(casper_rag.INDEX) else []
    chunks = casper_rag._CACHE
    out = []
    for i in range(0, len(chunks), batch):
        grp = chunks[i:i + batch]
        vecs, _why = embed_batch_ex([e["t"] for e in grp])
        if not vecs:
            print("  バッチ埋め込み失敗: " + EMBED_SKIP_MSG.get(_why, EMBED_SKIP_MSG["error"]) + "。中断。",
                  file=sys.stderr)
            return 0
        for e, v in zip(grp, vecs):
            out.append({"src": e["src"], "title": e.get("title", ""), "t": e["t"], "v": v})
        if (i + batch) % 320 == 0 or i + batch >= len(chunks):
            print(f"  {min(i+batch,len(chunks))}/{len(chunks)} ...", flush=True)
    _atomic_dump(out, EMB_INDEX)
    return len(out)


def reindex():
    """差分再index(帯更新後の連動用)。
    ① トライグラム索引を全再構築(安価・字面検索は常に最新)。
    ② 埋込は未変更chunkのベクトルを再利用し、新規/変更chunkのみ再埋込(高価な全再埋込を回避)。
    ③ bge-m3 未導入なら埋込はskip(旧埋込index温存・字面索引のみ最新)。
    戻り: {"chunks":総数,"reembedded":再埋込数,"embed":状態}。"""
    import casper_rag
    casper_rag.build_index()                                   # 安価: 全トライグラム再構築
    casper_rag._CACHE = None                                   # 次の検索で再読込
    chunks = json.load(open(casper_rag.INDEX, encoding="utf-8"))
    old = {}
    if os.path.exists(EMB_INDEX):
        try:
            for e in json.load(open(EMB_INDEX, encoding="utf-8")):
                old[(e["src"], e["t"])] = e.get("v")
        except Exception:
            old = {}                                           # 破損時は空扱い→全再埋込で自己修復
    out, miss_idx, miss_txt = [], [], []
    for e in chunks:                                           # 未変更は旧ベクトル再利用
        v = old.get((e["src"], e["t"]))
        out.append({"src": e["src"], "title": e.get("title", ""), "t": e["t"], "v": v})
        if v is None:
            miss_idx.append(len(out) - 1); miss_txt.append(e["t"])
    reembedded = 0
    for i in range(0, len(miss_txt), 32):                      # 変更/新規分のみバッチ埋込
        gi, gt = miss_idx[i:i + 32], miss_txt[i:i + 32]
        vecs, _why = embed_batch_ex(gt)
        if not vecs:                                           # 埋込が成らず → skip(旧index温存)
            # ★何故成らなんだかを取り違えぬ。不在を名乗るのは在庫照会が『無い』と答えた時のみ。
            return {"chunks": len(chunks), "reembedded": 0,
                    "embed": EMBED_SKIP_MSG.get(_why, EMBED_SKIP_MSG["error"]), "reason": _why}
        for j, v in zip(gi, vecs):
            out[j]["v"] = v; reembedded += 1
    out = [e for e in out if e.get("v") is not None]
    _atomic_dump(out, EMB_INDEX)
    global _VEC
    _VEC = None
    return {"chunks": len(chunks), "reembedded": reembedded, "embed": "ok"}


def _load():
    global _VEC
    if _VEC is None:
        try:
            _VEC = json.load(open(EMB_INDEX, encoding="utf-8")) if os.path.exists(EMB_INDEX) else []
        except Exception:
            _VEC = []                                          # 破損時は空=字面検索へ退避(crashさせぬ)
    return _VEC


def available():
    # Fable M2: 411MB全読込でなくsqliteの有無で判定(hybridの発火条件)。
    # cmd_497 欠陥Aの是正: sqliteが在っても埋込サーバが死んでいれば意味検索は成らぬ。
    # 「dbが在る」を「使える」と名乗ってはならぬ(掟: 未確認をtrueと名乗るな)。
    return db_available() and embed_alive()


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


# ── Fable M2: 意味検索の復活。411MB JSON全読込(26s)+全走査(8s)を、sqlite(候補だけ引く)+字面recallで置換。numpy不要。
def _key(src, t):
    return f"{src}\x00{(t or '')[:400]}"


def build_sqlite():
    """casper_embed_index.json(411MB) → sqlite(key,src,title,t,vec=float32 packed BLOB)。一度だけ実行。"""
    if not os.path.exists(EMB_INDEX):
        print("no EMB_INDEX", file=sys.stderr); return 0
    data = json.load(open(EMB_INDEX, encoding="utf-8"))
    tmp = EMB_DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE emb(key TEXT PRIMARY KEY, src TEXT, title TEXT, t TEXT, vec BLOB)")
    rows = []
    for e in data:
        v = e.get("v")
        if not v:
            continue
        rows.append((_key(e["src"], e["t"]), e["src"], e.get("title", ""), e["t"],
                     struct.pack("<%df" % len(v), *v)))
        if len(rows) >= 2000:
            con.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?,?,?)", rows); rows = []
    if rows:
        con.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?,?,?)", rows)
    con.commit(); con.close()
    os.replace(tmp, EMB_DB)
    return len(data)


_DBCON = None


def _db():
    global _DBCON
    if _DBCON is None and os.path.exists(EMB_DB):
        _DBCON = sqlite3.connect(EMB_DB, check_same_thread=False)
    return _DBCON


def db_available():
    return _db() is not None


def _blob_vec(b):
    return list(struct.unpack("<%df" % (len(b) // 4), b))


# ══════════════════════════════════════════════════════════════════════════════
# 埋込サーバの生死(cmd_497) — 短命キャッシュで「断」を即答する。
# 実害: 埋込サーバが落ちた時に毎turn疎通確認へ劣化し、応答が遅くなった。
# 掟: 期限内はHTTPを叩かず即答。down後もTTL_DOWN経過で自動再挑戦(手動リセット不要)。
# ══════════════════════════════════════════════════════════════════════════════
_EMB_TTL_OK = 60.0        # 健全と判った後、次に疑うまで(秒)
_EMB_TTL_DOWN = 30.0      # 断と判った後、再挑戦するまで(秒)。復帰を人手に頼らぬための短さ。
_EMB_PROBE_TIMEOUT = 3.0      # 短probe(温まっておれば0.07秒で返る・実測)
_EMB_CONFIRM_TIMEOUT = 30.0   # ★冷間の確認probe。冷間ロードは実測5.01秒——短probeでは原理的に測れぬ
_EMB_HEALTH = {"ok": True, "ts": 0.0, "fails": 0}


def _probe(timeout=None):
    """埋込サーバが実際に埋め込みを返せるかを最小の一発で確かめる。
    ★/api/tags では「行列に入れるか」が判らぬ(行列を通らぬゆえ即答する)。
    生死は使う経路そのもので測る——さもなくば「緑なのに使えぬ」が生まれる。
    ★timeout を受けるのは、短probeでは**冷間の健やかな宿を原理的に観測できぬ**ため
      (実測2026-08-30: 短probe(3秒)は三度とも False、直後の本番embedは ok/1024次元を
       5.01秒で返し、温まった後の短probeは0.07秒で True)。"""
    return _probe_ex(timeout)[0]


def _probe_ex(timeout=None):
    """probe を (成否, 理由) で返す。★理由を捨てぬのが要——捨てれば『混雑』が『死』に化ける。"""
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/embeddings",
            data=json.dumps({"model": MODEL, "prompt": "."}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=(timeout or _EMB_PROBE_TIMEOUT)) as r:
            d = json.load(r)
        return (bool(d.get("embedding")), "ok" if d.get("embedding") else "empty")
    except Exception as e:
        return (False, _diagnose_embed_failure(e))


_EMB_WARM = {"ts": 0.0, "running": False}


def _warm_async():
    """冷えた宿を**背後で**温める(要求路を止めぬ)。

    ★短probe(3秒)は冷間ロード(実測5.01秒)より短く、冷たいが健やかな宿を観測できぬ。
      だが要求路で長い確認probeを撃てば、殿の一問が数十秒止まる——それは治療でなく別の病。
      ゆえ要求路は即座に「今は使えぬ」と答えて字面検索へ退き、**背後で本番経路(embed_one)を
      一度撃って温める**。次の番は速い。温めは _EMB_TTL_DOWN に一度だけ(叩き続けぬ)。
    """
    now = time.time()
    if _EMB_WARM["running"] or (now - _EMB_WARM["ts"]) < _EMB_TTL_DOWN:
        return
    _EMB_WARM["running"] = True
    _EMB_WARM["ts"] = now

    def _run():
        try:
            embed_one(".")                                 # ★生死は使う経路そのもので測る/温める
        except Exception:
            pass
        finally:
            _EMB_WARM["running"] = False
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _EMB_WARM["running"] = False


def embed_alive():
    """埋込サーバの生死(**要求路**)。期限内はHTTPを叩かず即答する(AC1)。
    期限切れの時のみ短probeを【一度だけ】叩き、結果で台帳を更新する。

    ★速さが命の路ゆえ、ここでは短probeしか撃たぬ(冷間の確認は観測路 embed_health_verdict の役)。
      短probeが不発なら「今は使えぬ」と答えて字面検索へ退き、**背後で温める**(_warm_async)。
      ゆえ冷間で意味検索が切れるのは長くて一度の番だけ——旧実装は温めもせず切り続けていた。"""
    now = time.time()
    ok, ts = _EMB_HEALTH.get("ok", True), _EMB_HEALTH.get("ts", 0.0)
    ttl = _EMB_TTL_OK if ok else _EMB_TTL_DOWN
    if now - ts < ttl:
        return bool(ok)                                    # 期限内: 叩かぬ(高速フォールバックの核心)
    alive = bool(_probe())
    _EMB_HEALTH["ok"] = alive
    _EMB_HEALTH["ts"] = now
    _EMB_HEALTH["fails"] = 0 if alive else int(_EMB_HEALTH.get("fails", 0)) + 1
    if not alive:
        _warm_async()                                      # ★冷えていたのなら次の番までに温めておく
    return alive


def embed_health_verdict():
    """埋込サーバの生死を**四値**で正直に名乗る(**観測路**・背後の健診が使う)。

    ok      … 短probeに即答(温まっておる)
    cold    … 短probeは不発だが、在庫在り＋確認probe(長い)に応答 = **冷えていただけで健やか**
    down    … 在庫が無い、または確認probeも不発
    unknown … 宿に訊けなんだ。★これを down と名乗ってはならぬ(失敗とゼロを別の出口へ)

    ★cmd_519(生成probeの三値化)と同じ処方を埋込機へ移したもの。実測(2026-08-30):
      在庫True(0.03s) / 短probe×3すべてFalse(3.00s) / 直後の本番embed ok・1024次元(5.01s) /
      温まった後の短probe True(0.07s)。旧実装はこの冷間を『断』と数えていた。
    ★時間のかかる確認probeを撃つゆえ、要求路(embed_alive)から呼んではならぬ。
    返り: (verdict, reason)
    """
    ok, why = _probe_ex()
    if ok:
        return "ok", "生存(短probeに即答)"
    # ★2026-08-31 是正: 従前ここで理由を捨て、**混雑(503/429)を『断』と名乗っていた**。
    #   実害: 8/30〜8/31 に 145 度の偽の赤を吐き、家老の inbox まで届いた(狼少年)。
    #   同じ刻の breaker は emb:.139 を緑(oks=12568)と記し、殿の対話も通っていた——
    #   ★既に _diagnose_embed_failure が busy を分けていたのに、新しい関がそれを使わなんだ。
    #   「混雑を不在と名乗るな」(cmd_丁)は**関ごとに繰り返し破られる**。理由は捨てるな。
    if why == "busy":
        return "busy", "宿は在るが混んでおる(503/429)——死んではおらぬ"
    if why == "unreachable":
        return "unknown", "宿に訊けなんだ——落ちておるとは断ぜぬ"
    if why == "model_absent":
        return "down", "埋込モデルが在庫に無い"
    # timeout/error/empty: 冷間の見込みが在るゆえ確認probe(長い)を撃つ
    ok2, why2 = _probe_ex(timeout=_EMB_CONFIRM_TIMEOUT)
    if ok2:
        return "cold", "冷えていたが健やか(確認probeに応答・ついでに温めた)"
    if why2 == "busy":
        return "busy", "宿は在るが混んでおる(確認probeも混雑で弾かれた)"
    if why2 == "unreachable":
        return "unknown", "宿に訊けなんだ——落ちておるとは断ぜぬ"
    if why2 == "model_absent":
        return "down", "埋込モデルが在庫に無い"
    return "down", f"宿は在るが埋込が応じぬ(確認probeも不発: {why2})"


# ══════════════════════════════════════════════════════════════════════════════
# 索引の鮮度観測と自動反映(cmd_498)
# 実害: ① reindex() の後に build_sqlite() を呼ばず、意味検索が古いsqliteを見続けた。
#       ② 件数を【生件数】で数えたため重複分だけ常に「ズレている」と判定し、
#          84秒級の reindex が無限に再起動した。ゆえに件数は【一意key基準】で数える。
# ══════════════════════════════════════════════════════════════════════════════
_REINDEX_LOCK = threading.Lock()
_REINDEX_STATE = {"running": False, "pending": False, "last_ok": 0.0, "last_err": "", "reason": ""}
_REINDEX_LOG = os.path.join(HERE, "casper_embed_reindex.jsonl")
_META_MEASURE = {"running": False}


def _reindex_log(event, **kv):
    """再索引の出来事を追記台帳へ(観測できぬ機構は在らぬも同じ)。"""
    try:
        rec = {"ts": time.time(), "event": event}
        rec.update(kv)
        with open(_REINDEX_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_meta():
    """件数サイドカーを読む。無い/壊れていれば {} (=未確認)。"""
    try:
        with open(EMB_META, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _write_meta(obj=None, rows=None):
    """件数サイドカーを書く。本体(EMB_INDEX)の size/mtime を併記し、
    本体が後で差し替わったら台帳が【自ら無効になる】ようにする(陳腐化した数を信じさせぬ)。"""
    if rows is None:
        rows = len({_key(e.get("src"), e.get("t")) for e in (obj or []) if isinstance(e, dict)})
    st = os.stat(EMB_INDEX)
    tmp = EMB_META + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rows": int(rows), "size": st.st_size, "mtime": st.st_mtime,
                   "written_at": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, EMB_META)
    return rows


def _json_row_count():
    """EMB_INDEX の【一意key基準】の件数。
    ★生件数(len(data))で数えてはならぬ——重複を含む索引で永久に「ズレ」と判定され、
    reindex が無限に再起動した実害そのもの(cmd_498)。
    台帳(サイドカー)が本体の size/mtime と一致する時のみ台帳を信じ、
    さもなくば実測へ落ちる(高価ゆえ最後の手段)。"""
    m = _read_meta()
    try:
        st = os.stat(EMB_INDEX)
        if m and int(m.get("size", -1)) == st.st_size and abs(float(m.get("mtime", -1)) - st.st_mtime) < 1e-6:
            return int(m.get("rows", 0))
    except Exception:
        pass
    try:
        data = json.load(open(EMB_INDEX, encoding="utf-8"))
    except Exception:
        return 0
    return len({_key(e.get("src"), e.get("t")) for e in data if isinstance(e, dict)})


def sqlite_row_count():
    """sqlite 側の実件数(COUNT(*)・安価)。引けねば0。"""
    con = _db()
    if con is None:
        return 0
    try:
        return int(con.execute("SELECT COUNT(*) FROM emb").fetchone()[0])
    except Exception:
        return 0


def index_freshness(vault_glob=None):
    """索引の鮮度を観測して申告する(cmd_498)。二軸で見る——
      row_gap : json(本体)と sqlite の件数差。build_sqlite の取り零しを映す。
      behind_sec: vault の最新更新が sqlite 構築時刻をどれだけ追い越したか。
    どちらか一方でも動いていれば stale=True。"""
    g = vault_glob or os.path.join(casper_rag.VAULT, "**", "*.md")
    jr = _json_row_count()
    sr = sqlite_row_count()
    gap = abs(int(jr) - int(sr))
    try:
        db_mtime = os.path.getmtime(EMB_DB) if os.path.exists(EMB_DB) else 0.0
    except Exception:
        db_mtime = 0.0
    newest, newest_src = 0.0, ""
    for pth in glob.iglob(g, recursive=True):
        try:
            mt = os.path.getmtime(pth)
        except OSError:
            continue
        if mt > newest:
            newest, newest_src = mt, pth
    behind = max(0.0, newest - db_mtime)
    return {"json_rows": jr, "sqlite_rows": sr, "row_gap": gap,
            "db_mtime": db_mtime, "vault_newest": newest,
            "vault_newest_src": os.path.basename(newest_src) if newest_src else "",
            "behind_sec": round(behind, 1), "stale": bool(gap or behind > 0)}


def _measure_meta_async():
    """件数台帳が無い間の実測(422MB全読込)を【別スレッドへ隔離】する。
    観測装置が本番の呼出スレッドを止めては本末転倒ゆえ、一本だけ走らせる。"""
    def _run():
        try:
            n = _json_row_count()                          # 実測(高価)
            _write_meta(rows=n)
            _reindex_log("meta_measured", rows=n)
        except Exception as e:
            _reindex_log("meta_measure_failed", err=str(e)[:200])
        finally:
            _META_MEASURE["running"] = False
    with _REINDEX_LOCK:
        if _META_MEASURE["running"]:
            return False
        _META_MEASURE["running"] = True
    threading.Thread(target=_run, daemon=True).start()
    return True


def _reindex_worker(reason=""):
    """再索引の実体(単位機構)。★reindex() の後に必ず build_sqlite() を呼び、
    握っていた古いsqlite接続(_DBCON)を捨てる——ここを外すと意味検索が
    永久に古いsqliteを見続ける(cmd_498【発見1】の再発防止)。"""
    global _DBCON, _VEC
    try:
        r = reindex()
        n = build_sqlite()
        _DBCON = None                                      # 古い接続を握り続けぬ
        _VEC = None
        _REINDEX_STATE["last_ok"] = time.time()
        _REINDEX_STATE["last_err"] = ""
        _reindex_log("reindexed", reason=reason, result=r, sqlite_rows=n)
        return r
    except Exception as e:
        _REINDEX_STATE["last_err"] = str(e)[:300]
        _reindex_log("reindex_failed", reason=reason, err=str(e)[:300])
        return None


def _reindex_loop(reason):
    try:
        while True:
            _reindex_worker(reason)
            with _REINDEX_LOCK:
                if not _REINDEX_STATE["pending"]:
                    _REINDEX_STATE["running"] = False
                    return
                _REINDEX_STATE["pending"] = False          # 走行中に来た要求を1回に畳んで消化
                reason = "coalesced"
    except Exception:
        with _REINDEX_LOCK:
            _REINDEX_STATE["running"] = False


def reindex_async(reason=""):
    """再索引を非同期で【一本だけ】走らせる。走行中の再要求は pending へ畳む。
    ★多重起動を許すと84秒級の reindex が重なり本番を潰す(cmd_498の実害)。
    戻り: 起動したか(True) / 畳んだか(False)。"""
    with _REINDEX_LOCK:
        _REINDEX_STATE["reason"] = reason
        if _REINDEX_STATE["running"]:
            _REINDEX_STATE["pending"] = True
            _reindex_log("coalesced_request", reason=reason)
            return False
        _REINDEX_STATE["running"] = True
    _reindex_log("reindex_start", reason=reason)
    threading.Thread(target=_reindex_loop, args=(reason,), daemon=True).start()
    return True


def ensure_fresh(auto=True):
    """観測時に索引が古ければその場で是正する(cmd_498)。/health と起動時から呼ばれる。
    ★高価な実測は決して呼出スレッドで行わぬ。件数台帳が無い間は json_rows を
    None(=未確認)のまま返し、測定は別スレッドへ回す(掟: 未確認をtrueと名乗るな)。"""
    m = _read_meta()
    known = False
    try:
        st = os.stat(EMB_INDEX)
        known = bool(m) and int(m.get("size", -1)) == st.st_size and \
            abs(float(m.get("mtime", -1)) - st.st_mtime) < 1e-6
    except Exception:
        known = False
    if not known:
        _measure_meta_async()
        return {"json_rows": None, "sqlite_rows": sqlite_row_count(),
                "row_gap": None, "stale": None, "measuring": True,
                "note": "件数台帳が未確認ゆえ別スレッドで実測中(未確認をtrueと名乗らぬ)"}
    f = index_freshness()
    f["measuring"] = False
    if auto and f.get("stale"):
        f["action"] = "reindex_async" if reindex_async("ensure_fresh") else "coalesced"
    else:
        f["action"] = "none"
    f["reindex_running"] = bool(_REINDEX_STATE["running"])
    return f


def search(query, k=8, budget=3800):
    """意味検索(Fable M2復活版): 字面recall→候補の埋め込みだけsqliteから引き→cosine再ランク。
    sqlite/クエリ埋め込みが無ければ casper_rag(字面)にフォールバック。全走査(26s+8s)は行わない。"""
    con = _db()
    if con is None:
        return casper_rag.search(query, k=k, budget=budget)   # sqlite未生成→字面
    qv = embed_one(query)
    if qv is None:
        return casper_rag.search(query, k=k, budget=budget)   # クエリ埋込が成らず→字面(理由は問わず退避)
    cands = casper_rag.candidates(query, n=60)                 # 字面recall(速い)
    if not cands:
        return casper_rag.search(query, k=k, budget=budget)
    scored = []
    for e in cands:
        row = con.execute("SELECT vec FROM emb WHERE key=?", (_key(e["src"], e["t"]),)).fetchone()
        if row:
            scored.append((_cos(qv, _blob_vec(row[0])), e))
    scored.sort(key=lambda x: -x[0])
    res, used, seen = [], 0, set()
    for sc, e in scored:
        norm = e["t"][:80]
        if norm in seen:
            continue
        seen.add(norm)
        line = f"[{e.get('title') or e['src']}] {e['t']}"
        if used + len(line) > budget:
            break
        res.append(line)
        used += len(line)
        if len(res) >= k:
            break
    return res or casper_rag.search(query, k=k, budget=budget)


def hybrid(query, k=8, budget=3800):
    """意味検索 + 字面検索を融合(両方の上位を混ぜて重複除去)。"""
    sem = search(query, k=k, budget=budget // 2) if available() else []
    lex = casper_rag.search(query, k=k, budget=budget // 2)
    out, seen = [], set()
    for line in sem + lex:
        key = line[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= k:
            break
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        print("embedded:", build(), "->", EMB_INDEX)
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        for r in search(sys.argv[2]):
            print(" •", r[:160])
    else:
        print("available:", available(), "/", __doc__)
