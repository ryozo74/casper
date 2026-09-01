#!/usr/bin/env python3
"""cmd_520最終便-担当A(subtask_520_impl4a) AC7回帰試験。

calendar/image_asset/fewshotの3機構を_EXPECTATION_CHECKSへ新規登録した(replay_corpus.py)。
本試験はその3機構それぞれについて、既存のtest_replay_corpus_impl3.pyと同じ作法(in-memory
モジュール属性差替のみ・本番chat_server.pyは一切書き換えない・md5不変を実測で確認)で
AC7突然変異試験(門を無効化→missing側赤化→復元→verdict一致[=red件数が元に戻る])を行う。

★守秘: 実ログ本文はテスト出力に含めない(replay_corpus.load()経由でのみ読み、機構名/
turn番号/赤緑・bool判定のみを検査する)。

Usage: python3 test_replay_corpus_520_impl4a.py
"""
import hashlib
import os
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_server as C
import replay_corpus as R

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


def _mutation_case(mech_name, disable_fn, restore_fn):
    """門を無効化(disable_fn)→missing側赤化→復元(restore_fn)→verdict(red件数)が元に戻る、を
    machine的に確認する単一の作法(test_replay_corpus_impl3.pyのtest_urgent_exit_layer_verdict_mutation
    と同型)。disable_fn/restore_fnは副作用のみ持つ関数(引数無し)。"""
    src_path = os.path.join(HERE, "chat_server.py")
    before_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()

    result0 = R.run()
    red0 = sum(1 for r in result0["fired"] if r["verdict"] == "red")
    check(f"{mech_name} 変異前確認: UNMUTATED red=0(このcmd_520最終便の前提)", red0 == 0,
          f"red={red0}")
    fired0 = sum(1 for r in result0["fired"] if mech_name in r["mechanisms"])
    check(f"{mech_name} 変異前確認: 変異前はこのコーパス中で発火している(fired>0)", fired0 > 0,
          f"fired_turns={fired0}")

    disable_fn()
    try:
        result1 = R.run()
    finally:
        restore_fn()
    red1 = sum(1 for r in result1["fired"] if r["verdict"] == "red")
    missing1 = sum(1 for r in result1["fired"] if mech_name in (r.get("missing") or []))
    check(f"{mech_name} 変異後: missing側(不発火)として赤化する", missing1 > 0,
          f"missing_turns={missing1}")
    check(f"{mech_name} 変異後: 全体redが増加する(門無効化により赤化)", red1 > red0,
          f"before={red0} after={red1}")

    result2 = R.run()
    red2 = sum(1 for r in result2["fired"] if r["verdict"] == "red")
    check(f"{mech_name} 復元後: verdict(red件数)が元に戻る", red2 == red0,
          f"red2={red2} red0={red0}")

    after_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
    check(f"{mech_name} 本番chat_server.pyは変異試験中も一切書き換えられていない(md5不変)",
          before_md5 == after_md5, f"before={before_md5} after={after_md5}")


def test_calendar_mutation():
    orig = C.calendar_digest

    def disable():
        C.calendar_digest = lambda q: ""

    def restore():
        C.calendar_digest = orig

    _mutation_case("calendar", disable, restore)


def test_image_asset_mutation():
    orig = C.image_asset_digest

    def disable():
        C.image_asset_digest = lambda q: ""

    def restore():
        C.image_asset_digest = orig

    _mutation_case("image_asset", disable, restore)


def test_fewshot_mutation():
    orig = C.fewshot_digest

    def disable():
        C.fewshot_digest = lambda q: ""

    def restore():
        C.fewshot_digest = orig

    _mutation_case("fewshot", disable, restore)


if __name__ == "__main__":
    test_calendar_mutation()
    test_image_asset_mutation()
    test_fewshot_mutation()

    n_ok = len(_failures) == 0
    print(f"\n{'✅ 全PASS' if n_ok else '❌ FAIL あり'}: failures={_failures}")
    sys.exit(0 if n_ok else 1)
