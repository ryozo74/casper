#!/usr/bin/env python3
"""cmd_515手当3試験: alert_dispatch.py(casper_alerts.jsonl -> inbox_write.sh karo 配線)。

AC6: 合成の赤(casper_alerts.jsonl相当の行)を通したら通知が実際に呼ばれることを示す。
     かつ同一metricの連続超過は最初の1回のみ通知されることを示す(瑕疵Dの再発防止)。
AC9: 複数の異なるmetricが同時/連続で超過しても、殿宛(karo経由)の通知が1件も落ちないことを示す
     (取りこぼしが無いことを、遷移ごとの通知呼び出し回数で機械確認する)。

process()を直接呼び、_notify()をモンキーパッチして実際のinbox_write.sh(→本番karoの
inboxファイル)へは一切書き込まない(★試験でDiscordへ実投稿するな、と同じ精神で、
テスト用の副作用は本番inboxにも触れさせない)。

pytest不使用(既存慣例に合わせ素朴なassert方式)。

Usage: python3 test_alert_dispatch.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import alert_dispatch as AD

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


def _row(metric, current=100.0, threshold=50.0, baseline=10.0):
    return {"ts": "2026-08-21T00:00:00", "source": "casper_health",
            "deviations": [{"metric": metric, "current": current,
                             "baseline_median": baseline, "threshold": threshold}]}


def test_ac6_synthetic_alert_delivered_and_coalesced():
    """合成の赤を1件通すと通知が1回呼ばれる。同一metricを3回連続で追加投入しても
    通知は初回の1回のみ(2回目・3回目は『継続中』として静かに扱われる)。"""
    notified = []
    orig_notify = AD._notify
    AD._notify = lambda msg, dry_run: notified.append(msg)
    try:
        state = {"metrics": {}, "cursor_line": 0}
        rows = [_row("gen_p95")]
        result = AD.process(rows, state, dry_run=False)
        check("AC6-0: 初回超過で通知が呼ばれる", len(notified) == 1, f"notified={notified}")
        check("AC6-1: 状態がstillでなくnotified_start", result.get("gen_p95") == "notified_start",
              f"result={result}")

        # 同一metricがさらに3回連続(cursorは進むが行を追加していく=15分ごとの継続超過を模す)。
        rows += [_row("gen_p95"), _row("gen_p95"), _row("gen_p95")]
        result2 = AD.process(rows, state, dry_run=False)
        check("AC6-2: 連続する同一metricの超過は追加通知されない(まとめられる)",
              len(notified) == 1, f"notified件数={len(notified)} notified={notified}")
        check("AC6-3: 状態はstill_active", result2.get("gen_p95") == "still_active",
              f"result2={result2}")

        # 閾値内に戻る(deviations無しの行) → 復旧通知が1回。
        clear_row = {"ts": "2026-08-21T01:00:00", "source": "casper_health", "deviations": []}
        rows.append(clear_row)
        result3 = AD.process(rows, state, dry_run=False)
        check("AC6-4: 復旧で通知が1回追加される(合計2件)", len(notified) == 2,
              f"notified={notified}")
        check("AC6-5: 復旧通知の文言に'復旧'を含む", "復旧" in notified[-1], f"{notified[-1]}")
        check("AC6-6: 状態はnotified_recover", result3.get("gen_p95") == "notified_recover",
              f"result3={result3}")

        # さらに閾値内が続いても再通知しない。
        rows.append(clear_row)
        AD.process(rows, state, dry_run=False)
        check("AC6-7: 復旧が続いても再通知しない(合計2件のまま)", len(notified) == 2,
              f"notified={notified}")
    finally:
        AD._notify = orig_notify


def test_ac9_no_alert_dropped_across_multiple_metrics():
    """複数の異なるmetric(gen_p95・rag_zero)が同時に超過→片方だけ復旧→もう片方も復旧、
    という遷移列で、殿宛通知(遷移点)が1件も落ちないことを確認する。"""
    notified = []
    orig_notify = AD._notify
    AD._notify = lambda msg, dry_run: notified.append(msg)
    try:
        state = {"metrics": {}, "cursor_line": 0}

        # turn1: gen_p95とrag_zeroが同時に超過。
        row1 = {"ts": "t1", "source": "casper_health",
                "deviations": [{"metric": "gen_p95", "current": 300, "threshold": 50, "baseline_median": 10},
                                {"metric": "rag_zero", "current": 0.9, "threshold": 0.5, "baseline_median": 0.1}]}
        rows = [row1]
        r1 = AD.process(rows, state, dry_run=False)
        check("AC9-0: 同時発生の2metricとも初回通知される", len(notified) == 2,
              f"notified={notified}")
        check("AC9-1: 両方ともnotified_start", r1.get("gen_p95") == "notified_start"
              and r1.get("rag_zero") == "notified_start", f"r1={r1}")

        # turn2: gen_p95のみ継続、rag_zeroは閾値内に戻る。
        row2 = {"ts": "t2", "source": "casper_health",
                "deviations": [{"metric": "gen_p95", "current": 310, "threshold": 50, "baseline_median": 10}]}
        rows.append(row2)
        r2 = AD.process(rows, state, dry_run=False)
        check("AC9-2: rag_zero復旧で通知+1(合計3件)、gen_p95は追加通知なし",
              len(notified) == 3, f"notified={notified}")
        check("AC9-3: gen_p95=still_active, rag_zero=notified_recover",
              r2.get("gen_p95") == "still_active" and r2.get("rag_zero") == "notified_recover",
              f"r2={r2}")

        # turn3: gen_p95も復旧。
        row3 = {"ts": "t3", "source": "casper_health", "deviations": []}
        rows.append(row3)
        r3 = AD.process(rows, state, dry_run=False)
        check("AC9-4: gen_p95復旧で通知+1(合計4件)", len(notified) == 4, f"notified={notified}")
        check("AC9-5: 4件の通知本文にgen_p95/rag_zeroの超過・復旧が過不足なく含まれる(取りこぼし0)",
              sum(1 for m in notified if "gen_p95" in m) == 2
              and sum(1 for m in notified if "rag_zero" in m) == 2,
              f"notified={notified}")
    finally:
        AD._notify = orig_notify


def test_ac8_lock_file_only_valid():
    """_holdout.lockはファイル形式のみ有効(ディレクトリ形式は無視される・casper_supervisor.sh
    L39の-f判定と同型)。ロック取得→解放の往復でファイルが正しく生成・削除されることを確認する。"""
    if os.path.exists(AD.LOCK_PATH):
        os.remove(AD.LOCK_PATH)
    AD._acquire_lock("test_who")
    check("AC8-0: ロック取得後、ファイル形式で存在する", os.path.isfile(AD.LOCK_PATH),
          f"path={AD.LOCK_PATH}")
    content = open(AD.LOCK_PATH, encoding="utf-8").read()
    check("AC8-1: ロック内容にwho=が含まれる", "who=test_who" in content, f"content={content!r}")
    check("AC8-2: 自分自身が保持中のロックはlock_held_by_other=False",
          AD._lock_held_by_other("test_who") is False)
    check("AC8-3: 他者名義ならlock_held_by_other=True(TTL内)",
          AD._lock_held_by_other("someone_else") is True)
    AD._release_lock()
    check("AC8-4: 解放後はファイルが消える", not os.path.exists(AD.LOCK_PATH))


def _old_row_ff(metric, ts="2026-07-13T00:00:00"):
    return {"ts": ts, "source": "casper_health",
            "deviations": [{"metric": metric, "current": 1.0, "threshold": 0.5, "baseline_median": 0.1}]}


def _build_synthetic_stale_log():
    """39日前に2metric(guarded_claim/salvaged)が赤くなり、以後多数行にわたり
    出現しない(本番queue/casper_alerts.jsonlの実際の残骸状況)を模した合成ログ。"""
    rows = [_old_row_ff("guarded_claim"), _old_row_ff("salvaged")]
    for i in range(200):
        rows.append({"ts": f"2026-08-t{i}", "source": "casper_health",
                      "deviations": [{"metric": "gen_p95", "current": 380.3, "threshold": 51.1, "baseline_median": 10.0},
                                      {"metric": "rag_zero", "current": 1.0, "threshold": 0.5, "baseline_median": 0.1}]})
    return rows


def test_ac_f1_first_run_no_false_positive_on_stale_backlog():
    """AC-F1(cmd_515手当3是正): 初回起動(state未作成)時、過去ログ(39日前の残骸含む)に
    対して誤って通知が発火しないこと。_load_state(total_rows=len(rows))がcursorを
    末尾へ寄せる(=これまでの分は既読)ことを実測する。"""
    notified = []
    orig_notify = AD._notify
    AD._notify = lambda msg, dry_run: notified.append(msg)
    try:
        rows = _build_synthetic_stale_log()
        state = AD._load_state(total_rows=len(rows))  # STATE_PATH不在=初回起動を模す前提でtotal_rowsを渡す
        check("AC-F1-pre: 前提としてstate未作成(初回)扱いでcursorが末尾", state["cursor_line"] == len(rows))
        result = AD.process(rows, state, dry_run=True)
        check("AC-F1-0: 初回起動で誤通知が0件(39日前のguarded_claim/salvaged残骸含む)",
              len(notified) == 0, f"notified={notified}")
        check("AC-F1-1: 遷移結果が空", result == {}, f"result={result}")
    finally:
        AD._notify = orig_notify


def test_ac_f2_new_alert_after_first_run_still_notifies():
    """AC-F2(反証照会): 初回で黙らせすぎて以後の新規の赤まで届かなくなっていないかを確認する。
    初回起動の直後に新規の閾値超過行が追記された場合、通知1件が正しく発火すること。"""
    notified = []
    orig_notify = AD._notify
    AD._notify = lambda msg, dry_run: notified.append(msg)
    try:
        rows = _build_synthetic_stale_log()
        state = AD._load_state(total_rows=len(rows))
        AD.process(rows, state, dry_run=True)
        check("AC-F2-pre: 初回起動直後は通知0件", len(notified) == 0)

        rows = rows + [{"ts": "2026-08-21T13:05:00", "source": "casper_health",
                         "deviations": [{"metric": "guarded_claim", "current": 400.0,
                                          "threshold": 51.1, "baseline_median": 10.0}]}]
        result = AD.process(rows, state, dry_run=True)
        check("AC-F2-0: 初回起動後の新規行で通知が1件発火する", len(notified) == 1, f"notified={notified}")
        check("AC-F2-1: 遷移がnotified_start", result.get("guarded_claim") == "notified_start", f"result={result}")
    finally:
        AD._notify = orig_notify


def test_ac_f3_existing_state_cursor_unaffected_by_fix():
    """AC-F3: 2回目以降の起動(state既存)の挙動が本便の変更前後で変わらないこと(回帰なし)。
    _load_state()はSTATE_PATHが存在すればtotal_rowsを無視し、既存cursor_line/metricsを
    そのまま返す(初回分岐に入らない)ことをコードパスで確認する。"""
    import tempfile
    import shutil
    tmpdir = tempfile.mkdtemp()
    state_path = os.path.join(tmpdir, "state.json")
    orig_state_path = AD.STATE_PATH
    AD.STATE_PATH = state_path
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"metrics": {"gen_p95": {"active": True, "notified_at": 0}}, "cursor_line": 5}, f)
        loaded = AD._load_state(total_rows=999999)
        check("AC-F3-0: state既存時はtotal_rowsに関わらず既存cursor_lineを維持(=5)",
              loaded["cursor_line"] == 5, f"loaded={loaded}")
        check("AC-F3-1: state既存時は既存metrics状態も維持される",
              loaded["metrics"].get("gen_p95", {}).get("active") is True, f"loaded={loaded}")
    finally:
        AD.STATE_PATH = orig_state_path
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    tests = [
        test_ac6_synthetic_alert_delivered_and_coalesced,
        test_ac9_no_alert_dropped_across_multiple_metrics,
        test_ac8_lock_file_only_valid,
        test_ac_f1_first_run_no_false_positive_on_stale_backlog,
        test_ac_f2_new_alert_after_first_run_still_notifies,
        test_ac_f3_existing_state_cursor_unaffected_by_fix,
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
