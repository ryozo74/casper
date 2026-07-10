#!/usr/bin/env python3
"""Casper 夜間バッチ層 — 自己改善ループの定時ジョブを1箇所に集約(Fable5「欠落箱」)。

散発cron/スクリプトに散らばっていた定時処理を明示的な1つのジョブ定義に。毎夜(or 日次tick)に:
  ① learn_bank      — 人が触れた教師信号→クラウド蒸留→規則bank(flywheelの学習)
  ② gen_pending     — 失敗トレース→候補テストケース(golden setを実失敗から増やす・人が週1昇格)
  ③ outbox compact  — 終端レコードの間引き(台帳肥大防止)
  ④ attention expire— proposed>7日を自動失効
  ⑤ gate(任意)      — 回帰ゲート(golden suite実走・要 live server・重いので既定OFF)

with_gate=False 既定: サーバ内tickから呼ぶ時は gate を外す(サーバが自分自身を重く叩くのを避ける)。
gate は外部cron or 手動で `python3 casper_eval.py --gate`。
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NLOG = os.path.join(HERE, "nightly_log.jsonl")


def run(with_gate=False):
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
        with open(NLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return res


if __name__ == "__main__":
    import sys
    r = run(with_gate=("--gate" in sys.argv))
    print("=== 夜間バッチ完了 ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
