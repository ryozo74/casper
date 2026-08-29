#!/usr/bin/env python3
"""Casper エージェント用ツール (読取専用 v1)。qwen3 の function-calling から呼ばれる。
- search_vault: 右脳 Obsidian vault を全文検索 (RAG・ローカル)
- calendar_lookup: 左脳 Calendar をライブ照会 (read-only API)
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_rag

try:                                                # 平文token退避(M2秘匿): home secrets.env(0600)を env に載せる
    import casper_secrets
    casper_secrets.load_into_env()
except Exception:
    pass

RO_TOKEN = os.environ.get("CASPER_RO_TOKEN", "")   # 読取/議事録用(env優先・home secrets.env含む)
if not RO_TOKEN:                                    # env 無ければローカル秘匿ファイルから(gitignore済・write tokenと同方式)
    for _fn in (".casper_ro_token", "CASPER_RO_TOKEN.txt"):
        try:
            _rtf = os.path.join(os.path.dirname(os.path.abspath(__file__)), _fn)
            if not os.path.exists(_rtf):
                continue
            for _line in open(_rtf, encoding="utf-8").read().splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                # bare token か KEY=VALUE 形式(SCORE_READONLY_TOKEN=... / CASPER_RO_TOKEN=...)両対応 → 値だけ抽出
                if "=" in _line and _line.split("=", 1)[0].strip().upper().endswith("TOKEN"):
                    RO_TOKEN = _line.split("=", 1)[1].strip().strip('"').strip("'")
                else:
                    RO_TOKEN = _line.strip().strip('"').strip("'")
                if RO_TOKEN:
                    break
            if RO_TOKEN:
                break
        except Exception:
            pass
CAL = "http://192.168.44.253:8001/api/readonly"

def _calendar_lookup_description():
    """calendar_lookup の説明文をpack由来の例示語で組む(engineは雛形のみ・M5)。
    packにexamplesが無ければ一般プレースホルダへfail-closed(別スタジオがpack書き忘れても嘘のPJ名を例示せぬ)。"""
    try:
        import pack_config as _pc
        _ex = _pc.get("examples", {}) or {}
    except Exception:
        _ex = {}
    _pj = (_ex.get("project_names") or ["<PJ名>"])[0]
    _as = (_ex.get("assignees") or ["<担当者A>", "<担当者B>"])[:2]
    _as1 = _as[0] if len(_as) > 0 else "<担当者A>"
    _as2 = _as[1] if len(_as) > 1 else "<担当者B>"
    return ("左脳Calendarの最新データをライブ照会(プロジェクト/タスク/メンバー)。"
            f"★PJ名({_pj}等)が質問に出たら必ず query にそのPJ名を渡せ。"
            f"例: {_pj}の遅延→{{kind:'tasks',query:'{_pj}',status:'delayed'}} / "
            "今日のタスク→{kind:'tasks',due:'today',active:true} / "
            f"{_as1}担当の遅延→{{kind:'tasks',query:'{_pj}',status:'delayed',assignee:'{_as1}'}} / "
            f"{_as2}の担当全部→{{kind:'tasks',query:'{_pj}',assignee:'{_as2}'}}。"
            "結果に total(全件数)を含む。0件なら本当に0、推測で『無い』と言うな。")


TOOLS = [
    {"type": "function", "function": {
        "name": "search_vault",
        "description": "社内Obsidian vault(過去の監督フィードバック・LINE対話・PJアーカイブ・人物スキル・会社情報)を全文検索する。過去の経緯/指摘/誰が何をしたか等を調べる時に使う。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "検索語(日本語可)。例: 'TVCM c14 トラッキング'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "calendar_lookup",
        "description": _calendar_lookup_description(),
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["projects", "tasks", "users"], "description": "照会対象"},
            "query": {"type": "string", "description": "PJ名やタスク名で絞り込み。PJ名が質問に在れば必ず渡す"},
            "project_id": {"type": "integer", "description": "tasks 照会時の project_id 絞り込み(任意)"},
            "due": {"type": "string", "description": "tasks 期日絞り込み(任意)。'today'=本日締切"},
            "active": {"type": "boolean", "description": "true で未完(completed/approved除く)のみ(任意)"},
            "status": {"type": "string", "description": "tasks 状態絞り込み(任意)。例 'delayed'(遅延)/'todo'/'in-progress'/'retake'"},
            "assignee": {"type": "string", "description": "tasks 担当者で絞り込み(任意)。ユーザー名 例 'rui' 'hori'。日本語名(高井等)はローマ字username 'ryoji' 等で"}},
            "required": ["kind"]}}},
]
# cmd_501: web_search は qwen の tool 呼出には委ねぬ(指示: 発火は機構が判ずる)。
# ここには載せず、chat_server.py 側で確定的に(_asks_about_casper等と同型の判定で)発火・注入する
# (casper_web.should_search/build_query/search)。TOOLSに載せぬ=qwenが自発的に呼べる余地自体を持たぬ設計。


def _get(path):
    req = urllib.request.Request(CAL + path, headers={"X-Readonly-Token": RO_TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_vault(query):
    hits = casper_rag.search(query or "", k=8)
    return "\n".join(hits) if hits else "(該当する記録なし)"


def calendar_lookup(kind, query="", project_id=None, due=None, active=False, status=None, assignee=None):
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
        if status:                              # status 絞り込み(例 delayed) でトークン圧縮
            sl = status.lower().replace("_", "-")
            d = [t for t in d if (t.get("status") or "").lower().replace("_", "-") == sl]
        umap = {u.get("id"): (u.get("username") or u.get("name") or str(u.get("id")))
                for u in _get("/users?limit=200").get("items", [])}
        if assignee:                            # 担当者で絞り込み(ユーザー名 部分一致→uid)
            al = assignee.lower()
            aids = {uid for uid, nm in umap.items() if al in str(nm).lower()}
            d = [t for t in d if t.get("assigned_to") in aids]
        total = len(d)
        rows = [{"name": t.get("name"), "status": t.get("status"),
                 "assignee": umap.get(t.get("assigned_to"), "未割当"),
                 "due_date": str(t.get("due_date") or "")[:10],
                 "shotID": t.get("shotID")} for t in d[:80]]
        out = {"total": total, "shown": len(rows), "tasks": rows}
        if total > len(rows):
            out["note"] = f"全{total}件中{len(rows)}件表示(status/projectで絞ると全件取得可)"
        return json.dumps(out, ensure_ascii=False)
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
                                   args.get("project_id"), args.get("due"), args.get("active", False),
                                   args.get("status"), args.get("assignee"))
    except Exception as e:
        return f"(tool error: {e})"
    return f"(unknown tool: {name})"
