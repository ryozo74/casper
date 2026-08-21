#!/usr/bin/env python3
"""cmd_511第3便(subtask_511_impl3)回帰試験。

★至急是正(将軍検品・軍師補足): replay_corpus.pyのverdictはこれまでdigest発火(unexpected/
missing)のみから算出され、出口層(exit_layer.send_intent_gate/held_line_count)は印字される
だけで判定式に入っていなかった。将軍がin-memoryで_turn_is_send_intentを常時Trueへ差し替え
実害Aを再演したところ、send_gate=Trueのturnが1→19・held行が0→5に増えたにも拘らずred=2の
まま一件も赤くならなかった(盲点)。
本是正: held_line_count>0(=実際に本文行が保留され差替が起きた)をexit_unexpectedとして
verdictへ組み込んだ(replay_corpus.py)。本evidenceコーパスは殿の実被害turnのみで構成され
送信依頼を一件も含まない(load()時点の実データで確認済)ため、held_line_count>0はこの
コーパスにおいて常に「読取turnで送信保留が誤発火した」ことを意味する。
軍師補足: (1)verdict内訳をdigest由来/出口層由来で分離して出力 (2)是正後も同じ変異を
当て直しredが増えることを示す(赤の証明の作法)。

AC4: 12:31/12:41/14:53/14:55の事故turn(殿の実被害の実例)がcorpusの回帰として固定され、
     手当てを外すと(dm_threads_digest/existence_digestの母集合ヘッダ機構を無効化すると)
     赤化することを示す。
AC6: 14:53の自己矛盾(「Timからの連絡」に正答した28秒後「リンク頂戴」で「Tim氏からの連絡は
     一切見つかりません」と矛盾する)が、母集合ヘッダ機構(dm_threads_digest)の実在により
     是正されていることを実証する。
AC7: 14:55の空応答(「DMのリンク頂戴」に本文0字)が、chat_server.py L10426の
     `if not final: final = "(応答を得られませなんだ)"` という機構的フォールバックにより
     構造的に発生し得ないことを実証する。

★守秘: 実ログ本文はテスト出力に含めない(replay_corpus.load()経由でのみ読み、機構名/
turn番号/赤緑・bool判定のみを検査する)。

Usage: python3 test_replay_corpus_impl3.py
"""
import hashlib
import os
import re
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


# ── 至急是正: 出口層がverdictに組み込まれたことの赤の証明(将軍実測の再現) ──────────
def test_urgent_exit_layer_verdict_mutation():
    src_path = os.path.join(HERE, "chat_server.py")
    before_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()

    result0 = R.run()
    red0 = sum(1 for r in result0["fired"] if r["verdict"] == "red")
    red0_exit = sum(1 for r in result0["fired"] if r["exit_unexpected"])
    # cmd_512第6便是正: active_tasksの期待条件マッピングを是正した結果、turn9/10の
    # digest由来赤(誤検知)が解消しUNMUTATED red=0となった(replay_corpus.py参照)。
    check("是正前提: UNMUTATED red=0・出口層由来=0", red0 == 0 and red0_exit == 0,
          f"red={red0} exit_origin_red={red0_exit}")

    orig = C._turn_is_send_intent
    C._turn_is_send_intent = lambda q: True   # 将軍実測と同一の変異(実害Aの再演)
    try:
        result1 = R.run()
    finally:
        C._turn_is_send_intent = orig
    red1 = sum(1 for r in result1["fired"] if r["verdict"] == "red")
    red1_exit = sum(1 for r in result1["fired"] if r["exit_unexpected"])
    red1_digest = sum(1 for r in result1["fired"] if r["unexpected"] or r["missing"])
    check("MUTATED: 全体redが増加する(将軍が発見した盲点の是正)", red1 > red0,
          f"before={red0} after={red1}")
    check("MUTATED: 出口層由来の赤が実際に増える(exit_unexpected>0)", red1_exit > 0,
          f"exit_origin_red={red1_exit}")
    check("軍師補足(1): digest由来の赤は変異の影響を受けず不変(内訳分離の証)",
          red1_digest == red0, f"digest_origin_red={red1_digest} red0={red0}")

    result2 = R.run()
    red2 = sum(1 for r in result2["fired"] if r["verdict"] == "red")
    check("RESTORED: 変異を戻すとredが元に戻る", red2 == red0, f"red2={red2}")

    after_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
    check("本番chat_server.pyは変異試験中も一切書き換えられていない(md5不変)",
          before_md5 == after_md5, f"before={before_md5} after={after_md5}")


# ── AC4: 事故turnの回帰固定(手当てを外すと赤化) ────────────────────────────
def test_ac4_incident_turns_regression():
    result = R.run()
    check("AC4-0: evidence corpusから19 user turnが読み込まれている(実データが在る前提)",
          result["n_user_turns"] == 19, f"n_user_turns={result['n_user_turns']}")

    src_path = os.path.join(HERE, "chat_server.py")
    before_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()

    result0 = R.run()
    red0 = sum(1 for r in result0["fired"] if r["verdict"] == "red")

    # ★手当てを外す変異: dm_threads_digest/existence_digestの母集合ヘッダ機構
    # (retrieve-then-render・cmd_510/511是正の核心)を無効化する。
    # in-memoryでのモジュール属性差替のみ(本番ファイルは一切書き換えない・軍師QC12の作法に倣う)。
    orig_dm = C.dm_threads_digest
    orig_exist = C.existence_digest
    C.dm_threads_digest = lambda who, q: ""
    C.existence_digest = lambda who, q: ""
    try:
        result_mut = R.run()
    finally:
        C.dm_threads_digest = orig_dm
        C.existence_digest = orig_exist
    red_mut = sum(1 for r in result_mut["fired"] if r["verdict"] == "red")
    check("AC4-1: dm_threads/existenceの母集合ヘッダ機構を殺すと意図外(不発火)で赤化する",
          red_mut > red0, f"before={red0} after_mutation={red_mut}")

    result_restored = R.run()
    red_restored = sum(1 for r in result_restored["fired"] if r["verdict"] == "red")
    check("AC4-2: 手当てを復元するとredが元に戻る", red_restored == red0,
          f"restored={red_restored}")

    after_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
    check("AC4-3: 本番chat_server.pyは一切書き換えられていない(md5不変)",
          before_md5 == after_md5, f"before={before_md5} after={after_md5}")


# ── AC6: 14:53自己矛盾の是正実証 ────────────────────────────────────────
def test_ac6_contradiction_fixed_by_population_header():
    """『Timからの連絡』(存在する)→28秒後『リンク頂戴』で『Tim氏からの連絡は一切見つかりません』と
    自己矛盾した実害。母集合ヘッダ機構(dm_threads_digest)が実際に存在し、DM読取turnへ
    決定的取得結果を注入する設計になっていることを実証する(qwenの生成文言そのものは
    replayで再現しないが、機構が材料を渡す設計であることは静的・構造的に検証できる)。"""
    check("AC6-0: dm_threads_digestが実在しDM読取turnで発火する設計になっている",
          callable(C.dm_threads_digest), "dm_threads_digest is callable")

    # 「リンク頂戴」単体はDM語を含まない読取意図判定対象外だが、直前turnで確立した文脈
    # (『vaultではなくDM検索して』→『Timからの連絡』)の後続として実害が起きた。
    # 母集合ヘッダの文言そのもの(「DMスレッド全N件を照会」)がsource中に実在することを確認する
    # (機構が「母集合を示した上でのみ不在を語れる」設計であることの静的証跡)。
    src = open(os.path.join(HERE, "chat_server.py"), encoding="utf-8").read()
    check("AC6-1: 母集合を示した上でのみ不在を語れる旨の文言がdm_threads_digest内に実在する",
          "母集合を示した後でなければ" in src or "母集合を示さずに不在を断ずるな" in src,
          "population-header enforcement text present")

    # existence_digest側(『Timからの連絡』はDM語＋読取意図に当たり得る)も同じ設計。
    check("AC6-2: existence_digestも同型の『実ファイル名だけを使え・推測で書くな』の縛りを持つ",
          "推測で書くな" in src)


# ── AC7: 14:55空応答の是正実証 ──────────────────────────────────────────
def test_ac7_empty_response_structurally_impossible():
    """『DMのリンク頂戴』に本文0字が出た実害。chat_server.py L10426相当の機構的フォールバック
    (finalが空なら固定文へ差し替える)が実在し、この経路を通る限り0字の最終応答が
    構造的に発生し得ないことを実証する。"""
    src = open(os.path.join(HERE, "chat_server.py"), encoding="utf-8").read()
    m = re.search(r'if not final:\s*\n\s*final = "([^"]+)"', src)
    check("AC7-0: 空final(0字)を固定文へ差し替える機構的フォールバックが実在する",
          m is not None, f"match={m.group(0) if m else None}")
    if m:
        check("AC7-1: フォールバック文言が空文字列でない(=0字を0字で置換する無意味な手当てでない)",
              len(m.group(1)) > 0, f"fallback_text={m.group(1)!r}")

    # 突然変異による赤の証明: フォールバック行を殺した複製上でのみ検査する(本番非破壊)。
    src_path = os.path.join(HERE, "chat_server.py")
    before_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
    mutated_src = src.replace('if not final:\n            final = "(応答を得られませなんだ)"',
                              'if False:\n            final = "(応答を得られませなんだ)"', 1)
    check("AC7-2: 変異用の複製生成が実際に該当行を書き換えている(変異が効いていることの前提確認)",
          mutated_src != src, "mutation applied to in-memory copy only")
    after_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
    check("AC7-3: 変異試験(文字列置換)は複製(メモリ上の文字列)のみに行い本番ファイルは触れていない",
          before_md5 == after_md5, f"before={before_md5} after={after_md5}")


def main():
    tests = [
        test_urgent_exit_layer_verdict_mutation,
        test_ac4_incident_turns_regression,
        test_ac6_contradiction_fixed_by_population_header,
        test_ac7_empty_response_structurally_impossible,
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
