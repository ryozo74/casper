#!/usr/bin/env python3
"""run_holdout.py の判定ロジック回帰試験(cmd_500第2便・AC7)。

実サーバ起動不要(judge()/summarize()は純粋関数として直接呼ぶ)。
欠陥A(_is_negatedをmust_any/count_markersにまで掛けていた誤り)の退行防止が主目的。

使い方: python3 test_run_holdout.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import run_holdout as rh

CRITERIA = json.load(open(os.path.join(HERE, "criteria.json"), encoding="utf-8"))["items"]

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# --- pack と criteria.json の一致検問(軍師設計・cmd_504) ---
# このファイルの固有名はpackから引く。fallbackを"<PJ名>"にするとcriteria.jsonのmust_any
# と噛み合わなくなるため、fallbackはcriteria.json(この試験のscope外・cmd_500の成果物)
# 自身のmust_any[0]から取る(=engineにPJ名の文字を一切持たせない)。packとcriteriaが
# ずれれば以下のcheckで試験自体が前提崩れを検知して赤くなる。
_CRITERIA_PJ = (CRITERIA.get("A04", {}).get("must_any") or [None])[0]
try:
    import pack_config as _pc
    _PJ = (_pc.get("examples", {}).get("project_names") or [None])[0] or _CRITERIA_PJ
except Exception:
    _PJ = _CRITERIA_PJ

check("前提: pack(examples.project_names[0])とcriteria.json(A04.must_any[0])が一致",
      _PJ == _CRITERIA_PJ, f"pack={_PJ!r} criteria={_CRITERIA_PJ!r}")


# --- 欠陥A回帰試験: A04実文(run2でfailした実応答そのもの) ---
# PJ名は本文に在るが、同じ文/近傍に「0件」「確認せぬ」等の否定語があるため、
# is_negatedをmust_anyにまで掛けると誤ってfailと判ぜられていた(cmd_500第2便是正対象)。
A04_REAL_TEXT = (
    f"{_PJ}プロジェクトのタスクをCalendarで照会しました。**\n"
    f"現在、{_PJ}に関連する進行中・未着手のタスクは0件です。\n\n"
    f"**{_PJ}** には Calendar 上、現在 **50件** のタスクが登録されています"
    "（「無い/0件」は母集合を確認せぬ誤りにつき訂正）。特定の条件（工程・確認待ち等）で"
    "絞りたい場合は、条件を明示してくだされ。"
)
v, reasons = rh.judge("A04", CRITERIA, A04_REAL_TEXT, [], [])
check(f"A04: 否定文脈近傍でもmust_anyの'{_PJ}'が充足しpassとなる(欠陥A回帰防止)",
      v == "pass", f"got verdict={v} reasons={reasons}")

# --- A05: 退行検査。PJ名への言及自体は否定文脈に隣接しない通常ケース ---
A05_REAL_TEXT = (
    f"{_PJ} は現在 **in-progress** で、納期（2026-07-30）から **7日超過** の状態です。\n\n"
    "本日のタスク登録はありませんが、プロジェクト自体は進行中として残っています。"
    "詳細なタスク一覧や進捗状況を確認しますか？"
)
v, reasons = rh.judge("A05", CRITERIA, A05_REAL_TEXT, [], [])
check(f"A05: must_anyの'{_PJ}'が充足しpassとなる", v == "pass", f"got verdict={v}")

# --- B01: count_markers(母集合語)は否定除外なしで判定される(欠陥A是正対象) ---
# 母集合語(Calendar/件/母集合)を伴わず断言のみのケースは、is_negatedの影響を受けず正しくfail。
B01_TEXT_NO_COUNT = f"はい、その通りです。8月5日は{_PJ}の試写日ではありません。次の日程は記載がありません。"
v, reasons = rh.judge("B01", CRITERIA, B01_TEXT_NO_COUNT, [], [])
check("B01: 母集合語を伴わぬ断言のみはfail", v == "fail", f"got verdict={v} reasons={reasons}")

B01_TEXT_WITH_COUNT = f"{_PJ}の試写日ではありません。Calendar上、8月5日には50件のタスクが確認されています。"
v, reasons = rh.judge("B01", CRITERIA, B01_TEXT_WITH_COUNT, [], [])
check("B01: 母集合語(Calendar)を伴えば断言があってもfailにならぬ(review以上)",
      v in ("review", "pass"), f"got verdict={v} reasons={reasons}")

# --- B02: 同様にcount_markersは否定除外なし ---
B02_TEXT_WITH_COUNT = "登録がございません。Calendar上には該当タスクが0件です。"
v, reasons = rh.judge("B02", CRITERIA, B02_TEXT_WITH_COUNT, [], [])
check("B02: 母集合語(Calendar/件)を伴えばdenial_without_countが不発火", v != "fail" or "denial_without_count" not in str(reasons),
      f"got verdict={v} reasons={reasons}")

# --- must_not(否定除外あり)は従来通り機能する: A04の"特定できません"は否定文脈なら除外されない ---
A04_NG_TEXT = f"{_PJ}というプロジェクトは特定できません。"
v, reasons = rh.judge("A04", CRITERIA, A04_NG_TEXT, [], [])
check("A04: must_notの'特定できません'は普通に検出されfail", v == "fail", f"got verdict={v} reasons={reasons}")

# --- A02: ng語追加(cmd_500第2便・AC9) ---
A02_REAL_TEXT = "対象となるプロジェクト名またはタスク名を指定してください。"
v, reasons = rh.judge("A02", CRITERIA, A02_REAL_TEXT, [], [])
check("A02: 追加ng語『指定してください』でfailと判定される(AC9)", v == "fail", f"got verdict={v} reasons={reasons}")

# --- G01: untestable区分(AC8) ---
v, reasons = rh.judge("G01", CRITERIA, "何らかの応答", [], [])
check("G01: untestable区分として判定される(AC8)", v == "untestable", f"got verdict={v} reasons={reasons}")

# summarize()がuntestableを分母から除外しつつtotalには含めることを確認
fake_round1 = [
    {"id": "P1", "type": "t", "verdict": "pass"},
    {"id": "F1", "type": "t", "verdict": "fail"},
    {"id": "U1", "type": "t", "verdict": "untestable"},
    {"id": "R1", "type": "t", "verdict": "review"},
]
summary = rh.summarize(fake_round1, {})
c = summary["counts"]
check("summarize(): total=4(untestable含む全数)", c["total"] == 4, f"got {c}")
check("summarize(): untestable=1として計上される", c["untestable"] == 1, f"got {c}")
check("summarize(): score分母はpass+fail=2のみ(untestable/reviewは除外)",
      summary["score_pass_over_pass_plus_fail"] == 0.5, f"got {summary['score_pass_over_pass_plus_fail']}")

# --- E01: prev_substituteがcriteria.jsonに設定されている(AC8) ---
check("E01: criteria.jsonにprev_substituteが設定されている",
      bool(CRITERIA.get("E01", {}).get("prev_substitute")),
      f"got {CRITERIA.get('E01', {}).get('prev_substitute')!r}")

print()
if _failures:
    print(f"=== FAIL: {len(_failures)}件不合格 — {_failures} ===")
    sys.exit(1)
print("=== 全試験PASS ===")
sys.exit(0)
