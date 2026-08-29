#!/usr/bin/env python3
"""Casper 夜間バッチ層 — 自己改善ループの定時ジョブを1箇所に集約(Fable5「欠落箱」)。

散発cron/スクリプトに散らばっていた定時処理を明示的な1つのジョブ定義に。毎夜(or 日次tick)に:
  ① learn_bank      — 人が触れた教師信号→クラウド蒸留→規則bank(flywheelの学習)
  ② gen_pending     — 失敗トレース→候補テストケース(golden setを実失敗から増やす・人が週1昇格)
  ③ outbox compact  — 終端レコードの間引き(台帳肥大防止)
  ④ attention expire— proposed>7日を自動失効
  ⑤ gate(任意)      — 回帰ゲート(golden suite実走・要 live server・重いので既定OFF)
  ⑥ weekly_report   — 週次品質測定(cmd_502・ファイル読み+regexのみ・1秒未満・7日経過で発火)
  ⑦ pack_lint       — 語彙検問(cmd_504・数秒・85ファイル走査)。取りこぼしの網。
    「道具は在るが誰も走らせぬ」がcmd_489→495→504と三度崩れたため、gate群(①・本命)に
    加えnightlyにも相乗りさせる(②・取りこぼしの網)。weekly_reportと違い毎日走らせる
    (週次に合わせると崩れてから最大7日気づけないため)。

with_gate=False 既定: サーバ内tickから呼ぶ時は gate を外す(サーバが自分自身を重く叩くのを避ける)。
gate は外部cron or 手動で `python3 casper_eval.py --gate`。

with_holdout=False 既定(cmd_502): holdout実走(16問・軍師実測386〜628秒)はgateと同じ理由で
サーバ内tickに載せない。外部cronで週1回 `python3 nightly.py --holdout` を叩く運用とする。
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NLOG = os.path.join(HERE, "nightly_log.jsonl")
STATE_PATH = os.path.join(HERE, "reports", "_state.json")


def _read_state(state_path=STATE_PATH):
    try:
        return json.load(open(state_path, encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state, state_path=STATE_PATH):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def run_pack_lint():
    """語彙検問(pack_lint.py)を実行し、state(reports/_state.json)へ結果を記録する。
    掟「失敗とゼロを別出口へ」: 例外(pack.yaml構文エラー等)は「0件」と取り違えず
    pack_lint_last_error に記録する(cmd_502で weekly_report が定めた作法と揃える)。"""
    state = _read_state()
    today = datetime.date.today().isoformat()
    try:
        import pack_lint
        violations, n_files = pack_lint.scan("bokan")
        if violations is None:
            raise RuntimeError("vocab未定義(pack.yamlを確認せよ)")
        by_file = sorted({v[0] for v in violations})
        state["pack_lint_last_run"] = today
        state["pack_lint_last_count"] = len(violations)
        state["pack_lint_last_files"] = by_file
        state["pack_lint_last_error"] = None
    except Exception as e:
        state["pack_lint_last_run"] = today
        state["pack_lint_last_error"] = str(e)[:200]
    _write_state(state)
    return state.get("pack_lint_last_count"), state.get("pack_lint_last_error")


def pack_lint_dashboard_line(state=None):
    """dashboard.mdへ載せる1行。0件の時は何も出さぬ(過剰に賑やかにせぬ・軍師設計)。
    違反が在る時だけ件数とファイル数、先頭2件+「他N件」を出す。失敗はNoneでなく別出口。"""
    state = state if state is not None else _read_state()
    err = state.get("pack_lint_last_error")
    if err:
        return f"語彙検問: 実行失敗: {err}"
    n = state.get("pack_lint_last_count")
    if n is None:
        return None  # 未実施(まだ一度も走っていない)
    if n == 0:
        return None  # 0件の時は何も出さぬ
    files = state.get("pack_lint_last_files") or []
    shown = "、".join(files[:2])
    rest = len(files) - 2
    if rest > 0:
        shown += f" 他{rest}件"
    return f"語彙検問 {n}件違反({shown})"


def run(with_gate=False, with_holdout=False):
    res = {"ts": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        import learn_bank
        res["learn_bank_added"] = learn_bank.run()
    except Exception as e:
        res["learn_bank_err"] = str(e)[:120]
    try:
        import casper_eval
        res["gen_pending"] = casper_eval.gen_pending()
    except Exception as e:
        res["gen_pending_err"] = str(e)[:120]
    try:
        import casper_outbox
        res["outbox_count"] = casper_outbox.compact()
    except Exception as e:
        res["outbox_err"] = str(e)[:120]
    try:
        import attention
        res["expired"] = attention.expire_stale()
    except Exception as e:
        res["expired_err"] = str(e)[:120]
    try:
        import casper_person_gate                      # ③ 選択カードの選択ログ→per-user既定のcandidate提案(人が昇格)
        res["gate_candidates"] = casper_person_gate.promote_candidates()
    except Exception as e:
        res["gate_candidates_err"] = str(e)[:120]
    if with_gate:
        try:
            import casper_eval
            res["gate_rc"] = casper_eval.gate()          # 0=回帰なし / 1=回帰検知
        except Exception as e:
            res["gate_err"] = str(e)[:120]
    try:
        import weekly_report
        if weekly_report.should_run_weekly():
            wr = weekly_report.run_weekly()
            res["weekly_ran"] = True
            res["weekly_ok"] = wr["state"].get("weekly_last_ok")
            res["weekly_line"] = wr["dashboard_line"]
        else:
            res["weekly_ran"] = False                    # 7日未満・即座に戻る(1秒未満)
    except Exception as e:
        res["weekly_err"] = str(e)[:120]
    if with_holdout:
        try:
            res["holdout_rc"] = run_holdout()
        except Exception as e:
            res["holdout_err"] = str(e)[:120]
    try:
        res["pack_lint_count"], res["pack_lint_err"] = run_pack_lint()
    except Exception as e:
        res["pack_lint_err"] = str(e)[:120]
    try:
        with open(NLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return res


def run_holdout():
    """holdout実走(16問・軍師実測386〜628秒)。既定OFF・外部cronから明示的に呼ぶ専用。
    eval/holdout.json・eval/run_holdout.pyの判定基準には触れず、既存run_holdout.pyの
    main相当を呼ぶのみ(サーバ内tickからは呼ばれない=with_holdout既定Falseで守られる)。
    subtask_502_impl2で発覚した2件を修正: (1) RH.main()のargparseがnightly.pyの
    --holdoutを未知引数として拒否しSystemExit(2)で落ちていた→sys.argvを退避して渡す。
    (2) weekly_report.pyがholdout_last_run/last_ok/last_pass/last_totalをstateから
    読むが、これまで誰も書いていなかった→ここでstateへ記録する。"""
    eval_dir = os.path.join(HERE, "eval")
    import sys
    sys.path.insert(0, eval_dir)
    import run_holdout as RH
    state = _read_state()
    today = datetime.date.today().isoformat()
    _argv = sys.argv
    sys.argv = ["run_holdout.py"]
    try:
        rc = RH.main()
        state["holdout_last_run"] = today
        state["holdout_last_ok"] = (rc == 0)
        if rc == 0:
            results_dir = RH.RESULTS_DIR
            latest = max(
                (os.path.join(results_dir, f) for f in os.listdir(results_dir) if f.endswith(".json")),
                key=os.path.getmtime, default=None,
            )
            if latest:
                counts = json.load(open(latest, encoding="utf-8"))["summary"]["counts"]
                state["holdout_last_pass"] = counts.get("pass")
                state["holdout_last_total"] = counts.get("pass", 0) + counts.get("fail", 0)
        return rc
    finally:
        sys.argv = _argv
        _write_state(state)


if __name__ == "__main__":
    import sys
    r = run(with_gate=("--gate" in sys.argv), with_holdout=("--holdout" in sys.argv))
    print("=== 夜間バッチ完了 ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
