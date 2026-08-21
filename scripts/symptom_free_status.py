#!/usr/bin/env python3
"""cmd_511第3便 AC11/AC12: 無症状の定義S1〜S5を既存traceから機械的に読む。

★掟(軍師戦略review point_a/b・当陣の掟「服従でなく機構で強制」): 新しい判定機構・新しい
台帳を作らず、既存の単一ソース(casper_trace.jsonl の decision_record.declines/population_n・
casper_breaker.py の breaker.json)をそのまま読むだけに徹する。

S1(再打鍵0件)      : declines中 mechanism=="retry_detected" の件数。0件で緑。
S2(検問差替の傾向)  : trace_stats()のguarded_claim日次件数。「増加傾向のみ赤」——単発の
                      発火自体は正常動作(是正が効いている証)であり、日次件数が単調増加を
                      続ける時だけ赤とする(是正が追いついていない兆候)。
S3(母集合なき不在断言): declines中 mechanism にpopulation不在を示す種別が無いこと、かつ
                      population_n==0で完了したturnが無いことの組。現状の機構では
                      population_n>=0が常に明示されるため(existence_digest/dm_threads_digest
                      が母集合ヘッダを機構が書く設計・cmd_510/511)、母集合を示さぬ不在断言は
                      構造的に発生しない。ここでは「population_nがNoneのまま(=数えていない)
                      turnが無いこと」を機械的に検査する。
S4(不能出口率baseline以下): trace_stats().checks['abstained']のrate。baselineは既存の
                      直近90件平均(初回はNone=判定不能=赤とせず「未確定」)。
S5(人間の申告1件で即赤): 台帳より人が優先。本モジュールは自動判定を持たず、人間申告用の
                      フラグファイル(reports/human_report_red.flag)の有無だけを見る——
                      申告があれば無条件で赤にする一票拒否権。
S6(規則側/LLM側の食い違い0件): cmd_512第4便申し送り1是正。replay_corpus.pyのcheck_llm_divergence()
                      が書く台帳(reports/casper_howto_llm_divergence.jsonl)の最終行を読み、
                      n_diverged>0なら赤。台帳未生成(--live-llm未実行)なら判定不能(Unknown)。
                      新しい通知経路は作らず既存の赤経路(run_observation.py→karo)に相乗り。
                      ★cmd_512第5便(軍師QC3是正): 最終行のtsが14日(週次cron2回分)を超えて
                      古ければ判定不能(Unknown)とする(S4のD-4と同じ流儀・_parse_ledger_ts流用)。
                      台帳が凍りついたまま古い緑/赤を名乗り続ける穴を塞ぐ。

★AC12: 推論機(.139)復帰判断の二条件AND。
  条件A: casper_breaker.pyの台帳(breaker.json)で gen:192.168.44.119:11434 が
         連続72h green(=stateがgreenのまま72h以上経過。updated/opened_atのみでは
         「いつからgreenか」は直接わからぬため、fails==0継続をgreen開始の代理指標とせず、
         ★正直に「現状のbreaker.jsonは連続green時間を保持しない」ことを明示し、
         判定不能はUnknown(赤でも緑でもない)として扱う——捏造しない。
  条件B: z8a実経路(.119)のp95がしきい値を超過(=処理が遅く、より重い.139へ切り替える
         動機が生じている)。trace_stats().latency['p90']を代理指標として使う
         (p95相当の値がtrace_statsに無いため、既存のp90を保守的な代理として使い、
         その旨を出力に明記する——新しい統計機構は作らない)。

Usage: python3 symptom_free_status.py
"""
import datetime
import json
import os
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_server as C
import casper_breaker as B

HUMAN_REPORT_FLAG = os.path.join(HERE, "reports", "human_report_red.flag")
# cmd_512第4便申し送り1是正: check_llm_divergence()(replay_corpus.py)が書く台帳の読み手が
# 居らず、n_divergedが増えても誰にも届かなかった穴を塞ぐ。新しい通知経路は作らず、
# 既存のS1〜S5と同じ枠(symptom_free_report→run_observation.pyの赤経路)に載せるだけ。
DIVERGENCE_LEDGER = os.path.join(HERE, "reports", "casper_howto_llm_divergence.jsonl")

# AC12条件B: z8a実経路のp95閾値(ms)。casper_breaker.pyのslow_ms_for("gen:...")と同じ値を
# 単一ソースとして流用する(新しいしきい値を別途持たない)。
_P95_THRESHOLD_MS = B.slow_ms_for("gen:192.168.44.119:11434")

_GEN_119_KEY = "gen:192.168.44.119:11434"
_GEN_139_KEY = "gen:192.168.44.139:11434"


# ★瑕疵H是正(cmd_512第7便): casper_trace.pyの_MAX_LINES(traceファイルの保持量)と
# 本読み込みlimitを同値のまま結合させない(将来どちらかを変更する際に意図せず連動しない
# よう、別名の定数として分離する・軍師申し送り)。値は現状一致するが、意味的には
# 無関係(片方は「traceに何行残すか」、もう片方は「1回の判定で何行まで読むか」)。
_TRACE_READ_LIMIT = 5000


def _load_trace_records(limit=None):
    limit = limit if limit is not None else _TRACE_READ_LIMIT
    recs = []
    try:
        tr = C.casper_trace.TRACE if C.casper_trace else os.path.join(HERE, "casper_trace.jsonl")
        lines = open(tr, encoding="utf-8").read().splitlines()[-limit:]
    except Exception:
        lines = []
    for ln in lines:
        try:
            recs.append(json.loads(ln))
        except Exception:
            pass
    return recs


def _latest_trace_ts(recs):
    """recs中の最大ts文字列を返す(無ければNone)。traceのts書式(naive・秒精度の
    isoformat文字列)は文字列比較がそのまま時系列比較になる形式のため、パース不要。"""
    ts_values = [str(r.get("ts")) for r in recs if r.get("ts")]
    return max(ts_values) if ts_values else None


def _count_abstained_since(recs, since_ts):
    """★瑕疵H是正: recs中でts > since_ts(前回実行時点のtrace最新ts)の行だけを数える。
    traceがローテーションで古い側(先頭)を落としても、『新しい側の増分』を文字列比較の
    ts境界で直接数えるため、cumulative比較(現在値-前回値)のようにローテーションで
    頭打ちにならない。since_ts未指定(初回)はNoneを返す(呼び出し側がdeltaを刻まない)。"""
    if since_ts is None:
        return None
    count = n = 0
    for r in recs:
        ts = r.get("ts")
        if not ts or str(ts) <= since_ts:
            continue
        n += 1
        if r.get("abstained"):
            count += 1
    return {"count": count, "n": n}


def s1_retry_zero(recs):
    """S1: declines中 retry_detected の件数。0件で緑。"""
    n = 0
    for r in recs:
        for d in ((r.get("decision_record") or {}).get("declines") or []):
            if d.get("mechanism") == "retry_detected":
                n += 1
    return {"metric": "retry_detected_count", "value": n, "green": n == 0}


_S2_LEVEL_RATIO = 2.0       # 条件1(水準): 直近日が基準期間平均の何倍を超えたら赤か
_S2_PLATEAU_MIN_AVG = 5.0   # 条件2(高止まり): 基準期間平均がこの値未満なら「低水準」とみなし赤にしない


def _s2_trend_from_series(series):
    """手当3: series(guarded_claim日次件数の並び)を直接受け取り、三条件ORで赤/緑を判定する。
    ★三つの独立した赤条件のOR(単一式で全部を捕らえようとしない・軍師献策)。
    条件1(水準): 直近日が基準期間(それ以前の日)平均の_S2_LEVEL_RATIO倍を超える→赤。
    条件2(高止まり): 直近7日全日が基準期間平均以上、かつ平均が_S2_PLATEAU_MIN_AVG以上→赤。
    条件3(単調): 全隣接ペア非減少かつ最終>最初(現行踏襲・退行防止で残す)。"""
    if len(series) < 2:
        return {"red": False, "level": False, "plateau": False, "monotonic": False,
                "baseline_avg": None}
    latest = series[-1]
    baseline = series[:-1]
    baseline_avg = sum(baseline) / len(baseline) if baseline else None

    level = bool(baseline_avg is not None and baseline_avg > 0 and latest > baseline_avg * _S2_LEVEL_RATIO)

    plateau = bool(
        baseline_avg is not None and baseline_avg >= _S2_PLATEAU_MIN_AVG
        and len(series) >= 2 and all(v >= baseline_avg for v in series)
    )

    monotonic = (
        len(series) >= 3
        and all(b >= a for a, b in zip(series, series[1:]))
        and series[-1] > series[0]
    )

    return {"red": bool(level or plateau or monotonic), "level": level, "plateau": plateau,
            "monotonic": monotonic, "baseline_avg": baseline_avg}


def s2_guard_trend(recs, days=7):
    """S2: guarded_claimの日次件数の傾向。手当3で「増加傾向」を三条件OR(急増/高止まり/単調)に
    建て直す。AC5: 要求日数と実測日数の乖離(欠測)を出力自身が白状する(no silent caps)。
    days_with_data < 3 の時は傾向判定をunknownとする(母集合なき断言をしない・手当2の三値に乗る)。"""
    daily = {}
    for r in recs:
        day = str(r.get("ts") or "")[:10]
        if not day:
            continue
        b = daily.setdefault(day, {"n": 0, "guarded_claim": 0})
        b["n"] += 1
        if r.get("guarded_claim"):
            b["guarded_claim"] += 1

    all_days_sorted = sorted(daily.keys())
    ordered_days = all_days_sorted[-days:]
    series = [daily[d]["guarded_claim"] for d in ordered_days]

    days_with_data = len(ordered_days)
    if ordered_days:
        first = datetime.date.fromisoformat(ordered_days[0])
        last = datetime.date.fromisoformat(ordered_days[-1])
        calendar_span_days = (last - first).days + 1
        all_calendar_days = {(first + datetime.timedelta(days=i)).isoformat()
                              for i in range((last - first).days + 1)}
        missing_days = sorted(all_calendar_days - set(ordered_days))
    else:
        calendar_span_days = 0
        missing_days = []

    ac5 = {"days_requested": days, "days_with_data": days_with_data,
           "calendar_span_days": calendar_span_days, "missing_days": missing_days}

    if days_with_data < 3:
        return {"metric": "guarded_claim_daily_series", "days": ordered_days, "series": series,
                "green": None, "note": "days_with_data<3ゆえ傾向判定は不能(母集合なき断言を避ける)",
                **ac5}

    trend = _s2_trend_from_series(series)
    return {"metric": "guarded_claim_daily_series", "days": ordered_days, "series": series,
            "green": not trend["red"], "trend_detail": trend, **ac5}


def s3_population_always_shown(recs):
    """S3: 完了したturnでpopulation_nがNone(=数えていない)のまま母集合なき不在断言が
    起き得る状態のturnが無いこと。既存機構(existence_digest/dm_threads_digest)は
    population_nを常にintで明示する設計ゆえ、Noneが現れること自体が退行の兆候。
    ★population_nキー自体はcmd_508(decision_record新設)で追加されたもので、それ以前の
    traceには構造的に存在しない(退行ではなく単なる旧世代データ)。ここを赤に数えると
    「昔から見ていない」を「壊れた」と誤読する——decision_recordキー自体が在るrecordだけを
    母集合とし、その中でpopulation_nが欠けているものだけを検査する(fail-safe: 対象外は
    数えない・cmd_510 replay_corpus._EXPECTATION_CHECKS未知機構の扱いと同じ作法)。"""
    scoped = [r for r in recs if "decision_record" in r]
    n_missing = 0
    for r in scoped:
        dr = r.get("decision_record") or {}
        if "population_n" not in dr or dr.get("population_n") is None:
            n_missing += 1
    return {"metric": "population_n_missing_count", "value": n_missing,
            "n_scoped": len(scoped), "green": n_missing == 0}


OBSERVATION_LEDGER = os.path.join(HERE, "reports", "observation_ledger.jsonl")

# ★瑕疵D是正(cmd_512第4便・軍師addendum D-2): S4許容幅の二本立て。base_rateが0に近い時
# (実測0.4pt前後)は相対値だけでは効かぬため、絶対値と相対値の大きい方を採る(両建て)。
# 根拠はs4_abstain_rate_within_baseline()内のコメント参照。
_S4_TOLERANCE_ABS_PT = 0.5
_S4_TOLERANCE_REL_MULT = 1.5

# ★瑕疵D是正(cmd_512第4便・軍師addendum D-4): 採用する台帳行の古さの上限(日数)。
# これを超える行しか無ければbaseline=Noneでunknownを名乗る(陳腐化台帳を黙って使わない)。
_S4_BASELINE_MAX_AGE_DAYS = 30

# ★瑕疵E是正(cmd_512第5便・軍師design_decision/answer_a確定): baselineを「直近90行の
# 自分」から「窓分割(直近7日 vs 30〜90日前の古い窓)」へ改める。「自分の直近を基準に自分を
# 測る器は、緩慢な悪化に必ず盲である」(将軍・掟二)——基準が対象に追随すれば差が永久に
# 開かぬ。窓を時間的に引き離すことでdriftを窓差として顕在化させる(軍師実測: 感度4系列/
# 特異度4系列すべて期待どおり)。
_S4_RECENT_WINDOW_LO_DAYS = 0
_S4_RECENT_WINDOW_HI_DAYS = 7
_S4_OLD_WINDOW_LO_DAYS = 30
_S4_OLD_WINDOW_HI_DAYS = 90

# ★瑕疵E是正・補助(軍師answer_a: ③を従として併用): 窓分割は比較の器ゆえ、悪化が古い窓
# にまで及んだ後(90日以上かけて茹だれば古窓も一緒に茹だる)はまた盲になる。「どれだけ緩慢
# でも、この線を越えたら赤」という比較に依らぬ絶対の線を置く。値は軍師推奨(現状0.4%の
# 12倍・cmd_494導入時水準からの明白な逸脱)を採用。
_S4_ABSOLUTE_CAP_PCT = 5.0

# ★瑕疵G是正(cmd_512第6便・軍師strategy_report_6 risk_notes確定): 増分(delta)方式は
# 一行あたりのnが小さくなるため率の揺らぎが増える(実データの日量は中央値62・最少2件と
# 変動が大きい)。窓合算後のnがこの値未満なら、母集合が痩せすぎているとしてunknownを
# 名乗る(母集合が痩せた時に断言せぬのは当陣の一貫した掟)。
_S4_MIN_WINDOW_N = 50


def _parse_ledger_ts(ts_str):
    """台帳行のts文字列(例: '2026-08-19T11:55:24+0900')をdatetime(aware)へ。読めなければNone。"""
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        return datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _load_window_from_ledger(ledger_path=None, age_lo_days=0, age_hi_days=90, limit=5000, _now=None):
    """台帳(observation_ledger.jsonl)の行のうち、古さが[age_lo_days, age_hi_days)の範囲に
    入るものだけを合算して返す(軍師answer_b献策: 読み手を増やさず窓を引数化する)。
    自己参照(recs自身の前半)は s4 のdocstringが禁じるため使わない。

    ★瑕疵E是正(cmd_512第5便): 従来の_load_baseline_from_ledger(直近90行を一括合算)を
    窓引数化した形。呼び出し側が窓を変えて2回呼ぶことで「直近7日 vs 30〜90日前」の
    窓分割を実現する(軍師design_decision/answer_a確定)。

    ★D-1(cmd_512第4便由来・踏襲): base_rate=Σcount/Σnで合算する(直近1行を段差検出器の
    ように即returnしない)。
    ★D-4(cmd_512第4便由来・踏襲): 行のtsがage_hi_daysを超えて古ければ採用しない
    (陳腐化台帳を黙って使わない)。age_lo_days未満(=窓より新しい行)も同様に除外する。
    有効な行が1つも無ければNoneを返す(呼び出し側がunknownを名乗る・捏造しない)。

    ★S2の作法を借りる(軍師answer_b (2)): no silent capsとして days_with_data
    (該当窓内で行が存在した日数のユニーク数)も併せて返す。

    ★瑕疵G是正(cmd_512第6便・軍師strategy_report_6 design_decision確定): 台帳の
    abstained_n/abstained_count は直近5000件trace累計(日ごとの区間集計ではない)ため、
    連続する行を単純合算すると同じ母集合を何重にも重ねて数えてしまう。行に
    abstained_count_delta/abstained_n_delta(前回実行以降の増分)がある行のみを合算する。
    ★(design_decision (c)): delta列を持たぬ旧行(移行前)は窓集計から★除外する
    (累計と増分を混ぜて合算すれば数が壊れる・フォールバックはしない)。
    ★(design_decision (a)): n_delta<=0(trace上限到達等で増分が無い/負)の行は除外する
    (0で埋めて捏造しない)。
    ★(no silent caps・design_decision (d)): 採用した行数(rows_used)と、delta列を
    持たぬゆえ除外した行数(rows_excluded_no_delta)を両方返す。

    戻り値: {"count": 総abstained数, "n": 総turn数, "latest_ts": 採用行中最新のts文字列,
             "rows_used": 採用した行数(delta列を持つ有効行), "days_with_data": 該当日数の
             ユニーク数, "rows_excluded_no_delta": delta列が無く除外した行数}
             または該当行が無ければNone。"""
    path = ledger_path or OBSERVATION_LEDGER
    if not os.path.exists(path):
        return None
    try:
        lines = open(path, encoding="utf-8").read().splitlines()[-limit:]
    except Exception:
        return None
    now = _now or datetime.datetime.now(datetime.timezone.utc)
    total_n = 0
    total_abstained = 0
    latest_ts = None
    latest_dt = None
    rows_used = 0
    rows_excluded_no_delta = 0
    days_seen = set()
    for ln in lines:
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("tool") != "symptom_free_status":
            continue
        if "abstained_count" not in rec:
            continue
        ts_dt = _parse_ledger_ts(rec.get("ts"))
        if ts_dt is None:
            continue
        age_days = (now - ts_dt).total_seconds() / 86400.0
        if age_days < age_lo_days or age_days >= age_hi_days:
            continue
        # ★design_decision (c): delta列を持たぬ旧行は窓の該当範囲内であっても除外する。
        n_delta = rec.get("abstained_n_delta")
        c_delta = rec.get("abstained_count_delta")
        if not isinstance(n_delta, int) or not isinstance(c_delta, int):
            rows_excluded_no_delta += 1
            continue
        # ★design_decision (a): n_delta<=0(trace上限到達・初回除外済等)の行は除外。
        if n_delta <= 0:
            continue
        total_n += n_delta
        total_abstained += c_delta
        rows_used += 1
        days_seen.add(str(rec.get("ts"))[:10])
        if latest_dt is None or ts_dt > latest_dt:
            latest_dt = ts_dt
            latest_ts = rec.get("ts")
    if total_n <= 0:
        return None
    return {"count": total_abstained, "n": total_n, "latest_ts": latest_ts,
            "rows_used": rows_used, "days_with_data": len(days_seen),
            "rows_excluded_no_delta": rows_excluded_no_delta}


def _load_baseline_from_ledger(ledger_path=None, limit=90, max_age_days=None, _now=None):
    """後方互換の薄いラッパ(軍師answer_b: 現行の_load_baseline_from_ledgerは
    _load_window_from_ledgerを(0, max_age_days)で呼ぶだけの形にできる)。
    旧テスト・旧呼び出し元(直近90行合算・単一baseline)のために残す。"""
    max_age = max_age_days if max_age_days is not None else _S4_BASELINE_MAX_AGE_DAYS
    return _load_window_from_ledger(ledger_path=ledger_path, age_lo_days=0, age_hi_days=max_age,
                                     limit=limit, _now=_now)


def s4_abstain_rate_within_baseline(recs, recent_window=None, old_window=None, _now=None):
    """S4: 不能出口率(abstained rate)が「緩慢に悪化していない」か。

    ★瑕疵E是正(cmd_512第5便・軍師design_decision/answer_a確定): baselineを「直近90行の
    自分」から「窓分割(直近7日 vs 30〜90日前の古い窓)」へ改めた。従来はrecs(呼び出し時点の
    直近90行合算)をbaselineとして使っていたため、baseline自体が悪化に追随し、緩慢な悪化
    (0.4%→10.0%を120日等)を許容幅が常に飲み込んでいた(将軍実測・軍師追認、一度も赤に
    ならず)。窓を時間的に引き離すことでdriftを窓差として顕在化させる(掟二: 自分の直近を
    基準に自分を測る器は緩慢な悪化に必ず盲である)。

    recent_window/old_windowは{"count": int, "n": int, "latest_ts": str|None,
    "days_with_data": int}の辞書(_load_window_from_ledger()の戻り値そのもの)。未指定なら
    台帳から自動で引く(_S4_RECENT_WINDOW_*/_S4_OLD_WINDOW_*の既定窓)。

    判定式(軍師design_decision確定):
      recent_rate = Σ(直近7日のabstained_count) / Σ(直近7日のabstained_n) × 100
      old_rate    = Σ(30〜90日前のabstained_count) / Σ(30〜90日前のabstained_n) × 100
      tolerance   = max(_S4_TOLERANCE_ABS_PT, old_rate × (_S4_TOLERANCE_REL_MULT - 1.0))
      green       = recent_rate <= old_rate + tolerance
    ★古い窓(30〜90日前)に行が無い場合はgreen=None(unknown)——決して緑と名乗らせない
    (軍師design_decision最重要点: 台帳が育つまで(現状13行=約1日分・古窓が埋まるのは
    約30日後)S4はunknownを名乗り続けるのが正しい姿)。

    ★補助として絶対上限を併用する(軍師answer_a: ③を従として併用)。recent_rateが
    _S4_ABSOLUTE_CAP_PCTを超えれば、窓の充足に関わらず無条件で赤とする(窓分割は比較の器
    ゆえ、悪化が古い窓にまで及んだ後はまた盲になるための保険)。

    ★瑕疵G是正(cmd_512第6便・軍師risk_notes確定): 増分方式は一行あたりのnが小さくなり
    率の揺らぎが増えるため、窓合算後のnが_S4_MIN_WINDOW_N未満(母集合が痩せすぎ)なら
    green=None(unknown)を名乗る(母集合が痩せた時に断言せぬのは当陣の一貫した掟)。"""
    n = len(recs) or 1
    abstained = sum(1 for r in recs if r.get("abstained"))
    rate = abstained / n * 100
    result = {"metric": "abstain_rate_pct", "value": round(rate, 1), "value_raw": rate,
              "abstained_count": abstained, "abstained_n": n}

    if recent_window is None:
        recent_window = _load_window_from_ledger(
            age_lo_days=_S4_RECENT_WINDOW_LO_DAYS, age_hi_days=_S4_RECENT_WINDOW_HI_DAYS, _now=_now)
    if old_window is None:
        old_window = _load_window_from_ledger(
            age_lo_days=_S4_OLD_WINDOW_LO_DAYS, age_hi_days=_S4_OLD_WINDOW_HI_DAYS, _now=_now)

    window_info = {
        "recent_window_days": [_S4_RECENT_WINDOW_LO_DAYS, _S4_RECENT_WINDOW_HI_DAYS],
        "old_window_days": [_S4_OLD_WINDOW_LO_DAYS, _S4_OLD_WINDOW_HI_DAYS],
        "recent_count": (recent_window or {}).get("count"), "recent_n": (recent_window or {}).get("n"),
        "recent_ts": (recent_window or {}).get("latest_ts"),
        "recent_days_with_data": (recent_window or {}).get("days_with_data"),
        "old_count": (old_window or {}).get("count"), "old_n": (old_window or {}).get("n"),
        "old_ts": (old_window or {}).get("latest_ts"),
        "old_days_with_data": (old_window or {}).get("days_with_data"),
        "absolute_cap_pct": _S4_ABSOLUTE_CAP_PCT,
        "min_window_n": _S4_MIN_WINDOW_N,
        "recent_rows_excluded_no_delta": (recent_window or {}).get("rows_excluded_no_delta"),
        "old_rows_excluded_no_delta": (old_window or {}).get("rows_excluded_no_delta"),
    }

    if not recent_window or not recent_window.get("n"):
        return {**result, **window_info, "recent_rate": None, "old_rate": None,
                "tolerance_pt": None, "green": None,
                "note": "直近窓(0〜7日)にデータが無いゆえ判定不能"}

    # ★瑕疵G是正・軍師risk_notes確定: 直近窓の母集合が痩せすぎている(delta方式でnが
    # 小さくなり率の揺らぎが増える)時はunknownを名乗る(捏造しない・断言しない)。
    if recent_window["n"] < _S4_MIN_WINDOW_N:
        return {**result, **window_info, "recent_rate": None, "old_rate": None,
                "tolerance_pt": None, "green": None,
                "note": f"直近窓のn({recent_window['n']})が{_S4_MIN_WINDOW_N}未満"
                        "(母集合が痩せすぎ)ゆえ判定不能——断言しない"}

    recent_rate = recent_window["count"] / recent_window["n"] * 100
    window_info["recent_rate"] = round(recent_rate, 3)

    # ★補助の絶対上限: 窓の充足に関わらず、線を越えたら無条件で赤。
    if recent_rate > _S4_ABSOLUTE_CAP_PCT:
        return {**result, **window_info, "old_rate": None, "tolerance_pt": None, "green": False,
                "note": f"直近窓の棄権率({round(recent_rate, 1)}%)が絶対上限"
                        f"({_S4_ABSOLUTE_CAP_PCT}%)を超過ゆえ窓比較に関わらず無条件で赤"}

    if not old_window or not old_window.get("n"):
        return {**result, **window_info, "old_rate": None, "tolerance_pt": None, "green": None,
                "note": "古い窓(30〜90日前)にデータが無い(台帳が育っていない)ゆえ判定不能"
                        "——比べる相手が居らぬ間は緑と名乗らない"}

    if old_window["n"] < _S4_MIN_WINDOW_N:
        return {**result, **window_info, "old_rate": None, "tolerance_pt": None, "green": None,
                "note": f"古い窓のn({old_window['n']})が{_S4_MIN_WINDOW_N}未満"
                        "(母集合が痩せすぎ)ゆえ判定不能——断言しない"}

    old_rate = old_window["count"] / old_window["n"] * 100
    # ★瑕疵D是正(cmd_512第4便・軍師addendum D-2)を踏襲: 許容幅は絶対値と相対値の大きい方
    # (両建て)。old_rateが0に近い実測では相対値だけだと絶対量では僅かしか広がらず棄権
    # 1件増程度の揺らぎを拾いかねない一方、old_rateが大きい局面では絶対値だけだと相対的な
    # 倍増を見逃しかねない——ゆえ両者のmaxで両方の弱点を補う。
    tolerance_pt = max(_S4_TOLERANCE_ABS_PT, old_rate * (_S4_TOLERANCE_REL_MULT - 1.0))
    window_info["old_rate"] = round(old_rate, 3)
    return {**result, **window_info, "tolerance_pt": round(tolerance_pt, 3),
            "green": recent_rate <= old_rate + tolerance_pt}


def s5_human_report(flag_path=None):
    """S5: 人間の申告1件で即赤(台帳より人が優先)。フラグファイルの有無のみを見る。"""
    path = flag_path or HUMAN_REPORT_FLAG
    exists = os.path.exists(path)
    detail = None
    if exists:
        try:
            detail = open(path, encoding="utf-8").read().strip()
        except Exception:
            detail = "(読み取れず)"
    return {"metric": "human_report_flag", "flagged": exists, "detail": detail, "green": not exists}


# ★軍師QC3是正(cmd_512第5便): 採用する台帳最終行の古さの上限(日数)。S4のD-4(30日)と
# 同じ流儀(_parse_ledger_ts + age_days比較)を使う。divergence台帳(casper_howto_llm_divergence.jsonl)
# はcron(日曜05:00)の週1回更新であり、S4の元台帳(observation_ledger.jsonl・日次相当の頻度で
# 育つ)より更新頻度が低い。日次のS4[30日]の半分未満に絞ることで、「cronが1回程度飛んでも
# ただちにUnknown化はしない(週1回ゆえ±数日のずれは正常運用の範囲)」が「2回連続(2週間)
# 飛んだらUnknownとして正直に名乗る」を両立させる。14日 = 週次更新2回分。
_S6_LEDGER_MAX_AGE_DAYS = 14


def s6_llm_divergence_ledger(ledger_path=None, max_age_days=None, _now=None):
    """S6(cmd_512第4便申し送り1是正): check_llm_divergence()台帳の最終行を読み、
    n_diverged>0なら赤とする(規則側判定がLLM側と食い違い始めた=規則の陳腐化兆候)。
    台帳が存在しない/行が無い(=--live-llmがまだ一度も走っていない)場合はUnknownとし、
    赤と偽らない(判定不能を捏造しない・当陣の掟)。

    ★軍師QC3是正(cmd_512第5便・S6実装への小さな追加是正): 台帳自体の古さ(cronが週次で
    回っているか)を見ていなかった穴を塞ぐ。S4のD-4(_S4_BASELINE_MAX_AGE_DAYS・
    _parse_ledger_ts)と同じ流儀を流用し、最終行のtsがmax_age_days(既定
    _S6_LEDGER_MAX_AGE_DAYS)を超えて古ければgreen=None(unknown)を返す。新しい流儀は
    発明しない(同一ファイル内でS4とS6の陳腐化判定の流儀が食い違う状態=cmd_512発端の
    発見2と同型を繰り返さない)。"""
    path = ledger_path or DIVERGENCE_LEDGER
    if not os.path.isfile(path):
        return {"metric": "n_diverged", "value": None, "n_gated_turns": None,
                "green": None, "note": "台帳ファイルが存在しない(--live-llm未実行)"}
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    last = json.loads(ln)
                except Exception:
                    continue
    except Exception:
        return {"metric": "n_diverged", "value": None, "n_gated_turns": None,
                "green": None, "note": "台帳の読取に失敗"}
    if last is None:
        return {"metric": "n_diverged", "value": None, "n_gated_turns": None,
                "green": None, "note": "台帳が空(行が一つも無い)"}
    n_diverged = last.get("n_diverged")
    n_gated = last.get("n_gated_turns")
    ts = last.get("ts")
    if not isinstance(n_diverged, int):
        return {"metric": "n_diverged", "value": None, "n_gated_turns": n_gated,
                "green": None, "note": "台帳最終行にn_divergedが無い(判定不能)"}
    max_age = max_age_days if max_age_days is not None else _S6_LEDGER_MAX_AGE_DAYS
    now = _now or datetime.datetime.now(datetime.timezone.utc)
    ts_dt = _parse_ledger_ts(ts)
    if ts_dt is None:
        return {"metric": "n_diverged", "value": n_diverged, "n_gated_turns": n_gated,
                "ts": ts, "green": None,
                "note": "台帳最終行のtsが読めない(判定不能・陳腐化判定もできない)"}
    age_days = (now - ts_dt).total_seconds() / 86400.0
    if age_days > max_age:
        return {"metric": "n_diverged", "value": n_diverged, "n_gated_turns": n_gated,
                "ts": ts, "green": None, "age_days": round(age_days, 1),
                "max_age_days": max_age,
                "note": f"台帳最終行が{max_age}日超過({round(age_days, 1)}日前)ゆえ判定不能"
                        "(--live-llmが週次で回っていない可能性)"}
    return {"metric": "n_diverged", "value": n_diverged, "n_gated_turns": n_gated,
            "ts": ts, "green": n_diverged == 0, "age_days": round(age_days, 1),
            "max_age_days": max_age}


def symptom_free_report(recs=None, recent_window=None, old_window=None, since_trace_ts=None):
    """overall三値("green"/"red"/"unknown")。手当2: ac12_recovery_gate()と同じ流儀
    (UnknownはNone)に揃える。判定規則: いずれかのSがgreen:None(判定不能)を含み、redが無ければ
    unknown。redが一つでもあればred(不能より赤優先)。全てgreenならgreen。
    undeterminedに不能な指標名を列挙し、「不能を含む緑」と「全指標が緑の緑」を区別可能にする。

    ★瑕疵E是正(cmd_512第5便): baseline_recs(単一baseline辞書)引数をrecent_window/
    old_window(窓分割2辞書)へ置き換えた。呼び出し元(試験)は窓ごとに合成台帳を渡せる。

    ★瑕疵H是正(cmd_512第7便): traceが_MAX_LINESでローテーションしてもS4のdeltaが
    永久unknownへ落ちないよう、出力にlatest_trace_ts(今回読み込んだtrace recordの
    最新ts)を必ず載せる。since_trace_ts指定時はabstained_since(count/n)も併せて
    返す——run_observation.pyが台帳の前回latest_trace_tsをここへ渡すことで、
    『前回行との単純累計差分』でなく『traceの新しい側の増分』としてdeltaを数え直せる。"""
    recs = recs if recs is not None else _load_trace_records()
    s1 = s1_retry_zero(recs)
    s2 = s2_guard_trend(recs)
    s3 = s3_population_always_shown(recs)
    s4 = s4_abstain_rate_within_baseline(recs, recent_window=recent_window, old_window=old_window)
    s5 = s5_human_report()
    s6 = s6_llm_divergence_ledger()
    checks = {"S1_retry_zero": s1, "S2_guard_trend": s2, "S3_population_shown": s3,
              "S4_abstain_baseline": s4, "S5_human_report": s5, "S6_llm_divergence": s6}

    undetermined = [name for name, s in checks.items() if s["green"] is None]
    any_red = any(s["green"] is False for s in checks.values()) or bool(s5["flagged"])
    if any_red:
        overall = "red"
    elif undetermined:
        overall = "unknown"
    else:
        overall = "green"

    result = {**checks, "overall": overall, "undetermined": undetermined, "n_records": len(recs),
              "latest_trace_ts": _latest_trace_ts(recs)}
    abstained_since = _count_abstained_since(recs, since_trace_ts)
    if abstained_since is not None:
        result["abstained_since"] = abstained_since
    return result


def ac12_recovery_gate():
    """AC12: 推論機(.139)復帰判断の二条件AND。台帳(breaker.json)から読める形にする。
    ★捏造しない: breaker.jsonは連続green時間を保持しないため条件Aは常にUnknownを返す
    (state=="green"であることまでは読めるが「72h連続」は現行台帳の情報だけでは判定できない
    ——これを機械証明する構造上の限界として正直に明示する。台帳拡張は次cmdの範疇)。"""
    status = B.status()
    rec_119 = status.get(_GEN_119_KEY)
    rec_139 = status.get(_GEN_139_KEY)

    cond_a = {
        "requirement": f"{_GEN_119_KEY} が連続72h green",
        "current_state": (rec_119 or {}).get("state"),
        "satisfied": None,
        "note": "breaker.jsonはstate遷移時刻(opened_at)のみを保持し、green状態の連続時間そのものは"
                "記録しない構造上の限界ゆえ、現行台帳だけでは72h連続を機械証明できない(Unknown)。"
                "台帳へgreen開始時刻を追加するのは本cmdのscope外(観測の増設は次cmdで)。",
    }
    p90_ms_x1000 = None
    latency = None
    try:
        st = C.trace_stats(limit=5000)
        latency = st.get("latency") or {}
    except Exception:
        latency = {}
    p90_sec = latency.get("p90")
    p90_ms = (p90_sec * 1000) if isinstance(p90_sec, (int, float)) else None
    cond_b = {
        "requirement": f"z8a実経路(.119)のp95がしきい値({_P95_THRESHOLD_MS}ms)を超過",
        "proxy_note": "trace_stats()にp95相当の値が無いため、既存のp90を保守的な代理指標として使う"
                      "(新しい統計機構は作らない・p95はp90以上ゆえp90超過が確認できればp95も超過している"
                      "はずという片側の代理に留まり、p90以下の時は判定不能とする)。",
        "p90_ms": p90_ms,
        "threshold_ms": _P95_THRESHOLD_MS,
        "satisfied": (p90_ms > _P95_THRESHOLD_MS) if isinstance(p90_ms, (int, float)) else None,
    }
    both_known = cond_a["satisfied"] is not None and cond_b["satisfied"] is not None
    gate_open = bool(both_known and cond_a["satisfied"] and cond_b["satisfied"])
    return {
        "condition_a": cond_a, "condition_b": cond_b,
        "gate_open": gate_open if both_known else None,
        "note": "条件Aが現行台帳では常にUnknownのため、gate_openは常にNone(判定不能)になる"
                "——これは意図した挙動である(切替の可否を捏造で断定させない)。台帳拡張は次cmd。",
    }


_EXIT_CODE_FOR_OVERALL = {"green": 0, "unknown": 2, "red": 1}


def main():
    # ★瑕疵H是正: run_observation.pyがサブプロセス経由で前回のlatest_trace_tsを
    # 渡せるよう環境変数で受け取る(新しいCLI引数/通知経路を増やさず、既存のstdout
    # JSON経路にabstained_sinceを足すだけに留める)。未設定なら通常のcumulative出力のみ。
    since_trace_ts = os.environ.get("CASPER_SYMPTOM_FREE_SINCE_TRACE_TS") or None
    report = symptom_free_report(since_trace_ts=since_trace_ts)
    gate = ac12_recovery_gate()
    print(json.dumps({"symptom_free": report, "recovery_gate": gate}, ensure_ascii=False, indent=1))
    sys.exit(_EXIT_CODE_FOR_OVERALL[report["overall"]])


if __name__ == "__main__":
    main()
