#!/usr/bin/env python3
"""M4 Phase3 議事録→タスク候補の回帰ゲート（純機構・インメモリ・書込ゼロ）。
parse_task_line（type/name/担当解決/期限解決・憶測しない）＋extract_tasks（空/重複除外）。全PASSで exit 0。"""
import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_minutes as M

TODAY = datetime.date(2026, 7, 17)
ROSTER = {"30": "tetsuo", "40": "terajima", "37": "rui"}
results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── type/name 抽出 ──
p = M.parse_task_line("[fx] 担当者：グラデーションの確認（明日まで）", ROSTER, TODAY)
chk("type=fx", p["type"], "fx")
chk("name=グラデーションの確認", p["name"], "グラデーションの確認")
chk("プレースホルダ担当者→assignee空(憶測しない)", p["assignee_uid"], None)
chk("期限『明日まで』→翌日", p["due"], "2026-07-18")

# ── 実名の担当は解決 ──
p2 = M.parse_task_line("[lighting] tetsuo：明るさ調整（3日後）", ROSTER, TODAY)
chk("assignee_hint=tetsuo", p2["assignee_hint"], "tetsuo")
chk("assignee_uid=30(username解決)", p2["assignee_uid"], "30")
chk("期限『3日後』→7/20", p2["due"], "2026-07-20")

# ── 曖昧な期限は due にしない（憶測しない）──
p3 = M.parse_task_line("[comp] terajima：最終ライティング（最終調整）", ROSTER, TODAY)
chk("『最終調整』はdueにしない", p3["due"], None)
chk("assignee=terajima→40", p3["assignee_uid"], "40")

# ── 即日/即時 → today ──
chk("即日→today", M.parse_task_line("[x] 主管者：確認（即日）", ROSTER, TODAY)["due"], "2026-07-17")
# ── 未知の担当名は uid 解決せず（誤アサインしない）──
chk("未知担当→uid None", M.parse_task_line("[x] 生徒：レンダリング（明日）", ROSTER, TODAY)["assignee_uid"], None)
# ── type 無し行も名前は取れる ──
chk("type無し行", M.parse_task_line("資料をまとめる（来週）", ROSTER, TODAY)["name"], "資料をまとめる")

# ── extract_tasks: 空/重複除外・project_id 付与 ──
MTG = {"project_id": "72", "tasks": [
    "[fx] 担当者：グラデーションの確認（明日まで）",
    "[fx] 担当者：グラデーションの確認（明日まで）",   # 重複
    "",                                                 # 空
    "[comp] tetsuo：レンダリング（即日）",
]}
ex = M.extract_tasks(MTG, ROSTER, TODAY)
chk("重複と空を除外し2件", len(ex), 2)
chk("各候補にproject_id付与", all(e["project_id"] == "72" for e in ex), True)
chk("2件目 assignee=tetsuo(30)", ex[1]["assignee_uid"], "30")

# ── type 正規化（Score正規値へ・未知→other）──
chk("fx→fx", M.normalize_type("fx"), "fx")
chk("ライティング→lighting", M.normalize_type("ライティング"), "lighting")
chk("review→other(Score型に無い)", M.normalize_type("review"), "other")
chk("未知→other", M.normalize_type("なにか"), "other")

# ── 新規 vs FB 分類（ヒント・人が確定）──
chk("『確認』→fb", M.classify("グラデーションの確認"), "fb")
chk("『調整』→fb", M.classify("全体の明るさ調整"), "fb")
chk("『リスト作成』→new", M.classify("5月のリスト作成"), "new")
chk("どちらでもない→unknown", M.classify("背景"), "unknown")

# ── parse に type_norm/kind が乗る ──
pk = M.parse_task_line("[review] 担当者：色味を確認（明日）", ROSTER, TODAY)
chk("type_norm=other", pk["type_norm"], "other")
chk("kind=fb(確認)", pk["kind"], "fb")

n = len(results)
p = sum(results)
print(f"\n=== gate_minutes: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
