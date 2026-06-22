#!/usr/bin/env python3
"""Casper エージェント用ツール (読取専用 v1)。qwen3 の function-calling から呼ばれる。
- search_vault: 右脳 Obsidian vault を全文検索 (RAG・ローカル)
- calendar_lookup: 左脳 Calendar をライブ照会 (read-only API)
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_rag

RO_TOKEN = os.environ.get("CASPER_RO_TOKEN", "")
CAL = "http://192.168.44.253:8001/api/readonly"

TOOLS = [
    {"type": "function", "function": {
        "name": "search_vault",
        "description": "社内Obsidian vault(過去の監督フィードバック・LINE対話・PJアーカイブ・人物スキル・会社情報)を全文検索する。過去の経緯/指摘/誰が何をしたか等を調べる時に使う。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "検索語(日本語可)。例: 'TVCM c14 トラッキング'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "calendar_lookup",
        "description": "左脳Calendarの最新データをライブ照会。プロジェクト一覧/状態、タスク、メンバーを取得。"
                       "『今日のタスク』は kind=tasks, due='today', active=true で取れる。",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["projects", "tasks", "users"], "description": "照会対象"},
            "query": {"type": "string", "description": "名前での絞り込み(任意)"},
            "project_id": {"type": "integer", "description": "tasks 照会時の project_id 絞り込み(任意)"},
            "due": {"type": "string", "description": "tasks 期日絞り込み(任意)。'today'=本日締切"},
            "active": {"type": "boolean", "description": "true で未完(completed/approved除く)のみ(任意)"}},
            "required": ["kind"]}}},
]


def _get(path):
    req = urllib.request.Request(CAL + path, headers={"X-Readonly-Token": RO_TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_vault(query):
    hits = casper_rag.search(query or "", k=8)
    return "\n".join(hits) if hits else "(該当する記録なし)"


def calendar_lookup(kind, query="", project_id=None, due=None, active=False):
    q = (query or "").lower()
    if kind == "projects":
        d = _get("/projects?limit=200")["items"]
        if q:
            d = [p for p in d if q in (p.get("name") or "").lower()]
        return json.dumps([{k: p.get(k) for k in ("id", "name", "status", "start_date", "end_date")}
                           for p in d[:30]], ensure_ascii=False)
    if kind == "tasks":
        pid = project_id
        if not pid and q:                      # query をPJ名として解決(多段不要に)
            projs = _get("/projects?limit=200")["items"]
            match = [p for p in projs if q in (p.get("name") or "").lower()]
            if match:
                pid = match[0]["id"]
        d = []                                  # 全件ページング取得
        for off in (0, 500, 1000):
            page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
            d += page
            if len(page) < 500:
                break
        if pid:
            d = [t for t in d if str(t.get("project_id")) == str(pid)]
        elif q:                                 # PJ未解決なら task名で絞る
            d = [t for t in d if q in (t.get("name") or "").lower()]
        if due == "today":
            import datetime
            today = datetime.date.today().isoformat()
            d = [t for t in d if str(t.get("due_date") or "").startswith(today)]
        if active:
            d = [t for t in d if (t.get("status") or "").lower().replace("_", "-")
                 not in ("completed", "approved", "cancelled")]
        umap = {u.get("id"): (u.get("username") or u.get("name") or str(u.get("id")))
                for u in _get("/users?limit=200").get("items", [])}
        return json.dumps([{"id": t.get("id"), "name": t.get("name"), "status": t.get("status"),
                            "assignee": umap.get(t.get("assigned_to"), "未割当"),
                            "due_date": str(t.get("due_date") or "")[:10],
                            "shotID": t.get("shotID"), "project_id": t.get("project_id")}
                           for t in d[:60]], ensure_ascii=False)
    if kind == "users":
        d = _get("/users?limit=200")["items"]
        return json.dumps([{k: u.get(k) for k in ("id", "username", "role")} for u in d[:40]],
                          ensure_ascii=False)
    return "(unknown kind)"


def execute(name, args):
    try:
        if name == "search_vault":
            return search_vault(args.get("query", ""))
        if name == "calendar_lookup":
            return calendar_lookup(args.get("kind", "projects"), args.get("query", ""),
                                   args.get("project_id"), args.get("due"), args.get("active", False))
    except Exception as e:
        return f"(tool error: {e})"
    return f"(unknown tool: {name})"
