#!/usr/bin/env python3
"""Casper チャット鯖 — ブラウザ ⇄ (この鯖) ⇄ z8a Ollama のストリーミングプロキシ。

ブラウザは localhost:PORT を見るだけ。egress(z8a 接続)は本鯖が肩代わりするため
CORS 不要・ブラウザから外部IPへ直接出ない。

Usage:
  python3 chat_server.py --endpoint http://192.168.44.119:11434 --model qwen3:14b --port 8770
"""
import argparse, datetime, http.cookies, json, os, re, shutil, subprocess, sys, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import casper_rag
except Exception:
    casper_rag = None
try:
    import casper_tools
except Exception:
    casper_tools = None
try:
    import casper_extract
except Exception:
    casper_extract = None
import base64 as _b64
ASSET_DIR = os.path.join(HERE, "..", "vault", "50_asset_shadows")
VAULT = os.path.join(HERE, "..", "vault")
ASSET_FILES = os.path.join(ASSET_DIR, "files")
ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", default="http://192.168.44.119:11434")
ap.add_argument("--model", default="qwen3:14b")
ap.add_argument("--port", type=int, default=8770)
A = ap.parse_args()
OLLAMA = A.endpoint.rstrip("/") + "/api/chat"

# --- backend 切替 (ローカル Ollama / クラウド Anthropic つなぎ) ---
BACKEND = os.environ.get("CASPER_BACKEND", "ollama").lower()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("CASPER_ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# claude CLI 迂回 (Max ライセンス利用・つなぎ)
CLI_MODEL = os.environ.get("CASPER_CLI_MODEL", "sonnet")
CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN") or shutil.which("claude") or "claude"
CLI_CWD = "/tmp/casper_cli"        # 中立 cwd (プロジェクトの CLAUDE.md を継がぬよう)
os.makedirs(CLI_CWD, exist_ok=True)
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
AURORA_RENDER = os.path.join(PROJECT_ROOT, "skills", "aurora", "scripts", "render_local.py")
DIAG_DIR = os.path.join(HERE, "diagrams")
ASSETS_DIR = os.path.join(HERE, "assets")
FEEDBACK_LOG = os.path.join(HERE, "feedback_log.jsonl")
CONVO_LOG = os.path.join(HERE, "conversation_log.jsonl")


def identify(handler):
    """発信元を識別。優先: X-Actor-User-Id(組込み時 Score/Calendar から) > cookie uid > 匿名 sid。
    sid が無ければ新規発行(new_sid に入れて Set-Cookie する)。最終的に uid=Calendar uid へ寄せる。"""
    uid = (handler.headers.get("X-Actor-User-Id", "") or "").strip()
    ck = http.cookies.SimpleCookie()
    try:
        ck.load(handler.headers.get("Cookie", "") or "")
    except Exception:
        pass
    if not uid and "casper_uid" in ck:
        uid = ck["casper_uid"].value
    sid = ck["casper_sid"].value if "casper_sid" in ck else ""
    new_sid = ""
    if not sid:
        sid = uuid.uuid4().hex[:16]
        new_sid = sid
    ip = handler.client_address[0] if getattr(handler, "client_address", None) else ""
    return {"uid": uid, "sid": sid, "ip": ip, "new_sid": new_sid}


def calendar_digest(query):
    """Calendar 左脳の最新ライブデータを必要時に取得し digest 文字列で返す。
    claude_cli backend は tool を呼べぬため、ここで先読みしてプロンプトに注入する(search_vault=RAG と同様)。"""
    if not casper_tools:
        return ""
    q = query or ""
    if not re.search(r"タスク|task|予定|今日|本日|スケジュール|schedule|プロジェクト|PJ|案件|担当|進捗|締切|納期|誰|稼働|アサイン|assign", q, re.I):
        return ""
    today = datetime.date.today().isoformat()
    parts = []
    try:
        _get = casper_tools._get
        umap = {u.get("id"): (u.get("username") or u.get("name") or str(u.get("id")))
                for u in _get("/users?limit=200").get("items", [])}
        tasks = []
        for off in (0, 500, 1000):
            page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
            tasks += page
            if len(page) < 500:
                break

        def active(s):
            return (s or "").lower().replace("_", "-") not in ("completed", "approved", "cancelled")
        due = [t for t in tasks if str(t.get("due_date") or "").startswith(today) and active(t.get("status"))]
        if due:
            parts.append(f"本日({today})締切のタスク {len(due)}件:")
            for t in due[:45]:
                parts.append(f"  - {t.get('name')} [{t.get('status')}] 担当:{umap.get(t.get('assigned_to'),'未割当')}")
        else:
            parts.append(f"本日({today})締切のタスク: なし")
        ip = sum(1 for t in tasks if (t.get("status") or "") == "in-progress")
        todo = sum(1 for t in tasks if (t.get("status") or "") == "todo")
        parts.append(f"(タスク全体: 進行中 {ip} / todo {todo} / 総数 {len(tasks)})")
        ev = _get("/events?limit=500").get("items", [])
        tev = [e for e in ev if str(e.get("date") or "").startswith(today)
               or str(e.get("start_time") or "").startswith(today)]
        parts.append("本日の予定(events): " + (" / ".join(
            f"{e.get('title')}" for e in tev[:15]) if tev else "なし"))
    except Exception as e:
        return f"\n\n## Calendar 左脳\n(取得失敗: {e} — 取得不能を明示し、推測で埋めるな)"
    return "\n\n## Calendar 左脳（最新ライブ・" + today + "）\n" + "\n".join(parts)


def _parse_hint(hint):
    """『c5のチェックQT』等の一言ヒントから cut/工程/用途/形式 を推定。"""
    h = hint or ""
    cut = None
    m = re.search(r"[cC][\s_\-]?0*(\d{1,3})(?!\d)", h) or re.search(r"(?:cut|カット)\s*0*(\d{1,3})(?!\d)", h, re.I)
    if m:
        cut = int(m.group(1))
    proc = None
    for k, v in (("レイアウト", "lay"), ("lay", "lay"), ("アニメ", "anim"), ("anim", "anim"),
                 ("fx", "fx"), ("エフェクト", "fx"), ("ライティング", "lighting"), ("light", "lighting"),
                 ("コンポ", "comp"), ("comp", "comp"), ("合成", "comp"), ("モデル", "model"), ("model", "model")):
        if k.lower() in h.lower():
            proc = v
            break
    intent = "qc" if re.search(r"チェック|qc|レビュー|review|提出|check", h, re.I) else None
    fmt = ("video" if re.search(r"qt|mov|mp4|動画|ムービー", h, re.I)
           else ("image" if re.search(r"png|jpe?g|画像|静止画", h, re.I) else None))
    return {"cut": cut, "proc": proc, "intent": intent, "fmt": fmt}


def uploader_resolve(hint, vision_desc="", uid=None, max_c=6):
    """投入物のヒント×vision×本人タスクから、提出先候補タスクを確度順に返す(読取のみ)。"""
    if not casper_tools:
        return {"parsed": {}, "candidates": []}
    p = _parse_hint(hint)
    vd = (vision_desc or "").lower()
    _get = casper_tools._get
    tasks = []
    for off in (0, 500, 1000):
        page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
        tasks += page
        if len(page) < 500:
            break
    pm = {str(x["id"]): x.get("name") for x in _get("/projects?limit=200").get("items", [])}

    def active(s):
        return (s or "").lower().replace("_", "-") in ("todo", "in-progress", "review", "retake", "delayed")
    scored = []
    for t in tasks:
        if not active(t.get("status")):
            continue
        if uid and str(t.get("assigned_to")) != str(uid):
            continue
        name = (t.get("name") or "")
        shotid = str(t.get("shotID") or "")
        sc, why = 0, []
        if p["cut"] is not None:
            cands_cut = (f"c{p['cut']:03d}", f"c{p['cut']:02d}", f"c{p['cut']}")
            sl = shotid.lower(); nl = name.lower()
            if any(c in sl for c in cands_cut) or any(c in nl for c in cands_cut):
                sc += 3
                why.append(f"cut{p['cut']}")
        if p["proc"]:
            if p["proc"] in name.lower() or p["proc"] == (t.get("type") or "").lower():
                sc += 2
                why.append(p["proc"])
            if p["proc"] in vd:                       # vision で推定した工程とも一致
                sc += 1
        if str(t.get("due_date") or "").startswith(datetime.date.today().isoformat()):
            sc += 1
            why.append("本日締切")
        scored.append((sc, t, why))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("due_date") or "")))
    out = [{"id": t.get("id"), "name": t.get("name"), "status": t.get("status"),
            "project": pm.get(str(t.get("project_id")), t.get("project_id")),
            "due": str(t.get("due_date") or "")[:10], "shotID": t.get("shotID"),
            "score": sc, "why": why} for sc, t, why in scored[:max_c]]
    return {"parsed": p, "candidates": out}


def uploader_crosscheck(task_id, hint="", uid=None):
    """選択タスクの仕様(shot)＋決定(decisions)から、画像照合の基準と確認質問を返す(読取のみ)。"""
    if not casper_tools or not task_id:
        return {"questions": [], "spec": "", "decisions": []}
    _get = casper_tools._get
    task = None
    for off in (0, 500, 1000):
        page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
        for t in page:
            if str(t.get("id")) == str(task_id):
                task = t
                break
        if task or len(page) < 500:
            break
    if not task:
        return {"questions": ["仕様/指示どおりに作成しましたか?"], "spec": "(task not found)", "decisions": []}
    pid, shotid = task.get("project_id"), str(task.get("shotID") or "")
    spec = []
    try:
        for s in _get("/shots?limit=500").get("items", []):
            if shotid and (str(s.get("shot_code")) == shotid or str(s.get("id")) == shotid):
                for k in ("action", "note"):
                    if s.get(k):
                        spec.append(f"{k}: {s.get(k)}")
                if s.get("check_items"):
                    spec.append(f"check_items: {s.get('check_items')}")
                break
    except Exception:
        pass
    decs = []
    try:
        for d in _get("/decisions?limit=200").get("items", []):
            if str(d.get("project_id")) == str(pid) and not d.get("superseded"):
                decs.append(str(d.get("content")))
    except Exception:
        pass
    questions = [f"決定『{d[:50]}』は反映済?" for d in decs[:4]] or ["仕様/指示どおりに作成しましたか?"]
    return {"task": task.get("name"), "spec": " / ".join(spec)[:600], "decisions": decs[:6], "questions": questions}


def meeting_digest(query):
    """会議/議事録/決定 クエリ時に最新会議の要約を先読み注入(generic/時系列クエリの取りこぼし対策)。"""
    if not casper_tools:
        return ""
    if not re.search(r"会議|議事録|決定|決ま|MTG|mtg|打ち合わせ|ミーティング|meeting|アジェンダ|議論", query or "", re.I):
        return ""
    try:
        _get = casper_tools._get
        ms = _get("/meetings?limit=50").get("items", [])
        ms.sort(key=lambda m: str(m.get("date") or ""), reverse=True)
        pm = {str(p["id"]): p.get("name") for p in _get("/projects?limit=200").get("items", [])}
        parts = [f"最新の会議 {len(ms)}件(新しい順・要約)。詳細/全文は RAG の各議事録ノートに在り:"]
        for m in ms[:12]:
            pj = pm.get(str(m.get("project_id")), m.get("project_id"))
            decs = m.get("decisions")
            ds = decs if isinstance(decs, str) else ("; ".join(str(x) for x in decs) if isinstance(decs, list) else "")
            parts.append(f"- [{str(m.get('date'))[:10]}] {m.get('title')} (PJ:{pj}) 決定:{str(ds)[:220]}")
        return "\n\n## Calendar 議事録（最新・要約）\n" + "\n".join(parts)
    except Exception:
        return ""


DEV_LOG = os.path.join(HERE, "dev_log.jsonl")


def dev_log(who, user_msg, answer, meta=None):
    """開発用: 1ターンの『発言・回答・思考(thinking)・注入材料』を残す(デバッグ/改善用)。"""
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "uid": who.get("uid", ""), "sid": who.get("sid", ""),
               "user": str(user_msg)[:1000], "answer": str(answer)[:2000]}
        if meta:
            m = dict(meta)
            if isinstance(m.get("hits"), list):
                m["hits"] = [str(h)[:200] for h in m["hits"][:8]]
            m["thinking"] = str(m.get("thinking", ""))[:4000]
            rec.update(m)
        with open(DEV_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_convo(who, role, content, extra=None):
    """会話を発信元ごとの順序付きスレッドとして記録(文脈=流れ を資産化)。"""
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "uid": who.get("uid", ""), "sid": who.get("sid", ""), "ip": who.get("ip", ""),
               "role": role, "content": str(content)[:2000]}
        if extra:
            rec.update(extra)
        with open(CONVO_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

BASE_SYS = (
    "あなたは『Casper』、社内の入力負担ゼロを目指す伴走AIアシスタントです。丁寧かつ簡潔な日本語で答えよ。\n"
    "【ツール使用は必須】社内の具体情報(過去の監督フィードバック/LINE対話/PJの詳細やタスク/最新の状態/担当者 等)が"
    "少しでも要る質問には、推測や『未取得』で済ませず、必ず下記ツールを呼べ:\n"
    "- search_vault(query): 過去の経緯・指摘・対話・PJアーカイブ・人物スキルを vault 全文検索\n"
    "- calendar_lookup(kind, query, project_id): 左脳Calendarの最新 projects/tasks/users を取得"
    "(進行中PJは kind='projects' を取得し status='in-progress' で絞れ)\n"
    "下の要約に答えがあっても、確証・本文・最新性が要るならツールで裏取りせよ。ツール結果を根拠に具体的に答える。\n"
    "【短い語の入力】ユーザー入力が人物名/PJ名/タスク名など短い語だけの場合は、その対象を社内記録で調べ説明せよ(選択肢からの深掘りとみなす)。\n"
    "【主観の許容】『得意/良い/最適/向いている』等の評価を問われた場合、唯一の正解を装わず、"
    "根拠(スキルシート/過去PJの担当・実績/フィードバック)に基づく候補を複数挙げよ。評価は人や基準で異なって構わない。"
    "断定せず『候補』として選べる形にし、可能なら各候補の根拠を一言添える。")


def build_sys():
    """毎リクエストで社内ナレッジ digest (左脳+右脳) を読み込み system prompt に注入。"""
    ctx_path = os.path.join(HERE, "casper_context.md")
    ctx = ""
    if os.path.exists(ctx_path):
        try:
            ctx = open(ctx_path, encoding="utf-8").read()
        except Exception:
            ctx = ""
    return (ctx + "\n\n---\n" + BASE_SYS) if ctx else BASE_SYS


def ollama_chat(messages, tools=None):
    body = {"model": A.model, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def strip_think(s):
    import re
    return re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()


def claude_cli_text(prompt, allow=None):
    """Max ライセンスの claude CLI を headless(-p) で叩く (迂回 backend)。
    allow=['WebSearch','WebFetch'] 等で特定ツールのみ解禁 (権限系は無効化しない)。"""
    args = [CLAUDE_BIN, "-p", "--model", CLI_MODEL]
    if allow:
        args += ["--allowedTools"] + allow
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           timeout=400, cwd=CLI_CWD)
        return (r.stdout or "").strip() or ("[claude-cli] " + (r.stderr or "no output")[:300])
    except Exception as e:
        return f"[claude-cli error] {e}"


def claude_cli_vision(image_path, prompt):
    """claude CLI に画像を Read させて(=vision) 解析。Sonnet のマルチモーダルを活用。"""
    ap = os.path.abspath(image_path)
    img_dir = os.path.dirname(ap)
    full = f"次の画像ファイルを Read ツールで開いて中身を視認し、解析せよ:\n{ap}\n\n{prompt}"
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", CLI_MODEL,
                            "--add-dir", img_dir, "--allowedTools", "Read"],
                           input=full, capture_output=True, text=True, timeout=300, cwd=CLI_CWD)
        return (r.stdout or "").strip() or ("[vision] " + (r.stderr or "no output")[:300])
    except Exception as e:
        return f"[vision error] {e}"


def _render_state(blob):
    """STATE(json文字列) を Aurora 描画し /diagram/<id> を返す。失敗時 None。"""
    import hashlib
    try:
        json.loads(blob)
    except Exception:
        return None
    sid = hashlib.md5(blob.encode()).hexdigest()[:10]
    os.makedirs(DIAG_DIR, exist_ok=True)
    sp = os.path.join(DIAG_DIR, sid + ".json")
    op = os.path.join(DIAG_DIR, sid + ".html")
    open(sp, "w", encoding="utf-8").write(blob)
    try:
        subprocess.run([sys.executable, AURORA_RENDER, "--state", sp, "--out", op],
                       capture_output=True, timeout=60, cwd=PROJECT_ROOT)
    except Exception:
        return None
    return ("/diagram/" + sid) if os.path.exists(op) else None


def _md_table(text):
    """本文中の最初の markdown 表を (rows, (start,end)) で返す。無ければ (None, None)。"""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if re.match(r"^\s*\|.*\|\s*$", lines[i]):
            j = i
            while j < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[j]):
                j += 1
            block = lines[i:j]
            if len(block) >= 3:                       # header + 区切り + 1行以上
                def cells(ln):
                    return [c.strip() for c in ln.strip().strip("|").split("|")]
                rows = [cells(block[0])] + [cells(b) for b in block[2:]]
                start = sum(len(x) + 1 for x in lines[:i])
                end = sum(len(x) + 1 for x in lines[:j])
                return rows, (start, end)
            i = j
        else:
            i += 1
    return None, None


def render_diagram(text):
    """AURORA:{json} か 本文中の markdown 表 を Aurora 図解へ。(本文, 図URL) を返す。"""
    m = re.search(r"AURORA:\s*(\{)", text)
    if m:
        start = m.start(1); depth = 0; end = None
        for k in range(start, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        if end:
            url = _render_state(text[start:end])
            if url:
                return (text[:m.start()] + text[end:]).strip(), url
        return text[:m.start()].rstrip(), None
    # markdown 表はチャット内にインライン描画する (別ページ図解にしない)。
    return text, None


def anthropic_call(body):
    req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode(),
                                 headers={"x-api-key": ANTHROPIC_KEY,
                                          "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def anthropic_agent(client_msgs, extra_system=""):
    """Claude(Sonnet) backend の agentic ループ。ツールは Anthropic 形式へ変換。"""
    tools_a = [{"name": t["function"]["name"], "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"]}
               for t in (casper_tools.TOOLS if casper_tools else [])]
    conv = [{"role": m["role"], "content": m["content"]}
            for m in client_msgs if m.get("role") in ("user", "assistant") and m.get("content")]
    system = build_sys() + (extra_system or "")
    final = ""
    for _ in range(5):
        body = {"model": ANTHROPIC_MODEL, "max_tokens": 2000, "system": system, "messages": conv}
        if tools_a:
            body["tools"] = tools_a
        resp = anthropic_call(body)
        blocks = resp.get("content", []) or []
        tus = [b for b in blocks if b.get("type") == "tool_use"]
        txt = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if tus:
            conv.append({"role": "assistant", "content": blocks})
            results = [{"type": "tool_result", "tool_use_id": tu["id"],
                        "content": str(casper_tools.execute(tu["name"], tu.get("input", {}) or {}))[:6000]}
                       for tu in tus]
            conv.append({"role": "user", "content": results})
            continue
        final = txt
        break
    return final or "(応答を得られませなんだ)"


def llm_text(system, user):
    """ツール無しの単発生成 (backend 透過)。"""
    if BACKEND == "claude_cli":
        return strip_think(claude_cli_text(system + "\n\n" + user))
    if BACKEND == "anthropic" and ANTHROPIC_KEY:
        r = anthropic_call({"model": ANTHROPIC_MODEL, "max_tokens": 500,
                            "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    r = ollama_chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    return strip_think(r.get("message", {}).get("content", ""))


# === 逆インタビュー (Casper が問い、答えを覚える) ===
LEARN_LOG = os.path.join(HERE, "..", "vault", "00_inbox", "casper_learned.md")


def find_gaps(max_gaps=60):
    """vault を走査し『未記入の知識の穴』を具体的に列挙 (逆インタビューの燃料)。"""
    import glob
    gaps = []
    secs = [("スキル・得意", "得意分野・主な役割"),
            ("ニュアンス・暗黙知", "作業の癖・注意点・暗黙知"),
            ("コンディション傾向", "調子・コンディションの傾向")]
    for p in sorted(glob.glob(os.path.join(VAULT, "20_people", "*.md"))):
        t = open(p, encoding="utf-8").read()
        nm = re.search(r"name:\s*(.+)", t)
        name = nm.group(1).strip() if nm else ""
        if not name:
            continue
        for sec, desc in secs:
            m = re.search(re.escape(sec) + r".*?\n(.*?)(?=\n##|\Z)", t, re.S)
            body = re.sub(r">.*|例[:：].*", "", m.group(1)).strip() if m else ""
            if len(body) < 8:
                gaps.append({"kind": "person", "target": name, "attr": sec, "desc": desc})
    for p in sorted(glob.glob(os.path.join(VAULT, "50_asset_shadows", "*.md"))):
        t = open(p, encoding="utf-8").read()
        m = re.search(r"ニュアンス[・･].{0,6}教訓.*?\n(.*?)(?=\n##|\Z)", t, re.S)
        if m and len(re.sub(r">.*", "", m.group(1)).strip()) < 8:
            nm = re.search(r"name:\s*(.+)", t)
            gaps.append({"kind": "asset", "target": (nm.group(1).strip() if nm else os.path.basename(p)),
                         "attr": "ニュアンス・教訓", "desc": "運用上の注意・教訓・改善点"})
    return gaps[:max_gaps]


QUESTION_BANK = os.path.join(HERE, "question_bank.jsonl")


def _load_bank():
    out = []
    try:
        with open(QUESTION_BANK, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
    except Exception:
        pass
    return out


def gen_question(asked):
    import re
    # ① 事前生成バンク(Opus)から未出題を即提示
    recent_all = " ".join(asked)
    for q in _load_bank():
        if q.get("question") and q["question"] not in recent_all and q.get("target", "") not in (" ".join(asked[-6:])):
            ch = [c for c in q.get("choices", []) if c]
            if "その他" not in ch:
                ch.append("その他")
            return {"question": q["question"], "choices": ch, "src": q.get("src", "bank")}
    # ② バンク切れ → 穴ドリブンのライブ生成
    gaps = []
    try:
        gaps = find_gaps()
    except Exception:
        gaps = []
    recent = " ".join(asked[-12:])
    gap = next((g for g in gaps if g["target"] not in recent), (gaps[len(asked) % len(gaps)] if gaps else None))
    system = (build_sys() + "\n\nあなたは社内知識の解像度を上げる『逆インタビュアー Casper』。"
              "ユーザー(殿/社員)の入力負担を最小化するため、1問ずつ、選択式で答えられる質問をする。"
              "既知の再確認でなく、まだ vault に無い『一段深い解像度』を引き出す問いを。")
    if gap:
        ctx = ""
        try:
            hits = casper_rag.search(gap["target"], k=3) if casper_rag else []
            ctx = ("\n参考(既知): " + " / ".join(hits)) if hits else ""
        except Exception:
            ctx = ""
        user = (f"対象『{gap['target']}』の『{gap['desc']}』が vault に未記入。これを選択式で引き出す質問を1問。"
                f"選択肢は{gap['target']}に即した具体候補(役割/技能/癖/傾向 等)を3つ＋その他。{ctx}\n"
                "形式厳守(他に何も書かない):\nQUESTION: <一文の質問>\n"
                "CHOICES: <候補1> | <候補2> | <候補3> | その他")
    else:
        user = ("次の1問だけ作れ。形式厳守(他に何も書かない):\n"
                "QUESTION: <一文の質問>\nCHOICES: <候補1> | <候補2> | <候補3> | その他\n"
                "選択肢は具体的に。既出と重複させるな。既出: " + (" / ".join(asked[-10:]) or "なし"))
    txt = llm_text(system, user)
    qm = re.search(r"QUESTION:\s*(.+)", txt)
    cm = re.search(r"CHOICES:\s*(.+)", txt)
    q = qm.group(1).strip() if qm else "知識を深めたい点はありますか？"
    ch = [x.strip() for x in cm.group(1).split("|")] if cm else []
    ch = [c for c in ch if c]
    if "その他" not in ch:
        ch.append("その他")
    return {"question": q, "choices": ch}


def record_answer(question, answer):
    os.makedirs(os.path.dirname(LEARN_LOG), exist_ok=True)
    if not os.path.exists(LEARN_LOG):
        open(LEARN_LOG, "w", encoding="utf-8").write(
            "---\ntype: learned\ntags: [casper, learned]\n---\n\n"
            "# 🧠 Casper 学習ログ (逆インタビューで獲得した知識)\n\n")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    with open(LEARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"- ({stamp}) **Q:** {question} → **A:** {answer}\n")
    # 該当する人物ノートへ直接振り分け (構造的に知識を育てる)
    routed = 0
    try:
        routed = route_to_people(question, answer, stamp)
    except Exception:
        pass
    # 知識を即検索可能に (RAG 再索引)
    try:
        if casper_rag:
            casper_rag.build_index()
            casper_rag._CACHE = None
    except Exception:
        pass
    return routed


def route_to_people(question, answer, stamp):
    """答えに名前が出た人物のノートへ、その問い(属性)を直接追記する。"""
    import glob
    pdir = os.path.join(HERE, "..", "vault", "20_people")
    q = re.sub(r"\s+", " ", question).strip()[:160]
    line = f"- ({stamp}) 「{q}」に該当（殿の逆インタビュー回答より）"
    routed = 0
    for p in glob.glob(os.path.join(pdir, "*.md")):
        if os.path.basename(p).startswith("_"):
            continue
        t = open(p, encoding="utf-8", errors="replace").read()
        m = re.search(r"^name:\s*(.+)$", t, re.M)
        nm = m.group(1).strip() if m else ""
        if len(nm) >= 2 and nm in (answer or ""):
            head = "## Casper 学習メモ (逆インタビュー由来)"
            if head not in t:
                t = t.rstrip() + "\n\n" + head + "\n"
            t = t.rstrip() + "\n" + line + "\n"
            open(p, "w", encoding="utf-8").write(t)
            routed += 1
    return routed


def feed_ingest(filename, description, data_b64):
    """資料を保存→テキスト抽出→Casper が要約＋確認質問を作る。"""
    os.makedirs(ASSET_FILES, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", os.path.basename(filename or "material"))[:60] or "material"
    path = os.path.join(ASSET_FILES, safe)
    raw = _b64.b64decode((data_b64 or "").split(",")[-1])
    with open(path, "wb") as f:
        f.write(raw)
    ext = os.path.splitext(safe)[1].lower()
    is_image = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    fmt = ("出力形式を厳守(他に何も書かない):\n"
           "SUMMARY: <この資料が何で、何が読み取れるか。2〜4文>\n"
           "QUESTIONS: <解像度を上げる確認質問1> | <質問2> | <質問3>")
    if is_image and BACKEND == "claude_cli":
        # 画像は Sonnet の vision で直接解析
        vp = (build_sys() + "\n\nあなたは資料を取り込んで理解する Casper。"
              f"\n\n説明(提供者記入): {description}\n\nこの画像資料を視認し、" + fmt)
        out = claude_cli_vision(path, vp)
        text = "(画像: Casper vision で直接解析)"
    else:
        text = casper_extract.extract(path) if casper_extract else "(抽出器なし)"
        system = (build_sys() + "\n\nあなたは資料を取り込んで理解する Casper。"
                  "資料の説明と抽出内容から、内容を要約し、理解の解像度を上げる確認質問を作る。")
        user = (f"資料ファイル名: {safe}\n説明(提供者記入): {description}\n\n抽出内容(一部):\n{text[:8000]}\n\n" + fmt)
        out = llm_text(system, user)
    sm = re.search(r"SUMMARY:\s*(.+?)(?:\nQUESTIONS:|\Z)", out, re.S)
    qm = re.search(r"QUESTIONS:\s*(.+)", out)
    summary = (sm.group(1).strip() if sm else out.strip())[:1200]
    questions = [q.strip() for q in qm.group(1).split("|")] if qm else []
    return {"saved_as": safe, "summary": summary,
            "questions": [q for q in questions if q][:5], "extract_preview": text[:600]}


def feed_save(saved_as, description, summary, qa, filename):
    """理解した資料を vault(asset_shadow) へ知識化。"""
    os.makedirs(ASSET_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^\w\-]", "_", os.path.splitext(saved_as or "material")[0])[:40] or "material"
    path = os.path.join(ASSET_DIR, f"asset_{slug}.md")
    L = ["---", "type: asset_shadow", f"asset: {saved_as}", f"ingested: {stamp}",
         "tags: [casper, asset_shadow]", "---", "",
         f"# 🎬 資料 — {filename or saved_as}", "",
         f"> 元ファイル: `50_asset_shadows/files/{saved_as}` / 取り込み {stamp}", "",
         "## 説明 (提供者)", description or "(なし)", "",
         "## Casper の理解 (要約)", summary or "(なし)", "",
         "## 確認 Q&A (解像度向上)"]
    for item in (qa or []):
        if item.get("a"):
            L.append(f"- **Q:** {item.get('q', '')} → **A:** {item.get('a', '')}")
    # 抽出本文も保存 (コードフェンス無し=RAGが実データを索引できるように)
    body_text = ""
    try:
        fp = os.path.join(ASSET_FILES, saved_as)
        if casper_extract and os.path.exists(fp):
            body_text = casper_extract.extract(fp)[:8000]
    except Exception:
        body_text = ""
    if body_text and not body_text.startswith("("):
        L += ["", "## 抽出内容 (資料本文・検索用)", body_text]
    L += ["", "## ニュアンス・教訓 (運用で追記)", "> "]
    open(path, "w", encoding="utf-8").write("\n".join(L))
    try:
        if casper_rag:
            casper_rag.build_index(); casper_rag._CACHE = None
    except Exception:
        pass
    return {"ok": True, "note": f"50_asset_shadows/asset_{slug}.md"}


def graph_data():
    """vault のノード(ノート)＋エッジ([[link]]) を抽出。"""
    import glob
    V = os.path.join(HERE, "..", "vault")
    GROUP = {"20_people": "people", "90_db_archives": "project", "80_legacy_score": "legacy",
             "10_meetings": "comms", "30_culture_rules": "company",
             "50_asset_shadows": "asset", "00_inbox": "learned"}
    files = glob.glob(os.path.join(V, "**", "*.md"), recursive=True)
    nodes, links, nset = [], [], set()
    for p in sorted(files):
        base = os.path.splitext(os.path.basename(p))[0]
        rel = os.path.relpath(p, V)
        folder = rel.split(os.sep)[0] if os.sep in rel else "root"
        if base == "README" or folder == "_templates":
            continue
        nset.add(base)
        nodes.append({"id": base, "label": base[:26], "group": GROUP.get(folder, "other")})
    for p in sorted(files):
        base = os.path.splitext(os.path.basename(p))[0]
        if base not in nset:
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in set(re.findall(r"\[\[([^\]|]+)", t)):
            tgt = m.strip()
            if tgt in nset and tgt != base:
                links.append({"source": base, "target": tgt})
    return {"nodes": nodes, "links": links}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        page = None
        if self.path in ("/", "/index.html"):
            page = "chat.html"
        elif self.path in ("/qa", "/qa.html"):
            page = "qa.html"
        elif self.path in ("/learn", "/learn.html"):
            page = "learn.html"
        elif self.path in ("/play", "/play.html"):
            page = "play.html"
        elif self.path in ("/peek", "/peek.html", "/graph"):
            page = "graph.html"
        elif self.path == "/api/graph":
            try:
                self._json(graph_data())
            except Exception as e:
                self._json({"error": str(e), "nodes": [], "links": []})
            return
        elif self.path.startswith("/api/devlog"):
            try:
                import urllib.parse
                qs = urllib.parse.urlparse(self.path).query
                n = int(dict(urllib.parse.parse_qsl(qs)).get("n", "20"))
                lines = []
                if os.path.exists(DEV_LOG):
                    lines = open(DEV_LOG, encoding="utf-8").read().splitlines()
                recs = [json.loads(x) for x in lines[-n:] if x.strip()]
                self._json({"count": len(recs), "entries": list(reversed(recs))})
            except Exception as e:
                self._json({"error": str(e)})
            return
        elif self.path.startswith("/diagram/"):
            sid = re.sub(r"[^a-f0-9]", "", self.path.split("/diagram/")[-1])[:32]
            dp = os.path.join(DIAG_DIR, sid + ".html")
            if sid and os.path.exists(dp):
                b = open(dp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()
            return
        elif self.path.startswith("/asset/"):
            fn = os.path.basename(self.path.split("/asset/")[-1].split("?")[0])
            ap = os.path.join(ASSETS_DIR, fn)
            if fn and os.path.exists(ap) and os.path.abspath(ap).startswith(os.path.abspath(ASSETS_DIR)):
                ext = os.path.splitext(fn)[1].lower()
                ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "application/octet-stream")
                b = open(ap, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()
            return
        if page:
            body = open(os.path.join(HERE, page), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/icon":
            ipath = os.path.join(HERE, "casper_icon.jpg")
            if os.path.exists(ipath):
                b = open(ipath, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()
        elif self.path == "/health":
            if BACKEND == "claude_cli":
                active = f"Claude {CLI_MODEL.title()} (Max)"
            elif BACKEND == "anthropic" and ANTHROPIC_KEY:
                active = ANTHROPIC_MODEL
            else:
                active = A.model
            self._json({"ok": True, "model": active, "backend": BACKEND})
        else:
            self.send_response(404); self.end_headers()

    def _emit(self, content):
        try:
            self.wfile.write((json.dumps({"message": {"content": content}}) + "\n").encode())
            self.wfile.flush()
        except Exception:
            pass

    def do_POST(self):
        if self.path in ("/api/iv/next", "/api/iv/answer"):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/api/iv/answer" and req.get("answer"):
                    record_answer(req.get("question", ""), req.get("answer", ""))
                out = gen_question(req.get("asked", []))
                if self.path == "/api/iv/answer":
                    out["recorded"] = True
                self._json(out)
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path in ("/api/feed/ingest", "/api/feed/save"):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path.endswith("ingest"):
                    out = feed_ingest(req.get("filename", ""), req.get("description", ""), req.get("data_b64", ""))
                else:
                    out = feed_save(req.get("saved_as", ""), req.get("description", ""),
                                    req.get("summary", ""), req.get("qa", []), req.get("filename", ""))
                self._json(out)
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path in ("/api/uploader/resolve", "/api/uploader/crosscheck", "/api/uploader/submit"):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            uid = who["uid"] or req.get("uid") or None
            try:
                if self.path.endswith("resolve"):
                    import base64
                    vdesc = ""
                    fn = req.get("filename", "upload")
                    b64 = req.get("data_b64", "")
                    ext = os.path.splitext(fn)[1].lower()
                    if b64:
                        os.makedirs(ASSETS_DIR, exist_ok=True)
                        sp = os.path.join(ASSETS_DIR, "upl_" + who["sid"] + ext)
                        with open(sp, "wb") as f:
                            f.write(base64.b64decode(b64))
                        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                            vdesc = strip_think(claude_cli_vision(
                                sp, "この成果物に何が写っているか、及び制作工程(レイアウト/アニメ/FX/ライティング/コンポ/モデル等)を1〜2文で。"))
                    out = uploader_resolve(req.get("hint", ""), vdesc, uid)
                    out["recognized"] = vdesc
                    self._json(out)
                elif self.path.endswith("crosscheck"):
                    self._json(uploader_crosscheck(req.get("task_id"), req.get("hint", ""), uid))
                else:  # submit — 安全フェーズ: 書込はせず確認記録のみ
                    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                           "uid": uid or "", "task_id": req.get("task_id"), "intent": req.get("intent", "qc"),
                           "filename": req.get("filename"), "note": req.get("note", ""), "status": "confirmed_no_write"}
                    with open(os.path.join(HERE, "uploader_intent_log.jsonl"), "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    self._json({"ok": True, "written": False,
                                "message": "確認のみ記録(書込権限は未取得。ニブ/エルヴィス殿の許可後に実提出を接続)"})
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path == "/api/feedback":
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                rec = {
                    "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                    "question": str(req.get("question", ""))[:500],
                    "format": str(req.get("format", "")),      # text/table/mermaid/canvas/...
                    "verdict": str(req.get("verdict", "")),    # good/want_diagram/want_text/wrong_format
                    "answer_excerpt": str(req.get("answer_excerpt", ""))[:300],
                }
                os.makedirs(os.path.dirname(FEEDBACK_LOG), exist_ok=True)
                with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path != "/api/chat":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", [])
        fu = ("\n\n【選択式の回答】回答は『選んで深掘りできるリスト』で返すこと。"
              "回答が複数の候補(人物/プロジェクト/タスク/観点 等)になる場合は、本文は前置き1〜2文に抑え、"
              "候補それぞれを最後の行に次の形式で列挙せよ(装飾なし・これ以外を付けない):\n"
              "CHOICES: 候補1 | 候補2 | 候補3 | 候補4\n"
              "候補が性質上存在しない問いのみ、代わりに次に聞ける質問を同じ CHOICES: 形式で3つ出せ。"
              ) if req.get("suggest") else ""
        if not any(m.get("role") == "system" for m in msgs):
            msgs = [{"role": "system", "content": build_sys() + fu}] + msgs
        who = identify(self)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        if who["new_sid"]:
            self.send_header("Set-Cookie",
                             f"casper_sid={who['sid']}; Path=/; Max-Age=31536000; SameSite=Lax")
        self.end_headers()

        # --- claude CLI backend (Max 迂回) ---
        if BACKEND == "claude_cli":
            convo = [m for m in msgs if m.get("role") in ("user", "assistant") and m.get("content")]
            last_user = convo[-1]["content"] if convo else ""
            hist = ""
            if len(convo) > 1:
                hist = "\n\n## これまでの会話(直近):\n" + "\n".join(
                    ("殿: " if m["role"] == "user" else "Casper: ") + str(m["content"])[:600]
                    for m in convo[-7:-1])
            hits = casper_rag.search(last_user, k=8) if (casper_rag and last_user) else []
            src, fulltext = (casper_rag.top_source(last_user) if (casper_rag and last_user) else (None, None))
            fullnote = ("\n\n## 該当資料の全文 (" + src + ")\n" + fulltext[:7000]) if fulltext else ""
            cal = calendar_digest(last_user)          # Calendar 左脳を必要時に先読み注入
            cal += meeting_digest(last_user)          # 会議/議事録クエリは最新会議も注入
            diag_hint = ("\n\n【見せ方は内容に応じて自分で判断せよ】答えを最も分かりやすく伝える形式を選ぶ:\n"
                "・一覧/カット表/スケジュール/比較/項目×属性 など表が分かりやすいデータ → **markdown の表**で書く"
                "(チャット内にそのまま見やすい表として描画される)。冒頭に要点を1〜2行、続けて表。例:\n"
                "| カット | 画像 | 秒数 | 内容 |\n|---|---|---|---|\n| 1 | ![](/asset/x.jpeg) | 0:00~ | … |\n"
                "・**資料の全文に画像 `![](/asset/...)` が含まれていれば、表の画像列にそのURLを一字一句変えずコピーせよ**"
                "(サムネイルが表示される)。URLを創作・改変するな。\n"
                "・工程/流れ/手順/関係性/構成/タイムライン が主役 → ```mermaid フェンスで **mermaid 記法**で書く"
                "(チャット内に図として描画される)。用途別に flowchart(`flowchart LR`)/sequenceDiagram/gantt/"
                "mindmap/erDiagram を使い分けよ。例:\n```mermaid\nflowchart LR\n  A[実写] --> B[キー] --> C[合成]\n```\n"
                "・数値の大小比較が主役 → 行頭に `AURORA:` を付け1行のJSON STATE(bars)。\n"
                "・表/mermaid/数値で表せない独自のビジュアル(図形・レイアウト・簡単なインタラクション) → "
                "```html フェンスで**自己完結HTML/SVG**(外部依存なし・1ファイル完結・<style>同梱)。"
                "隔離サンドボックスで描画される。本当に必要な時だけ・多用するな。\n"
                "・動画・実績映像を見せたい時 → 該当の **Vimeo URL(https://vimeo.com/ID)** をそのまま本文に書け"
                "(チャットにプレイヤーが埋め込まれ再生できる)。YouTube/.mp4 URL も同様。\n"
                "・短い事実確認・雑談・1〜2文で済む話 → 図解も表も不要。普通の文章で簡潔に。\n"
                "迷ったら表が無難。無理に図解しようとしなくてよい。図解は1回答に1つまで。")
            prompt = (build_sys() + fu + diag_hint + hist + cal + "\n\n## 関連社内記録(RAG検索):\n" + "\n".join(hits)
                      + fullnote
                      + "\n\n## ユーザーの今回の発言:\n" + last_user
                      + "\n\n直前までの会話の流れも踏まえて答えよ。**左脳(Calendar)・右脳(RAG/資料) のデータは上に注入済**。"
                      "これらが手元の一次データ。**『ツールが接続されていない/このセッションで取得できない』とは絶対に言うな**"
                      "(裏で取得済を注入してある)。注入データに答えが無い時のみ『記録に無い』と述べよ。"
                      "その内容で答えられる限りユーザーに追加共有を求めるな(『ファイルを共有すれば〜』等の条件付け・保留は禁止)。"
                      "**ローカルのファイルを直接読もうとするな**(その手段は無い・注入分だけで判断・『ファイルが見つからない』とは言うな)。"
                      "社内に無い外部情報が要る時のみ WebSearch/WebFetch を使う(Web由来は『(Web)』明記・機密は検索語に含めぬ)。"
                      "**【捏造厳禁】社内固有の事実(制作実績・作品名・人物・案件・クライアント・数値)は、"
                      "上の注入データ(Calendar/RAG/資料)に明記された物だけを述べよ。"
                      "自分の一般知識・記憶から『それっぽい有名作』を補完・推測するな**"
                      "(例: データに無い映画/ゲーム/CM名を勝手に足さない)。"
                      "データに該当が無ければ『記録にあるのは〜』と在る分だけ挙げ、無い旨を正直に言え。"
                      "Casper として簡潔に答えよ。")
            raw = claude_cli_text(prompt, allow=["WebSearch", "WebFetch"])
            thinking = ""
            tm = re.search(r"<think>(.*?)</think>", raw or "", re.S)
            if tm:
                thinking = tm.group(1).strip()
            ans = strip_think(raw)
            ans = re.sub(r"\n{3,}", "\n\n", ans).strip()
            ans, diagram = render_diagram(ans)
            log_convo(who, "user", last_user)                     # 文脈=流れ を順序記録
            log_convo(who, "casper", ans, {"diagram": bool(diagram)})
            dev_log(who, last_user, ans, {                        # 開発用: 思考と材料を残す
                "thinking": thinking,
                "hits": hits, "top_source": src, "calendar": bool(cal),
                "prompt_chars": len(prompt), "model": CLI_MODEL})
            for i in range(0, len(ans), 36):
                self._emit(ans[i:i + 36])
            try:
                if diagram:
                    self.wfile.write((json.dumps({"diagram": diagram}) + "\n").encode())
                self.wfile.write(b'{"done":true}\n')
            except Exception:
                pass
            return

        # --- Anthropic(Sonnet) backend ---
        if BACKEND == "anthropic" and ANTHROPIC_KEY:
            try:
                ans = strip_think(anthropic_agent(msgs, fu))
            except Exception as e:
                ans = f"[anthropic error] {e}"
            for i in range(0, len(ans), 36):
                self._emit(ans[i:i + 36])
            try:
                self.wfile.write(b'{"done":true}\n')
            except Exception:
                pass
            return

        # --- Ollama(local) backend ---
        working = list(msgs)
        tools = casper_tools.TOOLS if casper_tools else None
        final = ""
        try:
            for _ in range(5):                      # 最大5反復 (ツール多段)
                resp = ollama_chat(working, tools=tools)
                m = resp.get("message", {}) or {}
                tcs = m.get("tool_calls")
                if tcs:
                    working.append(m)
                    for tc in tcs:
                        fn = tc.get("function", {}).get("name", "")
                        args = tc.get("function", {}).get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        result = casper_tools.execute(fn, args) if casper_tools else "(no tools)"
                        working.append({"role": "tool", "name": fn, "content": str(result)[:6000]})
                    continue
                final = strip_think(m.get("content", ""))
                break
            else:
                final = final or "(ツール反復が上限に達し申した)"
        except Exception as e:
            final = f"[error] {e}"

        if not final:
            final = "(応答を得られませなんだ)"
        for i in range(0, len(final), 36):          # 疑似ストリーミング
            self._emit(final[i:i + 36])
        try:
            self.wfile.write(b'{"done":true}\n')
        except Exception:
            pass


print(f"Casper chat -> http://localhost:{A.port}  (model {A.model} @ {A.endpoint})", flush=True)
ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
