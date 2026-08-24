#!/usr/bin/env python3
"""cmd_519第2便(Fable第三診・正典 context/fable_prescription_3rd.md 一節): 黒匣骨格。

★台帳は「健やかな時の平均」ではなく「病んだ瞬間の証言」のために建てる(正典・結)。

timeout検知時、三証言(ps snapshot・横断呼出台帳・TTFT)を1レコードへ束ね、判定表に沿って
verdictを機構自身に書かせる(queue/casper_incident.jsonl)。

★本モジュールは推論機を叩く身内(chat_server/distill_activity/casper_embed/casper_failover等
・軍師実測11ファイル)が将来集約する単一クライアント口の骨格。「骨格だけで誰も使わぬ」病を
避けるため、cmd_519第2便でchat_serverのみ最初の実配線対象とする(軍師献策・主問=殿を待たせた
760秒はchat_serverの呼出ゆえ)。distill_activity/casper_embedの配線は第3便(足軽3号)の担当。

横断呼出台帳(queue/ollama_inflight/): ★ディレクトリ実体にするのが肝(正典)——
timeoutの瞬間にlsで身内の一覧が取れ、クラッシュした呼出は残骸として残る。
1呼出=1ファイル {caller, model, host, prompt_chars, t0, pid}。終了時に消してjsonlへ移す。
★probeはinflightへ書かせるな(軍師献策・2700回/日でchat_serverの34件/日の99%を占め、
容疑者でないものを台帳に入れれば掃除の問題に変わる)。「重い呼出」の線引きは
INFLIGHT_MIN_PROMPT_CHARS(既定値・下記)で機械的に判定する——暗黙に落とさず設定として明示する。

黒匣(queue/casper_incident.jsonl)判定表:
  host不達                                    → network/host down
  psに自モデル不在                             → cold/eviction(psに載る別モデル名=追い出した者)
  自モデル在+自陣inflightに長走行あり           → 身内の占有(caller名まで名指し)
  自モデル在+inflight空+併走probeも詰まる       → unknown(観測の境界。「陣外だ」と断定させない)

★verdictは機構が書き、人が書き換えない(軍師補足①)。
★三証言の取得可否を個別に記録(軍師補足②・門⑦=異常時にも値が残るか)。
★1レコードで束ねる(軍師補足③・突き合わせは片方が欠ける種)。
★unknown行にはobserved/unobservableを必ず併記(正典・軍師補足=「原因不明」と読み流されぬため)。
"""
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(HERE, "..", "..", "..", "queue")
INFLIGHT_DIR = os.path.join(QUEUE_DIR, "ollama_inflight")
INCIDENT_LOG = os.path.join(QUEUE_DIR, "casper_incident.jsonl")
INFLIGHT_ORPHAN_LOG = os.path.join(QUEUE_DIR, "ollama_inflight_orphan.jsonl")

# ★cmd_519第3便(gunshi献策2): 残骸(クラッシュ等で消えなかったinflightファイル)は消すな・移せ。
# 「呼出のtimeout上限の2倍」を根拠に600秒とする(軍師献策の数値をそのまま踏襲・当てずっぽうにしない)。
INFLIGHT_ORPHAN_AFTER_SEC = 600

# ★「重い呼出」の線引き(軍師献策・no silent caps=何を書かなかったかを出力できる形に)。
# probeはnum_predict=1・prompt_chars極小(例: "ping"=4文字)。chat_server本呼出は通常
# system+userプロンプトで数百〜数千文字となるため、この閾値で機械的に分離できる
# (数字を当てずっぽうに決めぬため根拠を明記: probe実測4文字 < 閾値64文字 << 本呼出実測)。
INFLIGHT_MIN_PROMPT_CHARS = 64

PS_PROBE_TIMEOUT = 2      # 併走診断(i) /api/ps。病んでいる時に叩くため短く(軍師risk_notes・観測が病を悪化させぬ)
CO_PROBE_TIMEOUT = 2      # 併走診断(ii) 同モデルへ1token probe。同上


def _is_synthetic():
    """★subtask_519_synthetic_marker(gunshi案): CASPER_SYNTHETIC=1環境変数の有無を返す。
    site名へ接頭辞を書き込む方式(cmd_509の教訓=一つの欄は一つの問いに答える、に反した
    旧慣習)ではなく独立欄synthetic用の判定に限定して使う。既定はFalse(安全側・未設定なら
    本物として扱う=「本物が試験として黙殺される」事故を構造的に起こさない)。"""
    return os.environ.get("CASPER_SYNTHETIC") == "1"


def _now():
    return time.time()


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _append_jsonl(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ───────────────────────── 横断呼出台帳(inflight) ─────────────────────────

def inflight_should_record(prompt_chars, caller):
    """「重い呼出」かどうかを機械的に判定する(軍師献策: 線引きを設定として明示)。
    probe系callerは呼出者名で明示的に除外する(数字の閾値だけに頼らず二重の線引き)。"""
    if caller and str(caller).lower().startswith("probe"):
        return False
    return prompt_chars >= INFLIGHT_MIN_PROMPT_CHARS


def inflight_start(caller, model, host, prompt_chars, pid=None):
    """呼出開始時にqueue/ollama_inflight/へ1呼出=1ファイルを置く。
    ★ディレクトリ実体にするのが肝(正典)——timeoutの瞬間にlsで身内の一覧が取れ、
    クラッシュした呼出は残骸として残る。戻り値はinflight_end()へ渡すハンドル(ファイルパス)。
    inflight_should_record()でFalseと出た呼出はここを呼ばない(呼出元の責)。"""
    os.makedirs(INFLIGHT_DIR, exist_ok=True)
    t0 = _now()
    fname = f"{int(t0 * 1000)}_{os.getpid()}_{caller}.json"
    path = os.path.join(INFLIGHT_DIR, fname)
    rec = {"caller": caller, "model": model, "host": host, "prompt_chars": prompt_chars,
           "t0": round(t0, 3), "pid": pid if pid is not None else os.getpid()}
    _atomic_write(path, json.dumps(rec, ensure_ascii=False))
    return path


def inflight_end(handle):
    """呼出終了時にinflightファイルを消してjsonlへ移す(正常終了の記録)。
    handleがNone(=inflight_startを呼んでいない軽い呼出)なら何もしない。
    ファイルが既に無い(=何らかの理由で先に消えていた)場合は静かに無視する(冪等)。"""
    if not handle:
        return
    try:
        with open(handle, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return
    rec["t_end"] = round(_now(), 3)
    rec["duration_sec"] = round(rec["t_end"] - rec.get("t0", rec["t_end"]), 3)
    _append_jsonl(os.path.join(QUEUE_DIR, "ollama_inflight_done.jsonl"), rec)
    try:
        os.remove(handle)
    except FileNotFoundError:
        pass


def record_call_timing(caller, model, host, ttft_sec, ollama_done=None):
    """★AC5(正典(c)): 毎呼出のTTFTと、成功時はload/prompt_eval/eval_durationを台帳へ記録する。
    inflight閾値(INFLIGHT_MIN_PROMPT_CHARS)に関わらず全呼出が対象(probeの軽い呼出も含む)——
    inflight(占有の証言)とTTFT台帳(時間の解剖の証言)は別の目的ゆえ、同じ閾値で間引かない。
    ollama_doneはOllamaストリームのdone:true行(dict)。無ければ(timeout等)None=ロード/評価内訳は
    未取得のまま正直に記録する(門⑦: 異常時にも値が残るか)。単位はOllama生値(ナノ秒)のまま保持し
    秒への変換はしない(生値保持=丸め誤差回避・cmd_512瑕疵Dの教訓)。"""
    rec = {"ts": round(_now(), 3), "caller": caller, "model": model, "host": host,
           "ttft_sec": ttft_sec}
    if _is_synthetic():
        rec["synthetic"] = True
    if ollama_done:
        rec["total_duration_ns"] = ollama_done.get("total_duration")
        rec["load_duration_ns"] = ollama_done.get("load_duration")
        rec["prompt_eval_duration_ns"] = ollama_done.get("prompt_eval_duration")
        rec["eval_duration_ns"] = ollama_done.get("eval_duration")
    else:
        rec["total_duration_ns"] = None
        rec["load_duration_ns"] = None
        rec["prompt_eval_duration_ns"] = None
        rec["eval_duration_ns"] = None
    _append_jsonl(os.path.join(QUEUE_DIR, "ollama_call_timing.jsonl"), rec)
    return rec


def inflight_list():
    """現在queue/ollama_inflight/に残っている呼出一覧を返す(timeoutの瞬間の「身内」証言)。
    ディレクトリが無い(=一度も重い呼出が無かった)場合は空リスト(no silent caps: 空と未取得は別)。"""
    if not os.path.isdir(INFLIGHT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(INFLIGHT_DIR)):
        path = os.path.join(INFLIGHT_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            rec["_file"] = name
            out.append(rec)
        except Exception:
            # 読めぬ残骸も「在った」ことは証言する(門⑦: 異常時にも値が残るか)
            out.append({"_file": name, "_unreadable": True})
    return out


def inflight_gc(now=None, orphan_after_sec=None):
    """★cmd_519第3便(gunshi献策2): INFLIGHT_DIRに残る古いファイルを掃除する。
    ★消さない。orphan_after_sec(既定INFLIGHT_ORPHAN_AFTER_SEC=600秒)を超えたファイルだけを
    queue/ollama_inflight_orphan.jsonlへ移し、orphaned_after_secを付す(残骸こそが証言・正典)。
    600秒未満のファイル(=正常に進行中、またはinflight_endがまだ来ていないだけの正常な待機)は
    一切触らない(AC6反証照会: 正常終了直後のファイルを誤ってorphan扱いしない)。
    戻り値: 移した件数。ディレクトリが無ければ0(一度も重い呼出が無かった=正常)。"""
    if not os.path.isdir(INFLIGHT_DIR):
        return 0
    t_now = now if now is not None else _now()
    threshold = orphan_after_sec if orphan_after_sec is not None else INFLIGHT_ORPHAN_AFTER_SEC
    moved = 0
    for name in sorted(os.listdir(INFLIGHT_DIR)):
        path = os.path.join(INFLIGHT_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            # 読めぬ残骸も、ファイルの更新時刻で古さを判定して移す(門⑦: 読めぬものも証言として残す)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            age = t_now - mtime
            if age <= threshold:
                continue
            rec = {"_file": name, "_unreadable": True}
        else:
            t0 = rec.get("t0")
            if t0 is None:
                continue
            age = t_now - t0
            if age <= threshold:
                continue
        rec["orphaned_after_sec"] = round(age, 3)
        rec["_file"] = name
        try:
            _append_jsonl(INFLIGHT_ORPHAN_LOG, rec)
            os.remove(path)
            moved += 1
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return moved


# ───────────────────────── 併走診断 ─────────────────────────

def _http_json(url, timeout, method="GET", body=None):
    req = urllib.request.Request(url, data=body, method=method,
                                  headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "ok", json.load(r)
    except Exception as e:
        kind = "timeout" if _is_timeout(e) else "error"
        return kind, str(e)


def _is_timeout(e):
    import socket
    return isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower()


def probe_ps(host):
    """併走診断(i): /api/ps snapshot。timeout検知の同一瞬間に叩く。
    戻り値: (status"ok|timeout|error", data_or_errmsg)。statusは三証言の取得可否記録(門⑦)に使う。"""
    url = host.rstrip("/") + "/api/ps"
    return _http_json(url, PS_PROBE_TIMEOUT)


def probe_generate_1token(host, model):
    """併走診断(ii): 同モデルへ1token probe。戻り値: (status"ok|timeout|error", data_or_errmsg)。"""
    body = json.dumps({"model": model, "stream": False, "prompt": "ping",
                        "options": {"num_predict": 1}}).encode()
    url = host.rstrip("/") + "/api/generate"
    return _http_json(url, CO_PROBE_TIMEOUT, method="POST", body=body)


# ───────────────────────── 黒匣・判定表 ─────────────────────────

def _model_in_ps(ps_data, model):
    """ps snapshotのmodels[]にmodel(前方一致・タグ差異吸収)が在るか。無ければ載っている別名の一覧も返す。"""
    names = [m.get("name", "") for m in (ps_data or {}).get("models", [])]
    base = str(model).split(":")[0]
    present = any(base in n for n in names)
    return present, names


def judge_incident(ps_status, ps_data, co_status, co_data, model, inflight_snapshot):
    """判定表に沿ってverdictを機構自身に書く(軍師補足①: 人が書き換えない)。
    戻り値はincidentレコードの一部(verdict/observed/unobservable/details)。"""
    observed = []
    unobservable = []

    if ps_status == "ok":
        observed.append("ps")
    else:
        unobservable.append("ps")

    if inflight_snapshot is not None:
        observed.append("inflight")
    else:
        unobservable.append("inflight")

    if co_status == "ok":
        observed.append("co_probe")
    else:
        unobservable.append("co_probe")

    # host不達: ps自体が到達不能(timeout/error)なら宛先そのものに答えられぬ
    if ps_status != "ok":
        return {"verdict": "network/host down", "observed": observed, "unobservable": unobservable,
                "details": {"ps_status": ps_status, "ps_error": ps_data if ps_status != "ok" else None}}

    model_present, ps_names = _model_in_ps(ps_data, model)
    if not model_present:
        return {"verdict": "cold/eviction", "observed": observed, "unobservable": unobservable,
                "details": {"ps_names": ps_names, "evicted_by_candidates": ps_names}}

    long_running = [r for r in (inflight_snapshot or [])
                    if not r.get("_unreadable") and (time.time() - r.get("t0", time.time())) > 5]
    if long_running:
        callers = sorted({r.get("caller", "unknown") for r in long_running})
        return {"verdict": "身内の占有", "observed": observed, "unobservable": unobservable,
                "details": {"callers": callers, "inflight_count": len(inflight_snapshot or [])}}

    # 自モデル在+inflight空+併走probeも詰まる → unknown(陣外だと断定させない・正典の要)
    return {"verdict": "unknown", "observed": observed, "unobservable": unobservable,
            "details": {"co_probe_status": co_status,
                        "note": "自モデル在・自陣inflight空・併走probeも詰まった。"
                                "陣外の呼出(2nd艦隊等)は構造的に見えぬ観測の境界であり、"
                                "陣外だと断定しない(正典・三欄の門)。"}}


def record_incident(site, model, host, ttft_info=None):
    """timeout検知時に呼ぶ: 三証言を併走取得→黒匣へ1レコードで束ねて追記。
    ttft_infoはchat_server側で計測済のTTFT情報(dict、無ければNone=未取得として正直に記録)。"""
    t_incident = _now()
    ps_status, ps_data = probe_ps(host)
    co_status, co_data = probe_generate_1token(host, model)
    try:
        inflight_snapshot = inflight_list()
        inflight_read_status = "ok"
    except Exception as e:
        inflight_snapshot = None
        inflight_read_status = "error"

    judged = judge_incident(ps_status, ps_data, co_status, co_data, model, inflight_snapshot)

    rec = {
        "ts": round(t_incident, 3),
        "site": site,
        "model": model,
        "host": host,
        "verdict": judged["verdict"],
        "observed": judged["observed"],
        "unobservable": judged["unobservable"],
        "ps_probe": ps_status,
        "inflight_read": inflight_read_status,
        "co_probe": co_status,
        "ttft": ttft_info if ttft_info is not None else None,
        "details": judged["details"],
    }
    if _is_synthetic():
        rec["synthetic"] = True
    _append_jsonl(INCIDENT_LOG, rec)
    return rec
