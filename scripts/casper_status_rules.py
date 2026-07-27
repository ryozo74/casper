#!/usr/bin/env python3
"""タスク/PJ の派生判断(完了・対象外・納期超過)の【単一機構】。

真実源 = Calendar API が返す `status_category`(todo/in_progress/review/completed/held)。
status 文字列の集合をハードコードして判断してはならぬ——category が無い時の fallback に限る。
(掟: 色/ラベル/カテゴリは API 単一ソース・ハードコード禁止 / 件数と一覧は同一関数)

■ なぜ在るか(2026-07-27)
chat_server の `_TASK_NOT_OVERDUE = {"deliver","omit"}` は、ニブ資料2026-07-08 の
isOverdue 定義(due<today かつ status∉{deliver,omit})をそのまま写した定数であった。
だが Calendar 実データでは **ap / client_ap も status_category=completed**(ニブ殿 2026-07-24 実コード回答)。
結果、承認済タスクが「🔴N日超過」と誤表示されていた(実測: 超過表示73件のうち55件=75%が誤り)。
同種の集合が chat_server / casper_notify / casper_meeting に散在していたため、本モジュールへ畳む。

■ 語彙(ニブ殿 2026-07-24: canonical は9値)
  wt / mk / wip / qc / qc_fb / ap / client_ap / deliver / omit
  category: todo=mk / in_progress=wip / review=qc,qc_fb /
            completed=ap,client_ap,deliver / held=wt,omit
旧ラベル(todo/in-progress/review/approved/completed/delayed/retake 等)は Calendar 側の
LEGACY_STATUS_MAP で9値へ写像される。ゆえに本モジュールの fallback も旧値を吸収する。
"""

# ── 派生の基準は category(API単一ソース) ────────────────────────────────
# 完了(completed)と対象外(held)は「納期超過に成り得ない」＝非活動。
INACTIVE_CATEGORIES = frozenset({"completed", "held"})
DONE_CATEGORIES = frozenset({"completed"})

# ── fallback: category が欠落した応答/旧データ用(canonical 9値＋旧値互換) ──
# ★ここに status を足す前に「API が category を返していないか」を必ず疑うこと。
TASK_INACTIVE_FALLBACK = frozenset({
    "ap", "client_ap", "deliver",                      # canonical completed
    "wt", "omit",                                       # canonical held
    "completed", "done", "complete", "approved", "client-ap",   # 旧値互換(completed へ写像)
    "cancelled", "canceled",                            # 旧値互換(held 相当)
})
TASK_DONE_FALLBACK = frozenset({
    "ap", "client_ap", "deliver",
    "completed", "done", "complete", "approved", "client-ap",
})
# PJ は status_category を持たぬ(実測: 応答キーに status_category 無し・値は completed/in-progress/cancelled)。
PJ_INACTIVE_FALLBACK = frozenset({
    "completed", "done", "complete", "cancelled", "canceled",
    "deliver", "omit", "approved",
})


def _norm(v):
    return str(v or "").strip().lower()


def is_inactive(status, category=None, scope="task"):
    """納期超過に成り得ぬ(完了 or 対象外)か。category があればそれが正、無ければ status で fallback。"""
    cat = _norm(category)
    if cat:
        return cat in INACTIVE_CATEGORIES
    fallback = PJ_INACTIVE_FALLBACK if scope == "pj" else TASK_INACTIVE_FALLBACK
    return _norm(status) in fallback


def is_done(status, category=None):
    """完了か(held は完了に非ず)。category があればそれが正。"""
    cat = _norm(category)
    if cat:
        return cat in DONE_CATEGORIES
    return _norm(status) in TASK_DONE_FALLBACK


def overdue_days(due, status, category=None, today=None, scope="task"):
    """返り: 超過日数(int>0) / 0(超過でない) / None(日付が無い・不正)。
    ★None(判定不能)と 0(超過でない)は別物として返す——呼び手が取り違えぬよう出口を分ける。"""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
    except Exception:
        return None
    if is_inactive(status, category, scope):
        return 0
    today = today or _dt.date.today()
    return (today - d).days if d < today else 0


def is_overdue(due, status, category=None, today=None, scope="task"):
    """超過か。判定不能(None)は False 側に倒す(不明を超過と名乗らせぬ)。"""
    od = overdue_days(due, status, category, today, scope)
    return bool(od and od > 0)


def task_overdue_days(task, today=None):
    """タスク dict から直接。category の取り出しを呼び手ごとに書かせない(取り違え防止)。"""
    if not isinstance(task, dict):
        return None
    return overdue_days(task.get("due_date"), task.get("status"),
                        task.get("status_category"), today, "task")
