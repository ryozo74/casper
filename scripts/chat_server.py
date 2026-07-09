#!/usr/bin/env python3
"""Casper チャット鯖 — ブラウザ ⇄ (この鯖) ⇄ z8a Ollama のストリーミングプロキシ。

ブラウザは localhost:PORT を見るだけ。egress(z8a 接続)は本鯖が肩代わりするため
CORS 不要・ブラウザから外部IPへ直接出ない。

Usage:
  python3 chat_server.py --endpoint http://192.168.44.119:11434 --model qwen3:14b --port 8770
"""
import argparse, datetime, http.cookies, json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.error, uuid
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
    import casper_mcp
except Exception:
    casper_mcp = None
try:
    import casper_user_mcp
except Exception:
    casper_user_mcp = None
try:
    import casper_aurora
except Exception:
    casper_aurora = None
try:
    import report_lib
except Exception:
    report_lib = None
try:
    import casper_embed
except Exception:
    casper_embed = None
try:
    import casper_manifest                       # 資産台帳(実ファイルの決定的真実源)=出口検問/検索の基盤
except Exception:
    casper_manifest = None
try:
    import casper_openloop                       # OPEN LOOPレジストリ(未了の約束を⚙レコード化・自動追跡)
except Exception:
    casper_openloop = None
try:
    import casper_traits                         # 人物trait(癖)レジストリ=verify_digestが決定的消費
except Exception:
    casper_traits = None
try:
    import casper_trace                          # トレーシング(1req=1trace・事後分析基盤・Fable #7-1)
except Exception:
    casper_trace = None
try:
    import casper_outbox                          # アクションoutbox(永続状態機械・冪等・"送信済"の真実源・Fable #4)
except Exception:
    casper_outbox = None
try:
    import casper_health                          # セルフヘルス(トレース監視→health.md＋逸脱アラート・Fable北極星 柱2)
except Exception:
    casper_health = None
try:
    import casper_doc                             # 節構造ドキュメント(資料作り・節単位再生成/版管理・Fable UI設計)
except Exception:
    casper_doc = None
try:
    import casper_dropbox                         # Dropbox転送(ファイル→パスワード付き共有リンク・Business口)
except Exception:
    casper_dropbox = None
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
# 画像視認(vision)は chat backend(ollama/qwen 等)に依らず常に Claude Sonnet を使う。既定 claude_cli。"off"で無効化。
VISION_BACKEND = os.environ.get("CASPER_VISION", "claude_cli").lower()
# 起票(Excel→PJ/shot/task)の構造化・チャット修正に使う LLM: local(qwen) | cloud(Sonnet)
# 殿御下命(2026-06-23「localに切り替えよう」): 既定 local(qwen)。PII配慮＋高速(構造化 80秒→約10秒)。cloud は CASPER_IMPORT_LLM=cloud。
IMPORT_LLM = os.environ.get("CASPER_IMPORT_LLM", "local").lower()
os.makedirs(CLI_CWD, exist_ok=True)
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
AURORA_RENDER = os.path.join(PROJECT_ROOT, "skills", "aurora", "scripts", "render_local.py")
DIAG_DIR = os.path.join(HERE, "diagrams")
ASSETS_DIR = os.path.join(HERE, "assets")
FEEDBACK_LOG = os.path.join(HERE, "feedback_log.jsonl")
CORRECTIONS_LOG = os.path.join(HERE, "corrections.jsonl")   # 🙅『欲しい内容と違う』→ヒアリング+スレッドログの修正リスト(自己改善の教師信号)

DIAG_HINT = ("\n\n【見せ方は内容に応じて自分で判断せよ】答えを最も分かりやすく伝える形式を選ぶ:\n"
    "・一覧/カット表/スケジュール/比較/項目×属性 など表が分かりやすいデータ → **markdown の表**で書く"
    "(チャット内にそのまま見やすい表として描画される)。冒頭は要点1〜2行、続けて表。例:\n"
    "| カット | 画像 | 秒数 | 内容 |\n|---|---|---|---|\n| 1 | ![](/asset/x.jpeg) | 0:00~ | … |\n"
    "・画像を見せる時は `![](/asset/実ファイル名)`。**文脈に明示された実ファイル名だけ**を使い、推測で名を作らない"
    "(存在せぬ名はサーバが自動除去する=出しても無駄)。\n"
    "・工程/流れ/手順/関係性/構成/タイムライン が主役 → ```mermaid フェンスで **mermaid 記法**で書く"
    "(チャット内に図として描画される)。用途別に flowchart(`flowchart LR`)/sequenceDiagram/gantt/"
    "mindmap/erDiagram を使い分けよ。例:\n```mermaid\nflowchart LR\n  A[実写] --> B[キー] --> C[合成]\n```\n"
    "・数値の大小比較が主役 → 行頭に `AURORA:` を付け1行のJSON STATE(bars)。\n"
    "・表/mermaid/数値で表せない独自のビジュアル(図形・レイアウト・簡単なインタラクション) → "
    "```html フェンスで**自己完結HTML/SVG**(外部依存なし・1ファイル完結・<style>同梱)。"
    "隔離サンドボックスで描画される。本当に必要な時だけ・多用するな。\n"
    "・🚫**複数画像/カット一覧/ギャラリーの表示に ```html を使うな**。HTMLは冗長で出力上限に達し"
    "途中で切れ、一部の画像しか表示されない(例:14カット中8枚で切断)。**必ず markdown で全画像を出せ**——"
    "カット表なら上記の表形式(画像列に `![](/asset/ファイル名)` を全行)、単純な並べなら "
    "`![](/asset/c01_xxx.png)` を画像の数だけ改行区切りで羅列。markdownは軽く全件が確実に収まる。"
    "資料に列挙された画像ファイル名は1枚も省略せず全て出せ。\n"
    "・⚠️**表や画像の markdown を ```markdown や ``` のコードフェンスで囲むな**。囲むと描画されず"
    "生テキスト(| カット |...)のまま表示される。表・画像は**フェンス無しで直に**本文へ書け。\n"
    "・動画・実績映像を見せたい時 → 該当の **Vimeo URL(https://vimeo.com/ID)** をそのまま本文に書け"
    "(チャットにプレイヤーが埋め込まれ再生できる)。YouTube/.mp4 URL も同様。\n"
    "・短い事実確認・雑談・1〜2文で済む話 → 図解も表も不要。普通の文章で簡潔に。\n"
    "迷ったら表が無難。無理に図解しようとしなくてよい。図解は1回答に1つまで。")
CONVO_LOG = os.path.join(HERE, "conversation_log.jsonl")
# --- 個性プロファイル: アイドル便乗育成の状態 ---
LAST_CHAT_TS = datetime.datetime.now()   # 直近チャット時刻(アイドル判定)
DIRTY_USERS = {}                          # ukey -> 直近活動ts(要更新)
PROFILE_BUILT = {}                        # ukey -> 最終生成ts(クールダウン)


# Casper 独自の署名鍵(Score とは分離)。本人確認は Calendar /api/auth/token で行い、JWT は Casper 自前で署名。
# 固定鍵: env > .casper_secret ファイル(再起動で不変=ログイン維持) > 無ければ生成して保存。
JWT_SECRET = os.environ.get("CASPER_JWT_SECRET", "")
if not JWT_SECRET:
    _secf = os.path.join(HERE, ".casper_secret")
    try:
        if os.path.exists(_secf):
            JWT_SECRET = open(_secf, encoding="utf-8").read().strip()
        else:
            import secrets as _sec
            JWT_SECRET = "casper_" + _sec.token_hex(24)
            open(_secf, "w", encoding="utf-8").write(JWT_SECRET)
    except Exception:
        JWT_SECRET = ""
CAL_BASE = os.environ.get("CALENDAR_BASE_URL", "http://192.168.44.253:8001")
WRITE_TOKEN = os.environ.get("CASPER_WRITE_TOKEN", "")   # 書込/DM用(env優先)
if not WRITE_TOKEN:                                       # env 無ければローカル秘匿ファイルから(gitignore済)
    for _fn in (".casper_write_token", "CASPER_WRITE_TOKEN.txt"):
        try:
            _wtf = os.path.join(os.path.dirname(os.path.abspath(__file__)), _fn)
            if not os.path.exists(_wtf):
                continue
            # ファイルは bare token か KEY=VALUE 形式(CASPER_WRITE_TOKEN=...)の両対応。
            # ニブ殿 401診断(2026-06-23): 接頭辞 'CASPER_WRITE_TOKEN=' ごと送っていたのが原因→値だけ抽出。
            for _line in open(_wtf, encoding="utf-8").read().splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                if "=" in _line and _line.split("=", 1)[0].strip().upper().endswith("TOKEN"):
                    WRITE_TOKEN = _line.split("=", 1)[1].strip().strip('"').strip("'")
                else:
                    WRITE_TOKEN = _line.strip().strip('"').strip("'")
                if WRITE_TOKEN:
                    break
            if WRITE_TOKEN:
                break
        except Exception:
            pass
# 副作用のある MCP ツール(外向き/共有状態変更)= 自動実行せず確認ゲートに回す。
# get_messages 等の読取系はゲート対象外(WRITE_TOKEN+actor で即実行)。
MCP_SIDE_EFFECT = {"upload_asset", "add_reference_material", "send_message", "update_task"}
# actor_id(本人uid)を引数に要する MCP ツール。chat ループで本人uidを強制注入(spoof防止)。
MCP_ACTOR_TOOLS = {"upload_asset", "add_reference_material", "send_message", "get_messages", "update_task"}

# Calendar タスクステータス 新19値(2026-07-08 刷新・ニブ資料 calendar_status_changes_summary)。
# 完了は deliver のみ。遅延は status でなく isOverdue 派生(due<today かつ status∉{deliver,omit})。旧値は互換期間中も許容。
_TASK_DONE = {"deliver", "completed", "done", "complete", "approved"}   # 完了扱い(新=deliverのみ・旧値も互換で吸収)
_TASK_NOT_OVERDUE = {"deliver", "omit"}                                 # 遅延判定の除外(完了+作業対象外)
_TASK_ACTIVE = {"mk", "wip", "modeling", "lookdev", "caching", "rig", "facial",   # 未着手+進行中の工程群
                "todo", "in-progress", "in_progress"}                             # (旧値互換)
_TASK_ST_LABEL = {   # status → 表示ラベル(絵文字つき・5カテゴリ準拠)
    "mk": "⚪MK(未着手)", "wip": "🔵WIP(進行中)", "modeling": "🔵Modeling", "lookdev": "🔵LookDev",
    "caching": "🔵Caching", "rig": "🔵Rig", "facial": "🔵Facial",
    "v1qc": "🟡V1QC", "qc": "🟡QC(社内チェック)", "qc_fb": "🟠QC_FB(社内FB)",
    "ap": "🟣AP(社内承認)", "ap_fb": "🟠AP_FB(社外FB)", "dir_wt": "🟡Dir待ち", "dir_ap": "🟣Dir承認",
    "dir_fb": "🟠Dir_FB", "fix": "🟢FIX(クラ承認)", "deliver": "✅Deliver(完了)",
    "omit": "⚫Omit(除外)", "wt": "⏸WT(停止)",
    # 旧値互換(移行期間中)
    "todo": "⚪未着手", "in-progress": "🔵進行中", "in_progress": "🔵進行中", "review": "🟡レビュー",
    "completed": "✅完了", "done": "✅完了", "approved": "🟣承認済", "delayed": "🔴遅延", "blocked": "🔴停滞",
}
# category(5分類) → 絵文字。ラベル文字は API の status_label を単一ソースに使い、内蔵マップは fallback のみ(ニブ指針2026-07-08)。
_CAT_EMOJI = {"todo": "⚪", "in_progress": "🔵", "review": "🟡", "completed": "✅", "held": "⏸"}


def _task_label(t):
    """タスクの表示ラベル。API提供の status_label(＋category絵文字)を優先=Calendar単一ソースでドリフト無し。
    無ければ内蔵 _TASK_ST_LABEL(fallback)。ハードコード非推奨のニブ指針に沿う。"""
    if isinstance(t, dict) and t.get("status_label"):
        emo = _CAT_EMOJI.get(t.get("status_category") or "", "")
        return f"{emo}{t['status_label']}"
    st = (t.get("status") if isinstance(t, dict) else t) or ""
    return _TASK_ST_LABEL.get(str(st).lower(), str(st))


def _task_is_done(t):
    """完了か。API提供の status_category=='completed' を優先、無ければ status で判定(deliver+旧値互換)。"""
    if isinstance(t, dict) and t.get("status_category"):
        return t["status_category"] == "completed"
    st = (t.get("status") if isinstance(t, dict) else t) or ""
    return str(st).lower() in _TASK_DONE


def _task_is_moving(t):
    """『動いている』(進行中)か。category=='in_progress' を優先、無ければ status で判定。"""
    if isinstance(t, dict) and t.get("status_category"):
        return t["status_category"] == "in_progress"
    st = (t.get("status") if isinstance(t, dict) else t) or ""
    return str(st).lower() in {"wip", "modeling", "lookdev", "caching", "rig", "facial", "in-progress", "in_progress"}

# --- Stage2: 副作用ツールの「承認→実行」フロー(DM代筆・QC提出・参照登録) ---
PENDING_ACTIONS = {}   # id -> {tool, args, uid, summary}
_AURORA_CUR = {}       # thread -> {doc_id, title}: 1スレッド=1資料の紐付け(更新はappend)

# --- 恒久 roster: token失効でも壊れぬ永続キャッシュ＋多源リフレッシュ(get_users優先→RO REST→DM収穫) ---
ROSTER_FILE = os.path.join(HERE, "roster_cache.json")
_ROSTER_MAP = {}                      # uid(str) -> username (ディスク永続)


def _roster_save():
    try:
        json.dump(_ROSTER_MAP, open(ROSTER_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _roster_load():
    try:
        for k, v in json.load(open(ROSTER_FILE, encoding="utf-8")).items():
            _ROSTER_MAP[str(k)] = v
    except Exception:
        pass


def _roster_observe(participants):
    """get_messages 等で見えた participants から uid->name を恒久キャッシュへ収穫(RO token不要)。"""
    chg = False
    for p in (participants or []):
        uid, nm = p.get("user_id"), p.get("name")
        if uid and nm and nm != "Me" and _ROSTER_MAP.get(str(uid)) != nm:
            _ROSTER_MAP[str(uid)] = nm
            chg = True
    if chg:
        _roster_save()
        _ROSTER_CACHE["v"] = None      # 名簿テキストを作り直させる


def _roster_refresh():
    """全社名簿を集約: get_users(ニブ露出時・RO非依存)優先 → readonly REST。恒久キャッシュに合流。"""
    got = {}
    if casper_mcp and WRITE_TOKEN:
        try:
            avail = {t["function"]["name"] for t in casper_mcp.list_tools(token=WRITE_TOKEN)}
        except Exception:
            avail = set()
        if "get_users" in avail:
            try:
                r = casper_mcp.call_tool("get_users", {}, token=WRITE_TOKEN)
                d = json.loads(r) if isinstance(r, str) else r
                items = d.get("items") or d.get("users") or (d if isinstance(d, list) else [])
                for u in items:                       # ニブ get_users 返却: {uid, username, display_name}
                    uid = u.get("uid") or u.get("id")
                    nm = u.get("username") or u.get("display_name") or u.get("name")
                    if uid and nm:
                        got[str(uid)] = nm
            except Exception:
                pass
    try:
        for u in (casper_tools._get("/users?limit=200").get("items", []) if casper_tools else []):
            if u.get("id") and (u.get("username") or u.get("name")):
                got[str(u["id"])] = u.get("username") or u.get("name")
    except Exception:
        pass
    if got:
        _ROSTER_MAP.update(got)
        _roster_save()
        _ROSTER_CACHE["v"] = None
    return _ROSTER_MAP


_roster_load()                       # 起動時に永続roster cacheを読み込む(token失効でも前回名簿を保持)


def _uid_to_name(uid):
    """uid → username。恒久 roster cache から引き、無ければリフレッシュ(RO非依存)。"""
    if not uid:
        return "?"
    uid = str(uid)
    if uid in _ROSTER_MAP:
        return _ROSTER_MAP[uid]
    _roster_refresh()
    return _ROSTER_MAP.get(uid, uid)


def _action_summary(tool, args):
    """副作用操作を人間向けに1行要約(引数名はニブ inputSchema の揺れに頑健に)。"""
    try:
        a = args or {}
        if tool == "send_message":
            to = (a.get("to_user_id") or a.get("recipient_id") or a.get("to")
                  or a.get("user_id") or a.get("recipient") or "?")
            nm = _uid_to_name(to) if str(to).isdigit() else to
            body = str(a.get("body") or a.get("content") or a.get("text") or a.get("message") or "")
            return f"DM送信 → 宛先: {nm}（uid {to}）\n── 本文(全文) ──\n{body}"
        if tool == "aurora_create":
            return (f"Aurora ノート作成 → タイトル: {a.get('title') or '?'}\n"
                    f"── 本文 ──\n{str(a.get('body') or '')}")
        if tool == "aurora_append":
            return (f"Aurora ノート修正(新版追加) → doc_id: {a.get('doc_id') or '?'}\n"
                    f"── 修正後の本文 ──\n{str(a.get('body') or '')}")
        if tool == "update_task":
            ch = []
            if a.get("assignee"): ch.append(f"担当 → {a['assignee']}")
            if a.get("due"): ch.append(f"締切 → {a['due']}")
            if a.get("status"): ch.append(f"状態 → {a['status']}")
            if a.get("type"): ch.append(f"工程 → {a['type']}")
            return f"タスク更新 → task[{a.get('task_id') or '?'}]\n── 変更 ──\n" + ("\n".join(ch) if ch else "(変更指定なし)")
        if tool == "upload_asset":
            return f"成果物アップロード(QC) → task[{a.get('task_id') or a.get('shot_id') or '?'}] / {a.get('filename') or a.get('file') or ''}"
        if tool == "add_reference_material":
            return f"参考資料の登録 → {a.get('title') or a.get('filename') or a.get('name') or '?'}"
        return f"{tool}({json.dumps(a, ensure_ascii=False)[:100]})"
    except Exception:
        return tool

def _register_pending(tool, args, uid, summary, thread=None, origin="user", query=None, trace_id=None):
    # query(発端の発話)+trace_id: 承認時の編集差分から教師信号の三つ組を復元する為に必須(Fable5指摘・A実装)
    if casper_outbox:                              # 永続outbox=真実源(再起動でも承認待ちが消えず・冪等・状態機械)
        try:
            pid = casper_outbox.propose(tool, args, uid, summary, thread=thread,
                                        origin=origin, query=query, trace_id=trace_id)["id"]
        except Exception:
            pid = uuid.uuid4().hex[:12]
    else:
        pid = uuid.uuid4().hex[:12]
    PENDING_ACTIONS[pid] = {"tool": tool, "args": args, "uid": str(uid or ""), "summary": summary,
                            "origin": origin, "query": query, "trace_id": trace_id}
    if len(PENDING_ACTIONS) > 50:                  # 古いものから間引き(メモリキャッシュの肥大防止・真実源はoutbox)
        for k in list(PENDING_ACTIONS)[:-50]:
            PENDING_ACTIONS.pop(k, None)
    return pid


def _salvage_text_toolcall(final, who, pending_actions, query=None, trace_id=None):
    """qwenが send_message を呼ばず DM をテキストで書いた場合の救済(JSONブロック＋プロセ両対応)。
    宛先uid/名＋本文を拾い pending 登録→承認カードを出す(ローカルqwenのfunction-calling不発対策)。"""
    if pending_actions:                            # 既にツール呼出で pending 済なら不要
        return final
    f = final or ""
    # ⓪ Aurora ノート作成の表明救済: qwen が「Auroraに『TITLE』として作成しますか？承認ボタン…」と
    #    "言っただけ"で aurora_create を呼ばなかった場合、応答本体を本文に pending 登録→承認カードを出す。
    if (re.search(r"[Aa]urora", f) and re.search(r"承認ボタン|作成しますか|保存されます|保存しますか|作成しました|保存しました", f)
            and re.search(r"(ノート|ドキュメント|資料|note)", f)):
        tm = re.search(r"[「『]([^」』]{2,80})[」』]", f)
        title = (tm.group(1).strip() if tm else "Casperノート")
        # 本文=表明文(Aurora/承認ボタン等の行)を除いた応答本体(Casperが提示した一覧など)
        body = re.sub(r"(?m)^.*(承認ボタン|作成しますか|保存されます|保存しますか|Auroraに|Aurora に).*$", "", f).strip()
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if len(body) >= 20:
            args = {"title": title, "body": body}
            if who.get("uid"):
                args["actor_id"] = who["uid"]
            summary = _action_summary("aurora_create", args)
            pid = _register_pending("aurora_create", args, who.get("uid"), summary, origin="user", query=query, trace_id=trace_id)
            pending_actions.append({"id": pid, "tool": "aurora_create", "args": args, "summary": summary})
            f2 = re.sub(r"(作成します|保存します|作成しました|保存しました|作成しますか)", "下書きしました", f)
            return f2 + f"\n\n（↓の承認カードで確認し、ボタンを押すと Aurora に「{title}」として保存されます）"
    to = body = None
    cut = None
    # ① JSONブロック形 ```json {to_user_id, body}```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", f, re.S)
    if m:
        try:
            o = json.loads(m.group(1))
            to = o.get("to_user_id") or o.get("recipient_id") or o.get("to") or o.get("user_id")
            body = o.get("body") or o.get("content") or o.get("message") or o.get("text")
            cut = (m.start(), m.end())
        except Exception:
            pass
    # ①.5 tool-call JSON形 {"tool":"send_message","params"/"arguments":{recipient/to_user_id, message/body}}
    #     (qwenが承認カードでなく生JSONで書く新顔・入れ子波括弧を釣り合いで抽出)
    if not (to and body):
        _ti = f.find('"tool"')
        if _ti >= 0 and "send_message" in f[_ti:_ti + 60]:
            _st = f.rfind("{", 0, _ti)
            if _st >= 0:
                _d = 0; _en = -1
                for _j in range(_st, len(f)):
                    if f[_j] == "{":
                        _d += 1
                    elif f[_j] == "}":
                        _d -= 1
                        if _d == 0:
                            _en = _j + 1; break
                if _en > _st:
                    try:
                        o = json.loads(f[_st:_en], strict=False)   # strict=False: 本文中のリテラル改行も許容
                        p = o.get("params") or o.get("arguments") or o.get("parameters") or o
                        body = p.get("message") or p.get("body") or p.get("content") or p.get("text")
                        rcp = p.get("recipient") or p.get("to_user_id") or p.get("to") or p.get("user_id")
                        if rcp is not None:
                            mm = re.search(r"(\d+)", str(rcp))
                            to = mm.group(1) if mm else {v: k for k, v in _ROSTER_MAP.items()}.get(str(rcp))
                        cut = (_st, _en)
                    except Exception:
                        pass
    # ② プロセ形 「宛先: 〇〇（uid31）… 本文: 〇〇」
    if not (to and body):
        mb = re.search(r"本文\**\s*[:：]\s*\**\s*(.+?)\s*(?:\n|$)", f)
        if mb:
            body = mb.group(1).strip().strip("*").strip()
            um = re.search(r"uid\s*[:：]?\s*(\d+)", f)
            if um:
                to = um.group(1)
            else:
                mn = re.search(r"宛先\**\s*[:：]\s*\**\s*([^\n（(／/、 　]+)", f)
                if mn:
                    rev = {v: k for k, v in _ROSTER_MAP.items()}
                    to = rev.get(mn.group(1).strip())
    # ③ 送信表明＋引用ブロック形「〇〇さんへ…送信しました\n> 本文…」(qwenが完了報告調で書く頻出形)
    if not (to and body):
        ms = re.search(r"([^\s、。：:（(]+)\s*さん.{0,40}?(?:送信しました|送りました|お送りしました|送ります|送信します|DMしました|DMします|連絡しました)", f)
        quotes = re.findall(r"(?m)^\s*[>＞]\s*(.+)$", f)
        if ms and quotes:
            nm = ms.group(1).strip()
            rev = {v: k for k, v in _ROSTER_MAP.items()}
            to = rev.get(nm) or rev.get(nm.replace("さん", ""))
            body = " ".join(q.strip() for q in quotes).strip()
            body = re.sub(r"^" + re.escape(nm) + r"\s*さん[、,：:]?\s*", "", body)
    # ④ 関数呼び構文形 send_message(to_user_id="uid31", body="...") をテキストで書いた場合(qwen頻出)
    if not (to and body):
        mf = re.search(r"send_message\s*\(([^)]*)\)", f, re.S)
        if mf:
            inner = mf.group(1)
            mt = re.search(r"to_user_id\s*=\s*[\"']?([^\"',\s)]+)[\"']?", inner)
            mbd = re.search(r"body\s*=\s*[\"'](.+?)[\"']", inner, re.S)
            if mt and mbd:
                raw_to = mt.group(1)
                m_uid = re.search(r"(\d+)", raw_to)              # "uid31"→31 / 名前→roster逆引き
                to = m_uid.group(1) if m_uid else {v: k for k, v in _ROSTER_MAP.items()}.get(raw_to)
                body = mbd.group(1)
                cut = (mf.start(), mf.end())
    if not (to and body):
        return final
    args = {"to_user_id": to, "body": _clean_dm_body(body)}   # salvage経路のDMもプレーンテキスト整形(読みやすさ)
    if who.get("uid"):
        args["actor_id"] = who["uid"]
    summary = _action_summary("send_message", args)
    pid = _register_pending("send_message", args, who.get("uid"), summary, origin="user", query=query, trace_id=trace_id)
    pending_actions.append({"id": pid, "tool": "send_message", "args": args, "summary": summary})
    if cut:
        f = (f[:cut[0]] + f[cut[1]:]).strip()
    else:
        f = re.sub(r"(?m)^\s*[>＞]\s*.+$", "", f)          # 本文はカードに出すので引用ブロックを除去
    f = re.sub(r"(送信しました|送りました|お送りしました|DMしました|連絡しました)", "下書きしました", f)
    f = re.sub(r"(送信します|送ります|DMします)", "下書きします", f)
    f = re.sub(r"\n{3,}", "\n\n", f).strip()
    note = "（↓の承認カードで本文を確認・編集し、ボタンを押すと送信されます）"
    return (f + "\n\n" + note) if f else note


def _strip_tool_leak(text):
    """qwenがツール呼びをテキスト(```tool ... / 生JSON)で書いた漏れ、及び『〜を取得します』等の作業実況を除去。
    (retrieve-then-renderで事実は注入済ゆえツールは不要。漏れた宣言だけ残るのを掃除)。"""
    if not text or ("```" not in text and "します" not in text and '"tool"' not in text):
        return text
    text = re.sub(r"```tool.*?```", "", text, flags=re.S)          # ツール呼びの漏れブロック
    text = re.sub(r"```(?:python|json|tool_code)?\s*(?:calendar_lookup|get_projects|get_today_tasks|get_events)\([^`]*?```",
                  "", text, flags=re.S)
    # 生JSONのtool-call漏れ {"tool":"..","params"/"arguments":{..}} を除去(salvageが拾えなかった残り・生JSONを殿に見せぬ)
    if '"tool"' in text:
        _ti = text.find('"tool"'); _st = text.rfind("{", 0, _ti) if _ti >= 0 else -1
        if _st >= 0:
            _d = 0
            for _j in range(_st, len(text)):
                if text[_j] == "{":
                    _d += 1
                elif text[_j] == "}":
                    _d -= 1
                    if _d == 0:
                        text = (text[:_st] + text[_j + 1:]); break
        text = re.sub(r"```(?:json)?\s*```", "", text)             # 空になったコードフェンス
    text = re.sub(r"(?m)^.{0,40}(を確認するため.*?|を)(取得|照会|確認)します。?\s*$", "", text)   # 作業実況行
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _guard_completion_claims(text, pending_actions):
    """P1(Fable処方・fail-closed): アクション完了主張は"真実値テキスト"。承認カード(=アクション台帳の
    レシート)が無いのに送信/報告等を断じた文を打ち消す。既成事実化を salvage の網羅性でなく構造で封じる
    ——qwenがどんな未知の書式でツールをテキスト化しても、カードが無ければ完了主張は通さない。"""
    if not text or pending_actions:                        # カードあり=台帳にレシート有り→主張は裏付く
        return text
    if not re.search(r"(送信|お送り|DM|連絡|報告|通知|投稿|アップ(ロード)?)(しました|いたしました|済み|完了しました)", text):
        return text
    # レシート無し＋完了主張 → 該当行を打ち消し、未実行の注記へ差替(fail-closed=疑わしきは実行済と言わせぬ)
    text = re.sub(r"(?m)^.*(送信|お送り|DM|連絡|報告|通知|投稿|アップ(ロード)?)(しました|いたしました|済み|完了しました).*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (text + "\n\n※上記アクション(送信/報告等)はまだ実行しておりませぬ。承認カードが出ておらねば、恐れ入りますがもう一度お申し付けを。").strip()


def _ollama_json(system, user, num_predict=400):
    """z8a を format='json' の制約デコードで呼び、JSON文字列を返す(P2ルーター/引数抽出の土台)。
    Ollamaのschema-object modeはqwenが無視する為、format='json'＋プロンプト記述スキーマを使う(実測で確実)。"""
    body = {"model": A.model, "stream": False, "think": False, "keep_alive": -1, "format": "json",
            # num_ctx は対話/pinger と統一(Fable): 不一致は Ollama のランナー再作成=実質再ロードで温存を壊す(冷間の真犯人)
            "options": {"num_ctx": 12288, "num_predict": num_predict, "temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r).get("message", {}).get("content", "")


def _qwen_is_warm():
    """z8a に対話モデルが常駐しているか(/api/ps・数ms)。冷間検知(Fable縮退#3): 埋込による追い出しも捕捉。
    エラー時は True(=status出さず・過剰な"お待ちを"を避ける・fail-open)。"""
    try:
        with urllib.request.urlopen(A.endpoint.rstrip("/") + "/api/ps", timeout=2) as r:
            names = [m.get("name", "") for m in json.load(r).get("models", [])]
        base = str(A.model).split(":")[0]
        return any(base in n for n in names)
    except Exception:
        return True


_ACTION_Q_RE = re.compile(r"(DM|ディーエム|メッセージ|連絡し|伝え|知らせ|報告し|報せ|通達|通知し|送っ?て|送信し)", re.I)


def _looks_like_action(msg):
    """安価な事前ゲート: DM/送信の意図がある発話だけ P2ルーターを走らせる(全メッセージで走らせない)。"""
    return bool(msg and _ACTION_Q_RE.search(msg))


def _extract_list_lines(text):
    """テキストから一覧行を verbatim で抜き出す(markdown表の行/[PJ]で始まる/· 区切りの短い行)。
    『上記リストをそのまま』のDMで、LLMに要約させず一覧を忠実に付ける為(殿御下命『基本リストはそのまま・模造排除』)。"""
    out = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        # ① markdown表のデータ行 | a | b | c | → 区切り行/ヘッダを除きセルを整形して1行に(表の中身を verbatim 保持)
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 3:
            if re.match(r"^\|[\s:|\-]+\|$", s):            # 区切り行(|---|)はスキップ
                continue
            cells = [re.sub(r"\*\*(.+?)\*\*", r"\1", c.strip()) for c in s.strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells and not any(h in cells[0] for h in ("プロジェクト名", "タスク名", "名前", "項目", "件名")):
                out.append("　".join(cells))               # 全角スペース区切り(プレーンテキストで読める)
            continue
        # ② 箇条書き/[PJ]/· 区切り行
        s = re.sub(r"^[-・*•●]\s+", "", s)                 # 箇条書き記号
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)             # 太字装飾
        if (re.match(r"^\[[^\]]+\]", s) or " · " in s) and 4 < len(s) < 140:
            out.append(s)
    return out


def _clean_dm_body(body):
    """DM本文をプレーンテキストに整える(kiyotomo殿指摘『読みづらい/改行が多い』対策): HTMLエンティティ復元・
    タグ除去・機械前置き除去・冒頭署名除去・改行の詰め。DMは素のテキストゆえ装飾と間延びを消す。"""
    import html as _h
    b = _h.unescape(body or "")
    b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
    b = re.sub(r"<[^>]+>", "", b)                          # 生HTMLタグ除去
    b = re.sub(r"^\s*【[^】]*(Casper|Ryoji|殿).*?】\s*", "", b)   # 『【Casperより/Ryojiの指示に基づく連絡】』等の機械前置き
    b = re.sub(r"^\s*(ryoji|Ryoji|りょうじ|殿)\s*より[。、,：:\s]*", "", b)   # 冒頭の署名『ryojiより』『ryojiより。』を除去(インライン含む・送信者は自明)
    b = re.sub(r"[ \t]+\n", "\n", b)                       # 行末空白
    b = re.sub(r"\n{3,}", "\n\n", b)                       # 空行の連発をまず2つに
    b = b.strip()
    # 短いDM(180字未満)は空行を全て詰めて double-spaced の間延びを解消(kiyotomo殿『改行が多い』)
    if len(b) < 180:
        b = re.sub(r"\n\s*\n", "\n", b)                    # 空行→単一改行
    return b.strip()


def _action_router(user_msg, context, who, convo=None):
    """P2(Fable処方 propose→execute→render): 依頼が send_message(DM)かを制約デコードで判定し、型付き引数
    (to_user_id, body)を抽出。自由文 tool-call を作らせず機構が承認カードを起こす。返り {tool,args,reply} or None。
    ——qwenがテキストで関数を書く経路を通さないので、salvage のモグラ叩きが不要になる。convo=直前の会話(『上記』解決用)。"""
    roster_lines = "、".join(f"{nm}=uid{uid}" for uid, nm in list(_ROSTER_MAP.items())[:40])
    sys_j = ("あなたはCasperのアクション抽出器 兼 DM代筆者。ユーザーの依頼が『特定の相手へのDM/メッセージ送信』なら、"
             "送るべき本文を作り JSON だけで返せ。単なる質問・一覧要求・雑談など送信でなければ is_dm=false。\n"
             f"社員名簿(名前=uid): {roster_lines}\n"
             "【本文の書き方＝読みやすさ最優先】\n"
             "・普通のビジネスチャットの自然な文章で書く。プレーンテキストのみ(HTMLタグや &amp; 等のエンティティを使うな。"
             "『&』はそのまま『&』と書く)。\n"
             "・簡潔に。まず用件を1〜2文で述べ、詳細は必要な分だけ。長い羅列・壁のような文章にしない。\n"
             "・**改行は最小限**。2〜3文の短いメッセージなら改行せず続けて書く(1文ごとに改行・空行を入れて間延びさせない)。"
             "冒頭に『ryojiより』等の署名は書かない(送信者は自動で分かる)。\n"
             "・箇条書きは項目が3つ以上ある時だけ使い(3〜5まで・ネスト禁止)、短い依頼では使わない。\n"
             "・『【Casperより/Ryojiの指示に基づく連絡】』等の機械的な前置きは付けない。人が書いたように自然に。\n"
             "・数値/固有名は下記コンテキストの事実だけを使い、創作するな。\n"
             "【指示語の鉄則＝捏造防止(重要)】ユーザーが『上記の/この/その〜(タスク/件/PJ等)』と指す対象は、"
             "**直前の会話に明示されたものだけ**を指す。下記コンテキストは背景情報にすぎず、"
             "**そこから勝手に対象(特定のタスク名/PJ名等)を選んで本文に書き込むな**。"
             "指す対象が会話に見当たらず特定できない時は、**具体名を創作せず is_dm=false を返せ**"
             "(→Casperが『どれのことか』を聞き返す)。\n"
             'JSON形式: {"is_dm": true|false, "to_user_id": "uidの数字", "body": "送る本文"}')
    conv = [m for m in (convo or []) if m.get("role") in ("user", "assistant") and m.get("content")]
    # 機構ガード(捏造防止): 『上記/先ほど/この〜タスク/件』の参照なのに直前のCasper応答にタスクの気配が無ければ
    # 対象不明→DMを組ませず None(通常経路で聞き返す)。tim担当タスクを殿の物と偽ってDM化した事故(2026-07-08)の恒久策。
    if re.search(r"(上記|先ほど|さっき|この|その)\s*(の|、)?\s*(タスク|件|案件|依頼|PJ|プロジェクト)", user_msg or ""):
        prior_asst = [m for m in conv if m["role"] == "assistant"]
        last_a = str(prior_asst[-1].get("content", "")) if prior_asst else ""
        if not re.search(r"(タスク|担当|assigned|SEQ|〆|納期|status|ID:|進行中|\|.+\|)", last_a):
            return None                                    # 直前にタスク一覧/言及なし=対象不明→捏造させぬ
    # 直前の会話も渡す(『上記』の指示語を正しく解決させる為・背景コンテキストと区別)
    hist = ""
    try:
        if len(conv) > 1:
            hist = "\n\n直前の会話(『上記』等はここだけを指す):\n" + "\n".join(
                ("殿: " if m["role"] == "user" else "Casper: ") + str(m.get("content", ""))[:400] for m in conv[-7:-1])
    except Exception:
        pass
    user_j = f"背景コンテキスト(参考):\n{(context or '')[:3500]}{hist}\n\n依頼: {user_msg}"
    try:
        d = json.loads(_ollama_json(sys_j, user_j))
    except Exception:
        return None                                        # 抽出失敗→通常経路(salvage+P1)に委ねる(fail-openだが後段で守る)
    if not d.get("is_dm"):
        return None
    to = str(d.get("to_user_id") or "")
    m = re.search(r"(\d+)", to)
    to = m.group(1) if m else {v: k for k, v in _ROSTER_MAP.items()}.get(to)
    body = _clean_dm_body(str(d.get("body") or "").strip())   # プレーンテキスト整形(読みやすさ・&amp;除去)
    # 『上記のリスト/タスクをそのまま』のDMは、qwenに要約させず会話にある一覧を verbatim で付ける
    # (殿御下命『基本リストはそのまま・模造排除』・項目落ち/再計算をさせぬ)。上記〜件の間に文字を許容＋
    # 納期超過等の報告/通達依頼でも発火(一覧を verbatim で載せる意図)。
    _wants_verbatim = bool(re.search(r"(上記|この|その|それら|これら).{0,20}(タスク|リスト|一覧|件|もの|プロジェクト|PJ)", user_msg or "")) \
        or (bool(re.search(r"納期超過|超過|遅れ|遅延", user_msg or "")) and bool(re.search(r"(通達|報告|確認|連絡|知らせ|一覧|リスト|送)", user_msg or "")))
    if _wants_verbatim:
        prior_asst = [x for x in conv if x["role"] == "assistant"]
        items = []
        for m in reversed(prior_asst[-4:]):            # 直近数件から一覧/表を探す(表は少し前の応答のことがある)
            got = _extract_list_lines(str(m.get("content", "")))
            if len(got) >= 2:
                items = got; break
        _overdue = bool(re.search(r"納期超過|超過|遅れ|遅延", (user_msg or "") + (body or "")))
        if items and _overdue:                         # 納期超過の話題は🔴/超過の行だけに絞る(基本リストを verbatim・関連分のみ)
            items = [it for it in items if re.search(r"納期超過|超過|遅れ|🔴", it)]
            # 超過行が1つも無ければ verbatim を採らず qwen に委ねる(非超過の一覧を『納期超過』見出しで出さぬ・殿指摘)
        if len(items) >= 2:
            # 用件の骨子(1文)。定番は clean template、それ以外はqwen1文目を汎用化。一覧は verbatim(表の行はそのまま)。
            if _overdue:
                intro = "現在、以下のプロジェクトが納期超過となっています。ご確認の上、状況のご報告をお願いします。"
            elif re.search(r"担当(では|じゃ)?な|担当外|自分の.*でな|私の.*でな", (body or "") + (user_msg or "")):
                intro = "下記のタスクは私の担当ではないようです。ご確認・アサインの修正をお願いできますでしょうか。"
            else:
                intro = re.split(r"[。\n！？]", body)[0].strip()   # qwen本文の1文目=用件の骨子
                intro = re.sub(r"[（(]?c?\d+\s*[〜~\-－]\s*c?\d+[）)]?", "", intro)   # 誤ったカット範囲要約を除去
                intro = re.sub(r"[（(]?c\d+(?:[、,]\s*c\d+)*[）)]?", "", intro)
                intro = re.sub(r"の\s*(および|、|の|及び)\s*", "", intro).replace("  ", " ").strip()
                if intro and not re.search(r"[。！？]$", intro):
                    intro += "。"
            body = (intro + "\n\n" if intro else "") + "\n".join(items)   # 一覧は verbatim(・を付けず会話のまま)
    if not (to and body):
        return None
    reply = (f"**{_uid_to_name(to)}** 宛に以下のDM下書きを作成しました。↓の承認カードで確認・編集し、"
             "ボタンを押すと送信されまする（まだ送っておりませぬ）。\n\n> " + body.replace("\n", "\n> "))
    args = {"to_user_id": to, "body": body}
    if who.get("uid"):
        args["actor_id"] = who["uid"]
    return {"tool": "send_message", "args": args, "reply": reply}


def _validate_assets(text):
    """【出口検問=Fable5処方】応答内の /asset URL を資産台帳と照合し、実在せぬファイル名(LLMの捏造)を
    ユーザーに届けない。画像markdownは除去(割れ画像を出さぬ)・リンクは注記化。確率0で破れぬ最終防壁。"""
    text = _strip_tool_leak(text)                          # ツール呼びの漏れ・作業実況を掃除
    if not text or "/asset/" not in text or not casper_manifest:
        return text
    try:
        real = casper_manifest.real_names()
    except Exception:
        return text
    if not real:
        return text
    import urllib.parse as _up

    def _fn(u):
        return os.path.basename(_up.unquote(u.split("?")[0].split("#")[0]))

    def _img(m):                                            # 画像: 実在せぬなら丸ごと除去
        return m.group(0) if _fn(m.group(1)) in real else ""
    text = re.sub(r"!\[[^\]]*\]\((/asset/[^)\s]+)\)", _img, text)

    def _lnk(m):                                           # リンク: 実在せぬなら注記
        return m.group(0) if _fn(m.group(2)) in real else f"[未確認: {m.group(1)}]"
    text = re.sub(r"(?<!!)\[([^\]]+)\]\((/asset/[^)\s]+)\)", _lnk, text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _validate_report_html(html):
    """【報告書の出口検問=Fable5 #4】報告書HTMLの /asset リンク(img src / a href)を台帳照合し、
    実在せぬ捏造リンクを除去/注記化してから Aurora保存。報告書は数値・固有名・リンクの捏造面ゆえ最終防壁を置く。"""
    if not html or "/asset/" not in html or not casper_manifest:
        return html
    try:
        real = casper_manifest.real_names()
    except Exception:
        return html
    if not real:
        return html
    import urllib.parse as _up

    def _fn(u):
        return os.path.basename(_up.unquote(u.split("?")[0].split("#")[0]))

    def _img(m):                                            # <img src=/asset/x>: 実在せぬなら注記に置換
        return m.group(0) if _fn(m.group(1)) in real else "<span style=\"color:#b91c1c\">[未確認画像]</span>"
    html = re.sub(r'<img[^>]*\bsrc=["\'](/asset/[^"\']+)["\'][^>]*>', _img, html)

    def _a(m):                                             # <a href=/asset/x>txt</a>: 実在せぬならリンク剥がしテキストのみ
        return m.group(0) if _fn(m.group(1)) in real else m.group(2)
    html = re.sub(r'<a[^>]*\bhref=["\'](/asset/[^"\']+)["\'][^>]*>(.*?)</a>', _a, html, flags=re.S)
    return html


_EMAIL_UID_CACHE = {}


def _verify_score_token(tok):
    """Score 互換 JWT(HS256)を検証→payload(dict)を返す。失敗時 None。(sub=email, uid=Calendar uid)"""
    if not (tok and JWT_SECRET):
        return None
    try:
        import jwt as _jwt
        return _jwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def _email_to_uid(email):
    if not email:
        return ""
    if email in _EMAIL_UID_CACHE:
        return _EMAIL_UID_CACHE[email]
    uid = ""
    local = email.split("@")[0].lower()
    try:
        for u in (casper_tools._get("/users?limit=200").get("items", []) if casper_tools else []):
            em = (u.get("email") or "").lower(); un = (u.get("username") or "").lower()
            if em == email.lower() or un == email.lower() or (un and un == local):
                uid = u.get("id"); break
    except Exception:
        uid = ""
    _EMAIL_UID_CACHE[email] = uid
    return uid


def identify(handler):
    """発信元を識別。本人確定は **JWT 検証** のみ(名前選択だけの成りすまし防止)。
    優先: X-Actor-User-Id(組込み時 host が検証済) > 検証済 score_token/casper_token(JWT) > 匿名 sid。"""
    ck = http.cookies.SimpleCookie()
    try:
        ck.load(handler.headers.get("Cookie", "") or "")
    except Exception:
        pass
    uid = (handler.headers.get("X-Actor-User-Id", "") or "").strip()   # 組込み host が検証済の uid
    email = ""
    authed = bool(uid)
    if not uid:
        tok = ck["casper_token"].value if "casper_token" in ck else ""
        payload = _verify_score_token(tok) if tok else None             # Casper 独自鍵で検証
        if payload:
            email = payload.get("sub") or ""
            authed = True                                              # トークン有効=認証成功
            uid = str(payload.get("uid") or _email_to_uid(email) or "")  # token の uid 優先(login で Calendar /api/me から確定)
    sid = ck["casper_sid"].value if "casper_sid" in ck else ""
    new_sid = ""
    if not sid:
        sid = uuid.uuid4().hex[:16]
        new_sid = sid
    ip = handler.client_address[0] if getattr(handler, "client_address", None) else ""
    # 本人キー: uid 優先・無ければ email(認証済) ・最後に sid
    return {"uid": uid, "email": email, "authed": authed, "sid": sid, "ip": ip, "new_sid": new_sid}


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
            return (s or "").lower() not in _TASK_DONE | {"omit", "cancelled"}   # 完了(deliver)/除外は非active(新19値)
        due = [t for t in tasks if str(t.get("due_date") or "").startswith(today) and active(t.get("status"))]
        if due:
            parts.append(f"本日({today})締切のタスク {len(due)}件:")
            for t in due[:45]:
                parts.append(f"  - {t.get('name')} [{t.get('status')}] 担当:{umap.get(t.get('assigned_to'),'未割当')}")
        else:
            parts.append(f"本日({today})締切のタスク: なし")
        ip = sum(1 for t in tasks if (t.get("status") or "").lower() in ("wip", "in-progress", "in_progress"))
        mk = sum(1 for t in tasks if (t.get("status") or "").lower() in ("mk", "todo"))
        parts.append(f"(タスク全体: 進行中(wip) {ip} / 未着手(mk) {mk} / 総数 {len(tasks)})")
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
        return (s or "").lower() not in _TASK_DONE | {"omit", "cancelled"}   # 完了/除外以外=未完了(新19値・deliverのみ完了)
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


def shot_assignee_digest(query):
    """ショットの担当はshotテーブルに無く task.shotID 経由。クエリにPJ名+カット/担当語があれば
    shot×task を shotID で突合し『カット×担当×工程』を先読み注入する。"""
    if not casper_tools:
        return ""
    if not re.search(r"カット|cut|ショット|shot|担当|アサイン|誰", query or "", re.I):
        return ""
    _get = casper_tools._get
    try:
        projs = _get("/projects?limit=200").get("items", [])
        ql = (query or "").lower()
        pj = next((p for p in projs if (p.get("name") or "").lower() in ql
                   or (p.get("name") or "").lower() in ql.replace(" ", "")), None)
        # クエリにPJ名が含まれる方を緩く照合
        if not pj:
            pj = next((p for p in projs if p.get("name") and p["name"].lower() in ql), None)
        if not pj:
            return ""
        pid = pj["id"]
        umap = {u.get("id"): (u.get("username") or u.get("name") or str(u.get("id")))
                for u in _get("/users?limit=200").get("items", [])}
        tasks = []
        for off in (0, 500, 1000):
            page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
            tasks += page
            if len(page) < 500:
                break
        # shotID -> [(task名, 担当, status)]
        from collections import defaultdict
        bysh = defaultdict(list)
        for t in tasks:
            if str(t.get("project_id")) != str(pid):
                continue
            sid = str(t.get("shotID") or "").strip()
            if sid:
                bysh[sid].append((t.get("name"), umap.get(t.get("assigned_to"), "未割当"), t.get("status")))
        if not bysh:
            return ""
        lines = [f"{pj['name']} のカット×担当 (task.shotID 経由・shotに担当列は無い):"]
        for sid in sorted(bysh):
            for nm, who, st in bysh[sid]:
                lines.append(f"  {sid}: {nm} 担当={who} [{st}]")
        return "\n\n## カット別 担当 (左脳・task結合)\n" + "\n".join(lines[:60])
    except Exception:
        return ""


def cross_digest(query):
    """横断クエリ(全PJで一番遅れてる/誰が/やばい 等)に、全PJ遅延のPJ別・担当別ランキングを注入。"""
    if not casper_tools:
        return ""
    if not re.search(r"全(PJ|プロジェクト|案件)|一番|最も|誰が.*遅|やばい|横断|全体|ランキング|ワースト|どのPJ|どのプロジェクト", query or "", re.I):
        return ""
    try:
        _get = casper_tools._get
        tasks = []
        for off in (0, 500, 1000):
            page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
            tasks += page
            if len(page) < 500:
                break
        pm = {p["id"]: p.get("name") for p in _get("/projects?limit=200").get("items", [])}
        um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
              for u in _get("/users?limit=200").get("items", [])}
        import collections
        _tdy = datetime.date.today().isoformat()          # 遅延=isOverdue派生(due<today かつ status∉{deliver,omit})。旧delayedステータスは廃止(2026-07-08)
        dl = [t for t in tasks if str(t.get("due_date") or "")[:10] and str(t.get("due_date") or "")[:10] < _tdy
              and (t.get("status") or "").lower() not in _TASK_NOT_OVERDUE]
        byp = collections.Counter(pm.get(t.get("project_id"), t.get("project_id")) for t in dl)
        byu = collections.Counter(um.get(t.get("assigned_to"), "未割当") for t in dl)
        lines = [f"全PJ遅延タスク総数: {len(dl)}件",
                 "PJ別遅延(多い順): " + ", ".join(f"{k}={v}" for k, v in byp.most_common(6)),
                 "担当者別遅延(多い順): " + ", ".join(f"{k}={v}" for k, v in byu.most_common(6))]
        return "\n\n## 横断サマリ(全PJ遅延・左脳ライブ)\n" + "\n".join(lines)
    except Exception:
        return ""


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


THREADS_DIR = os.path.join(HERE, "threads")


def _user_key(who):
    """ユーザー識別キー: uid > email(認証済) > sid(端末)。"""
    if who.get("uid"):
        return "u_" + str(who.get("uid"))
    if who.get("email"):
        return "e_" + re.sub(r"[^A-Za-z0-9]", "_", str(who.get("email")))
    return "s_" + str(who.get("sid") or "anon")


def _user_keys(who):
    """このユーザーに紐づく保存キー候補(uid/email/現sid)。識別子が後から確定(email→uid)しても旧スレッドを拾う。"""
    keys = []
    if who.get("uid"):
        keys.append("u_" + str(who["uid"]))
    if who.get("email"):
        keys.append("e_" + re.sub(r"[^A-Za-z0-9]", "_", str(who["email"])))
    if who.get("sid"):
        keys.append("s_" + str(who["sid"]))
    return keys or ["s_anon"]


def _thread_dir(who):
    d = os.path.join(THREADS_DIR, re.sub(r"[^A-Za-z0-9_]", "", _user_key(who)))
    os.makedirs(d, exist_ok=True)
    return d


def thread_list(who):
    out, seen = [], set()
    for key in _user_keys(who):                        # 旧キー(email/sid)も横断して拾う
        d = os.path.join(THREADS_DIR, re.sub(r"[^A-Za-z0-9_]", "", key))
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            try:
                t = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                tid = t.get("id")
                if tid in seen:
                    continue
                seen.add(tid)
                # タイトルは先頭ユーザー発言から都度生成(保存タイトルの凍結ズレを回避→中身と一致)
                first = next((m.get("content", "") for m in t.get("messages", [])
                              if m.get("role") == "user" and m.get("content")), "")
                title = (str(first)[:24] if first else (t.get("title") or "(無題)"))
                out.append({"id": tid, "title": title,
                            "updated": t.get("updated", ""), "n": len(t.get("messages", []))})
            except Exception:
                pass
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return out


def thread_get(who, tid):
    safe = re.sub(r"[^A-Za-z0-9_]", "", str(tid)) + ".json"
    for key in _user_keys(who):                        # キー横断で旧スレッドも開ける
        p = os.path.join(THREADS_DIR, re.sub(r"[^A-Za-z0-9_]", "", key), safe)
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    return {"id": tid, "title": "(無題)", "messages": []}


def thread_save(who, tid, messages, title=None):
    tid = re.sub(r"[^A-Za-z0-9_]", "", str(tid)) or uuid.uuid4().hex[:12]
    p = os.path.join(_thread_dir(who), tid + ".json")
    cur = thread_get(who, tid)
    if not title:
        title = cur.get("title") if cur.get("title") not in (None, "(無題)") else None
    if not title:                                  # 最初のユーザー発言をタイトルに
        first = next((m["content"] for m in messages if m.get("role") == "user" and m.get("content")), "")
        title = (str(first)[:24] or "(無題)")
    rec = {"id": tid, "title": title, "uid": who.get("uid", ""),
           "updated": datetime.datetime.now().isoformat(timespec="seconds"),
           "messages": messages[-200:]}
    json.dump(rec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return {"id": tid, "title": title}


def thread_delete(who, tid):
    p = os.path.join(_thread_dir(who), re.sub(r"[^A-Za-z0-9_]", "", str(tid)) + ".json")
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


def portfolio_digest(query):
    """制作実績クエリ時、自社Vimeoポートフォリオを先読み注入(個人経歴との混同防止)。"""
    if not re.search(r"実績|ポートフォリオ|portfolio|作品|制作事例|過去案件|どんな.*作", query or "", re.I):
        return ""
    try:
        p = os.path.join(VAULT, "30_culture_rules", "ops_vimeo_portfolio.md")
        if os.path.exists(p):
            t = open(p, encoding="utf-8").read()
            tbl = t.split("|", 1)
            body = ("|" + tbl[1]) if len(tbl) > 1 else t
            return ("\n\n## 自社制作実績(Vimeo公開・これが一次の会社実績)\n" + body[:3500]
                    + "\n※会社実績はこのVimeo公開分を主に挙げよ。FF/Samurai Jack等は個人メンバーの経歴ゆえ区別。")
    except Exception:
        pass
    return ""


def image_asset_digest(query):
    """画像/静止画/カット系クエリ時、該当する asset_shadow の【実在する】画像URLを機械的に集めて注入。
    ファイル名は実ファイル(vault/50_asset_shadows/files)から取るため捏造不能。RAGの取りこぼし/qwenの名前改変を断つ。"""
    if not re.search(r"画像|静止画|イメージ|カット|サムネ|絵コンテ|ビジュアル|見せ|表示|一覧|image|cut|still", query or "", re.I):
        return ""
    try:
        import glob
        files_dir = os.path.join(ASSET_DIR, "files")
        if not os.path.isdir(files_dir):
            return ""
        IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif")
        # クエリの語(2文字以上の英数 + 日本語固有語)を抽出
        ql = (query or "").lower()
        qtok = set(re.findall(r"[a-z0-9]{2,}", ql)) | set(re.findall(r"[ぁ-んァ-ヶ一-龠]{2,}", query or ""))
        rows = []   # (asset_md, image_filename, desc, score)
        for md in sorted(glob.glob(os.path.join(ASSET_DIR, "*.md"))):
            try:
                t = open(md, encoding="utf-8").read()
            except Exception:
                continue
            m = re.search(r'^asset:\s*(.+\.(?:png|jpg|jpeg|webp|gif))\s*$', t, re.M | re.I)
            if not m:
                continue
            fname = os.path.basename(m.group(1).strip())
            if not os.path.exists(os.path.join(files_dir, fname)):   # 実在するファイルのみ(捏造防止の核)
                continue
            blob = t.lower()
            score = sum(1 for tok in qtok if tok in blob)            # クエリ語の一致数で関連度
            # 説明文(提供者記入)を1行に
            dm = re.search(r"##\s*説明.*?\n(.*?)(?=\n##|\Z)", t, re.S)
            desc = re.sub(r"\s+", " ", (dm.group(1) if dm else "")).strip()[:80]
            rows.append((fname, desc, score))
        if not rows:
            return ""
        hits = [r for r in rows if r[2] > 0]
        if not hits:
            return ""
        hits.sort(key=lambda r: -r[2])
        hits = hits[:30]
        lines = [f"- `![]( /asset/{fn} )` — {desc}".replace("( /asset/", "(/asset/").replace(" )", ")")
                 for fn, desc, _ in hits]
        return ("\n\n## 該当する画像アセット（実在確認済・URLは下記を一字一句そのまま使え／改変・接頭辞付与・拡張子変更を一切するな）\n"
                + "\n".join(lines)
                + "\n※ここに無いファイル名の画像URLを書くな（存在しない＝表示されない）。全件出すよう求められたら上記を省略せず全て出せ。")
    except Exception:
        return ""


def dm_threads(who):
    """ログイン中ユーザーのDMスレッド一覧を取得(get_messages相当)。
    書込トークン(per-user)が要る→未発行時は空。{threads:[{id,peer,unread,last,ts}], available:bool}"""
    uid = who.get("uid")
    def _dmlog(m):
        try:
            with open(os.path.join(HERE, "dm_debug.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {m}\n")
        except Exception:
            pass
    if not (who.get("authed") and uid and WRITE_TOKEN and casper_mcp):
        _dmlog(f"SKIP authed={who.get('authed')} uid={uid!r} wt={bool(WRITE_TOKEN)} mcp={bool(casper_mcp)}")
        return {"available": False, "threads": []}
    try:
        import concurrent.futures as _cf
        raw = casper_mcp.call_tool("get_messages", {"actor_id": int(uid)}, token=WRITE_TOKEN, actor=uid)
        data = json.loads(raw) if (raw or "").strip().startswith("{") else {}
        threads = sorted((data.get("threads") or []), key=lambda t: str(t.get("updated_at") or ""), reverse=True)[:20]
        for _t in threads:                              # DM participants から名簿を収穫(RO非依存で恒久cacheが育つ)
            _roster_observe(_t.get("participants"))

        def _chk(t):                                   # 相手からの未読(read_at=None)があるスレッドか
            try:
                r = casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(t.get("thread_id"))},
                                         token=WRITE_TOKEN, actor=uid)
                md = json.loads(r) if (r or "").strip().startswith("{") else {}
                return (t, any(str(m.get("sender_id")) != str(uid) and not m.get("read_at")
                               for m in (md.get("messages", []) or [])))
            except Exception:
                return (t, False)
        with _cf.ThreadPoolExecutor(max_workers=10) as _ex:
            checked = list(_ex.map(_chk, threads))
        out = []
        for t, un in checked:
            if not un:                                 # 未読(新規)のみ表示
                continue
            peers = [p for p in (t.get("participants", []) or []) if str(p.get("user_id")) != str(uid)]
            out.append({"id": t.get("thread_id"),
                        "peer": "、".join(str(p.get("name") or p.get("user_id")) for p in peers[:3]) or "(自分)",
                        "peer_id": (peers[0].get("user_id") if peers else None),
                        "unread": 1,
                        "last": str(t.get("last_message") or "")[:60],
                        "ts": str(t.get("updated_at") or "")[:19]})
        out.sort(key=lambda x: x.get("ts", ""), reverse=True)
        _dmlog(f"OK uid={uid} unread={len(out)}/{len(threads)}")
        return {"available": True, "threads": out}
    except Exception as e:
        _dmlog(f"ERR uid={uid}: {e}")
        return {"available": False, "threads": [], "error": str(e)[:120]}


def dm_messages(who, thread_id):
    """指定DMスレッドの本文取得。"""
    uid = who.get("uid")
    if not (who.get("authed") and uid and WRITE_TOKEN and casper_mcp):
        return {"available": False, "messages": []}
    try:
        raw = casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(thread_id)},
                                   token=WRITE_TOKEN, actor=uid)
        data = json.loads(raw) if (raw or "").strip().startswith("{") else {}
        msgs = data.get("messages", []) or []
        for m in msgs:                                  # 送信者を必ず"名前"で(_uid_to_name=堅牢解決・生uid表示を避ける)
            sid = m.get("sender_id")
            m["sender_name"] = ("あなた" if str(sid) == str(uid)
                                else ("システム" if sid is None else _uid_to_name(sid)))
        try:                                            # Casperで開いて読んだら既読化(mark_read・Nibu提供)
            casper_mcp.call_tool("mark_read", {"actor_id": int(uid), "thread_id": int(thread_id)},
                                 token=WRITE_TOKEN, actor=uid)
        except Exception:
            pass
        return {"available": True, "messages": msgs}
    except Exception as e:
        return {"available": False, "messages": [], "error": str(e)[:120]}


def user_profile_digest(who):
    """ログイン中ユーザーの蓄積プロファイル(profile_<ukey>.md)を先読み注入。
    会話学習で深まったユーザー理解を毎回の応対に反映する。"""
    try:
        if not who.get("authed"):
            return ""
        p = os.path.join(VAULT, "20_people", f"profile_{_user_key(who)}.md")
        if not os.path.exists(p):
            return ""
        t = open(p, encoding="utf-8").read()
        if "## Casper の理解" in t:
            u = t.split("## Casper の理解", 1)[1].strip()[:1200]
            return "\n\n## 話し相手(ログイン中ユーザー)についての理解\n" + u + \
                   "\nこの理解を踏まえ、相手に合わせて応対せよ(押し付けず・自然に)。"
    except Exception:
        pass
    return ""


def activity_digest(who):
    """ログイン中ユーザーの『動向帯』(25_activity/u_<uid>.md)を先読み注入=動向層＝経験層。
    静的プロファイル(個性)が"何者か"なら、こちらは"今どうしているか"(筋/未決/乗り替わり)。
    掟(推測は明示/状態は文面でなく実物照会/語は串刺し/人の癖)つきで渡す。設計=Aurora動向層設計メモ。"""
    try:
        if not who.get("authed"):
            return ""
        p = os.path.join(VAULT, "25_activity", f"{_user_key(who)}.md")
        if not os.path.exists(p):
            return ""
        t = open(p, encoding="utf-8").read()
        # 未決(open loop)=結末が出ていない継続中の筋を拾う(解決済み=クローズ/完了 行は除外)
        opens = [ln.strip() for ln in t.splitlines()
                 if ("OPEN LOOP" in ln or "未了" in ln or "待ち" in ln)
                 and ln.strip().startswith("-")
                 and not any(k in ln for k in ("クローズ", "完了", "問題ない", "解消"))][:6]
        # 先読み候補セクション(既に「(推測)」明示済)を抜く
        yomi = ""
        if "### 先読み候補" in t:
            yomi = t.split("### 先読み候補", 1)[1]
            yomi = re.split(r"\n#{2,3} ", yomi, maxsplit=1)[0].strip()[:1000]
        if not opens and not yomi:
            return ""
        out = "\n\n## 話し相手の直近の動向(動向層＝経験層・先読み材料)\n"
        if opens:
            out += "未決/継続中(open loop):\n" + "\n".join(opens) + "\n"
        if yomi:
            out += "\n先読み候補:\n" + yomi + "\n"
        out += ("\n【この動向の使い方=掟】黙って先回りの材料に使え(押し付けず)。"
                "①ここから述べる見立ては必ず『(推測)』と明示せよ(捏造禁止＞人格)。"
                "②『〜された?/終わった?/上がった?』等 状態を問われたら、この帯の古い記述を鵜呑みにせず"
                "実物(Calendar/Vimeo/Score)を確認してから答えよ(『作業中』報告を結末と誤認するな)。"
                "③『資料/データ/動画/静止画』等 揺れる語は同一対象へ串刺しで解釈せよ。"
                "④相手ごとの伝え方の癖を踏まえて新しい問いを読め。")
        return out
    except Exception:
        return ""


def _bounded(fn, sec, default=None):
    """fn() を sec秒だけ待ち、超えたら default を返す(遅いMCP/RAGで応答をhangさせない安全弁)。"""
    import concurrent.futures as _cf
    _ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        return _ex.submit(fn).result(timeout=sec)
    except Exception:
        return default
    finally:
        _ex.shutdown(wait=False)


# 状態を問う問い(〜された?/上がった?/どうなってる?/進捗?)を機械的に検知する関門。
_STATE_Q_RE = re.compile(
    r"(され(た|てる|てます|ました)|終わ(った|り|りました)|上が(った|ってる|りました)|"
    r"完了|できた|反映(され|した)|届い(た|てる)|返信.*(来|きた|あった)|アップ.*(済|した|された)|"
    r"どうなっ(て|た)|進捗|状況|現状|状態|もう.{0,6}(？|\?)|終わってる|できてる)", re.I)


def verify_digest(who, query):
    """【検証ゲート=pre_verify機構】状態を問う問いには、応答前に live照会を強制し、
    出所タグ(live/帯/推測)を義務づける。掟②が"促す"だけだったのを"通さねば"の機構へ格上げ。
    (Fable5 診断 #1: 裏取りを掟から機構へ / 2026-07-02)。状態質問でなければ空。"""
    try:
        if not query or not _STATE_Q_RE.search(query):
            return ""
        uid = who.get("uid")
        live = ""
        if uid and WRITE_TOKEN and casper_mcp:
            # Calendar照会は7秒で見切る(遅延時に応答をhangさせない=無応答事故の防止)
            ev = _bounded(lambda: casper_mcp.call_tool("get_events", {"actor_id": int(uid), "since": 0, "limit": 30},
                                                       token=WRITE_TOKEN, actor=int(uid)), 7, None)
            if isinstance(ev, str) and ev.strip().startswith("{") and "error" not in ev[:40]:
                live += "\n【live: Calendar 最新イベント(get_events)】\n" + ev[:1500]
            try:
                tt = _bounded(lambda: casper_mcp.call_tool("get_today_tasks", {}, token=WRITE_TOKEN, actor=int(uid)), 7, None)
                if isinstance(tt, str) and tt.strip().startswith(("{", "[")):
                    live += "\n【live: 本日タスク】\n" + tt[:900]
            except Exception:
                pass
        return ("\n\n## 【検証ゲート】状態確認の問い — 応答前の裏取り必須\n"
                "この問いは『状態(〜された?/上がった?/どうなってる?/進捗)』を尋ねている。掟②に従い:\n"
                "・動向層の帯は as-of 時点のスナップショットゆえ、その古い記述を『結末』と誤認して断定するな。\n"
                "・下記の live 照会を最優先の根拠にせよ。live に無ければ『現時点では確認できておらぬ』と正直に述べよ。\n"
                "・**回答の各事実に出所を明示せよ**: 【live】(今照会した実状態)／【帯】(動向層の過去記述)／【推測】。\n"
                "・**出所タグの無い状態断定は禁止**。"
                + (live or "\n(live照会は取得できず＝『未確認』として答えよ)"))
    except Exception:
        return ""


# 資料/データの"存在"を問う問い(〜はある?/無い/登録されて/見せて/探して/どこ)
_EXIST_Q_RE = re.compile(
    r"(あります|ある(か|の|\?|？)|ありませんか|無い|ない(か|の|\?|？)|存在|登録(され|して)|見せて|見たい|"
    r"探して|見つ|どこ(に|\?|？)|持って(る|いる|ますか)|残って(る|いる)|"
    r"(資料|画像|動画|データ|ファイル|素材|静止画|コンテ|絵|写真|映像|ドキュメント|議事録).{0,10}(は|が|ある|無|ない|見|探|どこ|欲し))", re.I)


def existence_digest(who, query):
    """【存在ゲート=retrieve-then-render/Fable5 #2】資料/データの有無を問う問いには、RAGの散文でなく
    資産台帳(決定的)を引き、実在ファイルの構造化リスト＋総数を注入。モデルは"このリストを語るだけ"に縮み、
    実名を与えられるので捏造せず、COUNTで網羅漏れ・『在るのに無い』誤断が構造的に消える。"""
    try:
        if not query or not _EXIST_Q_RE.search(query):
            return ""
        rows = []
        if casper_manifest:                               # 台帳=決定的真実源(存在は"事実")
            try:
                rows = casper_manifest.search(query, limit=80)
            except Exception:
                rows = []
        rag = []                                          # 補助RAGは廃止(台帳が真実源・hybridは重く応答を遅延させる為)
        if rows:
            shown = rows[:45]
            lines = []
            for m in shown:
                d = (m.get("desc") or "").replace("\n", " ").strip()[:70]
                lines.append(f"- {m['name']}" + (f" — {d}" if d else ""))
            more = f"\n(ほか {len(rows) - len(shown)} 件)" if len(rows) > len(shown) else ""
            return ("\n\n## 【存在確認=資産台帳の照会結果(決定的・唯一の真実源)】\n"
                    f"この問い『〜はあるか』に対し、資産台帳を引いた実在ファイル **計{len(rows)}件**(下記が全件):\n"
                    + "\n".join(lines) + more +
                    "\n──\n・**上記の実ファイル名だけを使え**。ここに無い名を推測で書くな(＝捏造・存在しない)。\n"
                    f"・この台帳の件数は上記の**{len(rows)}件が全て**(vault内の資産ファイルについて)。一部だけ見て断定するな。\n"
                    "・画像を見せるなら `![](/asset/実ファイル名)` を上記から選んで書け。\n"
                    "・注意: この台帳は**vault内の資産ファイル**の真実源であり、**Vimeo動画・Aurora資料・Calendar記録**の有無は"
                    "別物。台帳が0件でもそれらに在り得るゆえ、『世界に存在しない』とは断ずるな(必要なら別途照会)。"
                    + (("\n【補助: 説明の文脈(RAG)】\n" + "\n".join(f"- {r}" for r in rag)) if rag else ""))
        # 台帳0件 → RAG補助で留保付き回答(捏造も断定もさせぬ)
        block = "\n".join(f"- {h}" for h in rag[:5]) if rag else "(台帳・RAG共にヒットなし)"
        return ("\n\n## 【存在確認ゲート】資料/データの有無を問う問い\n"
                "資産台帳を引いたが該当ファイルは0件。参考(RAG):\n" + block +
                "\n・別名(TKP↔Nina等)を変えて再照会せよ。それでも無ければ『確認できた範囲では見当たらぬ』と"
                "留保付きで述べよ——**存在せぬファイル名を推測で書くな**。")
    except Exception:
        return ""


# 進行中PJ一覧/納期を尋ねる問い・直前の一覧への言及(上記リスト等)を検知
_PROJ_Q_RE = re.compile(
    r"(動いて(る|いる)|進行中|稼働中|現在.{0,4}(プロジェクト|PJ|案件)|"
    r"(プロジェクト|PJ|案件).{0,8}(一覧|教え|何|どれ|進行|ある|動)|"
    r"納期|締切|〆|遅れ|遅延|上記.{0,5}(リスト|一覧|PJ|プロジェクト|案件|の))", re.I)


def projects_digest(query):
    """【進行中PJ一覧=retrieve-then-render】『動いているPJは?』『上記リストの納期遅れ』等には Calendar
    (cal_projects.json)から online PJ を本日日付＋納期超過印つきで注入し、ツールを呼ばず一覧から答えさせる
    (qwenのツール呼び失敗＋"上記"=直前回答を参照できぬ文脈欠落 の両方を機構で回避)。"""
    try:
        if not query or not _PROJ_Q_RE.search(query):
            return ""
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        online = [p for p in items if str(p.get("display_status") or "online") == "online"]
        if not online:
            return ""
        today = datetime.date.today().isoformat()
        overdue = []
        lines = []
        for p in online[:40]:
            due = str(p.get("end_date") or "")[:10]
            is_late = bool(due) and due < today and str(p.get("status") or "") not in ("completed", "done", "cancelled")
            if is_late:
                overdue.append(p.get("name"))
            lines.append(f"- {p.get('name')}（{p.get('status')}" + (f"・〆{due}" if due else "")
                         + ("・🔴納期超過" if is_late else "") + "）")
        latenote = (f"\n\n**本日{today}時点で納期超過(🔴)は {len(overdue)}件: "
                    + "、".join(overdue) + "**") if overdue else f"\n\n※本日{today}時点で納期超過なし。"
        return (f"\n\n## 【進行中プロジェクト一覧(Calendar・確定・本日{today})】\n"
                f"現在 online の全{len(online)}件。**この一覧を根拠に答えよ(『上記リスト』とはこれ)。"
                "PJ一覧の取得に calendar_lookup 等を呼ぶな(この一覧を使え)・『〜を取得します』の実況や ```tool ブロックを書くな。"
                "ただし DM送信(send_message)等の別アクションは通常通りツールで実行せよ(送信前は承認待ち下書きになる。"
                "まだ送っていないのに『報告しました/送信しました』と既成事実化するな)**:\n"
                + "\n".join(lines) + latenote)
    except Exception:
        return ""


_TASKS_CACHE = {"at": 0.0, "items": []}


def _all_tasks(ttl=90):
    """全タスクを取得(ページング)。短命キャッシュ(既定90秒)で連問を高速化。"""
    import time
    now = time.time()
    if _TASKS_CACHE["items"] and now - _TASKS_CACHE["at"] < ttl:
        return _TASKS_CACHE["items"]
    if not casper_tools:
        return []
    out = []
    for off in (0, 500, 1000, 1500, 2000):
        page = casper_tools._get(f"/tasks?limit=500&offset={off}").get("items", [])
        out += page
        if len(page) < 500:
            break
    if out:
        _TASKS_CACHE["items"] = out
        _TASKS_CACHE["at"] = now
    return out


_ACTIVE_TASK_Q_RE = re.compile(
    r"(動いて(る|いる).{0,6}タスク|進行中.{0,6}タスク|現在.{0,8}タスク|稼働.{0,6}タスク|"
    r"wip.{0,6}タスク|タスク.{0,6}(動いて|進行中|稼働中))", re.I)


def active_tasks_digest(query):
    """【進行中タスク一覧=retrieve-then-render】『現在動いているタスクは?』に、全PJの進行中(wip/工程)タスクを
    プロジェクト別に注入する。get_today_tasks(本日締切のみ)に狭めるのを防ぐ——殿指摘『遅延PJが"動いているタスク"に
    出ず"動いていない"と誤解する』の恒久策(2026-07-08)。"""
    try:
        if not query or not _ACTIVE_TASK_Q_RE.search(query):
            return ""
        tasks = _all_tasks()
        if not tasks:
            return ""
        act = [t for t in tasks if _task_is_moving(t)]   # API category=='in_progress' 優先(内蔵setはfallback)
        if not act:
            return "\n\n## 【現在進行中(wip)のタスク】\n現在 wip 状態のタスクはありません(この事実を答えよ)。"
        pm = {p.get("id"): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])}
        except Exception:
            um = {}
        import collections
        byp = collections.defaultdict(list)
        for t in act:
            byp[pm.get(t.get("project_id"), t.get("project_id") or "?")].append(t)
        lines = []
        for pj, ts in sorted(byp.items(), key=lambda x: -len(x[1])):
            who_names = sorted({um.get(t.get("assigned_to"), "未割当") for t in ts})
            lines.append(f"- **{pj}**: 進行中 {len(ts)}件 (担当: {', '.join(who_names[:6])})")
        return (f"\n\n## 【現在進行中(wip/工程)のタスク一覧(Calendar・確定)】\n"
                f"全プロジェクトで進行中のタスクは計 {len(act)}件。**この一覧を根拠に答えよ。"
                "get_today_tasks(本日締切のみ)や特定PJ(marukome等)に狭めず、全PJの進行中を示せ。"
                "『動いているタスク』の問いには本一覧が答え(本日締切とは別物)**:\n" + "\n".join(lines))
    except Exception:
        return ""


def open_loop_digest(who):
    """【OPEN LOOPレジストリの先読み注入】この人が依頼元/通知先の"未了の約束"を⚙レコードから注入。
    帯の散文でなくレコードゆえ、Casperは常に把握し漏らさない(Fable5 #2・hori事件の恒久解)。"""
    try:
        if not casper_openloop:
            return ""
        loops = casper_openloop.open_for(who.get("uid"))
        done = casper_openloop.recently_closed(who.get("uid"))
        if not loops and not done:
            return ""
        out = "\n\n## 【約束の追跡(OPEN LOOP・⚙レコードで自動監視)】\n"
        if done:
            out += "▼ 最近 完了を自動検知(利用者へ先読み報告してよい):\n"
            for r in done[:5]:
                out += f"- ✅ {r.get('title')} — {r.get('evidence','')}\n"
        if loops:
            out += "▼ 未了(追跡中):\n"
            for r in loops[:8]:
                pt = {"vimeo": "Vimeoアップ待ち", "asset": "資料登録待ち", "manual": "手動確認"}.get(
                    r.get("probe", {}).get("type"), "")
                out += (f"- {r.get('title')}"
                        + (f"(相手: {r['assignee']})" if r.get("assignee") else "")
                        + f" [{pt}・{str(r.get('created_at',''))[:10]}〜]\n")
        out += ("・**状態を問われたら実物照会(裏取り)で最新確認**。完了検知済なら『済んだ』、未達なら"
                "『まだ・催促の頃合いか(推測)』と。Casperが自動で完了を監視している旨も添えてよい。")
        return out
    except Exception:
        return ""


def traits_digest(who, query):
    """【人物traitの決定的注入=Fable5 #5】質問文に名が現れる人物の"癖"を構造化レジストリから注入。
    散文でなくfieldゆえ漏れなく効く(例: horiは作業中報告→『作業中』を結末と誤認せず裏取りせよ)。"""
    try:
        if not (casper_traits and query):
            return ""
        if not _ROSTER_MAP:
            _roster_refresh()                             # roster(uid→名)をロード
        name_to_uid = {nm: uid for uid, nm in _ROSTER_MAP.items()}
        hits = casper_traits.for_text(query, name_to_uid)
        if not hits:
            return ""
        lines = []
        for nm, traits in hits:
            for t in traits[:3]:
                lines.append(f"- {nm}: {t.get('note')}")
        return ("\n\n## 【人物の癖(構造化trait・裏取りの手がかり)】\n"
                "この問いに関わる人物の既知の癖。**これを踏まえて読み、状態は必ず裏取りで確認せよ**:\n"
                + "\n".join(lines))
    except Exception:
        return ""


def log_convo(who, role, content, extra=None):
    """会話を発信元ごとの順序付きスレッドとして記録(文脈=流れ を資産化)。"""
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "uid": who.get("uid", ""), "email": who.get("email", ""), "ukey": _user_key(who),
               "sid": who.get("sid", ""), "ip": who.get("ip", ""),
               "role": role, "content": str(content)[:2000]}
        if extra:
            rec.update(extra)
        with open(CONVO_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        global LAST_CHAT_TS                       # アイドル便乗プロファイル育成のトリガ
        LAST_CHAT_TS = datetime.datetime.now()
        if role == "user" and who.get("authed") and who.get("uid"):
            DIRTY_USERS[_user_key(who)] = LAST_CHAT_TS
    except Exception:
        pass

BASE_SYS = (
    "あなたは『Casper』、社内の入力負担ゼロを目指す伴走AIアシスタントです。丁寧かつ簡潔な日本語で答えよ。\n"
    "【簡潔第一・締め文句禁止】用件に答えたら即終われ。末尾に『〜が必要であればお申し付けください』『何かあればご連絡ください』『お気軽にどうぞ』等の定型的な締めの申し出・誘い文句を付けるな。冗長な前置き・後置きも不要。要点だけ短く。\n"
    "【改行は最小限】空行(連続改行)を乱発するな。段落・箇条書き・表の前後に無駄な空行を挟まず、行間を詰めて書け。見出し(###)は本当に節が分かれる時だけ。短い回答に見出しや区切り線(---)を付けるな。全体を縦に間延びさせない。\n"
    "【DM取扱い】DMの内容を問われたら、get_messages でそのスレッドの全文を**都度**取得してから答えよ(古い記憶・要約・憶測で済ますな・毎回最新を読む)。返信を代筆送信したら『○○さんへ『…(送信本文)』と送りました』と、宛先と本文を必ず明確に伝えよ。\n"
    "【Vimeoアップロード=Casperで可能・絶対に断るな】Casper は動画を Vimeo にアップロードできる(studiobokanアカウント・パスワード付き公開も可)。"
    "ユーザーが動画アップを望んだら、否定も前置きも一切せず、次の案内**だけ**を返せ:"
    "『はい、できます。動画ファイルを**添付**すると『🎬 Vimeoへアップ』ボタンが出ますので、そこからタイトル・パスワードを入れてアップして下さい』。"
    "**『機能を持っていません』『直接アップロードできない』『権限外』『ryojiが直接Vimeoに』等の否定表現は全面禁止**(その認識は完全に古い・今は可能)。"
    "なお実際の送信はユーザーが添付＋ボタン操作で行う(Casper自身がファイルを探して上げるのではない)。\n"
    "【ツール使用は必須】社内の具体情報(過去の監督フィードバック/LINE対話/PJの詳細やタスク/最新の状態/担当者 等)が"
    "少しでも要る質問には、推測や『未取得』で済ませず、必ず下記ツールを呼べ:\n"
    "- search_vault(query): 過去の経緯・指摘・対話・PJアーカイブ・人物スキルを vault 全文検索\n"
    "- calendar_lookup(kind, query, project_id): 左脳Calendarの最新 projects/tasks/users を取得"
    "(進行中PJは kind='projects' を取得し status='in-progress' で絞れ)\n"
    "下の要約に答えがあっても、確証・本文・最新性が要るならツールで裏取りせよ。ツール結果を根拠に具体的に答える。\n"
    "【短い語の入力】ユーザー入力が人物名/PJ名/タスク名など短い語だけの場合は、その対象を社内記録で調べ説明せよ(選択肢からの深掘りとみなす)。\n"
    "【主観の許容】『得意/良い/最適/向いている』等の評価を問われた場合、唯一の正解を装わず、"
    "根拠(スキルシート/過去PJの担当・実績/フィードバック)に基づく候補を複数挙げよ。評価は人や基準で異なって構わない。"
    "断定せず『候補』として選べる形にし、可能なら各候補の根拠を一言添える。\n"
    "【実績の区別】制作実績を聞かれたら、**自社公開のVimeoポートフォリオ(67本・CEATEC/InterBee/Leisure等)を一次の自社実績**として挙げよ。"
    "スキルシート由来の有名作(FF/Samurai Jack等)は『個人メンバーの経歴』であり会社実績と混同するな(区別して述べよ)。"
    "資料に無い作品名を一般知識から創作するな。")

# 演出DNA = bokan_persona v0.4 の核＋[確]項目を応答 stance として常時注入(個性Rnd 由来)。
# [仮]項目・裏の意味の推論は誤発火防止のため除外。事実と解釈の峻別(捏造禁止)を最優先に据える。
PERSONA_SYS = (
    "\n\n【Casper の人格 — 右腕としての振る舞い】"
    "あなたは studio bokan の行動様式『静かに有能、しかし品質の核では退かない右腕』を体現する。源流は当社の演出DNA。次の構えで応答せよ:\n"
    "・品質・作品・技術的正しさを損なう点は、相手が殿や上長でも従順に流さず、理由を添えて指摘・確認する(無礼でなく淡々と)。\n"
    "・相手の次の困りごとを先読みし、予防策・必要な素材・確認事項を先回りで添える。\n"
    "・指示や情報は属人でなく仕組み・手順で回せる形に整える。\n"
    "・仕上がりは主観でなく物理的理由や具体例・リファレンスで握り、誰が見ても同じゴールに収束させる。\n"
    "・品質とコスト/手戻りを同時に最適化する技術判断を示す。\n"
    "・面倒な処理や調べ物は極力 Casper 側が巻き取り、相手の負担を下げる。\n"
    "・緊迫時もユーモアで場を保つ。\n"
    "ただし事実と解釈は必ず峻別し、推測を断定にするな(捏造禁止が最優先・人格より上位)。")


_ROSTER_CACHE = {"v": None}


def team_roster():
    """社内メンバー username→uid 名簿(send_message の宛先取り違え防止)。恒久 roster cache 由来・RO非依存。"""
    if not _ROSTER_MAP:
        _roster_refresh()
    n = len(_ROSTER_MAP)
    if _ROSTER_CACHE["v"] is not None and _ROSTER_CACHE.get("n") == n:
        return _ROSTER_CACHE["v"]
    pairs = [f"{nm}=uid{uid}" for uid, nm in
             sorted(_ROSTER_MAP.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)]
    out = ""
    if pairs:
        out = ("\n\n【社内メンバー名簿 — send_message の to_user_id は必ずこの対応で引け】\n"
               + " / ".join(pairs)
               + "\n【通称・別名】Elvis=ou(uid36)。通称で指示されたらこの対応で引け。"
               + "\n※宛先名に対応する uid を名簿/別名から**厳密に**引け。**推測・似た番号で代用するな**。"
               "名前が名簿にも別名にも無い/曖昧なら、**絶対に送信せず**『どなた宛でしょう？正式なお名前を』と確認せよ。"
               "\n【宛先の鉄則】to_user_id は『メッセージを届ける相手』。『XにDMして』ならXが宛先。"
               "**依頼者本人(あなたに話しかけている相手=actor)を宛先にするな**——自分宛DMは誤り。"
               "\n【本文の鉄則】body は相手に届く『メッセージ本文そのもの』を、依頼者になり代わり一人称で書け。"
               "**『〜とDMしました』『〜を送信しました』等の“行動の説明・報告文”を body にするな**(それは本文でない)。"
               "\n【複数項目はリストで・漏れなく】本文が複数のタスク/項目に触れる時は、本文中で改行＋『・』の箇条書きで"
               "1件ずつ列挙し、相手が一目で分かる形にせよ。ユーザーが『リスト/一覧/これら/上記のタスク』と指す時は、"
               "**伝えた一覧の該当分を漏れなく全て**本文のリストに載せよ(一部だけにするな)。各行にPJ名を添えると親切。"
               "\n例: 依頼『これらのタスクは私の担当でないと思う。kiyotomoに確認してもらうようDMして』(一覧に3件)"
               "→ 宛先=kiyotomo・本文=『お疲れ様です。下記のタスクが私にアサインされていますが、担当ではないと"
               "認識しております。ご確認のうえ、必要なら修正いただけますでしょうか？\\n・[Score検証] Score修正29日分"
               "\\n・[Soul] ac3102\\n・[Soul] ac3001』"
               "\nsend_message 提案時は宛先名を必ず明示し、送信後も『誰(名前)へ何を送ったか』を報告。")
    _ROSTER_CACHE["v"] = out
    _ROSTER_CACHE["n"] = n
    return out


def _calendar_lookup_mcp(args, uid):
    """calendar_lookup を MCP(write token・RO非依存)で実行。RO REST 401 の恒久回避。"""
    if not (casper_mcp and WRITE_TOKEN):
        return "(Calendar照会不可: MCP/token未設定)"
    kind = (args or {}).get("kind") or "projects"
    q = str((args or {}).get("query") or "").lower().strip()
    actor = uid or 28
    try:
        if kind == "users":
            d = json.loads(casper_mcp.call_tool("get_users", {"limit": 200}, token=WRITE_TOKEN))
            items = d.get("items", [])
            if q:
                items = [u for u in items if q in (str(u.get("username") or "") + str(u.get("display_name") or "")).lower()]
            return json.dumps({"total": len(items), "items": items[:40]}, ensure_ascii=False)
        if kind == "tasks":
            _aa = (args or {})
            _wants_today = bool(re.search(r"本日|今日|today", q + str(_aa.get("due_date") or "")))
            if _wants_today:                               # 明示的に本日締切を求める時だけ get_today_tasks
                r = casper_mcp.call_tool("get_today_tasks", {}, token=WRITE_TOKEN, actor=actor)
                d = json.loads(r) if isinstance(r, str) else r
                items = d.get("items", d if isinstance(d, list) else [])
                note = "本日締切のタスク"
            else:                                          # 既定=全PJの進行中(wip/工程)。本日締切に狭めぬ(殿指摘2026-07-08)
                tasks = _all_tasks()
                st = str(_aa.get("status") or "").lower()
                if st:
                    items = [t for t in tasks if st in (t.get("status") or "").lower()]
                else:
                    items = [t for t in tasks if _task_is_moving(t)]   # API category 優先(内蔵setはfallback)
                note = "全PJの進行中(wip/工程)タスク。本日締切に限らない"
            pm = {p.get("id"): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
            for t in items:                                # PJ名を付与(読みやすさ・どのPJか判別)
                if isinstance(t, dict) and t.get("project_id") in pm:
                    t["project_name"] = pm[t["project_id"]]
            for k in ("query", "assignee"):
                v = str(_aa.get(k) or "").lower()
                if v:
                    items = [t for t in items if v in json.dumps(t, ensure_ascii=False).lower()]
            return json.dumps({"total": len(items), "items": items[:60], "note": note}, ensure_ascii=False)
        # projects(既定)
        r = casper_mcp.call_tool("get_projects", {}, token=WRITE_TOKEN, actor=actor)
        d = json.loads(r) if isinstance(r, str) else r
        items = d.get("items", d if isinstance(d, list) else [])
        if q:
            items = [p for p in items if q in (p.get("name") or "").lower()]
        st = str((args or {}).get("status") or "").lower()
        if st:
            items = [p for p in items if st in (p.get("status") or "").lower()]
        return json.dumps({"total": len(items), "items": items[:40]}, ensure_ascii=False)
    except Exception as e:
        return f"(calendar_lookup MCP失敗: {e})"


def build_sys():
    """毎リクエストで社内ナレッジ digest (左脳+右脳) を読み込み system prompt に注入。"""
    ctx_path = os.path.join(HERE, "casper_context.md")
    ctx = ""
    if os.path.exists(ctx_path):
        try:
            ctx = open(ctx_path, encoding="utf-8").read()
        except Exception:
            ctx = ""
    today = datetime.date.today()
    wd = "月火水木金土日"[today.weekday()]
    datehdr = (f"【今日の日付】{today.isoformat()}（{wd}曜）。日数・遅延・締切は必ずこの日付を基準に計算せよ"
               "(自分の記憶の日付を使うな)。")
    tail = ("\n【回答の作法】記号や番号(A/B/C等)1文字だけで答えるな。必ず日本語の文で具体的に答えよ。"
            "数値(遅延日数等)はデータから計算して明示せよ。")
    # KVキャッシュのプレフィックス安定化(Fable 6-3): 静的要素(ctx/BASE/PERSONA/roster)を先頭に固め、
    # 日替わりの日付は末尾へ。→ 日を跨いでも静的プレフィックスが再利用され TTFT が下がる(1文字でも
    # 動的要素を先頭に混ぜると全損する為)。
    static = ((ctx + "\n\n---\n") if ctx else "") + BASE_SYS + PERSONA_SYS + tail + team_roster()
    return static + "\n\n" + datehdr


def ollama_chat(messages, tools=None, num_predict=1536):
    # think:false 必須 (qwen3.6 等の思考モデルが長考→遅延/タイムアウトするのを防ぐ)
    # num_ctx は大きめ(tool結果が大きいとコンテキスト溢れで出力が1文字に途切れる事故あり)
    # num_predict: 既定1536。import等の大きなJSON生成は呼出側で引き上げ(途中切れ→JSON解析失敗を防ぐ)
    body = {"model": A.model, "messages": messages, "stream": False, "think": False,
            "keep_alive": -1,                              # モデルを温存(再ロードの15秒遅延を防ぐ・賢さは不変)
            "options": {"num_ctx": 12288, "num_predict": num_predict,
                        "temperature": 0.15, "top_p": 0.9}}   # tool呼出を安定化(非決定性を抑制)
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


def llm_text(system, user, num_predict=1536):
    """ツール無しの単発生成 (backend 透過)。num_predict で出力上限を調整(大きなJSON生成用)。"""
    if BACKEND == "claude_cli":
        return strip_think(claude_cli_text(system + "\n\n" + user))
    if BACKEND == "anthropic" and ANTHROPIC_KEY:
        r = anthropic_call({"model": ANTHROPIC_MODEL, "max_tokens": max(500, num_predict),
                            "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    r = ollama_chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    num_predict=num_predict)
    return strip_think(r.get("message", {}).get("content", ""))


def _report_context(anchor, rtype=""):
    """報告書の第一稿に渡す社内文脈。アンカーPJの status/期間 等を digest から拾う(先埋め)。"""
    if not anchor:
        return ""
    lines = []
    try:
        for p in json.load(open("/tmp/cal_projects.json")).get("items", []):
            nm = (p.get("name") or "")
            if nm and (anchor.lower() in nm.lower() or nm.lower() in anchor.lower()):
                lines.append(f"対象PJ: {nm} / status:{p.get('status')} / 期間:{p.get('start_date')}〜{p.get('end_date')}")
                break
    except Exception:
        pass
    return "\n".join(lines) or f"対象: {anchor}"


def _report_facts(anchor):
    """報告書の retrieve-then-render(Fable5 #1): 数値・固有名・ファイル名を LLM に創作させず、
    Calendar/資産台帳/Vimeo から決定的に引いた"確定事実表"を注入。LLMはこの表から引いて並べるだけ。"""
    if not anchor:
        return ""
    facts = []
    try:                                                   # ① 対象PJの確定情報(Calendar)
        for p in json.load(open("/tmp/cal_projects.json")).get("items", []):
            nm = p.get("name") or ""
            if nm and (anchor.lower() in nm.lower() or nm.lower() in anchor.lower()):
                facts.append(f"PJ: {nm}｜status:{p.get('status')}｜期間:{p.get('start_date')}〜{p.get('end_date')}"
                             f"｜表示:{p.get('display_status', 'online')}")
                break
    except Exception:
        pass
    if casper_manifest:                                    # ② 実在する資産ファイル(台帳・これ以外は存在しない)
        try:
            fs = casper_manifest.search(anchor, limit=30)
            if fs:
                facts.append(f"実在ファイル({len(fs)}件): " + "、".join(m["name"] for m in fs[:30]))
        except Exception:
            pass
    try:                                                   # ③ 関連Vimeo動画(実在するもの)
        import casper_vimeo
        vs = casper_vimeo.search(anchor, per_page=20)
        items = vs if isinstance(vs, list) else (vs.get("data") or vs.get("items") or [])
        if items:
            facts.append(f"関連Vimeo({len(items)}件): "
                         + "、".join(f"{v.get('name') or '?'} {v.get('link') or ''}".strip() for v in items[:10]))
    except Exception:
        pass
    if not facts:
        return ""
    return ("\n\n■確定事実(Calendar/資産台帳/Vimeoから決定的に取得)。"
            "**数値・固有名・ファイル名・リンクは下記からのみ引け。ここに無い数値/名前/ファイルを創作するな**:\n"
            + "\n".join(f"・{f}" for f in facts))


_REPORT_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _report_source_digest(sources, cap=6000):
    """報告書の添付データ源(PPT/Excel/docx/図 等)を生の事実として抽出。
    office/text は casper_extract で本文抽出、画像/図は Claude Sonnet vision で
    『報告書に引用できる客観的説明』に変換(qwen 非依存ゆえ z8a 不達でも動く)。
    返り値は draft の context に追記する digest 文字列。"""
    if not sources:
        return ""
    os.makedirs(ASSET_FILES, exist_ok=True)
    blocks = []
    for s in (sources or [])[:8]:
        fn = (s.get("filename") or "material") if isinstance(s, dict) else "material"
        b64 = (s.get("data_b64") or "") if isinstance(s, dict) else ""
        if not b64:
            continue
        safe = re.sub(r"[^\w.\-]", "_", os.path.basename(fn))[:60] or "material"
        sp = os.path.join(ASSET_FILES, safe)
        try:
            with open(sp, "wb") as f:
                f.write(_b64.b64decode(b64.split(",")[-1]))
        except Exception as e:
            blocks.append(f"【{safe}】(読込失敗: {e})")
            continue
        ext = os.path.splitext(safe)[1].lower()
        if ext in _REPORT_IMG_EXT and VISION_BACKEND == "claude_cli":
            vp = ("次の図/画像を報告書のデータ源として客観的に説明せよ。読み取れる数値・"
                  "ラベル・傾向・構成を事実のみ箇条書きで。推測や脚色は禁止。3〜8項目。")
            body = claude_cli_vision(sp, vp)
            if body.startswith("[vision error]") or body.startswith("[vision]"):
                body = (casper_extract.extract(sp) if casper_extract else "(抽出器なし)")
        else:
            body = casper_extract.extract(sp) if casper_extract else "(抽出器なし)"
        blocks.append(f"【データ源: {safe}】\n{(body or '').strip()[:2500]}")
    return ("\n\n".join(blocks))[:cap]


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
PROJECT_BANK = os.path.join(HERE, "project_question_bank.jsonl")


def _load_bank():
    """穴ドリブン(人物/資料)＋PJ種別 の両バンクを統合して返す。"""
    out = []
    for path in (QUESTION_BANK, PROJECT_BANK):
        try:
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
        except Exception:
            pass
    return out


def gen_question(asked):
    import re, random
    # ① 事前生成バンク(Opus)から未出題を即提示。
    #    人物/PJ種別バンクを交互に出して両方を早く回す＋**出題順はランダム化**(殿御下命: 順番をランダムに)。
    recent_all = " ".join(asked)
    bank = _load_bank()
    persons = [q for q in bank if q.get("type") != "project"]
    projects = [q for q in bank if q.get("type") == "project"]
    random.shuffle(persons); random.shuffle(projects)   # 毎回シャッフル→未出題からランダムに選ばれる
    inter = []
    for i in range(max(len(persons), len(projects))):
        if i < len(persons):
            inter.append(persons[i])
        if i < len(projects):
            inter.append(projects[i])
    recent6 = " ".join(asked[-6:])
    for q in inter:
        tgt = q.get("target", "")
        if q.get("question") and q["question"] not in recent_all and not (tgt and tgt in recent6):
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


VENV_PY = os.path.join(HERE, "..", "..", "..", ".venv", "bin", "python")   # fitz(PyMuPDF)入りvenv


def pdf_to_page_images(pdf_path, max_pages=5, dpi=100):
    """PDF各ページをPNG化(VENV_PY fitz経由)。画像PDF(コンテ等)の vision 読解／サムネ用。返り値=画像パスlist。"""
    if not os.path.exists(VENV_PY):
        return []
    base = re.sub(r"[^\w.\-]", "_", os.path.splitext(os.path.basename(pdf_path))[0])[:40] or "pdf"
    code = ("import fitz,sys,os\n"
            "pdf,out,base,mx,dpi=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4]),int(sys.argv[5])\n"
            "d=fitz.open(pdf)\n"
            "for i in range(min(len(d),mx)):\n"
            "    fp=os.path.join(out,f'{base}_p{i+1}.png')\n"
            "    d[i].get_pixmap(dpi=dpi).save(fp); print(fp)\n")
    try:
        r = subprocess.run([VENV_PY, "-c", code, pdf_path, ASSET_FILES, base, str(max_pages), str(dpi)],
                           capture_output=True, text=True, timeout=120)
        return [ln for ln in (r.stdout or "").splitlines() if ln.strip() and os.path.exists(ln)]
    except Exception:
        return []


def office_to_pdf(src_path):
    """pptx/docx/xlsx を PDF に変換(libreoffice headless)。表示(PDFビューア)＋vision読解＋そのままDL用。
    返り値=生成PDFパス or ''(soffice未導入/失敗)。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not os.path.exists(src_path):
        return ""
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", ASSET_FILES, src_path],
                       capture_output=True, text=True, timeout=180)
        pdf = os.path.join(ASSET_FILES, os.path.splitext(os.path.basename(src_path))[0] + ".pdf")
        return pdf if os.path.exists(pdf) else ""
    except Exception:
        return ""


def feed_ingest(filename, description, data_b64):
    """資料を保存→テキスト抽出/vision→Casper が要約＋確認質問を作る。画像PDF(コンテ等)はページ画像化してvision。
    Office(pptx/docx/xlsx)は libreoffice で PDF 化→表示/DL/vision に供する。"""
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
    is_pdf = ext == ".pdf"
    if is_image and VISION_BACKEND == "claude_cli":
        # 画像は chat backend(ollama/qwen 等)に依らず Claude Sonnet の vision で直接解析
        vp = (build_sys() + "\n\nあなたは資料を取り込んで理解する Casper。"
              f"\n\n説明(提供者記入): {description}\n\nこの画像資料を視認し、" + fmt)
        out = claude_cli_vision(path, vp)
        if out.startswith("[vision error]") or out.startswith("[vision]"):   # vision 失敗時はテキスト抽出へ退避
            text = casper_extract.extract(path) if casper_extract else "(抽出器なし)"
            out = llm_text(build_sys() + "\n\nあなたは資料を取り込んで理解する Casper。",
                           f"資料ファイル名: {safe}\n説明: {description}\n抽出内容:\n{text[:8000]}\n\n" + fmt)
        else:
            text = "(画像: Casper vision[Sonnet] で直接解析)"
    elif is_pdf and VISION_BACKEND == "claude_cli" and \
            len((casper_extract.extract(path) if casper_extract else "").strip().replace("(PDF: テキスト無し=画像PDFの可能性)", "")) < 80:
        # 画像PDF(コンテ/絵素材等)=テキストが取れぬ → 各ページを画像化して vision で視認
        imgs = pdf_to_page_images(path, max_pages=5)
        if imgs:
            descs = []
            for i, ip in enumerate(imgs):
                vp = (build_sys() + "\n\nあなたは資料を理解する Casper。"
                      f"\n\nこれはPDF資料『{safe}』の{i+1}ページ目の画像(説明:{description})。"
                      "このページの絵柄・文字・構成・意図を簡潔に述べよ(2〜3文)。")
                d1 = claude_cli_vision(ip, vp)
                descs.append(f"[{i+1}ページ] " + (d1 if not d1.startswith("[vision") else "(視認失敗)"))
            text = "\n".join(descs)
            out = llm_text(build_sys() + "\n\nあなたは資料を理解する Casper。",
                           f"PDF資料『{safe}』(全ページ画像PDF・{len(imgs)}ページ視認)。説明:{description}\n\n"
                           f"各ページ視認結果:\n{text}\n\n" + fmt)
        else:
            text = "(画像PDF・ページ画像化失敗)"
            out = llm_text(build_sys() + "\n\nあなたは資料を理解する Casper。",
                           f"資料: {safe}(画像PDFだが画像化できず)\n説明: {description}\n\n" + fmt)
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
    resp = {"saved_as": safe, "summary": summary,
            "questions": [q for q in questions if q][:5], "extract_preview": text[:600],
            "download_url": "/asset/" + safe}                 # 生ファイルを そのままDL可能に
    if ext in (".pptx", ".docx", ".xlsx"):                    # Office → PDF化して"そのまま表示"＋DL
        pdf = office_to_pdf(path)
        if pdf:
            resp["view_url"] = "/asset/" + os.path.basename(pdf)   # PDFビューアで表示
    elif ext == ".pdf":
        resp["view_url"] = "/asset/" + safe
    return resp


IMPORT_LOG = os.path.join(HERE, "import_log.jsonl")


def _import_log(rec):
    """起票(構造化/修正)の LLM 入出力をログ(原因追跡用)。"""
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), **rec}
        with open(IMPORT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _import_llm(system, user):
    """起票の構造化/修正 LLM。既定 local(qwen・PII配慮)、CASPER_IMPORT_LLM=cloud で Sonnet。
    cloud が空/エラー([claude-cli…])を返したら local(qwen)へ自動退避。"""
    if IMPORT_LLM == "cloud" and CLAUDE_BIN:
        out = strip_think(claude_cli_text(system + "\n\n" + user))
        if out and not out.lstrip().startswith("[claude-cli"):
            return out
        _import_log({"fn": "_import_llm", "note": "cloud失敗→localへ退避", "cloud_out": (out or "")[:200]})
    return llm_text(system, user, num_predict=8000)   # importは大JSON→出力上限を大きく(途中切れ防止)


def _import_json(label, system, user, ctx=""):
    """LLM 呼出→JSON抽出→ログ。失敗時は {"error":..,"raw":..} を返し raw もログに残す。"""
    try:
        out = _import_llm(system, user)
    except Exception as e:
        out = f"[exc] {e}"
    m = re.search(r"\{.*\}", out or "", re.S)
    d, ok = None, bool(m)
    if ok:
        try:
            d = json.loads(m.group(0))
        except Exception as e:
            ok, d = False, {"_parse_err": str(e)}
    _import_log({"fn": label, "llm": IMPORT_LLM, "ok": ok, "ctx": str(ctx)[:300], "out": (out or "")[:1000]})
    if not ok:
        err = "JSON抽出不可(LLMがJSONを返さず)" if d is None else f"JSON解析失敗: {d.get('_parse_err')}"
        return {"error": err, "raw": (out or "")[:500]}
    d.setdefault("project", {}); d.setdefault("shots", []); d.setdefault("tasks", [])
    return d


def project_import_structure(grid_text, hint=""):
    """Excel 由来グリッド → 新規PJ＋shot/task の構造化提案(JSON)。視認系と同じく Sonnet で精度を出す。"""
    sysp = (build_sys() + "\n\nあなたは制作管理のデータ起票アシスタント。Excel(多くは見積書)由来のグリッドから "
            "Calendar 起票用に 新規プロジェクト＋shot(seq)＋task を構造化せよ。出力は厳密な JSON のみ(説明・コードフェンス禁止)。スキーマ:\n"
            '{"project":{"name":"","description":"","start_date":"","end_date":"","_inferred":[]},'
            '"shots":[{"code":"","name":"","note":"","_inferred":[]}],'
            '"tasks":[{"shot":"","type":"","assignee":"","due":"","estimate":"","note":"","_inferred":[]}]}\n'
            "■**最初に『ヘッダー行(列見出し)』を特定し、各列が何を表すかを解釈してから値を割り当てよ。**"
            "原本の列の意味・順序・粒度を尊重し、列を取り違えるな。典型的な見積書の対応: "
            "ショット/カット番号列→shot(code)、工程列(Lighting/Comp/FX/Animation/Layout/Asset/Modeling 等の語)→task.type、"
            "人名/担当列→assignee、作業時間・人日・工数列→estimate、納期/期日/日付列→due、内容/制作指示/備考列→note。"
            "**ヘッダーが無い/結合セル/縦持ち等で曖昧なら、列の値の中身(例: 'Lighting' という語が並ぶ列=type)から列の意味を推定**せよ。"
            "**出力は読み込んだ原本に近い形(列対応・行構成が保たれた形)**にし、原本に無い行や列を勝手に作らぬこと。\n"
            "■原本に在る情報はそのまま入れよ(工数/作業時間/見積→estimate、納期→due、制作指示→note 等)。\n"
            "■**Calendar 起票に必要だが原本に無い項目**(担当者assignee・工程type・shot/seq・開始/期日 等)は、"
            "**vault の社内知識(各人の役割や専門・PJの工程慣習・納期感)から妥当に推測して補え**。"
            "推測で埋めた項目名は、その要素の **`_inferred` 配列**に必ず列挙せよ(例: \"_inferred\":[\"assignee\",\"type\"])。"
            "原本から取れた値は _inferred に入れぬ。推測は『社内知識からの推定』に限り、根拠なき断定・捏造はするな(分からねば空＋_inferred に入れず空欄)。\n"
            "■担当者(assignee)は **username(氏名)**。type は推奨値: animation/layout/comp/fx/lighting/asset/programming/design/testing/documentation/shoot/gs/report/other。\n"
            "shot が無く task 主体なら shots は空配列で良い。")
    user = f"ヒント(利用者記入): {hint}\n\n--- グリッド(Excel抽出) ---\n{grid_text[:12000]}"
    return _import_json("structure", sysp, user, "hint=" + (hint or ""))


def project_import_refine(proposal, instruction):
    """起票案(JSON)にチャット修正指示を適用し、JSON全体を返す。視認/構造化と同じく Sonnet。"""
    sysp = (build_sys() + "\n\nあなたは制作データ起票の編集アシスタント。現在の起票案(JSON)に対し、"
            "ユーザーの修正指示を適用して**JSON全体**を返す。スキーマ(project/shots/tasks。taskは"
            "shot/type/assignee/due/estimate(工数)/note、各要素に _inferred 配列=推測で埋めた項目名)。"
            "指示箇所のみ変更し他は保持。追加・削除・値変更・一括変更に対応。"
            "\n**_inferred の扱い**: ユーザーが指示である項目を明示的に確定/変更したら、その項目名を当該要素の `_inferred` から**除け**(確定済ゆえ赤表示を解く)。逆に新たに推測で補った項目は _inferred に加えよ。"
            "\n**最重要: いかなる場合も JSON のみを返せ**。指示が曖昧・不完全でも、変更不要でも、確認や説明をしたくても、"
            "散文や質問文で答えてはならぬ。その場合は現在の JSON をそのまま返し、伝えたい事があれば "
            'project に "_note":"確認事項や補足" を加えよ(JSON の外には何も書くな)。**捏造禁止**。')
    user = (f"現在の起票案:\n{json.dumps(proposal, ensure_ascii=False)}\n\n修正指示:\n{instruction}")
    return _import_json("refine", sysp, user, "instr=" + (instruction or ""))


def open_briefing(who):
    """インデックス開門時の自動上奏: Casperが状況を踏まえ"考えて"述べる挨拶＋本日タスク＋新着DM。
    挨拶は固定テンプレでなく、タスク/DMの状況に即した気の利いた一言をLLMが生成する。"""
    import json as _j
    h = datetime.datetime.now().hour
    g = "おはようございます" if h < 11 else ("こんにちは" if h < 18 else "こんばんは")
    uid = who.get("uid")
    task_n = None                                          # 事実を集め→末尾でCasperが"考えて"挨拶
    task_lines = []
    task_ctx = ""
    dm_lines = []
    unread_n = 0
    if uid and WRITE_TOKEN and casper_mcp:
        try:                                              # get_today_tasks は時々timeout/500→軽く再試行(再現性)
            items = None
            for _att in range(2):
                tt = casper_mcp.call_tool("get_today_tasks", {"actor_id": uid}, token=WRITE_TOKEN, actor=uid)
                if (tt or "").strip().startswith(("{", "[")):
                    d = _j.loads(tt)
                    items = d.get("items") if isinstance(d, dict) else (d if isinstance(d, list) else None)
                    break
            if isinstance(items, list):
                items = [it for it in items                # 完了・除外(held/omit) は残務一覧に出さぬ(API category優先)
                         if not _task_is_done(it) and (it.get("status") or "").lower() != "omit"
                         and (it.get("status_category") or "") != "held"]
                task_n = len(items)
                pmap = {}                                 # project_id→PJ名(高精細表示に必須)
                try:
                    pj = casper_mcp.call_tool("get_projects", {"actor_id": uid}, token=WRITE_TOKEN, actor=uid)
                    if (pj or "").strip().startswith(("{", "[")):
                        pd = _j.loads(pj); pit = pd.get("items") if isinstance(pd, dict) else pd
                        pmap = {str(p.get("id")): p.get("name") for p in (pit or [])}
                except Exception:
                    pass
                _PR = {"HIGH": "優先高", "MEDIUM": "優先中", "LOW": ""}
                _tctx = []
                for it in items[:12]:                     # フィールド名の揺れに頑健(name/title/task_name)
                    nm = it.get("name") or it.get("title") or it.get("task_name") or ("task#%s" % it.get("id"))
                    shot = str(it.get("shotID") or it.get("shot") or "").strip()   # カット番号(c12等)を頭に付け同名タスクを区別
                    if shot and shot.lower() not in nm.lower():
                        nm = f"{shot} {nm}"
                    stj = _task_label(it)                 # API の status_label を単一ソースに(内蔵マップは fallback・ニブ指針)
                    pjn = pmap.get(str(it.get("project_id")), "")
                    due = str(it.get("due_date") or "")[:10]
                    prj = _PR.get((it.get("priority") or "").upper(), "")
                    meta = " · ".join(x for x in [stj, prj, (f"〆{due[5:]}" if due else "")] if x)
                    pjtag = f"**[{pjn}]** " if pjn else ""
                    task_lines.append(f"- {pjtag}{nm}" + (f" · {meta}" if meta else ""))
                    _tctx.append(f"{(pjn+'/') if pjn else ''}{nm}({stj})")
                task_ctx = "、".join(_tctx)
        except Exception:
            pass
        try:
            dm = casper_mcp.call_tool("get_messages", {"actor_id": uid, "limit": 30}, token=WRITE_TOKEN, actor=uid)
            d = _j.loads(dm) if (dm or "").strip().startswith("{") else {}
            th = sorted((d.get("threads", []) or []), key=lambda t: str(t.get("updated_at") or ""), reverse=True)[:15]
            if th:
                # 未読判定を並列実行(get_messages が1件~2秒ゆえ直列だと遅い→並列で短縮)
                import concurrent.futures as _cf

                def _chk(t):
                    try:
                        r = casper_mcp.call_tool("get_messages",
                                                 {"actor_id": int(uid), "thread_id": int(t.get("thread_id"))},
                                                 token=WRITE_TOKEN, actor=uid)
                        md = _j.loads(r) if (r or "").strip().startswith("{") else {}
                        return (t, any(str(m.get("sender_id")) != str(uid) and not m.get("read_at")
                                       for m in (md.get("messages", []) or [])))
                    except Exception:
                        return (t, False)
                with _cf.ThreadPoolExecutor(max_workers=10) as _ex:
                    unread = [t for t, un in _ex.map(_chk, th) if un]
                unread.sort(key=lambda t: str(t.get("updated_at") or ""), reverse=True)
                unread_n = len(unread)
                for t in unread:
                    peers = [p for p in (t.get("participants") or []) if str(p.get("user_id")) != str(uid)]
                    nm = "、".join(str(p.get("name") or p.get("user_id")) for p in peers[:2]) or "(自分)"
                    ts = str(t.get("updated_at") or "")[5:16].replace("T", " ")
                    snip = str(t.get("last_message") or "").replace("\n", " ").translate({ord(c): " " for c in "[]()"})[:40]
                    pid = peers[0].get("user_id") if peers else ""
                    # クリックで Casper 上にそのDMを開くリンク(casper-dm:thread:peer)
                    dm_lines.append(f"🔴 {ts}　[{nm}：{snip}](casper-dm:{t.get('thread_id')}:{pid})")
        except Exception:
            pass
    # 挨拶は Casper が"考える"(定型テンプレ廃止)。タスクの中身まで踏まえ、気の利いた一言をLLM生成。
    ctx = (f"時間帯の挨拶語: {g}。本日のタスク {('%d件' % task_n) if task_n is not None else '未取得'}"
           + (f"(内訳: {task_ctx})" if task_ctx else "")
           + f"。新着未読DM: {unread_n}件。相手: 殿。")
    def _gen_greet():
        return strip_think(llm_text(
            "あなたは studio bokan の伴走AI『Casper』。殿への開門の一言を、下記の状況を踏まえ述べよ。"
            "**短く・親しみやすく**。堅苦しい飾りや古語・詩的表現は使わず、文末だけ軽く『〜にござる』で締める。"
            "本日のタスク件数(完了は除く)と、あれば着手の一押しを、**1文で**。未読DMがあれば件数だけ添える。"
            "定型挨拶・締め文句(『お申し付けを』等)は不要。改行なし・一人称。",
            ctx, num_predict=120)).strip().replace("\n", " ")
    greet = ""
    try:                                                  # 8秒cap: qwen多忙でブリーフィングをhangさせぬ→テンプレ退避
        import concurrent.futures as _cf2
        _ex2 = _cf2.ThreadPoolExecutor(max_workers=1)     # with を使わぬ=8秒超のqwenを待たずに手放す(shutdown wait=False)
        _fut = _ex2.submit(_gen_greet)
        try:
            greet = _fut.result(timeout=8)
        finally:
            _ex2.shutdown(wait=False)
    except Exception:
        greet = ""
    if not greet:                                         # テンプレ退避時もタスク件数入りで味気なくせぬ
        greet = (f"{g}、殿。本日のタスクは{task_n}件にござる。" if task_n
                 else f"{g}、殿。Casper にござる。")
    lines = [greet]
    if task_lines:                                        # 見出しは挨拶が件数を述べる為 省く(上下の空行を作らぬ)
        lines += task_lines
    if dm_lines:
        lines.append(f"💬 新着DM {unread_n}件（クリックで開く・「○○さんに返信」で代筆可）")
        lines += dm_lines
    if uid is None and not who.get("authed"):
        lines.append("ログイン頂ければ、本日のタスク・新着DMもお知らせいたす。")
    return "\n".join(lines)


# ===== Casper 整理(offboarding): 知識を結晶化してから offline する儀式 =====
SEIRI_DIR = os.path.join(HERE, "..", "vault", "60_projects")


def _seiri_done_slugs():
    """既に結晶化済み(vault/60_projects/proj_<slug>.md 有り)の slug 集合。"""
    done = set()
    try:
        for f in os.listdir(SEIRI_DIR):
            if f.startswith("proj_") and f.endswith(".md"):
                done.add(f[5:-3])
    except Exception:
        pass
    return done


def seiri_projects(who):
    """① 整理対象PJ一覧。online(これから offline 予定)＋ offline(儀式未了)。archived と『蒸留完了済』は除く。"""
    uid = who.get("uid"); out = []
    done = _seiri_done_slugs()
    if uid and WRITE_TOKEN and casper_mcp:
        try:
            pj = casper_mcp.call_tool("get_projects", {"actor_id": uid}, token=WRITE_TOKEN, actor=uid)
            if (pj or "").strip().startswith(("{", "[")):
                d = json.loads(pj); items = d.get("items") if isinstance(d, dict) else d
                for p in (items or []):
                    ds = str(p.get("display_status") or "online")
                    if ds == "archived":
                        continue
                    slug = re.sub(r"[^\w\-]", "_", (p.get("name") or "project"))[:40] or "project"
                    if slug in done:                  # 蒸留完了済はリストから外す(殿指示)
                        continue
                    out.append({"id": p.get("id"), "name": p.get("name"),
                                "description": (p.get("description") or "")[:120],
                                "status": p.get("status") or "", "display_status": ds})
                out.sort(key=lambda x: (x["display_status"] != "online", x["name"] or ""))
        except Exception:
            pass
    return out


def _vault_excerpt(txt, name, per_file=700):
    """PJ名を含む行の周辺(±2行)を抜粋。全文でなく該当箇所のみ。"""
    lines = txt.split("\n"); keep = []; seen = set()
    for i, ln in enumerate(lines):
        if name in ln:
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                if j not in seen:
                    seen.add(j); keep.append(lines[j])
            if sum(len(x) for x in keep) > per_file:
                break
    return "\n".join(keep)[:per_file]


def seiri_vault_material(project_name, cap=12000):
    """PJ名で vault を横断し、既存の議事録/asset影武者/DB書庫/人物 等から素材を自動収集。
    60_projects(自らの結晶化=citogenesis回避)と汎用短名は除外。総量を上限で抑える。返り値=(素材text, 出典数)。"""
    if not project_name or len(project_name) < 3:
        return "", 0
    import glob
    vault = os.path.join(HERE, "..", "vault")
    SKIP = {"60_projects", "_templates", "bokan_persona_versions"}
    chunks = []; total = 0
    for f in sorted(glob.glob(os.path.join(vault, "**", "*.md"), recursive=True)):
        d = os.path.basename(os.path.dirname(f))
        if d in SKIP:
            continue
        try:
            txt = open(f, encoding="utf-8").read()
        except Exception:
            continue
        if project_name not in txt:
            continue
        ex = _vault_excerpt(txt, project_name)
        if ex.strip():
            piece = f"[{d}/{os.path.basename(f)}]\n{ex}"
            chunks.append(piece); total += len(piece)
            if total > cap:
                break
    return ("\n\n".join(chunks)[:cap], len(chunks))


def seiri_ask(who, project_name, materials):
    """③ PJの vault既存素材＋投入資料を踏まえ、Casperが『まだ埋まらぬ穴』を突く質問を生成。
    質問数は投入資料の量に比例させ、複数資料でも1問あたりの濃度が下がらぬようにする(殿御下命)。"""
    vault_mat, nsrc = seiri_vault_material(project_name)
    mat = materials or ""
    n_docs = mat.count("（Casper読解）") + mat.count("Vimeo")   # 投入した資料の点数(読解ファイル＋動画)
    n_q = min(9, 3 + n_docs + len(mat) // 2500)                 # 基本3問＋資料が多いほど増やす(上限9・薄めない)
    sysp = ("あなたは studio bokan の伴走AI『Casper』。完了プロジェクトの『整理(offboarding)』の最中。"
            "下記PJについて、vault既存素材(議事録/asset/DB書庫等)と人の投入資料で"
            f"**既に分かっている事は問わず**、永続結晶化に『まだ足りない穴』だけを突く質問を**{n_q}個**挙げよ。"
            "**投入資料が複数ある時は、各資料・各観点(段取り/落とし穴/判断根拠/外部やりとり)の穴を漏らさず**、"
            "表面的な質問で数を埋めるな——それぞれ具体的で、答えれば結晶化が濃くなる質問にせよ。"
            "各質問1行・前置き不要・語尾は軽く『〜にござる』等。")
    user = (f"プロジェクト: {project_name}\n\n## vault既存素材({nsrc}件)\n{vault_mat or '(なし)'}"
            f"\n\n## 人が投入した追加資料\n{mat or '(なし)'}")
    return strip_think(llm_text(sysp, user, num_predict=max(400, n_q * 90))).strip()


def seiri_crystallize(who, project_name, materials, qa):
    """④ 知識化: vault既存素材＋人の投入資料＋質疑を統合し、段取り/落とし穴/見積/判断根拠/外部やりとり/
    引き継ぎ を vault に永続結晶化。返り値=(結晶化本文, vault出典数)。"""
    vault_mat, nsrc = seiri_vault_material(project_name)
    sysp = ("あなたは Casper。完了PJの知識を次の類似案件で使える形に結晶化せよ。**冒頭に前置き・挨拶を書くな**"
            "(いきなり見出しから始めよ)。以下の見出しを必ず立て、下記素材から分かる範囲で埋め、"
            "不明は『(未取得)』と明記(捏造禁止・推測は(推測)と明示):\n"
            "## 段取り(工程の実際)\n## 落とし穴・トラブル\n## 見積 vs 実際\n## 判断の根拠\n"
            "## 外部やりとり(観測外含む)\n## 引き継ぎ要点\n平明な日本語で。\n"
            "【右脳左脳の重複回避=重要】タスク一覧・日付・担当者・ステータス・数値等の"
            "『構造化された事実』は左脳(Calendar/Score/DB書庫)が真実源。ここに丸写しするな(重複・陳腐化の元)。"
            "この結晶化は**左脳に無い『暗黙知・判断・つまづき・教訓』だけ**を右脳知識として蒸留せよ。"
            "構造化事実に触れる要があれば1行に要約し『(詳細は Calendar/DB書庫)』と出所を指すに留めよ。")
    user = (f"プロジェクト: {project_name}\n\n## vault既存素材({nsrc}件・議事録/asset/DB書庫等)\n{vault_mat or '(なし)'}"
            f"\n\n## 人が投入した追加資料\n{materials or '(なし)'}\n\n## 質疑応答\n{qa or '(なし)'}")
    body = strip_think(llm_text(sysp, user, num_predict=1600)).strip()
    try:
        os.makedirs(SEIRI_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d")
        slug = re.sub(r"[^\w\-]", "_", project_name or "project")[:40] or "project"
        path = os.path.join(SEIRI_DIR, f"proj_{slug}.md")
        head = (f"---\ntype: project_knowledge\nproject: {project_name}\ncrystallized: {stamp}\n"
                f"vault_sources: {nsrc}\ntags: [casper, offboarding, project]\n---\n\n"
                f"# 🗂 {project_name} — 完了知識(結晶化)\n\n> vault既存素材 {nsrc}件＋人の補足を統合。\n\n")
        open(path, "w", encoding="utf-8").write(head + body + "\n")
        if casper_embed:
            try: casper_embed.reindex()
            except Exception: pass
    except Exception:
        pass
    return body, nsrc


def seiri_interview(who, project_name, knowledge, answers):
    """⑤ 有用性ゲート(Fable5硬化)。硬化点: ①各trueに"証拠引用"を要求 ②ready判定をLLM任せにせず
    コードで決定的に(必須コア=判断根拠＋落とし穴 の両方true＋残り3項中2以上)＋不足を埋める逆IV。"""
    sys_j = ("完了PJの結晶化知識が『会社に有用な状態』かを採点。5項目それぞれ、**知識中の該当記述(短い引用)を根拠に**"
             "true/falseを判定しJSONのみ返せ。trueは『事実の羅列』でなく『判断・つまづき・教訓』が出所つきで在る時のみ:\n"
             "{\"rubric\":{\"段取り\":bool,\"落とし穴\":bool,\"見積\":bool,\"判断根拠\":bool,\"外部やりとり\":bool},"
             "\"evidence\":{\"段取り\":\"引用\",\"落とし穴\":\"引用\",\"見積\":\"引用\",\"判断根拠\":\"引用\",\"外部やりとり\":\"引用\"}}。"
             "ready判定はこちらで行うので rubric と evidence のみ正確に。")
    verdict = strip_think(llm_text(sys_j,
              f"PJ: {project_name}\n\n結晶化知識:\n{knowledge}\n\n直近の回答:\n{answers or '(なし)'}", num_predict=400)).strip()
    rubric = {}; evidence = {}
    try:
        m = re.search(r"\{.*\}", verdict, re.S)
        if m:
            v = json.loads(m.group(0)); rubric = v.get("rubric") or {}; evidence = v.get("evidence") or {}
    except Exception:
        pass
    # evidence検証(Fable指摘): trueの各項は引用が結晶化知識中に実在せねば false へ降格(引用の捏造を弾く)
    _kn = re.sub(r"\s+", "", knowledge or "")
    for _k in list(rubric.keys()):
        if rubric.get(_k):
            _ev = re.sub(r"\s+", "", str(evidence.get(_k, "")))[:30]
            if not _ev or (len(_ev) >= 6 and _ev not in _kn):
                rubric[_k] = False                         # 引用なし/知識に不在=自己申告捏造→false
    # ready をコードで決定的判定(LLMの自己申告readyを信じない): 必須コア＋残り3中2
    core = bool(rubric.get("判断根拠")) and bool(rubric.get("落とし穴"))
    rest = sum(1 for k in ("段取り", "見積", "外部やりとり") if rubric.get(k))
    ready = core and rest >= 2
    missing = [k for k in ("判断根拠", "落とし穴", "段取り", "見積", "外部やりとり") if not rubric.get(k)]
    reason = ("" if ready else
              ("必須項目(判断根拠・落とし穴)が未充足" if not core else "残り3項のうち2項以上が必要"))
    questions = ""
    if not ready:
        sys_q = ("あなたは Casper。下記『不足項目』を埋める逆インタビュー質問を最大3個、簡潔に挙げよ。"
                 "各質問1行・前置き不要・語尾は軽く『〜にござる』等。")
        questions = strip_think(llm_text(sys_q,
                    f"PJ: {project_name}\n不足: {missing or '全般の精度向上'}\n\n現在の知識:\n{knowledge[:1500]}", num_predict=300)).strip()
    return {"ready": ready, "rubric": rubric, "evidence": evidence, "missing": missing,
            "questions": questions, "core_ok": core, "reason": reason}


def seiri_closed_book(who, project_name, knowledge):
    """⑥前の最終硬化ゲート(Fable5・closed-book試験): 引き継ぎ担当が必ず知るべき質問を"知識を見せず"生成し
    (循環回避)、結晶化知識だけで closed-book 回答→実質的に答えられた数で採点。正答して初めて offline(不可逆)
    の引き金を許す=自己申告rubricより一段強い決定的ゲート。返り: {pass, score, graded[{q,a,covered}]}。"""
    try:
        qs = strip_think(llm_text(
            "あなたはCasper。完了プロジェクトの引き継ぎ試験官。『次に似た案件を担当する人』が必ず知るべき実務的で"
            "具体的な質問を4問だけ挙げよ(段取りの要所/最大の落とし穴/重要な判断の理由/外部との重要なやりとり を各1問)。"
            "各質問1行・番号のみ・前置き不要。",
            f"プロジェクト名: {project_name}", num_predict=250)).strip()
        questions = [re.sub(r"^[0-9.\-・\)\s]+", "", q).strip() for q in qs.split("\n") if q.strip()][:4]
        graded = []
        for q in questions:
            ans = strip_think(llm_text(
                "下記『結晶化知識』**だけ**を根拠に質問へ答えよ。知識に該当が無ければ必ず『(知識に記載なし)』とだけ答えよ。"
                "推測・一般論で補うな。",
                f"結晶化知識:\n{knowledge[:6000]}\n\n質問: {q}", num_predict=280)).strip()
            covered = ("記載なし" not in ans) and (len(re.sub(r"\s", "", ans)) >= 15)
            graded.append({"q": q, "a": ans[:400], "covered": covered})
        ncov = sum(1 for g in graded if g["covered"])
        passed = len(graded) > 0 and ncov >= max(3, len(graded) - 1)   # 4問中3以上(1問までは許容)
        return {"pass": passed, "score": f"{ncov}/{len(graded)}", "graded": graded}
    except Exception as e:
        return {"pass": False, "score": "0/0", "graded": [], "error": str(e)}


def _seiri_raw_snapshot(uid, project_id):
    """④ 不可逆な offline(=非可逆圧縮)の前に、Calendar 生データのスナップショットを正本の隣に保存(保険)。
    Fable5硬化: 蒸留が浅かったと後日判明した時の生データ復元源。返り値=保存パス or None。"""
    try:
        pj = casper_mcp.call_tool("get_projects", {"actor_id": int(uid)}, token=WRITE_TOKEN, actor=uid)
        items = (json.loads(pj).get("items") if (pj or "").strip().startswith("{") else []) or []
        prec = next((p for p in items if str(p.get("id")) == str(project_id)), None)
        if not prec:                                       # project記録が取れねば保険は不成立→None(嘘の安心を残さぬ)
            return None
        name = prec.get("name") or f"project_{project_id}"
        slug = re.sub(r"[^\w\-]", "_", name)[:40] or "project"
        # Calendar 生データ(REST): PJの タスク / 決定 / イベント を実取得(不可逆offlineの真の復元源)
        tasks = []; decisions = []; events = []
        try:
            _get = casper_tools._get if casper_tools else None
            if _get:
                allt = []
                for off in (0, 500, 1000, 1500):
                    page = _get(f"/tasks?limit=500&offset={off}").get("items", [])
                    allt += page
                    if len(page) < 500:
                        break
                tasks = [t for t in allt if str(t.get("project_id")) == str(project_id)]
                task_ids = {str(t.get("id")) for t in tasks}
                decisions = [d for d in _get("/decisions?limit=500").get("items", [])
                             if str(d.get("project_id")) == str(project_id)]
                events = [e for e in _get("/events?limit=1000").get("items", [])
                          if str(e.get("target_id")) in task_ids][:400]
        except Exception:
            pass
        cryst = ""
        cpath = os.path.join(SEIRI_DIR, f"proj_{slug}.md")   # 結晶化本文(あれば)
        if os.path.exists(cpath):
            cryst = open(cpath, encoding="utf-8").read()
        sources = []                                        # 蒸留に使った vault素材の一覧(復元の手がかり)
        if casper_manifest:
            try:
                sources = [m["name"] for m in casper_manifest.search(name)]
            except Exception:
                pass
        snap = {"snapshot_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "project": prec, "tasks": tasks, "decisions": decisions, "events": events,
                "crystallization": cryst, "vault_sources": sources,
                "note": "offline前の生データ保険(Fable硬化)。PJのタスク/決定/イベント(Calendar生データ)＋結晶化本文＋蒸留素材一覧。"
                        "offlineは不可逆圧縮ゆえ、蒸留が浅かった時の復元源。"}
        os.makedirs(SEIRI_DIR, exist_ok=True)
        path = os.path.join(SEIRI_DIR, f"proj_{slug}_raw.json")
        json.dump(snap, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return path
    except Exception:
        return None


def seiri_offline(who, project_id):
    """⑥ Calendar で offline 化(人の承認後)。生rawスナップショット保存(不可逆対策)→update_project(display_status='offline')。"""
    uid = who.get("uid")
    if not (uid and WRITE_TOKEN and casper_mcp and project_id):
        return {"ok": False, "error": "未ログイン/PJ未指定"}
    snap = _seiri_raw_snapshot(uid, project_id)               # ④ offline前に生データ保険
    try:
        r = casper_mcp.call_tool("update_project",
            {"actor_id": int(uid), "project_id": int(project_id), "display_status": "offline"},
            token=WRITE_TOKEN, actor=uid)
        ok = (r or "").strip().startswith("{") and "error" not in (r or "")[:40]
        return {"ok": ok, "result": str(r)[:200], "raw_snapshot": bool(snap)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def vimeo_kb_save(title, description, link, vid="", uploader="", extra=None):
    """Vimeoにアップした動画の『説明』を vault(asset_shadow) へ知識化し、別会話から検索可能にする。
    説明はそのまま検索用本文として保存。RAG索引も更新。返り値=保存パス。"""
    os.makedirs(ASSET_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    base = re.sub(r"[^\w\-]", "_", (title or "vimeo"))[:40] or "vimeo"
    slug = f"vimeo_{base}_{vid}" if vid else f"vimeo_{base}_{stamp}"
    path = os.path.join(ASSET_DIR, f"asset_{slug}.md")
    L = ["---", "type: asset_shadow", "media: vimeo", f"vimeo_id: {vid}",
         f"vimeo_link: {link}", f"uploaded: {stamp}", f"uploaded_by: {uploader}",
         "tags: [casper, vimeo, video]", "---", "",
         f"# 🎬 動画 — {title or '(無題)'}", "",
         f"> Vimeo: {link} / アップ {stamp}" + (f" / by {uploader}" if uploader else ""), "",
         "## 説明 (アップ時に付与・検索対象)", (description or "(説明なし)"), ""]
    for k, v in (extra or {}).items():
        if v:
            L.append(f"- {k}: {v}")
    L += ["", "## ニュアンス・教訓 (運用で追記)", "> "]
    try:
        open(path, "w", encoding="utf-8").write("\n".join(L))
    except Exception as e:
        return f"(知識化失敗: {e})"
    try:                                  # 即・検索可能にするため索引更新
        if casper_rag:
            casper_rag.build_index(); casper_rag._CACHE = None
    except Exception:
        pass
    return path


def proposal_to_calendar_csv(prop):
    """起票案 → Calendar 公式CSV(『プロジェクト情報』＋『タスク情報』の2セクション・テンプレ準拠)。"""
    import csv, io, re as _re
    buf = io.StringIO()
    w = csv.writer(buf)
    proj = prop.get("project", {}) or {}
    w.writerow(["プロジェクト情報", "", "", "", "", "", "", "", ""])
    w.writerow(["プロジェクト名", "開始日", "終了日", "説明", "", "", "", "", ""])
    w.writerow([proj.get("name", ""), proj.get("start_date", ""), proj.get("end_date", ""),
                proj.get("description", ""), "", "", "", "", ""])
    w.writerow(["", "", "", "", "", "", "", "", ""])
    w.writerow(["タスク情報", "", "", "", "", "", "", "", ""])
    w.writerow(["タスク名", "期日", "説明", "担当者", "コスト",
                "タイプ(推奨:animation,layout,comp,fx,lighting,asset,programming,design,testing,documentation,shoot,gs,report,other)",
                "seqID", "shotID", "依存タスク(複数ある場合はカンマ区切り)"])
    for t in (prop.get("tasks") or []):
        shot = t.get("shot", "") or ""
        name = t.get("name") or (f"{shot} {t.get('type','')}".strip()) or shot or "task"
        cost = str(t.get("estimate", "") or "")
        m = _re.search(r"\d+(?:\.\d+)?", cost)
        cost = m.group(0) if m else cost                      # "3h"→"3"
        deps = t.get("dependsOn") or t.get("depends") or []
        deps = ", ".join(deps) if isinstance(deps, list) else str(deps or "")
        w.writerow([name, t.get("due", ""), t.get("note", ""), t.get("assignee", ""), cost,
                    t.get("type", ""), t.get("seqID", "") or t.get("seq_code", ""), shot, deps])
    return "﻿" + buf.getvalue()                          # BOM付(Excel/日本語対応)


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
    # 人物名・PJ名の語彙(2文字以上)を集め、本文出現で意味エッジを張る
    people_names = []
    proj_names = []
    texts = {}
    for p in sorted(files):
        base = os.path.splitext(os.path.basename(p))[0]
        if base not in nset:
            continue
        rel = os.path.relpath(p, V)
        folder = rel.split(os.sep)[0] if os.sep in rel else "root"
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        texts[base] = t
        if folder == "20_people":
            nm = (re.search(r"^name:\s*(.+)$", t, re.M) or [None, ""])[1].strip()
            for tok in re.findall(r"[A-Za-z]{3,}|[一-龠ぁ-んァ-ヶ]{2,}", nm):
                if len(tok) >= 2:
                    people_names.append((tok, base))
        # PJ名: source 行や name から PJ語を拾う(asset/meeting/digest の project: 等)
        pj = (re.search(r"project:\s*(.+)$", t, re.M) or [None, ""])[1].strip()
        if pj and len(pj) >= 2:
            proj_names.append((pj, base))

    seen_e = set()

    def add(s, tgt):
        if s != tgt and (s, tgt) not in seen_e and (tgt, s) not in seen_e:
            seen_e.add((s, tgt)); links.append({"source": s, "target": tgt})

    for base, t in texts.items():
        # ① wiki link
        for m in set(re.findall(r"\[\[([^\]|]+)", t)):
            tgt = m.strip()
            if tgt in nset:
                add(base, tgt)
        body = t.lower()
        # ② 人物名が本文に出る → 人物ノートへ
        for nm, pnode in people_names:
            if pnode != base and nm.lower() in body:
                add(base, pnode)
        # ③ PJ名が本文に出る → 同PJの他ノートへ(project: 値で束ねる)
    # ③: 同じ project 値を持つノート同士を結ぶ
    from collections import defaultdict
    bypj = defaultdict(list)
    for pj, base in proj_names:
        bypj[pj.lower()].append(base)
    for pj, members in bypj.items():
        for i in range(len(members)):
            for j in range(i + 1, min(i + 6, len(members))):  # 各PJ内で連結(過密回避に上限)
                add(members[i], members[j])
    return {"nodes": nodes, "links": links}


def org_data():
    """会社の組織構造(指示系統)をネットワークで返す。
    studio bokan → Visual Arts / Spatial Tech の2部門 → 各領域(hp6準拠) → 社員(役割で配属)。
    社員配属は名鑑(役割)からの推定。殿の訂正で ORG 定義を直すだけで反映される。"""
    # 部門→領域(hp6 準拠)
    ORG = {
        "Visual Arts Division": ["VFX & Cinematic", "Animation & Character", "Design & Commercial"],
        "Spatial Tech Division": ["Drone & Spatial", "DX Visualization", "R&D & Interactive"],
    }
    # 社員→領域(名鑑の役割から推定。uid は calendar_accounts と対応)
    MEMBERS = [
        # (表示名, 領域, uid, 役割)
        ("黒丸クロマル", "Animation & Character", 46, "animator"),
        ("Mabuchi Aogu", "Animation & Character", 38, "animator(OP/ED)"),
        ("Rui", "Animation & Character", 37, "animation"),
        ("tim", "Animation & Character", 42, "animation"),
        ("hori shouichi", "Animation & Character", 34, "animation/AI生成"),
        ("Li", "VFX & Cinematic", 43, "modeler"),
        ("Yota Miyake", "VFX & Cinematic", 35, "modeler/LtCmp"),
        ("Hnada Megumi", "VFX & Cinematic", 39, "lighting/comp"),
        ("terajima", "VFX & Cinematic", 40, "lightcomp"),
        ("yu", "VFX & Cinematic", 41, "lighting/comp"),
        ("elvis", "R&D & Interactive", None, "program(Score)"),
        ("nibu", "R&D & Interactive", 45, "開発"),
        ("Hida", "VFX & Cinematic", 44, "(役割未確定)"),
        ("Taoka", "Animation & Character", 32, "担当/制作"),
        ("kohei", "DX Visualization", 29, "ディレクター/データ管理"),
    ]
    EXTERNAL = [("新井アライ", "VFX & Cinematic", "社外Houdini FX"),
                ("PCL 越野", "Design & Commercial", "社外ディレクタ"),
                ("タイプ", "VFX & Cinematic", "社外クラウドレンダ/FX"),
                ("SOL", "VFX & Cinematic", "社外Houdini FX")]
    nodes, links = [], []
    nmap, seen_e = {}, set()

    def addnode(n):
        if n["id"] not in nmap:
            nmap[n["id"]] = n; nodes.append(n)
        return nmap[n["id"]]

    def addlink(s, t):
        if s != t and (s, t) not in seen_e and (t, s) not in seen_e:
            seen_e.add((s, t)); links.append({"source": s, "target": t})

    # ① 組織骨格: studio bokan → 部門 → 領域
    addnode({"id": "studio bokan", "label": "studio bokan", "group": "root"})
    for div, areas in ORG.items():
        addnode({"id": div, "label": div, "group": "division"}); addlink("studio bokan", div)
        for ar in areas:
            addnode({"id": ar, "label": ar, "group": "area"}); addlink(div, ar)

    # ② vault 全体グラフを取り込み(PJ/議事録/資料/学習/文化/人物のノートとエッジ)
    g = graph_data()
    for n in g["nodes"]:
        addnode(dict(n))
    for l in g["links"]:
        addlink(l["source"], l["target"])

    # ③ 人物表示名 → people ノート(base) の対応表(name: 行で照合)
    import glob
    V = os.path.join(HERE, "..", "vault")
    name2base = {}
    for p in glob.glob(os.path.join(V, "20_people", "*.md")):
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        nm = (re.search(r"^name:\s*(.+)$", t, re.M) or [None, ""])[1].strip()
        if nm:
            name2base.setdefault(nm, os.path.splitext(os.path.basename(p))[0])

    # ④ 領域 → 社員/社外 を接ぐ。vault ノートがあればそれを人物ノードとして領域に紐付け
    #    (無ければ合成ノード)。これで PJ・議事録・資料が人物の言及エッジ経由で組織にぶら下がる。
    def attach(nm, ar, group, uid=None, role=None):
        base = name2base.get(nm)
        if base and base in nmap:
            nd = nmap[base]; nd["group"] = group; nd["label"] = nm
            if uid is not None: nd["uid"] = uid
            if role: nd["role"] = role
            addlink(ar, base)
        else:
            n = {"id": nm, "label": nm, "group": group}
            if uid is not None: n["uid"] = uid
            if role: n["role"] = role
            addnode(n); addlink(ar, nm)

    for nm, ar, uid, role in MEMBERS:
        attach(nm, ar, "people", uid, role)
    for nm, ar, role in EXTERNAL:
        attach(nm, ar, "external", None, role)

    return {"nodes": nodes, "links": links}


def node_data(node_id):
    """1ノード(vaultノート)の中身を覗く: 種別・蒸留要約・つながり先を返す。"""
    import glob
    V = os.path.join(HERE, "..", "vault")
    GROUP = {"20_people": "people", "90_db_archives": "project", "80_legacy_score": "legacy",
             "10_meetings": "comms", "30_culture_rules": "company",
             "50_asset_shadows": "asset", "00_inbox": "learned"}
    target = None
    for p in glob.glob(os.path.join(V, "**", "*.md"), recursive=True):
        if os.path.splitext(os.path.basename(p))[0] == node_id:
            target = p; break
    if not target:
        return {"error": "not found", "id": node_id}
    rel = os.path.relpath(target, V)
    folder = rel.split(os.sep)[0] if os.sep in rel else "root"
    try:
        t = open(target, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return {"error": str(e), "id": node_id}
    name = (re.search(r"^name:\s*(.+)$", t, re.M) or [None, node_id])[1].strip()
    typ = (re.search(r"^type:\s*(.+)$", t, re.M) or [None, ""])[1].strip()
    body = re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)      # frontmatter除去
    body = re.sub(r"^#+\s*", "", body, flags=re.M)                       # 見出し記号除去
    body = re.sub(r"\n{2,}", "\n", body).strip()
    summary = body[:700]
    # つながり(graph_data のエッジから当該ノードの隣接を抽出)
    g = graph_data()
    lbl = {n["id"]: n["label"] for n in g["nodes"]}
    grp = {n["id"]: n["group"] for n in g["nodes"]}
    neigh = []
    for l in g["links"]:
        if l["source"] == node_id and l["target"] in lbl:
            neigh.append(l["target"])
        elif l["target"] == node_id and l["source"] in lbl:
            neigh.append(l["source"])
    neigh = [{"id": x, "label": lbl.get(x, x), "group": grp.get(x, "other")} for x in dict.fromkeys(neigh)]
    return {"id": node_id, "name": name, "type": typ, "group": GROUP.get(folder, "other"),
            "folder": folder, "summary": summary, "neighbors": neigh}


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
            # 認証要(独自鍵設定時): 未ログインなら login 画面へ
            who = identify(self)
            page = "chat.html" if (who.get("authed") or not JWT_SECRET) else "login.html"
        elif self.path in ("/login", "/login.html"):
            page = "login.html"
        elif self.path in ("/qa", "/qa.html"):
            page = "qa.html"
        elif self.path in ("/learn", "/learn.html"):
            page = "learn.html"
        elif self.path in ("/play", "/play.html"):
            page = "play.html"
        elif self.path in ("/seiri", "/seiri.html"):
            page = "seiri.html"
        elif self.path in ("/arch", "/arch.html"):     # 現状アーキテクチャ マインドマップ(inline SVG・依存無し)
            page = "arch.html"
        elif self.path in ("/archflow", "/archflow.html"):   # React Flow版(触れるノードグラフ・CDN)
            page = "archflow.html"
        elif self.path == "/api/seiri/projects":       # ① online PJ 一覧(整理対象の候補)
            try:
                self._json({"projects": seiri_projects(identify(self))})
            except Exception as e:
                self._json({"projects": [], "error": str(e)})
            return
        elif self.path in ("/peek", "/peek.html", "/graph"):
            page = "graph.html"
        elif self.path in ("/mcp", "/mcp.html"):
            page = "mcp.html"
        elif self.path in ("/import", "/import.html"):
            page = "import.html"
        elif self.path == "/api/mcp/servers":
            who = identify(self)
            if not who.get("uid"):
                self._json({"error": "ログインが必要", "servers": []}); return
            self._json({"servers": casper_user_mcp.servers(who["uid"]) if casper_user_mcp else []})
            return
        elif self.path == "/api/graph":
            try:
                self._json(graph_data())
            except Exception as e:
                self._json({"error": str(e), "nodes": [], "links": []})
            return
        elif self.path == "/api/org":
            try:
                self._json(org_data())
            except Exception as e:
                self._json({"error": str(e), "nodes": [], "links": []})
            return
        elif self.path.startswith("/api/node"):
            import urllib.parse as _up
            nid = dict(_up.parse_qsl(_up.urlparse(self.path).query)).get("id", "")
            try:
                self._json(node_data(nid))
            except Exception as e:
                self._json({"error": str(e), "id": nid})
            return
        elif self.path == "/api/users":           # ログイン選択用の社員一覧
            try:
                us = casper_tools._get("/users?limit=200").get("items", []) if casper_tools else []
                out = [{"id": u.get("id"), "name": u.get("username") or u.get("name") or str(u.get("id"))}
                       for u in us if u.get("is_active", True)]
                self._json({"users": out})
            except Exception as e:
                self._json({"error": str(e), "users": []})
            return
        elif self.path == "/api/dm/threads":
            self._json(dm_threads(identify(self)))
            return
        elif self.path.startswith("/api/dm/messages"):
            import urllib.parse as _up
            tid = dict(_up.parse_qsl(_up.urlparse(self.path).query)).get("id", "")
            self._json(dm_messages(identify(self), tid))
            return
        elif self.path == "/api/briefing":         # 開門ブリーフィング(挨拶＋本日タスク＋新着DM＋逆IV1問)
            try:
                self._json({"text": open_briefing(identify(self))})
            except Exception as e:
                self._json({"text": "", "error": str(e)})
            return
        elif self.path == "/api/corrections":      # 🙅修正リスト(欲しい内容と違う→ヒアリング結果+スレッドログ)
            try:
                items = []
                if os.path.exists(CORRECTIONS_LOG):
                    for ln in open(CORRECTIONS_LOG, encoding="utf-8"):
                        if ln.strip():
                            items.append(json.loads(ln))
                openn = [c for c in items if c.get("status") == "open"]
                self._json({"total": len(items), "open": len(openn), "items": items[-50:]})
            except Exception as e:
                self._json({"items": [], "error": str(e)})
            return
        elif self.path == "/api/whoami":
            who = identify(self)
            name = who.get("email", "")
            avatar = ""
            # アバター(Nibu 2026-07-06 是正): /api/users/{id}/avatar が無認証で image/*(未設定はSVGプレースホルダ)を
            # 決定的に配信。avatar_url フィールドに頼らず uid から直接構築する(常に表示可・<img>はBearer不要)。
            if who.get("uid"):
                avatar = CAL_BASE.rstrip("/") + f"/api/users/{who['uid']}/avatar"
            if who.get("uid") and casper_tools:
                try:
                    u = next((x for x in casper_tools._get("/users?limit=200").get("items", [])
                              if str(x.get("id")) == str(who["uid"])), None)
                    if u:
                        name = (u.get("username") or u.get("name")) or name
                except Exception:
                    pass
            if not name and who.get("uid"):
                name = _uid_to_name(who["uid"])   # live /users 不達時も堅牢 roster で本人名解決(ゲスト誤表示回避)
            self._json({"uid": who.get("uid", ""), "email": who.get("email", ""),
                        "authed": who.get("authed", False), "name": name, "avatar": avatar})
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
            import urllib.parse as _up
            raw = _up.unquote(self.path.split("/asset/")[-1].split("?")[0])  # 日本語名=percent-encode を復号
            cands = [os.path.basename(raw)]
            try:                                          # 生UTF-8がlatin-1で来た場合の復号も試す
                cands.append(os.path.basename(raw.encode("latin-1").decode("utf-8")))
            except Exception:
                pass
            # 配信元2系統: scripts/assets(従来) と vault/50_asset_shadows/files(ingest保存先)。
            ap = ""; fn = cands[0]
            for _fn in cands:
                for _root in (ASSETS_DIR, ASSET_FILES):
                    _cand = os.path.join(_root, _fn)
                    if _fn and os.path.exists(_cand) and os.path.abspath(_cand).startswith(os.path.abspath(_root)):
                        ap = _cand; fn = _fn
                        break
                if ap:
                    break
            if ap:
                ext = os.path.splitext(fn)[1].lower()
                ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
                         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(ext, "application/octet-stream")
                b = open(ap, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                if ext == ".pdf":                          # ブラウザ内 inline 表示(iframe)を許す
                    self.send_header("Content-Disposition", "inline")
                elif ext in (".pptx", ".docx", ".xlsx"):   # Office はそのままダウンロード
                    import urllib.parse as _up2
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + _up2.quote(fn))
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
        elif self.path == "/api/prewarm":          # 先回りウォーム(Fable): 入力focus時に叩き、打っている間にqwenをロード
            if BACKEND not in ("claude_cli", "anthropic") and not _qwen_is_warm():
                def _warm():
                    try:
                        _ollama_json("ping", "hi", num_predict=1)   # num_ctx 12288 のランナーを温める
                    except Exception:
                        pass
                import threading as _tw
                _tw.Thread(target=_warm, daemon=True).start()
            self._json({"ok": True}); return
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
        if self.path.startswith("/api/mcp/"):     # 個人MCP管理(本人のみ・JWT検証済uid)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (who.get("uid") and casper_user_mcp):
                self._json({"error": "ログインが必要"}); return
            uid = who["uid"]
            try:
                if self.path == "/api/mcp/add":
                    self._json(casper_user_mcp.add(uid, req.get("name", ""), req.get("url", ""),
                                                   req.get("token", ""), req.get("transport", "http")))
                elif self.path == "/api/mcp/remove":
                    self._json(casper_user_mcp.remove(uid, req.get("name", "")))
                elif self.path == "/api/mcp/toggle":
                    self._json(casper_user_mcp.set_enabled(uid, req.get("name", ""), req.get("enabled", True)))
                else:
                    self._json({"error": "unknown"})
            except Exception as e:
                self._json({"error": str(e)})
            return
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
        if self.path.startswith("/api/seiri/"):    # Casper 整理(offboarding): ③質問/④知識化/⑤逆IV/⑥offline
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                if self.path == "/api/seiri/ask":            # ③ 資料について質問
                    self._json({"questions": seiri_ask(who, req.get("project", ""), req.get("materials", ""))})
                elif self.path == "/api/seiri/crystallize":  # ④ 知識化(vault既存素材＋補足を結晶化)
                    _kb, _ns = seiri_crystallize(who, req.get("project", ""), req.get("materials", ""), req.get("qa", ""))
                    self._json({"knowledge": _kb, "vault_sources": _ns})
                elif self.path == "/api/seiri/interview":    # ⑤ 逆IV＋有用性ゲート
                    self._json(seiri_interview(who, req.get("project", ""),
                                req.get("knowledge", ""), req.get("answers", "")))
                elif self.path == "/api/seiri/closedbook":   # ⑤→⑥ 最終硬化ゲート(closed-book試験)
                    self._json(seiri_closed_book(who, req.get("project", ""), req.get("knowledge", "")))
                elif self.path == "/api/seiri/offline":      # ⑥ Calendar offline(人承認後)
                    self._json(seiri_offline(who, req.get("project_id")))
                else:
                    self._json({"error": "unknown seiri endpoint"})
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path == "/api/login":             # 認証ログイン: email+password を Calendar で検証→JWT cookie
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                if not JWT_SECRET:
                    self._json({"ok": False, "error": "認証未設定(SCORE_JWT_SECRET 要)。管理者に連絡を"}); return
                email = (req.get("email") or req.get("username") or "").strip()
                pw = req.get("password") or ""
                if not (email and pw):
                    self._json({"ok": False, "error": "メールとパスワードが要ります"}); return
                import urllib.parse as _up
                data = _up.urlencode({"username": email, "password": pw}).encode()
                vr = urllib.request.Request(CAL_BASE + "/api/auth/token", data=data,
                                            headers={"Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with urllib.request.urlopen(vr, timeout=8) as r:
                        ok = (r.status == 200)
                        auth = json.load(r) if ok else {}
                except urllib.error.HTTPError as he:
                    if he.code in (400, 401, 403):    # 認証失敗(email不一致 or パスワード違い)
                        self._json({"ok": False, "error": "ログインできません。Score と同じ『メールアドレス』と『パスワード』で入力してください（ユーザー名でなく Calendar 登録のメール）"}); return
                    self._json({"ok": False, "error": f"Calendar 認証エラー(HTTP {he.code})"}); return
                except Exception:
                    self._json({"ok": False, "error": "Calendar 認証サーバに接続できませぬ"}); return
                if not ok:
                    self._json({"ok": False, "error": "メールまたはパスワードが違います"}); return
                cal_uid, cal_name = "", email      # Calendar の現ユーザーから uid/name を確定→JWTに載せる
                try:
                    at = auth.get("access_token")
                    if at:
                        me = json.load(urllib.request.urlopen(urllib.request.Request(
                            CAL_BASE + "/api/me", headers={"Authorization": f"Bearer {at}"}), timeout=8))
                        cal_uid = str(me.get("id") or "")
                        cal_name = me.get("name") or me.get("username") or email
                except Exception:
                    pass
                import jwt as _jwt, time as _t
                token = _jwt.encode({"sub": email, "uid": cal_uid, "name": cal_name,
                                     "exp": int(_t.time()) + 86400}, JWT_SECRET, algorithm="HS256")
                uid = cal_uid or _email_to_uid(email)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", f"casper_token={token}; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax")
                body = json.dumps({"ok": True, "uid": uid, "name": email}, ensure_ascii=False).encode()
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path == "/api/logout":
            try:
                _ = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", "casper_token=; Path=/; Max-Age=0")
            b = b'{"ok":true}'
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return
        if self.path in ("/api/threads/list", "/api/threads/get", "/api/threads/save", "/api/threads/delete"):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                if self.path.endswith("list"):
                    self._json({"threads": thread_list(who), "uid": who.get("uid", "")})
                elif self.path.endswith("get"):
                    self._json(thread_get(who, req.get("id")))
                elif self.path.endswith("save"):
                    self._json(thread_save(who, req.get("id") or "", req.get("messages", []), req.get("title")))
                else:
                    self._json(thread_delete(who, req.get("id")))
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path == "/api/vimeo/create":        # tus作成(本体はブラウザが直接Vimeoへ送る=大容量対応)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                import casper_vimeo
                r = casper_vimeo.create_upload(req.get("size", 0),
                                               name=(req.get("title") or "upload.mp4"),
                                               description=(req.get("description") or ""),
                                               password=(req.get("password") or None))
                try:                                  # 説明を vault へ知識化(別会話から検索可能に)
                    who = identify(self)
                    _desc = req.get("description") or ""
                    if _desc.strip():                 # 説明が在る時のみKB化(空説明は質問でフロント側が促す)
                        _vid = (r.get("uri") or "").split("/")[-1]
                        kb = vimeo_kb_save(req.get("title") or "upload", _desc, r.get("link") or "",
                                           vid=_vid, uploader=_uid_to_name(who.get("uid")) or "")
                        r["kb_saved"] = isinstance(kb, str) and kb.endswith(".md")
                except Exception:
                    pass
                self._json({"ok": True, **r})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]})
            return
        if self.path == "/api/vimeo/upload":        # 動画を Vimeo へアップ(Casper共有トークン=利用者の権限不問)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")

            def _vlog(m):
                try:
                    with open(os.path.join(HERE, "vimeo_debug.log"), "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {m}\n")
                except Exception:
                    pass
            try:
                who = identify(self)
                import base64 as _b64m, tempfile, casper_vimeo
                raw = _b64m.b64decode((req.get("data_b64") or "").split(",")[-1])
                fn = re.sub(r"[^A-Za-z0-9_.\-]", "_", req.get("filename") or "upload.mp4")
                _vlog(f"start uid={who.get('uid')!r} authed={who.get('authed')} file={fn} bytes={len(raw)}")
                if not raw:
                    self._json({"ok": False, "error": "ファイルデータが空です。大容量だとブラウザで読めぬ場合あり(別方式が要るか確認します)"})
                    _vlog("ERR empty data (大容量の可能性)")
                    return
                tmp = os.path.join(tempfile.gettempdir(), "casper_vimeo_" + fn)
                with open(tmp, "wb") as f:
                    f.write(raw)
                r = casper_vimeo.upload(tmp, name=(req.get("title") or fn),
                                        description=req.get("description") or "",
                                        password=(req.get("password") or None))
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                _vlog(f"OK {r.get('link')}")
                try:                                  # 説明を vault へ知識化(別会話から検索可能に)
                    _desc = req.get("description") or ""
                    _vid = (r.get("uri") or "").split("/")[-1]
                    kb = vimeo_kb_save(req.get("title") or fn, _desc, r.get("link") or "",
                                       vid=_vid, uploader=_uid_to_name(who.get("uid")) or "")
                    _vlog(f"KB saved: {kb}")
                    r["kb_saved"] = isinstance(kb, str) and kb.endswith(".md")
                except Exception as _ke:
                    _vlog(f"KB ERR {_ke}")
                self._json({"ok": True, **r})
            except Exception as e:
                _vlog(f"ERR {e}")
                self._json({"ok": False, "error": str(e)[:200]})
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
                else:  # submit — 用途で分岐: daily/メモ=右脳vault即保存(権限不要) / QC・reference=Calendar(権限待ち)
                    intent = req.get("intent", "qc")
                    fn = req.get("filename", "upload")
                    note = req.get("note", "")
                    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                           "uid": uid or "", "task_id": req.get("task_id"), "intent": intent,
                           "filename": fn, "note": note}
                    with open(os.path.join(HERE, "uploader_intent_log.jsonl"), "a", encoding="utf-8") as f:
                        f.write(json.dumps({**rec, "status": "submitted"}, ensure_ascii=False) + "\n")
                    if intent in ("daily", "memo", "record"):
                        # 右脳vault に即保存(Calendar 権限不要)
                        out = feed_save(fn, note or "(daily 記録)",
                                        (req.get("recognized") or note or "")[:1500], [], fn)
                        self._json({"ok": True, "written": True, "dest": "vault",
                                    "message": "✅ 右脳(vault)に記録しました（daily/メモはCalendar権限不要）"})
                    else:
                        # QC/reference は Calendar 書込ゆえ権限待ち(確認のみ)
                        self._json({"ok": True, "written": False, "dest": "calendar",
                                    "message": "🟡 確認のみ（QC/参照のCalendar登録はニブ/エルヴィス殿の書込許可後に接続）"})
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path == "/api/project_import/preview":   # Excel→新規PJ/shot/task 構造化プレビュー
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                fn = req.get("filename", "import.xlsx"); b64 = req.get("data_b64", "")
                grid = ""
                if b64:
                    os.makedirs(ASSET_FILES, exist_ok=True)
                    safe = re.sub(r"[^\w.\-]", "_", os.path.basename(fn))[:60] or "import.xlsx"
                    sp = os.path.join(ASSET_FILES, safe)
                    with open(sp, "wb") as f:
                        f.write(_b64.b64decode(b64.split(",")[-1]))
                    grid = casper_extract.extract(sp) if casper_extract else "(抽出器なし)"
                else:
                    grid = req.get("grid", "")
                prop = project_import_structure(grid, req.get("hint", ""))
                self._json({"ok": "error" not in prop, "proposal": prop,
                            "counts": {"shots": len(prop.get("shots", []) or []),
                                       "tasks": len(prop.get("tasks", []) or [])},
                            "grid_preview": (grid or "")[:600]})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/project_import/csv":       # 起票案 → Calendar公式CSV(ダウンロード用)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                self._json({"ok": True, "csv": proposal_to_calendar_csv(req.get("proposal") or {})})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/project_import/refine":    # 起票案へチャット修正指示を適用
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                rev = project_import_refine(req.get("proposal") or {}, req.get("instruction", ""))
                self._json({"ok": "error" not in rev, "proposal": rev,
                            "counts": {"shots": len(rev.get("shots", []) or []),
                                       "tasks": len(rev.get("tasks", []) or [])}})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/project_import/commit":    # プレビュー承認→一括起票(create系MCP公開後に実発火)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            prop = req.get("proposal") or {}
            avail = set()
            if casper_mcp:
                try:
                    avail = {t["function"]["name"] for t in casper_mcp.list_tools(token=(WRITE_TOKEN or None))}
                except Exception:
                    pass
            # 想定される create/import 系 MCP ツール名(ニブ公開待ち)
            need = {"create_project", "create_shot", "create_shots", "import_shots",
                    "create_task", "bulk_create_tasks", "ai_import_parse"}
            pname = (prop.get("project") or {}).get("name", "?")
            summary = (f"新規PJ一括起票「{pname}」 — shots {len(prop.get('shots',[]) or [])}件 / "
                       f"tasks {len(prop.get('tasks',[]) or [])}件")
            if not (WRITE_TOKEN and (avail & need)):
                # create系MCP未公開 → Calendar取込用CSVを返し『今すぐ起票できる』working pathに(承認は pending 登録)
                pid = _register_pending("project_import", prop, who.get("uid"), summary)
                try:
                    csv_text = proposal_to_calendar_csv(prop)
                except Exception:
                    csv_text = ""
                self._json({"ok": True, "executed": False, "via": "csv", "csv": csv_text,
                            "pending_id": pid, "summary": summary,
                            "message": "直接起票MCP(create系)は未公開のため、Calendar取込用CSVを用意いたした。"
                                       "これを Calendar の CSV インポートに通せば今すぐ起票できまする"
                                       "(create系MCP公開後は本ボタンから直接起票に切替わる)。プレビューは確定保存済。"})
                return
            # 公開済パス: project→shots(import)→tasks の順で実行(actor=本人)
            # 2026-07-01 Fuji_test重複+欠落の真因修理: ①スキーマ外フィールド(_接頭辞メタ/client_ref)除去
            # ②actor_id/project_id を正しく付与(import_shots は project_id 必須) ③同名PJ重複ガード。
            try:
                actor = who.get("uid")
                aid = int(actor) if str(actor).isdigit() else actor

                def _clean(d):
                    """Casper内部メタ(_inferred/_note等 _接頭辞)と client_ref を除去。
                    Calendar create系はスキーマ外フィールドで起票失敗/フィールド欠落を招くため。"""
                    return {k: v for k, v in (d or {}).items()
                            if not str(k).startswith("_") and k != "client_ref"}

                def _extract_id(r):
                    try:
                        j = json.loads(r) if isinstance(r, str) else r
                        if isinstance(j, dict):
                            return j.get("id") or (j.get("project") or {}).get("id")
                    except Exception:
                        pass
                    return None

                # 重複ガード: 同名PJ(非archived)が既存なら起票せず警告(Calendar側に冪等keyが無く二重起票が起きるため)
                try:
                    ex = json.loads(casper_mcp.call_tool("get_projects", {"actor_id": aid},
                                                         token=WRITE_TOKEN, actor=actor))
                    dups = [p for p in (ex.get("projects") or ex.get("items") or [])
                            if str(p.get("name", "")).strip() == str(pname).strip()
                            and str(p.get("display_status", "online")) != "archived"]
                except Exception:
                    dups = []
                if dups:
                    self._json({"ok": False, "executed": False, "duplicate": True,
                                "existing": [{"id": p.get("id"), "name": p.get("name")} for p in dups],
                                "message": f"同名PJ「{pname}」が既に存在いたす(id {', '.join(str(p.get('id')) for p in dups)})。"
                                           "二重起票を避けるため中止。別名にするか、既存PJへの追加起票を御指示くだされ。"})
                    return

                results = []
                pr = casper_mcp.call_tool("create_project",
                                          _clean({"actor_id": aid, **(prop.get("project") or {})}),
                                          token=WRITE_TOKEN, actor=actor)
                results.append(pr)
                new_pid = _extract_id(pr)
                if prop.get("shots") and new_pid and "import_shots" in avail:
                    results.append(casper_mcp.call_tool("import_shots",
                        {"actor_id": aid, "project_id": new_pid,
                         "shots": [_clean(s) for s in prop["shots"]]},
                        token=WRITE_TOKEN, actor=actor))
                if prop.get("tasks") and "bulk_create_tasks" in avail:
                    results.append(casper_mcp.call_tool("bulk_create_tasks",
                        {"actor_id": aid, "tasks": [_clean(t) for t in prop["tasks"]]},
                        token=WRITE_TOKEN, actor=actor))
                self._json({"ok": True, "executed": True, "summary": summary,
                            "project_id": new_pid, "results": [str(r)[:500] for r in results]})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/report/types":       # 報告書ビルダー: 種別一覧＋アンカーPJ候補
            projs = []
            try:
                projs = [p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", []) if p.get("name")]
            except Exception:
                pass
            self._json({"ok": bool(report_lib), "types": (report_lib.types_list() if report_lib else []), "projects": projs})
            return
        if self.path == "/api/report/structure":   # 整理術判断→構成＋質問(第一稿の主戦場)
            n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
            try:
                rtype = req.get("rtype", ""); goal = req.get("goal", ""); anchor = req.get("anchor", "")
                fw = req.get("framework", "")
                if fw in report_lib.FRAMEWORKS:    # ユーザーが整理術を切替えた場合
                    sug = {"recommended": fw, "rationale": "", "alternatives": [k for k in report_lib.FRAMEWORKS if k != fw]}
                else:                              # Casper が最適な整理術を判断
                    sug = report_lib.suggest_framework(goal, anchor, rtype, llm=llm_text); fw = sug["recommended"]
                plan = report_lib.framework_plan(fw)
                self._json({"ok": True, "framework": fw, "framework_label": plan["framework_label"],
                            "frameworks": report_lib.frameworks_list(),
                            "rationale": sug.get("rationale", ""), "alternatives": sug.get("alternatives", []),
                            "pages": plan["pages"], "questions": plan["questions"],
                            "data_sources": report_lib.REPORT_TYPES.get(rtype, {}).get("data_sources", []),
                            "label": report_lib.REPORT_TYPES.get(rtype, {}).get("label", "")})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/report/draft":       # 第一稿生成→Aurora保存
            n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                rtype = req.get("rtype", ""); structure = req.get("structure") or {}
                answers = req.get("answers") or {}; anchor = req.get("anchor", "")
                ctx = _report_context(anchor, rtype)
                ctx += _report_facts(anchor)         # retrieve-then-render: 確定事実表を注入(数値/固有名の創作を防ぐ)
                src_digest = _report_source_digest(req.get("sources") or [])
                if src_digest:                       # 添付資料(PPT/Excel/図)を生の事実として参考データに合流
                    ctx = (ctx + "\n\n■添付資料から読み取れる事実:\n" + src_digest).strip()
                dr = report_lib.generate_draft(rtype, structure, answers, context=ctx, llm=llm_text)
                lbl = report_lib.REPORT_TYPES.get(rtype, {}).get("label", "報告書")
                title = req.get("title") or (f"{anchor} {lbl}".strip())
                _uid = who.get("uid")   # 本人名優先(spoof不可)・未ログイン時のみ author 受理(_uid_to_name(None)='?'を避ける)
                uname = (_uid_to_name(_uid) if _uid else None) or req.get("author") or "casper"
                meta = f"著者: {uname} ／ 種別: {lbl} ／ 対象: {anchor or '—'}"
                html = report_lib.render_blocks_html(title, meta, structure, dr["sections"])
                html = _validate_report_html(html)          # 稿の出口検問: 捏造/assetリンクを除去(Fable5 #4)
                res = casper_aurora.create(title, html, author_id=uname, project=(anchor or "報告書"),
                                           work="報告書", tags=["report", rtype])
                rd = json.loads(res) if isinstance(res, str) else (res or {})
                doc_id = rd.get("id")
                url = (casper_aurora.doc_base().rstrip("/") + "/doc/" + doc_id + "/raw") if doc_id else ""
                self._json({"ok": bool(doc_id), "doc_id": doc_id, "slug": rd.get("slug"), "url": url,
                            "html": html, "draft_ok": dr.get("ok"), "title": title})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/report/save":        # 編集後の全文HTMLを新版保存(クリック編集の保存)
            n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                doc_id = req.get("doc_id", ""); html = req.get("html", "")
                if not (doc_id and html):
                    self._json({"ok": False, "error": "doc_id/html 必須"}); return
                html = _validate_report_html(html)           # 編集保存も出口検問(Fable5 #4)
                uname = _uid_to_name(who.get("uid")) or "casper"
                res = casper_aurora.append_version(doc_id, html, author_id=uname)
                rd = json.loads(res) if isinstance(res, str) else (res or {})
                self._json({"ok": bool(rd), "version": rd.get("version")})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/dropbox/transfer":       # ファイル → Dropbox転送(パスワード付き共有リンク)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if not (casper_dropbox and casper_dropbox.available()):
                self._json({"ok": False, "error": "Dropbox 転送は未設定にござる"}); return
            try:
                b64 = req.get("data_b64", "")
                if b64.startswith("data:") and "," in b64:
                    b64 = b64.split(",", 1)[1]             # data:URL の接頭辞を除去(Office形式のMIMEは77字超ゆえ位置制限せず)
                b64 += "=" * (-len(b64) % 4)              # base64パディング補正(Incorrect padding対策)
                data = _b64.b64decode(b64)
                r = casper_dropbox.transfer(data, req.get("filename", "file"),
                                            password=(req.get("password") or None))
                self._json(r)
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/dropbox/batch_add":      # 複数まとめ: フォルダへ1ファイルずつアップ(リンクはまだ作らぬ)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if not (casper_dropbox and casper_dropbox.available()):
                self._json({"ok": False, "error": "Dropbox 転送は未設定にござる"}); return
            try:
                b64 = req.get("data_b64", "")
                if b64.startswith("data:") and "," in b64:
                    b64 = b64.split(",", 1)[1]             # data:URL接頭辞除去(Office形式のMIMEは77字超ゆえ位置制限せず)
                b64 += "=" * (-len(b64) % 4)              # base64パディング補正
                data = _b64.b64decode(b64)
                self._json(casper_dropbox.upload_into(req.get("folder", "batch"), data, req.get("filename", "file")))
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/dropbox/batch_share":    # 複数まとめ: フォルダに1つのパスワード付きリンクを作る
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if not (casper_dropbox and casper_dropbox.available()):
                self._json({"ok": False, "error": "Dropbox 転送は未設定にござる"}); return
            try:
                self._json(casper_dropbox.share_folder(req.get("folder", "batch"),
                                                       password=(req.get("password") or None)))
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path.startswith("/api/doc/") and casper_doc:   # 節構造ドキュメント(資料作り・Fable UI設計)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                if self.path == "/api/doc/create":              # 節配列 or 報告書構成 から文書を作る
                    d = casper_doc.create(req.get("title", "無題"), req.get("sections") or [],
                                          project=req.get("project", ""), author=_uid_to_name(who.get("uid")) or "casper")
                    self._json({"ok": True, "doc": d})
                elif self.path == "/api/doc/get":
                    self._json({"ok": True, "doc": casper_doc.get(req.get("doc_id", ""))})
                elif self.path == "/api/doc/section/save":      # 節本文の編集保存(版退避＋教師信号 _prev)
                    d = casper_doc.save_section(req.get("doc_id", ""), req.get("section_id", ""),
                                                req.get("body", ""), orig=req.get("orig"))
                    self._json({"ok": bool(d), "doc": d})
                elif self.path == "/api/doc/section/regen":     # 節単位の再生成(弱いqwen対策=小さく回す)
                    doc = casper_doc.get(req.get("doc_id", ""))
                    sec = casper_doc.section(doc, req.get("section_id", "")) if doc else None
                    if not sec:
                        self._json({"ok": False, "error": "節が見つかりませぬ"}); return
                    instr = req.get("instruction", "") or "より分かりやすく簡潔に"
                    sysp = ("あなたはCasper。報告書の『1つの節だけ』を指示に沿って書き直せ。"
                            "**見出しや他の節は書くな・その節の本文markdownのみ**返せ。前置き不要。")
                    user = (f"文書: {doc.get('title','')}\n節の見出し: {sec.get('heading','')}\n"
                            f"現在の本文:\n{sec.get('body','')}\n\n書き直しの指示: {instr}")
                    nb = strip_think(llm_text(sysp, user, num_predict=max(400, len(sec.get('body','')) // 2)))
                    nb = _strip_tool_leak(nb).strip()
                    d = casper_doc.save_section(req.get("doc_id", ""), req.get("section_id", ""),
                                                nb, orig=sec.get("body"), instruction=instr)
                    self._json({"ok": bool(d), "doc": d, "regenerated": req.get("section_id")})
                elif self.path == "/api/doc/add_section":
                    d = casper_doc.add_section(req.get("doc_id", ""), req.get("heading", ""),
                                               req.get("body", ""), after=req.get("after"))
                    self._json({"ok": bool(d), "doc": d})
                elif self.path == "/api/doc/versions":
                    self._json({"ok": True, "versions": casper_doc.versions(req.get("doc_id", ""))})
                elif self.path == "/api/doc/restore":           # 版へ戻す(戻す操作も可逆)
                    d = casper_doc.restore(req.get("doc_id", ""), req.get("v"))
                    self._json({"ok": bool(d), "doc": d})
                elif self.path == "/api/doc/publish":           # 完成→既存の承認カード(Aurora起票)へ流す
                    doc = casper_doc.get(req.get("doc_id", ""))
                    if not doc:
                        self._json({"ok": False, "error": "文書が見つかりませぬ"}); return
                    md = casper_doc.to_markdown(doc)
                    args = {"title": doc.get("title", "無題"), "body": md, "tags": req.get("tags") or []}
                    summary = _action_summary("aurora_create", args)
                    pid = _register_pending("aurora_create", args, who.get("uid"), summary, thread=req.get("thread"))
                    self._json({"ok": True, "confirm": {"id": pid, "tool": "aurora_create", "args": args, "summary": summary}})
                else:
                    self._json({"ok": False, "error": "unknown doc endpoint"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/confirm":            # Stage2: 承認待ち副作用操作の実行/却下
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            pid = req.get("id", ""); approve = bool(req.get("approve"))
            # 真実源=outbox(永続)。in-memoryは高速キャッシュ。再起動(reload多発)でもoutboxから拾える。
            pend = PENDING_ACTIONS.get(pid)
            if pend is None and casper_outbox:
                orec = casper_outbox.get(pid)
                if orec and orec.get("state") == "proposed":
                    pend = {"tool": orec["tool"], "args": orec["args"], "uid": orec["uid"],
                            "summary": orec.get("summary", ""), "thread": orec.get("thread")}
            if not pend:
                self._json({"ok": False, "error": "提案が見つかりませぬ(期限切れか既処理)"}); return
            if pend["uid"] and who.get("uid") and str(who["uid"]) != pend["uid"]:
                self._json({"ok": False, "error": "本人のみ承認できまする"}); return
            if not approve:
                if casper_outbox:
                    casper_outbox.reject(pid, uid=who.get("uid"))
                PENDING_ACTIONS.pop(pid, None)
                self._json({"ok": True, "executed": False, "message": "却下しました"}); return
            if not WRITE_TOKEN:                             # 実行不可なら approved にせず proposed のまま残す(後で再試行可)
                self._json({"ok": False, "error": "write token 未設定のため実行できませぬ(ニブ殿の token 更新待ち)"}); return
            body_edit = req.get("body") if req.get("body") is not None else None
            if casper_outbox:                              # 冪等ガード: proposed→approved を原子遷移。二重承認/多重クリックは弾く(二度送らぬ)
                appr = casper_outbox.approve(pid, uid=who.get("uid"), body_edit=body_edit)
                if not appr:
                    self._json({"ok": False, "error": "既に処理済みにござる(二重送信を防ぎました)"}); return
                if isinstance(appr.get("args"), dict):
                    pend["args"] = appr["args"]            # outboxの確定args(本文編集反映済)を採用
                casper_outbox.mark_executing(pid)
            elif body_edit is not None and isinstance(pend.get("args"), dict):
                if body_edit != pend["args"].get("body"):    # 教師信号: LLM原案を body_orig へ退避(outbox不在時の漏れ穴を塞ぐ・Fable指摘)
                    pend["body_orig"] = pend["args"].get("body")
                pend["args"]["body"] = body_edit
            PENDING_ACTIONS.pop(pid, None)                 # キャッシュから除去(以後の真実源はoutboxのstate)
            pend["summary"] = _action_summary(pend["tool"], pend["args"])
            try:
                actor = who.get("uid") or pend["uid"]
                if pend["tool"] in ("aurora_create", "aurora_append"):   # Aurora書込は casper_aurora 経由(write token・別endpoint)
                    a = pend["args"] or {}
                    uname = _uid_to_name(actor)            # 投稿者は username(casper でなく本人名)
                    html = casper_aurora.make_note(a.get("title", ""), a.get("body", ""),
                                                   author=uname, tags=a.get("tags"))
                    if pend["tool"] == "aurora_create":
                        result = casper_aurora.create(a.get("title", ""), html, author_id=uname, tags=a.get("tags"))
                    else:                                  # aurora_append = 既存ノートの修正(新版)
                        result = casper_aurora.append_version(a.get("doc_id", ""), html, author_id=uname)
                    ok = bool(result) and not str(result).startswith("(")
                    if ok and pend.get("thread"):          # 1スレ1資料: 作成/更新した資料をスレッドに束ねる
                        try:
                            rd = json.loads(result) if isinstance(result, str) else (result or {})
                            did = rd.get("id") or a.get("doc_id")
                            if did:
                                _AURORA_CUR[pend["thread"]] = {"doc_id": did, "title": pend.get("title") or _AURORA_CUR.get(pend["thread"], {}).get("title", "")}
                        except Exception:
                            pass
                else:
                    result = (casper_mcp.call_tool(pend["tool"], pend["args"], token=WRITE_TOKEN, actor=actor)
                              if casper_mcp else "(MCP無効)")
                    ok = not str(result).startswith("(MCP")
                    # DMが成果物(動画/資料)の依頼なら OPEN LOOP を自動登録。抽出は正規表現でなく LLM構造化出力
                    # (Fable指摘: DM本文はLLMが書くのだから対象語もLLMに構造で吐かせる方が頑健)。安価な事前ゲート付き。
                    if ok and pend["tool"] == "send_message" and casper_openloop:
                        try:
                            body = str((pend.get("args") or {}).get("body") or "")
                            if re.search(r"(アップ|提出|作成|お願い|依頼|上げ|共有)", body) and \
                               re.search(r"(動画|ムービー|映像|資料|画像|ファイル|[Vv]imeo)", body):
                                rec = strip_think(llm_text(
                                    "次のDMは相手に『成果物(動画/資料)の作成・アップ・提出』を依頼しているか判定し JSONのみ返せ:"
                                    " {\"track\":true|false, \"kind\":\"vimeo\"|\"asset\"|\"\", \"keyword\":\"追跡に使う対象名(PJ名/作品名・簡潔に)\"}。"
                                    "動画のVimeoアップ依頼なら kind=vimeo、資料ファイルなら asset。単なる連絡・質問なら track=false。",
                                    body, num_predict=120))
                                mm = re.search(r"\{.*\}", rec, re.S)
                                d = json.loads(mm.group(0)) if mm else {}
                                if d.get("track") and d.get("keyword") and d.get("kind") in ("vimeo", "asset"):
                                    to = (pend.get("args") or {}).get("to_user_id")
                                    casper_openloop.add(
                                        who=str(actor),
                                        title=f"{_uid_to_name(to)}に「{d['keyword']}」の{'Vimeoアップ' if d['kind']=='vimeo' else '資料提出'}を依頼",
                                        probe={"type": d["kind"], "q": d["keyword"]}, assignee=_uid_to_name(to))
                        except Exception:
                            pass
                if casper_outbox:                          # 状態を確定(=『送信済』の唯一の真実源)
                    (casper_outbox.mark_sent(pid, str(result)[:500]) if ok
                     else casper_outbox.mark_failed(pid, str(result)[:500]))
                self._json({"ok": ok, "executed": True, "tool": pend["tool"], "result": str(result)[:2000]})
            except Exception as e:
                if casper_outbox:
                    casper_outbox.mark_failed(pid, str(e)[:500])   # executing→failed(再試行可・状態を残す)
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/feedback":
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                rec = {
                    "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                    "question": str(req.get("question", ""))[:500],
                    "format": str(req.get("format", "")),      # text/table/mermaid/canvas/...
                    "verdict": str(req.get("verdict", "")),    # good/want_diagram/want_text/wrong_format/wrong_content
                    "answer_excerpt": str(req.get("answer_excerpt", ""))[:300],
                }
                os.makedirs(os.path.dirname(FEEDBACK_LOG), exist_ok=True)
                with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                # 🙅『欲しい内容と違う』+ ヒアリング説明 → スレッドログごと修正リスト化(自己改善の教師信号・殿御下命2026-07-08)
                if req.get("verdict") == "wrong_content" and str(req.get("detail", "")).strip():
                    thread = req.get("thread") or []
                    thread = [{"role": str(m.get("role", ""))[:12], "content": str(m.get("content", ""))[:1200]}
                              for m in thread if isinstance(m, dict)][-8:]
                    corr = {
                        "id": uuid.uuid4().hex[:10],
                        "ts": rec["ts"],
                        "question": rec["question"],
                        "answer_excerpt": str(req.get("answer_excerpt", ""))[:600],
                        "what_was_wrong": str(req.get("detail", ""))[:1200],   # 何が違ったか(ヒアリング結果)
                        "thread_log": thread,                                  # そのスレッドの直近ログ
                        "status": "open",                                      # open→(人が審査)→triaged/fixed
                    }
                    with open(CORRECTIONS_LOG, "a", encoding="utf-8") as f:
                        f.write(json.dumps(corr, ensure_ascii=False) + "\n")
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)})
            return
        if self.path != "/api/chat":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", [])
        thr = str(req.get("thread", "") or "")          # 1スレ1資料 紐付け用
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
            # 応答パスは高速な字面検索(casper_rag)を使う。意味検索(casper_embed 412MB)は load26s/検索8sで
            # 応答をhangさせる為 hot pathから外す(存在確認は台帳が担う)。索引の高速化(binary)は別課題。
            hits = casper_rag.search(last_user, k=8) if (casper_rag and last_user) else []
            src, fulltext = (casper_rag.top_source(last_user) if (casper_rag and last_user) else (None, None))
            fullnote = ("\n\n## 該当資料の全文 (" + src + ")\n" + fulltext[:7000]) if fulltext else ""
            cal = user_profile_digest(who)            # ログイン中ユーザーの蓄積理解を注入
            cal += activity_digest(who)               # 動向層＝経験層: 直近の筋/未決/先読みを掟つき注入
            cal += verify_digest(who, last_user)      # 検証ゲート: 状態質問は応答前にlive裏取り強制＋出所タグ義務
            cal += projects_digest(last_user)         # 進行中PJ一覧: Calendarから注入(ツール呼び失敗を回避)
            cal += active_tasks_digest(last_user)     # 進行中タスク一覧: 全PJのwipを注入(本日締切に狭めぬ)
            cal += existence_digest(who, last_user)   # 存在ゲート: 資料有無の問いはRAG検索強制＋"無い"の断定禁止
            cal += open_loop_digest(who)              # 未了の約束(OPEN LOOP)を⚙レコードから注入
            cal += traits_digest(who, last_user)      # 人物の癖(構造化trait)を注入=裏取りの手がかり
            cal += calendar_digest(last_user)         # Calendar 左脳を必要時に先読み注入
            cal += meeting_digest(last_user)          # 会議/議事録クエリは最新会議も注入
            cal += portfolio_digest(last_user)        # 実績クエリは自社Vimeo実績を注入
            cal += cross_digest(last_user)            # 横断クエリは全PJ遅延サマリを注入
            cal += shot_assignee_digest(last_user)    # カット×担当(shot×task結合)も注入
            diag_hint = DIAG_HINT
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
            ans = _validate_assets(ans)                           # 出口検問: 捏造/asset URLを除去
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
            ans = _validate_assets(ans)                           # 出口検問
            for i in range(0, len(ans), 36):
                self._emit(ans[i:i + 36])
            try:
                self.wfile.write(b'{"done":true}\n')
            except Exception:
                pass
            return

        # --- Ollama(local) backend (qwen3.6:27b 等・自律 tool-calling) ---
        ll_user = next((m.get("content", "") for m in reversed(msgs)
                        if m.get("role") == "user"), "")
        _tid = uuid.uuid4().hex[:12]                 # このリクエストのtrace_id(outbox↔trace 結線・教師信号の文脈復元用・A実装)
        if not _qwen_is_warm():                     # 縮退(Fable): 冷間なら"少々お待ちを"を即返す(本回答は同一ストリームで続く)
            try:
                self.wfile.write((json.dumps({"status": "🔥 Casper を起こしております、少々お待ちを…（初回は十数秒かかり申す）"},
                                             ensure_ascii=False) + "\n").encode())
                self.wfile.flush()
            except Exception:
                pass
        # 出力指針(表/mermaid/Canvas/動画)を system に追記。
        # 右脳(vault)はtoolで探させると空振りしやすい→ショットリスト/資料系は top_source を先読み注入。
        sysadd = DIAG_HINT + user_profile_digest(who)   # ログイン中ユーザーの蓄積理解を注入
        sysadd += activity_digest(who)                   # 動向層＝経験層: 直近の筋/未決/先読みを掟つき注入
        sysadd += verify_digest(who, ll_user)            # 検証ゲート: 状態質問は応答前にlive裏取り強制＋出所タグ義務
        sysadd += projects_digest(ll_user)               # 進行中PJ一覧: Calendarから注入(ツール呼び失敗を回避)
        sysadd += active_tasks_digest(ll_user)           # 進行中タスク一覧: 全PJのwipを注入(本日締切に狭めぬ)
        sysadd += existence_digest(who, ll_user)         # 存在ゲート: 資料有無の問いはRAG検索強制＋"無い"の断定禁止
        sysadd += open_loop_digest(who)                  # 未了の約束(OPEN LOOP)を⚙レコードから注入
        sysadd += traits_digest(who, ll_user)            # 人物の癖(構造化trait)を注入=裏取りの手がかり
        sysadd += meeting_digest(ll_user)               # 会議/議事録クエリは最新会議を注入(tool空振り対策)
        sysadd += shot_assignee_digest(ll_user)         # カット×担当も注入
        sysadd += image_asset_digest(ll_user)           # 画像/カット系は実在ファイルのURLを機械注入(捏造防止)
        sysadd += portfolio_digest(ll_user)             # 実績クエリは自社Vimeo実績を注入
        sysadd += cross_digest(ll_user)                 # 横断クエリは全PJ遅延サマリを注入
        try:
            hits = (casper_rag.search(ll_user, k=6) if (casper_rag and ll_user)
                    else (casper_rag.search(ll_user, k=6) if (casper_rag and ll_user) else []))
            if hits:
                sysadd += "\n\n## 関連社内記録(右脳vault・意味/字面検索):\n" + "\n".join(hits)
            src, fulltext = (casper_rag.top_source(ll_user) if (casper_rag and ll_user) else (None, None))
            if fulltext:
                sysadd += ("\n\n## 該当資料(右脳vault・" + src + ") — サムネ等の画像URL `![](/asset/..)` は"
                           "ここから一字一句コピーせよ。これに無い画像URLは創作するな:\n" + fulltext[:7000])
        except Exception:
            pass
        # 会話履歴が長いと入力が num_ctx(12288) を埋め、出力(表等)が途中で切れる=文脈オーバーフロー。
        # 直近の会話だけに絞り、生成の余地を残す(char budget・最新から遡って詰める・systemは常に残す)。
        _sys_msgs = [m for m in msgs if m.get("role") == "system"]
        _conv = [m for m in msgs if m.get("role") != "system"]
        _HIST_BUDGET = 4500                                # 履歴の総文字数上限(sysadd/RAG/出力の余地を確保)
        _kept, _used = [], 0
        for m in reversed(_conv):                          # 最新から遡り、予算内で採用(直近ほど優先)
            c = len(str(m.get("content", "")))
            if _kept and _used + c > _HIST_BUDGET:
                break
            _used += c
            _kept.append(m)
        _conv = list(reversed(_kept))
        working = []
        for m in _sys_msgs:
            working.append({"role": "system", "content": m["content"] + sysadd})
        working += _conv
        if not any(m.get("role") == "system" for m in working):
            working = [{"role": "system", "content": build_sys() + fu + sysadd}] + working
        tools = list(casper_tools.TOOLS) if casper_tools else []
        mcp_names = set()
        if casper_mcp:                              # MCP公開ツールを合流(同名は MCP 優先)
            try:
                mt = casper_mcp.list_tools(token=(WRITE_TOKEN or None))   # write token で全6本を露出
                if not mt and WRITE_TOKEN:                                # write token 不調なら read 権限で退避(読取2本を死守)
                    mt = casper_mcp.list_tools()
                mcp_names = {t["function"]["name"] for t in mt}
                tools = mt + [t for t in tools if t["function"]["name"] not in mcp_names]
            except Exception:
                pass
        user_mcp_names = set()                      # ログイン中ユーザー個人の MCP ツール(本人権限)
        if casper_user_mcp and who.get("uid"):
            try:
                ut = casper_user_mcp.tools(who["uid"])
                user_mcp_names = {t["function"]["name"] for t in ut}
                tools = ut + [t for t in tools if t["function"]["name"] not in user_mcp_names]
            except Exception:
                pass
        tools = (tools or []) + [    # Vimeo ライブ検索 & パスワード設定(casper_vimeo)
            {"type": "function", "function": {"name": "vimeo_search",
             "description": "studio bokan の Vimeo ライブラリ(全動画・公開/非公開問わず)を名前で検索し、一致動画(タイトル・リンク・id)を返す。動画を探す/見せたい時に使う。",
             "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "検索語(動画名やPJ名の一部)"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "vimeo_set_password",
             "description": "指定 Vimeo 動画(video_id)にパスワード付き公開を設定し、共有リンクとパスワードを返す。video_id は vimeo_search の結果の id を使う。",
             "parameters": {"type": "object", "properties": {"video_id": {"type": "string", "description": "動画のid(数値)またはURL"}, "password": {"type": "string", "description": "設定するパスワード"}}, "required": ["video_id", "password"]}}},
        ]
        if casper_aurora and casper_aurora.configured():   # Aurora 共有ノート図書館(司書)
            tools = tools + [
                {"type": "function", "function": {"name": "aurora_search",
                 "description": "Aurora(全社共有ノート図書館: 議事録/レポート/分析等)を全文検索し、関連ノート(タイトル/id/抜粋/**閲覧url**)を返す。ユーザーが資料を探している時に使い、見つけたら**そのノートの url をクリックできるリンク [タイトル](url) としてユーザーに渡す**。",
                 "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "検索語(日本語可)"}}, "required": ["query"]}}},
                {"type": "function", "function": {"name": "aurora_get",
                 "description": "Aurora のノート1件を id で取得し本文を返す。aurora_search の結果 id を使う。",
                 "parameters": {"type": "object", "properties": {"doc_id": {"type": "string", "description": "ノートのid"}}, "required": ["doc_id"]}}},
                {"type": "function", "function": {"name": "aurora_create",
                 "description": "Aurora に新規ノートを作成する(会社の知識を文書化して共有書架へ)。本文は markdown 可。**書込=ユーザー承認が要る**。",
                 "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string", "description": "ノート本文(markdown可)"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "body"]}}},
                {"type": "function", "function": {"name": "aurora_append",
                 "description": "Aurora の既存ノートを修正(新しい版を追加・版履歴は backend が保持)。doc_id(aurora_search/get で得た id)と修正後の本文(markdown可・全文)を渡す。作成→修正→完成の『修正』はこれ。**書込=ユーザー承認が要る**。",
                 "parameters": {"type": "object", "properties": {"doc_id": {"type": "string"}, "body": {"type": "string", "description": "修正後の本文 全文(markdown可)"}}, "required": ["doc_id", "body"]}}},
            ]
        tools = tools or None
        final = ""
        pending_actions = []                        # Stage2: 副作用操作の承認待ちキュー
        MAXIT = 6
        _t0 = time.time()                           # トレース: 生成時間計測の起点
        # P2(Fable propose→execute→render): DM等のアクションは制約デコード(format=json)で型付き提案を作り
        # 承認カードを機構生成→自由文tool-callを迂回。確定時は生成ループをスキップ(salvageのモグラ叩き不要に)。
        routed = _action_router(ll_user, sysadd, who, convo=msgs) if _looks_like_action(ll_user) else None
        if routed:
            try:
                summary = _action_summary(routed["tool"], routed["args"])
                pid = _register_pending(routed["tool"], routed["args"], who.get("uid"), summary,
                                        origin="user", query=str(ll_user)[:400], trace_id=_tid)
                try:
                    PENDING_ACTIONS[pid]["thread"] = thr
                except Exception:
                    pass
                pending_actions.append({"id": pid, "tool": routed["tool"], "args": routed["args"], "summary": summary})
                final = routed["reply"]
            except Exception:
                routed = None                       # 起票失敗→通常経路へフォールバック
        try:
            for it in range(MAXIT):
                if routed:                          # P2でアクション確定済 → 生成ループをスキップ
                    break
                last = (it == MAXIT - 1)
                # 最終反復は tool 無しで強制的に回答させる(空振り無限ループ防止)
                resp = ollama_chat(working, tools=(None if last else tools))
                m = resp.get("message", {}) or {}
                tcs = m.get("tool_calls")
                if tcs and not last:
                    working.append(m)
                    for tc in tcs:
                        fn = tc.get("function", {}).get("name", "")
                        args = tc.get("function", {}).get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        if fn in user_mcp_names and casper_user_mcp:   # ユーザー個人MCP(本人権限)
                            result = casper_user_mcp.call(who.get("uid"), fn, args, actor=who.get("uid"))
                        elif fn in mcp_names and casper_mcp:           # MCP公開ツール(共通Calendar)
                            if fn in MCP_ACTOR_TOOLS and who.get("uid") and isinstance(args, dict):
                                args["actor_id"] = who["uid"]          # actor_id を本人uidで強制(qwen のspoof防止・schema必須対応)
                            if fn in MCP_SIDE_EFFECT:                  # 副作用系=Stage2 承認ゲート(pending 登録・自動実行せず)
                                summary = _action_summary(fn, args)
                                pid = _register_pending(fn, args, who.get("uid"), summary,
                                                        origin="user", query=str(ll_user)[:400], trace_id=_tid)
                                pending_actions.append({"id": pid, "tool": fn, "args": args, "summary": summary})
                                result = (f"[承認待ち・未実行] 「{summary}」を下書き登録した(id={pid})。"
                                          "⚠️ **まだ実行しておらぬ**。ユーザーが画面下の承認ボタンを押すまで送信/実行されぬ。"
                                          "**絶対に『送信しました/送りました/実行しました/作成しました』等の完了報告をするな**(嘘になる)。"
                                          "正しくは『○○を下書きしました。下の承認ボタンを押すと実行されます』と案内せよ。同じ操作を再呼出するな。")
                            else:                                      # 読取系(get_messages 等)= write token+actor で実行
                                result = casper_mcp.call_tool(fn, args, token=(WRITE_TOKEN or None),
                                                              actor=who.get("uid"))
                        elif fn in ("vimeo_search", "vimeo_set_password"):   # Vimeo ライブ検索/パスワード設定
                            import casper_vimeo
                            if fn == "vimeo_search":
                                result = json.dumps(casper_vimeo.search(args.get("query", "")), ensure_ascii=False)
                            else:
                                result = json.dumps(casper_vimeo.set_password(args.get("video_id"), args.get("password", "")), ensure_ascii=False)
                        elif fn in ("aurora_search", "aurora_get", "aurora_create", "aurora_append"):   # Aurora 司書(read=直接 / 書込=Stage2)
                            import casper_aurora as _au
                            if fn == "aurora_search":
                                result = str(_au.search(args.get("query", ""), limit=8))[:6000]
                            elif fn == "aurora_get":
                                result = str(_au.get(args.get("doc_id", "")))[:6000]
                            else:                                  # aurora_create / aurora_append = 書込 → 承認ゲート(1スレ1資料 紐付け)
                                cur = _AURORA_CUR.get(thr) if thr else None
                                new_intent = bool(re.search(r"新規|新しく|新しい|別の|別に|もう[1一]つ|new doc", ll_user or ""))
                                efn = fn
                                if fn == "aurora_create" and cur and not new_intent:   # 既存資料あり&新規指定なし→同じ資料へ追記
                                    efn = "aurora_append"; args = {"doc_id": cur["doc_id"], "body": args.get("body", "")}
                                elif fn == "aurora_append" and not args.get("doc_id") and cur:
                                    args["doc_id"] = cur["doc_id"]
                                summary = _action_summary(efn, args)
                                pid = _register_pending(efn, args, who.get("uid"), summary,
                                                        origin="user", query=str(ll_user)[:400], trace_id=_tid)
                                PENDING_ACTIONS[pid]["thread"] = thr
                                PENDING_ACTIONS[pid]["title"] = args.get("title", "")
                                pending_actions.append({"id": pid, "tool": efn, "args": args, "summary": summary})
                                result = (f"[承認待ち・未実行] 「{summary}」を下書き登録した(id={pid})。"
                                          "⚠️ **まだ書き込んでおらぬ**。承認ボタンを押すまで実行されぬ。"
                                          "**絶対に『作成しました/書きました/実行しました』等の完了報告をするな**(嘘になる)。"
                                          "『○○を下書きしました。下の承認ボタンを押すと書き込まれます』と案内せよ。同じ操作を再呼出するな。")
                        elif fn == "calendar_lookup":      # RO 401 恒久回避: MCP(write token)経由でライブ照会
                            result = _calendar_lookup_mcp(args, who.get("uid"))
                        else:
                            result = casper_tools.execute(fn, args) if casper_tools else "(no tools)"
                        working.append({"role": "tool", "name": fn, "content": str(result)[:6000]})
                    if it == MAXIT - 2:            # 次が最終: ここまでの情報でまとめるよう促す
                        working.append({"role": "user", "content":
                            "（これ以上ツールは呼ばず、ここまでに取得した情報だけで今すぐ回答せよ。"
                            "サムネイル等 vault 由来の画像は『関連社内記録』に在ればそれを使い、無ければ無い旨を述べよ）"})
                    continue
                final = strip_think(m.get("content", ""))
                break
        except Exception as e:
            final = f"[error] {e}"

        if not final:
            final = "(応答を得られませなんだ)"
        final = re.sub(r"\n{3,}", "\n\n", final).strip()
        _pre = final
        final = _salvage_text_toolcall(final, who, pending_actions, query=str(ll_user)[:400], trace_id=_tid)   # qwenがツール未呼出でJSON文を書いた時の救済→承認カード
        _salv = final != _pre; _pre = final
        final = _validate_assets(final)                              # 出口検問: 捏造/asset URLを除去(qwen経路の主戦場)
        _val = final != _pre; _pre = final
        final = _guard_completion_claims(final, pending_actions)     # P1: カード無き完了主張を打ち消し(既成事実化の構造封じ)
        _grd = final != _pre
        if casper_trace:                            # トレース: 判断点を1req=1行で記録(事後分析基盤・Fable #7-1)
            try:
                _abstain = bool(re.search(r"(見当たら|確認できた範囲|わかりませ|分かりませ|存じませ|"
                                          r"該当(する|情報|資料).{0,8}(見つか|ありませ|無い|なし))", final))
                casper_trace.emit({"trace_id": _tid, "query": str(ll_user)[:200], "actor": who.get("uid"), "thread": thr,
                                   "routed": bool(routed), "action": (routed or {}).get("tool"),
                                   "rag_hits": len(hits) if isinstance(hits, list) else 0, "ctx_len": len(sysadd),
                                   "gen_sec": round(time.time() - _t0, 1), "salvaged": _salv, "validated": _val,
                                   "guarded_claim": _grd, "abstained": _abstain,   # 棄権(Fable #3-5/7-5: 棄権率の定点観測)
                                   "final_len": len(final), "cards": len(pending_actions)})
            except Exception:
                pass
        final, diagram = render_diagram(final)
        log_convo(who, "user", ll_user)
        log_convo(who, "casper", final, {"diagram": bool(diagram)})
        dev_log(who, ll_user, final, {"model": A.model, "backend": "ollama"})
        for i in range(0, len(final), 36):          # 疑似ストリーミング
            self._emit(final[i:i + 36])
        try:
            if diagram:
                self.wfile.write((json.dumps({"diagram": diagram}) + "\n").encode())
            for pa in pending_actions:              # Stage2: 承認待ち操作をUIへ
                self.wfile.write((json.dumps({"confirm": pa}, ensure_ascii=False) + "\n").encode())
            self.wfile.write(b'{"done":true}\n')
        except Exception:
            pass


def _warm_model_loop():
    """qwen を常時温存(20分毎に1tokのpingでkeep_alive更新)→冷間再ロード(15〜40秒)を防ぐ。賢さは不変。"""
    import time as _t
    while True:
        try:
            wb = {"model": A.model, "messages": [{"role": "user", "content": "hi"}],
                  "stream": False, "think": False, "keep_alive": -1,
                  "options": {"num_ctx": 12288, "num_predict": 1}}   # 実チャットと同 num_ctx(違うと積み直す)
            urllib.request.urlopen(urllib.request.Request(OLLAMA, data=json.dumps(wb).encode(),
                                   headers={"Content-Type": "application/json"}), timeout=120).read()
        except Exception:
            pass
        _t.sleep(1200)


def _build_profile(ukey):
    """ukey ユーザーの会話履歴から個性プロファイルを合成→ vault/20_people/profile_<ukey>.md。"""
    turns = []
    try:
        for line in open(CONVO_LOG, encoding="utf-8"):
            r = json.loads(line)
            if r.get("ukey") == ukey and r.get("content"):
                lbl = "ユーザー" if r.get("role") == "user" else "Casper"
                turns.append(f"{lbl}: {str(r['content'])[:280]}")
    except Exception:
        pass
    turns = turns[-50:]
    if len(turns) < 4:                       # 材料不足はスキップ(短期ノイズで人物像をブレさせない)
        PROFILE_BUILT[ukey] = datetime.datetime.now(); DIRTY_USERS.pop(ukey, None); return False
    m = re.match(r"u_(\d+)", ukey)
    uid = m.group(1) if m else None
    name = _uid_to_name(uid) if uid else ukey
    # 個性(静止画=不変の人物像)のみ蒸留。動向(現在の担当/進行中=動画)は 25_activity(動向層)の担当ゆえ
    # ここでは扱わぬ — 二重蒸留・chat時の二重注入・記述ドリフトを避ける(個性≠動向の分離)。
    sysp = ("以下は社内ユーザーと Casper の会話履歴。Casperが**先読み応対**に活かすため、"
            "この『ユーザー』本人の《不変の人物像(個性)》を簡潔にまとめよ。観点:\n"
            "① 個性・コミュニケーションの癖・働き方・避けたいこと\n"
            "② 関心/よく聞くこと(会話の頻出トピック・繰り返す懸念)\n"
            "**観察された事実のみ。憶測・決めつけは避け、根拠が薄いものは書かない**。各観点1〜3行の箇条書き。"
            "※『現在の担当・進行中の作業(動向)』は別層(動向層)が扱うゆえ、ここには書かぬこと。"
            "出力は必ず見出し『## Casper の理解』で始めること。")
    usr = f"対象ユーザー: {name}\n\n## 会話履歴(直近)\n" + "\n".join(turns)
    try:
        out = strip_think(llm_text(sysp, usr, num_predict=900)).strip()
    except Exception:
        return False
    if "## Casper の理解" not in out:
        out = "## Casper の理解\n" + out
    safe = re.sub(r"[^A-Za-z0-9_]", "", ukey)
    p = os.path.join(VAULT, "20_people", f"profile_{safe}.md")
    hdr = (f"---\nname: profile_{safe}\nuser: {name}\n"
           f"updated: {datetime.datetime.now().isoformat(timespec='seconds')}\n---\n\n"
           f"# {name} 個性プロファイル(Casper自動生成・アイドル便乗更新)\n\n")
    try:
        open(p, "w", encoding="utf-8").write(hdr + out + "\n")
    except Exception:
        return False
    PROFILE_BUILT[ukey] = datetime.datetime.now(); DIRTY_USERS.pop(ukey, None)
    return True


def _refresh_activity_band(ukey):
    """動向帯(25_activity/u_*.md)をアイドル便乗で日次更新。頭=qwen(安価・PII安全・ローカル)。
    深い蒸留(Opus)は distill_activity.py の手動/定期バッチが担う。best-effort・失敗は無害skip。"""
    m = re.match(r"u_(\d+)$", ukey or "")
    if not m:                                               # uid基盤ユーザーのみ(sid/email はskip)
        return
    try:
        import subprocess as _sp
        env = dict(os.environ)
        env["CASPER_ACT_MODEL"] = "qwen3.6:27b"             # 日次=ローカルqwen(深部Opusは別バッチ)
        _sp.run(["python3", os.path.join(HERE, "distill_activity.py"), m.group(1)],
                env=env, cwd=HERE, capture_output=True, text=True, timeout=300)
    except Exception:
        pass


def _profile_worker():
    """リソース余力(アイドル)時に、変化のあったユーザーの 個性プロファイル＋動向帯 を1人ずつ更新。"""
    import time as _t
    while True:
        _t.sleep(50)
        try:
            if (datetime.datetime.now() - LAST_CHAT_TS).total_seconds() < 90:
                continue                                    # まだアイドルでない→本番チャットに譲る
            now = datetime.datetime.now()
            cand = None
            for uk in list(DIRTY_USERS):
                last = PROFILE_BUILT.get(uk)
                if last and (now - last).total_seconds() < 6 * 3600:   # クールダウン6h
                    DIRTY_USERS.pop(uk, None); continue
                cand = uk; break
            if cand:
                _build_profile(cand)                        # 個性(静止画・qwen)
                _refresh_activity_band(cand)                # 動向帯(動画・qwen日次・深部Opusは別バッチ)
        except Exception:
            pass


# --- 全社ログ集約: get_events を定期 increment pull → 自前store(各人の動向/トラックの素) ---
EVENTS_FILE = os.path.join(HERE, "events_store.jsonl")
EVENTS_CURSOR = os.path.join(HERE, ".events_cursor")            # 後方互換(calendar 旧cursor)


def _events_cursor_path(source):
    return EVENTS_CURSOR if source == "calendar" else os.path.join(HERE, f".events_cursor_{source}")


def _events_cursor_get(source="calendar"):
    try:
        return int(open(_events_cursor_path(source), encoding="utf-8").read().strip() or "0")
    except Exception:
        return 0


def _events_pull_source(source, list_tools, call_get):
    """1ソース(Calendar/Aurora/Score)の get_events を増分pull→events_store.jsonl 追記。
    list_tools()→ツール名set / call_get(args)→結果。get_events 未露出なら 0(無害skip)。"""
    try:
        if "get_events" not in list_tools():
            return 0
    except Exception:
        return 0
    cur = _events_cursor_get(source)
    try:
        d = call_get({"actor_id": 28, "since": cur, "limit": 300})
        d = json.loads(d) if isinstance(d, str) else d
        evs = d.get("events") or []
    except Exception:
        return 0
    if not evs:
        return 0
    mx = cur
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        for e in evs:
            if not e.get("system"):
                e["system"] = source
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            try:
                mx = max(mx, int(e.get("seq", 0)))
            except Exception:
                pass
    try:
        open(_events_cursor_path(source), "w", encoding="utf-8").write(str(mx))
    except Exception:
        pass
    return len(evs)


def _events_pull_once():
    """全社ログ集約: Calendar＋Aurora(＋将来Score)の get_events を多源pull。各源 get_events 未露出ならskip。"""
    n = 0
    if casper_mcp and WRITE_TOKEN:                              # ① Calendar
        n += _events_pull_source(
            "calendar",
            lambda: {t["function"]["name"] for t in casper_mcp.list_tools(token=WRITE_TOKEN)},
            lambda a: casper_mcp.call_tool("get_events", a, token=WRITE_TOKEN, actor=28))
    try:                                                       # ② Aurora(Elvis殿 get_events 露出後 自動採用)
        if casper_mcp and casper_aurora and casper_aurora.configured():
            _u, _t = casper_aurora._conf()
            n += _events_pull_source(
                "aurora",
                lambda: {t["function"]["name"] for t in casper_mcp.list_tools(token=_t, url=_u)},
                lambda a: casper_mcp.call_tool("get_events", a, token=_t, url=_u))
    except Exception:
        pass
    return n


def _events_puller():
    """5分ごとに全社イベントを増分集約(seq昇順・冪等)。各人の動向/トラックの一次データになる。
    併せて OPEN LOOP(未了の約束)の完了プローブを走らせ、達成を自動検知して閉じる(hori事件の恒久解)。"""
    import time as _t
    _tick = 0
    while True:
        _t.sleep(300)
        _tick += 1
        try:
            n = _events_pull_once()
            if n:
                print(f"[events] +{n} (cursor={_events_cursor_get()})", flush=True)
        except Exception:
            pass
        if casper_health and _tick % 3 == 0:            # ~15分ごと: セルフヘルス監視→health.md更新＋逸脱アラート
            try:
                h = casper_health.run()
                if h.get("deviations"):
                    print(f"[health] 🔴 逸脱 {len(h['deviations'])}件: "
                          + ", ".join(d['metric'] for d in h['deviations']), flush=True)
            except Exception:
                pass
        try:                                               # OPEN LOOP 自動追跡: 完了プローブが満たされたら閉じる
            if casper_openloop:                             # 完了は open_loop_digest が"最近完了"として利用者へ先読み報告
                for r in (casper_openloop.check() or []):
                    print(f"[openloop] closed: {r.get('title')} — {r.get('evidence')}", flush=True)
        except Exception:
            pass


def _digest_refresh_once():
    """casper_context の元データ(projects/users)を MCP からライブ更新→digest再生成。RO非依存・恒久。"""
    if not (casper_mcp and WRITE_TOKEN):
        return
    try:
        pr = casper_mcp.call_tool("get_projects", {}, token=WRITE_TOKEN, actor=28)
        pr = json.loads(pr) if isinstance(pr, str) else pr
        json.dump({"items": pr.get("items", pr if isinstance(pr, list) else [])},
                  open("/tmp/cal_projects.json", "w", encoding="utf-8"), ensure_ascii=False)
        us = casper_mcp.call_tool("get_users", {"limit": 200}, token=WRITE_TOKEN)
        us = json.loads(us) if isinstance(us, str) else us
        json.dump({"items": [{"id": u.get("uid"), "username": u.get("username")} for u in us.get("items", [])]},
                  open("/tmp/cal_users.json", "w", encoding="utf-8"), ensure_ascii=False)
        import subprocess
        subprocess.run(["python3", os.path.join(HERE, "build_brain_digest.py")],
                       capture_output=True, timeout=90)
    except Exception:
        pass


def _digest_refresh_loop():
    """起動時＋30分ごとに digest をライブ更新(新規PJ/メンバーが先読み知識に即反映=V未表示の再発防止)。"""
    import time as _t
    _digest_refresh_once()
    while True:
        _t.sleep(1800)
        try:
            _digest_refresh_once()
        except Exception:
            pass


import threading as _threading
_threading.Thread(target=_warm_model_loop, daemon=True).start()   # 起動直後からモデルを温め続ける
_threading.Thread(target=_profile_worker, daemon=True).start()    # アイドル便乗で個性プロファイル育成
_threading.Thread(target=_events_puller, daemon=True).start()     # 全社ログ集約(get_events 増分pull)
_threading.Thread(target=_digest_refresh_loop, daemon=True).start()  # digest をライブ自動更新(RO非依存・恒久)
print(f"Casper chat -> http://localhost:{A.port}  (model {A.model} @ {A.endpoint})", flush=True)
ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
