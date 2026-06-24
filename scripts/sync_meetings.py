#!/usr/bin/env python3
"""Calendar の議事録 → vault/10_meetings/ へ同期(新規会議のみ追記・既存は保全)。
RAG 全文検索の鮮度確保用。手動 or cron で実行。
トークンは CASPER_RO_TOKEN(env) → 無ければ dump_users._token()(powershell で X: の RO トークン)。
使い方: python3 sync_meetings.py [--force]   (--force で既存も上書き更新)
"""
import os, re, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "vault", "10_meetings"))
FORCE = "--force" in sys.argv

import dump_users, casper_tools
os.environ.setdefault("CASPER_RO_TOKEN", dump_users._token())
casper_tools.RO_TOKEN = os.environ["CASPER_RO_TOKEN"]


def _list(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _san(t):
    return re.sub(r'[#/\\:*?"<>|]', "", str(t or "")).strip()[:80]


def main():
    # project_id -> name
    pmap = {}
    try:
        for p in casper_tools._get("/projects?limit=500").get("items", []):
            pmap[p.get("id")] = p.get("name") or p.get("title") or str(p.get("id"))
    except Exception as e:
        print("(projects 取得失敗・project名はidで代替):", e)

    meetings = casper_tools._get("/meetings?limit=500").get("items", [])
    # 既存 vault の meeting id(frontmatter meeting_id か mtg_<id>_ 接頭辞)
    present = set()
    for fn in os.listdir(OUT) if os.path.isdir(OUT) else []:
        m = re.match(r"mtg_(\d+)_", fn)
        if m:
            present.add(int(m.group(1)))

    new_n, skip_n = 0, 0
    for mt in meetings:
        mid = mt.get("id")
        if mid is None:
            continue
        if int(mid) in present and not FORCE:
            skip_n += 1
            continue
        title = mt.get("title") or f"meeting_{mid}"
        proj = pmap.get(mt.get("project_id"), str(mt.get("project_id") or ""))
        date = str(mt.get("date") or "")[:10]
        att = mt.get("attendees") or "None"
        decisions = _list(mt.get("decisions"))
        tasks = _list(mt.get("tasks"))
        dps = _list(mt.get("discussion_points"))
        dls = _list(mt.get("deadlines"))
        transcript = (mt.get("transcript") or "").strip()

        body = [
            "---",
            f"name: 議事録 {title} ({proj})",
            "type: meeting",
            f"project: {proj}",
            f"date: {date}",
            f"attendees: {att}",
            f"meeting_id: {mid}",
            "tags: [casper, meeting, 議事録, 決定]",
            "---",
            "",
            f"# 議事録: {title}",
            f"PJ: {proj} / 日付: {date} / 参加: {att}",
            f"キーワード: 議事録 会議 決定 経緯 {proj} {title}",
            "",
            "## 決定事項",
            ("\n".join("- " + d for d in decisions) if decisions else ""),
            "",
            "## タスク",
            ("\n".join("- " + t for t in tasks) if tasks else ""),
            "",
            "## 議論ポイント",
            ("\n".join("- " + d for d in dps) if dps else ""),
            "",
            "## 期限",
            ("\n".join("- " + d for d in dls) if dls else ""),
            "",
            "## 全文文字起こし (transcript)",
            transcript,
            "",
        ]
        fname = f"mtg_{mid}_{_san(title)}.md"
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        new_n += 1
        print(("更新" if int(mid) in present else "新規") + f": {fname}")

    print(f"\n完了: Calendar {len(meetings)}件 / 新規・更新 {new_n}件 / 既存スキップ {skip_n}件")
    if new_n:
        print("→ RAG 全文検索へ反映するには索引再構築が要る(字面: casper_rag / 意味: casper_embed の build)。")


if __name__ == "__main__":
    main()
