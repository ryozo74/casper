#!/usr/bin/env python3
"""cmd_512第1便 手当1(担当A): symptom_free_status.py / replay_corpus.py の定期実行の口。

★実行主体はcron(casper_supervisor.shには載せない——supervisorは常駐プロセス生死監視の役、
周期実行の器ではない。既存holdoutがcrontab 0 4 * * 0で登録されている前例に揃える)。

やること(1回の実行につき):
  1. _holdout.lock(ファイル形式)を取得する(supervisor auto-reloadとの干渉を断つ・既存holdout作法と同型)。
  2. 対象ツール(symptom_free_status.py または replay_corpus.py)をサブプロセスで実行し、
     stdout・exit_code・所要秒を測る。
  3. reports/observation_ledger.jsonl へ1行追記する(ts/tool名/exit_code/overall/各指標の値/
     n_records/duration_sec/llm_calls)。
  4. overallがredならkaroへinbox_write.shで一報する(observation_red)。
  5. 台帳の最終行が48時間より古い(=回っていない)ことをこのスクリプト自身の実行時にも検査し、
     古ければ別途karoへ赤を送る(見張りの見張り・stale_check()・cron_stale.shから呼ばれる想定)。
  6. ロックを解放する。

Usage:
  python3 run_observation.py --tool symptom_free
  python3 run_observation.py --tool replay
  python3 run_observation.py --tool replay --synthetic   # AC2の合成赤試験専用(台帳へsynthetic:trueを立てる)
  python3 run_observation.py --stale-check                # 台帳の見張りの見張り(cronの3行相当をpythonでも提供)
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))   # multi-agent-shogun-main/
REPORTS_DIR = os.path.join(HERE, "reports")
LEDGER_PATH = os.path.join(REPORTS_DIR, "observation_ledger.jsonl")
LOCK_PATH = os.path.join(REPORTS_DIR, "_holdout.lock")
INBOX_WRITE = os.path.join(ROOT, "scripts", "inbox_write.sh")
TRACE_PATH = os.path.join(HERE, "casper_trace.jsonl")

try:
    import casper_llm_client                      # cmd_519第3便: inflight掃除+件数観測の相乗り(新cronを増やさない)
except Exception:
    casper_llm_client = None

_STALE_THRESHOLD_SEC = 48 * 3600


def _acquire_lock(who):
    """既存holdout(eval/run_holdout.py)と同じ書式(1行目=epoch秒・2行目pid・3行目who)。
    ★ディレクトリ形式は無効(casper_supervisor.sh L39は-fでファイルのみ真・過去実害の教訓)。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\n")
        f.write(f"pid={os.getpid()}\n")
        f.write(f"who={who}\n")


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def _lock_held_by_other(who):
    """他者が保持中(TTL内)ならTrueを返す(交通整理・過去のロック競合事故の教訓)。"""
    if not os.path.isfile(LOCK_PATH):
        return False
    try:
        lines = open(LOCK_PATH, encoding="utf-8").read().splitlines()
        ts = int("".join(c for c in (lines[0] if lines else "") if c.isdigit()) or "0")
        owner = next((ln.split("=", 1)[1] for ln in lines if ln.startswith("who=")), "")
    except Exception:
        return False
    if owner == who:
        return False
    age = time.time() - ts
    return age <= 1200   # supervisor側TTLと同じ20分


def _extract_json_stdout(stdout):
    """chat_server.pyのimport副作用(起動バナー行の印字)がstdout先頭に混じるため、
    最初に現れる'{'から末尾までをJSON本体として切り出す(banner行を新しい解析対象にせず、
    構造(先頭'{')で判定する)。"""
    idx = stdout.find("{")
    return stdout[idx:] if idx >= 0 else stdout


def _run_symptom_free():
    # ★瑕疵H是正(cmd_512第7便): 前回台帳行のlatest_trace_tsを子プロセスへ渡し、
    # 『traceの新しい側の増分』を直接数えさせる(累計の単純差分だとtraceローテーション
    # でcumulative nが頭打ちになりdeltaが0に落ち続ける瑕疵Hの根治)。
    prev_latest_trace_ts = _find_prev_latest_trace_ts()
    env = dict(os.environ)
    if prev_latest_trace_ts:
        env["CASPER_SYMPTOM_FREE_SINCE_TRACE_TS"] = prev_latest_trace_ts

    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, "symptom_free_status.py")],
                        capture_output=True, text=True, timeout=120, env=env)
    dt = time.time() - t0
    abstained_count = abstained_n = None
    latest_trace_ts = None
    abstained_count_delta = abstained_n_delta = None
    try:
        out = json.loads(_extract_json_stdout(p.stdout))
        sf = out.get("symptom_free", {})
        overall = sf.get("overall")
        n_records = sf.get("n_records")
        metrics = {k: v.get("value", v.get("green")) for k, v in sf.items()
                   if isinstance(v, dict) and k.startswith("S")}
        latest_trace_ts = sf.get("latest_trace_ts")
        # 瑕疵B連携(ashigaru2要請): S4のbaselineが台帳から立つには、台帳行に生の
        # abstained件数/母数が要る(rate%からの逆算は n が大きい時に丸め誤差で
        # 値がずれるため採らない・symptom_free_status.py側がS4_abstain_baseline dict内に
        # abstained_count/abstained_nの実値を載せた場合のみ、それをそのまま台帳へ転記する)。
        s4 = sf.get("S4_abstain_baseline")
        if isinstance(s4, dict):
            ac, an = s4.get("abstained_count"), s4.get("abstained_n")
            if isinstance(ac, int) and isinstance(an, int):
                abstained_count, abstained_n = ac, an
        # ★瑕疵H是正: abstained_sinceはprev_latest_trace_ts指定時のみ子プロセスが返す
        # (ts境界で直接数えた新しい側の増分。ローテーションで頭打ちにならない)。
        since = sf.get("abstained_since")
        if isinstance(since, dict):
            c, n = since.get("count"), since.get("n")
            if isinstance(c, int) and isinstance(n, int):
                abstained_count_delta, abstained_n_delta = c, n
    except Exception:
        overall, n_records, metrics = None, None, {}
    result = {
        "tool": "symptom_free_status", "exit_code": p.returncode, "duration_sec": round(dt, 3),
        "overall": overall, "n_records": n_records, "metrics": metrics, "llm_calls": 0,
        "stderr_tail": (p.stderr or "")[-2000:] if p.returncode not in (0, 1, 2) else "",
    }
    if abstained_count is not None:
        result["abstained_count"] = abstained_count
        result["abstained_n"] = abstained_n
    if latest_trace_ts:
        result["latest_trace_ts"] = latest_trace_ts
    if abstained_count_delta is not None:
        result["abstained_count_delta"] = abstained_count_delta
        result["abstained_n_delta"] = abstained_n_delta
    return result


def _run_replay():
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, "replay_corpus.py")],
                        capture_output=True, text=True, timeout=180)
    dt = time.time() - t0
    # ★replay_corpus.pyのMACHINE_SUMMARY行(安定形式・人向け文言の変更に影響されない)を読む。
    summary_line = next((ln for ln in p.stdout.splitlines() if ln.startswith("MACHINE_SUMMARY")), "")
    n_red = llm_calls = n_user_turns = None
    for tok in summary_line.split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        try:
            v = int(v)
        except ValueError:
            continue
        if k == "n_red":
            n_red = v
        elif k == "llm_calls":
            llm_calls = v
        elif k == "n_user_turns":
            n_user_turns = v
    users_line = next((ln for ln in p.stdout.splitlines() if ln.startswith("replay corpus:")), "")
    overall = None if n_red is None else ("red" if n_red else "green")
    return {
        "tool": "replay_corpus", "exit_code": p.returncode, "duration_sec": round(dt, 3),
        "overall": overall, "n_records": n_user_turns, "metrics": {"n_red_turns": n_red},
        "llm_calls": llm_calls,
        "note": users_line,
        "stderr_tail": (p.stderr or "")[-2000:] if p.returncode not in (0, 1) else "",
    }


def _run_llm_occupancy(date_str=None):
    """cmd_515手当2 層2(軍師design_decision層2をそのまま実装): casper_trace.jsonlの
    llm_calls(層1・cmd_515手当2で各turnに刻まれた推論機呼出記録)を対象日ぶん集計し、
    by_site(呼出元ごとのn/wait_sec)・timeouts_n・max_wait_secの日次summaryを返す。
    ★casper_traceは_MAX_LINES=5000でローテートし古い行が消える(cmd_512瑕疵Hと同型)ため、
    この日次集計をobservation_ledger.jsonlへ落とすことで「一週間前の占有」を読めるようにする。
    date_str未指定時は前日(JST calendar day相当・ここでは実行時のlocaltime基準)を対象とする
    (cronで日次実行する運用を想定・当日はまだ全件揃っていないため)。"""
    if date_str is None:
        date_str = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    by_site = {}
    timeouts_n = 0
    max_wait_sec = 0.0
    n_lines = 0
    try:
        with open(TRACE_PATH, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                ts = rec.get("ts") or ""
                if ts[:10] != date_str:
                    continue
                calls = rec.get("llm_calls") or []
                if not calls:
                    continue
                n_lines += 1
                for c in calls:
                    site = c.get("site") or "unknown"
                    wait = c.get("wait_sec") or 0
                    b = by_site.setdefault(site, {"n": 0, "wait_sec": 0.0})
                    b["n"] += 1
                    b["wait_sec"] = round(b["wait_sec"] + wait, 3)
                    if c.get("outcome") == "timeout":
                        timeouts_n += 1
                    if wait > max_wait_sec:
                        max_wait_sec = wait
    except FileNotFoundError:
        pass
    result = {
        "tool": "llm_occupancy", "exit_code": 0, "duration_sec": 0.0,
        "overall": None,   # ★集計であり合否判定を持たぬ(symptom_free/replayと違いgreen/red対象外)
        "n_records": n_lines, "metrics": {"by_site": by_site},
        "llm_calls": sum(b["n"] for b in by_site.values()),
        "date": date_str, "by_site": by_site,
        "timeouts_n": timeouts_n, "max_wait_sec": round(max_wait_sec, 3),
    }
    # ★cmd_519第3便(gunshi献策3・4): 新cronを増やさず既存のllm_occupancy日次集計へ相乗り。
    # inflightの掃除(残骸をorphan台帳へ移す・消さない)と、掃除直前の件数観測を同時に行う。
    # 平常時はinflight_maxがほぼ0のはずゆえ、「常に多い」ことが占有の兆候として読める(正典)。
    if casper_llm_client:
        try:
            inflight_before = len(casper_llm_client.inflight_list())
        except Exception:
            inflight_before = None
        try:
            orphan_n = casper_llm_client.inflight_gc()
        except Exception:
            orphan_n = None
        result["inflight_max"] = inflight_before
        result["orphan_n"] = orphan_n
    return result


def _find_prev_abstained_totals(ledger_path=None):
    """瑕疵G是正(cmd_512第6便): 台帳の最終行(tool=symptom_free_status かつ
    abstained_count/abstained_nを持つ行)の値を探して返す((count, n)のtuple、無ければNone)。
    ★瑕疵H是正後は直接のdelta算出には使わない(traceローテーションで頭打ちになるため)。
    互換のため残す(他所からの参照は無いが、台帳の生累計値を辿る手段として)。"""
    path = ledger_path or LEDGER_PATH
    if not os.path.isfile(path):
        return None
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("tool") != "symptom_free_status":
            continue
        ac, an = rec.get("abstained_count"), rec.get("abstained_n")
        if isinstance(ac, int) and isinstance(an, int):
            return ac, an
    return None


def _find_prev_latest_trace_ts(ledger_path=None):
    """★瑕疵H是正(cmd_512第7便): 台帳の最終行(tool=symptom_free_status かつ
    latest_trace_tsを持つ行)のlatest_trace_tsを探して返す(無ければNone=初回)。
    このtsをsymptom_free_status.pyへ渡し、『それより新しいtrace recordだけ』を
    数えさせることで、traceがローテーションで古い側を落としてもdeltaが頭打ちに
    ならない(cumulative差分でなくts境界の直接カウントへ切替)。"""
    path = ledger_path or LEDGER_PATH
    if not os.path.isfile(path):
        return None
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("tool") != "symptom_free_status":
            continue
        ts = rec.get("latest_trace_ts")
        if isinstance(ts, str) and ts:
            return ts
    return None


def _append_ledger(row):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    # ★瑕疵H是正(cmd_512第7便): deltaは_run_symptom_free()がsymptom_free_status.py
    # 子プロセスから受け取ったabstained_since(ts境界で直接数えた新しい側の増分)を
    # そのままrowへ載せている(_run_symptom_free参照)。ここでの再計算は不要——旧実装
    # (現在値-前回値のcumulative差分)はtraceローテーションでcumulative nが頭打ちに
    # なるとdelta=0が続き、S4が永久unknownへ落ちる瑕疵Hの原因だったため廃止した。
    #
    # ★軍師戦略review(subtask_512_strategy6・design_decision(b))は今回も踏襲: 前回行が
    # 無い(初回実行・prev_latest_trace_tsが取れない)時はabstained_sinceが返らずdeltaは
    # 刻まれない。台帳が育つまでS4はunknownを名乗り続けるのが正しい姿(AC-E4と同じ作法)。
    #
    # ★n_delta<=0の行は読み手側(symptom_free_status.py _load_window_from_ledger)が
    # n<=0判定で自動的に窓集計から除外する(design_decision(a)は健在・変更なし)。
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _notify_red(row, synthetic):
    prefix = "【試験】" if synthetic else ""
    msg = (f"{prefix}observation_ledger赤: tool={row['tool']} overall={row['overall']} "
           f"exit_code={row['exit_code']} ts={row['ts']} duration_sec={row['duration_sec']}")
    subprocess.run(["bash", INBOX_WRITE, "karo", msg, "observation_red", "cron_observation"],
                    check=False)


_TOOL_FN = {
    "symptom_free": _run_symptom_free,
    "replay": _run_replay,
    "llm_occupancy": _run_llm_occupancy,
}
# ★llm_occupancy(cmd_515手当2層2)はsymptom_free/replayと異なり合否判定を持たぬ純粋な日次集計
# (推論機を叩いた回数/待ち秒数の内訳)ゆえ、overall=Noneを「判定不能=赤」として通知させない
# (_notify_broken誤発火防止・毎日赤通知が飛ぶ事故を防ぐ)。
_NO_OVERALL_TOOLS = {"llm_occupancy"}


def run_one(tool, synthetic=False, date_str=None):
    who = f"cron_run_observation_{tool}"
    if _lock_held_by_other(who):
        print(f"[run_observation] ロック競合(他者保持中)ゆえ今回はスキップ: tool={tool}", file=sys.stderr)
        sys.exit(3)
    _acquire_lock(who)
    try:
        result = _run_llm_occupancy(date_str) if tool == "llm_occupancy" else _TOOL_FN[tool]()
    finally:
        _release_lock()

    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": result["tool"],
        "exit_code": result["exit_code"],
        "overall": result["overall"],
        "metrics": result.get("metrics", {}),
        "n_records": result.get("n_records"),
        "duration_sec": result["duration_sec"],
        "llm_calls": result.get("llm_calls"),
    }
    if synthetic:
        row["synthetic"] = True
    if result.get("note"):
        row["note"] = result["note"]
    if result.get("stderr_tail"):
        row["stderr_tail"] = result["stderr_tail"]
    if "abstained_count" in result:
        row["abstained_count"] = result["abstained_count"]
        row["abstained_n"] = result["abstained_n"]
    if result.get("latest_trace_ts"):
        row["latest_trace_ts"] = result["latest_trace_ts"]
    if "abstained_count_delta" in result:
        row["abstained_count_delta"] = result["abstained_count_delta"]
        row["abstained_n_delta"] = result["abstained_n_delta"]
    if tool == "llm_occupancy":
        row["date"] = result["date"]
        row["by_site"] = result["by_site"]
        row["timeouts_n"] = result["timeouts_n"]
        row["max_wait_sec"] = result["max_wait_sec"]
        if "inflight_max" in result:
            row["inflight_max"] = result["inflight_max"]
        if "orphan_n" in result:
            row["orphan_n"] = result["orphan_n"]
    _append_ledger(row)

    if tool in _NO_OVERALL_TOOLS:
        pass   # ★合否判定なし・赤通知経路を通さぬ(上記コメント参照)
    elif row["overall"] == "red":
        _notify_red(row, synthetic)
    elif row["overall"] is None or row["exit_code"] not in (0, 1, 2):
        _notify_broken(row, synthetic)

    print(json.dumps(row, ensure_ascii=False, indent=1))
    return row


def _notify_broken(row, synthetic):
    """瑕疵A是正: overallがNone(判定不能=クラッシュ/traceback/kill/MACHINE_SUMMARY欠落)、
    またはexit_codeが想定外(0/1/2以外、例: 137=SIGKILL)の時も赤として届ける。
    overall=="red"一箇所のみを条件としていた従来経路は「落ちた」を「静かに台帳へnull行を書くだけ」
    にしてしまい、誰にも気づかれずに死に続けられる穴があった(将軍実測・台帳1行目11:11:35で現に発生)。"""
    prefix = "【試験】" if synthetic else ""
    msg = (f"{prefix}observation_ledger異常終了: tool={row['tool']} overall={row['overall']} "
           f"exit_code={row['exit_code']} ts={row['ts']} duration_sec={row['duration_sec']}"
           f"(判定不能またはexit_code想定外・落ちた可能性)")
    subprocess.run(["bash", INBOX_WRITE, "karo", msg, "observation_red", "cron_observation"],
                    check=False)


def stale_check():
    """★見張りの見張り: 台帳の最終行のtsが48時間より古ければkaroへ赤。
    「回っていないこと」自体が観測できるようにする(cron自体が死んだ時に一番静かになる、
    という設計の穴を塞ぐ)。

    瑕疵A是正: 「最終行のts」だけでなく「最後にoverallが非nullだった行のts」も見る。
    落ちた実行もoverall=nullの行を台帳に書き足すため、最終行tsだけを見ていると
    nullの行が量産され続けても最終行tsは更新され続け、48時間の沈黙判定が永久に発火しない
    (real overallが立たなくなっていても「回っている」ように見えてしまう)。"""
    if not os.path.isfile(LEDGER_PATH):
        _notify_stale("台帳ファイル自体が存在しない(一度も実行されていない可能性)")
        return
    try:
        last_line = None
        last_non_null_line = None
        with open(LEDGER_PATH, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                last_line = ln
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if rec.get("overall") is not None:
                    last_non_null_line = ln
        if not last_line:
            _notify_stale("台帳が空(行が一つも無い)")
            return
        last = json.loads(last_line)
        ts_str = last.get("ts", "")
        last_epoch = time.mktime(time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S"))
        age = time.time() - last_epoch
        if age > _STALE_THRESHOLD_SEC:
            _notify_stale(f"台帳最終行が{age/3600:.1f}時間前(48時間超)のまま更新されていない"
                          f"(最終行ts={ts_str})")
            return

        if not last_non_null_line:
            _notify_stale("台帳に overall が非null の行が一つも無い(判定不能・クラッシュのみが記録され続けている)")
            return
        last_non_null = json.loads(last_non_null_line)
        nn_ts_str = last_non_null.get("ts", "")
        nn_epoch = time.mktime(time.strptime(nn_ts_str[:19], "%Y-%m-%dT%H:%M:%S"))
        nn_age = time.time() - nn_epoch
        if nn_age > _STALE_THRESHOLD_SEC:
            _notify_stale(f"最後にoverallが非nullだった行が{nn_age/3600:.1f}時間前(48時間超)のまま"
                          f"(最終行tsは更新され続けているが判定不能行のみが続いている・ts={nn_ts_str})")
    except Exception as e:
        _notify_stale(f"台帳の読取に失敗: {e!r}")


def _notify_stale(detail):
    msg = f"【見張りの見張り・赤】observation_ledger.jsonlが回っていない: {detail}"
    subprocess.run(["bash", INBOX_WRITE, "karo", msg, "observation_red", "cron_observation"],
                    check=False)
    print(f"[stale_check] {msg}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", choices=["symptom_free", "replay", "llm_occupancy"])
    ap.add_argument("--date", help="llm_occupancy専用: 集計対象日(YYYY-MM-DD)。未指定は前日。")
    ap.add_argument("--synthetic", action="store_true",
                     help="AC2合成赤試験用。台帳にsynthetic:trueを立て、karo通知に【試験】を明記する。")
    ap.add_argument("--stale-check", action="store_true")
    args = ap.parse_args()

    if args.stale_check:
        stale_check()
        return
    if not args.tool:
        ap.error("--tool か --stale-check のいずれかが必要")
    row = run_one(args.tool, synthetic=args.synthetic, date_str=args.date)
    if args.tool == "llm_occupancy":
        return   # ★合否判定を持たぬ集計ゆえoverall=Noneは正常(exit 0のまま・判定不能とは別物)
    # 補足4是正: 対象ツールが赤/判定不能でもrun_observation.py自身は常にexit 0で終わっていた
    # (cronのシェル層からは常に成功に見える)。台帳が真実源ゆえ実害は小さいが、対象ツールの結果を
    # 自身のexit codeへ伝播させる(green=0 / red=1 / 判定不能=2)。
    if row["overall"] == "red":
        sys.exit(1)
    if row["overall"] is None:
        sys.exit(2)


if __name__ == "__main__":
    main()
