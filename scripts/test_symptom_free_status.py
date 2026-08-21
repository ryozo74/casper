#!/usr/bin/env python3
"""cmd_511第3便 AC11/AC12回帰試験: symptom_free_status.pyの各S1〜S5・AC12ゲートが
赤くなれることを合成recordで証明する(緑の証明でなく赤の証明・当陣の掟)。

★守秘: 本試験は実ログを一切開かない(合成recordのみを使う)。symptom_free_status.py自体は
既存の単一ソース(casper_trace.jsonl/breaker.json)を読むだけで新しい判定機構を作らない
設計であることをここで検査する。

Usage: python3 test_symptom_free_status.py
"""
import os
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import symptom_free_status as S

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


def test_s1_retry_zero_green_then_red():
    green = S.s1_retry_zero([{"decision_record": {"declines": []}}])
    check("S1-0: declinesが空なら緑", green["green"] is True, str(green))

    red = S.s1_retry_zero([{"decision_record": {"declines": [
        {"mechanism": "retry_detected", "reason": "x"}]}}])
    check("S1-1: retry_detectedが1件でも赤", red["green"] is False and red["value"] == 1, str(red))


def test_s2_guard_trend_flat_green_increasing_red():
    flat = S.s2_guard_trend([
        {"ts": "2026-08-01T00:00:00", "guarded_claim": True},
        {"ts": "2026-08-02T00:00:00", "guarded_claim": False},
        {"ts": "2026-08-03T00:00:00", "guarded_claim": True},
    ])
    check("S2-0: 横ばい/減少系列は緑", flat["green"] is True, str(flat))

    increasing_recs = []
    for day, n in enumerate([1, 2, 3, 4], start=1):
        for _ in range(n):
            increasing_recs.append({"ts": f"2026-08-0{day}T00:00:00", "guarded_claim": True})
    increasing = S.s2_guard_trend(increasing_recs)
    check("S2-1: 単調増加系列(1→2→3→4)は赤", increasing["green"] is False, str(increasing))


def test_s3_population_shown_green_then_red():
    green = S.s3_population_always_shown([
        {"decision_record": {"population_n": 0}},
        {"decision_record": {"population_n": 5}},
    ])
    check("S3-0: 全recordがpopulation_nを持てば緑", green["green"] is True, str(green))

    red = S.s3_population_always_shown([
        {"decision_record": {"population_n": 0}},
        {"decision_record": {}},   # population_n欠如
    ])
    check("S3-1: decision_recordはあるがpopulation_nが欠如したrecordがあれば赤",
          red["green"] is False and red["value"] == 1, str(red))

    scoped_out = S.s3_population_always_shown([
        {"decision_record": {"population_n": 0}},
        {"query": "no decision_record key at all(旧世代trace)"},
    ])
    check("S3-2: decision_recordキー自体が無い旧世代recordはscope外(誤って赤に数えない)",
          scoped_out["green"] is True and scoped_out["n_scoped"] == 1, str(scoped_out))


def test_s4_abstain_baseline_within_and_exceeding():
    """cmd_512第5便(瑕疵E是正)で baseline_recs(単一baseline) を
    recent_window/old_window(窓分割)へ置き換えたため、この試験も窓分割の形へ改める。"""
    old_window = {"count": 10, "n": 100, "latest_ts": None, "days_with_data": 60}  # old_rate=10%
    within_recent = {"count": 5, "n": 100, "latest_ts": None, "days_with_data": 7}   # 5% <= 10%
    r_within = S.s4_abstain_rate_within_baseline([], recent_window=within_recent, old_window=old_window)
    check("S4-0: recent<=old+toleranceなら緑", r_within["green"] is True, str(r_within))

    exceeding_recent = {"count": 20, "n": 100, "latest_ts": None, "days_with_data": 7}  # 20% > 10%+tol
    r_exceed = S.s4_abstain_rate_within_baseline([], recent_window=exceeding_recent, old_window=old_window)
    check("S4-1: recent超過なら赤", r_exceed["green"] is False, str(r_exceed))

    # recent_window/old_window=None時はS.OBSERVATION_LEDGERへ自動フォールバックする実装のため、
    # 「台帳が無い/未成熟」を再現するには一時的に存在しないパスへ差し替えて隔離する
    # (cmd_512第3便で台帳が実際に育つようになったため、本番台帳を素通しすると
    # このテストが本番の状態に依存してしまう・テスト隔離の穴を塞ぐ)。
    _orig_ledger = S.OBSERVATION_LEDGER
    S.OBSERVATION_LEDGER = "/nonexistent/path/for/test/observation_ledger.jsonl"
    try:
        r_unknown = S.s4_abstain_rate_within_baseline([], recent_window=None, old_window=None)
    finally:
        S.OBSERVATION_LEDGER = _orig_ledger
    check("S4-2: 窓データ未確定時はgreen=None(判定不能・捏造しない)",
          r_unknown["green"] is None, str(r_unknown))

    # cmd_512第5便AC-E5: 絶対上限を超えれば古い窓のデータに関わらず無条件で赤
    cap_recent = {"count": 60, "n": 100, "latest_ts": None, "days_with_data": 7}  # 60% > 5%
    r_cap = S.s4_abstain_rate_within_baseline([], recent_window=cap_recent, old_window=old_window)
    check("S4-3(AC-E5): 絶対上限超過なら窓比較に関わらず無条件で赤",
          r_cap["green"] is False and "絶対上限" in (r_cap.get("note") or ""), str(r_cap))


def test_load_baseline_from_ledger_roundtrip():
    """cmd_512第3便 瑕疵B是正: 台帳行(run_observation.pyが書く実際のスキーマ・row直下の
    abstained_count/abstained_n)からbaselineが実際に立ち、S4がgreen/redへ転じることを
    合成台帳で確認する(将軍指摘: 台帳が何行育ってもbaselineが永久にNoneだった構造欠陥の
    再発防止)。"""
    import tempfile
    tmpdir = tempfile.mkdtemp()
    ledger_path = os.path.join(tmpdir, "ledger.jsonl")

    # ★瑕疵G是正(cmd_512第6便)以降、_load_window_from_ledgerはts(古さ判定に使う)と
    # abstained_count_delta/abstained_n_delta(前回実行からの増分)の両方を持つ行のみを
    # 窓集計対象とする(design_decision (c): delta列を持たぬ旧行は除外・累計と増分を
    # 混ぜない/D-4: tsが読めない・古すぎる行も除外)。本試験の合成台帳行もその形に
    # 追随させる(瑕疵H是正・cmd_512第7便で試験ファイルの署名追随ついでに直す・軍師申し送り)。
    import datetime as _dt
    now_ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    row = {"tool": "symptom_free_status", "overall": "unknown", "n_records": 500,
           "metrics": {"S4_abstain_baseline": 0.4}, "abstained_count": 2, "abstained_n": 500,
           "abstained_count_delta": 2, "abstained_n_delta": 500, "ts": now_ts}
    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(__import__("json").dumps(row) + "\n")

    baseline = S._load_baseline_from_ledger(ledger_path=ledger_path)
    check("baseline-0: 台帳行(abstained_count/abstained_n)からbaselineが実際に立つ"
          "(cmd_512第5便: 戻り値は集計辞書{count,n,...}へ統一済)",
          baseline is not None and baseline["n"] == 500 and baseline["count"] == 2, str(baseline))

    recs_low = {"count": 2, "n": 500, "latest_ts": None, "days_with_data": 1}   # 0.4% == baseline
    r_green = S.s4_abstain_rate_within_baseline([], recent_window=recs_low, old_window=baseline)
    check("baseline-1: 台帳経由old_window以下ならS4はgreen(overall=greenへの道が開くことの証明)",
          r_green["green"] is True, str(r_green))

    recs_high = {"count": 20, "n": 500, "latest_ts": None, "days_with_data": 1}  # 4% > 0.4%
    r_red = S.s4_abstain_rate_within_baseline([], recent_window=recs_high, old_window=baseline)
    check("baseline-2: 台帳経由old_window超過ならS4はred", r_red["green"] is False, str(r_red))

    # 未成熟な台帳(abstained_count/abstained_nが無い旧行のみ)は正直にNoneを返す
    stale_ledger = os.path.join(tmpdir, "stale.jsonl")
    with open(stale_ledger, "w", encoding="utf-8") as f:
        f.write(__import__("json").dumps(
            {"tool": "symptom_free_status", "n_records": 500, "metrics": {}}) + "\n")
    baseline_none = S._load_baseline_from_ledger(ledger_path=stale_ledger)
    check("baseline-3: abstained_count/abstained_nが無い行のみの台帳ではNone(捏造しない)",
          baseline_none is None, str(baseline_none))


def test_s5_human_report_flag():
    import tempfile
    green = S.s5_human_report(flag_path="/nonexistent/path/for/test")
    check("S5-0: フラグファイルが無ければ緑", green["green"] is True, str(green))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".flag", delete=False) as f:
        f.write("殿からの申告: turn 14:53が誤答")
        tmp_path = f.name
    try:
        red = S.s5_human_report(flag_path=tmp_path)
        check("S5-1: フラグファイルが1件でもあれば赤(台帳より人が優先)",
              red["green"] is False and red["flagged"] is True, str(red))
    finally:
        os.unlink(tmp_path)


def test_overall_report_aggregates():
    # 手当2/AC5: days_with_data>=3が無ければS2はunknownを名乗る(母集合なき断言を避ける)ため、
    # 「全S green」を試験するにはS2が判定可能な日数分のrecordが要る。
    # ★瑕疵H是正(cmd_512第7便): symptom_free_report()の旧引数baseline_recs(単一辞書)は
    # 瑕疵E是正(cmd_512第5便)でrecent_window/old_window(窓分割2辞書・_load_window_from_ledger
    # の戻り値と同じshape)へ置き換え済み。本試験もその署名に追随する。
    # ★併せて、S6(cmd_512第4便申し送り1で新設)がDIVERGENCE_LEDGER不在時に判定不能を
    # 名乗ることで「全S green」試験がunknownへ落ちていた穴も塞ぐ(S4と同じ流儀で
    # DIVERGENCE_LEDGERを一時的に隔離した合成台帳へ差し替える・本番環境の状態に依存させない)。
    import tempfile, json as _json
    recs = [{"ts": f"2026-08-{d:02d}T10:00:00", "decision_record": {"population_n": 3, "declines": []},
             "abstained": False} for d in range(16, 19)]
    green_window = {"count": 0, "n": 300, "latest_ts": None, "days_with_data": 7}

    _orig_divergence = S.DIVERGENCE_LEDGER
    tmpdir = tempfile.mkdtemp()
    green_divergence_ledger = os.path.join(tmpdir, "divergence_green.jsonl")
    with open(green_divergence_ledger, "w", encoding="utf-8") as f:
        f.write(_json.dumps({"ts": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "n_diverged": 0, "n_gated_turns": 10}) + "\n")
    S.DIVERGENCE_LEDGER = green_divergence_ledger
    try:
        report = S.symptom_free_report(recs=recs, recent_window=green_window, old_window=green_window)
    finally:
        S.DIVERGENCE_LEDGER = _orig_divergence
    check("overall-0: 全S green相当のrecordならoverall=green(undetermined=[])",
          report["overall"] == "green" and report["undetermined"] == [], str(report["overall"]))

    recs_bad = [{"ts": f"2026-08-{d:02d}T10:00:00",
                 "decision_record": {"population_n": 3,
                                      "declines": [{"mechanism": "retry_detected"}] if d == 18 else []},
                 "abstained": False} for d in range(16, 19)]
    report_bad = S.symptom_free_report(recs=recs_bad, recent_window=green_window, old_window=green_window)
    check("overall-1: S1が赤ならoverall=red(一つでも赤なら全体が赤)",
          report_bad["overall"] == "red", str(report_bad["overall"]))

    # AC3: S4が不能(baseline無し)のまま・他は正常でも overall は green を名乗らない
    # (S4-2と同じ理由でOBSERVATION_LEDGERを一時隔離・本番台帳の状態に依存させない。
    # S6はここでは通常通りDIVERGENCE_LEDGER不在=unknownのままとし、undeterminedに
    # S4・S6の両方が含まれることを期待値とする——「不能を含む緑」でないことの検査ゆえ、
    # 不能要因が複数あっても趣旨は変わらない)。
    recs_s4_unknown = [{"ts": f"2026-08-{d:02d}T10:00:00",
                         "decision_record": {"population_n": 3, "declines": []},
                         "abstained": False} for d in range(16, 19)]
    _orig_ledger = S.OBSERVATION_LEDGER
    S.OBSERVATION_LEDGER = "/nonexistent/path/for/test/observation_ledger.jsonl"
    try:
        report_s4_unknown = S.symptom_free_report(recs=recs_s4_unknown, recent_window=None, old_window=None)
    finally:
        S.OBSERVATION_LEDGER = _orig_ledger
    check("overall-2(AC3): S4不能(baseline無し)ならoverall=unknown(greenと名乗らない)",
          report_s4_unknown["overall"] == "unknown"
          and "S4_abstain_baseline" in report_s4_unknown["undetermined"],
          str((report_s4_unknown["overall"], report_s4_unknown["undetermined"])))


def test_ac12_gate_never_fabricates():
    """AC12: 条件Aが構造上Unknownである限り、gate_openは常にNone(切替可否を断定しない)。
    これは実装のバグではなく意図した設計であることを確認する。"""
    gate = S.ac12_recovery_gate()
    check("AC12-0: condition_a.satisfiedは常にNone(現行台帳の構造的限界を正直に示す)",
          gate["condition_a"]["satisfied"] is None, str(gate["condition_a"]))
    check("AC12-1: 条件Aが未確定である限りgate_openはNone(True/Falseを捏造しない)",
          gate["gate_open"] is None, str(gate["gate_open"]))
    check("AC12-2: condition_bはtrace_stats()のp90を代理指標として実際に読んでいる(実行時エラー無し)",
          "p90_ms" in gate["condition_b"], str(gate["condition_b"]))


def main():
    tests = [
        test_s1_retry_zero_green_then_red,
        test_s2_guard_trend_flat_green_increasing_red,
        test_s3_population_shown_green_then_red,
        test_s4_abstain_baseline_within_and_exceeding,
        test_load_baseline_from_ledger_roundtrip,
        test_s5_human_report_flag,
        test_overall_report_aggregates,
        test_ac12_gate_never_fabricates,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            t()
        except Exception as e:
            check(t.__name__, False, f"EXCEPTION: {e!r}")
    print(f"\n{'='*60}")
    if _failures:
        print(f"FAIL: {len(_failures)}件 -> {_failures}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
