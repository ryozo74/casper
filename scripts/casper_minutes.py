#!/usr/bin/env python3
"""M4 Phase 3: 議事録→タスク起票の純機構（LLM非経由・書き込みゼロ）。

議事録の `tasks`（`[type] 担当者：内容（期限）` 形式のアクションアイテム）を構造化候補にする。
起票そのものは承認カードを経て bulk_create_tasks（別層）。ここは"候補の構造化"まで＝人が検品して作るための素材。

vague なもの（担当プレースホルダ・曖昧な期限）は無理に埋めない（Fable: 憶測で機械項目を作らない）。
解決できる分だけ due/assignee を機構で確定し、残りは人が承認カードで埋める。
- parse_task_line(line, roster, today) → {raw, type, name, assignee_hint, assignee_uid, due_hint, due}
- extract_tasks(meeting, roster, today)  → 上記の配列（空行/重複は除外）
"""
import re

try:
    import casper_reschedule
except Exception:
    casper_reschedule = None

# 「担当者」等のプレースホルダ（実名でない）＝assignee とみなさない
_PLACEHOLDER = {"担当者", "担当", "未指定", "主管者", "主管", "各自", "全員", "みんな", "TBD", "未定"}
# 期限表現から日付にできぬもの（"最終調整"等は due にしない）
_NO_DATE_HINT = re.compile(r"最終|調整|回復|随時|継続|未定|適宜|以降|ごろ|頃$")

# Score の正規 type（bulk_create_tasks が受ける値）。議事録の [type] をこれへ写す。
SCORE_TYPES = ["animation", "layout", "comp", "fx", "lighting", "asset",
               "programming", "design", "testing", "shoot", "gs", "report", "other"]
_TYPE_ALIAS = {"anim": "animation", "アニメ": "animation", "アニメーション": "animation",
               "レイアウト": "layout", "コンポ": "comp", "compositing": "comp", "合成": "comp",
               "ライティング": "lighting", "ライト": "lighting", "light": "lighting",
               "エフェクト": "fx", "アセット": "asset", "プログラム": "programming",
               "デザイン": "design", "撮影": "shoot", "レポート": "report", "報告": "report",
               "review": "other", "meeting": "other", "mtg": "other"}
# 「既存への FB/確認」を示唆する語 / 「新規作業」を示唆する語（分類ヒント・確定は人）
_FB_HINT = re.compile(r"確認|チェック|調整|修正|リテイク|差[し]?戻|戻し|直し|見直|レビュー|反映|検討|FB|フィードバック")
_NEW_HINT = re.compile(r"作成|新規|新しく|作る|起こす|追加|リスト化|準備|着手|セットアップ")


def normalize_type(t):
    """議事録の [type] → Score 正規 type。未知は 'other'。"""
    t = (t or "").strip().lower()
    if t in SCORE_TYPES:
        return t
    return _TYPE_ALIAS.get(t, "other")


def classify(name):
    """項目が『既存へのFB』か『新規タスク』か（ヒント・断定でなく既定値。人が承認カードで確定）。
    FB語かつ新規語でない→fb / 新規語かつFB語でない→new / それ以外→unknown（既定は人にfb寄りで見せる）。"""
    nm = name or ""
    fb = bool(_FB_HINT.search(nm))
    new = bool(_NEW_HINT.search(nm))
    if fb and not new:
        return "fb"
    if new and not fb:
        return "new"
    return "unknown"


def _resolve_due(due_hint, today):
    if not (due_hint and casper_reschedule):
        return None
    if _NO_DATE_HINT.search(due_hint):
        return None
    if re.search(r"即時|即日|本日|今日|至急|すぐ", due_hint):
        return today.isoformat() if today else None
    iso, err = casper_reschedule.resolve_new_due(None, due_hint, today)
    return None if err else iso


def _match_username(name_hint, roster_names):
    """assignee_hint が既知の username（か display 名）に一致すれば uid を返す。曖昧なら None。"""
    if not (name_hint and roster_names):
        return None
    h = name_hint.strip().lower().rstrip("さん")
    for uid, nm in roster_names.items():
        n = str(nm or "").strip().lower()
        if n and (n == h or (len(h) >= 2 and h in n)):
            return str(uid)
    return None


def parse_task_line(line, roster_names=None, today=None):
    """1行のアクションアイテムを構造化。決定的・憶測しない。"""
    raw = (line or "").strip()
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", raw)
    ttype = (m.group(1).strip() if m else "")
    rest = (m.group(2) if m else raw).strip()

    due_hint = ""
    dm = re.search(r"[（(]([^（()）]*)[)）]\s*$", rest)       # 末尾の（期限）
    if dm:
        due_hint = dm.group(1).strip()
        rest = rest[:dm.start()].strip()

    assignee_hint = ""
    am = re.match(r"^([^：:]{1,14})[：:]\s*(.*)$", rest)       # 「X：内容」の X
    if am:
        cand = am.group(1).strip()
        body = am.group(2).strip()
        if cand not in _PLACEHOLDER:
            assignee_hint = cand
        rest = body or rest

    name = rest.strip("　 、,")
    return {"raw": raw, "type": ttype, "type_norm": normalize_type(ttype), "name": name,
            "kind": classify(name),                     # new / fb / unknown（人が承認カードで確定）
            "assignee_hint": assignee_hint,
            "assignee_uid": _match_username(assignee_hint, roster_names),
            "due_hint": due_hint,
            "due": _resolve_due(due_hint, today)}


def extract_tasks(meeting, roster_names=None, today=None):
    """議事録の tasks 配列 → 構造化候補（空/重複除外・元の順序保持）。"""
    out, seen = [], set()
    for line in (meeting.get("tasks") or []):
        p = parse_task_line(line, roster_names, today)
        key = p["name"]
        if not key or key in seen:
            continue
        seen.add(key)
        p["project_id"] = meeting.get("project_id")
        out.append(p)
    return out


if __name__ == "__main__":
    import datetime
    today = datetime.date(2026, 7, 17)
    roster = {"30": "tetsuo", "40": "terajima"}
    for ln in ["[fx] 担当者：グラデーションの確認（明日まで）",
               "[lighting] tetsuo：全体の明るさ調整（最終調整）",
               "[comp] 生徒：レンダリング（3日後）",
               "[review] 県道を確認: コード担当者 (即日)"]:
        print(parse_task_line(ln, roster, today))
