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
        "description": "左脳Calendarの最新データをライブ照会。プロジェクト一覧/状態、タスク、メンバーを取得。",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["projects", "tasks", "users"], "description": "照会対象"},
            "query": {"type": "string", "description": "名前での絞り込み(任意)"},
            "project_id": {"type": "integer", "description": "tasks 照会時の project_id 絞り込み(任意)"}},
            "required": ["kind"]}}},
]


def _get(path):
    req = urllib.request.Request(CAL + path, headers={"X-Readonly-Token": RO_TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_vault(query):
    hits = casper_rag.search(query or "", k=8)
    return "\n".join(hits) if hits else "(該当する記録なし)"


def calendar_lookup(kind, query="", project_id=None):
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
        path = "/tasks?limit=200" + (f"&project_id={pid}" if pid else "")
        d = _get(path)["items"]
        if not pid and q:                      # PJ未解決なら task名で絞る
            d = [t for t in d if q in (t.get("name") or "").lower()]
        return json.dumps([{k: t.get(k) for k in ("id", "name", "status", "assigned_to", "project_id")}
                           for t in d[:40]], ensure_ascii=False)
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
            return calendar_lookup(args.get("kind", "projects"), args.get("query", ""), args.get("project_id"))
    except Exception as e:
        return f"(tool error: {e})"
    return f"(unknown tool: {name})"
