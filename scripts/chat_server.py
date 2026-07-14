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
    import casper_notify                          # M3: 割り込み政策エンジン(朝ブリーフ/閾値割り込み・push型)
except Exception:
    casper_notify = None
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
    import casper_person_gate                    # 人ごと理解ゲート(入力の接地・別名/既定ファセット・Fable諮問)
except Exception:
    casper_person_gate = None
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
    import casper_breaker                          # サーキットブレーカー(依存ごと縮退/自動復帰・Fable北極星 柱2)
except Exception:
    casper_breaker = None
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
# PJ が納期超過に"成り得ない"status(完了/対象外)。新値deliver+旧値completed等を互換吸収。
_PJ_NOT_OVERDUE = {"deliver", "omit", "completed", "done", "complete",
                   "cancelled", "canceled", "approved"}


def _not_overdue_set(scope):
    """遅延判定の除外status集合。scope='task'は新19値の掟どおり{deliver,omit}のみ、'pj'は完了系も除外。
    件数と表の二重基準ドリフト(approved超過タスクが件数=遅延/表=完了済 と食い違う)を防ぐ単一ソース(Fable指摘)。"""
    return _TASK_NOT_OVERDUE if scope == "task" else _PJ_NOT_OVERDUE


def _overdue_days(due, status, today=None, scope="pj"):
    """【納期超過=派生事実の唯一の判定機構(Fable: 集合/派生の判断は機構・LLMは修辞)】
    返り: 超過日数(int>0) / 0(超過でない) / None(日付不正)。
    完了/対象外statusは due<today でも超過に非ず(isOverdue派生)。qwenに due<today の計算をさせない為の単一ソース。"""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
    except Exception:
        return None
    today = today or _dt.date.today()
    if str(status or "").lower() in _not_overdue_set(scope):
        return 0
    return (today - d).days if d < today else 0


def _due_note_c(due, status, today=None, scope="pj"):
    """派生の『納期状況』を機構が確定して文字列化(qwen/表に日付計算を委ねない)。
    超過→🔴N日超過 / 本日締切→⚠️ / 過去だが完了→"完了済(納期超過ではない)"(誤計算封じ) / それ以外→""。"""
    import datetime as _dt
    od = _overdue_days(due, status, today, scope)
    if od is None:
        return ""
    if od > 0:
        return f"🔴{od}日超過"
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
    except Exception:
        return ""
    today = today or _dt.date.today()
    done = str(status or "").lower() in _not_overdue_set(scope)
    if d == today and not done:
        return "⚠️本日締切"
    if d < today and done:                       # 過去納期だが完了 → 明示し qwen の「N日超過」誤断を封じる
        return "完了済(納期超過ではない)"
    return ""
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


# ── status_category(API 5値=todo/in_progress/review/completed/held) を単一ソースに(Fableレビュー・掟: ハードコード禁止)。
#    完了/残務/稼働は上の _task_is_done/_task_is_moving に寄せる。承認(dir_ap)とFB係争(qc_fb)は共に category=review ゆえ、
#    『承認で通過(clean)』と『FB/確認が係争中(active)』の区別だけは status値を"1箇所で"読む(APIのstatusが真実源・捏造でない)。
_REVIEW_APPROVED = {"dir_ap", "ap", "fix"}          # review内で『承認/通過』側(API status値・単一定義)


def _task_open(t):
    """残務(未完・作業対象)か。完了(category=completed)・除外(held=omit/wt)を除いた"これからやる/やっている"もの。"""
    return not _task_is_done(t) and (t.get("status_category") or "") != "held"


def _task_fb_active(t):
    """FB/確認が係争中か(素通り承認でない)。in_progress、または review かつ未承認(qc/qc_fb/dir_wt)。
    素通り=完了(deliver)/除外(omit)/承認(dir_ap/ap/fix)。todo(未着手)は"FB無し"側でactiveにしない。"""
    cat = (t.get("status_category") or "") if isinstance(t, dict) else ""
    if cat == "in_progress":
        return True
    if cat == "review":
        return str(t.get("status") or "").lower() not in _REVIEW_APPROVED
    return False

# --- Stage2: 副作用ツールの「承認→実行」フロー(DM代筆・QC提出・参照登録) ---
PENDING_ACTIONS = {}   # id -> {tool, args, uid, summary}
_LAST_CHOICES = {}     # thread -> {"opts":[{say,label,card_type}], "uid", "ts"}: 直前に出した選択カード(③選択ログ用)
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

def _draft_recipient_body(tool, args):
    """下書きレコードから (宛先名, 宛先uid, 本文) を頑健に抽出(引数名の揺れに耐える)。
    Q3C(Fable): 本文=真実源。これを機構で取り出し、憶測を封じる材料にする。"""
    a = args or {}
    if tool == "send_message":
        to = (a.get("to_user_id") or a.get("recipient_id") or a.get("to")
              or a.get("user_id") or a.get("recipient") or "")
        nm = _uid_to_name(to) if str(to).isdigit() else (to or "?")
        body = str(a.get("body") or a.get("content") or a.get("text") or a.get("message") or "")
        return nm, to, body
    if tool == "aurora_create":
        return (a.get("title") or "?"), "", str(a.get("body") or "")
    if tool == "aurora_append":
        return (a.get("doc_id") or "?"), "", str(a.get("body") or "")
    return "?", "", str(a.get("body") or a.get("content") or a.get("message") or "")


def _draft_excerpt(tool, args, n=150):
    """下書き1件を『宛先＋本文先頭n字』の1行に。retrieve-then-render: 憶測でなく実本文を見せる。"""
    nm, _to, body = _draft_recipient_body(tool, args)
    body = re.sub(r"\s+", " ", (body or "")).strip()
    head = "DM→" if tool == "send_message" else ("Aurora作成→" if tool == "aurora_create" else ("Aurora修正→" if tool == "aurora_append" else "→"))
    ex = body[:n] + ("…" if len(body) > n else "")
    return f"{head}{nm}｜{ex or '(本文なし)'}"


def _draft_bodies_context(who, limit=6):
    """滞留proposed下書きの『実本文』をsystem contextへ注入する block を返す(Q3C・Fable)。
    憶測の真因=本文がモデルの手元に無い(retrieveの穴)。これを埋め『内容は下記が全て・推測禁止』と縛る。"""
    if not casper_outbox:
        return ""
    try:
        props = [r for r in casper_outbox.pending(who.get("uid"))
                 if r.get("tool") in ("send_message", "aurora_create", "aurora_append")]
    except Exception:
        return ""
    if not props:
        return ""
    props.sort(key=lambda r: r.get("ts", ""), reverse=True)
    lines = []
    for r in props[:limit]:
        nm, _to, body = _draft_recipient_body(r.get("tool"), r.get("args") or {})
        body = re.sub(r"\s+", " ", (body or "")).strip()
        kind = {"send_message": "DM下書き", "aurora_create": "Aurora作成", "aurora_append": "Aurora修正"}.get(r.get("tool"), "下書き")
        lines.append(f"- [{kind}] 宛先/対象: {nm}\n  本文: 「{body[:220]}{'…' if len(body) > 220 else ''}」")
    return ("\n【承認待ち下書きの実本文(真実源・下記が全て)】\n" + "\n".join(lines) +
            "\n※下書きの内容を語る際はこの実本文のみを用いよ。ここに無い件名/意図/背景を推測・創作するな"
            "(「〜と思われる」等の憶測は禁止)。\n")


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


_DRAFT_SURFACE_RE = re.compile(r"((下書き|承認待ち|滞留|気にかけ|今日の3件).{0,14}(見せ|見た|確認|どう|選択|処理|対応|出し|表示|一覧|中身|内容|なに|何|どんな|どういう)|"
                               r"(見せ|表示|確認|処理).{0,6}(下書き|承認待ち)|溜まって.{0,6}(下書き|承認))", re.I)
# 既存下書きの"中身を問う"パターン(新規DM作成でない)。actionゲートに引っかかっても決定的fast pathを通す(Q3C強処方)。
_DRAFT_ASK_RE = re.compile(r"(下書き|承認待ち).{0,12}(中身|内容|なに|何|見せ|確認|どんな|どういう|全部|全文|read|見る)", re.I)
# 追従漏れ対策: Casper自身が「下書きを表示しますか?」と申し出た直後、ユーザーの裸の肯定(おねがい/はい/見せて…)は
# その申し出への同意=下書き浮上の合図。直前assistant発話に下書き申し出があり、今回が肯定なら surface を発火。
_AFFIRM_RE = re.compile(r"^(おねがい(します|いたします)?|お願い(します|いたします)?|はい|うん|ええ|そう(です|ね)?|"
                        r"頼(む|みます)|お頼み|見せて|表示して|見たい|お願いね|よろ(しく)?|了解|うむ|yes|ok|オーケー|ぜひ)"
                        r"[。、\s!！?？]*$", re.I)
_DRAFT_OFFER_RE = re.compile(r"(下書き|承認待ち).{0,40}(表示|見せ|確認しま|一覧|中身|内容|ご覧)|"
                             r"(表示|見せ|ご覧に入れ|確認).{0,12}(下書き|承認待ち)", re.I)


def _last_assistant(msgs):
    for m in reversed(msgs or []):
        if m.get("role") == "assistant":
            return str(m.get("content") or "")
    return ""


def _surface_pending_drafts(who, pending_actions, limit=6):
    """滞留proposed下書きを『承認カード』として再浮上させる=内容が見え・承認/却下ボタンで選択できる状態にする。
    (Casperが『下書きがある』と言うだけで内容も選択手段も示さぬ問題の解=決定は散文でなくカードで・殿指摘)。
    返り (総件数, 案内テキスト)。カードは pending_actions に積むとチャット末尾で confirm カードとして描画される。"""
    if not casper_outbox:
        return 0, ""
    try:
        props = [r for r in casper_outbox.pending(who.get("uid"))
                 if r.get("tool") in ("send_message", "aurora_create", "aurora_append")]
    except Exception:
        return 0, ""
    props.sort(key=lambda r: r.get("ts", ""), reverse=True)
    _lines = []
    for i, r in enumerate(props[:limit], 1):
        pid = r["id"]; args = r.get("args") or {}
        PENDING_ACTIONS[pid] = {"tool": r["tool"], "args": args, "uid": r.get("uid"),
                                "summary": r.get("summary"), "thread": r.get("thread")}
        pending_actions.append({"id": pid, "tool": r["tool"], "args": args, "summary": r.get("summary")})
        _lines.append(f"{i}. {_draft_excerpt(r['tool'], args)}")   # 実本文抜粋(憶測でなく真実源・Q3C)
    if not props:
        return 0, ""
    note = (f"承認待ちの下書きが **{len(props)}件** ございます。中身は以下の通り——\n\n"
            + "\n".join(_lines)
            + "\n\n下の各カードで**内容を確認**し、**「送信」か「破棄」を選択**してくだされ（本文の編集も可）。")
    if len(props) > limit:
        note += f"（多いため直近{limit}件を表示。残りは順次）"
    return len(props), note


def _briefing_draft_cards(who, limit=4):
    """開門ブリーフィングで滞留下書きを『承認カード』として直接出す(一往復短縮・Fable Q4)。
    テキストで『下書きがある』と述べて殿の「なに?」→「表示?」→「おねがい」の往復を待たず、
    最初から中身＋送信/破棄ボタンを提示する。返り=card list(PENDING_ACTIONSへ登録済)。"""
    cards = []
    if not casper_outbox:
        return cards
    try:
        props = [r for r in casper_outbox.pending(who.get("uid"))
                 if r.get("tool") in ("send_message", "aurora_create", "aurora_append")]
    except Exception:
        return cards
    props.sort(key=lambda r: r.get("ts", ""), reverse=True)
    for r in props[:limit]:
        pid = r["id"]; args = r.get("args") or {}
        PENDING_ACTIONS[pid] = {"tool": r["tool"], "args": args, "uid": r.get("uid"),
                                "summary": r.get("summary"), "thread": r.get("thread")}
        cards.append({"id": pid, "tool": r["tool"], "args": args, "summary": r.get("summary")})
    return cards


# ── Q1 選択カード機構(Fable): 曖昧な指示語で対象が複数の時、qwenに推測(捏造)させず人に選ばせる ──
_DEICTIC_RE = re.compile(r"(それ|あれ|これ|その件|あの件|この件|例の(件|やつ|resep|資料|下書き)?|"
                         r"さっきの(やつ|件|下書き|資料)?|くだんの|先ほどの(件|下書き|資料)?)")
_DEICTIC_ACTION_RE = re.compile(r"(送|出し|進め|やっ|対応|承認|片付け|完了|返信|確認|処理|片づけ)")
# 下書き候補の選択は『送る系』の意図に限定(下書き=送るもの)。確認/状態問い等での誤発火を避け精度を上げる。
_DEICTIC_SEND_RE = re.compile(r"(送|出し|返信|連絡|報告し|通達|通知し|承認|片付け|片づけ|進め|対応)")


def _deictic_word(q):
    m = _DEICTIC_RE.search(q or "")
    return m.group(0) if m else "その件"


def _build_choices(who, query, convo=None):
    """曖昧な指示語(それ/あの件/例の…)＋action意図で、対象候補が2件以上あり得る時、
    qwenに1つを推測(=捏造リスク)させず『選択カード』で人に決めさせる(Fable Q1・say型・接地の機構化)。
    候補は機構が真実源(承認待ち下書き)から決定的に列挙。返り choices dict(prompt/options) or None。
    say型: 各optionのsayは、選ぶと『その対象への具体指示』として/api/chatへ再投入される自足文。"""
    q = query or ""
    if not _DEICTIC_RE.search(q) or not _DEICTIC_SEND_RE.search(q):   # 曖昧指示語＋『送る系』意図の時だけ(誤発火抑制)
        return None
    if not casper_outbox:
        return None
    try:
        props = [r for r in casper_outbox.pending(who.get("uid"))
                 if r.get("tool") in ("send_message", "aurora_create", "aurora_append")]
    except Exception:
        return None
    if len(props) < 2:                                      # 候補1件以下=曖昧でない→通常フロー(surface/router)に委ねる
        return None
    props.sort(key=lambda r: r.get("ts", ""), reverse=True)
    opts = []
    for r in props[:6]:
        nm, _to, body = _draft_recipient_body(r.get("tool"), r.get("args") or {})
        ex = re.sub(r"\s+", " ", (body or "")).strip()
        opts.append({"id": r["id"],
                     "label": (f"{nm} 宛の下書き" if r.get("tool") == "send_message" else f"{nm}"),
                     "preview": (ex[:80] + ("…" if len(ex) > 80 else "")) or "(本文なし)",
                     "say": f"{nm}宛の下書き「{ex[:60]}」を送信して"})
    return {"prompt": f"『{_deictic_word(q)}』が指す下書きが**{len(props)}件**ございます。どれにいたしましょう？",
            "options": opts}


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
    if not text or ("```" not in text and "します" not in text and '"tool"' not in text
                    and "<tool" not in text and '"name"' not in text):
        return text
    text = re.sub(r"```tool.*?```", "", text, flags=re.S)          # ツール呼びの漏れブロック
    text = re.sub(r"</?tool_(?:code|call)>", "", text)             # <tool_code>/<tool_call> タグ(qwenのXML風漏れ)
    text = re.sub(r"```(?:python|json|tool_code)?\s*(?:calendar_lookup|get_projects|get_today_tasks|get_events)\([^`]*?```",
                  "", text, flags=re.S)
    # {"name":"<tool名>","arguments"/"parameters":{..}} 形式の漏れ(括弧の釣り合いで除去・calendar_lookup等)
    _nm = re.search(r'\{\s*"name"\s*:\s*"(calendar_lookup|get_[a-z_]+|search_vault|aurora_\w+|vimeo_\w+|update_task|send_message)"', text)
    if _nm:
        _st = _nm.start(); _d = 0
        for _j in range(_st, len(text)):
            if text[_j] == "{":
                _d += 1
            elif text[_j] == "}":
                _d -= 1
                if _d == 0:
                    text = text[:_st] + text[_j + 1:]; break
    text = re.sub(r"(?m)^\s*【?live】?[^\n]*?(取得|照会|確認)(して|し).*?(します|確認).*$", "", text)   # 【live】取得して確認します 等の実況
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
    text = re.sub(r"(?m)^.{0,70}(を|から)[^。\n]{0,25}(取得|照会|確認|参照|チェック)(し|いた)ます。?\s*$", "", text)   # 作業実況行(『〜をCalendarから照会します』等)
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


_ACTION_Q_RE = re.compile(r"(DM|ディーエム|メッセージ|連絡し|伝え|知らせ|報告し|報せ|通達|通知し|送っ?て|送信し|"
                          r"に確認(して|を|し|願|頼|取)|確認して(もら|頂|いただ)|確かめ(て|る))", re.I)


def _looks_like_action(msg):
    """安価な事前ゲート: DM/送信の意図がある発話だけ P2ルーターを走らせる(全メッセージで走らせない)。"""
    return bool(msg and _ACTION_Q_RE.search(msg))


# ── スムースなファイル送付DM(殿頻出2026-07-13): リンク＋任意パスワード＋宛先 のDM依頼を決定的に処理。
#    qwenのaction_routerが宛先解決失敗/カード未生成 で不揃いだった綻びの根治。
_URL_RE = re.compile(r"https?://[^\s　」』)）\]】]+")
_DM_INTENT_RE = re.compile(r"(DM|ディーエム|送っ?と?い?て|送信|送る|送って|連絡|伝え|渡(し|す|して)|共有|届け|投げ|報告|"
                           r"dropbox|ドロップボックス|リンク|納品|校了|共有)", re.I)
_PW_RE = re.compile(r"(?:🔑|パス(?:ワード)?|ぱす|pw|password|ＰＷ)\s*(?:は|=|＝|:|：)?\s*"
                    r"([A-Za-z0-9][A-Za-z0-9!-/:-@\[-`{-~]{2,})", re.I)   # PWは英数字始まり(散文『パスワードで共有』を拾わない)
# 文脈参照(『このファイル/これ/上記/先ほどの』)。発火は別途『URL無し＋DM意図＋宛先解決＋直前にURL有り』で厳重ゆえ広めで安全。
_FILE_REF_RE = re.compile(r"(この|その|これ|それ|先(の|ほど)|上記|さっき|例の|上の|あれ|奴|やつ)", re.I)
# "これは配信でなく"伝える"意図"の合図。担当違い/誤送/間違い等は、URLを配信するのでなく文面を書くべき。
# (殿指摘2026-07-13: 誤QC依頼のURLを「このQCは担当でない旨をkiyotomoにDM」と言ったのにURL配信扱いされた)
# 「担当出ない」のIME誤変換(で→出)も拾う。「違う」単独は拾わない(『違うファイルを送って』の正当配信を潰さぬ為)。
_NOT_DELIVERY_RE = re.compile(
    r"(担当|たんとう|管轄|所管|範囲|役割)[^。\n]{0,4}(で|出|じゃ|では)[^。\n]{0,3}(な|無)|"
    r"(私|わたし|自分|うち|こちら)[^。\n]{0,5}(担当|案件|仕事|もの|役割)[^。\n]{0,4}(で|出|じゃ)[^。\n]{0,3}(な|無)|"
    r"(間違(った|い|え)|まちが(った|い|え)|誤(送|り|って)|お門違い|人違い|宛先[^。\n]{0,3}(違|誤|間違))", re.I)


def _file_delivery_dm(user_msg, who, convo=None):
    """【スムースなファイル送付DM】共有リンク(Dropbox等URL)＋任意パスワード＋宛先 のDM依頼を決定的に処理。
    現メッセージにURLが無くても『このファイル/リンクをtetsuoにDMして』の文脈参照なら直前の会話からURL+PWを引く。
    宛先を人物解決器で確実に解決・丁寧な文面を機構生成・URL/PWを保持して send_message 下書きを起こす。"""
    q = user_msg or ""
    if not _DM_INTENT_RE.search(q):
        return None
    if _NOT_DELIVERY_RE.search(q):
        # 「担当でない/誤送/間違い」=URLを配信するのでなく"その旨を伝える"文面が要る→配信fast-pathを退き、
        # 通常のDM作成(LLMが文脈のQC情報から丁寧な誤送連絡を起こす+承認カード)に委ねる(殿指摘2026-07-13)。
        return None
    urls = _URL_RE.findall(q)
    src = q                                               # PW/URLの抽出元(既定=現メッセージ)
    fname = None
    if not urls and _FILE_REF_RE.search(q):               # 『このファイルを〜DMして』=直前の共有ブロックから引く
        for mm in reversed(convo or []):
            c = str(mm.get("content") or "")
            if _URL_RE.search(c):
                urls = _URL_RE.findall(c)
                src = c + "\n" + q                        # PWは文脈側にある(🔑 M834.. 等)
                mf = re.search(r"([^\s:：（(『」\n/]+\.(?:md|zip|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|mp4|mov|csv|txt|ai|psd|aep?))\b",
                               c, re.I)                       # 共有ブロックからファイル名だけ抽出(『Dropbox共有:』等の接頭を含めない)
                if mf:
                    fname = mf.group(1).strip()
                break
    if not urls:
        return None
    q_nourl = _URL_RE.sub(" ", q)                         # URL内の文字列を人物誤解決しないようURL除去して宛先解決
    uid, name = _resolve_person(q_nourl, exclude=None)    # tetsuo/漢字名/ひらがな読み も確実に解決(qwen非依存)
    if not uid:
        uid, name = _fuzzy_person(q_nourl)                # 『Testo』→tetsuo 等のタイポを近傍一致で救済
    if not uid:
        # ファイル送付の意図は明確(共有リンク＋DM＋『このファイル』)だが宛先だけ不明→
        # qwenに投げて『添付してください』の的外れ返答にせず、決定的に宛先だけ聞き返す(ファイルは理解済みと示す)。
        return {"_choices": True,
                "reply": "共有リンクは受け取りました。**どなた宛にDMしましょう？** お名前を教えてくだされ"
                         "（例: てつお／tetsuo／寺島さん 等）。"}
    m = _PW_RE.search(_URL_RE.sub(" ", src))              # パスワード(URL除去後から・URL断片を拾わない・🔑印も可)
    pw = m.group(1).strip("：:、。") if m else None
    mn = re.search(r"[「『]([^」』]{2,120})[」』]", q)     # 「…」で明示された本文があれば採用(URLでない時)
    if mn and not _URL_RE.search(mn.group(1)):
        note = mn.group(1).strip()
    else:
        note = (f"「{fname}」をお送りします。ご確認ください。" if fname else "データをお送りします。ご確認ください。")
    urls = list(dict.fromkeys(urls))                      # 重複URL除去
    lines = [note] + urls + ([f"パスワード: {pw}"] if pw else [])
    body = "\n".join(lines)
    args = {"to_user_id": str(uid), "body": body}
    if who.get("uid"):
        args["actor_id"] = who["uid"]
    reply = (f"**{name}** 宛に、下記のDM下書きを作成しました（まだ送っておりませぬ）。↓の承認カードで確認・編集し、"
             "ボタンを押すと送信されまする。\n\n> " + body.replace("\n", "\n> "))
    return {"tool": "send_message", "args": args, "reply": reply}


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
    b = re.sub(r"^\s*(お疲れ様です[。、\s]*)?(ryoji|Ryoji|りょうじ|殿)\s*(より|です)[。、,：:\s]*", "", b)   # 冒頭の署名『ryojiより/ryojiです/お疲れ様です。ryojiです』を除去(送信者は自明)
    b = re.sub(r"[ \t]+\n", "\n", b)                       # 行末空白
    b = re.sub(r"\n{3,}", "\n\n", b)                       # 空行の連発をまず2つに
    b = b.strip()
    # 短いDM(180字未満)は空行を全て詰めて double-spaced の間延びを解消(kiyotomo殿『改行が多い』)
    if len(b) < 180:
        b = re.sub(r"\n\s*\n", "\n", b)                    # 空行→単一改行
    return b.strip()


def _action_router(user_msg, context, who, convo=None, gate=None):
    """P2(Fable処方 propose→execute→render): 依頼が send_message(DM)かを制約デコードで判定し、型付き引数
    (to_user_id, body)を抽出。自由文 tool-call を作らせず機構が承認カードを起こす。返り {tool,args,reply} or None。
    ——qwenがテキストで関数を書く経路を通さないので、salvage のモグラ叩きが不要になる。convo=直前の会話(『上記』解決用)。
    gate=人ごと理解ゲートの解決結果(別名→人 の決定的宛先解決に使う・Fable①: roster前段に alias 辞書を挟む)。"""
    roster_lines = "、".join(f"{nm}=uid{uid}" for uid, nm in list(_ROSTER_MAP.items())[:40])
    # 理解ゲート: この人固有の"別名→人"を名簿に前置(決定的ルックアップ)。宛先の向き誤読を機構で防ぐ。
    _alias_person = {}   # say(小文字) -> uid
    for a in (gate or {}).get("alias_refs", []):
        r = a.get("ref") or {}
        if r.get("type") in ("person", "user") and r.get("id"):
            roster_lines = f"{a.get('say')}=uid{r['id']}、" + roster_lines
            _alias_person[str(a.get("say") or "").lower()] = str(r["id"])
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
             "・『確認して/確かめて』等の確認依頼では、変更の向き(誰から誰へ)・カット番号・詳細を勝手に断定/創作せず、"
             "依頼者が述べた事実の分だけで**中立に**確認する文面にせよ(『〜の件、依頼が反映されているか確認いただけますか』等)。推測を断定文にするな。\n"
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
    # 理解ゲート決定的override: 発話にこの人固有の別名(→人)が含まれるなら、宛先はその uid を正とする(向き誤読の機構的根治)
    for _say, _uid in _alias_person.items():
        if _say and _say in (user_msg or "").lower():
            to = _uid
            break
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


_NAKED_CHOICE_RE = re.compile(
    r"(送信(する)?か.{0,8}(破棄|却下|中止|削除)(する)?か|"
    r"(破棄|却下|削除)(する)?か.{0,8}(送信|承認)|"
    r"(どちら|いずれ|どれ)(に|を|か).{0,12}(選択|選ん|お選び|決め|して)|"
    r"(選択|お選び|選ん)(して)?(ください|下さい|くだされ)|"
    r"(送信|承認|却下|破棄)(するか|を)(選択|お選び|決め))",
    re.I)


def _validate_choices(text, pending_actions, choices=None):
    """【選択の出口検問=Fable Q2・不変条件①】『選択してください』等の選択要求を書くなら、必ず選択装置
    (承認カード or choices)が随伴していること。裸の選択要求(装置なし)は該当文を削除し中立な誘導へ差し替える。
    弱モデルが『選べ』と言うだけで選択手段を出さぬ事故(殿指摘)を出口で機械的に封じる最終防壁。"""
    if not text:
        return text
    if pending_actions or choices:                          # 選択装置が随伴→正当な選択要求。素通し
        return text
    if not _NAKED_CHOICE_RE.search(text):
        return text
    # 裸の選択要求: その文を落とし、選べぬ理由/次の一手へ中立に差し替える(delete+redirect)
    parts = re.split(r"(?<=[。\n])", text)
    kept = [p for p in parts if not _NAKED_CHOICE_RE.search(p)]
    out = re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
    if not out or len(out) < 8:
        out = ("お選びいただける項目が今はございませぬ。『下書きを見せて』等とお申し付けあらば、"
               "中身つきの選択肢（承認/破棄ボタン）を機構でお出しいたす。")
    return out


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


_PORTF_ALL_RE = re.compile(r"(全部|全て|すべて|全件|一覧|全実績|網羅|残り|他の|もっと|フル|full|list)", re.I)


def _portfolio_rows():
    """portfolio.md を {id: (公開日,タイトル,尺,リンク行)} に解析(ヘッダ行はそのまま返す)。"""
    p = os.path.join(VAULT, "30_culture_rules", "ops_vimeo_portfolio.md")
    rows, header = [], ""
    for ln in open(p, encoding="utf-8"):
        if re.match(r"\|\s*公開日", ln):
            header = ln.rstrip("\n")
        m = re.match(r"\|.*?https://vimeo\.com/(\d+)", ln)
        if m:
            rows.append((m.group(1), ln.rstrip("\n")))
    return header, rows


def _featured_entries():
    """portfolio_curation.yaml の featured エントリ列(順序保持)。各 entry は {id, [title,date,dur,link]}。
    md に無いID(例: ライブVimeoの社内ショーリール)は entry のインラインメタで行を組める=curation.yamlが真実源。"""
    cp = os.path.join(VAULT, "30_culture_rules", "portfolio_curation.yaml")
    try:
        import yaml
        d = yaml.safe_load(open(cp, encoding="utf-8")) or {}
        return [e for e in (d.get("featured") or []) if e.get("id")]
    except Exception:
        return []


def portfolio_digest(query):
    """制作実績クエリ時、自社Vimeoポートフォリオを注入(個人経歴との混同防止)。
    Q1(Fable): 弱モデルに『厳選せよ』と頼まず、厳選済み(curation.yaml の★)だけを注入する=注入量が期待出力量。
    『全部/一覧/全件』等 明示時のみ全件注入。曖昧なら curated を既定(progressive disclosure)。"""
    if not re.search(r"実績|ポートフォリオ|portfolio|作品|制作事例|過去案件|どんな.*作", query or "", re.I):
        return ""
    try:
        header, rows = _portfolio_rows()
        if not rows:
            return ""
        want_all = bool(_PORTF_ALL_RE.search(query or ""))
        feats = _featured_entries()
        if not want_all and feats:                          # 代表系 or 既定 → ★のみ注入
            fmap = dict(rows)
            sel = []
            for e in feats:
                fid = str(e.get("id"))
                if fid in fmap:                             # md に在れば md 行(公開日/タイトル/尺/リンク)
                    sel.append(fmap[fid])
                elif e.get("link"):                         # md に無い(社内ショーリール等)→ curation.yaml のインラインメタで行を組む
                    sel.append(f"| {e.get('date','—')} | {e.get('title', fid)} | {e.get('dur','—')} | {e.get('link')} |")
            rest = max(0, len(rows) - sum(1 for e in feats if str(e.get('id')) in fmap))
            table = "\n".join([header, "|---|---|---|---|"] + sel)
            return ("\n\n## 自社制作実績(代表作・Vimeo公開・一次の会社実績)\n" + table
                    + f"\n※これは**代表作 {len(sel)}本の厳選**。公開実績は全{len(rows)}本(他{rest}本)。"
                    "回答ではこの表の『タイトル(公開日・尺) リンク』を使え(リンクだけの羅列は禁止)。"
                    "この厳選をそのまま提示し、勝手に全件へ広げるな。末尾に『全実績もお見せできます』と一言添えよ。"
                    "\nFF/Samurai Jack等は個人メンバー経歴ゆえ会社実績と区別。")
        # 全件明示 → 全件注入(截ち切れは自動継続機構が拾う)
        body = "\n".join([header, "|---|---|---|---|"] + [r[1] for r in rows])
        return ("\n\n## 自社制作実績(全件・Vimeo公開・一次の会社実績)\n" + body[:6000]
                + f"\n※全{len(rows)}本。回答では『タイトル(公開日・尺) リンク』を使え(リンクだけの羅列は禁止)。"
                "FF/Samurai Jack等は個人メンバー経歴ゆえ会社実績と区別。")
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


def _thread_is_new(uid, msgs):
    """スレッドが"新着(要対応)"か判定する唯一の機構。最新メッセージが自分の送信なら新着でない
    (自分で送ったDMを新着扱いしない・殿指摘2026-07-13)。それ以外は相手発の未読(read_at無し)が
    1通でもあれば新着。dm_threads と 朝ブリーフで同一判定を共有(件数と一覧を割らせない・Fable鉄則五)。"""
    msgs = msgs or []
    if not msgs:
        return False
    newest = max(msgs, key=lambda m: str(m.get("created_at") or m.get("ts") or m.get("id") or ""))
    if str(newest.get("sender_id")) == str(uid):
        return False
    return any(str(m.get("sender_id")) != str(uid) and not m.get("read_at") for m in msgs)


# 開発時に注入されたseed/テストDMの判別(殿指摘2026-07-14: Scoreで見つからぬ幻DMを新着に出していた)。
# 強いseedシグナル: 10000xxx帯のthread_id(システム連番) / システムマーカー本文 / テスト名参加者。実在するDMは残す。
_SEED_MARK_RE = re.compile(r"Task message thread initialized|Thread started\.|thread initialized", re.I)
_SEED_NAME_RE = re.compile(r"User\s*\d+|Spec\s*Admin", re.I)


def _is_seed_thread(t):
    """seed/テストDMスレッドか。真実源(Calendar messaging)に開発用の投入が残っている分を新着表示から外す。"""
    try:
        if int(t.get("thread_id") or 0) >= 10000000:      # 10000000始まりの連番=システム/seed
            return True
    except Exception:
        pass
    lm = str(t.get("last_message") or "")
    if _SEED_MARK_RE.search(lm) or lm.strip().lower() == "test":
        return True
    names = " ".join(str(p.get("name") or "") for p in (t.get("participants") or []))
    return bool(_SEED_NAME_RE.search(names))


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
        _allt = [t for t in (data.get("threads") or []) if not _is_seed_thread(t)]   # seed/テストDMを除外
        threads = sorted(_allt, key=lambda t: str(t.get("updated_at") or ""), reverse=True)[:20]
        for _t in threads:                              # DM participants から名簿を収穫(RO非依存で恒久cacheが育つ)
            _roster_observe(_t.get("participants"))

        def _chk(t):                                   # 相手からの未読(read_at=None)があるスレッドか
            try:
                r = casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(t.get("thread_id"))},
                                         token=WRITE_TOKEN, actor=uid)
                md = json.loads(r) if (r or "").strip().startswith("{") else {}
                return (t, _thread_is_new(uid, md.get("messages", [])))
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


# 人物の別名(漢字/カナ)→uid 索引: 「寺島」が roster「terajima」と一致せず主語解決に失敗する綻びの汎用解。
# PJ名解決器(_pj_index)の人物版。ハードコードでなく vault/20_people/*.md から機構抽出(読み仮名の一部を自前で賄う)。
_PERSON_ALIAS = {"mtime": 0.0, "idx": {}}


def _person_alias_index():
    """人物別名(漢字/カナ)→uid を 20_people/*.md から機構抽出。源: frontmatter name / 見出しの漢字カナ、
    本文『◯◯さん』(漢字2-4字・2回以上=強シグナル)。uid は calendar_user_id。mtimeキャッシュ・検査可能。"""
    import glob
    pdir = os.path.join(HERE, "..", "vault", "20_people")
    try:
        latest = max((os.path.getmtime(f) for f in glob.glob(os.path.join(pdir, "*.md"))), default=0.0)
    except Exception:
        latest = 0.0
    if latest == _PERSON_ALIAS["mtime"] and _PERSON_ALIAS["idx"]:
        return _PERSON_ALIAS["idx"]
    # alias -> {uid: strength}(strong=frontmatter/見出し由来 / weak=本文さんパターン)。corpus全体で衝突を集計し3値化(Fable P2)。
    cand = {}

    def _add(a, uid, strong):
        if not a:
            return
        cand.setdefault(a, {})
        cand[a][uid] = max(cand[a].get(uid, 0), 2 if strong else 1)
    for f in glob.glob(os.path.join(pdir, "*.md")):
        try:
            txt = open(f, encoding="utf-8").read()
        except Exception:
            continue
        muid = re.search(r"calendar_user_id:\s*(\d+)", txt)
        if not muid:
            continue
        uid = int(muid.group(1))
        for m in re.finditer(r"(?m)^(?:name:\s*|#\s+)(.+)$", txt):     # frontmatter name / 見出し=強ソース
            v = re.sub(r"[（(].*", "", m.group(1)).strip()
            if re.search(r"[一-龯ぁ-んァ-ヿ]", v) and not re.search(r"[A-Za-z]{3,}", v):
                if len(v) >= 2:
                    _add(v, uid, True)
                mk = re.match(r"^([一-龯]{1,3})([ぁ-んァ-ヿ]{2,})$", v)   # 「黒丸クロマル」→姓「黒丸」+読み「クロマル」(単漢字姓も強ソースなら可・Fable P2)
                if mk:
                    _add(mk.group(1), uid, True); _add(mk.group(2), uid, True)
        total, withsan = {}, {}                                        # 本文『◯◯さん』(漢字1-3字・単漢字姓も『さん』付き強シグナルなら可)
        for m in re.finditer(r"[一-龯]{1,3}", txt):
            total[m.group(0)] = total.get(m.group(0), 0) + 1
        for m in re.finditer(r"([一-龯]{1,3})さん", txt):
            withsan[m.group(1)] = withsan.get(m.group(1), 0) + 1
        for k in withsan:                                              # 本文『◯◯さん』=弱ソース
            if total.get(k, 0) < 2:
                continue
            if len(k) >= 2 and withsan[k] >= 1:                        # 2-3字姓: さん1回で可(寺島等)
                _add(k, uid, False)
            elif len(k) == 1 and withsan[k] >= 2:                      # 単漢字姓: 誤爆多いのでさん2回必須(堀さん等)
                _add(k, uid, False)
    # 3値化: alias が単一uid→採用。複数uid→強ソースが唯一勝者ならそれ、さもなくば ambiguous として落とす(silent-pick禁止)。
    idx = {}
    for a, uids in cand.items():
        if len(uids) == 1:
            idx[a] = next(iter(uids))
        else:
            mx = max(uids.values())
            winners = [u for u, s in uids.items() if s == mx]
            if len(winners) == 1:
                idx[a] = winners[0]                                    # 強ソースの唯一勝者
            # else: 同強度で複数uid=ambiguous→索引に入れない(誤帰属より未解決を選ぶ)
    _PERSON_ALIAS.update({"mtime": latest, "idx": idx})
    return idx


def _resolve_person(query, exclude=None):
    """クエリ中の人物を uid で解決。roster(ローマ字/カナ)優先→人物別名索引(漢字/カナ・vault由来)。
    exclude=質問者本人uid(主語扱いしない)。返り=(uid, name) or (None, None)。"""
    if not query:
        return None, None
    try:
        if not _ROSTER_MAP:
            _roster_refresh()
        for u, nm in _ROSTER_MAP.items():
            if str(u) == str(exclude):                        # roster キーは str・exclude は int/str 両対応
                continue
            nm = str(nm or "")
            if len(nm) < 2:
                continue
            if re.fullmatch(r"[A-Za-z0-9]+", nm):             # ASCII名(ou/yu/li/tim等)は単語境界必須。ou⊂soul/tim⊂estimate の誤爆を断つ(Fable P0-2)
                hit = re.search(r"(?<![A-Za-z0-9])" + re.escape(nm) + r"(?![A-Za-z0-9])", query, re.I)
            else:                                             # 和名/カナ/複合名は部分一致
                hit = re.search(re.escape(nm), query, re.I)
            if hit:
                return _uid_int(u), nm                        # assigned_to は int ゆえ int に正規化(str "34"のまま返すと照合が全外れ)
        # 読み仮名(ひらがな/カタカナ)→ローマ字 で roster ASCII名に一致(『てつお』→tetsuo・『これてつおに』も)。
        # 宛先は「に/へ/宛/さん/くん」の直前に来る=その直前のかな/漢字連続を切り出し翻字し、roster名が末尾に来れば一致。
        # (クエリ全体を翻字すると『テツオニDM』→"tetsuonidm"のようにDM等がくっつき助詞判定が壊れる為、宛先位置を先に切る)
        def _tr(seg):
            k = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in seg)
            return _translit_kana_runs(k).lower()
        segs = [m.group(1) for m in re.finditer(r"([ぁ-ヿ一-龯]{2,12})(に|へ|宛|さん|くん|さま)", query)]
        segs += re.findall(r"[ぁ-ヿ]{2,10}", query)         # 助詞無し(『てつお送っといて』)も候補に
        _cand = sorted(((str(u), str(nm or "").lower()) for u, nm in _ROSTER_MAP.items()),
                       key=lambda x: -len(x[1]))            # 長名優先(短い suffix の誤一致を避ける)
        for seg in segs:
            sr = re.sub(r"[^a-z0-9]", "", _tr(seg))         # 翻字漏れの小書きァ等の非ASCIIを除去→純ローマ字に
            if len(sr) < 3:
                continue
            for u, nm in _cand:
                if u == str(exclude) or len(nm) < 3 or not re.fullmatch(r"[a-z0-9]+", nm):
                    continue                                # 読み一致は3字以上(ou/yu等の短名は誤爆回避)
                if sr == nm or sr.endswith(nm):            # 『てつお』=tetsuo / 『をてつお』=wotetsuo(末尾一致)
                    return _uid_int(u), _ROSTER_MAP.get(u, nm)
    except Exception:
        pass
    for alias, uid in sorted(_person_alias_index().items(), key=lambda x: -len(x[0])):
        if str(uid) != str(exclude) and alias in query:
            return _uid_int(uid), _ROSTER_MAP.get(str(uid), alias)
    return None, None


def _editdist(a, b):
    """Levenshtein 編集距離(タイポ近傍一致用)。"""
    if a == b:
        return 0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[n]


def _fuzzy_person(query, exclude=None):
    """クエリ中の名前らしきASCIIトークンを roster名に近傍一致(タイポ救済・『Testo』→tetsuo)。
    編集距離<=2・名前長>=4・唯一の最良のみ採用(誤救済を避ける)。返り=(uid, name) or (None,None)。"""
    toks = set(re.findall(r"[A-Za-z]{4,}", (query or "").lower()))
    if not toks:
        return None, None
    cands = []
    for u, nm in (_ROSTER_MAP or {}).items():
        if str(u) == str(exclude):
            continue
        nm = str(nm or "").lower()
        if len(nm) < 4 or not re.fullmatch(r"[a-z]+", nm):
            continue
        d = min((_editdist(tok, nm) for tok in toks), default=99)
        if d <= 2:
            cands.append((d, u, nm))
    if not cands:
        return None, None
    cands.sort()
    if len(cands) > 1 and cands[0][0] == cands[1][0]:      # 最良が同距離で複数=曖昧→救済しない
        return None, None
    return _uid_int(cands[0][1]), _ROSTER_MAP.get(str(cands[0][1]), cands[0][2])


def _uid_int(u):
    """uid を int に正規化(roster キーは str・Calendar の assigned_to は int)。失敗時は原値。"""
    try:
        return int(u)
    except Exception:
        return u


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
        # 主語解決(Fable処方4-C): 問いが指す"人物"の live をその人のuidで引く(質問者本人でなく主語)。
        # 証拠(live)なき人物断定=『◯◯氏の癖で作業中報告→未完了』の無接地雛形貼付を根で断つ。
        # 漢字表記(寺島)も人物別名索引で解決(roster「terajima」と一致せぬ綻びの汎用解)。
        subj_uid, subj_name = _resolve_person(query, exclude=uid)
        subj_note = ""
        if subj_uid:
            try:
                mine = [t for t in _all_tasks() if t.get("assigned_to") == subj_uid and _task_is_moving(t)]
            except Exception:
                mine = []
            if mine:
                _ls = [f"- {t.get('name') or t.get('title')} [{t.get('status_label') or t.get('status')}]"
                       f" 〆{str(t.get('due_date') or '')[:10]}" for t in mine[:15]]
                live += f"\n【live: {subj_name} の進行中(wip)タスク {len(mine)}件(Calendar)】\n" + "\n".join(_ls)
            else:
                live += f"\n【live: {subj_name} に現在 進行中(wip)の割当タスクは無し(Calendar)】"
            subj_note = (f"\n・この問いの主語は人物『{subj_name}』。上の {subj_name} の live のみを状態の根拠にせよ。"
                         "**live に無い断定(完了/未完了/🔴/『◯◯氏の癖で〜』等の人格由来の結論化)は禁止**。"
                         "traitは読み方の手がかりであって結論ではない。裏取り材料が無ければ『現時点では未確認』と述べよ。")
            # 主語×PJ の交差(Q3): 「marukomeでの寺島の進捗」型は、主語のそのPJ内割当を機構で確定し、
            # 0件なら『当該PJに未アサイン』と明言させる(『記録なし→①②③どれ?』のメニュー逃げを封じる)。
            _pst, _pnames, _ = _pj_resolve(query)
            if _pst == "unique":
                try:
                    _items = json.load(open("/tmp/cal_projects.json")).get("items", [])
                    _pid = next((p.get("id") for p in _items if str(p.get("name")) == _pnames[0]), None)
                    _inpj = [t for t in _all_tasks() if t.get("assigned_to") == subj_uid and t.get("project_id") == _pid]
                except Exception:
                    _inpj = []
                if _inpj:
                    live += (f"\n【live: {subj_name} の {_pnames[0]} 内タスク {len(_inpj)}件】\n"
                             + "\n".join(f"- {t.get('name') or t.get('title')} [{t.get('status_label') or t.get('status')}]"
                                         f" 〆{str(t.get('due_date') or '')[:10]}" for t in _inpj[:15]))
                    subj_note += f"\n・{subj_name} は {_pnames[0]} に上記の割当あり。これを根拠に進捗を述べよ。"
                else:
                    subj_note += (f"\n・**{subj_name} は {_pnames[0]} に現在アサインされたタスクが無い(割当0)。"
                                  f"これを明言せよ。①②③のような選択肢メニューで逃げず『{subj_name} は {_pnames[0]} に"
                                  "現在の割当なし』と機構の事実として答える(他PJに割当があるなら1文で触れてよい)。**")
        return ("\n\n## 【検証ゲート】状態確認の問い — 応答前の裏取り必須\n"
                "この問いは『状態(〜された?/上がった?/どうなってる?/進捗)』を尋ねている。掟②に従い:\n"
                "・動向層の帯は as-of 時点のスナップショットゆえ、その古い記述を『結末』と誤認して断定するな。\n"
                "・下記の live 照会を最優先の根拠にせよ。live に無ければ『現時点では確認できておらぬ』と正直に述べよ。\n"
                "・**回答の各事実に出所を明示せよ**: 【live】(今照会した実状態)／【帯】(動向層の過去記述)／【推測】。\n"
                "・**出所タグの無い状態断定は禁止**。" + subj_note
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
        if not query:
            return ""
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        online = [p for p in items if str(p.get("display_status") or "online") == "online"]
        # 発火: 一般PJ語(_PROJ_Q_RE) or online PJ名を直接含む問い(『marukomeは今どうなってる?』等の個別PJ照会=
        # ツール呼びの漏れを防ぐ・データを注入して一覧から答えさせる)
        _name_hit = bool(_match_online_pj(query))        # 表記ゆれ耐性(カタカナ⇄ローマ字)でPJ名照合
        if not (_PROJ_Q_RE.search(query) or _name_hit):
            return ""
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


def entity_digest(query):
    """【実体アイデンティティ=unique解決の出口に中身を配線(Fable処方3-B)】名前解決器が unique に解けたPJの
    Calendar実レコード(正規名/期間/状態/概要)を『このPJの正体』として注入。名前から社名/意味を推測する真空を埋め、
    marukome→丸亀製麺 の幻覚展開を断つ。閉集合(解決済み実体のみ)ゆえ軽い。unique でなければ空。"""
    try:
        if not query:
            return ""
        st, names, _ = _pj_resolve(query)
        if st != "unique":
            return ""
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        p = next((x for x in items if str(x.get("name")) == names[0]), None)
        if not p:
            return ""
        desc = (p.get("description") or "").replace("\n", " ").strip()[:220]
        sd = str(p.get("start_date") or "")[:10]
        ed = str(p.get("end_date") or "")[:10]
        # 納期状況は"派生事実"→機構が確定して渡す(qwenに end_date と本日の引き算をさせない)。
        # 完了PJの過去納期を「N日超過」と誤計算する事故(殿指摘2026-07-13・コンバトラーV)を根絶。
        _dn = _due_note_c(ed, p.get("status")) or ("納期未設定" if not ed else "予定内(納期超過ではない)")
        return ("\n\n## 【このPJの正体(これが全て・名前から社名/読みを推測するな)】\n"
                f"- 正規名: {names[0]}\n"
                f"- 状態: {p.get('status')}／期間: {sd or '—'}〜{ed or '—'}\n"
                f"- 納期状況(確定値): {_dn}\n"
                + (f"- 概要: {desc}\n" if desc else "")
                + "**この実レコードだけがこのPJの正体。名前の字面から社名・商品名・意味を勝手に補完/展開するな"
                "(例『◯◯（△△株式会社）』のような括弧書きの推測は禁止)。上の概要に無い属性を創作するな。**\n"
                "**納期の超過/未超過は上の『納期状況(確定値)』が唯一の正。end_date と本日を突き合わせて"
                "自分で超過日数を計算・言及するな(完了/deliver済のPJは過去納期でも納期超過ではない)。**")
    except Exception:
        return ""


# チーム構成/人員体制/外注 の問いを検知(自社の実職能で答えさせる為・Fable処方5-D)
_TEAM_Q_RE = re.compile(
    r"(チーム|体制|人員|要員|布陣|構成|何人|人数|外注).{0,12}(構成|体制|理想|組め|組む|提案|必要|どう|案|最適|振り分け)|"
    r"(理想|最適|どんな|どういう|どう).{0,8}(チーム|体制|人員|布陣|構成)", re.I)


def team_vocab_digest(query):
    """【自社の職能語彙=機構抽出(Fable処方5-D)】チーム構成の問いに、汎用IT職(PM/QA/エンジニア)でなく
    自社(CG/VFX)の実職能で答えさせる。職能語彙は Calendar 全タスクの type(工程)ラベルの distinct から機構抽出
    (散文でハードコードせず真実源準拠・自動更新)。チーム構成の問いでなければ空。"""
    try:
        if not query or not _TEAM_Q_RE.search(query):
            return ""
        from collections import Counter
        c = Counter()
        for t in _all_tasks():
            v = (t.get("type") or "").strip()
            if v:
                c[v] += 1
        roles = [k for k, _ in c.most_common(14)]
        if not roles:
            return ""
        return ("\n\n## 【自社の職能語彙(Calendar実タスクの工程から機構抽出)】\n"
                f"当社(studio bokan=CG/VFX制作)の実際の工程/職能: {'、'.join(roles)}。\n"
                "**チーム構成を答える時は、この自社の実職能(アニメーション/コンポジット/ライティング/FX/モデリング等)で"
                "組め。汎用IT職(PM/QA/エンジニア/Webデザイナー)の一般論に流すな。**外注も同じ工程語彙で振り分けよ。")
    except Exception:
        return ""


# 今後アサインされているPJ の問いを検知(殿指示2026-07-10: 「予定される→されている」=Calendarに既に入っている先の割当を見る)。
_FUTURE_ASSIGN_RE = re.compile(
    r"(今後|次(の|に)?|これから|将来|以降|後に|終わ(った|り).{0,4}後).{0,14}(アサイン|プロジェクト|PJ|案件|仕事|参加|入る|予定|やる)|"
    r"(アサイン|参加).{0,8}(予定|されている|される)|次(の)?(プロジェクト|案件|PJ|現場|仕事)", re.I)


def future_assign_digest(query, who):
    """【今後アサインされているPJ=retrieve-then-derive(殿指示2026-07-10)】主語(人物・無ければログイン本人)の、
    Calendarに既に入っている先の割当=①未来開始(start>=today)タスク ②未完(status未完)の残務 を工程/PJ別に機構導出。
    『予定される』でなく『既に予定されている』ものを見る。無ければ正直に『今後の新規アサインは未登録』＋残務を示す。"""
    try:
        if not query or not _FUTURE_ASSIGN_RE.search(query):
            return ""
        uid, name = _resolve_person(query, exclude=None)      # クエリに人物名→その人／無ければログイン本人
        if not uid:
            # Fable P2: 人物らしきトークン(◯◯さん/漢字姓)があるのに未解決なら、黙って本人へ落とさない(第三者を本人の予定で語る事故)
            if re.search(r"[一-龯ぁ-ヿ]{1,4}さん|[A-Za-z]{2,}\s*さん", query):
                return ("\n\n## 【今後アサイン=人物未特定】\n"
                        "クエリに人物名らしき語があるが roster/人物索引で特定できなかった。"
                        "**誰の予定かを勝手にログイン本人と仮定して答えるな。**『どなたの今後アサインでしょう？(お名前を)』"
                        "と聞き返すか、名前が曖昧なら候補を挙げて確認せよ。")
            uid = _uid_int(who.get("uid")); name = _ROSTER_MAP.get(str(who.get("uid")), "あなた")
        if not uid:
            return ""
        today = datetime.date.today().isoformat()
        proj = {p.get("id"): p for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
        mine = [t for t in _all_tasks() if t.get("assigned_to") == uid]
        import collections
        fut = collections.defaultdict(int)                    # 未来開始
        rem = collections.defaultdict(int)                    # 未完の残務
        for t in mine:
            if str(t.get("start_date") or "")[:10] >= today:
                fut[t.get("project_id")] += 1
            if _task_open(t):                                 # 残務判定はcategory単一ソースの _task_open に寄せる(Fable P1-2)
                rem[t.get("project_id")] += 1

        def _pjdetail(pid, n):
            p = proj.get(pid, {})
            ed = str(p.get("end_date") or "")[:10]
            ds = (p.get("description") or "").replace("\n", " ").strip()[:80]
            return f"- {p.get('name', pid)}（{n}件・〆{ed or '—'}" + (f"・{ds}" if ds else "") + "）"
        out = f"\n\n## 【{name} の今後アサイン=Calendar確定・本日{today}】\n"
        if fut:
            out += "今後開始(未来start)の割当:\n" + "\n".join(_pjdetail(k, v) for k, v in fut.items()) + "\n"
        else:
            out += "**今後開始(未来start)の新規アサインは Calendar に未登録。**\n"
        if rem:
            out += "現在の残務(未完タスク):\n" + "\n".join(_pjdetail(k, v) for k, v in rem.items()) + "\n"
        else:
            out += "現在の未完タスクも無し(手が空いている)。\n"
        out += ("**この確定結果で答えよ。『予定される(不確定)』でなく『既にCalendarに入っている先の割当』を見た結果。"
                "今後の新規アサインが未登録なら、その事実＋現在の残務を正直に述べよ(将来PJを推測で創作するな)。**")
        return out
    except Exception:
        return ""


# FB/チェックログ/リテイク の問いを検知(殿指示2026-07-10: FBログはスレッドのテキスト＋対象カットのretake記録に在る)。
_FBLOG_Q_RE = re.compile(
    r"(fb|フィードバック|リテイク|retake|差し戻し|検収|レビュー内容|チェックログ|"
    r"(チェック|指示|やり取り|コメント|レビュー).{0,4}(内容|ログ|履歴|記録)|"
    r"(ログ|履歴).{0,6}(教え|見せ|見たい|ある|は|を|くれ))", re.I)


def fb_log_digest(query):
    """【FBログ=スレッドテキスト＋retake記録(殿指示2026-07-10)】FB/リテイク/チェックログの問いは、議事録・各人の
    activity逐語・DMスレッド＋対象カットの status 遷移(dir_ap=監督承認/retake=差し戻し)がFBログの実体。retrieved の
    スレッド記録を『記録されていない』と却下させず、それをFBログとして提示させる(却下してから中身を出す自己矛盾の封じ)。"""
    try:
        if not query or not _FBLOG_Q_RE.search(query):
            return ""
        # 停滞FBの"一覧"意図(通知のN件)はCalendar機構の表が答える→ここでRAG(legacy混入源)を出さず譲る(殿指摘2026-07-13)。
        if _STALL_LIST_RE.search(query) and not re.search(r"(?:cut|カット|c)\s*0*\d{1,3}\b", query, re.I):
            return ""
        # ① まず対象カットのstatusを確定(素通り承認 vs 係争中FB)。これが framing を支配する。
        st, names, _ = _pj_resolve(query)
        mcut = re.search(r"(?:cut|カット|c)\s*0*(\d{1,3})\b", query, re.I)   # 接頭辞必須(『10月分』の数字を拾わない・Fable P1-4)
        cut_block, cut_clean = "", None
        if st == "unique" and mcut:
            cutn = int(mcut.group(1))                          # カット番号を int で持ち shotID側の数字と int 等値照合(c01⊄c001 の取りこぼし防止)
            items = json.load(open("/tmp/cal_projects.json")).get("items", [])
            pid = next((p.get("id") for p in items if str(p.get("name")) == names[0]), None)

            def _shot_num(t):
                m = re.search(r"c(?:ut)?0*(\d{1,3})", str(t.get("shotID") or t.get("shot_id") or t.get("name") or ""), re.I)
                return int(m.group(1)) if m else None
            try:
                ct = [t for t in _all_tasks() if t.get("project_id") == pid and _shot_num(t) == cutn]
            except Exception:
                ct = []
            if ct:
                cut = f"c{cutn:02d}"
                lines = [f"- {t.get('type') or t.get('name')}: {t.get('status_label') or t.get('status')}"
                         f"（{t.get('status_category')}）" for t in ct]
                has_active = any(_task_fb_active(t) for t in ct)   # 係争中FB=category単一ソース(承認/FBの区別のみstatus値・Fable P1-2)
                cut_clean = not has_active
                cut_block = (f"\n\n【{names[0]} {cut} の工程別status(検収/retake履歴の一次・Calendar)】\n" + "\n".join(lines))
        # ② framing: カットがclean(素通り承認)なら、そのカットに固有のFBスレッドは無い＝両事実併記が支配的。
        #    そうでなければ、スレッドが実際に retrieve できたか(_has_thread)で命令強度を機構選択(Fable P1-3)。
        if cut_clean is True:
            note = ("\n\n## 【FB/チェックログ=このカットは素通り承認(殿指示)】\n"
                    + cut_block +
                    "\n→ このカットの工程は全て承認(Dir_AP)/省略(Omit)/納品(Deliver)＝**差し戻し(retake)も係争中FBも無い**。"
                    "個別のFBやり取りスレッドは無い。**欠落調に『記録されていません』とだけ言うのでなく、"
                    "『個別のFBやり取りの記録は無いが、retakeなく承認/省略で通った(素通りで承認済み)』と両事実を併せて**伝えよ。"
                    "無い内容(スレッド本文)を在る風に創作するな。")
            return note
        _has_thread = False
        try:
            _has_thread = bool(casper_rag and casper_rag.search(query, k=4))
        except Exception:
            _has_thread = False
        if _has_thread:
            note = ("\n\n## 【FB/チェックログの読み方(殿指示・retrieve-then-render)】\n"
                    "**FBログの実体は『スレッドのテキスト』＝議事録／各人のactivity逐語／DMのやり取り、および対象カットの "
                    "status 遷移(retake記録)にある。** 下に注入された関連社内記録(RAG/activity/議事録)や、get_messages で読める"
                    "スレッド本文が、まさにFBログそのもの。\n"
                    "・**下に該当スレッドが注入されている時は『FB内容は記録されていない』と却下するな**。retrieved の議事録/"
                    "activity逐語・主体交代・色味/リテイクのやり取りを『FBログ』として具体的に提示せよ(却下してから中身を書く自己矛盾は禁止)。\n"
                    "・status 遷移(dir_ap=監督承認済 / wip=作業中 / retake・差し戻し)＝検収と差し戻しの履歴。これも記録として述べよ。")
        else:
            note = ("\n\n## 【FB/チェックログの読み方(殿指示)】\n"
                    "この問いのFBログは『スレッド(議事録/activity/DM)＋対象カットのstatus遷移』にある。"
                    "**ただし今回、関連スレッドは retrieve できていない。status遷移のみを記録として述べ、"
                    "スレッド本文の中身は創作するな**(無い内容を在る風に書くのは禁止)。status で語れる範囲を述べ、"
                    "詳細が要るなら get_messages/議事録の確認を案内せよ。")
        if cut_block:                                          # 係争中FBのカット: statusを注入し、retrievedスレッドのやり取りを提示させる
            note += cut_block + ("\n→ このカットは係争中のFB/確認(QC_FB/Dir_WT/WIP等)を含む。"
                                 "retrieved スレッドのやり取りをFBとして具体的に提示せよ。")
        return note
    except Exception:
        return ""


# 現フェーズ締切・次回社内チェックの問いを検知(殿指示2026-07-10: Calendarのtask type/due/チェックタスクから導出可能)。
_PHASE_Q_RE = re.compile(
    r"(現(在の)?フェーズ|今のフェーズ|フェーズ.{0,4}(締切|締め切り|期限|末|終|いつ)|"
    r"次回.{0,6}(チェック|社内|確認|レビュー|提出)|社内チェック|チェック日|チェック.{0,4}(いつ|日)|"
    r"(現|今)(の|)?(工程|フェーズ).{0,6}(締|期限|末|いつ)|(工程|フェーズ).{0,4}(締切|締め切り|期限))", re.I)

_CHECK_NAME_RE = re.compile(r"チェック|確認|提出|レビュー|review|check|社内", re.I)


def phase_schedule_digest(query):
    """【現フェーズ締切・次回社内チェック=retrieve-then-derive(殿指示2026-07-10)】milestone登録は不要——
    タスクの type(工程)＋due_date で工程別期日を、チェック/提出タスク名で社内チェック日を機構が導出し注入。
    プロジェクト end_date(最終納期)を『現フェーズ締切』と誤認するのを断つ。フェーズ/チェック問い＋unique PJでなければ空。"""
    try:
        if not query or not _PHASE_Q_RE.search(query):
            return ""
        st, names, _ = _pj_resolve(query)
        if st != "unique":
            return ""
        nm = names[0]
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        pid = next((p.get("id") for p in items if str(p.get("name")) == nm), None)
        tks = [t for t in _all_tasks() if t.get("project_id") == pid]
        if not tks:
            return ""
        today = datetime.date.today().isoformat()
        import collections
        bp = collections.defaultdict(lambda: {"n": 0, "inc": 0, "moving": 0, "dues": []})
        for t in tks:
            ph = t.get("type") or "?"
            d = bp[ph]
            d["n"] += 1
            if _task_open(t):                                  # 残務(category単一ソース・Fable P1-2)
                d["inc"] += 1
            if _task_is_moving(t):                             # 実際に動いている(category==in_progress)
                d["moving"] += 1
            if t.get("due_date"):
                d["dues"].append(str(t["due_date"])[:10])
        # 現フェーズ導出(Fable P1-1): ①wip(実際に動いている)工程が真の現フェーズ→複数なら締切最先。
        # ②wipが無ければ未完工程のうち"最先"(min max-due)=次にやるべき工程。compが常に未完だから最後尾を取る誤りを断つ。
        rows = []
        moving_ph, open_ph = [], []
        for ph, d in sorted(bp.items(), key=lambda x: (max(x[1]["dues"]) if x[1]["dues"] else "")):
            if ph == "?" or not d["dues"]:
                continue
            mx = max(d["dues"])
            rows.append(f"- {ph}: 期日 {min(d['dues'])}〜{mx}（全{d['n']}・未完{d['inc']}・進行中{d['moving']}）")
            if d["moving"] > 0:
                moving_ph.append((ph, mx))
            if d["inc"] > 0:
                open_ph.append((ph, mx))
        cur = (moving_ph[0] if moving_ph else (open_ph[0] if open_ph else None))   # wip優先→無ければ未完の最先
        # チェック/提出タスク(名前パターン)を期日順に、今日以降=次回
        checks = sorted([(str(t.get("due_date") or "")[:10], t.get("name") or t.get("title") or "", t.get("status") or "")
                         for t in tks if _CHECK_NAME_RE.search(t.get("name") or t.get("title") or "")],
                        key=lambda x: x[0])
        nextchk = next((c for c in checks if c[0] and c[0] >= today), None)
        chk_lines = "\n".join(f"- {c[0]} {c[1]}（{c[2]}）" for c in checks) or "-（チェック/提出と判る名のタスクは無し）"
        _basis = "（進行中の工程）" if moving_ph else "（着手前/停滞中の最先の未完工程）"
        derived = (f"→ **現フェーズ = {cur[0]}{_basis}・そのフェーズ締切 = {cur[1]}**" if cur else "→ 未完の工程が無い（全工程 完了扱い）") + \
                  (f"／**次回の社内チェック/提出 = {nextchk[0]} {nextchk[1]}**" if nextchk else
                   f"／次回の社内チェック予定は無し（直近は {checks[-1][0]} {checks[-1][1]}）" if checks and checks[-1][0] else "")
        return (f"\n\n## 【{nm} のフェーズ/チェック=Calendarのtask(type/due)から機構導出・本日{today}】\n"
                "**プロジェクトの end_date(最終納期)を『現フェーズ締切』と混同するな。工程(type)別の期日で答えよ**:\n"
                "工程別の期日:\n" + "\n".join(rows) +
                "\nチェック/提出タスク(期日順):\n" + chk_lines +
                "\n" + derived +
                "\n**上の導出結果を根拠に、現フェーズの締切と次回社内チェック日を具体的な日付で答えよ(『登録されていない』で逃げるな)。**")
    except Exception:
        return ""


# NINA/撮影の機材・ギアリストの問いを検知(殿指示2026-07-10)。機材語＋NINA/LED/撮影文脈のAND条件で発火。
_GEAR_Q_RE = re.compile(
    r"(ギアリスト|機材|機器|装置|セットアップ|(gear|equipment).?list|"
    r"(撮影|現場|設営|施工|セット).{0,6}(必要|準備|道具|もの|物))", re.I)


def gear_digest(query):
    """【NINA機材=retrieve-then-render(殿指示2026-07-10)】機材/ギアリストの問いに、ops_spatial_tech.md の
    機材節(デバイス/制御スペック＋技術スタック)を決定的に注入し、qwenが一般知識で製品名(Canon等)を上乗せするのを断つ。
    撮影機材はiPhone/insta360/depthであってCanon等ではない=vault記載に無い機材を足させない。機材問い＋NINA文脈でなければ空。"""
    try:
        if not query or not _GEAR_Q_RE.search(query):
            return ""
        # Fable P2: 誤ドメイン注入回避——NINA/空間演出系の固有文脈に限定(汎用の『撮影機材』にNINA機材を真実源として被せない)
        if not (re.search(r"(nina|ニーナ|art-?net|aurora|LED|空間演出|プロジェクションマッピング|プロマッピング|ドローンショー)", query, re.I)
                or _pj_resolve(query)[0] == "unique"):
            return ""
        p = os.path.join(HERE, "..", "vault", "30_culture_rules", "ops_spatial_tech.md")
        txt = open(p, encoding="utf-8").read()
        want = ("デバイス/制御スペック", "技術スタックまとめ")
        secs = re.split(r"(?m)^(?=#{2,3} )", txt)
        picked = [s.strip() for s in secs if any(w in s.split("\n", 1)[0] for w in want)]
        if not picked:
            if casper_trace:                                   # 見出し改名で節が拾えぬ=黙って消えずfail-loud(Fable P2)
                try: casper_trace.emit({"warn": "gear_digest: sections not found in ops_spatial_tech.md", "want": list(want)})
                except Exception: pass
            return ""
        body = "\n\n".join(picked)[:2600]
        # 製品名はコードに書かない(vaultが真実源・掟)。指示は「上記vault記載外の製品名を足すな」の汎用形のみ(Fable P2)。
        return ("\n\n## 【NINA/空間演出 機材の真実源(vault: ops_spatial_tech.md・確定)】\n"
                + body +
                "\n──\n**機材リストは上記vault記載の機材だけで答えよ。ここに載っていない製品名(カメラ/PC/スイッチ等)を"
                "一般知識で補完・上乗せするな(=捏造)。** 正典の詳細ファイル(SMB/X:等)がある旨は添えてよいが、機材の実体は上記が真実源。")
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
    r"wip.{0,6}タスク|タスク.{0,6}(動いて|進行中|稼働中)|"
    r"作業(されて|中|して|進).{0,4}(タスク|もの|案件)|タスク.{0,8}作業(され|中|して|進)|"
    r"(今日|本日|今).{0,8}(何|どんな|どの).{0,6}(タスク|作業|案件)|何.{0,6}(作業|タスク).{0,6}(され|進行|中))", re.I)


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
        _pjs = json.load(open("/tmp/cal_projects.json")).get("items", [])
        pm = {p.get("id"): p.get("name") for p in _pjs}
        due_m = {p.get("id"): str(p.get("end_date") or "")[:10] for p in _pjs}   # ③ 期限ファセット
        st_m = {p.get("id"): p.get("status") for p in _pjs}                      # PJ status(超過判定用)
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])}
        except Exception:
            um = {}
        import collections
        _today = datetime.date.today()
        byp = collections.defaultdict(list)
        for t in act:
            byp[t.get("project_id")].append(t)
        lines = []
        for pid, ts in sorted(byp.items(), key=lambda x: -len(x[1])):
            nm = pm.get(pid, pid or "?")
            who_names = sorted({um.get(t.get("assigned_to"), "未割当") for t in ts})
            due = due_m.get(pid, "")
            _dn = _due_note_c(due, st_m.get(pid), _today)      # 完了PJの過去納期を超過表示しない(単一機構)
            od = f"（{_dn}）" if _dn else ""
            lines.append(f"- **{nm}**: {len(ts)}件 | 担当 {', '.join(who_names[:6])} | 締切 {due or '—'}{od}")
        return (f"\n\n## 【現在進行中(wip/工程)のタスク一覧(Calendar・確定)】\n"
                f"全プロジェクトで進行中のタスクは計 {len(act)}件。**この一覧を根拠に答えよ。"
                "get_today_tasks(本日締切のみ)や特定PJ(marukome等)に狭めず、全PJの進行中を示せ。"
                "『動いているタスク』の問いには本一覧が答え(本日締切とは別物)。"
                "**③提示: 曖昧な問い(『タスクは?』等)には件数・担当・締切を1つの表で併記し、"
                "推測で1属性に絞るな。表の後に『期限順で見ますか/担当別に束ねますか』と切り口を一言添えよ**:\n"
                + "\n".join(lines))
    except Exception:
        return ""


# ── 派生事実: 「空いている人は誰か」= 実務担当者 ∖ wip割当者。集合差は弱モデルに解かせず機構が確定(Fable処方1) ──
_AVAIL_Q_RE = re.compile(
    r"((空|あ)い(て|た).{0,6}(人|メンバー|アーティスト|スタッフ|アニメーター|作業者|誰|だれ)|"
    r"(人|メンバー|アーティスト|スタッフ|アニメーター|作業者).{0,8}(空|あ)い(て|た)|"
    r"手(が|の)?空(い|き)|手空き|"
    r"アサイン(が|は)?(され|されて)?(いない|ない|無い|てない)|"
    r"(稼働|予定|タスク).{0,6}(が|は)?(空|無|なし|入って(い)?ない|余裕)|"
    r"(誰|だれ|どの.{0,4}(人|メンバー|アーティスト)).{0,10}(使え|手伝|回せ|余裕|暇|空)|"
    r"(フォロー|ヘルプ|サポート|手伝|助け|替わ|代わ|巻き取|カバー|応援)(に|を|で|の)?.{0,12}(入れ|できる|可能|人|誰|メンバー|アーティスト|いる|回せ|頼め|ある)|"
    r"(人|誰|だれ|メンバー|アーティスト|手).{0,10}(フォロー|ヘルプ|手伝|助け|カバー|応援|余っ))", re.I)


def availability_digest(query):
    """【派生事実=空き人材】実務担当者(過去に1度でもタスク割当のある者)のうち、現在 wip 割当0 の者を『空き』として
    機構が集合差で確定し注入(集合演算は弱モデルに委ねず機構が算出=Fable処方1)。『空き』の定義(=wip0)を明示し、
    休暇/外部予定は未接続である境界も可視化(真空を推測で埋めさせない)。空き人材の問いでなければ空。"""
    try:
        if not query or not _AVAIL_Q_RE.search(query) or _looks_like_action(query):
            return ""                                    # action意図(『連絡を入れて』等)には空き集計を被せない(Fable P2)
        tasks = _all_tasks()
        if not tasks:
            return ""
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])
                  if u.get("is_active", True)}          # Fable P2: 在籍中(is_active)のみ=退職者を『空き』に出さない
        except Exception:
            um = {}
        try:                                             # Fable P2: 母集団を online PJ の担当に限定(過去PJだけの外部/退職者を除く)
            online_pids = {p.get("id") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])
                           if str(p.get("display_status") or "online") == "online"}
        except Exception:
            online_pids = None
        import collections
        wip_by = collections.defaultdict(list)
        workers = set()
        for t in tasks:
            a = t.get("assigned_to")
            if not a or (um and a not in um):            # 現行の在籍ユーザーに限る
                continue
            if online_pids is not None and t.get("project_id") not in online_pids:
                continue                                 # online PJ の担当のみを実務担当母集団に
            workers.add(a)
            if _task_is_moving(t):
                wip_by[a].append(t)
        if not workers:
            return ""
        free = sorted(um.get(w, w) for w in workers if not wip_by.get(w))
        loaded = sorted(((um.get(w, w), len(wip_by[w])) for w in workers if wip_by.get(w)), key=lambda x: -x[1])
        free_txt = ("、".join(free) if free else "（現在 wip 割当0 の担当者はいません）")
        load_txt = "、".join(f"{nm}({n}件)" for nm, n in loaded[:12])
        return ("\n\n## 【空き人材=機構が集合差で確定(Calendar・確定)】\n"
                f"実務担当者(過去にタスク割当のある者) 計{len(workers)}名のうち、"
                f"**現在 進行中(wip)タスクを1件も持たぬ=『空き』は {len(free)}名: {free_txt}**。\n"
                f"（参考・稼働中の負荷: {load_txt}）\n"
                "**この確定結果を根拠に、空きの人名を具体的に答えよ。『どの切り口で見ますか』のメニューや"
                "『監視機能はない』で逃げて答えを出さぬのは禁止。答えは上に在る。**\n"
                "・『空き』の定義は『wip 割当0』の意。有給/休暇/外部予定/稼働率は真実源に未接続ゆえ含まぬ"
                "(この境界は、答えを出した上でなら添えてよい)。")
    except Exception:
        return ""


# ── PJ名の表記ゆれ耐性(カタカナ⇄ローマ字): 「マルコメ」が正規名「marukome」と一致せず迷子になる綻びの汎用解 ──
_KANA2ROMA = {
    'ア': 'a', 'イ': 'i', 'ウ': 'u', 'エ': 'e', 'オ': 'o', 'カ': 'ka', 'キ': 'ki', 'ク': 'ku', 'ケ': 'ke', 'コ': 'ko',
    'サ': 'sa', 'シ': 'shi', 'ス': 'su', 'セ': 'se', 'ソ': 'so', 'タ': 'ta', 'チ': 'chi', 'ツ': 'tsu', 'テ': 'te', 'ト': 'to',
    'ナ': 'na', 'ニ': 'ni', 'ヌ': 'nu', 'ネ': 'ne', 'ノ': 'no', 'ハ': 'ha', 'ヒ': 'hi', 'フ': 'fu', 'ヘ': 'he', 'ホ': 'ho',
    'マ': 'ma', 'ミ': 'mi', 'ム': 'mu', 'メ': 'me', 'モ': 'mo', 'ヤ': 'ya', 'ユ': 'yu', 'ヨ': 'yo',
    'ラ': 'ra', 'リ': 'ri', 'ル': 'ru', 'レ': 're', 'ロ': 'ro', 'ワ': 'wa', 'ヲ': 'wo', 'ン': 'n',
    'ガ': 'ga', 'ギ': 'gi', 'グ': 'gu', 'ゲ': 'ge', 'ゴ': 'go', 'ザ': 'za', 'ジ': 'ji', 'ズ': 'zu', 'ゼ': 'ze', 'ゾ': 'zo',
    'ダ': 'da', 'ヂ': 'ji', 'ヅ': 'zu', 'デ': 'de', 'ド': 'do', 'バ': 'ba', 'ビ': 'bi', 'ブ': 'bu', 'ベ': 'be', 'ボ': 'bo',
    'パ': 'pa', 'ピ': 'pi', 'プ': 'pu', 'ペ': 'pe', 'ポ': 'po', 'ヴ': 'vu', 'ー': '',
}
_KANA_SMALL = {'ャ': 'ya', 'ュ': 'yu', 'ョ': 'yo'}


def _kana_to_romaji(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ''
        if c == 'ッ':                                    # 促音→次子音を重ねる(簡易にスキップ)
            i += 1; continue
        if nxt in _KANA_SMALL and c in _KANA2ROMA:       # 拗音(キャ等)
            base = _KANA2ROMA[c]
            out.append((base[:-1] if base.endswith('i') else base) + _KANA_SMALL[nxt]); i += 2; continue
        out.append(_KANA2ROMA.get(c, c)); i += 1
    return ''.join(out)


def _translit_kana_runs(q):
    return re.sub(r'[゠-ヿ]+', lambda m: _kana_to_romaji(m.group(0)), q or "")


def _canonical(s):
    """正準スケルトン(Fable): 両側を同じ空間へ射影し、カタカナ⇄ローマ字・ヘボン/訓令・長音/促音/記号の揺れを吸収。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = _translit_kana_runs(s)                            # カタカナ→ローマ字(ー は除去済)
    for a, b in [("shi", "si"), ("chi", "ti"), ("tsu", "tu"), ("sha", "sya"), ("shu", "syu"),
                 ("sho", "syo"), ("cha", "tya"), ("chu", "tyu"), ("cho", "tyo"), ("ji", "zi"), ("fu", "hu")]:
        s = s.replace(a, b)                               # ヘボン→訓令に寄せる
    s = re.sub(r"[\s_\-・,、。]", "", s)                    # 記号/空白除去
    s = re.sub(r"(.)\1+", r"\1", s)                       # 連続同字つぶし(促音kk→k・長音aa→a)
    return s


_PJ_ALIAS = {"mtime": 0.0, "idx": {}}                     # canonical -> [正規名] (同一スケルトンに複数=衝突→ambiguous)


def _pj_index():
    """online PJ名 → canonical 別名索引を cal_projects から導出(オフライン・mtimeキャッシュ・検査可能)。"""
    try:
        m = os.path.getmtime("/tmp/cal_projects.json")
    except Exception:
        return _PJ_ALIAS
    if m == _PJ_ALIAS["mtime"] and _PJ_ALIAS["idx"]:
        return _PJ_ALIAS
    idx = {}
    try:
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
    except Exception:
        items = []
    for p in items:
        if str(p.get("display_status") or "online") != "online":
            continue
        nm = str(p.get("name") or "")
        if len(nm) < 3:
            continue
        can = _canonical(nm)
        if len(can) < 3:
            continue
        idx.setdefault(can, [])
        if nm not in idx[can]:
            idx[can].append(nm)
    _PJ_ALIAS.update({"mtime": m, "idx": idx})
    return _PJ_ALIAS


def _pj_resolve(query):
    """クエリから online PJ を解決(Fable 3値)。返り (status, names, path)。
    status='unique'|'ambiguous'|'none'。閉集合(online PJ)照合ゆえ names は真実源の部分集合(構成上保証)。"""
    q = query or ""
    idx = _pj_index()["idx"]
    qcan = _canonical(q)
    hits = []
    for can, names in idx.items():
        if any(nm in q for nm in names) or (len(can) >= 3 and can in qcan):   # 生一致 or 正準スケルトン一致
            hits += names
    hits = list(dict.fromkeys(hits))
    if not hits:
        return ("none", [], None)
    exact = [nm for nm in hits if nm in q]               # 決定則: 完全(生)一致 > 部分/スケルトン一致
    if len(exact) == 1:
        return ("unique", exact, "raw")
    if len(hits) == 1:
        return ("unique", hits, "skeleton")
    return ("ambiguous", hits, "multi")


def _match_online_pj(query):
    """後方互換: 解決した online PJ 名(unique 時のみ1件)。曖昧/不在は空(=呼び側で3値処理)。"""
    st, names, _ = _pj_resolve(query)
    return names if st == "unique" else []


_PJ_TASK_RE = re.compile(r"タスク.{0,8}(見せ|教え|一覧|リスト|出し|表示|見たい|ある|状況|ください|くれ|どうなって|進捗)|"
                         r"(見せ|教え|一覧|表示|出し).{0,6}タスク|どんな.{0,4}タスク", re.I)


def _pj_near_candidates(query, k=4):
    """名前解決0件だが名前らしきトークンがある時の近傍候補。候補生成のみ・自動解決に使わない(Fable)。
    (a)クエリの名前トークンが PJ正準名の前置(部分名『コンバトラーV』等) (b)bigram重なり(打ち間違い『ゼニス』等)。"""
    def _bg(s):
        return {s[i:i + 2] for i in range(len(s) - 1)}
    qcan = _canonical(query)
    qb = _bg(qcan)
    toks = [_canonical(t) for t in re.findall(r'[゠-ヿA-Za-z0-9]{2,}', query or "")]
    toks = [t for t in toks if len(t) >= 4]
    scored = {}
    for can, names in _pj_index()["idx"].items():
        nm = names[0]
        sc = 0.0
        if any(can.startswith(t) or t.startswith(can) for t in toks):   # (a)前置一致=部分名
            sc = 1.0
        elif qb and _bg(can):
            ov = len(qb & _bg(can)) / len(_bg(can))                      # (b)bigram重なり
            if ov >= 0.3:
                sc = ov
        if sc:
            scored[nm] = max(scored.get(nm, 0), sc)
    return [nm for nm, _ in sorted(scored.items(), key=lambda x: -x[1])[:k]]


def _pj_task_choices(query):
    """特定PJのタスク要求だが名前解決が unique でない時、候補PJを選択カードで提示(無言None落ちさせない・3値のnone/ambiguous出口)。"""
    if not _PJ_TASK_RE.search(query or ""):
        return None
    st, names, _ = _pj_resolve(query)
    if st == "unique":
        return None                                       # unique は table_card が拾う
    if st == "ambiguous":
        cands, prompt = names, "どのプロジェクトのタスクでしょう？下から選んでくだされ。"
    else:                                                 # none: 名前らしきトークンがある時のみ近傍候補
        if not re.search(r"[゠-ヿA-Za-z]{2,}", query or ""):
            return None                                   # 名前らしきものが無い=一般タスク問い→通常経路へ
        cands = _pj_near_candidates(query)
        if not cands:
            return None
        prompt = "そのプロジェクト名に一致がございませぬ。もしやこちらでは？（違えば具体名で仰せを）"
    opts = [{"id": f"pjtask_{nm}", "label": f"{nm} のタスク", "preview": f"{nm} の未完了タスク一覧を表示",
             "say": f"{nm}のタスクを見せて"} for nm in cands[:6]]
    return {"prompt": prompt, "options": opts}


# ④ table card(Fable設計): 表は機構が真実源からテンプレ描画=LLMは表を書かず継ぎ目の修辞だけ。
# 截ち切れ・転写捏造・全件ダンプが構造的に消える。切り口(並べ替え)はクライアントのチップで(LLM再呼出不要)。
_PROJ_LIST_RE = re.compile(r"(動いて(る|いる)|進行中|稼働中|全.{0,2}(プロジェクト|PJ|案件)|"
                           r"(プロジェクト|PJ|案件).{0,8}(一覧|教え|どれ|ある|全部)|"
                           r"(納期|締切|遅れ|遅延|超過).{0,10}(プロジェクト|PJ|案件|一覧|もの|の))", re.I)

# 停滞FB/確認の"一覧"意図(通知の『停滞FB N件』の実体を見せる)。進捗の真実源はCalendar(vault/legacyでなく)。
_STALL_LIST_RE = re.compile(
    r"(停滞|滞留|止ま(って|った)|溜ま(って|った)|たまって).{0,8}(FB|ＦＢ|確認|チェック|検収|レビュー)|"
    r"(FB|ＦＢ|確認|チェック|検収|レビュー).{0,8}(停滞|滞留|止ま|溜ま|たまって)|"
    r"停滞.{0,6}\d+\s*件", re.I)


def _table_card(query, who):
    """一覧意図(進行中タスク/PJ)を機構で表カード化。返り=table dict or None。個別PJ照会(marukomeは?)は散文ゆえ対象外。"""
    q = query or ""
    try:
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
    except Exception:
        items = []
    online = [p for p in items if str(p.get("display_status") or "online") == "online"]
    _name_hit = bool(_match_online_pj(q))                # 表記ゆれ耐性の名前解決器へ統一(生substring照合を残さない)
    today = datetime.date.today()

    def _due_note(due, status="", scope="pj"):
        # 納期状況は status を見て機構が確定(完了PJの過去納期を超過表示しない・単一ソース _due_note_c)。
        return _due_note_c(due, status, today, scope)

    # ⓪-a 停滞FB/確認の一覧(通知の『停滞FB N件』の実体) — 進捗の真実源=Calendar からのみ描く。
    #    vault/legacy_score(過去DBM2 2022)を拾わせない(殿指摘2026-07-13: 進捗はCalendarが全て/vaultに進捗は無い設計)。
    if _STALL_LIST_RE.search(q):
        try:
            import casper_notify
            _tasks = casper_notify._all_tasks()
        except Exception:
            _tasks = []
        if not _tasks:
            # Calendar取得失敗(API落ち/token失効等) → 「停滞ゼロ」と【確定】を名乗って断言しない(Fable掟3=機構の嘘の禁)。
            # 取得失敗とゼロを同じ出口に流さず、通常経路へ落として正直に扱わせる。
            return None
        stalled = casper_notify._stalled_fb(_tasks, today.isoformat())
        pmn = {p.get("id"): p.get("name") for p in items}
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])}
        except Exception:
            um = {}
        stalled = sorted(stalled, key=lambda s: s.get("since") or "9999")   # 古い(=長期停滞)順
        rows = [[s.get("shot") or "—", pmn.get(s.get("project_id"), "—"),
                 s.get("type") or "—", um.get(s.get("assigned_to"), "未割当"),
                 s.get("status_label") or s.get("status") or "", f"{s.get('days','')}日"]
                for s in stalled]
        if not rows:
            return {"title": "停滞中のFB/確認", "columns": ["カット", "プロジェクト", "工程", "担当", "状態", "停滞"],
                    "rows": [], "sortable": True, "numeric_cols": [],
                    "footer": f"本日{today}時点、3日以上動いていない確認待ちはありません（Calendar確定）。"}
        return {"title": f"停滞中のFB/確認（{len(rows)}件・3日以上停滞）",
                "columns": ["カット", "プロジェクト", "工程", "担当", "状態", "停滞"],
                "rows": rows, "sortable": True, "numeric_cols": [],
                "footer": "Calendar 確定データ（qc_fb/dir_wt/qc等の確認待ちで3日以上更新なし）。"
                          "過去のレガシー記録(2022 DBM2/legacy_score)は進捗に含めません。"}

    # ⓪ 特定PJのタスク一覧(『マルコメのタスク見せて』等) — 名前解決器で unique に解けた時のみ表を描く
    #    (曖昧/不在は None を返し、呼び側が選択カード/近傍候補で拾う=無言None落ちさせない・Fable)
    if _PJ_TASK_RE.search(q):
        st, _pjs, _path = _pj_resolve(q)
        if st == "unique":
            nm = _pjs[0]
            pid = next((p.get("id") for p in online if p.get("name") == nm), None)
            try:
                tks = [t for t in _all_tasks() if t.get("project_id") == pid]
            except Exception:
                tks = []
            if tks:
                try:
                    um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                          for u in casper_tools._get("/users?limit=200").get("items", [])}
                except Exception:
                    um = {}
                act = [t for t in tks if _task_is_moving(t)]   # 完了判定はハードコードせず _task_is_moving に寄せる(API単一ソース)
                shown = act or tks                        # 未完了優先・無ければ全件
                shown = sorted(shown, key=lambda t: str(t.get("due_date") or "9999"))
                rows = []
                for t in shown[:60]:
                    due = str(t.get("due_date") or "")[:10]
                    rows.append([t.get("name") or t.get("title") or "?", t.get("type") or "",
                                 um.get(t.get("assigned_to"), "未割当"),
                                 t.get("status_label") or t.get("status") or "", due, _due_note(due, t.get("status"), "task")])
                _hidden = (len(tks) - len(act)) if act else 0    # footerの嘘を断つ: 全件表示時は非表示0
                _foot = "Calendar 確定データ。列見出しクリックで並べ替え。"
                if _hidden:
                    _foot += f" 完了 {_hidden}件は非表示。"
                if len(shown) > 60:
                    _foot += "（多いため上位60件）"
                _n = len(act) if act else len(tks)
                _tl = (f"{nm} のタスク（未完了 {len(act)}件 / 全{len(tks)}件）" if act
                       else f"{nm} のタスク（全{len(tks)}件）")
                return {"title": _tl, "columns": ["タスク", "工程", "担当", "状態", "締切", ""],
                        "rows": rows, "sortable": True, "numeric_cols": [], "footer": _foot}

    # ① 進行中タスク一覧(件数/担当/締切) — retrieve-then-render を表カードに
    if _ACTIVE_TASK_Q_RE.search(q):
        try:
            tasks = [t for t in _all_tasks() if _task_is_moving(t)]
        except Exception:
            tasks = []
        if not tasks:
            return None
        pm = {p.get("id"): p.get("name") for p in items}
        due_m = {p.get("id"): str(p.get("end_date") or "")[:10] for p in items}
        st_m = {p.get("id"): p.get("status") for p in items}     # PJ status(超過判定にstatusを渡す為)
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])}
        except Exception:
            um = {}
        import collections
        byp = collections.defaultdict(list)
        for t in tasks:
            byp[t.get("project_id")].append(t)
        rows = []
        for pid, ts in sorted(byp.items(), key=lambda x: -len(x[1])):
            who_names = sorted({um.get(t.get("assigned_to"), "未割当") for t in ts})
            due = due_m.get(pid, "")
            rows.append([pm.get(pid, pid or "?"), len(ts), ", ".join(who_names[:6]), due, _due_note(due, st_m.get(pid))])
        return {"title": f"進行中タスク（全社 計{len(tasks)}件）", "columns": ["プロジェクト", "件数", "担当", "締切", "状況"],
                "rows": rows, "sortable": True, "numeric_cols": [1], "name_col": 0,
                "footer": "Calendar 確定データ。列見出しクリックで並べ替え。"}

    # ② 進行中PJ一覧(状態/締切) — リスト意図の時のみ(個別PJ名照会は除外)
    if _PROJ_LIST_RE.search(q) and not _name_hit and online:
        rows = []
        for p in online:
            due = str(p.get("end_date") or "")[:10]
            rows.append([p.get("name"), p.get("status") or "", due, _due_note(due, p.get("status"))])
        return {"title": f"進行中プロジェクト（{len(online)}件）", "columns": ["プロジェクト", "状態", "締切", "状況"],
                "rows": rows, "sortable": True, "numeric_cols": [], "name_col": 0,
                "footer": "Calendar 確定データ。列見出しクリックで並べ替え。"}

    # ③ 空き人材(人軸の負荷表) — 「空いている人は?」等。集合差は availability_digest と同源(機構確定・Fable処方1)
    #    action意図(『◯◯に連絡を入れて』等)の時は抑止=アクション要求に表カードを被せない(Fable P2)
    if _AVAIL_Q_RE.search(q) and not _looks_like_action(q):
        try:
            tasks = _all_tasks()
        except Exception:
            tasks = []
        if tasks:
            try:
                um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                      for u in casper_tools._get("/users?limit=200").get("items", [])
                      if u.get("is_active", True)}       # 在籍中のみ(Fable P2)
            except Exception:
                um = {}
            try:
                online_pids = {p.get("id") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])
                               if str(p.get("display_status") or "online") == "online"}
            except Exception:
                online_pids = None
            import collections
            wip_by = collections.defaultdict(list)
            workers = set()
            for t in tasks:
                a = t.get("assigned_to")
                if not a or (um and a not in um):
                    continue
                if online_pids is not None and t.get("project_id") not in online_pids:
                    continue
                workers.add(a)
                if _task_is_moving(t):
                    wip_by[a].append(t)
            if workers:
                rows = []
                for w in workers:
                    ts = wip_by.get(w, [])
                    nd = min((str(t.get("due_date") or "9999")[:10] for t in ts), default="")
                    rows.append([um.get(w, w), len(ts), (nd if nd and nd != "9999" else "—"),
                                 "🟢空き" if not ts else "稼働中"])
                rows.sort(key=lambda r: (r[1], str(r[0])))   # 空き(0件)を上へ
                nfree = sum(1 for r in rows if r[1] == 0)
                return {"title": f"メンバー稼働状況（空き {nfree}名 / 実務担当 {len(rows)}名）",
                        "columns": ["メンバー", "進行中件数", "直近締切", "状況"],
                        "rows": rows, "sortable": True, "numeric_cols": [1],
                        "footer": "『空き』=進行中(wip)タスク0件の意。有給/休暇/外部予定は未接続。Calendar確定データ。"}
    return None


_TOOL_NARRATION_RE = re.compile(
    r"^\s*[`*_>-]*\s*(?:\d+[\.\)]\s*)?(calendar_lookup|search_vault|get_[a-z_]+|send_message|create_[a-z_]+|"
    r"update_task|bulk_[a-z_]+|import_[a-z_]+|vimeo_[a-z_]+|aurora_[a-z_]+|ai_import_parse)\s*\(.*$",
    re.I)


def _strip_tool_narration(text):
    """【出口・道具実況ガード(Fable処方2の副作用ゼロ版)】qwenがツールを"呼ばず"生の関数呼び構文だけを本文に
    書いて止まった時(例『calendar_lookup(...)』『search_vault(...)』のみで停止=答えゼロ)、その実況行を剥ぐ。
    実行はしない(副作用ゼロ)。剥いで空になれば呼び側が _pj_status_fallback へ落として救済する。"""
    try:
        if not text:
            return text
        # ```tool ... ``` フェンス除去
        t = re.sub(r"```(?:tool|json)?\s*(?:calendar_lookup|search_vault|get_[a-z_]+|send_message)[^`]*```", "", text, flags=re.I | re.S)
        kept = [ln for ln in t.splitlines() if not _TOOL_NARRATION_RE.match(ln)]
        out = "\n".join(kept)
        return re.sub(r"\n{3,}", "\n\n", out).strip()
    except Exception:
        return text


def _strip_name_gloss(text, sysadd, query):
    """【出口・gloss検問(Fable処方3)】応答中の"既知の実在PJ名"の直後の括弧展開『marukome（丸亀製麺）』は、
    括弧内が注入コンテキスト(sysadd)に無ければ推測=剥がす。閉集合(Calendarのonline PJ名のみ)照合ゆえ軽い。
    クエリのタイポ(maukome等)で名前解決がnoneでも、応答に現れた実在名を直接掴むので発火する。"""
    try:
        if not text:
            return text
        try:
            items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        except Exception:
            items = []
        names = sorted({str(p.get("name")) for p in items
                        if str(p.get("display_status") or "online") == "online" and len(str(p.get("name") or "")) >= 3},
                       key=len, reverse=True)                        # 長い名から(部分包含の取りこぼし防止)
        if not names:
            return text
        ctx = sysadd or ""
        out = text
        for nm in names:
            def _repl(m, _nm=nm):
                gloss = (m.group(1) or "").strip()
                return m.group(0) if (gloss and gloss in ctx) else _nm   # 文脈に実在=正当・無ければ推測展開を剥ぐ
            out = re.sub(re.escape(nm) + r"\s*[（(]([^）)]{1,40})[)）]", _repl, out)
        return out
    except Exception:
        return text


def _pj_status_fallback(query):
    """出口検問で全消し(qwenがナレーションだけ吐いた等)の救済: 問いが指す online PJ の状態を
    Calendarデータから決定的に答える(retrieve-then-render・LLM非依存)。無ければ空。"""
    try:
        st, names, _ = _pj_resolve(query)                # 名前解決器へ統一(生substring照合を残さない・Fable)
        if st != "unique":
            return ""
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        today = datetime.date.today().isoformat()
        for p in items:
            nm = str(p.get("name") or "")
            if nm == names[0] and str(p.get("display_status") or "online") == "online":
                due = str(p.get("end_date") or "")[:10]
                late = bool(due) and due < today and str(p.get("status") or "") not in ("completed", "done", "cancelled")
                line = f"**{nm}** の現状:\n・ステータス: {p.get('status')}"
                if due:
                    line += f"\n・納期: {due}" + ("（🔴 納期超過）" if late else "（予定内）")
                return line
        return ""
    except Exception:
        return ""


_FEWSHOT_BANK = os.path.join(HERE, "learn_bank.jsonl")
_FEWSHOT_USED = []                                  # 直近リクエストで注入した規則id(trace用・fewshot_used)


_DIGESTS_YAML = os.path.join(HERE, "digests.yaml")
_dg_cache = {"mtime": 0.0, "cfg": {}}


def _dg(name, text):
    """設定のデータ化(Fable5柱2): digests.yaml の enabled/budget を適用。本体ロジックはPython関数のまま、
    運用制御だけ外出し=暴れたdigestをデプロイ無しで止める/予算を回す。ホットリロード(mtime)。"""
    try:
        m = os.path.getmtime(_DIGESTS_YAML)
        if m != _dg_cache["mtime"]:
            import yaml
            _dg_cache["cfg"] = yaml.safe_load(open(_DIGESTS_YAML, encoding="utf-8")) or {}
            _dg_cache["mtime"] = m
    except Exception:
        pass
    cfg = _dg_cache["cfg"].get(name, {})
    if cfg.get("enabled") is False:                       # YAMLで無効化→注入ゼロ(即停止)
        return ""
    b = cfg.get("budget")
    t = text or ""
    if not (b and len(t) > b):
        return t
    # Fable M1: 予算切詰めは"レコード境界"で(文字尻切り=半端な事実を確定注入する罠の回避)。
    cut = t[:b]
    nl = cut.rfind("\n")                                  # 予算内の最後の改行=行(レコード)境界
    if nl > b * 0.5:                                      # 半分以上残るなら行境界で切る
        cut = cut[:nl]
    return cut.rstrip() + "\n…(予算により以下省略)"


def fewshot_digest(query):
    """【教訓の注入=flywheel柱1の出口(Fable5)】learn_bank の規則から、この問いに関連する上位3-5則を
    プロンプト末尾に注入し qwenの型を矯正する。全50則は入れず類似上位のみ(合計~400トークン上限)。
    人が触れた失敗から蒸留された規則ゆえ『使うほど型が矯正される』。"""
    global _FEWSHOT_USED
    _FEWSHOT_USED = []
    try:
        if not query or not os.path.exists(_FEWSHOT_BANK):
            return ""
        rules = []
        for ln in open(_FEWSHOT_BANK, encoding="utf-8"):
            if ln.strip():
                try:
                    rules.append(json.loads(ln))
                except Exception:
                    pass
        if not rules:
            return ""
        # 簡易関連度: 問いと規則(situation)の文字bigram重なり(日本語は空白無しゆえ語分割でなくn-gram)。閾値未満は注入ゼロ。
        def _bg(s):
            s = re.sub(r"[\s、。・！？]", "", str(s))
            return set(s[i:i + 2] for i in range(len(s) - 1))
        qset = _bg(query)
        scored = []
        for r in rules:
            rset = _bg(str(r.get("situation", "")) + str(r.get("lesson", "")))
            ov = len(qset & rset)
            if ov >= 3:                              # 閾値: bigram3個以上一致(無関係な規則を注入せぬ)
                scored.append((ov, r))
        scored.sort(key=lambda x: -x[0])
        top = [r for _, r in scored[:5]]
        if not top:
            return ""
        _FEWSHOT_USED = [r.get("id") for r in top]
        lines = "\n".join("・" + str(r.get("lesson", "")) for r in top)
        return ("\n\n## 【過去の教訓(必ず守れ・人の修正から学習)】\n"
                "同種の状況で以前 利用者に指摘・修正された点。今回もこれらを守って答えよ:\n" + lines)
    except Exception:
        return ""


_ATTN_Q_RE = re.compile(r"(気にかけ|今日の3件|気になる.{0,4}(点|件|こと|とこ|ところ)|滞留|"
                        r"承認待ち|下書き.{0,4}(何|どう|は|って)|上記.{0,8}(気に|件.{0,4}(どう|何|は)))", re.I)


def attention_digest(who, query):
    """【今日の3件(気にかけどころ)の実体注入】利用者が『気にかけどころ/今日の3件/上記の件』と言ったら、
    attention が先回りで拾った項目(滞留下書き/納期超過/未了)を対処つきで注入。ブリーフィングで示した項目を
    後から『どうしたらいい?』と問われても、vault検索等に迷子にならず正しく答えさせる(retrieve-then-render)。"""
    try:
        if not query or not _ATTN_Q_RE.search(query) or not who.get("uid"):
            return ""
        import attention as _att
        three = _att.today_three(who.get("uid"))
        if not three:
            return ""
        lines = [f"- [{c['kind']}] {c['title']} — {c['detail']}" for c in three]
        return ("\n\n## 【今日の3件(気にかけどころ)=先回りで拾った要対応・これが『上記の件』】\n"
                "利用者が『気にかけどころ/今日の3件/上記の2件』と言ったらこれを指す(vault議事録検索ではない)。"
                "各々の対処を案内せよ: draft=承認待ちの下書き→『承認で送信・却下で破棄できます。まとめて確認しますか?』と促す / "
                "overdue=納期超過PJ→状況確認や催促 / loop=未了の約束→催促の頃合いか。:\n" + "\n".join(lines))
    except Exception:
        return ""


def _attention_action_cards(who, query):
    """【Q4 attention→その場で片付く】今日の3件のうち overdue/loop を『選択カード』で提示(draftは①で承認カード浮上)。
    各項目に具体行動＋『今日は流す(snooze)』を付す(alert fatigue対策)。snoozeは__attn_snooze__ sentinel=say型で
    再投入→決定的handlerが attention.snooze を呼ぶ(副作用ゼロ)。DM/Calendar書込は必ずoutbox経由(原理②)。"""
    try:
        if not query or not _ATTN_Q_RE.search(query) or not who.get("uid"):
            return []
        import attention as _att
        cards = []
        for c in _att.today_three(who.get("uid")):
            ref = c.get("ref"); nm = c.get("title") or ""
            if c["kind"] == "overdue":
                cards.append({"prompt": f"🔴 **{nm}** — {c.get('detail','')}。どういたす？", "options": [
                    {"id": f"att_{ref}_remind", "label": "催促DMを起案", "preview": f"{nm} の担当へ進捗確認DMを下書き（送信は承認制）",
                     "say": f"{nm}の担当に進捗を確認するDMを作って"},
                    {"id": f"att_{ref}_snooze", "label": "今日は流す", "preview": "本日は非表示（明日また出ます）",
                     "say": f"__attn_snooze__ {ref} {nm}"}]})
            elif c["kind"] == "loop":
                cards.append({"prompt": f"🔗 **{nm}** — {c.get('detail','')}。どういたす？", "options": [
                    {"id": f"att_{ref}_remind", "label": "催促・確認DMを起案", "preview": f"{nm} の相手へ確認DMを下書き（送信は承認制）",
                     "say": f"{nm}の件で相手に確認するDMを作って"},
                    {"id": f"att_{ref}_snooze", "label": "今日は流す", "preview": "本日は非表示（明日また出ます）",
                     "say": f"__attn_snooze__ {ref} {nm}"}]})
        return cards
    except Exception:
        return []


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
        # Fable処方4b: trait は"主語の live 証拠と同梱でのみ"効かせる。各人物の現wip件数を機構で引き、
        # 0件なら『疑いの手がかり』でなく『現在の割当は無い』という確定事実として提示させる(無接地の疑い雛形化を断つ)。
        try:
            _tasks = _all_tasks()
        except Exception:
            _tasks = []
        lines = []
        for nm, traits in hits:
            uid = _uid_int(name_to_uid.get(nm))          # roster由来はstr→int正規化(assigned_toはint・Fable P0-1)
            wip = sum(1 for t in _tasks if t.get("assigned_to") == uid and _task_is_moving(t)) if uid is not None else None
            note = " / ".join((t.get("note") or "") for t in traits[:3])
            if wip == 0:
                lines.append(f"- {nm}: 【live: 現在 進行中(wip)の割当タスクは0件】癖『{note}』は参考だが、"
                             "今は動いている割当が無い＝この事実で答えよ(『未完了』『癖で作業中報告』等の独自ストーリーを創作するな)。")
            elif wip:
                lines.append(f"- {nm}: 【live: 進行中(wip)割当 {wip}件】癖『{note}』を踏まえ、状態は live/裏取りで確認せよ。")
            else:
                lines.append(f"- {nm}: 癖『{note}』(liveは未取得ゆえ状態断定はせず『未確認』と述べよ)。")
        return ("\n\n## 【人物の癖＋live証拠(Fable: traitは証拠と同梱でのみ効かせる)】\n"
                "この問いに関わる人物の既知の癖と、現時点のlive割当。**癖は読み方の手がかりであって結論ではない。"
                "上のlive事実を答えの根拠にせよ。liveに無い状態(未完了/完了/🔴)を癖から推し量って断定するな**:\n"
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


# Q3B(Fable): 常時注入すると弱モデルが無関係な問いに滲出させる「操作ガイド」節を、見出し名で条件注入化。
# (md linter が HTML コメントを剥がす為、インラインmarkerでなく既存の `## 見出し` を境界に使う=linter耐性)
_CTX_CONDITIONAL = [
    {"h": "Casper にできること",
     "kws": ["できること", "何ができ", "どうやっ", "どうすれ", "使い方", "手順", "やり方", "アップ", "アップロード",
             "動画", "ビデオ", "vimeo", "ヴィメオ", "探し", "探す", "見せ", "機能", "検索", "報告書", "議事録",
             "カット", "画像", "資料", "読み取", "読取", "添付", "dm", "ディーエム"]},
    {"h": "画像・動画の貼付ルール",
     "kws": ["画像", "動画", "貼付", "貼り", "貼る", "埋め込み", "埋込", "iframe", "vimeo", "ノート", "aurora",
             "base64", "img", "サムネ"]},
]
_ctx_cache = {"mtime": 0.0, "core": "", "sections": []}


def _load_context():
    """casper_context.md を core(常時注入)＋sections(キーワード条件注入)に分解(Q3B・Fable)。
    `## 見出し` で分割し、_CTX_CONDITIONAL に該当する節だけ『条件注入』へ回す。残りは core。mtimeホットリロード。"""
    ctx_path = os.path.join(HERE, "casper_context.md")
    try:
        m = os.path.getmtime(ctx_path)
    except Exception:
        return _ctx_cache
    if m == _ctx_cache["mtime"]:
        return _ctx_cache
    try:
        raw = open(ctx_path, encoding="utf-8").read()
    except Exception:
        raw = ""
    # `## ` (level-2見出し)で塊に分割。先頭の見出し前テキストは core。
    parts = re.split(r"(?m)^(?=## )", raw)
    core_chunks, sections = [], []
    for chunk in parts:
        head = chunk.split("\n", 1)[0].lstrip("# ").strip()
        cond = next((c for c in _CTX_CONDITIONAL if c["h"] in head), None)
        if cond:
            sections.append({"kws": [k.lower() for k in cond["kws"]], "body": chunk.strip()})
        else:
            core_chunks.append(chunk.strip())
    core = re.sub(r"\n{3,}", "\n\n", "\n\n".join(x for x in core_chunks if x)).strip()
    _ctx_cache.update({"mtime": m, "core": core, "sections": sections})
    return _ctx_cache


def context_sections_digest(query):
    """クエリのキーワードに合致する CTXSEC セクションだけを注入(動的注入・Vimeo混入の恒久解の入口壁)。"""
    q = (query or "").lower()
    if not q:
        return ""
    hits = [s["body"] for s in _load_context()["sections"] if any(k in q for k in s["kws"])]
    return ("\n\n" + "\n\n".join(hits)) if hits else ""


def _strip_context_echo(text, query):
    """【出口壁・echo検問(Q3B)】トリガーされていない条件注入セクション(Vimeo手順等)の特徴的な文言が応答に
    滲出したら、その行だけ落とす(常時注入をやめても弱モデルが記憶から漏らす保険)。クエリにkwが有る=正当ゆえ残す。"""
    if not text:
        return text
    q = (query or "").lower()
    _bare = lambda z: re.sub(r"[*_`]", "", z)              # md装飾を外して照合
    changed = False
    kept = text
    for s in _load_context()["sections"]:
        if any(k in q for k in s["kws"]):                  # このセクションはクエリに関係あり→正当・素通し
            continue
        keys = []
        for ln in s["body"].splitlines():
            frag = _bare(re.sub(r"^[-・\s>#]+", "", ln)).strip()
            key = re.split(r"[:：（(]", frag)[0].strip()
            if len(key) >= 8:                              # 8字以上の固有句=そのセクション固有の滲出シグネチャ
                keys.append(key)
        if not keys:
            continue
        out = []
        for line in kept.splitlines():
            if any(k in _bare(line) for k in keys):        # この行はセクション固有句を含む=滲出→落とす
                changed = True
                continue
            out.append(line)
        kept = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", kept).strip() if changed else text


def build_sys():
    """毎リクエストで社内ナレッジ digest (左脳+右脳) を読み込み system prompt に注入。"""
    ctx = _load_context()["core"]                          # 操作ガイド等の混入源は core から除外済(条件注入へ)
    today = datetime.date.today()
    wd = "月火水木金土日"[today.weekday()]
    datehdr = (f"【今日の日付】{today.isoformat()}（{wd}曜）。日数・遅延・締切は必ずこの日付を基準に計算せよ"
               "(自分の記憶の日付を使うな)。")
    tail = ("\n【回答の作法】記号や番号(A/B/C等)1文字だけで答えるな。必ず日本語の文で具体的に答えよ。"
            "数値(遅延日数等)はデータから計算して明示せよ。"
            "\n【提示の作法=分かりやすく・見やすく・使いやすく(全回答共通)】"
            "①ラベル併記: 一覧を出す時は人間可読な名前/タイトルを必ず添えよ。生ID・生URL・番号だけの羅列は禁止"
            "(元データにタイトルが在るなら必ず使う)。"
            "②厳選: 『代表的な/主な/おすすめ』等を問われたら全件を並べず要点を厳選し、残りは『全件も出せます』と誘導せよ。"
            "③冗長を括る: 表で全行が同じ値を繰り返すなら、その共通項は表の外に1度だけ書き、表の列からは外して行を軽くせよ"
            "(長い重複でスペースを浪費して途中で切れるのを防ぐ)。"
            "④曖昧なら併記: 問いがどの属性を指すか曖昧な時(例『タスクは?』=件数/担当/期限)は推測で1つに絞らず主要属性を併記せよ。"
            "『どの切り口で見ますか』等の切り口質問は、まず具体的な答え(人名/件数/事実)を出した"
            "『上で』のみ添えてよい。**答えの代わりにメニューや選択肢を出して止まるな**"
            "(データが注入されているのに『監視機能はない』『どれで見ますか』で逃げるのは禁止)。"
            "⑤締め: 一覧・表は最後まで出し切れ。多すぎる時は代表を見せ『続き/全件』への導線を必ず残せ(黙って途中で止めるな)。")
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


# ── DigestRegistry(Fable M1): 全digestを"1枚の宣言表"にし両バックエンド共通の単一ループで組む。
#    以前は claude_cli 経路と Ollama 経路に注入スタックが複製・ドリフトしていた(claude_cli側に entity/
#    availability/team_vocab/gear/phase_sched/fb_log/future_assign が欠落)。表を単一ソースにして根絶する。
#    各entry=(name, fn(who, query)->text)。attention も例外なく _dg(kill-switch/予算)下に置く。
_DIGEST_REGISTRY = [
    ("activity",         lambda who, q: activity_digest(who)),
    ("verify",           lambda who, q: verify_digest(who, q)),
    ("projects",         lambda who, q: projects_digest(q)),
    ("entity",           lambda who, q: entity_digest(q)),
    ("active_tasks",     lambda who, q: active_tasks_digest(q)),
    ("availability",     lambda who, q: availability_digest(q)),
    ("team_vocab",       lambda who, q: team_vocab_digest(q)),
    ("fewshot",          lambda who, q: fewshot_digest(q)),
    ("existence",        lambda who, q: existence_digest(who, q)),
    ("open_loop",        lambda who, q: open_loop_digest(who)),
    ("attention",        lambda who, q: attention_digest(who, q)),
    ("context_sections", lambda who, q: context_sections_digest(q)),
    ("calendar",         lambda who, q: calendar_digest(q)),
    ("traits",           lambda who, q: traits_digest(who, q)),
    ("meeting",          lambda who, q: meeting_digest(q)),
    ("shot_assignee",    lambda who, q: shot_assignee_digest(q)),
    ("image_asset",      lambda who, q: image_asset_digest(q)),
    ("portfolio",        lambda who, q: portfolio_digest(q)),
    ("gear",             lambda who, q: gear_digest(q)),
    ("phase_sched",      lambda who, q: phase_schedule_digest(q)),
    ("fb_log",           lambda who, q: fb_log_digest(q)),
    ("future_assign",    lambda who, q: future_assign_digest(q, who)),
    ("cross",            lambda who, q: cross_digest(q)),
]


# 全体トークン予算(Fable M1): 全digestが同時発火するとcontext飽和(lost in the middle)ゆえ、注入総量に上限を置く。
# 超過時は"接地/安全でない味付けdigest"から順に落とす(接地・検問系は絶対に落とさない)。落としたものはtraceに記録=silent cap禁止。
_DIGEST_CEILING = int(os.environ.get("CASPER_DIGEST_CEILING", "18000"))   # 注入ブロックの文字上限(≈ num_ctx 12288 の内数)
_TRIM_FIRST = ["activity", "context_sections", "calendar", "meeting", "cross",   # 低優先(先に落とす)…
               "open_loop", "team_vocab", "portfolio", "user_profile", "fewshot"]
#  ↑に無い digest(verify/projects/entity/active_tasks/availability/existence/gear/phase_sched/fb_log/
#    future_assign/traits/shot_assignee/image_asset/attention)は接地・安全の一次ゆえ予算超過でも落とさない。


def build_digests(who, q, trace=None):
    """全digestを単一の宣言表(_DIGEST_REGISTRY)から順に組む(両バックエンド共通・Fable M1)。
    各digestは _dg(kill-switch/予算)を通す。draftは条件付き。全体上限を超えたら低優先から落とす。
    trace(dict)を渡すと発火/切詰めを記録(観測=M2の土台)。"""
    pieces = [("user_profile", _dg("user_profile", user_profile_digest(who)))]
    for name, fn in _DIGEST_REGISTRY:
        try:
            txt = fn(who, q)
        except Exception:
            txt = ""
        if txt:
            piece = _dg(name, txt)
            if piece:
                pieces.append((name, piece))
    if _DRAFT_SURFACE_RE.search(q or ""):                  # 条件: 下書き浮上時は実本文を注入(内容の憶測を封じる)
        pieces.append(("draft_bodies", _draft_bodies_context(who)))
    # 全体予算: 超過なら _TRIM_FIRST の順(低優先)に落とす。接地・安全系は残す。
    dropped = []
    total = sum(len(p) for _, p in pieces if p)
    if total > _DIGEST_CEILING:
        keep = dict((n, True) for n, _ in pieces)
        for name in _TRIM_FIRST:
            if total <= _DIGEST_CEILING:
                break
            for i, (n, p) in enumerate(pieces):
                if n == name and keep.get(n) and p:
                    total -= len(p); keep[n] = False; dropped.append(n)
        pieces = [(n, p) for (n, p) in pieces if keep.get(n)]
    fired = [n for n, p in pieces if p]
    if trace is not None:
        trace["digests_fired"] = fired
        if dropped:
            trace["digests_dropped"] = dropped
    return "".join(p for _, p in pieces if p)


def trace_stats(limit=5000):
    """【観測(Fable M2)】casper_trace.jsonl(1req=1行)を集計→digest発火率/共発火/出口検問発動計数/
    fastpath分布/レイテンシ を1画面に出す土台。読取専用・直近limit件。『検問が何回捏造を止めたか』が価値の計器。"""
    import collections
    recs = []
    try:
        _tr = casper_trace.TRACE if casper_trace else os.path.join(HERE, "casper_trace.jsonl")
        lines = open(_tr, encoding="utf-8").read().splitlines()[-limit:]
    except Exception:
        lines = []
    for ln in lines:
        try:
            recs.append(json.loads(ln))
        except Exception:
            pass
    n = len(recs) or 1
    dig = collections.Counter(); cofire = collections.Counter(); fastpath = collections.Counter()
    checks = {"validated": 0, "guarded_claim": 0, "gloss": 0, "vch": 0, "salvaged": 0, "echoed": 0, "abstained": 0}
    dropped = collections.Counter()
    lat = []; cards_n = routed_n = 0
    for r in recs:
        fired = r.get("digests_fired") or []
        for d in fired:
            dig[d] += 1
        for i in range(len(fired)):
            for j in range(i + 1, len(fired)):
                cofire[" + ".join(sorted((fired[i], fired[j])))] += 1
        for d in (r.get("digests_dropped") or []):
            dropped[d] += 1
        for k in checks:
            if r.get(k):
                checks[k] += 1
        fastpath[r.get("fastpath") or "—(qwen)"] += 1
        if isinstance(r.get("gen_sec"), (int, float)):
            lat.append(r["gen_sec"])
        if r.get("cards"):
            cards_n += 1
        if r.get("routed"):
            routed_n += 1
    lat.sort()

    def pct(p):
        return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1) if lat else 0
    _labels = {"validated": "捏造asset除去", "guarded_claim": "既成事実化打消", "gloss": "幻覚展開剥ぎ",
               "vch": "裸選択削除", "salvaged": "ツール救済", "echoed": "混入除去", "abstained": "正直に棄権"}
    return {
        "n": len(recs),
        "digests": [{"name": k, "count": v, "rate": round(v / n * 100)} for k, v in dig.most_common()],
        "cofire": [{"pair": k, "count": v} for k, v in cofire.most_common(12)],
        "checks": [{"name": k, "label": _labels[k], "count": checks[k], "rate": round(checks[k] / n * 100, 1)} for k in checks],
        "checks_total": sum(checks.values()),
        "dropped": [{"name": k, "count": v} for k, v in dropped.most_common()],
        "fastpath": [{"name": k, "count": v, "rate": round(v / n * 100)} for k, v in fastpath.most_common()],
        "latency": {"p50": pct(0.5), "p90": pct(0.9), "max": (round(lat[-1], 1) if lat else 0)},
        "cards_rate": round(cards_n / n * 100), "routed_rate": round(routed_n / n * 100),
    }


def ollama_chat_stream(messages, tools=None, num_predict=1536, emit_fn=None, temperature=0.15):
    """本物のストリーミング版(B・Fable指摘の最大の一手): Ollama stream:True(NDJSON)を読み、content片を
    emit_fn(chunk)で即クライアントへ→TTFT短縮。返り=組み立てたレスポンス({message:{content,tool_calls}, done_reason})。
    tool_call応答はcontentが空ゆえ何も流れない(=text応答だけがストリームされる)。"""
    body = {"model": A.model, "messages": messages, "stream": True, "think": False,
            "keep_alive": -1,
            "options": {"num_ctx": 12288, "num_predict": num_predict, "temperature": temperature, "top_p": 0.9}}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    content = ""
    tcs = None
    done_reason = None
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            mm = o.get("message") or {}
            c = mm.get("content") or ""
            if c:
                content += c
                if emit_fn:
                    try:
                        emit_fn(c)
                    except Exception:
                        pass
            if mm.get("tool_calls"):
                tcs = mm["tool_calls"]
            if o.get("done"):
                done_reason = o.get("done_reason")   # "stop"=自然終了 / "length"=上限で截ち切れ(継続要)
                break
    msg = {"role": "assistant", "content": content}
    if tcs:
        msg["tool_calls"] = tcs
    return {"message": msg, "done_reason": done_reason}


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
    task_ok = False; dm_ok = False                         # 取得成否(失敗とゼロを別出口に・Fable処方2/鉄則一)
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
                task_n = len(items); task_ok = True       # get_today_tasks が有効JSON→取得成功(件数が真)
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
            dm_ok = (dm or "").strip().startswith("{")    # 有効JSON→DM取得成功(0件と取得失敗を分ける)
            d = _j.loads(dm) if dm_ok else {}
            _thr = [t for t in (d.get("threads", []) or []) if not _is_seed_thread(t)]   # seed/テストDMを除外
            th = sorted(_thr, key=lambda t: str(t.get("updated_at") or ""), reverse=True)[:15]
            if th:
                # 未読判定を並列実行(get_messages が1件~2秒ゆえ直列だと遅い→並列で短縮)
                import concurrent.futures as _cf

                def _chk(t):
                    try:
                        r = casper_mcp.call_tool("get_messages",
                                                 {"actor_id": int(uid), "thread_id": int(t.get("thread_id"))},
                                                 token=WRITE_TOKEN, actor=uid)
                        md = _j.loads(r) if (r or "").strip().startswith("{") else {}
                        return (t, _thread_is_new(uid, md.get("messages", [])))
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
    # 【Fable処方2】件数(事実)は機構が確定してテンプレに埋め、qwenには"数字を書くな・枕だけ"と指示。
    # qwenに件数を渡すと数字を盛る/曖昧化する為(retrieve-then-render徹底・掟2)。取得失敗は「確認できませんでした」で
    # ゼロと別出口に(鉄則一: 失敗とゼロを同じ出口に流さない=平穏な朝の偽装を防ぐ)。
    if not (uid and WRITE_TOKEN and casper_mcp):
        fact_line = ""                                    # 未ログイン等: 件数を騙らない
    elif not task_ok:
        fact_line = "本日のタスクは確認できませんでした（Calendar未取得）。"
    elif task_n == 0:
        fact_line = "本日締切のタスクはございませぬ。"
    else:
        fact_line = f"本日締切のタスクは{task_n}件。"
    if uid and WRITE_TOKEN and casper_mcp:                 # DMの事実(失敗/0/N件を分ける)
        if not dm_ok:
            fact_line += "新着DMは確認できませんでした。"
        elif unread_n:
            fact_line += f"新着未読DMが{unread_n}件。"
    _al = "未取得" if not task_ok else ("ゼロ" if task_n == 0 else "あり")
    def _gen_greet():
        return strip_think(llm_text(
            "あなたは studio bokan の伴走AI『Casper』。殿への開門の『枕』(挨拶＋気の利いた一言)を1文で。"
            "**数字・件数は一切書くな**(件数は別途機構が正確に述べる)。堅苦しい飾り・古語・詩的表現は使わず、"
            "文末だけ軽く『〜にござる』。定型締め文句(『お申し付けを』等)は不要。改行なし・一人称。",
            f"時間帯の挨拶語: {g}。本日のタスクは{_al}。相手: 殿。", num_predict=80)).strip().replace("\n", " ")
    opener = ""
    try:                                                  # 8秒cap: qwen多忙でブリーフィングをhangさせぬ→テンプレ退避
        import concurrent.futures as _cf2
        _ex2 = _cf2.ThreadPoolExecutor(max_workers=1)     # with を使わぬ=8秒超のqwenを待たずに手放す(shutdown wait=False)
        _fut = _ex2.submit(_gen_greet)
        try:
            opener = _fut.result(timeout=8)
        finally:
            _ex2.shutdown(wait=False)
    except Exception:
        opener = ""
    opener = re.sub(r"[0-9０-９]+\s*件?", "", opener or "").strip()   # 安全網: qwenが数字を書いても機構が剥がす
    if not opener:
        opener = f"{g}、殿。Casper にござる。"
    greet = (opener.rstrip("。 ") + "。 " + fact_line).strip() if fact_line else opener
    try:
        import attention as _attlog
        _attlog._alog(f"briefing uid={uid}: task_ok={task_ok}(n={task_n}) dm_ok={dm_ok}(unread={unread_n}) dm_lines={len(dm_lines)}")
    except Exception:
        pass
    lines = [greet]
    if task_lines:                                        # 見出しは挨拶が件数を述べる為 省く(上下の空行を作らぬ)
        lines += task_lines
    if dm_lines:
        lines.append(f"💬 新着DM {unread_n}件（クリックで開く・「○○さんに返信」で代筆可）")
        lines += dm_lines
    if uid is None and not who.get("authed"):
        lines.append("ログイン頂ければ、本日のタスク・新着DMもお知らせいたす。")
    if uid:                                               # 今日の3件(attention・柱3の燃料ポンプ): 未了/納期超過を先回り提示
        try:
            import attention as _att
            _al = _att.briefing_lines(uid, include_drafts=False)   # 下書きは承認カードで直接出す(一往復短縮)
            if _al:
                lines.append(_al)
        except Exception:
            pass
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
                    # 蒸留完了済で外すのは"完了(offline)"のPJのみ。online(進行中)は結晶化済でも案件が生き続け
                    # 新たな知識が積まれるゆえ整理対象に残す(殿指摘2026-07-13: LED Analyze 自律開発=online進行中
                    # なのに今日の結晶化で消えていた)。onlineは常に候補、offlineは儀式了なら外す。
                    if ds != "online" and slug in done:
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


def _caption_html_images(html, max_images=6):
    """【Fable処方A】html内の埋込base64画像を Claude Sonnet vision でキャプション化(定量情報のみ・推測禁止)。
    高度技術資料(RTAB-Map/LED等)の工学的核心は画像側(点群/グラフ/データフロー図)に在り、テキストだけでは
    痩せて Qwen に核心が届かぬ為。判断(定量読解)を能力ある機構(Sonnet)へ寄せ、Qwenには"読み取れた事実"を渡す。
    返り: (captions_text, n_images, n_captioned)。"""
    imgs = re.findall(r"data:image/([A-Za-z0-9.+\-]+);base64,([A-Za-z0-9+/=]+)", html or "")
    n_images = len(imgs)
    if not n_images or VISION_BACKEND == "off":
        return "", n_images, 0
    import base64 as _b64
    caps = []; n_cap = 0
    scratch = os.path.join(HERE, "..", "_vision_tmp")
    try:
        os.makedirs(scratch, exist_ok=True)
    except Exception:
        pass
    prompt = ("この図/画像から読み取れる定量情報(数値・軸・単位・計測値・比較・構成要素、"
              "データフロー図ならノードと矢印の流れ)だけを、推測せず短い箇条書きで日本語で述べよ。"
              "読み取れない/判別不能なら『判読不可』とだけ書け。前置き不要。")
    for i, (ext, b64) in enumerate(imgs[:max_images]):
        ext2 = {"jpeg": "jpg", "svg+xml": "svg"}.get(ext.lower(), ext.lower())
        fp = os.path.join(scratch, f"aimg_{os.getpid()}_{i}.{ext2}")
        try:
            with open(fp, "wb") as _f:
                _f.write(_b64.b64decode(b64 + "=" * (-len(b64) % 4)))
            cap = strip_think(claude_cli_vision(fp, prompt)).strip()
            if cap and not cap.startswith("[vision"):
                caps.append(f"[図{i+1}] {cap}")
                if "判読不可" not in cap[:24]:
                    n_cap += 1
        except Exception:
            pass
        finally:
            try:
                os.remove(fp)
            except Exception:
                pass
    txt = ("\n\n## 図表から読み取れた事実(Sonnet vision・推測なし)\n" + "\n".join(caps)) if caps else ""
    if n_images > max_images:
        txt += f"\n（図は全{n_images}枚中 上位{max_images}枚のみ読解）"
    return txt, n_images, n_cap


def seiri_aurora_fetch(url):
    """Aurora資料のURL(base/doc/{slug})から本文を取得し、整理の"追加素材"テキストに変換して返す。
    観測外資料(LINE/Slack等)をAuroraに保存したものを、URL一つで整理フローへ引き込む(殿御下命2026-07-13)。
    返り: {ok, title, url, material} or {ok:False, error}。"""
    import casper_aurora as _au
    import html as _htmlmod
    u = (url or "").strip()
    if not u:
        return {"ok": False, "error": "URLが空です。"}
    if not _au.configured():
        return {"ok": False, "error": "Auroraが未接続です(.casper_aurora 設定要)。"}
    m = re.search(r"/doc/(.+?)(?:[?#].*)?$", u)          # slug はスラッシュを含む(casper/2026-.../…)ゆえ貪欲に拾う
    ref = _htmlmod.unescape(m.group(1) if m else u.rstrip("/").split("/")[-1]).strip()
    if not ref:
        return {"ok": False, "error": "URLから資料IDを取り出せませんでした。"}
    try:
        raw = _au.get(ref)
    except Exception as e:
        return {"ok": False, "error": f"Aurora取得失敗: {str(e)[:120]}"}
    if not raw or (isinstance(raw, str) and raw.strip().startswith("(Aurora")):
        return {"ok": False, "error": "Aurora資料が取得できませんでした(URL/権限をご確認くだされ)。"}
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        d = None
    if not isinstance(d, dict):
        return {"ok": False, "error": "Aurora資料の形式を解釈できませんでした。"}
    doc = d.get("document") or d.get("doc") or d
    title = doc.get("title") or ref
    html = doc.get("html") or doc.get("body") or doc.get("content") or ""
    text = doc.get("text") or ""
    if not text:                                          # text 欠落時は html をタグ除去して平文化
        body = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)  # CSS/JS塊を除去
        body = re.sub(r"<br\s*/?>", "\n", body)
        body = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", body)
        text = _htmlmod.unescape(re.sub(r"<[^>]+>", "", body))
    # text 側に紛れた <style>/<script> 塊や生CSSも掃う(図解HTMLアプリ資料での混入を防ぐ)
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"[^\n{}]*\{[^{}]*\}", "", text) if text.count("{") > 3 else text  # CSSルール多数=コード資料→除去
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    # 【Fable処方A】高度資料は核心が画像側に在る(点群/グラフ/データフロー図)。埋込画像をvisionでキャプション化し
    # "図表から読み取れた事実"として素材に合流させる(テキストだけ渡すと痩せて Qwen に核心が届かぬ)。
    caps, n_img, n_cap = _caption_html_images(html)
    img_bytes = sum(len(b) for b in re.findall(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", html))
    image_dominant = bool(img_bytes > 200000 and len(text) < 4000)
    # 【Fable処方B】被覆率の正直な申告: 画像主体なのに vision で図を読めていない(off/失敗)時は、痩せた素材で
    # 黙って結晶化させず"欠落"を素材に明記する(結晶化・逆インタビューが浅い合格を出すのを防ぐ・鉄則一/二)。
    warn = ""
    if image_dominant and n_cap == 0:
        warn = ("\n\n⚠️【被覆率の警告】この資料は視覚情報が主体(埋込画像が本文を大きく上回る)ですが、"
                "図の読解ができていません(vision未実施/失敗)。テキストだけでは工学的核心(計測値・点群品質・"
                "データフロー等)が欠落しています。結晶化の前に、図の要点を人が補足するか、vision読解を通してくだされ。")
    body_txt = (text + caps + warn).strip()
    if not body_txt:
        return {"ok": False, "error": "本文が空でした(URL/権限をご確認くだされ)。"}
    material = f"【Aurora資料: {title}】\n{body_txt}"[:16000]   # 図キャプション分の余地を持たせる
    coverage = {"text_len": len(text), "n_images": n_img, "n_captioned": n_cap,
                "img_bytes": img_bytes, "image_dominant": image_dominant,
                "insufficient": bool(image_dominant and n_cap == 0)}
    return {"ok": True, "title": title, "url": u, "material": material, "coverage": coverage}


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
    # rubric を"証拠駆動"の単一機構に一本化(Fable鉄則四: 判断は機構・弱モデルの自己申告true/falseに委ねない)。
    # 各項は、LLMが挙げた evidence 引用が"手元の材料"(knowledge＋直近回答)に実在し実質的(6字以上)なら true、
    # でなければ false。照合に answers を含めるのが要諦——含めねば、回答で穴を埋めても引用が knowledge に無く
    # false のまま→missing不変→同じ質問を無限再生成する(殿指摘2026-07-13)。捏造引用は不在ゆえ弾かれ false-positiveも防ぐ。
    _kn = re.sub(r"\s+", "", (knowledge or "") + "\n" + (answers or ""))
    for _k in ("段取り", "落とし穴", "見積", "判断根拠", "外部やりとり"):
        _ev = re.sub(r"\s+", "", str(evidence.get(_k, "")))
        rubric[_k] = bool(len(_ev) >= 6 and _ev[:40] in _kn)
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
                 "**『これまでの回答』で既に答えられた点は問い直すな**。同じ質問の繰り返しを避け、まだ埋まらぬ穴を"
                 "別の角度から具体的に突け。各質問1行・前置き不要・語尾は軽く『〜にござる』等。")
        questions = strip_think(llm_text(sys_q,
                    f"PJ: {project_name}\n不足: {missing or '全般の精度向上'}\n\n"
                    f"これまでの回答:\n{(answers or '(なし)')[:800]}\n\n現在の知識:\n{knowledge[:1500]}", num_predict=300)).strip()
    # 逆インタビューのログ(殿御下命2026-07-13: 同じ質問を繰り返す原因を追えるように残す)。
    # rubric/missing/questions と、回答が採点に効いたかを1行ずつ seiri_interview.jsonl に記録。
    try:
        with open(os.path.join(HERE, "seiri_interview.jsonl"), "a", encoding="utf-8") as _f:
            _f.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "project": project_name, "ready": ready, "core_ok": core,
                "rubric": rubric, "missing": missing,
                "answers_in": (answers or "")[:300], "kn_len": len(knowledge or ""),
                "questions": questions[:400]}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"ready": ready, "rubric": rubric, "evidence": evidence, "missing": missing,
            "questions": questions, "core_ok": core, "reason": reason}


def seiri_closed_book(who, project_name, knowledge, materials=""):
    """⑥前の最終硬化ゲート(Fable5・closed-book試験): 引き継ぎ担当が必ず知るべき質問を生成し、結晶化知識だけで
    closed-book 回答→答えられた数で採点。自己申告rubricより一段強い決定的ゲート。
    【Fable処方C-1: 試験官と被採点者の材料を非対称に】試験問題は"原資料(materials・vision図キャプション込み)"
    から作る(結晶化知識からでない)。画像側にしか答えの無い問い(点群RMS等)が混じり、痩せた知識は落ちる=偽合格が
    構造的に不可能。原資料が薄い/無い時のみ従来どおりPJ名から出題。返り: {pass, score, graded[{q,a,covered}]}。"""
    try:
        src = re.sub(r"\s", "", materials or "")
        if len(src) >= 120:                                   # 原資料が実質ある→そこから出題(採点者=濃い材料)
            qgen_sys = ("あなたはCasper。完了プロジェクトの引き継ぎ試験官。**下記『原資料』に答えが明記されている事項だけ**を"
                        "問う質問を5問挙げよ。次に似た案件を担当する人が必ず知るべき、原資料に在る具体(記載された数値/固有名/手順/"
                        "図から読み取れた事実)に踏み込んだ問いを。**原資料に無い情報(記載されていない数値・指標・RMSE等)は問うな**"
                        "——答えが原資料に無い問いは失格。各質問1行・番号のみ・前置き不要。")
            qgen_ctx = f"プロジェクト名: {project_name}\n\n原資料(図表から読み取れた事実を含む):\n{(materials or '')[:7000]}"
            nq = 5; from_source = True
        else:
            qgen_sys = ("あなたはCasper。完了プロジェクトの引き継ぎ試験官。『次に似た案件を担当する人』が必ず知るべき"
                        "実務的で具体的な質問を4問だけ挙げよ(段取りの要所/最大の落とし穴/重要な判断の理由/外部との重要な"
                        "やりとり を各1問)。各質問1行・番号のみ・前置き不要。")
            qgen_ctx = f"プロジェクト名: {project_name}"; nq = 4; from_source = False
        qs = strip_think(llm_text(qgen_sys, qgen_ctx, num_predict=320)).strip()
        questions = [re.sub(r"^[0-9.\-・\)\s]+", "", q).strip() for q in qs.split("\n") if q.strip()][:nq]
        graded = []
        for q in questions:
            ans = strip_think(llm_text(
                "下記『結晶化知識』**だけ**を根拠に質問へ答えよ。知識に該当が無ければ必ず『(知識に記載なし)』とだけ答えよ。"
                "推測・一般論で補うな。",
                f"結晶化知識:\n{knowledge[:6000]}\n\n質問: {q}", num_predict=280)).strip()
            covered = ("記載なし" not in ans) and (len(re.sub(r"\s", "", ans)) >= 8)   # 簡潔な事実回答(型番/固有名)も可とする
            graded.append({"q": q, "a": ans[:400], "covered": covered})
        ncov = sum(1 for g in graded if g["covered"])
        n = len(graded)
        # 【C-2】原資料出題(濃い試験)は合格線を被覆率8割で(痩せた知識が少問正答で通るのを防ぐ)。
        #        従来のPJ名出題は4問中3以上(1問までは許容)。
        passed = n > 0 and ((ncov / n >= 0.8) if from_source else (ncov >= max(3, n - 1)))
        return {"pass": passed, "score": f"{ncov}/{n}", "graded": graded, "from_source": from_source}
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


# 工程表CSV(既存タスク→Calendar公式CSV)の要求を検知(①・殿指示2026-07-10)。PJ名解決とAND条件で発火。
_SCHED_CSV_RE = re.compile(
    r"(スケジュール|工程表|進行表|タスク表|schedule).{0,12}(csv|ダウンロード|書き出|吐き出|出力|エクスポート|作成|作って|出して|ください|欲し|まとめ)|"
    r"(csv|CSV).{0,10}(スケジュール|工程表|タスク|出力|吐き出|書き出|ください|欲し|出して)", re.I)


def schedule_csv_export(query, who):
    """【工程表CSV=既存タスク→Calendar公式CSV(①・殿指示2026-07-10)】スケジュールCSV要求＋unique解決PJに対し、
    そのPJの実タスクを起票案化し proposal_to_calendar_csv でCSVを機構生成→ASSET_FILESに保存し、ダウンロード
    リンク(md)を決定的に返す(qwenに書かせず真実源から吐く=捏造/截ち切れ防止)。返り=(md_link, meta) or None。"""
    try:
        if not query or not _SCHED_CSV_RE.search(query):
            return None
        st, names, _ = _pj_resolve(query)
        if st != "unique":
            return None                                   # 曖昧/不在は選択カード側(名前解決器)へ委ねる
        nm = names[0]
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        proj = next((p for p in items if str(p.get("name")) == nm), None)
        if not proj:
            return None
        pid = proj.get("id")
        try:
            tks = [t for t in _all_tasks() if t.get("project_id") == pid]
        except Exception:
            tks = []
        if not tks:
            return None
        try:
            um = {u["id"]: (u.get("username") or u.get("name") or u["id"])
                  for u in casper_tools._get("/users?limit=200").get("items", [])}
        except Exception:
            um = {}
        tks = sorted(tks, key=lambda t: str(t.get("due_date") or "9999"))
        prop = {"project": {"name": nm, "start_date": str(proj.get("start_date") or "")[:10],
                            "end_date": str(proj.get("end_date") or "")[:10],
                            "description": proj.get("description") or ""},
                "tasks": [{"name": t.get("name") or t.get("title") or "",
                           "due": str(t.get("due_date") or "")[:10],
                           "note": "", "assignee": um.get(t.get("assigned_to"), ""),
                           "estimate": t.get("cost") or t.get("estimate") or "",
                           "type": t.get("type") or "",
                           "seqID": t.get("seq_code") or t.get("seqID") or "",
                           "shot": t.get("shot") or t.get("shotID") or ""} for t in tks]}
        csv_text = proposal_to_calendar_csv(prop)
        os.makedirs(ASSET_FILES, exist_ok=True)
        safe = re.sub(r"[^\w\-]", "_", nm)[:40] or "project"
        fname = f"schedule_{safe}_{datetime.date.today().isoformat()}.csv"
        with open(os.path.join(ASSET_FILES, fname), "w", encoding="utf-8") as f:
            f.write(csv_text)
        return (f"[⬇ {fname}](/asset/{fname})", {"pj": nm, "rows": len(tks), "fname": fname})
    except Exception:
        return None


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
        elif self.path in ("/obs", "/obs.html"):        # 観測ダッシュボード(Fable M2・digest発火/検問発動計数/レイテンシ)
            page = "obs.html"
        elif self.path == "/api/obs":                   # 観測データ(trace集計・読取専用)
            try:
                self._json(trace_stats())
            except Exception as e:
                self._json({"error": str(e), "n": 0})
            return
        elif self.path == "/api/notifications":         # M3: 先回り通知(未読)。本人のもののみ。
            who = identify(self)
            uid = who.get("uid")
            try:
                items = casper_notify.pending(uid) if (casper_notify and uid) else []
            except Exception:
                items = []
            self._json({"items": items, "count": len(items)})
            return
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
        elif self.path == "/api/briefing":         # 開門ブリーフィング(挨拶＋本日タスク＋新着DM＋逆IV1問＋滞留下書きカード)
            try:
                _who = identify(self)
                _cards = _briefing_draft_cards(_who)   # 一往復短縮: 滞留下書きを承認カードで直接提示(Fable Q4)
                _btxt = open_briefing(_who)
                if _cards:                             # カードを出す旨を一言添える(唐突なカード出現を避ける)
                    _btxt += f"\n\n📝 **承認待ちの下書きが {len(_cards)}件** ございます。下のカードで中身を確認し「送信」か「破棄」をお選びくだされ。"
                _notifs = []                           # M3: 先回り通知(未読)を briefing に同梱
                try:
                    _notifs = casper_notify.pending(_who.get("uid")) if (casper_notify and _who.get("uid")) else []
                except Exception:
                    _notifs = []
                self._json({"text": _btxt, "cards": _cards, "notifications": _notifs})
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
                         ".csv": "text/csv; charset=utf-8",
                         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(ext, "application/octet-stream")
                b = open(ap, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                if ext == ".pdf":                          # ブラウザ内 inline 表示(iframe)を許す
                    self.send_header("Content-Disposition", "inline")
                elif ext in (".pptx", ".docx", ".xlsx", ".csv"):   # Office/CSV はそのままダウンロード
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
        if self.path == "/api/notifications/read":   # M3: 通知を既読に(本人のみ・dedup_keys省略で全既読)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            try:
                c = casper_notify.mark_read(who.get("uid"), req.get("keys")) if (casper_notify and who.get("uid")) else 0
                self._json({"marked": c})
            except Exception as e:
                self._json({"error": str(e)})
            return
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
                    self._json(seiri_closed_book(who, req.get("project", ""), req.get("knowledge", ""),
                                                 req.get("materials", "")))
                elif self.path == "/api/seiri/offline":      # ⑥ Calendar offline(人承認後)
                    self._json(seiri_offline(who, req.get("project_id")))
                elif self.path == "/api/seiri/aurora":       # ② Aurora資料URL→本文を取り込んで追加素材に
                    self._json(seiri_aurora_fetch(req.get("url", "")))
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

                # 重複ガード + 再開(resume): 同名PJ(非archived)が既存の時、それが「中身空(verify-after-write で
                # PJ だけ出来て shots/tasks 未投入)」なら二重起票でなく"続きから"投入する(Fable指摘: unverified後の
                # 再試行が重複ガードで恒久デッドエンド化=PJだけ空・中身永遠に入らぬ、を防ぐ)。中身が既に在れば真の重複で中止。
                try:
                    ex = json.loads(casper_mcp.call_tool("get_projects", {"actor_id": aid},
                                                         token=WRITE_TOKEN, actor=actor))
                    dups = [p for p in (ex.get("projects") or ex.get("items") or [])
                            if str(p.get("name", "")).strip() == str(pname).strip()
                            and str(p.get("display_status", "online")) != "archived"]
                except Exception:
                    # get_projects 失敗 → 既存有無が判定不能。二重起票の危険を避け、起票を止めて正直に報告(黙って素通りしない)。
                    self._json({"ok": False, "executed": False,
                                "message": "Calendarへの照会に失敗し、既存PJの有無を確認できませんでした。"
                                           "二重起票を避けるため起票を見送ります。少し置いて再試行してくだされ。"})
                    return

                resume_pid = None
                if dups:
                    existing_pid = dups[0].get("id")
                    try:
                        _extk = [t for t in _all_tasks() if t.get("project_id") == existing_pid]
                    except Exception:
                        _extk = None                          # 判定不能
                    if _extk is not None and not _extk and (prop.get("shots") or prop.get("tasks")):
                        resume_pid = existing_pid             # 中身空のPJ=verify-after-writeの続き→createを飛ばし投入
                    else:
                        self._json({"ok": False, "executed": False, "duplicate": True,
                                    "existing": [{"id": p.get("id"), "name": p.get("name")} for p in dups],
                                    "message": f"同名PJ「{pname}」が既に存在し、タスクも入っております"
                                               f"(id {', '.join(str(p.get('id')) for p in dups)})。"
                                               "二重起票を避けるため中止。別名にするか、既存PJへの追加起票を御指示くだされ。"})
                        return

                results = []
                if resume_pid:
                    new_pid = resume_pid                      # 作成済の空PJ → create を飛ばして冪等に再開
                    results.append(f"(既存の空PJ id{resume_pid} に続きから投入=resume)")
                else:
                    pr = casper_mcp.call_tool("create_project",
                                              _clean({"actor_id": aid, **(prop.get("project") or {})}),
                                              token=WRITE_TOKEN, actor=actor)
                    results.append(pr)
                    new_pid = _extract_id(pr)
                    # 【verify-after-write】Calendar書込は応答が~30s超でtimeoutするが実体は作成される事象がある
                    # (殿指摘2026-07-13・tetsuo「起票したのに反映されない」の真因)。応答からidを取れない時は、
                    # 読みは速い get_projects で新規PJ名を照合し"実際に出来たか"を確認して実idを回収する。
                    # これを怠ると executed:true / project_id:null の"偽の成功"になる(Fable掟: 機構の嘘は最悪)。
                    if not new_pid:
                        import time as _t
                        for _ in range(6):
                            _t.sleep(3)
                            try:
                                ck = json.loads(casper_mcp.call_tool("get_projects", {"actor_id": aid},
                                                                     token=WRITE_TOKEN, actor=actor))
                                hit = [p for p in (ck.get("projects") or ck.get("items") or [])
                                       if str(p.get("name", "")).strip() == str(pname).strip()
                                       and str(p.get("display_status", "online")) != "archived"]
                                if hit:
                                    new_pid = hit[0].get("id"); break
                            except Exception:
                                pass
                    if not new_pid:
                        # 確認が取れない → 正直に"未確認"を報告(偽の成功を出さない)。承認は pending に残し再試行可能に。
                        pid = _register_pending("project_import", prop, who.get("uid"), summary)
                        self._json({"ok": False, "executed": False, "unverified": True,
                                    "pending_id": pid, "summary": summary,
                                    "message": "Calendarの応答が遅く、起票の完了確認が取れませんでした。"
                                               "（Calendar側で数十秒後に反映される場合がございます。）"
                                               "少し置いてから反映をご確認くだされ。未反映なら本ボタンで再試行を。"})
                        return
                # PJ確認済 → shots / tasks(これらも遅延しうるが致命化させず結果に残す)。
                # tasks には project_id を明示付与(import_shots同様の紐付け・付けねば孤児化/不着の因)。
                if prop.get("shots") and new_pid and "import_shots" in avail:
                    results.append(casper_mcp.call_tool("import_shots",
                        {"actor_id": aid, "project_id": new_pid,
                         "shots": [_clean(s) for s in prop["shots"]]},
                        token=WRITE_TOKEN, actor=actor))
                _exp_tasks = len(prop.get("tasks") or [])
                if prop.get("tasks") and "bulk_create_tasks" in avail:
                    results.append(casper_mcp.call_tool("bulk_create_tasks",
                        {"actor_id": aid, "tasks": [{**_clean(t), "project_id": new_pid} for t in prop["tasks"]]},
                        token=WRITE_TOKEN, actor=actor))
                # 【tasks も verify-after-write】bulk_create_tasks は timeout/スキーマ差で"黙って0件"になり得る。
                # PJ側だけ verified:true と名乗ると tasks の偽成功になる(Fable鉄則二)→実着地数を数えて正直に報告。
                _landed = None
                if _exp_tasks:
                    import time as _t2
                    for _ in range(6):
                        _t2.sleep(3)
                        try:
                            _landed = len([t for t in _all_tasks() if t.get("project_id") == new_pid])
                        except Exception:
                            _landed = None
                        if _landed:
                            break
                if _exp_tasks and not _landed:
                    # PJは出来たが tasks が着地していない → verified:true と偽らず、部分成功として正直に報告。
                    self._json({"ok": True, "executed": True, "verified": False, "tasks_landed": 0,
                                "tasks_expected": _exp_tasks, "project_id": new_pid, "summary": summary,
                                "message": f"プロジェクト「{pname}」は作成できましたが、タスク {_exp_tasks}件の"
                                           "登録がCalendar側で確認できませんでした（応答遅延またはタスク登録の不整合）。"
                                           "少し置いて反映をご確認くだされ。未反映なら本ボタンで再試行を（重複せず続きから投入します）。",
                                "results": [str(r)[:300] for r in results]})
                    return
                self._json({"ok": True, "executed": True, "verified": True, "summary": summary,
                            "project_id": new_pid, "tasks_landed": _landed, "tasks_expected": _exp_tasks,
                            "results": [str(r)[:500] for r in results]})
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
                                    "動画のVimeoアップ依頼なら kind=vimeo、Casperにアップされる資料ファイルなら asset。"
                                    "**ただし相手がこれから作成し外部で受け渡す成果物(報告書/レポート/議事録/回答など、"
                                    "Casperのアップロードや資料庫に現れず完了を観測できないもの)は track=false**"
                                    "(観測不能な依頼を追跡すると永久に未了の偽約束になる為・殿指摘2026-07-13)。"
                                    "単なる連絡・質問・割り振り依頼も track=false。keyword は実在しうる簡潔な対象名のみ(長い説明文を連結するな)。",
                                    body, num_predict=120))
                                mm = re.search(r"\{.*\}", rec, re.S)
                                d = json.loads(mm.group(0)) if mm else {}
                                if d.get("track") and d.get("keyword") and d.get("kind") in ("vimeo", "asset"):
                                    to = (pend.get("args") or {}).get("to_user_id")
                                    _tid = None                    # 元DMのthread_id(裏どり導線用・Fable処方3)
                                    try:
                                        _rd = json.loads(result) if isinstance(result, str) else result
                                        _tid = (_rd or {}).get("thread_id")
                                    except Exception:
                                        pass
                                    casper_openloop.add(
                                        who=str(actor),
                                        title=f"{_uid_to_name(to)}に「{d['keyword']}」の{'Vimeoアップ' if d['kind']=='vimeo' else '資料提出'}を依頼",
                                        probe={"type": d["kind"], "q": d["keyword"]}, assignee=_uid_to_name(to),
                                        source=({"thread_id": _tid, "to_user_id": to} if _tid else None))
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
            hits = (casper_embed.hybrid(last_user, k=8) if (casper_embed and last_user)   # M2: 意味検索復活(sqlite再ランク・内部で字面フォールバック)
                    else (casper_rag.search(last_user, k=8) if (casper_rag and last_user) else []))
            src, fulltext = (casper_rag.top_source(last_user) if (casper_rag and last_user) else (None, None))
            fullnote = ("\n\n## 該当資料の全文 (" + src + ")\n" + fulltext[:7000]) if fulltext else ""
            cal = build_digests(who, last_user)       # Fable M1: Ollama経路と同一の単一表から(entity/availability/gear/phase/fb_log/future_assign 欠落の解消)
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
        # ③ 選択カードの選択を検知(say型再投入=直前カードのoptionと一致)→教師信号として記録(カードが自らを減らす学習)
        if casper_person_gate:
            _lc = _LAST_CHOICES.pop(thr, None)
            if _lc:
                for _o in _lc.get("opts", []):
                    if _o.get("say") and str(ll_user).strip() == str(_o["say"]).strip():
                        try:
                            casper_person_gate.record_selection(_lc.get("uid"), _o.get("card_type", "choice"),
                                                                _lc.get("prompt", ""), _o.get("label"), _o.get("ref"))
                        except Exception:
                            pass
                        break
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
        _dig_trace = {}
        sysadd = DIAG_HINT + build_digests(who, ll_user, trace=_dig_trace)   # Fable M1: 全digestを単一表から(claude_cli経路と共通)
        _gate = casper_person_gate.resolve(who.get("uid"), ll_user, convo=msgs) if casper_person_gate else {}
        if _gate.get("digest"):                          # 理解ゲート: その人の既定ファセット/別名を前提として注入(入力の接地)
            sysadd += _gate["digest"]
        table_card = _table_card(ll_user, who)           # ④ 一覧意図→機構が表カードを描画(LLMは表を書かない)
        if table_card:
            sysadd += ("\n\n## 【表示装置の注記】要求された一覧は『表カード』として機構が真実源から描画済み"
                       f"(タイトル『{table_card['title']}』・{len(table_card['rows'])}行)。"
                       "**あなたは表やmarkdown表、各行の箇条書きで一覧を再現するな**(装置と重複する)。"
                       "全体の要点(件数・納期超過の有無・注視点)と次の一手だけを2〜4文の簡潔な散文で述べよ。")
        _sched = schedule_csv_export(ll_user, who)       # ① 工程表CSV: 既存タスク→Calendar公式CSVを機構生成(殿指示2026-07-10)
        if _sched:
            _slink, _smeta = _sched
            sysadd += ("\n\n## 【工程表CSVを生成済み(機構・Calendar確定)】"
                       f"{_smeta['pj']} の現在のタスク {_smeta['rows']}件を Calendar公式CSV に書き出した。"
                       f"**回答には必ず次のダウンロードリンクをそのまま改変せず含めよ: {_slink}**。"
                       "『Excelで開け、編集して取り込み直せる』旨を1文添えよ。ガントや全タスクの再掲はするな(冗長)。"
                       "Calendarへ直接反映したい場合は、その旨言えば承認カードで書込む と案内してよい。")
        try:
            hits = (casper_embed.hybrid(ll_user, k=6) if (casper_embed and ll_user)   # M2: 意味検索復活(sqlite再ランク・内部で字面フォールバック)
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
        # Q4 snooze: 『今日は流す』の say型sentinel(__attn_snooze__ <ref> <name>)を決定的に処理(副作用ゼロ・qwen非経由)。
        _snz = re.match(r"^__attn_(snooze|dismiss)__\s+(\S+)(?:\s+(.*))?$", (ll_user or "").strip())
        attn_cards = []
        if _snz:
            try:
                import attention as _att
                _ref = _snz.group(2); _nm = (_snz.group(3) or "この件").strip()
                if _snz.group(1) == "dismiss":
                    _att.dismiss(who.get("uid"), _ref); _msg = f"「{_nm}」は以後お出ししませぬ。"
                else:
                    _u = _att.snooze(who.get("uid"), _ref); _msg = f"「{_nm}」は本日は流しました（{_u}以降にまた）。"
            except Exception:
                _msg = "承知。今日は流しまする。"
            routed = {"_choices": True, "reply": _msg}; choices_obj = None
            # 以降の生成/カードは不要 → routed で生成ループskip
        else:
            routed = None
        # Q1(Fable 選択カード): 曖昧な指示語(それ/あの件…)＋action意図で対象候補が複数→qwenに推測(捏造)
        # させず選択カードで人に決めさせる。routerより優先(推測の芽を潰す)。say型ゆえ副作用起票はしない。
        _fdm = None if _snz else _file_delivery_dm(ll_user, who, convo=msgs)   # 最優先: 文脈の共有リンク→DM(『これtetsuoに送っといて』の deixis で選択カードに横取りされぬよう choices より先)
        choices_obj = None if (_snz or _fdm) else _build_choices(who, ll_user, convo=msgs)   # 内部で deixis＋action意図を判定
        if not choices_obj and not _snz and not _fdm:    # 名前解決の3値(ambiguous/none)→選択カードで拾う(無言None落ち禁止・Fable)
            choices_obj = _pj_task_choices(ll_user)
        if not _snz:
            attn_cards = _attention_action_cards(who, ll_user)       # Q4: 今日の3件の overdue/loop を選択カードで(draftは①で承認カード)
        # P2(Fable propose→execute→render): DM等のアクションは制約デコード(format=json)で型付き提案を作り
        # 承認カードを機構生成→自由文tool-callを迂回。確定時は生成ループをスキップ(salvageのモグラ叩き不要に)。
        if not _snz:                                # snooze確定時は routed を維持(上書き禁止)
            routed = _fdm or (None if choices_obj else (_action_router(ll_user, sysadd, who, convo=msgs, gate=_gate) if _looks_like_action(ll_user) else None))
            if choices_obj:                         # 曖昧→選択カード提示。生成ループはスキップ(routed扱い)
                routed = {"_choices": True, "reply": choices_obj["prompt"]}
        # 追従: Casperが「下書きを表示しますか?」と申し出た直後の裸の肯定(おねがい/はい)=その申し出への同意→浮上
        _affirm_draft = bool(_AFFIRM_RE.match((ll_user or "").strip())) and bool(_DRAFT_OFFER_RE.search(_last_assistant(msgs)))
        # 滞留下書きの浮上: 『下書き見せて/承認待ち確認/気にかけどころ処理』等→実カード(内容+承認/却下)を出す
        # (決定は散文でなくカードで=殿指摘。新規DM作成意図でない時のみ)
        if not routed and (_DRAFT_SURFACE_RE.search(ll_user) or _affirm_draft) and (not _looks_like_action(ll_user) or _DRAFT_ASK_RE.search(ll_user) or _affirm_draft):
            _n, _note = _surface_pending_drafts(who, pending_actions)   # Q3C強処方: 下書きの中身を問う=決定的fast path(qwen非経由・憶測ゼロ)
            if pending_actions:
                routed = {"_surfaced": True, "reply": _note}
        if routed and (routed.get("_surfaced") or routed.get("_choices")):   # 浮上/選択=reply表示のみ(起票しない)
            final = routed["reply"]
        elif routed:
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
        # B) 本物のストリーミング(Fable最大の一手・TTFT短縮): text応答をトークン単位でクライアントへ即送出。
        # tool_call応答はcontentが空ゆえ流れない。routed(P2アクション)時はカード返信ゆえストリームせず。
        _sbuf = [""]; _did_stream = [False]
        _cont = 0                                   # 截ち切れ自動継続の回数(トレース用・routed時も定義)
        _pend = [""]                                # 穴1(Fable): 行バッファ。末尾の不完全行は保留し、截ち切れ時に破棄できる
        def _semit(c):
            _sbuf[0] += c; _did_stream[0] = True     # _sbuf=全生チャンク(replace比較用)
            _pend[0] += c
            if "\n" in _pend[0]:                     # 完成した行(改行まで)だけクライアントへ→壊れた行を画面に出さない
                cut = _pend[0].rfind("\n") + 1
                emit_now = _pend[0][:cut]; _pend[0] = _pend[0][cut:]
                try:
                    self._emit(emit_now)
                except Exception:
                    pass
        def _flush_pend():                           # 自然終了時: 保留中の完成分をクライアントへ
            if _pend[0]:
                try:
                    self._emit(_pend[0])
                except Exception:
                    pass
                _pend[0] = ""
        def _drop_pend():                            # 截ち切れ時: 未送出の不完全行を破棄(継続が書き直す)
            _pend[0] = ""
        try:
            for it in range(MAXIT):
                if routed:                          # P2でアクション確定済 → 生成ループをスキップ
                    break
                last = (it == MAXIT - 1)
                # 最終反復は tool 無しで強制的に回答させる(空振り無限ループ防止)
                resp = ollama_chat_stream(working, tools=(None if last else tools), emit_fn=_semit)
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
                # ②截ち切れの機構的解(汎用・Fable Q2): done_reason=length で切れた時だけ自動継続。
                # キーワード非依存。穴1=不完全行はクライアント表示から破棄(_drop_pend)。穴2=既出行は機構でdedupe。
                _sys_msgs2 = [mm for mm in working if mm.get("role") == "system"]   # 穴3: 継続時 ctx を刈る土台
                _cont = 0
                while resp.get("done_reason") == "length" and _cont < 3 and final.strip():
                    _cont += 1
                    _drop_pend()                          # 穴1: 截ち切れた不完全行を画面から破棄
                    if not final.endswith("\n"):          # 保存側も不完全最終行を落とす(継続が書き直す)
                        _nl = final.rfind("\n")
                        if _nl > 0:
                            final = final[:_nl + 1]
                    # 穴3: 継続の working は肥大させず system＋『これまでの出力』＋続行指示 に刈り込む(ctx頭切れ防止)
                    working = _sys_msgs2 + [
                        {"role": "assistant", "content": final[-6000:]},
                        {"role": "user", "content":
                         "直前の出力が途中で切れた。既に完成している行は一切繰り返さず、"
                         "最後の行の続きから残りだけを最後まで出力せよ(表なら残りの行のみ)。"}]
                    resp = ollama_chat_stream(working, tools=None, num_predict=2048,
                                              temperature=0.0, emit_fn=_semit)   # 継ぎ目安定
                    more = strip_think((resp.get("message") or {}).get("content", ""))
                    if not more.strip():
                        break
                    _existing = {l.strip() for l in final.splitlines() if l.strip()}   # 穴2: 既出行を機械的に除去
                    _newl = [l for l in more.splitlines() if l.strip() not in _existing]
                    if not _newl:                         # 全て既出=前進なし→無限ループ回避
                        break
                    final = final.rstrip("\n") + "\n" + "\n".join(_newl).lstrip("\n")
                _flush_pend()                             # 保留中の完成行をクライアントへ出す
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
        final = _strip_name_gloss(final, sysadd, ll_user)            # 出口検問: 解決済みPJ名の推測括弧展開(丸亀製麺等)を剥ぐ(Fable処方3)
        _gloss = final != _pre; _pre = final
        final = _guard_completion_claims(final, pending_actions)     # P1: カード無き完了主張を打ち消し(既成事実化の構造封じ)
        _grd = final != _pre; _pre = final
        final = _validate_choices(final, pending_actions, choices=(choices_obj or attn_cards))   # Q2: 裸の選択要求(装置なし)を削除+中立誘導(不変条件①)
        _vch = final != _pre; _pre = final
        final = _strip_tool_narration(final)                         # Q7: 道具実況(生の関数呼び構文だけで停止)を剥ぐ→空なら下でfallback救済
        final = _strip_context_echo(final, ll_user)                  # Q3B: 非該当セクション(Vimeo手順等)の滲出を出口で除去
        _ech = final != _pre
        if not final.strip() and not pending_actions:                # 出口検問で全消し(ツール漏れ等)かつカード無し→PJ状態を救済 or graceful
            final = _pj_status_fallback(ll_user) or "うまくお答えできませなんだ。恐れ入りますが、今一度 別の言い方でお尋ねくだされ。"
        if _sched and _sched[0] not in final:                        # ① 決定的保証: 工程表CSVリンクがqwen応答から漏れたら機構が付す
            final = (final.rstrip() + f"\n\n{_sched[0]}\n"
                     f"（Excelで開けます。編集して取り込み直すことも可能です／Calendarへ直接反映も承認カードで行えます）")
        if casper_breaker:                          # z8a(qwen)の健全性を記録: 成功可否+レイテンシ→連続失敗でred=クラウド縮退の判断材料
            try:
                casper_breaker.record("z8a", ok=not final.startswith("[error]"),
                                      latency_ms=int((time.time() - _t0) * 1000))
            except Exception:
                pass
        if casper_trace:                            # トレース: 判断点を1req=1行で記録(事後分析基盤・Fable #7-1)
            try:
                _abstain = bool(re.search(r"(見当たら|確認できた範囲|わかりませ|分かりませ|存じませ|"
                                          r"該当(する|情報|資料).{0,8}(見つか|ありませ|無い|なし))", final))
                _fastpath = ("surface" if (routed or {}).get("_surfaced") else
                             "choices" if choices_obj else "attention" if attn_cards else
                             "snooze" if _snz else None)   # Q3C/Q1/Q4: qwen非経由の決定的経路(観測=どれだけ推測を回避したか)
                # Q4(Fable): 注入した型付き事実を記録=事後の真実源照合を機構化する土台(何を注入したか機構は知っている)
                _inj = {"vimeo": sorted(set(re.findall(r"vimeo\.com/(\d+)", sysadd)))[:40],
                        "asset": sorted(set(re.findall(r"/asset/([^\s\)\"'\]]+)", sysadd)))[:40]}
                _resp_ids = {"vimeo": sorted(set(re.findall(r"vimeo\.com/(\d+)", final)))[:40],
                             "asset": sorted(set(re.findall(r"/asset/([^\s\)\"'\]]+)", final)))[:40]}
                casper_trace.emit({"trace_id": _tid, "query": str(ll_user)[:200], "actor": who.get("uid"), "thread": thr,
                                   "routed": bool(routed), "action": (routed or {}).get("tool"),
                                   "fastpath": _fastpath, "echoed": _ech, "vch": _vch,   # 決定的fast path/echo検問/裸選択検問の発火
                                   "injected_facts": _inj, "resp_ids": _resp_ids, "cont": _cont,   # 注入事実/応答ID/継続回数
                                   "gate": {"intent": _gate.get("intent"), "facet": _gate.get("facet"),
                                            "aliases": len(_gate.get("alias_refs") or [])} if _gate else None,
                                   "pj": (lambda r: {"status": r[0], "n": len(r[1]), "path": r[2]})(_pj_resolve(ll_user)),   # 名前解決の3値/経路(観測)
                                   "rag_hits": len(hits) if isinstance(hits, list) else 0, "ctx_len": len(sysadd),
                                   "gen_sec": round(time.time() - _t0, 1), "salvaged": _salv, "validated": _val, "gloss": _gloss,
                                   "guarded_claim": _grd, "abstained": _abstain,   # 棄権(Fable #3-5/7-5: 棄権率の定点観測)
                                   "digests_fired": _dig_trace.get("digests_fired"),   # M1: 発火digest(M2観測の種)
                                   "final_len": len(final), "cards": len(pending_actions), "fewshot_used": list(_FEWSHOT_USED)})
            except Exception:
                pass
        final, diagram = render_diagram(final)
        if table_card:                                    # 表カードがある時、本文が重複md表を再現しても機構で剥がす
            # (qwenが「表を再現するな」指示を無視して全再現する→截ち切れ源。Fable: 服従に頼らず機構で強制)
            _rows = table_card.get("rows") or []
            _nod = [ln for ln in final.split("\n") if not re.match(r"\s*\|.*\|", ln)]   # md表行(|…|)を除去
            _nod_txt = re.sub(r"\n{3,}", "\n\n", "\n".join(_nod)).strip()
            # 代表名の網羅保証は"名前の列"を持つカードのみ(name_col)。列0がPJ名でないカード(停滞FB=カット番号)で
            # 「主なものは c012、—」と珍妙になるのを防ぐ(Fable指摘: カードを作った機構がname_colを申告)。
            _ncol = table_card.get("name_col")
            _names = []
            if _ncol is not None:
                for _r in _rows:
                    _nm = str(_r[_ncol]) if _r and len(_r) > _ncol and _r[_ncol] else ""
                    if _nm and _nm != "—" and _nm not in _names:
                        _names.append(_nm)
            if _names:
                _mentioned = sum(1 for _nm in _names if _nm in _nod_txt)
                if _nod_txt and _mentioned >= min(3, len(_names)):   # 本文が代表名に十分触れている→そのまま
                    final = _nod_txt
                else:                                     # 名前が表行に偏り本文が薄い→代表名を1文添え網羅保証(全再現は避ける)
                    _summ = "、".join(_names[:6])
                    _lead = _nod_txt or f"{table_card['title']}にござる。"
                    final = f"{_lead}\n\n主なものは {_summ} 等。全{len(_rows)}件は下表の通り、並べ替えは列見出しから。".strip()
            elif _nod_txt:                                # name_col無しカード(停滞FB/タスク表)は剥がすのみ
                final = _nod_txt
        if not final.strip() and (diagram or table_card or _sched):   # チャート/表/CSVだけで本文が空(qwenのAURORA前置等)→機構で復元
            if _sched:                                        # 工程表CSV: リンク＋案内を機構で(render_diagramに消されても復元)
                final = (f"{_sched[1]['pj']} の工程表をCSVにしました。\n\n{_sched[0]}\n"
                         "（Excelで開け、編集して取り込み直せます／Calendarへ直接反映も承認カードで行えます）")
            elif table_card:
                _rw = table_card.get("rows") or []
                _summ = "、".join(f"{r[0]}（{r[1]}）" for r in _rw[:6] if r and len(r) > 1)
                final = (f"{table_card['title']}にござる。主だったところは {_summ} 等。"
                         f"全{len(_rw)}件の件数・担当・締切は下表の通り。並べ替えは列見出しから。")
            else:
                final = "下記の図に整理しました。ご確認くだされ。"
        if _sched and _sched[0] not in final:                # CSVリンクは render_diagram 後も最終保証(AURORA前置で消えても付す)
            final = final.rstrip() + f"\n\n{_sched[0]}"
        log_convo(who, "user", ll_user)
        log_convo(who, "casper", final, {"diagram": bool(diagram)})
        dev_log(who, ll_user, final, {"model": A.model, "backend": "ollama"})
        # B) 送出: 既にストリーム済(text応答)なら二重送出せず——ただし出口検問/salvage/diagram で本文が
        #    変わった時のみ replace で差し替え(Fable: 検問はバッファに、修正時のみ末尾で訂正)。
        _stream_clean = re.sub(r"\n{3,}", "\n\n", _sbuf[0]).strip()
        if _did_stream[0]:
            if final != _stream_clean or diagram:
                try:
                    self.wfile.write((json.dumps({"replace": final}) + "\n").encode()); self.wfile.flush()
                except Exception:
                    pass
        else:
            for i in range(0, len(final), 36):      # 未ストリーム(routed等)→従来の疑似ストリーミング
                self._emit(final[i:i + 36])
        try:
            if diagram:
                self.wfile.write((json.dumps({"diagram": diagram}) + "\n").encode())
            if table_card:                          # ④ 表カード(機構描画・截ち切れ/転写捏造/全件ダンプの構造解)
                self.wfile.write((json.dumps({"table": table_card}, ensure_ascii=False) + "\n").encode())
            _emitted_opts = []                      # ③ 次ターンの選択検知用に、出したカードの option を控える
            if choices_obj:                         # Q1: 選択カード(say型・曖昧指示語の解決装置)をUIへ
                self.wfile.write((json.dumps({"choices": choices_obj}, ensure_ascii=False) + "\n").encode())
                for _o in choices_obj.get("options", []):
                    _emitted_opts.append({"say": _o.get("say"), "label": _o.get("label"), "card_type": "deictic", "ref": _o.get("id")})
            for _ac in attn_cards:                  # Q4: 今日の3件の overdue/loop を選択カード(催促起案/今日は流す)としてUIへ
                self.wfile.write((json.dumps({"choices": _ac}, ensure_ascii=False) + "\n").encode())
                for _o in _ac.get("options", []):
                    _emitted_opts.append({"say": _o.get("say"), "label": _o.get("label"), "card_type": "attention", "ref": _o.get("id")})
            if _emitted_opts:                       # スレッドに控える(次ターンの say型再投入と突合)
                _LAST_CHOICES[thr] = {"opts": _emitted_opts, "uid": who.get("uid"),
                                      "prompt": (choices_obj or (attn_cards[0] if attn_cards else {})).get("prompt", "")}
                if len(_LAST_CHOICES) > 200:
                    for _k in list(_LAST_CHOICES)[:-200]:
                        _LAST_CHOICES.pop(_k, None)
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
    _last_nightly = ""
    while True:
        _t.sleep(300)
        _tick += 1
        _today = datetime.date.today().isoformat()
        if _today != _last_nightly and datetime.datetime.now().hour >= 3:   # 日次バッチ(1日1回・早朝以降): flywheel蒸留/失敗trace→候補/圧縮
            _last_nightly = _today
            try:
                import nightly
                r = nightly.run(with_gate=False)          # gateはサーバ自己叩き回避で外す(外部cron/手動)
                print(f"[nightly] {r.get('learn_bank_added',0)}則学習 / pending{r.get('gen_pending',0)} / expired{r.get('expired',0)}", flush=True)
            except Exception as _e:
                print(f"[nightly] err {_e}", flush=True)
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
            try:                                        # attention: proposed>7日を自動失効(台帳を生きた承認待ちに保つ)
                import attention as _att
                _ex = _att.expire_stale()
                if _ex:
                    print(f"[attention] proposed>7日 {_ex}件を expired 化", flush=True)
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
def _notify_scheduler():
    """M3 司令塔: 常駐して割り込み政策エンジンを定期実行(既定15分毎)。朝ブリーフ(1日1回)＋閾値割り込みを
    通知ストアへ積む。実送信はせず"積む"だけ(承認/配信は別)。対象uidは環境変数 or 既定[28](殿)。"""
    import threading, time as _t
    if not casper_notify:
        return
    uids = [u.strip() for u in os.environ.get("CASPER_NOTIFY_UIDS", "28").split(",") if u.strip()]
    interval = int(os.environ.get("CASPER_NOTIFY_INTERVAL", "900"))   # 秒(既定15分)

    def _loop():
        _t.sleep(20)                                   # 起動直後は少し待つ(索引ロード等の混雑回避)
        while True:
            try:
                casper_notify.tick(uids)
            except Exception:
                pass
            _t.sleep(interval)
    threading.Thread(target=_loop, daemon=True).start()


print(f"Casper chat -> http://localhost:{A.port}  (model {A.model} @ {A.endpoint})", flush=True)
_notify_scheduler()                                    # M3: 常駐スケジューラ起動(先回り通知)
ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
