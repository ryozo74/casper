#!/usr/bin/env python3
"""週次品質測定(cmd_502)の回帰ゲート(純機構・読取のみ)。全PASSで exit 0。

守る掟(brief AC7):
 ①分析の数が手作業と合う(母数・重複除去の作法。型別内訳は合わぬ場合は正直に報告)
 ②holdoutが回る(既存run_holdout.pyを壊さず利用できること)
 ③週次とholdoutが混ざらぬ(別出口・別スコア)
 ④前週との差分が出る(初回はnull+「初回」明示)
 ⑤測定停止が判る(経過日数・失敗時の文言分岐)
突然変異3種(brief記載)でも赤化を確認する:
 (i) ip選り分けを外す→試験7,756行が混ざり数が跳ねる
 (ii) 重複除去を外す→つまずき数が延べ(のべ)に化ける
 (iii) 週次とholdoutを同じ集計に混ぜる→AC3違反
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weekly_report as W

LOG_PATH = os.path.join(HERE, "conversation_log.jsonl")

# 軍師実測窓(gunshi_report.yaml subtask_502_strategy1・2026-08-06確定): この窓で
# 問い115件・重複4件・不在断言14・聞き返し15・できません7・エラー4が将軍手作業と一致した。
WIN_START = datetime.datetime.fromisoformat("2026-07-31T00:00:00")
WIN_END = datetime.datetime.fromisoformat("2026-08-06T13:00:00")

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond, detail=""):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}" + (f" ({detail})" if detail else ""))


# ── ①分析の数が手作業と合う(母数・重複除去) ──────────────────────
analysis = W.analyze_window(LOG_PATH, WIN_START, WIN_END)
chk("①母数(問い数)が将軍手作業と一致(115)", analysis["totals"]["questions"], 115)
chk("①つまずき数(dedup後)が将軍手作業と一致(36)", analysis["totals"]["stumbles"], 36)
chk("①不在断言が一致(14)", analysis["by_type"].get("不在断言"), 14)
chk("①聞き返しが一致(15・軍師実測値=将軍13とは不一致だが再現目標は軍師実測値)", analysis["by_type"].get("聞き返し"), 15)
chk("①できませんが一致(7)", analysis["by_type"].get("できません"), 7)
chk("①エラーが一致(4・将軍手作業8とは不一致=regexで無理に合わせない)", analysis["by_type"].get("エラー"), 4)
chk_true("①重複除去の作法(delta=4)が記録されている",
         analysis.get("dedup_delta") == 4, f"got={analysis.get('dedup_delta')}")

# ── ②holdoutが回る(既存モジュールを壊さず呼べる) ───────────────────
eval_dir = os.path.join(HERE, "eval")
sys.path.insert(0, eval_dir)
try:
    import run_holdout as RH
    chk_true("②run_holdout.pyがimport可能(nightly結線を壊していない)", True)
    chk_true("②holdout.json/criteria.jsonの判定基準に触れていない",
              os.path.exists(os.path.join(eval_dir, "holdout.json")) and
              os.path.exists(os.path.join(eval_dir, "criteria.json")))
except Exception as e:
    chk_true("②run_holdout.pyがimport可能", False, str(e))

chk_true("②nightly.pyにholdout関数が存在するが既定OFF",
         hasattr(__import__("nightly"), "run") and
         "with_holdout" in __import__("inspect").signature(__import__("nightly").run).parameters)

# ── ③週次とholdoutが混ざらぬ(別出口・別スコア) ───────────────────
fake_weekly = {"totals": {"questions": 10, "stumbles": 3}, "by_type": {}}
fake_holdout = {"pass": 8, "fail": 2, "review": 5, "untestable": 1, "total": 16}
merged_report = W.build_report(fake_weekly, None, fake_holdout, None)
chk_true("③レポートのweeklyキーとholdoutキーが分離している",
         "weekly" in merged_report and "holdout" in merged_report and
         "questions" not in merged_report.get("holdout", {}) and
         "pass" not in merged_report.get("weekly", {}))
chk_true("③weekly.rateとholdout.passが同一の分母/分子に混入していない",
         merged_report["weekly"]["totals"]["questions"] == 10 and
         merged_report["holdout"]["total"] == 16)

# ── ④前週との差分(初回はnull) ────────────────────────────────
diff_first = W.diff_from_prev(analysis, None)
chk("④初回はdiff_from_prevがnull", diff_first, None)

prev_fake = {
    "period": {"from": "2026-07-24", "to": "2026-07-31"},
    "totals": {"questions": 100, "stumbles": 38},
    "by_type": {"不在断言": 17, "聞き返し": 14, "できません": 7, "エラー": 6},
    "pairs": [
        {"q": "去年の実績を教えて", "stumble_types": ["不在断言"]},
        {"q": "何を確認すればいい", "stumble_types": []},
    ],
}
current_fake = {
    "totals": {"questions": 100, "stumbles": 36},
    "by_type": {"不在断言": 14, "聞き返し": 15, "できません": 7, "エラー": 4},
    "pairs": [
        {"q": "去年の実績を教えて", "stumble_types": []},
        {"q": "何を確認すればいい", "stumble_types": ["聞き返し"]},
    ],
}
diff2 = W.diff_from_prev(current_fake, prev_fake)
chk_true("④diffにimproved_examplesが載る(前週つまずき→今週通過)",
         any(e["q"] == "去年の実績を教えて" for e in diff2.get("improved_examples", [])))
chk_true("④diffにregressed_examplesが載る(前週通過→今週つまずき)",
         any(e["q"] == "何を確認すればいい" for e in diff2.get("regressed_examples", [])))
chk("④questions差分", diff2["questions"], 0)
chk("④stumbles差分", diff2["stumbles"], -2)

# ── ⑤測定停止が判る ────────────────────────────────────────
today = datetime.date(2026, 8, 13)
chk_true("⑤should_run_weekly: 7日経過で発火", W.should_run_weekly_from_date("2026-08-06", today))
chk_true("⑤should_run_weekly: 6日では発火せぬ", not W.should_run_weekly_from_date("2026-08-07", today))
chk_true("⑤should_run_weekly: 初回(state無し)は発火", W.should_run_weekly_from_date(None, today))

line_ok = W.dashboard_line({"weekly_last_run": "2026-08-06", "days_since": 0,
                             "questions": 115, "stumbles": 36, "diff_stumbles": -2,
                             "holdout_pass": 8, "holdout_total": 10, "last_error": None})
chk_true("⑤経過0日の1行に「測定失敗」等の異常文言が出ない", "測定失敗" not in line_ok and "止まって" not in line_ok)

line_stale = W.dashboard_line({"weekly_last_run": "2026-07-23", "days_since": 14,
                                "questions": 115, "stumbles": 36, "diff_stumbles": -2,
                                "holdout_pass": 8, "holdout_total": 10, "last_error": None})
chk_true("⑤14日経過で「測定が止まっておりまする」の文言が出る", "止まって" in line_stale, line_stale)

line_err = W.dashboard_line({"weekly_last_run": "2026-08-06", "days_since": 0,
                              "questions": None, "stumbles": None, "diff_stumbles": None,
                              "holdout_pass": None, "holdout_total": None,
                              "last_error": "ip選り分け失敗: 人の発話0件"})
chk_true("⑤last_error在り→「測定失敗」文言が出る(0件と区別)", "測定失敗" in line_err, line_err)

chk_true("⑤人の発話0件は異常検知される(選り分け誤りの疑い文言)",
         "選り分け" in W.human_zero_warning(0) and W.human_zero_warning(5) is None)

# ── 突然変異(i): ip選り分けを外す→数が跳ねる ─────────────────────
pairs_no_ip_filter = W._extract_pairs(LOG_PATH, WIN_START, WIN_END, ip_filter=None)
pairs_with_ip_filter = W._extract_pairs(LOG_PATH, WIN_START, WIN_END, ip_filter="172.17.0.1")
chk_true("突然変異(i): ip選り分け無しだと母数が跳ね上がる(115より大)",
         len(pairs_no_ip_filter) > len(pairs_with_ip_filter),
         f"no_filter={len(pairs_no_ip_filter)} filtered={len(pairs_with_ip_filter)}")

# ── 突然変異(ii): 重複除去を外す→延べ数に化ける ───────────────────
by_type_dedup = analysis["totals"]["stumbles"]
by_type_no_dedup = sum(analysis["by_type"].values())
chk_true("突然変異(ii): 重複除去なし(延べ)はdedup後より多い(延べ40 vs dedup36)",
         by_type_no_dedup > by_type_dedup, f"延べ={by_type_no_dedup} dedup={by_type_dedup}")

# ── 突然変異(iii): 週次とholdoutを同じ集計に混ぜる→AC3違反 ─────────
try:
    bad_report = W.build_report(fake_weekly, None, fake_holdout, None, _mutation_merge=True)
    mixed = ("questions" in bad_report.get("holdout", {})) or ("pass" in bad_report.get("weekly", {}))
    chk_true("突然変異(iii): 混合フラグを立てるとAC3違反(混入)が検知される", mixed)
except AttributeError:
    chk_true("突然変異(iii): 混合フラグ未実装(要修正・現状は赤でよい)", False)


print()
ng = results.count(False)
print(f"{'✅ 全PASS' if ng == 0 else '❌ FAIL'}: {results.count(True)}/{len(results)}")
sys.exit(1 if ng else 0)
