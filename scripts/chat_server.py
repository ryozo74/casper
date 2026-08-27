#!/usr/bin/env python3
"""Casper チャット鯖 — ブラウザ ⇄ (この鯖) ⇄ z8a Ollama のストリーミングプロキシ。

ブラウザは localhost:PORT を見るだけ。egress(z8a 接続)は本鯖が肩代わりするため
CORS 不要・ブラウザから外部IPへ直接出ない。

Usage:
  python3 chat_server.py --endpoint http://192.168.44.119:11434 --model qwen3:14b --port 8770
"""
import argparse, datetime, http.cookies, json, os, re, shutil, subprocess, sys, threading, time, urllib.request, urllib.error, uuid
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
    import casper_tool_ledger                    # cmd_508病五: 社内ツール名の単一ソース(散在リテラルの台帳化・漸進形)
except Exception:
    casper_tool_ledger = None
try:
    import casper_web                             # cmd_501: 一般の調べ物のためのネット検索(検問込み)
except Exception:
    casper_web = None
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
    import casper_authority                        # M4権限層(純関数 tier_of/audience_for/allowed・誰が何を誰に見せて)
except Exception:
    casper_authority = None
try:
    import casper_assign                           # M4 Phase1: アサイン候補検出(実績)＋W2実行前ガード付き execute
except Exception:
    casper_assign = None
try:
    import casper_reschedule                        # M4 Phase2: 日程変更(日付解決＋影響プレビュー＋W2実行ガード)
except Exception:
    casper_reschedule = None
try:
    import casper_meeting                            # M4 Phase2': MTG助言(会議前議題＋そろそろ定例・読取のみ)
except Exception:
    casper_meeting = None
try:
    import casper_minutes                            # M4 Phase3: 議事録→タスク候補の構造化(起票は承認カード経由)
except Exception:
    casper_minutes = None
try:
    import casper_cloud_ledger                       # 殿御下命2026-08-24: 雲へ出た内容の帳簿(頻度と中身を後で検分)
except Exception:
    casper_cloud_ledger = None
try:
    import casper_status                             # M4 Phase4: status更新verb(納品/客先承認/対象外・W2実行ガード)
except Exception:
    casper_status = None
try:
    import casper_health                          # セルフヘルス(トレース監視→health.md＋逸脱アラート・Fable北極星 柱2)
except Exception:
    casper_health = None
try:
    import casper_push                             # M3配信: 自前Web Push(VAPID/RFC8291)=先回り通知を閉じてても端末へ
except Exception:
    casper_push = None
try:
    import casper_breaker                          # サーキットブレーカー(依存ごと縮退/自動復帰・Fable北極星 柱2)
except Exception:
    casper_breaker = None
try:
    import casper_llm_client                      # cmd_519黒匣: 推論機呼出の横断台帳(inflight/incident/TTFT・軍師設計)
except Exception:
    casper_llm_client = None
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
import pack_paths   # M5: vault/pack パスの単一解決点(CASPER_VAULT/CASPER_PACK env で差替可)
ASSET_DIR = os.path.join(pack_paths.VAULT, "50_asset_shadows")
VAULT = os.path.join(pack_paths.VAULT)
ASSET_FILES = os.path.join(ASSET_DIR, "files")
ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", default="http://192.168.44.119:11434")
ap.add_argument("--model", default="qwen3:14b")
ap.add_argument("--port", type=int, default=8770)
A = ap.parse_args()
OLLAMA = A.endpoint.rstrip("/") + "/api/chat"
_ENDPOINT_HOSTPORT = A.endpoint.rstrip("/").split("://", 1)[-1]   # "host:port"(cmd_509第2便: breaker gen:key用)

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


# 平文token退避(M2秘匿): 機微値を ext4 home の ~/.config/casper/secrets.env(0600) から os.environ へ載せる。
# 以降の全ての os.environ.get(TOKEN/SECRET) が home 値を第一優先で拾う(9p上の平文ファイルは fallback に降格)。
try:
    import casper_secrets as _casper_secrets
    _casper_secrets.load_into_env()
except Exception:
    pass

# Casper 独自の署名鍵(Score とは分離)。本人確認は Calendar /api/auth/token で行い、JWT は Casper 自前で署名。
# 固定鍵: env(home secrets.env含む) > .casper_secret ファイル(再起動で不変=ログイン維持) > 無ければ生成して保存。
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

# Calendar タスクステータス。canonical は9値(ニブ殿 2026-07-24 実コード回答で訂正・旧「19値」は legacy ラベル)。
# 完了/対象外の判断は **status_category(API単一ソース)** が正。status 集合は category 欠落時の fallback のみ。
# → 判断の実体は casper_status_rules（単一機構）。ここではその薄い委譲だけを持つ。
import casper_status_rules as _sr
_TASK_DONE = _sr.TASK_DONE_FALLBACK              # 後方互換(既存参照用・fallback集合)
_TASK_NOT_OVERDUE = _sr.TASK_INACTIVE_FALLBACK   # 後方互換(既存参照用・fallback集合)
_PJ_NOT_OVERDUE = _sr.PJ_INACTIVE_FALLBACK


def _not_overdue(status, cat=None, scope="task"):
    """遅延判定の除外(完了 or 対象外)か。category が在ればそれが正・無ければ status で fallback。
    件数と表の二重基準ドリフト(承認済タスクが件数=遅延/表=完了済 と食い違う)を防ぐ単一ソース(Fable指摘)。"""
    return _sr.is_inactive(status, cat, scope)


def _overdue_days(due, status, today=None, scope="pj", cat=None):
    """【納期超過=派生事実の唯一の判定機構(Fable: 集合/派生の判断は機構・LLMは修辞)】
    返り: 超過日数(int>0) / 0(超過でない) / None(日付不正)。
    完了/対象外は due<today でも超過に非ず(isOverdue派生)。qwenに due<today の計算をさせない為の単一ソース。
    cat=status_category を渡せば API 単一ソースで判断(ap/client_ap も completed=非超過)。"""
    return _sr.overdue_days(due, status, cat, today, scope)


def _due_note_c(due, status, today=None, scope="pj", cat=None):
    """派生の『納期状況』を機構が確定して文字列化(qwen/表に日付計算を委ねない)。
    超過→🔴N日超過 / 本日締切→⚠️ / 過去だが完了→"完了済(納期超過ではない)"(誤計算封じ) / それ以外→""。"""
    import datetime as _dt
    od = _overdue_days(due, status, today, scope, cat)
    if od is None:
        return ""
    if od > 0:
        return f"🔴{od}日超過"
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
    except Exception:
        return ""
    today = today or _dt.date.today()
    done = _not_overdue(status, cat, scope)
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


# ★slug は ASCII のみを取る。仮名/漢字を混ぜると、URLの直後に続く日本語
#   (「…-2026-08-26**の以下の文字を消して下さい**」)まで飲み込み、
#   台帳に無い slug になって「特定できず」に倒れる(実測で踏んだ)。
#   Aurora の slug は実物がローマ字("kiyotomo/2026-08-26/sorafune-mtg-gijiroku-2026-08-26")。
_AURORA_DOC_URL_RE = re.compile(
    r"https?://[^\s/]+(?::\d+)?/doc/([A-Za-z0-9_\-./%~]+)")
_AURORA_DOC_ID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.I)


def aurora_doc_ref(text):
    r"""【殿御下命2026-08-26】ユーザーの発話が名指しした Aurora 資料を**決定的に**解く。

    実害(2026-08-26 18:18〜18:22): kiyotomo殿が
      「http://…:8100/doc/kiyotomo/2026-08-26/sorafune-mtg-… の以下の文字を消して下さい」
    と URL を添えて頼んだが、URL から doc_id を解く機構が無かった。
    aurora_append(既存ノートの修正)は結線されているのに doc_id が埋まらず、
    Casper は「編集機能を持っていません」と答え、二分後には「削除しました」と嘘をついた。
    ——道具は在ったのに、鍵(doc_id)を渡す機構が無かったのである。

    ★識別子はモデルに生成させぬ。URL/slug から機構で引く([[project_casper_grounding_machinery]])。
    ★三値で返す: 解けた=dict / 名指しが無い=None / 名指しはあるが見つからぬ={"ref":…, "found":False}。
      「名指しが無い」と「名指しはあるが台帳に無い」を混ぜると、後者を黙って新規作成に倒しかねぬ。
    """
    t = text or ""
    m = _AURORA_DOC_URL_RE.search(t)
    ref, by = (m.group(1).rstrip("/.、。"), "slug") if m else (None, None)
    if not ref:
        m2 = _AURORA_DOC_ID_RE.search(t)
        ref, by = (m2.group(1), "id") if m2 else (None, None)
    if not ref:
        return None                                    # 名指しが無い
    if by == "id":
        return {"ref": ref, "by": by, "found": True, "doc_id": ref, "title": ""}
    try:
        import casper_aurora as _au
        d = _au.document_exists(slug=ref)
    except Exception:
        d = None
    if not d:                                          # None(照会失敗) も {}(該当無し) もここ
        return {"ref": ref, "by": by, "found": False, "doc_id": "", "title": ""}
    did = d.get("id") or d.get("doc_id") or ""
    if not did:
        return {"ref": ref, "by": by, "found": False, "doc_id": "", "title": ""}
    return {"ref": ref, "by": by, "found": True, "doc_id": did,
            "title": d.get("title") or "", "deleted": bool(d.get("deleted"))}


_DOC_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _aurora_plain(html):
    r"""Aurora のノートHTMLから**本文だけ**を取り出す。

    【殿御下命2026-08-27】実地試験で当方の検問が偽警報を出した——正しい追記に対し
    「1941字 → 492字（約75%減）」と警告した。真因は `make_note` が埋める `<style>` の
    中身(CSS 1,482字)を、タグ除去だけでは落とせず**本文として数えていた**こと
    (実測: html 2796字 / タグ除去 1941字 / style除去 459字)。
    ★偽警報は警報より質が悪い。人は鳴り続ける鐘を無視するようになる。
    ★測る側を一箇所に畳む(縮み検問と乖離検問が別々に数えれば、また食い違う)。
    """
    t = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", str(html or ""))
    t = re.sub(r"<[^>]+>", "\n", t)
    t = re.sub(r"(?m)^\s*@import[^\n]*$", "", t)          # style の外に漏れた @import も落とす
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def aurora_valid_doc_id(v):
    """Aurora の doc_id はUUID。slug や題を doc_id と称する値を通さぬ。

    【殿御下命2026-08-27】実害(14:21/14:25): モデルが doc_id に **slug** を書いた
    (`kiyotomo/2026-08-27/sorafune-sama-mtg-gijiroku`)。機構は URL から本物の doc_id を
    引けていたにも関わらず、埋め込みが `not args.get("doc_id")` の条件付きであったため
    **モデルの偽物が機構の本物を押しのけた**。識別子は生成させぬ——生成された物は弾く。
    """
    return bool(_DOC_ID_RE.match(str(v or "").strip()))


def aurora_body_drift_note(doc_id, new_body):
    r"""版差し替えの本文が、現本文と**別物**になっていないか。

    【殿御下命2026-08-27】実害(14:21:47/14:25:20): 「BOKAN 担当事項に一行足して」と頼まれた
    aurora_append の body が、**実在せぬ議事録の丸ごと書き起こし**であった
    (実在せぬ参加者「武井/rui」、実在せぬ節「フェーズ1(レイアウト/アニメーション)」)。
    承認されておれば本物の資料が捏造で置き換わっていた。

    ★字数の増減では捕まらぬ(捏造は同じくらいの長さで来る)。**現本文の見出しがどれだけ
      生き残っているか**を測る。追記なら見出しはそのまま残る。書き起こしなら消える。
    ★止めはせぬ——章立てごと作り直す正当な差し替えも在る。だが**黙っては通さぬ**。
    戻り値: 注記(str) / 問題なし・照会できぬ時は ""。
    """
    if not doc_id or not new_body:
        return ""
    try:
        import casper_aurora as _au
        cur = _au.get(doc_id)
    except Exception:
        return ""
    if not cur:
        return ""
    try:
        d = json.loads(cur) if isinstance(cur, str) else cur
    except Exception:
        d = {}
    html = (d.get("html") or d.get("body") or d.get("content") or "") if isinstance(d, dict) else ""
    text = _aurora_plain(html)                  # ★縮み検問と同じ物差し(別々に数えれば食い違う)
    heads = []
    for ln in text.splitlines():
        ln = ln.strip()
        # 見出しらしき行(番号付き/#/太字見出し)を骨格として拾う
        if re.match(r"^(#{1,6}\s+\S|[0-9０-９]+\s*[.．、)）]\s*\S|\*\*[^*]{2,40}\*\*\s*$)", ln):
            heads.append(re.sub(r"^[#*\s]+|[*\s]+$", "", ln)[:40])
    heads = [h for h in dict.fromkeys(heads) if len(h) >= 3][:12]
    if len(heads) < 2:
        return ""                                    # 骨格が読めぬ資料では判じぬ(推測で騒がぬ)
    nb = str(new_body)
    kept = [h for h in heads if h in nb]
    if len(kept) >= max(2, int(len(heads) * 0.5)):
        return ""                                    # 骨格の半分以上が残っている=追記/部分修正
    lost = [h for h in heads if h not in kept][:5]
    return (f"\n🚨 **現本文の見出し {len(heads)}件のうち {len(heads) - len(kept)}件が"
            f"新しい本文に見当たりませぬ**（消える見出し: {' / '.join(lost)}）。\n"
            "Aurora の版差し替えは中身を丸ごと入れ替えまする。"
            "**追記のつもりであれば、これは資料の作り直しになっており申す**——"
            "本文をよくお確かめの上で承認くだされ。")


def aurora_shrink_note(doc_id, new_body):
    r"""既存ノートの差し替えで**本文が大きく減る**時、それを承認カードの表に立てる。

    ★append_version は名に反して**内容を丸ごと差し替える**(2nd艦隊が実害を記録:
      aurora-docid-overwrite-pitfall)。修正のつもりで断片を渡せば、資料の残りが消える。
    ★止めはせぬ——要約への差し替えは正当な操作である。だが**黙って通さぬ**。
      減る事実を人の目に映してから承認させる(silent cap の禁)。
    戻り値: 注記(str) / 減っておらぬ・照会できぬ時は ""。
    """
    if not doc_id or not new_body:
        return ""
    try:
        import casper_aurora as _au
        cur = _au.get(doc_id)
    except Exception:
        return ""
    if not cur:
        return ""
    try:
        d = json.loads(cur) if isinstance(cur, str) else cur
    except Exception:
        d = {}
    html = ""
    if isinstance(d, dict):
        html = d.get("html") or d.get("body") or d.get("content") or ""
    old_len = len(_aurora_plain(html))          # ★style/script を落として数える(偽警報の是正)
    new_len = len(str(new_body))
    if old_len <= 0 or new_len >= old_len * 0.6:
        return ""
    return (f"\n⚠️ **本文が {old_len}字 → {new_len}字 に減りまする"
            f"（約{100 - int(new_len * 100 / old_len)}%減）。**"
            "Aurora の版差し替えは中身を丸ごと入れ替えまする——"
            "一部だけを直すつもりなら、修正後の**全文**をお確かめくだされ。")


# cmd_492 第1便: 直前turnの話題を機構が記録する(記録のみ・まだ判定/注入には使わない)。
# 既存の決定的解決器(top_source/_pj_resolve/_resolve_persons)が既に解決した結果を拾うだけで、新たな推測は行わない。
_LAST_TOPIC = {}       # thread -> {"kind":"doc"|"project"|"person", "key":..., "label":..., "ts":..., "uid":...}
# cmd_499: 裸の列挙選択(装置なしで①②を並べて選ばせる形)への応答で来た番号を、直前turnの
# 列挙と突合するための控え(_LAST_TOPICと同型・cmd_492本体には手を入れず独立機構として新設)。
_LAST_ENUM = {}         # thread -> {"lines":[str,...], "ts":..., "uid":...}
# cmd_508 第3便(病三=対象スロットの空白): 直前turnで確定した対象(資料/案件/人物)を機構が保持する錨。
# _LAST_TOPIC(cmd_492)と同型(thread->dict・per-thread store)だが別機構として独立させる——
# _LAST_TOPICの引き継ぎ判定(_topic_handoff)はLLM classifier(_needs_prior_context)を要件とするのに対し、
# 本機構(_anchor_continuation)は「判定はトークン照合のみ・LLM classifier不要」というbrief要件を満たす
# 別経路であり、両者を一本化するとcmd_492のAC10回帰(既に本番稼働中)を巻き込む危険がある。
# 書込は_LAST_TOPICと同じ解決結果(_resolve_turn_topic)を横取りするのみ=新たな推測機構は追加しない。
_LAST_ANCHOR = {}       # thread -> {"kind":"doc"|"project"|"person", "key":..., "label":..., "ts":..., "uid":...}

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


# 依頼(query)に鉤括弧で本文そのものが明示指定されている形。殿が本文を指定した以上、
# 中身の有無を機構が疑う筋ではない(cmd_494 AC3・過剰検問除け)。
_DM_QUOTED_BODY_RE = re.compile(r"[「『][^」』]{1,400}[」』]")


def _dm_body_complete(query, body):
    """DM代筆の本文(body)が、依頼(query)された中身を実際に載せているかの三値判定(cmd_494)。
    語彙表(禁止語リスト)は使わない——実害①(前置きのみで肝心の説明が丸ごと無い)と実害②(依頼と無関係な
    本文)は『中身が無い』と『中身が違う』という同一構造ゆえ、意味判定1本で両方を捉える(軍師実測で
    6/6正答・10/10揺らぎゼロ確認済のプロンプトをそのまま使う)。
    戻り値は三値: True(complete)/False(incomplete)/None(判定不能=例外・timeout・JSON解析失敗・
    keyの欠落・値がbool型でない)。呼出側は必ず `is not True` の明示比較で扱うこと(cmd_486以来の掟)。"""
    q = str(query or "")
    b = str(body or "")
    if not b.strip():
        return False
    try:
        r = _ollama_json(
            "あなたは検査器。ユーザの依頼(query)と、AIが代筆したDM本文(body)を見比べ、"
            "『依頼された中身が本文に実際に載っているか』だけを判定せよ。"
            "本文が前置き・予告・案内のみで肝心の中身が無い、または依頼と無関係な内容ならcomplete=false。"
            "依頼された事柄の実体(具体的な情報・数値・名前・文面)が本文に含まれていればcomplete=true。"
            "短くとも依頼どおりの用が足りていればtrue。JSONのみ: {\"complete\":true|false}",
            f"依頼: {q}\n本文: {b}", num_predict=60)
        o = json.loads(r)
        if "complete" not in o or not isinstance(o["complete"], bool):
            return None
        return o["complete"]
    except Exception:
        return None


# cmd_494 1-3: False/None時の聞き返し文面(fail-closed)。カードを立てず、次に何をすればよいかを示す。
_DM_BODY_INCOMPLETE_MSG = ("お送りする本文に、頼まれた中身がまだ入っておりませぬ。"
                           "何を書いてお送りすればよいか、内容をお聞かせくだされ。")


def _register_pending(tool, args, uid, summary, thread=None, origin="user", query=None, trace_id=None):
    # query(発端の発話)+trace_id: 承認時の編集差分から教師信号の三つ組を復元する為に必須(Fable5指摘・A実装)
    # cmd_494: DM代筆の中身欠如検問。send_messageの全登録経路(通常/fanout/action_router)が
    # 必ずここを通る一点ゆえ、ここに置けば個別に足して片方が漏れる事故(cmd_485の轍)を構造的に防げる。
    if tool == "send_message" and origin != "fanout":     # fanoutは原本の複製ゆえ原本判定を使い回す(1-5)
        _q = query or ""
        _body = (args or {}).get("body")
        if not _DM_QUOTED_BODY_RE.search(_q):              # 殿が本文を鉤括弧で明示指定済みなら判定器を呼ばぬ(1-4)
            _cmpl = _dm_body_complete(_q, _body)
            if _cmpl is not True:                           # False/None(判定不能)を等しく fail-closed で倒す(cmd_486の掟)
                return None
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


_AURORA_SAVE_UNKNOWN_PROMPT = "『Auroraへ保存』のご依頼と存じますが、判定機構が今しがた応答いたしませなんだ。いかがいたしましょう？"


def _aurora_save_title_unknown_choices():
    """(C・cmd_486是正) 承認カードの題を決められぬ時の聞き返しchoices。
    既定題「Casperノート」で起票してしまうのが事故の源だったため、材料が無いturnは
    既定で埋めるのでなく聞き返す(掟: 未確認をtrueと名乗るな)。"""
    return {"prompt": "Aurora資料の題名をお決めいただきたく存じます。いかがいたしましょう？",
            "options": [
                {"label": "殿の発話をそのまま題にする", "say": "題名はそのままでよい"},
                {"label": "内容から推測させる", "say": "題名はお任せする"},
            ]}


def _resolve_aurora_note_title(query, response_text, table_md):
    """(C・cmd_486是正) 承認カード題の出所を優先順位で機構的に固定する:
      ①殿の発話中の鉤括弧(queryから抽出。実例「Auroraに「status内容 0729」をまとめて」→題=status内容 0729)
      ②機構が用意した材料の見出し(table_md経路・呼出側で別途処理済ゆえ本関数はtable_md優先枝には呼ばれぬ)
      ③応答本文の「タイトル:」明示行のみ(qwenが意図的に題を宣言した形ゆえ信頼できる)
      ④いずれも無い→None(既定題「Casperノート」で埋めず、呼出側が聞き返しへ回す)
    旧実装は応答本文の鉤括弧(fの「」)や既定文字列「Casperノート」を安易に採っていたため、
    Vimeo案内文が返ったturnで題が「🎬 Vimeoへアップ」等になる事故があった(実測)。
    応答本文の鉤括弧はもはや題の出所にしない——それは殿の発話ではなく qwen の言い回しだから。"""
    q = query or ""
    qm = re.search(r"[「『]([^」』]{2,80})[」』]", q)
    if qm:
        return qm.group(1).strip()
    tm = re.search(r"(?m)^\s*\**\s*タイトル\s*\**\s*[:：]\s*\**\s*(.+?)\s*\**\s*$", response_text or "")
    if tm:
        return tm.group(1).strip()
    return None


def _aurora_save_unknown_choices():
    """(A・cmd_486是正) 分類器がsave意図をNone(判定不能)で返した時の聞き返しchoices。
    層1を陽性へ倒して起票してはならぬ(fail-closed維持)——承認カードは立てず、choices(選択カード・
    say型・既存機構に相乗り)で殿に決めていただく。sayの片方は即断路(規則ベース)で必ずTrueになる
    文面を選ぶ——分類器が死んでいても、この再投入は規則だけで通り承認カードが立つ
    (=分類器不応答でも殿は2クリックで目的を達せられる。無言落下の解消)。"""
    return {"prompt": _AURORA_SAVE_UNKNOWN_PROMPT,
            "options": [
                {"label": "Auroraへ保存する", "say": "Auroraに保存して"},
                {"label": "保存は不要(このまま続ける)", "say": "保存は不要"},
            ]}


_AURORA_EDIT_INTENT_RE = re.compile(
    r"(追加|追記|足し|加え|修正|直し|直す|変更|書き換え|置き換え|消し|消して|削除|更新|差し替え)")
# モデルが道具を呼ばず、本文を地の文へ書いてしまった時の取り出し口。
_AURORA_BODY_KW_RE = re.compile(r'body\s*=\s*("""|\'\'\'|"|\')(.+?)\1', re.S)


def aurora_edit_compose(pin, instruction):
    r"""資料の修正を**機構が直接こしらえる**（モデルの道具呼びに頼らぬ）。

    【殿御下命2026-08-27】実害(15:22〜16:08・46分): kiyotomo殿は
    「承認ボタンがでてこない」「でない」「どうしたらいい？」と九度訴えられた。
    Casper は九度とも「承認カードが表示されますので押してください」と**約束だけ**を返し、
    カードは一枚も立たなかった(trace: cards=0 が14turn連続)。
    ★材料は揃っていた——錨により現本文は注入済(ctx_len 4625)。
      欠けていたのは『モデルが道具を呼ぶ』という**運**だけであった。
    ★弱いモデルに運を求め続けてはならぬ。**機構がこしらえて機構が立てる。**
      生成に使うのは「今の全文＋指示→直した全文」だけの、逃げ場の無い一問にする。

    戻り値: 修正後の全文(str) / こしらえられぬ時は None(fail-closed=起票せぬ)
    """
    mat = (pin or {}).get("material") or ""
    if not mat.strip() or not (instruction or "").strip():
        return None
    prompt = (
        "以下は社内の共有資料の『現在の全文』である。\n"
        "----- 現在の全文 ここから -----\n" + mat[:12000] + "\n----- 現在の全文 ここまで -----\n\n"
        "利用者の指示: " + str(instruction)[:600] + "\n\n"
        "この指示だけを反映した【修正後の全文】を出力せよ。\n"
        "・前置き・後書き・説明・挨拶を書くな。**資料の本文だけ**を出せ。\n"
        "・指示に触れていない箇所は一字も変えるな。見出し・節・順序をそのまま保て。\n"
        "・記憶から補うな。上の全文に無い参加者・日付・決定事項を足すな。\n"
        "・コードブロックや引用符で包むな。素の本文をそのまま書け。")
    try:
        if BACKEND == "claude_cli":
            out = strip_think(claude_cli_text(prompt))
        else:
            r = ollama_chat([{"role": "user", "content": prompt}], num_predict=3000)
            out = strip_think(((r or {}).get("message") or {}).get("content") or "")
    except Exception:
        return None
    out = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", (out or "").strip())
    out = re.sub(r"^\s*(以下|修正後|承知|かしこまり)[^\n]{0,40}\n+", "", out)
    if len(out) < 40:
        return None
    # ★こしらえた物が『その資料』であることを確かめてから起票する。
    #   骨格が半分も残っておらねば、それは修正でなく作り直し=捏造の疑い(fail-closed)。
    heads = [h for h in
             (re.sub(r"^[#*\s]+|[*\s]+$", "", ln.strip())[:40] for ln in mat.splitlines()
              if re.match(r"^\s*(#{1,6}\s+\S|[0-9０-９]+\s*[.．、)）]\s*\S)", ln))
             if len(h) >= 3]
    heads = list(dict.fromkeys(heads))[:12]
    if len(heads) >= 2:
        kept = [h for h in heads if h in out]
        if len(kept) < max(2, int(len(heads) * 0.5)):
            return None
    if len(out) < len(mat) * 0.4:            # 極端に痩せた=指示に無い部分まで落ちている
        return None
    return out[:20000]


def aurora_append_salvage(final, pin, query):
    r"""【殿御下命2026-08-27】錨が生きておるのに道具が呼ばれなんだ turn を救う。

    実害(2026-08-27 15:14:34・実地試験で再現): 錨の手当により doc_id も現本文も正しく
    渡っていたにも関わらず、qwen は `aurora_append` を**呼ばず地の文へ書いた**
      (doc_id= と body= を Python の代入の形で地の文へ書いた)。カードは0件、殿へ返ったのは
    「うまくお答えできませなんだ」の一行のみ。**材料は揃っていたのに届かなかった。**

    ★既存の salvage は `aurora_create` しか作らず、しかも発火条件が `_wants_aurora_save(query)`
      ——「BOKAN 担当事項のところに以下追加」には Aurora語が無いゆえ、そもそも通らぬ。
      **修正の救済路が一本も無かった。**
    ★弱いモデルに『道具を正しく呼べ』と求め続けるのでなく、**呼ばなんだ時に機構が拾う**。

    戻り値: 差し替え本文(str) / 救えぬ時は None
    """
    if not pin or not pin.get("doc_id") or not (final or "").strip():
        return None
    if not _AURORA_EDIT_INTENT_RE.search(query or ""):
        return None                                  # 修正の意図が無い turn では拾わぬ
    m = _AURORA_BODY_KW_RE.search(final)
    cand = (m.group(2) if m else final).strip()
    if len(cand) < 40:
        return None
    # ★拾った物が「その資料」であることを確かめる。地の文の雑談を本文に据えて
    #   資料を吹き飛ばさぬための関——現本文の見出しが半分以上生きている物だけを通す。
    mat = pin.get("material") or ""
    heads = [h for h in
             (re.sub(r"^[#*\s]+|[*\s]+$", "", ln.strip())[:40] for ln in mat.splitlines()
              if re.match(r"^\s*(#{1,6}\s+\S|[0-9０-９]+\s*[.．、)）]\s*\S)", ln))
             if len(h) >= 3]
    heads = list(dict.fromkeys(heads))[:12]
    if len(heads) < 2:
        return None                                  # 骨格が読めぬ資料では救わぬ(推測で書き換えぬ)
    kept = [h for h in heads if h in cand]
    if len(kept) < max(2, int(len(heads) * 0.5)):
        return None
    return cand[:20000]


def _salvage_text_toolcall(final, who, pending_actions, query=None, trace_id=None, table_md="", choices_obj=None):
    """qwenが send_message を呼ばず DM をテキストで書いた場合の救済(JSONブロック＋プロセ両対応)。
    宛先uid/名＋本文を拾い pending 登録→承認カードを出す(ローカルqwenのfunction-calling不発対策)。
    戻り値は (final, unknown_choices) のタプル。unknown_choicesは分類器が判定不能(None)だった時のみ
    choices dictを返す(呼出側は choices_obj が未使用ならこれを採用する。既に choices_obj が
    埋まっている場合は本カードを出さぬのが安全側=呼出側の責務)。"""
    _AU_LAST_ROUTE["route"] = None                 # trace観測(cmd_487): turn毎に既定null/nullへ戻す
    _AU_LAST_ROUTE["decision"] = None
    if pending_actions:                            # 既にツール呼出で pending 済なら不要
        return final, None
    f = final or ""
    # ⓪ Aurora ノート作成の表明救済: qwen が「Auroraに『TITLE』として作成しますか？承認ボタン…」と
    #    "言っただけ"で aurora_create を呼ばなかった場合、応答本体を本文に pending 登録→承認カードを出す。
    #    ★錨は「殿が頼んだか」に置く。応答の言い回しだけを見ていたゆえ取り落とした——実測2026-07-28:
    #    『この表をAurora資料にしてアップして』へ『保存してよろしいでしょうか？承認いただければ…』と
    #    答えたが、語彙表(承認ボタン/保存しますか…)に無く救済が発火せず、殿の言う「保存ボタンがない」に至った。
    _au = _wants_aurora_save(query)
    _au_req = (_au is True)          # ★三値の明示比較(cmd_486(A))。Noneは偽値ゆえ`if _au:`に潰すと
    _au_unknown = (_au is None)      #   三値化しても実質何も変わらぬ最悪の結果になる——ここは掟。
    # trace観測(cmd_487追加AC): 判定値と決着経路をmodule変数に確定する。nullは_wants_aurora_save側で
    # 既に確定済(Aurora語なしのturn)。判定不能(None)はllm経路の中でも聞き返しへ回る特別な決着なので
    # ここで"unknown_askback"に昇格させる(nullとfalseを混同せぬのと同じ掟でllmとunknown_askbackも混同せぬ)。
    if _AU_LAST_ROUTE["route"] is not None:
        _AU_LAST_ROUTE["decision"] = ("true" if _au is True else "false" if _au is False else "unknown")
        if _au_unknown:
            _AU_LAST_ROUTE["route"] = "unknown_askback"
    else:
        _AU_LAST_ROUTE["decision"] = None
    if _au_unknown and not choices_obj:            # (A) 判定不能→起票せず聞き返しchoicesを返す(fail-closed維持)
        return final, _aurora_save_unknown_choices()
    if _au_req or (re.search(r"[Aa]urora", f)
                   and re.search(r"承認ボタン|作成しますか|保存されます|保存しますか|作成しました|保存しました", f)
                   and re.search(r"(ノート|ドキュメント|資料|note)", f)):
        # 本文=表明文(Aurora/承認ボタン等の行)を除いた応答本体(Casperが提示した一覧など)
        body = re.sub(r"(?m)^.*(承認ボタン|作成しますか|保存されます|保存しますか|保存してよろしい|"
                      r"承認いただけ|起票します|Auroraに|Aurora に).*$", "", f).strip()
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        _from_table = False
        if table_md and _au_req:
            # ★材料は機構が用意する。注入した表を弱qwenが見落とし『直前の応答に表が含まれていない』と
            # 断って聞き返した(実測2026-07-29)。頼むのをやめ、解決済みの表を本文の正とする。
            # ②機構が用意した材料の見出し=既に殿の会話由来ゆえ、題の出所として①③に優先して確定する。
            _h = [c.strip(" *") for c in table_md.split("\n")[0].strip().strip("|").split("|")]
            title = (f"{_h[0]}一覧" if _h and _h[0] else (_resolve_aurora_note_title(query, f, table_md) or "Casperノート"))
            body = f"## {title}\n\n{table_md}\n\n（Casperとの会話で整理した内容を資料化）"
            _from_table = True
        elif re.search(r"特定できて(おりませ|いませ)|見つかりませ|含まれておりませ|貼り付けて|添付して|"
                       r"いずれかの方法|教えていただけ", body):
            return final, None                            # 聞き返し/拒否の文面を資料として保存させぬ
        else:
            title = _resolve_aurora_note_title(query, f, table_md)
            if title is None:                      # (C)④ いずれの出所にも無い→既定題で起票せず、題を聞き返す
                return final, _aurora_save_title_unknown_choices()
        if len(body) >= 20:
            args = {"title": title, "body": body}
            if who.get("uid"):
                args["actor_id"] = who["uid"]
            summary = _action_summary("aurora_create", args)
            pid = _register_pending("aurora_create", args, who.get("uid"), summary, origin="user", query=query, trace_id=trace_id)
            pending_actions.append({"id": pid, "tool": "aurora_create", "args": args, "summary": summary})
            if _from_table:                               # 機構が本文を確定した=qwenの散文は捨て、簡潔に告げる
                return (f"「{title}」として Aurora に起票する下書きを用意いたした（{len(table_md.splitlines())}行の表）。"
                        "↓の承認カードで内容をご確認の上、ボタンを押されればアップロードいたす。"), None
            f2 = re.sub(r"(作成します|保存します|作成しました|保存しました|作成しますか)", "下書きしました", f)
            return f2 + f"\n\n（↓の承認カードで確認し、ボタンを押すと Aurora に「{title}」として保存されます）", None
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
    # ③.5(cmd_494 5便): 単一行完結形「〇〇(uid31)へ『本文』を送信しました/送りました/します」
    # (qwenが「さん」抜き・引用ブロックなしで1行に収める頻出形——③は「さん」＋別行引用を要件とするため
    # 拾えない。名前直後の(uidN)からuidを直接取れるので、roster逆引きより先にこちらを優先する)。
    if not (to and body):
        mu = re.search(r"([^\s、。：:（(『]+)\s*[（(]uid\s*(\d+)[）)]\s*(?:へ|に)\s*[『「](.+?)[』」]\s*"
                       r"(?:を|と)?\s*(?:送信しました|送りました|お送りしました|送ります|送信します|DMしました|DMします|連絡しました)\s*[。\.]?", f)
        if mu:
            to = mu.group(2)
            body = mu.group(3).strip()
            cut = (mu.start(), mu.end())
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
        return final, None
    args = {"to_user_id": to, "body": _clean_dm_body(body)}   # salvage経路のDMもプレーンテキスト整形(読みやすさ)
    if who.get("uid"):
        args["actor_id"] = who["uid"]
    summary = _action_summary("send_message", args)
    pid = _register_pending("send_message", args, who.get("uid"), summary, origin="user", query=query, trace_id=trace_id)
    if pid is None:                                    # cmd_494: 中身欠如→起票せず聞き返す(fail-closed)
        return _DM_BODY_INCOMPLETE_MSG, None
    pending_actions.append({"id": pid, "tool": "send_message", "args": args, "summary": summary})
    if cut:
        f = (f[:cut[0]] + f[cut[1]:]).strip()
    else:
        f = re.sub(r"(?m)^\s*[>＞]\s*.+$", "", f)          # 本文はカードに出すので引用ブロックを除去
    f = re.sub(r"(送信しました|送りました|お送りしました|DMしました|連絡しました)", "下書きしました", f)
    f = re.sub(r"(送信します|送ります|DMします)", "下書きします", f)
    f = re.sub(r"\n{3,}", "\n\n", f).strip()
    note = "（↓の承認カードで本文を確認・編集し、ボタンを押すと送信されます）"
    return ((f + "\n\n" + note) if f else note), None


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


# DM本文に問い/依頼が残っているか。これを失った下書きは送っても用を成さぬ(表だけのDMを実際に送った реальный事故)。
# 殿が Aurora への保存/アップを頼んだか(錨は応答の言い回しでなく依頼そのもの)。
_AURORA_SAVE_REQ_RE = re.compile(
    r"(aurora|オーロラ)[^。\n]{0,10}(に|へ|で)[^。\n]{0,10}(アップ|上げ|保存|登録|起票|作成|載せ|投稿|残)|"
    # 裸の『して』は入れぬ——『Auroraの資料を読んで説明して』が保存依頼に化ける(自作ゲートが捕えた)。
    r"(aurora|オーロラ)[^。\n]{0,8}(資料|ノート|文書|ドキュメント)[^。\n]{0,6}(にして|に直して|化して|作って|作成)", re.I)
_ASK_KEEP_RE = re.compile(r"[？?]|ください|下さい|いただけ|頂け|ますか|ましょうか|願いま|お願い|"
                          r"ご確認|ご教示|ご意見|ご返信|教えて", re.I)

# 保存意図判定(層1・目標①): 語彙列挙でなく構造(Aurora語+委任性)で拾う。未知の新語(AC5)にも耐える。
_AURORA_WORD_RE = re.compile(r"(aurora|オーロラ)", re.I)
# (1-A) 読取(閲覧/検索)の標識。近傍距離窓は廃止(将軍指摘cmd_485差戻: 長文の読取依頼が窓を越えて
# すり抜けていた)。正規表現自体は語の有無のみを見る(節の切り出しは呼出側の責務)。
# (cmd_486是正・欠陥2) 呼出側(_wants_aurora_save)は本regexを全文でなく
# _aurora_read_verb_same_clause 経由でAurora語と同一節に限定して用いる——編集/読取語
# (_AURORA_EDIT_READ_VERB_RE)側は先に節単位化されていたのに読取側だけ全文評価のままだった
# 非対称が、無関係な節の読取語に保存依頼を殺させていた(将軍実測)。
_AURORA_READ_RE = re.compile(
    r"(読|見せ|見た|検索|探|調べ|説明|教え|何がある|ある[？?]|参照|開い|表示して|一覧|"
    r"もう一度|再度|出して|確認して|どうなった|どこ)", re.I)
# (1-A') 保存動詞が完了・受身形(既存物への言及)で使われている標識。
# 「保存した/アップ済み/登録されている/以前アップした」等は"これから保存せよ"ではなく
# "既に保存済みの物"を指す言及であり、読取語が無くとも即断路を陽性に倒してはならない
# (将軍実測 subtask_485_qc2「Auroraに登録されている資料の一覧を出して」に読取語「一覧」を
# 追加してもなお別文面で再発しうるため、時制側も機構化して二重に塞ぐ=軍師推奨(B))。
_AURORA_SAVED_REF_RE = re.compile(
    r"(aurora|オーロラ)[^。\n]{0,10}(に|へ|で)[^。\n]{0,15}"
    r"(アップ|上げ|保存|登録|起票|作成|載せ|投稿|残し?)"
    r"(済み|済|した|してある|されている|されてある|してた|してあった)|"
    r"以前[^。\n]{0,6}(アップ|上げ|保存|登録|起票|作成|載せ|投稿|残)した", re.I)
# 明示的な保存動詞(温存・即断路): 既存 _AURORA_SAVE_REQ_RE をそのまま使う。
# (D・cmd_485_impl4是正) 即断路は「保存動詞の直後が依頼形」の時のみ許す構造に変更。
# 旧実装は _AURORA_SAVE_REQ_RE(動詞の存在のみ)+ not _read + not _saved_ref で通していたため、
# 「上げたやつ」「しておいたはず」「されたと思う」等、_AURORA_SAVED_REF_RE の語彙列挙が
# 拾いきれない過去形・推量形・伝聞形が素通りしていた(将軍実測3件・subtask_485_qc3)。
# 依頼形そのものを即断路の必要条件にすることで、時制語の追加列挙を不要にする。
# (H・cmd_485_impl5是正・残穴D→cmd_485_impl6で列挙除外から文字種遷移判定へ再転換)
# 保存動詞の直前が別語の語中でないことを要求する語境界配慮。「リストアップして」
# 「ピックアップして」「クローズアップして」の"アップして"部分が保存動詞「アップ」に誤一致するのを防ぐ。
# 旧実装は直前語を(リスト|ピック|クローズ)と列挙する否定先読みだったため、軍師qc5実測で
# 「バージョンアップ」「レベルアップ」等の未列挙複合語が素通りした(AC10生成的組合せテストで発見)。
# 個別語の列挙ではなく、直前1文字がカタカナ(=カタカナ複合語の語中である)か否かという
# 文字種の遷移そのもので判定する——これは有限列挙でなく構造(語境界)なので、
# 未知のカタカナ複合語にも原理的に耐える(掟: 列挙でなく構造で拾う)。
_AURORA_IMMEDIATE_TAIL_VERB_BOUNDARY_NEG = r"(?<![ァ-ヴー])"
# (J・cmd_485_impl5是正・退行) 助詞(を|の)の介在を許容。「保存をお願い」「登録をお願いします」
# 「アップをお願い」等の丁寧形が、助詞なし語尾のみを想定していた旧実装で陰性化していた退行を是正。
# (M・cmd_485_impl6是正・qc5 AC5) 「頼む」を「頼[むみ]」へ活用形展開。「頼みたい」(連用形+たい)が
# 語幹一致せず漏れていた(qc5実測)。新語の追加列挙ではなく同一動詞の活用系統の拡張であり、
# LLM経路(意味判定)の負担を実行頻度の高い定型からは減らす目的。
_AURORA_IMMEDIATE_REQUEST_TAIL_RE = re.compile(
    _AURORA_IMMEDIATE_TAIL_VERB_BOUNDARY_NEG +
    r"(アップ|上げ|保存|登録|起票|作成|載せ|投稿|残)(を|の)?\s*"
    r"(してくれ|してください|して下さい|しといて|しておいて|お願い|頼[むみ]|よろしく|て欲しい|てほしい|願いた|頂きたい|いただきたい|して(?![おて]))")
# (1-B) 依頼形(委任性)の標識。動詞を列挙せず「〜して/してくれ/してください/しておいて/させて」等の語尾で取る。
_ASK_DELEGATE_RE = re.compile(r"(てくれ|てください|て下さい|ておいて|といて|させて|お願い|頼む|願いた|頂きたい|いただきたい|て[？?]|て$)")
# 灰色判定の一方の材料: 読取語と共起した時だけ意味が割れる保存示唆語(「見て、まとめて」等)。
_SAVE_HINT_RE = re.compile(r"(まとめ|書き起こ|記録|整理|残し|残す)")
# (G・cmd_485_impl5是正・残穴C) (1-B)の直前で陰性確定させる除外リスト。依頼されている動作が
# 『保存』ではなく『編集・加工・整形・要約・修正等』であり、かつ目的語が既存のAurora資料である場合、
# これは保存依頼ではない。語彙を(1-B)の陽性条件に足すのではなく、編集/読取動詞を陰性標識として
# 新設し先に弾く方式を採る——未知の保存語(書きとどめて等)は(1-B)の対象外語彙のままなので
# 引き続き陽性判定され、退行を避けられる(軍師推奨・除外リスト方式)。
_AURORA_EDIT_READ_VERB_RE = re.compile(
    r"(整え|要約|修正|直し|削除|リストアップ|ピックアップ|クローズアップ|一覧化|翻訳|変換|チェック|確認|開い|見せ|探し)")
# (D・cmd_486是正・節跨ぎ誤該当) 上記は文中どこに在っても陰性確定させていたため、Aurora語と無関係な
# 節に在る編集/読取語(「<PJ名>の進捗を確認した上で、Auroraにまとめて」の「確認」等)が
# 保存依頼を巻き込んで殺していた(将軍実測)。節を跨いだ誤該当を機構で切る=Aurora語との同一節性を
# 要求する(列挙でなく構造)。クエリを節に分割し、Aurora語を含む節のみを編集/読取判定の対象とする。
_AURORA_CLAUSE_SPLIT_RE = re.compile(r"(?<=[、。\n])|(?<=てから)|(?<=た上で)")


def _aurora_edit_read_verb_same_clause(q):
    """Aurora語を含む節に編集/読取動詞が在る時のみ True(節を跨いだ誤該当を防ぐ)。"""
    for clause in _AURORA_CLAUSE_SPLIT_RE.split(q or ""):
        if _AURORA_WORD_RE.search(clause) and _AURORA_EDIT_READ_VERB_RE.search(clause):
            return True
    return False


def _aurora_read_verb_same_clause(q):
    """(cmd_486是正・欠陥2) Aurora語を含む節に読取語(_AURORA_READ_RE)が在る時のみ True。
    編集動詞側は_aurora_edit_read_verb_same_clauseで既に節単位化済みだったが、読取判定
    (_AURORA_READ_RE)だけは全文評価のまま残っていたため、片側だけ節単位に揃った非対称が生じ、
    「先に資料を見せて、それをAuroraに保存して」のように無関係な節の読取語「見せて」が
    別節の保存依頼を殺す実害があった(将軍実測・cmd_486差し戻し)。読取語も編集語と同型の
    節スコープに揃える(構造の対称性を回復)。"""
    for clause in _AURORA_CLAUSE_SPLIT_RE.split(q or ""):
        if _AURORA_WORD_RE.search(clause) and _AURORA_READ_RE.search(clause):
            return True
    return False


def _aurora_clause_delegate_form(q):
    """(D派生) 依頼形の標識も、Aurora語を含む節自身で判定する(節を跨いだ希薄化を防ぐ)。
    「Auroraにまとめて、あとで確認する」は全文では末尾が依頼形でなくなり(1-B)を素通りしていたが、
    Aurora語を含む節「Auroraにまとめて、」自体は依頼形であり、後続の無関係な節に判定を
    引きずられてはならない。全文一致も併せて見る(OR)ので退行は起きない——狭める変更ではなく
    見落としを拾う変更。"""
    if _ASK_DELEGATE_RE.search(q or ""):
        return True
    for clause in _AURORA_CLAUSE_SPLIT_RE.split(q or ""):
        if _AURORA_WORD_RE.search(clause) and _ASK_DELEGATE_RE.search(clause.rstrip("、。\n")):
            return True
    return False
# (F・cmd_485_impl4是正・残穴B) 受益・完了の複合形は「既存物の再提示要求」であり保存依頼ではない。
# 「〜てもらった/〜ていただいた/〜たやつ/〜た分/〜たもの」および疑問形でない「〜てくれた」を
# 陰性標識として機構化する。ただし「てくれた？」(kiyotomo殿実発話・AC1)は"これから"の依頼確認で
# あり引き続き陽性を要するため、直後に疑問符が続く場合はこの陰性標識から除外する
# (軍師助言: 区別の鍵は後続の要求動詞(もう一度/再度/出して等)の有無ではなく、疑問形か否かに置く
#  —— 疑問形は未来への確認、平叙の完了形は既存物への言及という統語的差で機構的に割れる)。
_BENEFIT_COMPLETION_NEG_RE = re.compile(
    r"てもらった|ていただいた|てくれた(?![？?])|たやつ|た分|たもの")


# trace観測(cmd_487追加AC): 直近の_wants_aurora_save判定が辿った決着経路を記録する。
# 呼出側(_salvage_text_toolcall→chat_server本体)がcasper_trace.emitへ載せるための受け渡し専用。
# ★三値(True/False/None)の設計・比較箇所には一切手を入れず、経路名だけを併走記録する副作用。
_AU_LAST_ROUTE = {"route": None}


def _decision_record(pj, digests_fired, rag_hits, web_fired, pending_actions, anchor=None, declines=None):
    """【AC8決定記録(cmd_508第1便・最重要成果物)】turnごとに機構が吐く決定記録の骨組み。
    Fable「服従でなく機構で強制せよは判定器にも適用される掟」に従い、qwenの文面(服従の言葉)ではなく
    機構が実際に何を引いたかを測る。これが他の全ACの検証手段——文面だけで判ずるな(AC3以降が拠って立つ土台)。
    対象スロット: _pj_resolve()の3値解決結果(閉集合ゆえ machine-checkable)。pj は既存trace形式
    {"status","n","path"}(name一覧そのものは既存traceに無いためnを引き継ぐ・新規キー追加はしない)。
    ★cmd_508第3便(病三): anchor(本turnで_LAST_ANCHORへ記録された対象・kind/key/label)をtarget_slotへ
    追加する(軍師QC1申し送り: 「第3便では anchor を target_slot へ足す必要がある」)。
    corpus: 母集合(rag_hits)がゼロより大きければ vault(RAG)を引いたと判る。
    web発火の有無: casper_web.should_search()が実際に検索を実行したか(should_search=Trueだけでは発火とは言わぬ)。
    カードpid: この turn で積まれた pending_actions の id 一覧(何を確定的に提示したかの証跡)。
    ★cmd_510第3便(観測の機構・AC8降車ログ): declines(このturnで_record_declineが刻んだ一覧)を
    そのまま載せる。新機構は作らず、既存の_DECLINE_LOGを decision_record へ横流しするだけ。"""
    return {
        "target_slot": {"status": (pj or {}).get("status"), "n": (pj or {}).get("n"),
                         "path": (pj or {}).get("path"),
                         "anchor": ({"kind": anchor.get("kind"), "key": anchor.get("key"),
                                     "label": anchor.get("label")} if anchor else None)},
        "corpus": "vault_rag" if (rag_hits or 0) > 0 else None,
        "population_n": rag_hits or 0,
        "web_fired": bool(web_fired),
        "card_pids": [a.get("id") for a in (pending_actions or []) if a.get("id")],
        "declines": list(declines or []),
    }


def _trace_payload(trace_id, query, actor, thread, routed, fastpath, echoed, vch,
                    injected_facts, resp_ids, cont, gate, pj, rag_hits, ctx_len,
                    gen_sec, salvaged, validated, gloss, guarded_claim, abstained,
                    digests_fired, final_len, cards, fewshot_used, topic=None,
                    stream_claim_held=0, web_fired=False, pending_actions=None,
                    turn_start_ts=None, send_intent_gate=None, llm_calls=None):
    """casper_trace.emitへ渡すpayload本体の組立(cmd_487是正: 配線をgateでast抽出検査可能にする)。
    ★au_decision/au_routeは _AU_LAST_ROUTE から読む(呼出側の値でなくここで直接参照することで、
    この2キーを消す/読み違える突然変異がここ1箇所を検査するだけで捕まる)。
    ★cmd_510第3便: turn_start_ts(このturnの開始時刻)以降に_DECLINE_LOG[thread]へ積まれた分だけを
    このturnの降車として渡す(_DECLINE_LOGはthread単位の累積台帳ゆえ、turnの境界はts比較で切り出す・
    新たな台帳は作らない)。
    ★cmd_511第2便(AC10・観測増設のみ): send_intent_gateは_turn_is_send_intent(層1)がこのturnで
    実際に返した判定値(True=送信turn/False=読取turn)。一週間後の監査材料として必要
    (現状traceにこの判定値が残らず、両義語是正の効果を事後に検証できなかった)。"""
    _declines = [d for d in (_DECLINE_LOG.get(thread) or [])
                 if turn_start_ts is None or float(d.get("ts") or 0) >= turn_start_ts]
    return {"trace_id": trace_id, "query": query, "actor": actor, "thread": thread,
            "routed": bool(routed), "action": (routed or {}).get("tool"),
            "fastpath": fastpath, "echoed": echoed, "vch": vch,
            "injected_facts": injected_facts, "resp_ids": resp_ids, "cont": cont,
            "gate": gate,
            "pj": pj,
            "topic": topic,   # cmd_492第1便: _LAST_TOPIC記録の観測用(まだ判定/注入には使わない)
            "rag_hits": rag_hits, "ctx_len": ctx_len,
            "gen_sec": gen_sec, "salvaged": salvaged, "validated": validated, "gloss": gloss,
            "guarded_claim": guarded_claim, "abstained": abstained,
            "stream_claim_held": stream_claim_held,   # cmd_494 3便: ストリーム側で保留した完了主張行の数(0=発火なし)
            "digests_fired": digests_fired,
            "au_decision": _AU_LAST_ROUTE.get("decision"), "au_route": _AU_LAST_ROUTE.get("route"),  # cmd_487追加AC: 層1(Aurora保存意図)の判定値と決着経路
            "send_intent_gate": send_intent_gate,   # cmd_511第2便AC10: 層1(_turn_is_send_intent)の判定値(True/False)
            "final_len": final_len, "cards": cards, "fewshot_used": fewshot_used,
            "llm_calls": list(llm_calls or []),           # cmd_515手当2(AC-L1/AC-L2): このturnの推論機呼出記録
            "llm_calls_n": len(llm_calls or []),           # AC-L2: 1turnあたりの呼出回数
            "llm_wait_total_sec": round(sum(c.get("wait_sec") or 0 for c in (llm_calls or [])), 3),  # このturnで推論機を待った合計
            "decision_record": _decision_record(pj, digests_fired, rag_hits, web_fired, pending_actions,
                                                 anchor=topic, declines=_declines)}  # AC8(cmd_508)・anchor=cmd_508第3便/declines=cmd_510第3便(降車ログ)


def _wants_aurora_save(query):
    """殿がAuroraへ新規に保存/記録することを頼んでいるか。戻り値は三値(True|False|None・cmd_486(A)):
      True  = 保存意図あり(規則で確信 or 分類器がsave=true)
      False = 保存意図なし(規則で陰性確定 or 分類器がsave=false)
      None  = 判定不能(分類器が答えぬ。Aurora語+依頼形は在るが真偽を決められぬ)
    呼出側は必ず `is True` / `is None` の明示比較で扱うこと(Noneは偽値なのでbool判定に潰すと
    三値化の意味が失われる)。
    cmd_485_impl6(K)(L): 5巡の実測(軍師 subtask_485_qc5)が「列挙で閉じることは原理的に不可能」
    (日本語の編集動詞・複合語・丁寧形は事実上無限)と結論したため、語彙列挙を主経路から降ろし、
    LLM意味判定(_wants_aurora_save_llm)を主経路に格上げする。即断路は「明示的保存動詞+依頼形+
    編集/読取語なし」の狭い確信ケースのみ残す(高速路として温存・完全削除はしない)。
    (L) 加えて、意味判定に回さぬ規則ベース陽性((1-B)相当)は「保存示唆語または明示的保存動詞が
    在る時のみ」の白リストへ絞り、それ以外(編集/読取/未知動詞のみで保存示唆が無い)は陰性とする
    (fail-closed。未知の保存語はここで陰性化しても(K)のLLM経路で拾われる設計)。
    (A・cmd_486是正) LLM側が例外・timeout・解析不能の時はNoneをそのまま透過する(以前はFalseに
    倒しており「答えなかった」と「陰性と答えた」を混同していた。fail-closedは起票しない側で保つ
    ——Noneは起票せず聞き返しに回すので安全性は変わらない)。
    Aurora語を含まぬ全turnでLLMは呼ばぬ(冷間timeout対策・実運用で稀)。
    (cmd_487追加AC) 決着経路を _AU_LAST_ROUTE へ併記する:
      "null"            = Aurora語なし(Aurora無関係のturn)
      "rule_negative"   = 規則で陰性確定(読取/既存物言及/編集動詞/依頼形なし)
      "immediate"       = 明示的保存動詞+依頼形の即断路(True確定)
      "llm"             = 分類器(_wants_aurora_save_llm)へ回した(結果がTrue/False/Noneいずれでも経路はllm)"""
    q = query or ""
    if not _AURORA_WORD_RE.search(q):                     # 1. Aurora語なし → False(不変)
        _AU_LAST_ROUTE["route"] = None
        return False
    _read = _aurora_read_verb_same_clause(q)  # (D派生・cmd_486是正) 読取語もAurora語と同一節のみ(節跨ぎ対称化)
    _saved_ref = bool(_AURORA_SAVED_REF_RE.search(q))     # 既存物への言及(完了・受身形)か
    _benefit_done = bool(_BENEFIT_COMPLETION_NEG_RE.search(q))  # 受益・完了の複合形(既存物の再提示要求)か
    _edit_read_verb = _aurora_edit_read_verb_same_clause(q)  # (G)(D) 編集/読取動詞・Aurora語と同一節のみ
    # 2. 読取標識が在り、保存示唆が無い → False(不変・(1-A))
    if _read and not _SAVE_HINT_RE.search(q):
        _AU_LAST_ROUTE["route"] = "rule_negative"
        return False
    # 3. 既存物言及・受益完了 → False(不変)
    if _saved_ref or _benefit_done:
        _AU_LAST_ROUTE["route"] = "rule_negative"
        return False
    # 4. 編集/読取動詞 → False(不変・(G)。(L)で白リスト側にも絞りをかけるため二重の防壁)
    if _edit_read_verb:
        _AU_LAST_ROUTE["route"] = "rule_negative"
        return False
    # 5. 明示的保存動詞+依頼形+上記いずれの陰性標識にも該当しない → True(即断路・高確信ケースのみ)
    if bool(_AURORA_IMMEDIATE_REQUEST_TAIL_RE.search(q)) and not _read and not _saved_ref and not _benefit_done and not _edit_read_verb:
        _AU_LAST_ROUTE["route"] = "immediate"
        return True
    # 6. 上記のいずれにも該当せず、Aurora語+何らかの依頼形が在る → LLM意味判定(K)。
    #    灰色判定を大幅拡張(旧: 読取語+保存示唆語共起のみ → 新: 依頼形が在る全turn)。
    #    戻り値は三値のまま透過する(True/False/None・(A))。
    # ★体言止め(「Auroraにアップ」)も灰色として同じ分類器へ回す。
    #   依頼形の語尾だけを入口にすると、投函の添え書き欄に書かれた指示が
    #   step7 で陰性確定してしまう(2026-08-26 18:33 の実害)。
    if _aurora_clause_delegate_form(q) or _aurora_noun_stop_request(q):
        _AU_LAST_ROUTE["route"] = "llm"
        return _wants_aurora_save_llm(q)
    # 7. 依頼形すら無い → False(不変)
    _AU_LAST_ROUTE["route"] = "rule_negative"
    return False


# 体言止めの保存指示。指示は短く、動詞で言い切られる——特にファイル投函の添え書き欄。
# 【殿御下命2026-08-27】実害(2026-08-26 18:33): kiyotomo殿が .rtf を投じ添え書きに
#   「Auroraにアップ」と書いた。届いた発話は `sorafune 様　MTG.rtf — 「Auroraにアップ」`。
#   依頼形の語尾が無いため step7 の「依頼形すら無い」に落ち、rule_negative で陰性確定。
#   ★「して」を足すだけで immediate/True になる(実測で再現)。
#   規則はチャットの文に合わせて作られており、**添え書きの体言止めに合っていなかった**。
# ★カタカナ語中一致は弾く(バックアップ/セットアップ/フォローアップ)。即断路と同じ lookbehind を使う。
# ★短い行に限る。長い文書の末尾がたまたま保存語で終わる「報告の記述」を指示と読まぬ。
_AURORA_NOUN_STOP_VERB_RE = re.compile(
    r"(?<![ァ-ヴー])(アップ(ロード)?|保存|登録|起票|投稿|格納|掲載|記録)"
    r"\s*[」』】\)）”\"'。、．，]*\s*$")
_AURORA_NOUN_STOP_MAX = 24
# 投函の添え書きは鉤括弧に入って届く: `sorafune 様　MTG.rtf — 「Auroraにアップ」`。
# ★行全体の長さで締めると、飾り(ファイル名+区切り)の分だけ閾値を緩めねばならず、
#   38字の『記述』まで指示と読む隙ができる(実測で閾値40が薄氷であった)。
#   鉤括弧が在るならその中身こそが人の書いた指示ゆえ、そこだけを測る。
_AURORA_QUOTED_RE = re.compile(r"[「『]([^「」『』]{1,60})[」』]")


def _aurora_noun_stop_request(query):
    """末尾の行(または其処の鉤括弧の中身)が保存動詞で言い切られている=体言止めの指示か。

    ★これ自体を True(即断)にはせぬ。step6 の分類器へ回すための『灰色の入口』である
      ——「Auroraにアップ」は指示だが、「資料はAuroraにアップ」は報告かもしれぬ。
      その見分けは語彙表でなく意味判定の仕事にござる(fail-closed は起票せぬ側で保たれる)。
    """
    lines = [ln.strip() for ln in (query or "").splitlines() if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    q = _AURORA_QUOTED_RE.findall(last)
    cand = q[-1].strip() if q else last          # 鉤括弧が在れば中身が人の書いた指示
    if len(cand) > _AURORA_NOUN_STOP_MAX:
        return False
    return bool(_AURORA_NOUN_STOP_VERB_RE.search(cand))


def _wants_aurora_save_llm(query):
    """(K・cmd_485_impl6→(A)cmd_486是正) 層1主経路の意味分類。従来は灰色(読取語+保存示唆語共起)限定の
    フォールバックだったが、5巡の実測(軍師 subtask_485_qc5)が語彙列挙の限界を示したため、
    『Aurora語+依頼形』全turnへ適用範囲を拡大した主経路とする。
    プロンプトは軍師qc5実測の誤起票類型(カタカナ複合語「アップ」語中一致)を明示的に反例として
    列挙する——qwenは素の説明だけでは「バックアップ」等を保存動詞と誤認したため(実測)。
    戻り値は三値: True(save=true)/False(save=false)/None(判定不能=例外・timeout・JSON解析失敗・
    keyの欠落・save値がbool型でない)。cmd_485は例外時Falseに倒し陰性固定していたが、これは
    「分類器が答えなかった」ことと「分類器が陰性と答えた」ことを混同させ、kiyotomo4発話が
    無言でFalse落ちする実害を招いた(cmd_486真因)。以後、判定不能は上位(_wants_aurora_save)で
    Noneとして扱い、fail-closed(起票しない)は保ったまま聞き返しへ回す設計に切替える。"""
    try:
        r = _ollama_json(
            "あなたは意図分類器。ユーザ発話が『Aurora(社内ナレッジベース)へ新規に保存/記録すること』を"
            "頼んでいるか否かを判定せよ。"
            "重要な注意: 「バックアップ」「セットアップ」「フォローアップ」「ブラッシュアップ」「スタンバイ」等の"
            "カタカナ複合語は、たとえ『アップ』を含んでいても、Aurora自体への保存動詞ではない"
            "(それぞれ意味は: バックアップ=控えを取る/元データの複製、セットアップ=準備・設定、"
            "フォローアップ=後続対応、ブラッシュアップ=改善、スタンバイ=待機)。"
            "これらは既存の対象を『扱う・処理する』意図であり、Auroraへの新規保存の意図ではないので save=false とせよ。"
            "既存資料の閲覧・検索・説明・編集・整形・改名・校正・分割・統合の依頼も save=false。"
            "『まとめて』『保存して』『登録して』『アップして』『記録して』『書きとどめて』等、Aurora自体を保存先として"
            "明示/示唆する依頼のみ save=true。JSONのみ: {\"save\":true|false}",
            query or "", num_predict=60)
        o = json.loads(r)
        if "save" not in o or not isinstance(o["save"], bool):
            return None
        return o["save"]
    except Exception:
        return None


# (cmd_490 手当2・B-1) Casper自身の使い方を尋ねているか。三値(True|False|None・_wants_aurora_saveと同形):
#   True  = Casper(このアシスタント自身)の使い方/アクセス方法/機能/設定を尋ねている
#   False = 案件・タスク・人物・議事録・資料等、Casper自身以外の内容についての問い
#   None  = 判定不能(分類器が例外/timeout/解析不能)
# 疑問形か依頼形かの粗い判定に使う(冷間timeout対策・全turnでLLMを呼ばぬ)。
# ★境界探索実測(将軍実測4発話中2件): 「携帯ではいりたいんだけど」「携帯で、キャスパーにはいりたい
# です。」はいずれも疑問符が無く、依頼形(1-B・てくれ/お願い等)にも一致しない——「〜たい」の
# 願望形(そのままの意志表明・平叙形)が疑問形/依頼形いずれの網にも掛からず素通りしていた。
# 願望形も「アクセス方法を尋ねる」turnの自然な言い回しの一部ゆえ、依頼形の標識へ加える。
_QUESTION_FORM_RE = re.compile(r"[？?]|かな|かしら|でしょうか|ますか|んの|の[？?]?$")
_DESIRE_FORM_RE = re.compile(r"たい(んだ|です|んです)?(けど|が|けれど)?[。.]?$")
_REQUEST_FORM_RE = re.compile(_ASK_DELEGATE_RE.pattern + "|" + _DESIRE_FORM_RE.pattern)


def _grounding_state(hits, fulltext, query):
    """材料の接地状態を三値で返す。呼出側は`is`で明示比較すること(_asks_about_casperと同形)。
    戻り: "grounded" | "thin" | "none"
      grounded = 関連記録が在る(通常)               → 従来通り
      thin     = 関連記録0件だが全文だけ在る(★実害の形) → 断言を禁ずる注意書きを添える
      none     = 材料が何も無い                      → 正直に「記録に無い」と言わせる
    ★件数の閾値で切らない。hitsが在ればgroundedとし、過剰な沈黙を招かない(cmd_497軍師実測: 単一閾値
    では'おはよう'のスコア1.027が実害'携帯ではいりたい'の0.545を上回り解けない。件数でも閾値でもなく
    「材料の構造の齟齬」=hits=0なのにfullnoteが付く、で判ずる)。"""
    if hits:
        return "grounded"
    return "thin" if fulltext else "none"


_GROUNDING_NOTE = {
    "thin": ("\n\n※この資料は問いに直接該当せぬ可能性が高い(関連記録の検索は0件であった)。"
             "この資料に書かれておらぬことを、書かれておるかのように述べるな。"
             "問いに答える材料が無ければ『社内の記録には見当たりませなんだ』と正直に述べよ。\n"),
    "none": ("\n\n※今回の問いに対し社内記録から材料を取得できなんだ。"
             "推測で手順や機能を作って述べるな。分からぬことは分からぬと申せ。\n"),
}


def _build_grounding_block(gstate, src, fulltext):
    """cmd_497 第2便(欠陥B是正): 注意書き(_GROUNDING_NOTE)を fulltext の有無から独立させて注入する。
    以前は fullnote = (...) if fulltext else "" の内側に注意書きを置いていた為、"none"
    (fulltext無し)の時は注意書きが★永久に注入されぬ死に分岐だった。注意書きはgstateにのみ従い、
    全文引用はfulltextにのみ従う、という2つの独立した条件で組む。
    戻り: (gstateに対応する注意書き) + (fulltextがあれば全文引用ブロック、無ければ空文字列)"""
    gnote = _GROUNDING_NOTE.get(gstate, "")
    fullnote = ("\n\n## 該当資料の全文 (" + str(src) + ")\n" + fulltext[:7000]) if fulltext else ""
    return gnote + fullnote


def _asks_about_casper(query):
    """Casper自身(使い方・入り方・機能・通知設定)を尋ねているかの三値判定。呼出側は必ず
    `is True` / `is None` の明示比較で扱うこと。
    呼出条件(冷間timeout対策): 「疑問形 or 依頼形」かつ「案件語(PJ名解決uniqueがある等)が無い」turnのみ
    分類器へ回す。_pj_resolve(query)[0] == "unique" なら案件の問いゆえ即False(既存機構の再利用)。"""
    q = query or ""
    if not q:
        return False
    if _pj_resolve(q)[0] == "unique":                      # 案件名が一意に解決→案件の問い(Casper自身の話ではない)
        return False
    if not (_QUESTION_FORM_RE.search(q) or _REQUEST_FORM_RE.search(q)):
        return False                                       # 疑問形でも依頼形でもない→対象外(LLMを呼ばぬ)
    return _turn_memo(("asks_about_casper", q), lambda: _asks_about_casper_llm(q))


# cmd_498 穴B手当1: 「そのturnに正典が要るか」を先に判じ(_asks_about_casper再利用)、要る時だけ序列を動かす。
_CANON_SRCS = ("30_culture_rules/casper_howto.md",)   # 家老裁定: 1ファイルの為の設定機構は過剰・ハードコードで進める


def _prioritize_canon(hits, canon_srcs=_CANON_SRCS):
    """正典を上位へ引き上げる。捨てるのではなく【並べ替える】だけ。
    議事録も残るゆえ、議事録が本当に要る問いでも消えない(AC4の担保)。"""
    canon = [h for h in hits if any(s in h for s in canon_srcs)]
    rest = [h for h in hits if h not in canon]
    return canon + rest


# cmd_498 第2便・欠陥A手当2: 母集合(hybrid()の戻り)に正典が0件の時は【並べ替えでは救えぬ】。
# casper_rag.search()自体はcandidatesで拾える正典chunkがあっても、より大きな(budget超過)chunkが
# 上位にあると break で全滅する既存の字面挙動(casper_rag.pyのアルゴリズムは触れぬ制約ゆえ、
# 呼出側=chat_server.pyで【直接差し込む】)。cmd_490の正典直接注入と同じ思想(retrieve-then-render)。
_CANON_INJECT_KWS = ("8443", "携帯")   # AC7判定と同じ語(このどちらかを含むchunkのみ差し込む=無関係chunk混入を防ぐ)
_CANON_INJECT_CACHE = {"mtime": 0.0, "lines": []}


_CANON_INJECT_FRONTMATTER_RE = re.compile(r"^(name|tags|project)\s*:\s")   # cmd_498第3便: frontmatter行を構造で判定


def _canon_inject_lines(canon_srcs=_CANON_SRCS, kws=_CANON_INJECT_KWS, limit=2):
    """casper_howto.md を casper_rag._chunks() と同じ切り方で読み、kws を含む chunk を
    hits と同じ整形("[title] text")で最大 limit 件返す。ファイル不在時は空リスト(is thin/none側へ倒す)。
    mtime ホットリロード(他の正典系機構と同型)。
    cmd_498第3便(欠陥C是正): 「文書順の先頭」ではなく【答え(8443)を含むもの優先】で選ぶ。
    ★frontmatter(name:/tags:/project:で始まるchunk)は手順を一切含まぬゆえ除外する(構造判定・
    kws一致だけでは"携帯"がtagsに載っている等で誤選出される=欠陥Cの再発防止)。"""
    try:
        hpath = pack_paths.vault(*canon_srcs[0].split("/"))
        m = os.path.getmtime(hpath)
    except Exception:
        return []
    if m == _CANON_INJECT_CACHE["mtime"]:
        return list(_CANON_INJECT_CACHE["lines"])
    try:
        title, chunks = casper_rag._chunks(hpath)
        body = [c for c in chunks if not _CANON_INJECT_FRONTMATTER_RE.match(c)]
        primary_kw = kws[0]   # "8443" — 答えを実際に含む語を最優先で選ぶ
        primary = [c for c in body if primary_kw in c]
        fallback = [c for c in body if primary_kw not in c and any(k in c for k in kws)]
        picked = (primary + fallback)[:limit]
        lines = [f"[{title or canon_srcs[0]}] {c}" for c in picked]
    except Exception:
        lines = []
    _CANON_INJECT_CACHE.update({"mtime": m, "lines": lines})
    return list(lines)


def _inject_canon(hits, canon_srcs=_CANON_SRCS, kws=_CANON_INJECT_KWS):
    """欠陥A本丸: hits(hybrid()の戻り相当)に正典が1件も無ければ、機構が直接1〜2件差し込む
    (検索の当たり外れに頼らない)。既に正典が在れば何もしない(_prioritize_canonで足りる=退行なし)。
    ★存在判定は canon_srcs(パス文字列)ではなく kws(AC7と同一の語)で行う——hits の行は
    "[title] text" 形式で title を持てば src パスは行内に一切現れず(実測で発見)、
    _prioritize_canon のパス一致判定は casper_howto.md 相手には常に不一致=二重挿入の温床になる。"""
    if any(any(k in h for k in kws) for h in hits):
        return hits
    injected = _canon_inject_lines(canon_srcs, kws)
    if not injected:
        return hits
    return injected + hits


def _asks_about_casper_llm(query):
    """(B-1実体) 軍師実測で11/11正答・揺らぎゼロ(各3回試験)を確認したプロンプトをそのまま使う。
    戻り値は三値: True(about_casper=true)/False(about_casper=false)/None(判定不能=例外・timeout・
    JSON解析失敗・keyの欠落・値がbool型でない)。"""
    try:
        r = _ollama_json(
            "あなたは意図分類器。ユーザ発話が『Casper(このアシスタント自身)の使い方・アクセス方法・機能・設定について尋ねている』か否かだけを判定せよ。"
            "案件・プロジェクト・タスク・人物・議事録・資料の内容についての問いはfalse。"
            "『これ/このツール/キャスパー/Casper』が主語で、使い方・入り方・見方・できること・通知設定を問うていればtrue。"
            "JSONのみ: {\"about_casper\":true|false}",
            query or "", num_predict=60)
        o = json.loads(r)
        if "about_casper" not in o or not isinstance(o["about_casper"], bool):
            return None
        return o["about_casper"]
    except Exception:
        return None


# (B-2/B-3) casper_howto.md(正典)のキャッシュ読取。mtimeホットリロード(digestの他機構と同型)。
_HOWTO_CACHE = {"mtime": 0.0, "body": ""}

# ★体験ガイド(Aurora・社員向けonboarding資料 doc_id 2978eda6)への導線。殿御下命 2026-08-18。
# 【なぜ機構で足すか】正典 casper_howto.md の本文に書くだけでは、
#   ①正典が読めなんだ時(_HOWTO_FALLBACK経路)に案内が消える
#   ②chunk切りの当たり外れで該当節が落ちれば案内も落ちる
# ——ゆえに「Casper自身への問い」と判じた turn には★経路によらず必ず添える。
# 【URLを機構が持つ理由】qwenにURLを覚えさせ・生成させてはならぬ(識別子は生成でなく決定的機構で選ぶ
#   =接地の原則)。一字違えば繋がらぬものを、弱いモデルの記憶に委ねぬ。
_TAIKEN_GUIDE_URL = ("http://nina_notepc_02:8100/doc/casper/2026-07-21/"
                     "casper-taiken-gaido-dekirukoto-sawatsu-temiru")
_TAIKEN_GUIDE_LINE = (
    "\n\n## 【体験ガイド(社内資料)への案内・機構が確定的に添える】\n"
    "答えの末尾に、次の一文を★そのまま添えよ(URLは一字も変えるな・短縮するな):\n"
    "「詳しくは体験ガイドを御覧くだされ → " + _TAIKEN_GUIDE_URL + " 」\n"
    "★このURLを記憶から書き起こすな。上の文字列をそのまま写せ。")

_HOWTO_FALLBACK = ("Casperの使い方について、確かな手順をただいま参照できませなんだ。"
                   "恐れ入るが『携帯 通知 設定』等と言い換えてお尋ねくだされ。")


def _load_casper_howto():
    """casper_howto.md(vault:30_culture_rules)を直接読む(B-2: RAGの当たり外れに委ねぬ確定的取得)。
    ファイル不在・読取失敗時は空文字を返す(呼出側=casper_howto_digestがB-3のフォールバックへ倒す)。"""
    try:
        hpath = pack_paths.vault("30_culture_rules", "casper_howto.md")
        m = os.path.getmtime(hpath)
    except Exception:
        return ""
    if m != _HOWTO_CACHE["mtime"]:
        try:
            _HOWTO_CACHE["body"] = open(hpath, encoding="utf-8").read().strip()
            _HOWTO_CACHE["mtime"] = m
        except Exception:
            return ""
    return _HOWTO_CACHE["body"]


def casper_howto_digest(query):
    """(B-2・B-3) 判定Trueのturnで、casper_howto.mdの正典を機構が【確定的に】システムへ注入する
    (retrieve-then-render)。RAG/条件注入(_CTX_CONDITIONAL)の当たり外れに委ねぬ独立経路——
    見出し語がkwsに一致せずとも、分類器がTrueと答えた turn には必ず注入される。
    判定Trueだが正典が空(ファイル不在・読取失敗)の場合は、qwenに自由作文させず機構が
    正直な文を注入する(B-3・材料ゼロ時の正直な出口)。
    判定FalseまたはNone(分類器がtimeout等で答えられない)の場合は何も注入しない
    (cmd_515手当1: Noneは「判定不能」であって「使い方の話である」ことを意味せぬ——
    無関係turnへの滲出を避ける)。"""
    about = _asks_about_casper(query)
    if about is not True:
        return ""   # False(無関係) または None(判定不能=timeout等) → 何も差さぬ
    body = _load_casper_howto()
    if body:
        return ("\n\n## 【Casperの使い方(正典・vault: casper_howto.md)】\n" + body +
                 "\n\n上記の手順だけで答えよ。ここに無い手順(アプリストア/招待URL/VPN/QRコード/"
                 "IT部門への依頼/リモートデスクトップ等)を一般知識で補うな(=捏造)。"
                 "また、上記に無い外部への問い合わせ・依頼(IT部門へのDM送信提案等)を勧めるな"
                 "——答えは正典に在るゆえ、外部へ問い合わせる筋を提案してはならぬ。"
                 "\n★この正典は他のいかなる定型案内(動画アップロード案内等)にも優先する。"
                 "Casper自身の使い方を問われたturnでは、必ず上記の手順を答えよ。"
                 # ★体験ガイドの導線は【正典が実際に読めた turn のみ】に限る(2026-08-18 巻戻し)。
                 # 【なぜ限ったか — 実害】同日 12:41、殿の「vaultではなくDM検索して」に対し
                 #   Casper は★体験ガイドのURLだけを返し、問いに一切答えなかった。
                 #   about is None(判定不能)の turn にまで導線を添えたため、
                 #   「答えの末尾に添えよ」という指示が★答えそのものを乗っ取った。
                 # 【教訓】添え物は、本文が確かに在る時にしか添えてはならぬ。
                 #   本文の無い出口に添えれば、添え物が本文になる。
                 + _TAIKEN_GUIDE_LINE)
    # ここに来るのは about is True だが本文が空(ファイル不在/読取失敗)の場合のみ
    # (about is None は上の `if about is not True: return ""` で既に排除済み)。
    # ★ここには導線を添えぬ。材料ゼロの正直な出口に案内を足すと、案内が答えに化ける(上記実害)。
    return "\n\n## 【Casperの使い方】\n" + _HOWTO_FALLBACK


# ============================================================
# cmd_503: Aurora一覧照会・存在確認(gate方式・retrieve-then-render)
# 実害(2026-07-31 20:21): 「Aurora内の今日アップデートした資料って何？」に対しqwenが
# 「リアルタイムで照会できません」と自ら文言を作り、2日前のファイルを最新と称した(この文言は
# コードのどこにも無い=qwenの捏造)。casper_tools.pyへは足さない(qwenへ渡さない)——道具を
# 渡すだけではqwenが呼ぶ気にならねば同じ事が起きる。機構がgateで検知し、決定的に呼び、
# 結果を整形して注入し、「上の一覧をそのまま述べよ」と命ずる一連の手当で初めて直る。
# ============================================================
_AURORA_LIST_RE = re.compile(r"(Aurora|オーロラ|あうろら)", re.I)
_AURORA_LIST_INTENT_RE = re.compile(
    r"(上が|あが|アップ|更新|追加|入っ|投稿|載っ)[^。]{0,8}(資料|ドキュメント|もの|の)|"
    r"(資料|ドキュメント)[^。]{0,8}(一覧|リスト|何|なに|ある)")


def _aurora_list_turn(query):
    """Auroraの一覧照会を求めるturnか(gate方式・二値で足りる)。
    ①Auroraを指す語 ②一覧/更新の意図 ③疑問形or依頼形 の三条件。"""
    q = query or ""
    if not _AURORA_LIST_RE.search(q):
        return False
    if not _AURORA_LIST_INTENT_RE.search(q):
        return False
    return bool(_QUESTION_FORM_RE.search(q) or _REQUEST_FORM_RE.search(q))


def _resolve_since(query, today=None):
    """相対日付をsince(YYYY-MM-DD)へ写す。機構が決定的に解く(qwenに日付を作らせない=誤りの温床)。
    戻り: (since:str|None, label:str)  None=期間の指定なし(既定へ)。"""
    t = today or datetime.date.today()
    q = query or ""
    if re.search(r"(今日|本日|きょう)", q):
        return t.isoformat(), "本日"
    if re.search(r"(昨日|きのう)", q):
        return (t - datetime.timedelta(days=1)).isoformat(), "昨日"
    if re.search(r"(今週|直近1?週)", q):
        return (t - datetime.timedelta(days=7)).isoformat(), "直近1週間"
    if re.search(r"(今月|直近1?ヶ?月)", q):
        return (t - datetime.timedelta(days=30)).isoformat(), "直近1ヶ月"
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", q)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", m.group(0)
    return None, ""


def _aurora_fmt_doc_line(d):
    ts = str(d.get("uploaded_at") or "")[:16].replace("T", " ")
    return f"- {d.get('title') or '(無題)'}（{d.get('uploaded_by') or '不明'} / {ts}）"


def aurora_list_digest(who, query):
    """【Aurora一覧=gate/retrieve-then-render(cmd_503)】判定Trueのturnで list_documents を機構が
    決定的に呼び、題・投稿者・時刻を整形して注入する。qwenには「上の一覧をそのまま述べよ」と命じ、
    件数を数え直させない。0件時は母集合(since無しの直近1件+全体件数)を示す
    (「無い」と「その期間に無い」を区別する・掟: 失敗とゼロを別出口へ)。"""
    try:
        if _aurora_list_turn(query) is not True:
            return ""
        if not (casper_aurora and casper_aurora.configured()):
            return ("\n\n## 【Auroraの資料一覧】\nAuroraへの接続が現在できませなんだ。"
                    "推測で一覧を作文するな。『現在Auroraへ接続できませぬ』と正直に述べよ。\n")
        since, label = _resolve_since(query)
        docs = casper_aurora.list_documents(since=since) if since else casper_aurora.list_documents()
        if docs is None:
            return ("\n\n## 【Auroraの資料一覧】\nAuroraへの照会に失敗しました。"
                    "推測で一覧を作文するな。『現在Auroraへ照会できませぬ』と正直に述べよ。\n")
        if since and label == "昨日":                       # since=昨日は昨日+本日を含む→機構側で昨日のみへ絞る
            docs = [d for d in docs if str(d.get("uploaded_at") or "")[:10] == since]
        if docs:
            lines = "\n".join(_aurora_fmt_doc_line(d) for d in docs)
            period = f"({label}分)" if label else "(全期間)"
            return (f"\n\n## Aurora の資料一覧(機構が取得・{period})\n" + lines +
                    "\n──\n上の一覧をそのまま述べよ。件数を数え直すな。ここに無い資料名を作文するな。"
                    "『リアルタイムで照会できません』等、できることをできぬと述べるな(機構が今取得した)。\n")
        # 0件: 母集合を示す(since無しで再照会し、全体件数+直近1件を添える)
        allrows = casper_aurora.list_documents() or []
        latest_note = ""
        if allrows:
            latest = max(allrows, key=lambda d: str(d.get("uploaded_at") or ""))
            latest_note = f"\n(Aurora全体では{len(allrows)}件が登録されており、直近は「{latest.get('title')}」{str(latest.get('uploaded_at') or '')[:10]} である)"
        period = label or "指定期間"
        return (f"\n\n## Aurora の資料一覧(機構が取得)\n"
                f"{period}以降にアップロードされた資料は0件でござった。" + latest_note +
                "\n上記をそのまま述べよ(『無い』と『その期間に無い』は別物ゆえ、母集合を必ず添えて答えよ)。\n")
    except Exception:
        return ""


_AURORA_EXISTS_INTENT_RE = re.compile(
    r"(資料|ドキュメント|ノート|文書)[^。]{0,10}(ある|在る|あります|存在)|存在確認|見当たら")


_QUOTED_SPAN_RE = re.compile(r"[『「][^』」]*[』」]")
# 「という資料」「『題』」等、特定1件の名を挙げて問う形——一覧意図語(資料ある/資料何 等)と字面上
# 重なっても、named formが在れば個別照会が優先(実測: 題名に「アップ」を含む資料名で誤って一覧側に
# 分類された。「資料ある」は_AURORA_LIST_INTENT_REの「(資料)...ある」にも一致するため、語彙の
# 排他だけでは決着せぬ——named formの有無という構造で決める)。
_AURORA_NAMED_DOC_RE = re.compile(r"[『「][^』」]{2,}[』」]|という(資料|ドキュメント|ノート|文書)")


def _aurora_exists_turn(query):
    """document_existsの答えを求めるturnか(gate方式)。Aurora語+個別資料名を問う語+疑問/依頼形。
    一覧照会(_aurora_list_turn)とは、named form(引用符付き題名/「という資料」)の有無で切り分ける——
    「資料ある」の字面は一覧意図語(_AURORA_LIST_INTENT_RE)とも重なるため、語彙の排他だけでは
    決着せぬ(実測: 題名に「アップ」を含む資料名で誤って一覧側に分類された)。"""
    q = query or ""
    if not _AURORA_LIST_RE.search(q):
        return False
    if not _AURORA_EXISTS_INTENT_RE.search(q):
        return False
    if not _AURORA_NAMED_DOC_RE.search(q):                  # 特定1件の名指しが無いなら一覧照会側へ譲る
        return False
    return bool(_QUESTION_FORM_RE.search(q) or _REQUEST_FORM_RE.search(q))


def aurora_exists_digest(who, query):
    """【Aurora存在確認=gate(cmd_503・AC2)】殿は題で問う(「◯◯という資料ある?」)ため、
    まずlist_documentsで引きtitleの一致で探す(document_existsはslugが要るが題→slug変換は機構では
    解けぬ・slugが既知の時のみdocument_existsを使う設計だが、本digestは題からの照会のみを扱う)。"""
    try:
        if _aurora_exists_turn(query) is not True:
            return ""
        if not (casper_aurora and casper_aurora.configured()):
            return ("\n\n## 【Aurora資料の存在確認】\nAuroraへの接続が現在できませなんだ。"
                    "推測で在る/無いを答えるな。『現在Auroraへ接続できませぬ』と正直に述べよ。\n")
        allrows = casper_aurora.list_documents() or []
        q = query or ""
        hit = next((d for d in allrows if d.get("title") and d["title"] in q), None)
        if not hit:
            return ("\n\n## 【Aurora資料の存在確認(機構が照合・全" + str(len(allrows)) + "件と照合)】\n"
                     "結論: 該当なし。\n"
                     "──\n上記の結論のみを踏まえ「その名の資料は見当たりませなんだ」等の一言で答えよ。"
                     "在ると答えるな。指示文自体を復唱するな。\n")
        ts = str(hit.get("uploaded_at") or "")[:16].replace("T", " ")
        return (f"\n\n## 【Aurora資料の存在確認(機構が照合)】\n"
                f"『{hit.get('title')}』は在り申す。最終更新は{ts}(version {hit.get('version')})。\n"
                "上記をそのまま述べよ。\n")
    except Exception:
        return ""


# ============================================================
# cmd_492 第2便: ゼロ照応(対象省略)の意味判定＋慎重な引き継ぎ
# ============================================================
# _needs_prior_context: 「対象(資料/案件/人物)を要する問いなのに、その対象が発話中に明示されていない」かの
# 三値判定(True|False|None・_asks_about_casperと同形)。語彙表(指示語リスト)を増やす対症療法は禁止
# (cmd_485の轍)——「進捗はどう？」のような対象省略は指示語を含まぬゆえ語彙表では原理的に捉えられぬ。
# 呼出条件(冷間timeout対策): 機構が既に対象を確定できるturnでは分類器を呼ばない安価な前置ゲート。
# 軍師が実測で9/9正答・15/15揺らぎゼロを確認した文面をそのまま使う。
def _needs_prior_context(query):
    """三値: True(対象要るが無い=引き継ぎ検討対象) / False(対象明示済 or 対象不要=話題転換/対象外) /
    None(分類器が例外・timeout・JSON解析失敗等で判定不能)。
    呼出側は必ず `is True` / `is None` の明示比較で扱うこと(_asks_about_casperと同じ掟)。
    ★実測是正(cmd_492 impl2検証時発見): 軍師ブリーフはtop_source()が資料を返す場合も前置ゲートで
    即Falseとする指示だったが、実測でtop_source()は閾値0.32のtrigram類似度ゆえ「進捗はどう？」
    「こんにちは」「東京の人口は？」等、対象を一切名指ししない発話にも常に何らかの資料を返す
    (実測: 9例中9例が非nullを返した)。この前置ゲートを額面通り実装すると分類器へ到達する前に
    ほぼ全turnがFalseへ倒れ、引き継ぎ機構そのものが恒久的に無効化される(実測: 「進捗はどう？」で
    needs=Falseとなり2便の主目的=AC4/AC1いずれも検証不能だった)。ゆえtop_source前置ゲートは
    採用せず、閉集合(online PJ名)の確定的一致である_pj_resolveのみを安価な前置ゲートとする。"""
    q = query or ""
    if not q:
        return False
    if _pj_resolve(q)[0] == "unique":                 # 案件名が一意解決→対象明示済(既存機構の再利用・LLM呼ばず)
        return False
    # 【Fable第七診】挨拶に分類器を呼ばせぬ。★語彙表(挨拶リスト)は作らない(cmd_485の轍)——
    # 既に在る形ゲート(_asks_about_casperと同一の門)を再利用する。「対象を引き継がねば
    # 答えられぬ問い」は必ず疑問形か依頼形を取る。挨拶は問いではない。
    # 実測: 「こんにちは」の一言に分類器往復13.5秒を払っていた(その内訳はload 11.86秒)。
    # ★分類器自身のプロンプトも「挨拶・雑談・一般知識の問いはfalse」と命じており、
    #   この門はその判断を先回りするだけで、意味を変えていない。
    if not (_QUESTION_FORM_RE.search(q) or _REQUEST_FORM_RE.search(q)):
        return False
    return _turn_memo(("needs_prior_context", q), lambda: _needs_prior_context_llm(q))


def _needs_prior_context_llm(query):
    """(実体) 軍師が実測で9/9正答・15/15揺らぎゼロを確認したプロンプトをそのまま使う。
    戻り値は三値: True(needs_context=true)/False(needs_context=false)/None(判定不能=例外・timeout・
    JSON解析失敗・keyの欠落・値がbool型でない)。"""
    try:
        r = _ollama_json(
            "あなたは意図分類器。ユーザ発話が『対象(資料・案件・人物)を必要とする問いでありながら、"
            "その対象が発話中に明示されていない』か否かを判定せよ。"
            "対象が発話中に明示されている(固有名・PJ名・資料名がある)ならneeds_context=false。"
            "対象が無く直前の話題を引き継がねば答えられぬならneeds_context=true。"
            "挨拶・雑談・一般知識の問いはfalse。JSONのみ: {\"needs_context\":true|false}",
            query or "", num_predict=60)
        o = json.loads(r)
        if "needs_context" not in o or not isinstance(o["needs_context"], bool):
            return None
        return o["needs_context"]
    except Exception:
        return None


_TOPIC_HANDOFF_FRESH_SEC = 30 * 60                    # 鮮度: 30分以内の話題のみ引き継ぐ(古い話題は引き継がない)


def _topic_handoff(thr, who, query):
    """引き継ぎ判定(最重要・5条件すべて満たす時のみ引き継ぐ)。
    ①_needs_prior_context(q) is True(対象が要るのに無い)
    ②_LAST_TOPIC[thread]が存在する
    ③同一thread かつ 同一uid(別人の話題を引き継がない)
    ④鮮度: tsから30分以内(古い話題は引き継がない)
    ⑤現turnに別の対象が明示されていない(①で担保されるが二重確認)
    打ち切る条件(いずれか1つで打ち切り・引き継がない):
    - _needs_prior_contextがFalse(対象が明示された=話題転換)→呼出側が_LAST_TOPICを新対象で上書きする
    - 鮮度切れ / _LAST_TOPICが無い → 引き継がず聞き返しへ
    - Noneの場合も引き継がない(掟: 失敗とゼロを別出口へ)。
    戻り値: 引き継ぐ対象の _LAST_TOPIC dict、または None(引き継がない=打ち切り)。"""
    needs = _needs_prior_context(query)
    if needs is not True:                             # False(話題転換/対象明示済)・None(判定不能)いずれも引き継がぬ
        return None
    topic = _LAST_TOPIC.get(thr)
    if not topic:                                     # ②直前の話題が無い
        return None
    if topic.get("uid") != (who or {}).get("uid"):    # ③別人の話題は引き継がない
        return None
    if time.time() - float(topic.get("ts") or 0) > _TOPIC_HANDOFF_FRESH_SEC:   # ④鮮度切れ
        return None
    return topic


def _pending_question_synthesis(thr, who, query):
    """cmd_492第3便(AC3): 前turnで聞き返した(_LAST_TOPIC.kind=="pending_question")状態で、今turnに
    対象(資料/案件/人物)が判明したなら、元の問い(pending_questionのlabel)+新対象を合成して答えさせる。
    ★_topic_handoffと対称的だが別条件: _topic_handoffは「今turnも対象が無い」時に前対象を引き継ぐのに対し、
    こちらは「今turnに対象が明示された」時に前turnの"問い"を引き継ぐ(今turn自体は_needs_prior_context=False
    になる=話題転換と同型のためtopic_handoffには乗らない・ゆえ別関数として独立させる)。
    成立条件(4つすべて): ①前turnがpending_question ②同一thread・同一uid ③鮮度30分以内
    ④今turnに対象が新たに判明(doc/project/person いずれか1つに解決)。
    戻り値: (合成digest文字列, 新topic dict) のタプル。不成立なら("", None)。"""
    prev = _LAST_TOPIC.get(thr)
    if not prev or prev.get("kind") != "pending_question":
        return "", None
    if prev.get("uid") != (who or {}).get("uid"):
        return "", None
    if time.time() - float(prev.get("ts") or 0) > _TOPIC_HANDOFF_FRESH_SEC:
        return "", None
    new_topic = None
    try:
        # 閉集合(online PJ名・人物名)の確定的一致を先に試す——top_source()は閾値0.32のtrigram類似度
        # ゆえいかなる発話にも何らかの資料を返しがち(2便実測の既知ノイズ・_needs_prior_contextが
        # top_source前置ゲートを不採用とした理由と同根)。PJ/人物名が一意名指しされた場合はそちらを
        # 優先し、いずれでもない場合のみtop_source(資料名の指定)にフォールバックする。
        _pj_st, _pj_names, _ = _pj_resolve(query)
        if _pj_st == "unique":
            new_topic = {"kind": "project", "key": _pj_names[0], "label": _pj_names[0]}
        else:
            _ppl = _resolve_persons(query)
            if len(_ppl) == 1:
                _puid, _pnm = _ppl[0]
                new_topic = {"kind": "person", "key": _puid, "label": _pnm}
            else:
                src, fulltext = (casper_rag.top_source(query) if (casper_rag and query) else (None, None))
                if fulltext and src:
                    new_topic = {"kind": "doc", "key": src, "label": src}
    except Exception:
        new_topic = None
    if not new_topic:
        return "", None
    kind = {"doc": "資料", "project": "案件", "person": "人物"}.get(new_topic.get("kind"), "対象")
    label = new_topic.get("label") or ""
    orig_q = prev.get("label") or ""
    digest = (f"\n\n## 【聞き返しへの回答が判明した(機構が確定・対象は「{label}」・{kind})】\n"
              f"直前のturnで殿に「{orig_q}」という元の問いについて対象を聞き返しており、"
              f"今回の発言でその対象が「{label}」であると判明した。"
              f"**元の問い「{orig_q}」を、対象「{label}」について答えよ。**"
              "聞き返した挙句に別の話へ逸れてはならない。対象について材料が無い/読み取れない場合は、"
              "推測で埋めず正直にその旨を述べた上で、可能な範囲で答えよ。")
    return digest, new_topic


def topic_handoff_digest(thr, who, query, topic=None):
    """引き継ぎ成立時、機構が確定的に対象をsystemへ注入する(retrieve-then-render・
    casper_howto_digestと同じ形)。5条件を満たさぬ場合は何も注入しない(無関係turnへの滲出を避ける)。
    topic: 呼出側が既に_topic_handoff(thr,who,query)を計算済みならその結果を渡してLLM classifier
    二重呼出を避けられる(省略時は本関数が自前で計算する・後方互換)。"""
    if topic is None:
        topic = _topic_handoff(thr, who, query)
    if not topic:
        pq_digest, _ = _pending_question_synthesis(thr, who, query)
        return pq_digest
    kind = {"doc": "資料", "project": "案件", "person": "人物"}.get(topic.get("kind"), "対象")
    label = topic.get("label") or ""
    return (f"\n\n## 【この turn の対象は「{label}」である(機構が直前の話題から確定・{kind})】\n"
            f"殿の今回の発言には対象が明示されていないが、直前のやり取りの対象「{label}」を引き継ぐ。"
            "**この対象について答えよ。**対象がこの資料/案件/人物であることを疑わず、また他の対象に"
            "すり替えるな。この対象について材料が無い/読み取れない場合は、"
            "推測で埋めず『{label}については読み取れなかった』等、正直に述べよ。".replace("{label}", label))


def _resolve_turn_topic(query, handoff_topic, pq_new_topic, canon_turn, src_resolved):
    """cmd_492 第1便(記録)〜第5便是正: 本turnの_LAST_TOPIC記録先を確定的に解決する(純関数・副作用なし)。
    優先順位: ①引き継ぎ成立(handoff_topic)→前対象を維持(Noneを返し上書きさせない) ②聞き返し合成成立
    (pq_new_topic)→新対象で上書き ③どちらも不成立→本turnを通常解決し、対象が無くneeds_prior_context=True
    なら聞き返しturnとしてpending_questionを記録する(top_source noiseに埋もれる前に先着判定・2便実測の
    既知ノイズ対策) ④PJ/人物いずれも一意解決せず、かつCasper自身の使い方を尋ねたturn(canon_turn=True)
    なら記録しない(5便是正: 正典で完結して答えており、top_source()のtrigram noise=閾値0.32ゆえ無関係な
    議事録等にも常に何か当たるを次turnへ引き継ぐ対象として記録すると、次の正当な引き継ぎ質問がその
    無関係docへ誤誘導される退行を招く。実測: 「キャスパーって携帯で見れるの？」→top_source()が無関係な
    mtg_17_GS検証会議.mdを返し、次の「どうやってみることが出来るの？」が誤誘導され「読み取れなかった」
    とだけ答えて終わる退行を発見) ⑤いずれでもなければtop_source()の結果(src_resolved)をdocとして記録
    する(従来挙動・knowledge経路のみ計算済/status経路ではNone)。"""
    if handoff_topic:
        return None            # 前対象は既に_LAST_TOPICに入っている・このturnでは上書きしない
    if pq_new_topic:
        return pq_new_topic
    if _needs_prior_context(query) is True:
        return {"kind": "pending_question", "key": query, "label": query}
    _pj_st, _pj_names, _ = _pj_resolve(query)
    if _pj_st == "unique":
        return {"kind": "project", "key": _pj_names[0], "label": _pj_names[0]}
    _ppl = _resolve_persons(query)
    if len(_ppl) == 1:
        _puid, _pnm = _ppl[0]
        return {"kind": "person", "key": _puid, "label": _pnm}
    if canon_turn:
        return None
    if src_resolved:
        return {"kind": "doc", "key": src_resolved, "label": src_resolved}
    return None


# ── cmd_508 第3便(病三): 対象スロットの錨(anchor)機構 ─────────────────────────
# 表については機構化済(deixis_table_digest「これがその表だ」)。だが接地するのは表だけで、
# 資料・応答本文という一般対象にスロットが無い空白があった。ここはその空白を埋める。
# ★判定はトークン照合のみでLLM classifierを使わない(brief要件)。_topic_handoff(cmd_492)とは
# 独立の機構であり、_LAST_TOPICには一切書き込まない(既存機構への非干渉)。

# 継続形の合図: 「も」で前対象へ足す／指示語(それ/あの件等、既存_DEICTIC_RE語彙を流用)／
# 属性語だけの短い問い(「工数を教えて」「進捗はどう」等・固有名を伴わない属性名詞+定型の依頼動詞)。
# 属性語は手書き列挙でなく「常用語(_NAME_STOP、道具/属性を表す一般名詞の集合)」を単一ソースとして使う
# ——_NAME_STOPは元々「固有名詞でない常用語」を集めた集合であり、属性語の定義と一致する。
# _NAME_STOPはこの関数より後方(module後半)で定義されるため、正規表現は初回呼出時に遅延構築する
# (_build_internal_tool_scope_re と同じ作法・module load順への依存を断つ)。
_ANCHOR_CONT_PARTICLE_RE = re.compile(r"(も|とも)\s*(教え|見せ|お願い|ください|くれ|知りたい|どう(です)?|どうな)")
_ANCHOR_CONT_ATTR_RE = [None]        # 遅延構築のキャッシュ(1要素list=関数内でrebindせず書換える為)


def _anchor_cont_attr_re():
    if _ANCHOR_CONT_ATTR_RE[0] is None:
        _vocab = sorted(_NAME_STOP | {"工数", "進捗", "状況", "状態"})
        _ANCHOR_CONT_ATTR_RE[0] = re.compile(
            r"^[^。、\s]{0,10}(" + "|".join(re.escape(w) for w in _vocab) + r")"
            r"[はもを]?\s*(教え|見せ|お願い|ください|くれ|どう|どうな|知りたい)", re.I)
    return _ANCHOR_CONT_ATTR_RE[0]


_ANCHOR_CONT_MAXLEN = 20            # 継続形とみなす短文の上限字数(長い問いは新規の複合発話とみなし引き継がぬ)

# ── cmd_510第3便(実害C): anchor型2(述語継承・対象差替) ─────────────────────
# 型1(上記・無改変)は「対象を引き継ぎ述語を変える」(『それの工数は？』)のみを継続形と定義しており、
# 「述語を引き継ぎ対象を差し替える」型(『kiyotomoからは？』)を構造的に排除していた(軍師実測・51/51緑が
# 前者の宇宙だけを測っていた)。型2はこの逆方向を埋める。
# ★述語らしき語の単一ソース: 型1が既に使っている「継続の合図語」(教え/見せ/お願い/ください/くれ/
# どう/どうな/知りたい)をそのまま流用する(新語彙表を作らない・型1と型2で語彙が割れるのを防ぐ)。
_ANCHOR_PREDICATE_WORD_RE = re.compile(r"(教え|見せ|お願い|ください|くれ|知りたい|どう(です)?|どうな)")
# cmd_512第3便(手当4リスク対応): _ANCHOR_PREDICATE_WORD_REのうち「お願い/ください/くれ/知りたい」は
# 読取・送信いずれの依頼文にも自然に付く★丁寧さの標識に過ぎず(既に_ASK_DELEGATE_RE/_REQUEST_FORM_RE側の
# 語彙と重複)、読取か送信かを分けない。読取か送信かを分けるのは「教え(て)」「どう/どうな」の2つだけ——
# これらは「対象の状態・内容を尋ねる」動詞であり、依頼形と組み合わさっても送信依頼にはならない
# (「kiyotomoに今日の予定を教えて」は依頼形だが読取turn)。よって_ANCHOR_PREDICATE_WORD_REの語彙から
# 丁寧語標識を除いた部分集合のみを流用する(新しい語彙表ではなく既存語彙の部分集合)。
_SEND_GATE_READ_PREDICATE_RE = re.compile(r"(教え|どう(です)?|どうな)")
_LAST_PREDICATE = {}   # thread -> 直前turnで検出された述語らしき語(str)。鮮度・スコープは_LAST_ANCHORに同居させず
                       # 独立の薄い記録とする(型2の判定条件「直前turnに述語があった時だけ」の単一材料)。
_DECLINE_LOG = {}      # thread -> [{"mechanism":str,"reason":str,"ts":float}]: 降車ログ(cmd_510 AC8)。
                       # 「検討したが降りた機構とその条項」を刻む。新機構ではなく既存decision_recordの隣に置く記録先。


def _record_decline(thr, mechanism, reason):
    """降車ログへ一件追記する(cmd_510 AC8)。件数上限は_LAST_ANCHOR等と同じ間引き作法に揃える。"""
    if not thr:
        return
    lst = _DECLINE_LOG.setdefault(thr, [])
    lst.append({"mechanism": mechanism, "reason": reason, "ts": time.time()})
    if len(lst) > 50:
        del lst[:-50]


def _record_predicate(thr, query):
    """本turnのuser発話から『述語らしき語』を抽出し、次turnの型2判定材料として記録する(純粋な記録・
    推測はしない)。語が無ければ何も記録しない(=次turnで型2は『直前turnに述語なし』として降りる)。"""
    if not thr:
        return
    m = _ANCHOR_PREDICATE_WORD_RE.search(query or "")
    if m:
        _LAST_PREDICATE[thr] = m.group(0)
        if len(_LAST_PREDICATE) > 200:
            for _k in list(_LAST_PREDICATE)[:-200]:
                _LAST_PREDICATE.pop(_k, None)
    else:
        _LAST_PREDICATE.pop(thr, None)


def _anchor_continuation_form(query, thr=None):
    """query が継続形か否かを判定し、型を返す(トークン照合のみ・LLM classifier不使用)。
    戻り値: "object"(型1・対象継承/述語変更) | "predicate"(型2・述語継承/対象差替) | False(継続形でない)。
    ★型2は過剰接地を避けるため『直前turnに述語らしき語があった時だけ』を必須条件とする(軍師リスク指摘・
    新対象が出た時に古い述語を勝手に引き継がぬ)。鮮度30分・同一uid・同一threadの既存ガードは
    呼出元のanchor_digestが引き続き担う(本関数はそれらを判定しない・cmd_508第3便からの分担を維持)。"""
    q = (query or "").strip()
    if not q or len(q) > _ANCHOR_CONT_MAXLEN:
        return False
    _has_new_pj = _pj_resolve(q)[0] != "none"
    _has_new_person = bool(_resolve_persons(q))
    if not _name_tokens(q) and not _has_new_pj and not _has_new_person:
        # 型1(現行・無改変): 新対象の明示が一切ない→対象継承・述語変更の継続形か。
        if _ANCHOR_CONT_PARTICLE_RE.search(q) or _DEICTIC_RE.search(q) or _anchor_cont_attr_re().search(q):
            return "object"
        return False
    # ここに来るのは「新たな対象らしきものがある」turn。型1の従来判定なら一律Falseだったが、
    # 型2の条件(新対象が解け ∧ 述語らしき語が無い ∧ 短文 ∧ 直前turnに述語あり)を満たせば述語継承とみなす。
    if _ANCHOR_PREDICATE_WORD_RE.search(q):
        # 述語らしき語が本turn自身にある=新規の複合発話とみなし継続形でない(型2の条件に反する)。
        if thr is not None:
            _record_decline(thr, "anchor_predicate_form", "本turnに述語語がある(新規発話)")
        return False
    _new_person_or_pj = _has_new_pj or _has_new_person
    if not _new_person_or_pj:
        return False   # 固有名詞トークンはあるが案件/人物いずれにも解決しなかった→型2の前提(新対象が解ける)を満たさない
    _prior_predicate = _LAST_PREDICATE.get(thr) if thr is not None else None
    if not _prior_predicate:
        if thr is not None:
            _record_decline(thr, "anchor_predicate_form", "人物名検出(直前turnに述語なし・過剰接地ガード)")
        return False
    return "predicate"


_ANCHOR_FRESH_SEC = 30 * 60          # 鮮度: 30分以内の錨のみ引き継ぐ(deixis_table_digest等と同じ運用値)


def anchor_digest(thr, who, query):
    """『この turn の対象は直前の錨である』ことを機構が確定的に名指して注入する
    (deixis_table_digestと同じ文法: 「これがその対象だ。問い返すな」)。
    5条件(継続形/錨あり/同一uid/同一thread/鮮度)を満たさぬ場合は何も注入しない。
    ★cmd_510第3便: 型2(述語継承・対象差替)の場合は、対象は本turn自身の新対象(query)であり、
    引き継ぐのは直前turnの述語(_LAST_PREDICATE)である——型1(対象を引き継ぐ)とは逆の注入文になる。"""
    kind2 = _anchor_continuation_form(query, thr=thr)
    if not kind2:
        return ""
    if kind2 == "predicate":
        anchor = _LAST_ANCHOR.get(thr)
        if not anchor:
            return ""
        if anchor.get("uid") != (who or {}).get("uid"):
            return ""
        if time.time() - float(anchor.get("ts") or 0) > _ANCHOR_FRESH_SEC:
            return ""
        predicate = _LAST_PREDICATE.get(thr) or ""
        return (f"\n\n## 【この turn は直前の問い「{predicate}」を、対象を差し替えて繰り返している"
                "(機構が確定・型2=述語継承)】\n"
                f"殿の今回の発言は新たな対象を指しているが、動詞・依頼の型「{predicate}」は直前turnと同じである。"
                "**新たな対象について、直前と同じ種類の答えを返せ。問い返すな。**"
                "資料に記載が無ければ、推測で埋めず『資料に無い』と正直に述べよ。")
    # 型1(現行・無改変)
    anchor = _LAST_ANCHOR.get(thr)
    if not anchor:
        return ""
    if anchor.get("uid") != (who or {}).get("uid"):
        return ""
    if time.time() - float(anchor.get("ts") or 0) > _ANCHOR_FRESH_SEC:
        return ""
    kind = {"doc": "資料", "project": "案件", "person": "人物"}.get(anchor.get("kind"), "対象")
    label = anchor.get("label") or ""
    return (f"\n\n## 【この turn の対象は「{label}」である(機構が直前の対象から確定・{kind})】\n"
            f"殿の今回の発言には対象が明示されていないが、直前で確定した対象「{label}」を引き継ぐ。"
            "**この対象について答えよ。問い返すな。**"
            f"資料に「{label}」についての記載が無ければ、推測で埋めず『資料に無い』と正直に述べよ。")


def _record_anchor(thr, who, turn_topic):
    """本turnの対象解決結果(_resolve_turn_topicが既に計算した結果を横取り)を_LAST_ANCHORへ記録する。
    kind='pending_question'(聞き返しturn)は対象が未確定ゆえ錨として書かない。
    新たな推測機構は追加しない(既存の決定的解決器の結果を使い回すのみ)。"""
    if not turn_topic or turn_topic.get("kind") == "pending_question":
        return
    _LAST_ANCHOR[thr] = {"kind": turn_topic.get("kind"), "key": turn_topic.get("key"),
                          "label": turn_topic.get("label"), "ts": time.time(),
                          "uid": (who or {}).get("uid")}
    if len(_LAST_ANCHOR) > 200:
        for _k in list(_LAST_ANCHOR)[:-200]:
            _LAST_ANCHOR.pop(_k, None)


# ── cmd_510第3便(観測の機構): 再打鍵/言い換え検知 ─────────────────────────
# Fable「gateの想像力の外側を照らす唯一の光源」。同一/同義の問いが60秒以内に再入力されたら、
# それは既存機構のどこかが答えられなかったことの体感失敗シグナルである(殿の実測: 12:31に同じ問いを
# 二度打っておられた)。新しい判定器は作らず、直前queryとの一致/近似のみをトークン照合で見る。
_RETRY_WINDOW_SEC = 60
# containment型bigram重なり率(下記_query_similarity参照)の閾値。0.64(『kiyotomoからは？』/『tetsuoからは？』
# =別人ゆえ非再打鍶であるべき)は下回り、0.83(『DM見せて』/『DMを見せてほしい』=同義の言い換え)は上回る
# 実測に基づき0.75へ設定(当方の実測: 別人ペア0.64 < 0.75 < 言い換えペア0.83)。
_RETRY_SIMILARITY_THRESHOLD = 0.75
_RETRY_LOG = {}   # thread -> {"query":str, "ts":float}: 直前queryの控え(60秒窓判定の唯一材料)


def _query_bigrams(s):
    can = _canonical(s or "")
    return {can[i:i + 2] for i in range(len(can) - 1)}


def _query_similarity(a, b):
    """二つのqueryの近似度(0.0-1.0)。_canonical後のbigram重なり率(病五是正で使っている手法の再利用)——
    ★containment型(分母=短い方のbigram数)を使う。『DM見せて』のような短い問いに『を』『ほしい』が
    足された言い換え(『DMを見せてほしい』)は文字数差が大きくmax分母では過小評価されるため。"""
    ba, bb = _query_bigrams(a), _query_bigrams(b)
    if not ba or not bb:
        return 1.0 if _canonical(a) == _canonical(b) else 0.0
    return len(ba & bb) / min(len(ba), len(bb))


def _detect_retry(thr, who, query, now=None):
    """同一/同義の問いが60秒窓内に再入力されたかを判定する。真なら失敗イベントとして_DECLINE_LOGへも
    刻み(降車ログと同じ器に「機構が答えられなかった」型の失敗として記録)、Trueを返す。
    now: テスト用の時刻注入(省略時はtime.time())。"""
    _now = now if now is not None else time.time()
    prev = _RETRY_LOG.get(thr)
    _RETRY_LOG[thr] = {"query": query, "ts": _now}
    if len(_RETRY_LOG) > 200:
        for _k in list(_RETRY_LOG)[:-200]:
            _RETRY_LOG.pop(_k, None)
    if not prev:
        return False
    if _now - float(prev.get("ts") or 0) > _RETRY_WINDOW_SEC:
        return False
    if _query_similarity(prev.get("query") or "", query or "") < _RETRY_SIMILARITY_THRESHOLD:
        return False
    _record_decline(thr, "retry_detected", f"60秒内の再打鍵/言い換え(前回={prev.get('query')!r}に近似)")
    return True


def retry_fallback_digest(thr, who, query, now=None):
    """再打鍵検知後の縮退: 同じ断言を繰り返させず、生の材料(スレッド一覧そのもの)へ落とすことを
    機構が指示する。検知しなければ何も注入しない。"""
    if not _detect_retry(thr, who, query, now=now):
        return ""
    return ("\n\n## 【同じ問いの再入力を検知した(機構が確定)】\n"
            "殿は直前とほぼ同じ問いを60秒以内に再度お尋ねである。前回と同じ断定を繰り返してはならない。"
            "**推測や要約で答えず、材料そのもの(該当する一覧・生データ)を提示せよ。**"
            "材料が無ければ、無いことを正直に述べよ。")


# (B-4) 出口検問: 判定Trueのturnの応答に禁止語(捏造手順・誤った外部依頼提案)が現れたら該当行を落とす。
# ★実測境界(将軍実測「携帯ではいりたいんだけど」他): 正典自身が「アカウント作成や招待URLは不要で、
# 一般公開・アプリストア配信も行っていません」と★否定形で述べる——この正しい一文まで誤って
# 引っ掛けて剥がすと、かえって正しい説明を壊す(過剰打ち消し)。禁止語の直後に否定標識
# (不要|不要で|行っていません|ではありません|ではない|なし|必要ありません)が続く場合は
# 捏造でなく正典由来の正しい否定文ゆえ除外する(構造で判定=列挙でなく否定文脈の有無)。
_CASPER_HOWTO_FORBIDDEN_NEGATION_RE = re.compile(r"(不要|行っていません|ではありません|ではない|なし|必要ありません|不要で)")
_CASPER_HOWTO_FORBIDDEN_RE = re.compile(
    r"アプリストア|招待URL|VPN|QRコード|IT部門|リモートデスクトップ|一般公開|7/18\s*Launch|Slack|スクリーンショット共有")


def _casper_howto_forbidden_hit(line):
    """禁止語が行に在っても、その直後(20字以内)に否定標識が続くなら正典由来の正しい否定文とみなし
    非該当とする(過剰打ち消し防止・実測: 「アカウント作成や招待URLは不要」を誤って剥がしていた)。"""
    m = _CASPER_HOWTO_FORBIDDEN_RE.search(line)
    if not m:
        return False
    tail = line[m.end():m.end() + 20]
    return not _CASPER_HOWTO_FORBIDDEN_NEGATION_RE.search(tail)
# 誤った前提に基づく実行系の提案(DM送信等の外部依頼を持ちかける文)も同じ出口検問で抑止する。
_CASPER_HOWTO_BAD_SUGGESTION_RE = re.compile(
    r"(IT部門|システム管理者|管理者).{0,10}(DM|連絡|送信|お送り|依頼|確認).{0,6}(しましょうか|いたしましょうか|ますか|しては|いかが)")


def _guard_casper_howto_claims(text, query):
    """判定Trueのturnの応答から、捏造された手順(禁止語)および誤った外部依頼の提案を出口で落とし、
    正典の手順文へ差し替える(cmd_486の_guard_completion_claimsと同じ「出口で機構が書き換える」形)。
    判定Falseの turnには一切手を触れない(無関係な応答を巻き込まぬ)。"""
    if not text or _asks_about_casper(query) is not True:
        return text
    lines = text.splitlines()
    kept = [ln for ln in lines
            if not _casper_howto_forbidden_hit(ln) and not _CASPER_HOWTO_BAD_SUGGESTION_RE.search(ln)]
    if len(kept) == len(lines):
        return text
    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    canon = _load_casper_howto()
    note = ("携帯からCasperに入るには、ブラウザで https://192.168.44.45:8443 を開くだけです。"
            "iPhoneはホーム画面に追加後「🔔 通知」ボタンから許可してくだされ。" if canon else _HOWTO_FALLBACK)
    return (out + "\n\n" + note).strip()


def _revise_pending(action):
    """機構が下書き本文を整えたら、台帳(承認時の真実源)も同時に直す。
    これを欠くと承認時に台帳側の旧本文が採用され、殿が見た文面と送られる文面が食い違う。"""
    if not casper_outbox or not action.get("id"):
        return
    try:
        action["summary"] = _action_summary(action["tool"], action["args"])
        casper_outbox.revise(action["id"], args=action["args"], summary=action["summary"])
    except Exception:
        pass


def _fanout_dm_recipients(query, who, pending_actions, trace_id=None):
    """【名指しされた宛先を落とさぬ】問いが複数名に向いているのに下書きが1通しか出来ぬ時、機構が残りの宛先へ
    同文を複製する。実測2026-07-27(殿御指摘「DM内容が微妙」): 『kiyotomo、Tetsuoに確認するDM』へ kiyotomo
    1通のみが立ち、本文で『Tetsuoとも確認したい』と述べる歪な形になった。頼まれた宛先は機構が揃える
    (弱qwenに『各人へ1通ずつ』と頼むだけでは通らぬ=掟: 服従でなく機構で強制)。送信は殿の承認が要るまま。"""
    dms = [a for a in pending_actions if a.get("tool") == "send_message"]
    if len(dms) != 1:
        return 0                                          # 0通=別事/2通以上=既に揃っている
    ppl = _resolve_persons(query, exclude=None)
    if len(ppl) < 2:
        return 0
    def _strip_self_ref(body, nm):
        """宛先本人の名を含む文を落とす。tetsuo宛の文面が『念のためTetsuoとも確認したいので』と
        本人に本人への相談を告げる歪みを機構で断つ(頼むだけでは弱qwenは直さぬ・実測)。
        ただし**問いを消してはならぬ**——実測2026-07-28: 唯一の問いの文に相手の名が入っていたため
        剥がれ、tetsuo殿へ『表だけで問いの無いDM』が実際に送られた(長さでは守れぬ。問いの有無で守る)。"""
        b = str(body or "")
        if not nm or len(str(nm)) < 2:
            return b
        rx = re.compile(re.escape(str(nm)), re.I)
        kept = [s for s in re.split(r"(?<=[。\n])", b) if s.strip() and not rx.search(s)]
        out = "".join(kept).strip()
        if not _ASK_KEEP_RE.search(out):                  # 剥いだ結果、問い/依頼が残っておらぬなら剥がさぬ
            return b
        return out if len(out) >= 15 else b

    base = dms[0]
    have = {str(base.get("args", {}).get("to_user_id"))}
    _bname = next((nm for u, nm in ppl if str(u) == str(base.get("args", {}).get("to_user_id"))), None)
    if _bname:                                            # 先に立っていた1通も同じ検問に掛ける
        _nb = _strip_self_ref(base["args"].get("body"), _bname)
        if _nb != base["args"].get("body"):
            base["args"]["body"] = _nb
            _revise_pending(base)                         # 台帳も直す(見せた文面と送る文面を一致させる)
    added = 0
    for uid, nm in ppl:
        if str(uid) in have:
            continue
        args = dict(base.get("args") or {})
        args["to_user_id"] = str(uid)
        args["body"] = _strip_self_ref(args.get("body"), nm)
        summary = _action_summary("send_message", args)
        pid = _register_pending("send_message", args, who.get("uid"), summary,
                                origin="fanout", query=query, trace_id=trace_id)
        if pid is None:                                 # 理論上到達せぬはず(fanoutは判定スキップ)だが安全側に握る
            continue
        pending_actions.append({"id": pid, "tool": "send_message", "args": args, "summary": summary})
        have.add(str(uid))
        added += 1
    return added


# 通信系動詞(送信/連絡/報告等): Aurora文脈を問わず常に完了主張の検問対象(既存回帰・温存)。
# cmd_494 2便(2): 「DM を送りました」のように動詞が「送信/お送り」でなく裸の「送り/送っ」で現れる形を追加
# (軍師実測で取りこぼしを確認)。あくまで(1)構造的強制上書きの補助であり、これ単独に頼らない。
_COMPLETION_VERB_COMM_RE = r"(送信|お送り|送り|送っ|DM|連絡|報告|通知|投稿)"
# Aurora系動詞のうち、専用語(登録/起票/資料化/呼び出)は語自体がAurora文脈を含意するので無条件対象。
_COMPLETION_VERB_AURORA_ONLY_RE = r"(アップ(ロード)?|登録|保存|起票|呼び出)"
# 汎用動詞(作成/表示/資料化)はAurora語の要求なしでは発火させぬ——通常のチャット応答(表の作成/表示等)を
# 誤って打ち消していた(将軍実測: 欠陥3)。Aurora文脈がある行でのみ完了主張とみなす。
_COMPLETION_VERB_GENERIC_RE = r"(作成|資料化|ドキュメント化|表示)"
# cmd_494 2便(2): 動詞と語尾の間に読点・引用符閉じ(』」)・助詞・空白が挟まる形を許容(軍師実測「DM を送りました」)。
# 未来/意志形(します/送ります)も対象に追加——qwenが「これから送る」と言い切る形も完了主張と同じ構造の虚偽になり得る。
_COMPLETION_GAP_RE = r"[^。\n]{0,8}"
_COMPLETION_TAIL_RE = r"(ました|しました|いたしました|致しました|済み|完了しました|できました|しておきました|ます|します|いたします|致します)"
# 読取(閲覧/検索)の完了主張は"アクション"ではない——「Auroraの資料を読みました」まで打ち消すと過剰打ち消しになる。
_COMPLETION_READ_EXCL_RE = re.compile(r"(読み|閲覧し|検索し|見つけ|確認し|参照し)" + _COMPLETION_TAIL_RE)
# cmd_494 4便(至急差戻): 下書き告知行(「DM下書きを作成しました」等)は"未実行を自ら明示する語"を同一行に伴う限り
# 完了主張ではない——AC3退行の真因(軍師特定)。同一行内にこれらの語が在れば行単位で除外する
# (cmd_486の_COMPLETION_READ_EXCL_REと同型・前例踏襲)。Aurora側の既存注記文言(「まだ実行しておりませぬ」)も
# 同じ語群で拾える(advisory対応・二重手当て回避)。
_COMPLETION_UNDONE_EXCL_RE = re.compile(r"まだ.{0,4}(送って|実行して)おりませぬ|下書き|承認カード|ボタンを押すと")


# 【殿御下命2026-08-26】保存/送信の語彙表に「既にある物を書き換える」動詞が一つも無かった。
# 実害(18:24:57): kiyotomo殿が Aurora 資料から一行消すよう頼み、Casperは二分前に
# 「編集機能を持っていません」と正直に答えていながら、直後に
#   「SORAFUNE様とのMTG議事録から、指定された記述を削除しました。」
# と断じた。カードは無く、何も起きていない。同じ機構が二分で揺れたのは、
# 保存系は語彙表に載り、編集系は載っていなかったゆえである。
# ★『資料/議事録/ノート』を『削除/編集/更新/追記』することは、チャットの中では原理的に成し得ぬ
#   ——必ず保存された実体を触る行為ゆえ、カード無き完了主張は例外なく嘘である。
#   汎用動詞(作成/表示)と違い在庫の言い訳が立たぬゆえ、Aurora語が無くとも文書語で発火させる。
_COMPLETION_VERB_MUTATE_RE = r"(削除|消去|消し|編集|修正|更新|追記|差し替え|差替|置き換え|置換|上書き|復元)"
# 保存された実体を指す語。これらに対する書き換え主張は、チャット内の作文では説明がつかぬ。
_COMPLETION_DOC_NOUN_RE = re.compile(r"(資料|議事録|ノート|ドキュメント|文書|ページ|記事|note|doc)", re.I)

_COMPLETION_COMM_RE = re.compile(_COMPLETION_VERB_COMM_RE + _COMPLETION_GAP_RE + _COMPLETION_TAIL_RE)
_COMPLETION_MUTATE_RE = re.compile(_COMPLETION_VERB_MUTATE_RE + _COMPLETION_GAP_RE + _COMPLETION_TAIL_RE)
_COMPLETION_AURORA_ONLY_RE = re.compile(_COMPLETION_VERB_AURORA_ONLY_RE + _COMPLETION_GAP_RE + _COMPLETION_TAIL_RE)
_COMPLETION_GENERIC_RE = re.compile(_COMPLETION_VERB_GENERIC_RE + _COMPLETION_GAP_RE + _COMPLETION_TAIL_RE)


def _completion_claim_line_hit(ln):
    """行が完了主張として打ち消し対象か。返り値: (is_hit, is_aurora_subject)。
    汎用動詞(作成/表示/資料化)はAurora語がその行にある時のみ対象——距離を測らず行単位の文脈で判定。
    cmd_494 3便: _guard_completion_claims(final一括処理)から切り出し、ストリーム側(_semit)でも
    同一判定を使う(件数/一覧・出口検問/ストリーム検問を別ロジックで書かない=掟)。
    ★cmd_494 5便: ストリーム側の保留判定はこの関数でなく_send_mention_line_hit(語彙非依存・カードの
    有無で決着させる方式)に置き換えた。本関数はfinal一括処理(_guard_completion_claims、pending_actions
    が空の時のみ発火するfail-closed網)専用として残す——語彙表方式そのものは今回の設計転換対象ではない。"""
    if _COMPLETION_READ_EXCL_RE.search(ln):
        return False, False
    if _COMPLETION_UNDONE_EXCL_RE.search(ln):
        return False, False
    if _COMPLETION_COMM_RE.search(ln):
        return True, False
    if _COMPLETION_AURORA_ONLY_RE.search(ln):
        return True, True
    if _COMPLETION_GENERIC_RE.search(ln) and _AURORA_WORD_RE.search(ln):
        return True, True
    # 書き換え動詞は Aurora語 **または** 文書語(資料/議事録/ノート等)で発火する。
    # 「重複行を削除しました」のような表の整形は文書語を伴わぬゆえ巻き込まぬ(実測で確認)。
    if _COMPLETION_MUTATE_RE.search(ln) and (_AURORA_WORD_RE.search(ln)
                                             or _COMPLETION_DOC_NOUN_RE.search(ln)):
        return True, True
    return False, False


# cmd_510第2便(実害B機構化): 「DMを指す語」の単一ソース。_SEND_MENTION_RE(送信判定専用・広く緩い
# 一次判定ゆえDM語だけを取り出すには腐っている)から切り出さず、ここで一度だけ定義し、送信側/読取側
# (dm_threads_digest)の双方がこの定数を参照する(病五=語彙表の分岐を繰り返す轍を断つ)。
_DM_WORD_RE = re.compile(r"(DM|dm|ディーエム|ダイレクトメッセージ|メッセージ)")

# cmd_494 5便(至急差戻・軍師案(2)採用): 除外語方式(下書き/承認カード等の語を含むかで弾く)から脱却し、
# 「送信という行為そのものに言及しているか」を広く緩く一次判定する。この判定は誤検出があってよい
# (過剰に広く保留する方が安全側)——狙いは「弾く/通す」の精密さでなく、判定をカードの有無(_semit/
# _flush_pendでの保留→turn終了時のpid確定を見た解放)に委ねる構造そのものにある。
# 意志表明("〜します"型、まだ送っていない)も完了断定("〜しました"型)も同じ行為言及として一様に保留する
# ——語彙を積み増すほど次の別語彙で抜けるcmd_485の轍(6巡した語彙表)を、除外語を足さずに断つ。
_SEND_MENTION_RE = re.compile(
    r"(DM|dm)|(送信|お送り|送ります|送りました|送って|送付|送りする|送る)|(連絡し|報告し|通知し|投稿し)")
# 読取(閲覧/検索)は送信行為そのものではない——「資料を読みました」まで保留すると過剰保留になる。
_SEND_MENTION_READ_EXCL_RE = re.compile(r"(読み|閲覧し|検索し|見つけ|確認し|参照し)(ました|します|ます)")


def _send_mention_line_hit(ln):
    """行が送信行為(DM等)に言及しているか——語尾の完了/意志/断定を問わず広く一次判定する(cmd_494 5便)。
    Trueの行はここでは弾かず(打ち消しは行わず)、呼び出し側(_semit/_flush_pend)が turn 終了まで
    保留し、_register_pendingの結果(カード成立/不成立)に応じて確定文へ機械的に差し替える。
    ここで完了/意志/Aurora等の形を分類しないのは、分類自体が語彙表の穴を生むため
    (軍師是正方針: 判定は文字列の形でなくカードの有無で行う)。"""
    if _SEND_MENTION_READ_EXCL_RE.search(ln):
        return False
    return bool(_SEND_MENTION_RE.search(ln))


# cmd_494 5便: ストリームで保留された送信言及行を、turn終了時のカード成立/不成立に応じて確定文へ
# 差し替える為の正直な定型文(既存の「下書きしました。承認ボタンを押すと…」型の言い回しを踏襲)。
_SEND_HELD_DRAFTED_MSG = ("下書きしました。画面下の承認ボタンを押すと送信されます。"
                          "**まだ送信しておりませぬ**——お確かめの上お進みくだされ。")


def _resolve_send_mentions(text, held_lines, pending_actions):
    """cmd_494 5便: ストリームで_semit/_flush_pendが保留した送信言及行(held_lines、原文のqwen生成行)を、
    final一括テキスト(text)からも同一の行単位で除去し、turn終了時に確定したpending_actionsを見て
    ①送信系カードが1件でも成立(pid確定)→正直な下書き告知文 ②不成立→_DM_BODY_INCOMPLETE_MSG、
    のいずれか一文に差し替える。stream_claim_held性質と同じ判定材料(pending_actionsの有無)を使い、
    件数/一覧・ストリーム検問・final検問を別ロジックで書かない(掟)。
    ★意志表明("〜します")も完了断定("〜しました")も_send_mention_line_hitで一様に保留された行なので、
    ここでも一様に一文へ差し替える(語彙の形で場合分けしない)。"""
    if not held_lines:
        return text
    _lines = text.splitlines(keepends=True)
    _kept = [ln for ln in _lines if not _send_mention_line_hit(ln)]
    text = "".join(_kept)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    _has_send_card = any((a.get("tool") or "") == "send_message" for a in (pending_actions or []))
    note = _SEND_HELD_DRAFTED_MSG if _has_send_card else _DM_BODY_INCOMPLETE_MSG
    return (text + "\n\n" + note).strip() if text else note


def _guard_completion_claims(text, pending_actions):
    """P1(Fable処方・fail-closed): アクション完了主張は"真実値テキスト"。承認カード(=アクション台帳の
    レシート)が無いのに送信/報告等を断じた文を打ち消す。既成事実化を salvage の網羅性でなく構造で封じる
    ——qwenがどんな未知の書式でツールをテキスト化しても、カードが無ければ完了主張は通さない。
    Aurora系の動詞(登録/保存/起票/呼び出)も対象——「aurora_createを呼び出しました」のようにツール名を
    名乗る嘘も同じ構造で拾う(2026-07-30拡張・目標②本命)。
    汎用動詞(作成/表示/資料化)はAurora文脈がある行でのみ対象とし(cmd_485差戻: 欠陥3是正)、通常チャット
    応答(表の作成/表示等)を巻き込まぬ。差替注記の主語は実際に一致した動詞群から機構的に決定する
    (欠陥2是正: 通信系のみ一致した時にAurora注記を出す誤りを排す)。"""
    if not text or pending_actions:                        # カードあり=台帳にレシート有り→主張は裏付く
        return text
    _hits = [_completion_claim_line_hit(ln) for ln in text.splitlines()]
    if not any(h for h, _ in _hits):
        return text
    # レシート無し＋完了主張 → 該当行を打ち消し、未実行の注記へ差替(fail-closed=疑わしきは実行済と言わせぬ)
    _any_aurora = any(is_au for hit, is_au in _hits if hit)
    _any_comm = any(hit and not is_au for hit, is_au in _hits)
    text = "\n".join(ln for ln, (hit, _) in zip(text.splitlines(), _hits) if not hit)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if _any_aurora and not _any_comm:
        note = "※Auroraへの保存はまだ実行しておりませぬ。承認カードが出ておらねば、もう一度お申し付けを。"
    else:
        # 通信系が混じる(またはAurora以外)場合は主語を固定せず汎用注記へ——事実と異なる主語を語らせぬ(欠陥2)。
        note = "※上記アクションはまだ実行しておりませぬ。承認カードが出ておらねば、もう一度お申し付けを。"
    return (text + "\n\n" + note).strip()


# cmd_512第1便手当6・AC8機械証明用カウンタ: _ollama_json(=LLM分類器の唯一の入口)の呼出回数を数える。
# replay_corpus.pyがreplay前後の差分をこれで読み、規則側判定へ差し替えたことを
# 「grepでurlopenが無い」ではなく実行時の値で証明する(間接呼出も捕捉できる)。
_OLLAMA_JSON_CALL_COUNT = 0


def ollama_json_call_count():
    """現在の_ollama_json呼出累計回数を返す(AC8: replay前後の差分を機械的に取るための単一ソース)。"""
    return _OLLAMA_JSON_CALL_COUNT


# cmd_515手当2(軍師設計subtask_515_strategy1をそのまま実装): 推論機占有の台帳。
# ★★呼出元それぞれの自己申告は採らぬ——推論機へ出る口は3つ(_ollama_json/ollama_chat/
# ollama_chat_stream)あり、cmd_512の_OLLAMA_JSON_CALL_COUNTは_ollama_jsonしか数えておらず
# 残り2つを数え忘れていた(軍師自認の穴)。3箇所すべてが通る薄い出口をここに一つ設け、
# 呼出元はsite名を渡すだけにする(計測の責は束ねた出口が持つ・単一機構の作法)。
# turn(=1 HTTPリクエスト=1スレッド)ごとに集計するためthreading.localへ積む
# (ThreadingHTTPServerゆえ1turn=1スレッド。globalリストだと並行turnで混線する)。
_LLM_CALL_LOCAL = threading.local()
_TURN_SEQ = 0                      # turn印の採番(memoの生存範囲を1発話に閉じるため)


def _llm_call_turn_reset():
    """turn(1 HTTPリクエスト)の開始時に呼ぶ。このスレッドの呼出記録を空にする。"""
    _LLM_CALL_LOCAL.calls = []
    _LLM_CALL_LOCAL.memo = {}          # 【Fable第七診】turn内の意図判定memoも同時に空にする
    _LLM_CALL_LOCAL.memo_hits = 0
    _LLM_CALL_LOCAL.ctx = {}           # turnの素性(雲の帳簿が引く)
    # ★turn印。memoはこの印が立っている間だけ生きる(下記_turn_memo参照)。
    global _TURN_SEQ
    _TURN_SEQ += 1
    _LLM_CALL_LOCAL.turn_id = _TURN_SEQ


def _turn_ctx_set(**kv):
    """このturnの素性(uid/name/thread/trace_id/query)を置く。雲の帳簿が「誰の何の発話が
    社外へ出たか」を名乗れるようにするため(殿御下命2026-08-24)。"""
    c = getattr(_LLM_CALL_LOCAL, "ctx", None)
    if c is None:
        c = _LLM_CALL_LOCAL.ctx = {}
    c.update({k: v for k, v in kv.items() if v is not None})


def _turn_ctx():
    return dict(getattr(_LLM_CALL_LOCAL, "ctx", None) or {})


def _cloud_ledger(door, model, prompt=None, response=None, dur_sec=None,
                  outcome="ok", image_path=None, extra=None):
    """雲へ出た一件を帳簿へ。★本番の応答は決して止めぬ(帳簿の失敗で答えを失わせぬ)。"""
    if not casper_cloud_ledger:
        return
    try:
        casper_cloud_ledger.record(door, model, prompt=prompt, response=response,
                                   dur_sec=dur_sec, outcome=outcome, ctx=_turn_ctx(),
                                   image_path=image_path, extra=extra)
    except Exception:
        pass


def _turn_memo(key, compute):
    """【Fable第七診・同じ問いを二度払わぬ】turn内で同一の意図判定を1回に畳む。
    実測: _asks_about_casper は4箇所(canon判定/web gate/howto digest/出口検問)から独立に
    呼ばれ、同じ問いを最大4往復ぶん推論機へ払っていた。1turn=1スレッド=1発話ゆえ、
    同じ(判定名, 発話)の答えはturn内で変わらぬ。
    ★turn境界を跨いで持ち越さない(_llm_call_turn_resetが空にする)——古い判定が
      次の発話へ漏れれば、それは cache ではなく嘘になる。
    ★Noneも記憶する(判定不能という結論も結論である・三値の掟)。二度目に別の値を返しては、
      同一turn内で機構の判断が揺れる。"""
    # ★turnの外では決して記憶しない。gate_context_handoffが実際にこの穴を撃った——
    #   ゲートはturnを開始せぬまま同じ発話を複数回判じるが、memoが生き残ると
    #   「分類器が例外を投げた回」に前回の答えが返り、機構が嘘をつく。
    #   turn印(_llm_call_turn_resetが立てる)が無い経路=背景スレッド・非turn呼出では
    #   memoを完全に無効化する。記憶して良いのは「1発話の中」だけである。
    if getattr(_LLM_CALL_LOCAL, "turn_id", None) is None:
        return compute()
    m = getattr(_LLM_CALL_LOCAL, "memo", None)
    if m is None:
        m = _LLM_CALL_LOCAL.memo = {}
    if key in m:
        _LLM_CALL_LOCAL.memo_hits = getattr(_LLM_CALL_LOCAL, "memo_hits", 0) + 1
        return m[key]
    v = compute()
    m[key] = v
    return v


def _llm_call_turn_records():
    """このturnで積まれた呼出記録一覧を返す(turn終了時にcasper_trace.emitへ載せるため)。"""
    return list(getattr(_LLM_CALL_LOCAL, "calls", []) or [])


def _llm_is_timeout_error(e):
    """例外がtimeout由来か(outcomeをok/timeout/errorの三値に分ける・失敗とゼロを別出口へ・cmd_512以来の掟)。
    urllib.request.urlopen(timeout=N)超過は socket.timeout(=TimeoutErrorの別名) を送出する。"""
    import socket
    return isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower()


def _llm_call_record(site, model, fn):
    """推論機へ出る3つの口(_ollama_json/ollama_chat/ollama_chat_stream)が共通で通る計測の薄い出口。
    刻む時点は送出★直前と受信★直後(リクエスト構築前でも応答parse後でもない・軍師設計の核心)。
    outcomeはok/timeout/errorの三値(失敗とゼロを別出口へ・cmd_512以来の掟)。
    ★観測のために新たな呼出を増やさない——既存のfn()呼出に時刻を添えるだけ(軍師risk_notes)。"""
    t_send = time.time()
    outcome = "error"
    server_total_sec = server_eval_sec = None
    server_load_sec = server_prompt_eval_sec = None
    server_eval_count = None
    try:
        result = fn()
        outcome = "ok"
        if isinstance(result, dict):
            st, se = result.get("total_duration"), result.get("eval_duration")
            if isinstance(st, (int, float)):
                server_total_sec = round(st / 1e9, 3)
            if isinstance(se, (int, float)):
                server_eval_sec = round(se / 1e9, 3)
            # 【Fable第七診の楔】load(ランナー再ロード)とprefill(prompt評価)を分別する。
            # ★応答dictは元よりこの2欄を載せているのに、我らが捨てていただけである。
            #   これが無いために将軍は「遅さ=生成」と誤帰属した(実測: server_evalは0.1秒台で、
            #   9〜18秒の呼出の正体はload 11.86秒=qwen再ロードであった)。
            _ec = result.get("eval_count")
            if isinstance(_ec, (int, float)):
                server_eval_count = int(_ec)
            sl, sp = result.get("load_duration"), result.get("prompt_eval_duration")
            if isinstance(sl, (int, float)):
                server_load_sec = round(sl / 1e9, 3)
            if isinstance(sp, (int, float)):
                server_prompt_eval_sec = round(sp / 1e9, 3)
        return result
    except Exception as e:
        outcome = "timeout" if _llm_is_timeout_error(e) else "error"
        raise
    finally:
        t_recv = time.time()
        rec = {"site": site, "model": model, "t_send": round(t_send, 3), "t_recv": round(t_recv, 3),
               "wait_sec": round(t_recv - t_send, 3), "server_total_sec": server_total_sec,
               "server_eval_sec": server_eval_sec, "server_load_sec": server_load_sec,
               "server_prompt_eval_sec": server_prompt_eval_sec,
               "server_eval_count": server_eval_count, "outcome": outcome}
        # 生成の速さ(tok/s)。★健康は【速さ】で測る——所要そのものは答えの長さに比例するゆえ
        # 尺度にならぬ(実測: 1203字の正しい答えが server 44.8秒=「故障」と数えられた)。
        if server_eval_count and server_eval_sec:
            rec["server_tps"] = round(server_eval_count / server_eval_sec, 1)
        # 待ちの帰属を機構が名乗る(推測させぬ): load / prefill / eval / queue のどれで待ったか。
        # ★不明を "unknown" と名乗る(未確認をtrueと名乗るな)。
        if server_total_sec is None:
            rec["wait_kind"] = "unknown"
        else:
            _parts = {"load": server_load_sec or 0.0, "prefill": server_prompt_eval_sec or 0.0,
                      "eval": server_eval_sec or 0.0}
            _queue = round(max(0.0, (t_recv - t_send) - server_total_sec), 3)
            _parts["queue"] = _queue
            rec["wait_kind"] = max(_parts, key=_parts.get)
            rec["queue_sec"] = _queue
        calls = getattr(_LLM_CALL_LOCAL, "calls", None)
        if calls is None:
            calls = _LLM_CALL_LOCAL.calls = []
        calls.append(rec)


class LocalClassifierSuppressed(RuntimeError):
    """雲に座っている間、ローカルの分類器を呼ばなかったことを表す。
    ★これは「失敗」ではなく「呼ばなかった」である。呼出元の三値契約では None(判定不能)へ落ち、
    各機構は安全側の既定へ倒れる(送信意図は載せる/canonは差さぬ 等)。"""


_OLLAMA_JSON_SUPPRESSED = 0        # 雲に座っている間に呼ばずに済ませた回数(観測用・黙って変えぬ)


def _ollama_json(system, user, num_predict=400):
    """z8a を format='json' の制約デコードで呼び、JSON文字列を返す(P2ルーター/引数抽出の土台)。
    Ollamaのschema-object modeはqwenが無視する為、format='json'＋プロンプト記述スキーマを使う(実測で確実)。"""
    global _OLLAMA_JSON_CALL_COUNT, _OLLAMA_JSON_SUPPRESSED
    # 【殿御下命 2026-08-24】雲に座っている間は【ローカルの分類器を呼ばぬ】。
    # ★真因(実測): 雲へ移したのは「本文を書く口」だけで、意図判定(_ollama_json)は
    #   従来どおりローカル宛先(CASPER_OLLAMA)を叩き続けていた。ゆえに殿が別作業へ回された
    #   z8a に 27b が再ロードされ、「z8aは使わぬ」の御下命が半分しか効いていなかった。
    # ★呼出元5箇所はすべて try/except で包まれ、三値契約(True/False/None)を持つ。
    #   ここで例外を投げれば None(判定不能)へ落ち、各機構は既に設計された安全側へ倒れる。
    #   「判定を雲へ回す」道も採り得たが、送出量と費えが増えるゆえ殿は「呼ばぬ」を選ばれた。
    if BACKEND in ("claude_cli", "anthropic"):
        _OLLAMA_JSON_SUPPRESSED += 1
        raise LocalClassifierSuppressed(
            f"雲({BACKEND})に着座中ゆえローカル分類器を呼ばぬ(判定不能=Noneへ倒す)")
    _OLLAMA_JSON_CALL_COUNT += 1
    body = {"model": A.model, "stream": False, "think": False, "keep_alive": -1, "format": "json",
            # num_ctx は対話/pinger と統一(Fable): 不一致は Ollama のランナー再作成=実質再ロードで温存を壊す(冷間の真犯人)
            "options": {"num_ctx": 12288, "num_predict": num_predict, "temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}

    def _do():
        req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)

    resp = _llm_call_record("_ollama_json", A.model, _do)
    return resp.get("message", {}).get("content", "")


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
# cmd_510第1便(実害A止血・層1): _DM_INTENT_RE の中で「話題としてDMに触れているだけ」の語は
# 送信の意志を意味しない——「DMを見せて」「DMは届いておりません」のような読取turnにも
# 普通に現れる。_turn_is_send_intent はこれらの話題語だけを除いた残りが_DM_INTENT_RE自身に
# 当たるかで判定する(=新しい語彙表を作らず、既存の単一ソースの中の"話題語"部分だけを
# 機械的に除外する)。
# ★cmd_510第3便是正(軍師QC1条件1): 「共有」はここから除いた。「共有」は話題語でなく行為語であり
# (「共有して」は送信の依頼そのもの)、「この内容をryojiに共有しておいて」のように送信語彙が
# 「共有」しか無い依頼文で、話題語として除くと何も残らずFalse(読取)へ誤判定されていた。
# ★軍師が最も強く警告した方向の誤り(「迷えば送信turn側へ倒せ・不快より嘘の方が重い」の逆)。
# ★cmd_511第2便是正(軍師戦略review済・subtask_511_strategy1): 「報告」「納品」「校了」を
# ここから除いた。これらは話題語でなく行為語である——「〜して」を付けて依頼文になる
# (「報告して」「納品して」「校了を報告して」は送信の依頼そのもの)。「TimにQC結果を報告して」
# 「kiyotomoに納品して」「tetsuoに校了を報告して」のように送信語彙がこれらの語しか無い
# 依頼文で、話題語として除くと何も残らずFalse(読取)へ誤判定されていた(軍師実測)。
# ★cmd_511追加便是正(将軍検品turn2指摘・軍師真因決着): 「連絡」もここへ加えた。「連絡」は
# 「報告」等と同型の両義語(送信/読取いずれの文脈でも自然に現れる)であり、_DM_TOPIC_ONLY_RE
# 側の①(行為として使える)を満たしFalse固定の話題語にはできない一方、②(裸の名詞としても
# 地の文に現れる=「Timからの連絡」のような読取turn)も満たすため、報告/納品/校了と同じ
# 「要接尾辞語」集合に属する。従来は_DM_INTENT_REの残存判定でしか吸収されず、裸の名詞出現
# (「Timからの連絡」)まで送信語彙と誤認しturn2を送信turn(gate=True)と誤判定していた。
# ★残す語は①(行為として使える=「〜して」で依頼文になる)を満たさず、②(話題として名詞で
# 対象を指す)のみを満たす語に限る——道具の名(DM本体・dropbox・リンク)である:
#   DM/ディーエム   … 「DMして」という動詞化はしない。常に名詞(対象)としてのみ現れる。
#   dropbox/ドロップボックス … 固有名詞。動詞化しない。
#   リンク          … 「リンクして」は日常語として送信依頼を意味しない(道具の名として現れる)。
_DM_TOPIC_ONLY_RE = re.compile(r"(DM|ディーエム|dropbox|ドロップボックス|リンク)", re.I)

# cmd_511第2便是正(軍師戦略review point_c裏取りで発覚・実ログ12:37:44「直近のDM見せてsorafuneの
# 報告だと思う」で赤化を確認): 「報告」「納品」「校了」は_DM_TOPIC_ONLY_REから出したが、これらは
# DM/dropbox/リンクと違い★裸の名詞としても文中に自然に現れる(「〜の報告だと思う」「納品状況」
# 「校了の状況」)。裸のまま_DM_INTENT_REの残存判定に委ねると、読取turn中の地の文の名詞まで
# 送信語彙と誤認する(実害Aの再演)。行為語として真に効くのは①(「〜して」を付けて依頼文になる)
# の場合のみゆえ、_DM_TOPIC_ONLY_REと対称の「要接尾辞語」集合として別途持ち、
# 依頼接尾辞が直後に付く時だけ送信語彙として認める(DM等の話題語+接尾辞と同型の構造判定)。
# ★cmd_511追加便で「連絡」を追加(理由は上のコメント参照・turn2「Timからの連絡」の誤判定是正)。
# ★cmd_511追加便・軍師全数検査(裁可済): 「送信」「共有」も同型の漏れと判明したため追加。
# 「共有」はcmd_510第3便で_DM_TOPIC_ONLY_REから正しく外されたが(行為語ゆえ)、その際
# _DM_AMBIGUOUS_VERB_ONLY_REへの転記が漏れ裸の名詞のまま残っていた——「Timからの共有、
# ありがとう」のような読取turnの地の文が誤ってTrue(送信)判定されていた。
# 「送信」は元から未対応(_DM_INTENT_REの裸語彙のまま)——「Timからの送信を確認」も同型の誤判定。
# cmd_511追加便2是正(軍師QC4 verdict=CONDITIONAL_PASS・全数検査の母集合を「corpus内実例」から
# 「_DM_INTENT_REの全語」へ拡大): 上のコメントの「対象外」判断は誤りだった。「からの<語>」の
# 直後結合(裸名詞)としては不成立でも、_DM_INTENT_REはsearch(部分一致)であり文中のどこに現れても
# 一致する。「Timが送ってきたDMを見せて」「Timからの届け物ある？」のように、動詞の活用形が
# 過去形/completed表現(〜てきた/〜た/〜物 等)の一部として地の文に現れる読取turnで、
# 依頼形(〜して/〜しといて)と区別できず送信語彙として誤判定されていた(軍師実測)。
# 全数検査(_DM_INTENT_REの全語を「<人名>からの<語>」/「<人名>に<語>して」で機械的に走らせた)で
# 検出: 伝え/届け/投げ/送って/送る/送っといて/渡し/渡す/渡して の9語。
# これらは報告/納品/校了等(名詞+する)と違い★既に活用済みの動詞(て形/終止形)であり、依頼接尾辞
# 「して」を追加結合すると非文になる(「送っといてして」)。よって同じ「要接尾辞語」構造は使えず、
# 代わりに「動詞の直後に依頼として成立する形しか続いていないか」を正の許可リストで判定する
# (_DM_VERB_REQUEST_TAIL_RE)。非依頼の継続表現は「てきた/た/物」等、際限なく増えうる開集合で
# 網羅列挙は漏れの再演を招くため、依頼として成立する続き(ください/くれ/おいて/ね/よ/文末等)
# という閉じた許可リスト側で判定する方が堅牢(軍師QC4指摘の同型再発防止)。
_DM_AMBIGUOUS_VERB_ONLY_RE = re.compile(r"(報告|納品|校了|連絡|送信|共有)", re.I)
# 依頼接尾辞: 「して」系(意志/完了/丁寧命令形含む)。cmd_510第1便のTOPIC語+動詞化と同型。
_REQUEST_SUFFIX_RE = r"(して|しといて|しといてください|してください|します|しました)"
_DM_TOPIC_WORD_AS_VERB_RE = re.compile(
    r"(?:" + _DM_TOPIC_ONLY_RE.pattern + r")" + _REQUEST_SUFFIX_RE, re.I)
_DM_AMBIGUOUS_WORD_AS_VERB_RE = re.compile(
    r"(?:" + _DM_AMBIGUOUS_VERB_ONLY_RE.pattern + r")" + _REQUEST_SUFFIX_RE, re.I)
# 既に活用済みの動詞語彙(_DM_INTENT_RE内の該当語すべて・て形/連用形/終止形いずれも含む)。
# 残存判定(stripped)からはこの全形を除く(裸の語幹/活用形のどれで現れても話題語・両義語と
# 同様にノイズとして扱う——依頼形かどうかは別途_dm_verb_word_as_requestのみで判定する)。
_DM_VERB_CONJUGATED_RE = re.compile(
    r"(送っ?と?い?て|送る|送って|伝え(て)?|渡(して|し|す)|届け(て)?|投げ(て)?)", re.I)
# 依頼として成立する語形(て形のみ)にマッチ末尾の直後、依頼として成立する続き
# (_DM_VERB_REQUEST_TAIL_RE)だけが来ていれば依頼形と認める。裸の語幹(伝え/届け/投げ等・
# て形化されていない)はここでは対象外(「Timに伝え」だけでは依頼として不成立)。
_DM_VERB_TE_FORM_RE = re.compile(r"(送っ?と?い?て|送って|伝えて|渡して|届けて|投げて)", re.I)
_DM_VERB_REQUEST_TAIL_RE = re.compile(r"^(ください|くれ|おいて|ね|よ|[。！？\s]|$)", re.I)


def _dm_verb_word_as_request(q):
    """活用済み動詞語彙のて形(_DM_VERB_TE_FORM_RE)が、直後に依頼として成立する続き
    (_DM_VERB_REQUEST_TAIL_RE)のみを伴って現れているかを判定する。「Timに送って」は
    True、「Timが送ってきた」「送ったファイル」のように動詞へさらに活用/名詞化が続く場合はFalse。"""
    for m in _DM_VERB_TE_FORM_RE.finditer(q):
        if _DM_VERB_REQUEST_TAIL_RE.match(q[m.end():]):
            return True
    return False


# _DM_INTENT_REの残存判定(stripped)から、報告/納品/校了の"裸の名詞出現"も除外する必要がある
# (接尾辞判定は別途_DM_AMBIGUOUS_WORD_AS_VERB_REで拾うため、残存判定側では話題語と同様に
# ノイズとして除いてよい)。動詞語彙(_DM_VERB_CONJUGATED_RE・て形/裸の語幹いずれも)も同様に、
# 依頼形判定は別途_dm_verb_word_as_requestで拾うため残存判定からは除く。
_DM_STRIP_FOR_RESIDUAL_RE = re.compile(
    _DM_TOPIC_ONLY_RE.pattern + "|" + _DM_AMBIGUOUS_VERB_ONLY_RE.pattern + "|" +
    _DM_VERB_CONJUGATED_RE.pattern, re.I)


# cmd_511第1便(補集合設計への転換・軍師戦略review済subtask_511_strategy2): 接尾辞列挙方式
# (_DM_VERB_REQUEST_TAIL_RE/_REQUEST_SUFFIX_RE)は開集合(依頼の言い回し)を閉集合と誤認する
# 病五の再演である——「をお願い」「を頼む」「てもらえる」「てほしい」「ていただけますか」等の
# 丁寧語形6組すべてが漏れ、送信依頼を読取と誤判定していた(将軍検品subtask_511_qc5_shogun_finding)。
# 差出人マーカー: roster人名は解けても「〜からの」「が送っ」は宛先でなく差出人であり、
# 「tetsuoからの報告を教えて」のような読取turnを送信と誤判定する穴を塞ぐ(軍師実測)。
_DM_SENDER_MARKER_RE = re.compile(r"(から(の|来た|届いた|もらった|送られ)|が送っ)", re.I)


def _dm_sender_marker_hit(q, resolved_name):
    """★足軽2号実測是正(AC-S2掃引で発覚): _DM_SENDER_MARKER_REの列挙(の|来た|届いた|
    もらった|送られ)は「から」の直後の続きを開集合で数え上げており、「Timから送っといてと
    言われた件」のような自由形の伝聞(「から<動詞>と言われた」等)を漏らす。続きを列挙で
    足すのは病五の再演ゆえ、代わりに「解決した人名(resolved_name)そのものの直後にから が
    来ているか」という格助詞の構造で判定する(数え上げるのは"から"という助詞一つだけであり、
    続きの形は問わない=閉じている)。"""
    if _DM_SENDER_MARKER_RE.search(q):
        return True
    if resolved_name and re.fullmatch(r"[A-Za-z0-9]+", str(resolved_name)):
        if re.search(re.escape(resolved_name) + r"から(?![^ぁ-ヿ一-龯A-Za-z0-9]*(に|へ))",
                      q, re.I):
            return True
    return False


# cmd_512第4便(手当4の依頼形3種の穴・軍師戦略review済subtask_512_strategy4): 条件(4)は
# _QUESTION_FORM_RE/_REQUEST_FORM_REのいずれにも当たらない「してほしい」「よろしく」
# 「願います」の3形を通さず、11語すべてがこの3形で読取へ誤って倒れていた(軍師QC2で発見)。
# 軍師は当初、条件(4)を補集合側へ寄せる案(案B)を献策したが★自ら実装・実測し、名詞句読取
# 6件が新規に送信誤判定される退行(「kiyotomoは元気」まで送信と判定される等)を発見して
# ★自ら棄却した。代わりに★案D(宛先格判定)を献策・実測済み: 穴の3形と退行例を比較すると、
# 人名の直後の格助詞が弁別子になる——「kiyotomoへ送付してほしい」は人名直後が「に/へ」
# (宛先格)、「kiyotomoの送付状況」「kiyotomoは元気」は人名直後が「の/は」(連体・主題)。
# 送信依頼は定義上「宛先へ向かう」行為ゆえ、宛先格の有無こそが弁別子である。
# _dm_sender_marker_hitの対称形として設計: 数え上げるのは格助詞「に」「へ」の二つだけであり
# 依頼の言い回し(開集合)は数えない。ただし「〜に関する/〜について」等はにが宛先格でなく
# 連体化のため除く。
_DATIVE_NON_ADDRESSEE_RE = re.compile(r"^(関する|関し|対する|対し|ついて|つき|おける|おいて)")


def _dm_dative_marker_hit(q, resolved_name):
    """解決した人名の直後の格助詞が に/へ(宛先格)であるかを見る。"""
    if not resolved_name:
        return False
    for m in re.finditer(re.escape(str(resolved_name)) + r"\s*(に|へ)", q, re.I):
        if not _DATIVE_NON_ADDRESSEE_RE.match(q[m.end():]):
            return True
    return False


def _turn_is_send_intent(user_query, exclude_uid=None):
    """cmd_510第1便(実害A止血・層1): このturnがユーザーの送信依頼か否かを、ユーザーの発話
    (user_query)のみから一度だけ判定する。qwenの応答行を見て判定しない
    (話題がDMである限り応答にも「DM」が現れるのは当然で、判定材料として腐っている——実害Aの真因)。
    語彙は既存の_DM_INTENT_RE(送信意図の単一ソース)を流用し、新しい語彙表は作らない。
    ★安全側設計(軍師裁定): 判定に迷えば必ずTrue(送信turn=従来動作)へ倒す。読取と断ずるのは
    送信意図語彙が一つも無い時、または在ってもすべて話題語(DM等)に尽きる時だけ。
    不快(誤って物乞い文が出る)より嘘(誤って完了詐称が通る)の方が重い。
    ★cmd_511第2便是正: 話題語(DM等)・両義語(報告/納品/校了)いずれも、依頼接尾辞
    (「して」等)で動詞化されている場合はTrueへ倒す(_DM_TOPIC_WORD_AS_VERB_RE/
    _DM_AMBIGUOUS_WORD_AS_VERB_RE)。接尾辞が無い裸の名詞出現(「〜の報告だと思う」等の
    地の文)は残存判定から除外し、他に送信語彙が無ければFalseのまま保つ
    (実ログ12:37:44の赤化で発覚・軍師戦略review point_c)。
    ★cmd_511追加便2是正(軍師QC4全数検査): 活用済み動詞語彙(伝え/届け/投げ/送って/送る/
    送っといて/渡し/渡す/渡して)は、直後に依頼として成立する続きのみを伴っていれば
    依頼形と認めTrueへ倒す(_dm_verb_word_as_request)。それ以外の続き(〜てきた/〜た/〜物等)
    が来ていれば残存判定からも除外し、他に送信語彙が無ければFalseのまま保つ
    (「Timが送ってきたDMを見せて」「Timからの届け物ある？」が誤ってTrueになっていた実害の是正)。
    ★cmd_511第1便(補集合設計): roster人名が解け(_resolve_person(q, exclude=exclude_uid))、
    _DM_INTENT_REが一致し、読取意図(_EXIST_Q_RE)が無く、差出人マーカー(_DM_SENDER_MARKER_RE)も
    無ければ、接尾辞語彙が何であれTrueへ倒す(丁寧語形「〜をお願い」「〜ていただけますか」等、
    接尾辞表に無い依頼形も拾う)。roster人名が解けない場合(「あの人に送っておいて」等の
    指示語宛先)は、従来の接尾辞判定へフォールバックし「迷えば送信側」の既定を保つ
    (軍師risks: rosterの痩せ対策)。
    ★cmd_512第3便(手当4・門の構造是正): 上記の補集合設計は、_DM_INTENT_RE(語彙表)が
    ★冒頭で絶対の門になっていたため、語彙表に無い語(送付/転送/展開/回覧/提出/申し送り/
    打診/差し替え/上申/周知/配信等)は宛先が解けていても一度も適用されなかった
    (病五=「二層のうち上の層に居残っていた」)。よって門の順序を入れ替え、
    (1)roster人名が解ける ∧ (2)読取意図(_EXIST_Q_RE)が無い ∧ (3)差出人マーカーが無い ∧
    (4)依頼形である(_QUESTION_FORM_RE/_REQUEST_FORM_RE・既存の単一ソース) の4条件が
    揃えば、_DM_INTENT_REに一語も当たらずとも送信へ倒す。新しい語彙表は作らず、既に在る
    解決器(roster)と既存の形式判定を組み替えるだけである。
    ★リスク対応: 条件(1)〜(4)だけでは「kiyotomoに今日の予定を教えて」のような、宛先が
    解けるだけの読取turnまで送信へ倒しかねない——「教えて」は_EXIST_Q_REの語彙に無く
    通らない。そこで(4)をさらに「依頼形 ∧ 述語が読取語(教え/どう等)でない」まで絞る。
    読取述語は_SEND_GATE_READ_PREDICATE_RE(教え|どう(です)?|どうな)で判定する——これは
    cmd_508第3便で既に定義済みの_ANCHOR_PREDICATE_WORD_REの★部分集合であり新しい語彙表
    ではない(「お願い/ください/くれ/知りたい」は読取・送信いずれの依頼文にも付く丁寧さの
    標識に過ぎずread/send判別に使えないため除外した——全語をそのまま流用すると送信の
    丁寧語形「〜をお願いします」まで読取側へ誤って倒れる実害があった・軍師実測)。
    (軍師実測・AC-S2読取形6種×全語＋丁寧語形の複数文型で退行0件を確認済)。
    ★cmd_512第4便(手当4の依頼形3種の穴是正・軍師戦略review済案D): 条件(4)は
    _QUESTION_FORM_RE/_REQUEST_FORM_REのいずれにも当たらない「してほしい」「よろしく」
    「願います」の3形を通さず、11語すべてがこの3形で読取へ誤って倒れていた(軍師QC2で発見)。
    軍師は当初、条件(4)を補集合側へ寄せる案(案B)を実装・実測したが、名詞句読取6件が新規に
    送信誤判定される退行(「kiyotomoは元気」等)を発見して自ら棄却し、代わりに案D(宛先格判定・
    _dm_dative_marker_hit)を採用した。人名直後の格助詞が「に/へ」(宛先格)であればORで
    条件(4)を満たす——「〜に関する/〜について」等の連体化は除く。"""
    q = user_query or ""
    if not q:
        return True                          # 迷えば送信turn側(安全側)
    _uid, _name = _resolve_person(q, exclude=exclude_uid)
    if (_uid is not None and not _EXIST_Q_RE.search(q)
            and not _dm_sender_marker_hit(q, _name)
            and not _SEND_GATE_READ_PREDICATE_RE.search(q)
            and (_QUESTION_FORM_RE.search(q) or _REQUEST_FORM_RE.search(q)
                 or _dm_dative_marker_hit(q, _name))):
        return True                          # 手当4(cmd_512第4便案D): 語彙表(_DM_INTENT_RE)を経由せず送信へ倒す
    if not _DM_INTENT_RE.search(q):
        return False                         # 送信意図語彙が一つも無い→読取と断じてよい
    if _uid is not None and not _EXIST_Q_RE.search(q) and not _dm_sender_marker_hit(q, _name):
        return True                          # 補集合設計: 宛先が解け読取意図も差出人マーカーも無い→送信
    if _DM_TOPIC_WORD_AS_VERB_RE.search(q):  # 話題語が依頼接尾辞で動詞化されている→送信依頼そのもの
        return True
    if _DM_AMBIGUOUS_WORD_AS_VERB_RE.search(q):  # 両義語が依頼接尾辞で動詞化されている→送信依頼そのもの
        return True
    if _dm_verb_word_as_request(q):          # 活用済み動詞語彙が依頼として成立する続きを伴って現れている→依頼形そのもの
        return True
    stripped = _DM_STRIP_FOR_RESIDUAL_RE.sub("", q)  # 話題語・両義語・動詞語彙の裸出現を除いても送信語彙が残るか
    return bool(_DM_INTENT_RE.search(stripped))


_PW_RE = re.compile(r"(?:🔑|パス(?:ワード)?|ぱす|pw|password|ＰＷ)\s*(?:は|=|＝|:|：)?\s*"
                    r"([A-Za-z0-9][A-Za-z0-9!-/:-@\[-`{-~]{2,})", re.I)   # PWは英数字始まり(散文『パスワードで共有』を拾わない)
# 文脈参照(『このファイル/これ/上記/先ほどの』)。発火は別途『URL無し＋DM意図＋宛先解決＋直前にURL有り』で厳重ゆえ広めで安全。
_FILE_REF_RE = re.compile(r"(この|その|これ|それ|先(の|ほど)|上記|さっき|例の|上の|あれ|奴|やつ)", re.I)
# ただし指示語の受け先が『表/一覧/まとめ/内容』なら、それは会話中の表への参照であってファイルではない
# (実測2026-07-27: 『この表の中で〜』の"この"を共有ファイル参照と取り、直前のAurora URLを配信しようとした)。
_NOT_FILE_REF_RE = re.compile(r"(この|その|上の|先の|さっきの|今の|前の)(表|テーブル|一覧|リスト|まとめ|内容|話|件)", re.I)
# "これは配信でなく"伝える"意図"の合図。担当違い/誤送/間違い等は、URLを配信するのでなく文面を書くべき。
# (殿指摘2026-07-13: 誤QC依頼のURLを「このQCは担当でない旨をkiyotomoにDM」と言ったのにURL配信扱いされた)
# 「担当出ない」のIME誤変換(で→出)も拾う。「違う」単独は拾わない(『違うファイルを送って』の正当配信を潰さぬ為)。
#   ★問いを立てる依頼は配信ではない(殿御指摘2026-07-27「DM内容が微妙」)。
#   『この表の中でcasperが変更かける必要があるステータスはどれか？をkiyotomo、Tetsuoに確認するDMを』へ、
#   ファイル配信の定型『データをお送りします。ご確認ください。＋URL』を返した。問いが本文から消え、
#   宛先も一名に落ちた。相手に尋ねる/相談する意図は、文面を書くべき筋であって、リンクを投げる筋ではない。
_ASK_INTENT_RE = re.compile(r"(確認|質問|問い合わせ|聞(く|いて|きたい)|尋ね|伺(う|い)|相談|意見|"
                            r"どれ(が|か)|どちら|どうする|可否|要否|決めて|判断(を|して)|ヒアリング)", re.I)
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
    if _looks_declarative(q):
        # 資料URLを添えた定義/合意の共有は「これを誰かへ送れ」ではない。実測2026-07-27 19:05:
        # 19値の定義表を貼られて『共有リンクは受け取りました。どなた宛にDMしましょう？』と返した。
        return None
    if _ASK_INTENT_RE.search(q):
        # 尋ねる/相談する依頼は、定型の配信文でなく本文を書く経路へ譲る(問いが消えるのを防ぐ)。
        return None
    if _NOT_DELIVERY_RE.search(q):
        # 「担当でない/誤送/間違い」=URLを配信するのでなく"その旨を伝える"文面が要る→配信fast-pathを退き、
        # 通常のDM作成(LLMが文脈のQC情報から丁寧な誤送連絡を起こす+承認カード)に委ねる(殿指摘2026-07-13)。
        return None
    urls = _URL_RE.findall(q)
    src = q                                               # PW/URLの抽出元(既定=現メッセージ)
    fname = None
    if not urls and _FILE_REF_RE.search(q) and not _NOT_FILE_REF_RE.search(q):   # 『このファイルを〜DMして』=直前の共有ブロックから引く
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


# cmd_508 第3便(病三・E01): 『この内容をryojiに共有しておいて』——指示語(_FILE_REF_RE語彙)の指す先が
# ファイル/URLでなく「直前の自分の応答本文」である場合の配信。_file_delivery_dmはURLの有無で
# 分岐するため、_NOT_FILE_REF_RE(「この内容」「この話」等)に該当する語はそもそも_file_delivery_dmの
# 対象外(意図的な設計・L1994の注記どおり)。ここがその空白を埋める——カード機構自体は正しく働いていた
# (Fableの補正)。直すべきは「空本文でカードを立てる」ことではなく「この内容」→直前の自分の応答本文、
# という接地の欠如。★空本文でカードを立ててはならぬ(holdout NG条件の自作)ゆえ、本文が空/取得不能なら
# 何もせず通常経路(qwenの聞き返し)へ委ねる。
def _own_response_delivery_dm(user_msg, who, convo=None):
    q = user_msg or ""
    if not _DM_INTENT_RE.search(q):
        return None
    if not _NOT_FILE_REF_RE.search(q):
        return None                                       # 「この内容/この話」等でなければ本機構の対象外(通常のFILE_REF等は_file_delivery_dmへ)
    if _URL_RE.search(q) or _DM_QUOTED_BODY_RE.search(q):
        return None                                       # 新素材(URL/鉤括弧本文)が明示されているならそちらが正(通常経路に譲る)
    if _looks_declarative(q) or _ASK_INTENT_RE.search(q) or _NOT_DELIVERY_RE.search(q):
        return None                                       # 定義の共有/問いを立てる依頼/誤送訂正は別筋(_file_delivery_dmと同じ除外)
    last_body = _clean_dm_body(_last_assistant(convo or []))
    if not last_body.strip():
        return None                                       # 直前に自分の応答が無い/空→空本文でカードを立てぬ(NG条件の自作を避ける)
    q_norest = re.sub(r"(この内容|この話|その内容|その話|上記の内容|前の内容|今の内容)", " ", q)
    uid, name = _resolve_person(q_norest, exclude=None)
    if not uid:
        uid, name = _fuzzy_person(q_norest)
    if not uid:
        return {"_choices": True,
                "reply": "内容は把握しました。**どなた宛にお送りしましょう？** お名前を教えてくだされ"
                         "（例: てつお／tetsuo／寺島さん 等）。"}
    body = last_body
    args = {"to_user_id": str(uid), "body": body}
    if who.get("uid"):
        args["actor_id"] = who["uid"]
    reply = (f"**{name}** 宛に、直前の内容を下記のDM下書きとして作成しました（まだ送っておりませぬ）。"
             "↓の承認カードで確認・編集し、ボタンを押すと送信されまする。\n\n> " + body.replace("\n", "\n> "))
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

# cmd_499: 語彙を足さず「問いかけ＋列挙＋選択肢の中身の薄さ」の三条件で裸の列挙選択を構造的に捉える。
# _NAKED_CHOICE_REが言い回し(「選択してください」等)のみを見て、「いたしますか？+①②」の列挙形を
# 素通ししていた穴(kiyotomo殿実害2026-08-05)への手当。_NAKED_CHOICE_REは残し両輪とする。
_ENUM_LINE_RE = re.compile(r"^\s*(?:[①-⑳]|[0-9]{1,2}\s*[.)、]|[a-zA-Z]\s*[.)])\s*(?P<body>\S.*)$")
# 行頭のマーカー＋本文という構造で捉える(丸数字/数字ドット/英字を一網に)

_ASK_RE = re.compile(r"(いたしますか|しますか|ましょうか|でしょうか|いかがいたし|"
                     r"どちら|どれ|よろしいですか)[\s　]*[？?]?\s*$", re.M)
# 行末で問うている形(疑問符の有無を問わぬ)。行末に錨を打つことで文中の「〜しますが」等を拾わぬ

_THIN_OPTION_RE = re.compile(r"^(はい|いいえ|する|しない|不要|結構|"
                             r"お願い|やめ|中止|キャンセル)")
# 選択肢の本文が行動の可否だけで中身を持たぬか。
# 「はい、内容を確認して…」は"はい"で始まる=中身が無い側→捉える対象。
# 「黒丸クロマル(Animator/Maya…)」は該当せぬ=中身を持つ側→消さぬ(候補提示は保護)。


# ★cmd_508病四是正: 引用資料内の列挙(a)(b)(c)等)を「Casperが裸の選択を迫った」と誤認する事故への
# 手当。列挙行の本文が注入素材(sysadd等・vault/検索結果の引用元)に文字n-gramでfuzzy一致するなら、
# それは「引用内容」であって「選択装置」ではない——検問対象から外す。fewshot_digestの_bg型と同じ
# 文字bigram重なりで判定する(日本語は空白無しゆえ語分割でなくn-gram)。
_QUOTE_NGRAM_MIN_OVERLAP = 4


def _bg(s):
    s = re.sub(r"[\s、。・！？]", "", str(s or ""))
    return set(s[i:i + 2] for i in range(len(s) - 1))


def _enum_line_is_quoted(body, injected_grams):
    if not injected_grams:
        return False
    bg = _bg(body)
    if not bg:
        return False
    return len(bg & injected_grams) >= _QUOTE_NGRAM_MIN_OVERLAP


def _bare_enum_choice(text, injected=""):
    """裸の列挙選択(装置なしで①②を並べて選ばせる形)を構造で捉える。
    三条件すべてを満たす時のみTrue:
      ① 問いかけの行が在る(_ASK_RE)
      ② その直後3行以内に列挙行が2件以上(_ENUM_LINE_RE)
      ③ 列挙の本文が中身を持たぬ(_THIN_OPTION_REが過半に当たる)
    ③が過剰検出を防ぐ要。候補提示(人名+役割等)は③で落ちるゆえ消えぬ。
    列挙の直後性(問いかけ行から3行以内に最初の列挙行が来ること)も条件に含む。
    ★cmd_508: 列挙本文が注入素材(injected)にfuzzy一致すれば引用とみなし、その列挙行は
    列挙数に数えぬ(装置なし選択の判定から除外)。"""
    if not text:
        return False
    injected_grams = _bg(injected) if injected else set()
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if not _ASK_RE.search(ln):
            continue
        enum_lines = []
        for j in range(i + 1, min(i + 4, len(lines))):
            m = _ENUM_LINE_RE.match(lines[j])
            if m:
                if _enum_line_is_quoted(m.group("body"), injected_grams):
                    continue                            # 引用行は列挙数に数えぬ(選択装置ではない)
                enum_lines.append(m.group("body"))
            elif enum_lines:
                break                                    # 列挙の連続が途切れたらそこで打ち切り
        if len(enum_lines) < 2:
            continue
        thin = sum(1 for b in enum_lines if _THIN_OPTION_RE.match(b.strip()))
        if thin * 2 >= len(enum_lines):                  # 過半が中身の薄い選択肢
            return True
    return False


def _validate_choices(text, pending_actions, choices=None, injected=""):
    """【選択の出口検問=Fable Q2・不変条件①】『選択してください』等の選択要求を書くなら、必ず選択装置
    (承認カード or choices)が随伴していること。裸の選択要求(装置なし)は該当文を削除し中立な誘導へ差し替える。
    弱モデルが『選べ』と言うだけで選択手段を出さぬ事故(殿指摘)を出口で機械的に封じる最終防壁。
    cmd_499: _NAKED_CHOICE_RE(言い回し)に加え_bare_enum_choice(構造)も検問対象とする(両輪)。
    ★cmd_508病四是正:
      ① 列挙行が注入素材(injected)にfuzzy一致するなら引用であって選択装置ではない
        ——_bare_enum_choiceへ渡し検問対象から外す(裸選択カウントに数えぬ)。
      ② kept構築での導入文(_ASK_RE行)削除をやめる(箇条書きの孤児化を是正)。
         削るのは_NAKED_CHOICE_RE該当文と、引用でない列挙行(_ENUM_LINE_RE・非引用)のみ。
      ③ 非空の成功本文への失敗文appendを廃止。『お出しできませなんだ』は空出口専用。"""
    if not text:
        return text
    if pending_actions or choices:                          # 選択装置が随伴→正当な選択要求。素通し
        return text
    _enum_hit = _bare_enum_choice(text, injected=injected)
    if not (_NAKED_CHOICE_RE.search(text) or _enum_hit):
        return text
    injected_grams = _bg(injected) if injected else set()

    def _is_naked_enum_line(p):
        m = _ENUM_LINE_RE.match(p.strip())
        if not m:
            return False
        if _enum_line_is_quoted(m.group("body"), injected_grams):
            return False                                    # 引用行は削らぬ(孤児化防止)
        return True

    # 裸の選択要求: 該当文(_NAKED_CHOICE_RE)・引用でない列挙行(_ENUM_LINE_RE)のみ落とす。
    # ★導入文(_ASK_RE行)はもう削らぬ——列挙が引用として保護された場合に導入だけ消えて
    # 箇条書きが孤児化する事故(F01)を断つ。
    parts = re.split(r"(?<=[。\n])", text)
    kept = [p for p in parts
            if not _NAKED_CHOICE_RE.search(p) and not _is_naked_enum_line(p)]
    out = re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
    if not out:
        out = ("お選びいただける項目が今はございませぬ。『下書きを見せて』等とお申し付けあらば、"
               "中身つきの選択肢（承認/破棄ボタン）を機構でお出しいたす。")
    return out


_NUM_ONLY_RE = re.compile(r"^\s*(?P<n>[①-⑳]|[0-9０-９]{1,2})\s*[.)、]?\s*$")
_ENUM_FRESH_SEC = 30 * 60              # 鮮度: 30分以内の列挙のみ番号を突合する

_CIRCLED_NUM = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _enum_num_to_index(raw):
    """①②③.../全角数字/半角数字いずれの表記でも1-based indexへ正規化する。不正なら None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw in _CIRCLED_NUM:
        return _CIRCLED_NUM.index(raw) + 1
    try:
        return int(raw.translate(_FULLWIDTH_DIGITS))
    except ValueError:
        return None


def _resolve_number_reply(query, thr, who):
    """直前turnの応答に列挙が在った時のみ、番号を選択とみなす(cmd_499論点c)。
    条件(すべて満たす時のみ):
      ① 入力が数字のみ(_NUM_ONLY_RE)
      ② 直前turnの自分の応答が_LAST_ENUM[thr]に控えてある(30分以内・同一uid)
      ③ 番号が列挙の範囲内
    該当行の本文を返し、検索語でなく前turnの文脈に接ぐ指示として扱う。
    該当せねばNoneを返し通常処理へ流す(threadを跨いで持ち越さぬ・cmd_492教訓)。"""
    m = _NUM_ONLY_RE.match(str(query or ""))
    if not m:
        return None
    idx = _enum_num_to_index(m.group("n"))
    if not idx:
        return None
    rec = _LAST_ENUM.get(thr)
    if not rec:
        return None
    if rec.get("uid") != (who or {}).get("uid"):          # 別人の列挙は引き継がない
        return None
    if time.time() - float(rec.get("ts") or 0) > _ENUM_FRESH_SEC:   # 鮮度切れ
        return None
    lines = rec.get("lines") or []
    if idx < 1 or idx > len(lines):                        # 範囲外
        return None
    return lines[idx - 1]


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
    # 【送信者詐称の是正・M2秘匿(2026-07-15)】X-Actor-User-Id は誰でも付けられるヘッダゆえ、無条件に信じると
    # LAN上の第三者が任意uid(例:殿=28)へ成りすませる(bind=0.0.0.0:8770=LAN公開)。現状 inbound で本ヘッダを正当に
    # 送る送り手は皆無(唯一の設定箇所 casper_mcp は Casper→Calendar の outbound)。ゆえ信じるのは①loopback発
    # ②host共有secret一致 の時のみに機構で限定し、それ以外は JWT(casper_token)検証のみを認証とする(なりすまし機構否定)。
    _xactor = (handler.headers.get("X-Actor-User-Id", "") or "").strip()
    _cip = handler.client_address[0] if getattr(handler, "client_address", None) else ""
    _origin_ok = _cip in ("127.0.0.1", "::1", "localhost")
    _hsec = os.environ.get("CASPER_HOST_SECRET", "")
    _secret_ok = bool(_hsec) and (handler.headers.get("X-Casper-Host-Secret", "") == _hsec)
    uid = _xactor if (_xactor and (_origin_ok or _secret_ok)) else ""   # 信頼できるoriginのみ header を採用
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


def uploader_to_aurora(src_path, note, filename, uid):
    """【殿御下命2026-08-26】投じられた資料を Aurora(共有ノート図書館)へ載せる道。

    実害(2026-08-26 18:33): kiyotomo殿が「sorafune 様　MTG.rtf」を投じ「Auroraにアップ」と
    書き添えたが、uploader の行先は qc/daily/reference の三つしか無く、note の文言は読まれぬまま
    qc→daily へ流れ、Aurora には一度も届かなかった。

    ★書き込まぬ。承認カードを立てるだけ。Aurora書込は承認制ゆえ、ここに直書きの裏口を作れば
      門が二つになる(掟: 件数と一覧は同一機構)。
    ★抽出の失敗を本文として載せぬ。casper_extract は失敗を「(非対応形式 .xxx)」のような
      括弧書きで返す。これをそのまま載せれば、その一行が全社の共有資料になる。
      失敗とゼロ(空)と成功を、それぞれ別の出口で名乗らせる。

    戻り値(常に dict): {"ok":bool, "written":False, "dest":"aurora",
                       "confirm":{...}(成功時のみ), "message":str, "reason":str(失敗時のみ)}
    """
    if not src_path or not os.path.exists(src_path):
        return {"ok": False, "written": False, "dest": "aurora", "reason": "no_file",
                "message": "⚠️ Aurora に載せられませぬ — 投じられた実体が見当たりませぬ(添付し直してくだされ)"}
    if not casper_extract:
        return {"ok": False, "written": False, "dest": "aurora", "reason": "no_extractor",
                "message": "⚠️ Aurora に載せられませぬ — 本文抽出の機構が無効にござる"}
    body = casper_extract.extract(src_path) or ""
    if body.startswith("("):                       # 抽出できなかった(非対応形式・読取失敗・空 等)
        return {"ok": False, "written": False, "dest": "aurora", "reason": "extract_failed",
                "message": f"⚠️ Aurora に載せられませぬ — {body.strip('()')}"}
    if not body.strip():
        return {"ok": False, "written": False, "dest": "aurora", "reason": "empty",
                "message": "⚠️ Aurora に載せられませぬ — 本文が空にござる"}
    title = re.sub(r"\s+", " ", (note or "").strip())[:120] \
        or os.path.splitext(os.path.basename(filename or src_path))[0]
    args = {"title": title, "body": body[:20000], "tags": ["casper", "uploader"]}
    summary = _action_summary("aurora_create", args)
    pid = _register_pending("aurora_create", args, uid, summary,
                            origin="user", query=f"[uploader] {filename} → Aurora")
    if not pid:                                    # 起票できなんだ = 成功と名乗らぬ
        return {"ok": False, "written": False, "dest": "aurora", "reason": "propose_failed",
                "message": "⚠️ Aurora への下書き登録に失敗しました"}
    return {"ok": True, "written": False, "dest": "aurora",
            "confirm": {"id": pid, "tool": "aurora_create", "args": args, "summary": summary},
            "message": ("📖 Aurora への下書きを立てました。**まだ書き込んでおりませぬ**"
                        f"——下の承認ボタンを押すと保存されまする。(本文 {len(body)}字を抽出)")}


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
        # 遅延=isOverdue派生。除外は status_category(completed/held) が正——単一機構 _sr に委ねる
        # (旧: status∉{deliver,omit} のハードコード → ap/client_ap を超過と誤判定していた。2026-07-27是正)
        dl = [t for t in tasks if _sr.task_overdue_days(t)]
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
                    "\nスキルシート由来の有名作は個人メンバー経歴ゆえ会社実績と区別。")
        # 全件明示 → 全件注入(截ち切れは自動継続機構が拾う)
        body = "\n".join([header, "|---|---|---|---|"] + [r[1] for r in rows])
        return ("\n\n## 自社制作実績(全件・Vimeo公開・一次の会社実績)\n" + body[:6000]
                + f"\n※全{len(rows)}本。回答では『タイトル(公開日・尺) リンク』を使え(リンクだけの羅列は禁止)。"
                "スキルシート由来の有名作は個人メンバー経歴ゆえ会社実績と区別。")
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
    # 真実源 read_at で判定(Nibu action A 2026-07-15: get_messages が read_at/is_read を正しく返すよう是正済。
    # 暫定 local overlay は撤去し真実源一本に戻した)。相手発の未読(read_at無し)が1通でもあれば新着。
    return any(str(m.get("sender_id")) != str(uid) and not m.get("read_at") for m in msgs)


# 【暫定】開発時に注入されたseed/テストDMの判別(殿指摘2026-07-14: Scoreで見つからぬ幻DMを新着に出していた)。
# ★これは真実源(Calendar/Nibu)が is_seed フラグを提供するまでの"暫定フィルタ"。閾値/マーカーは腐る定数(鉄則八)ゆえ、
#   Nibu確認後は Calendar側の is_seed を読むだけに寄せる(判別を機構=真実源へ)。Fable指摘で "test"完全一致は撤去
#   (実在の人物が本当に「test」とだけDMした時に永久除外する誤除外=false-negativeを避ける)。
# 内容のテストマーカー: 自動テスト投稿(システム初期化文)・Casper自己疎通テスト・スタブ「test」。
_SEED_MARK_RE = re.compile(r"Task message thread initialized|Thread started\.|thread initialized|疎通テスト", re.I)
# テスト"口座"名のみ(Spec Admin)。★"User N"は撤去(Nibu確定2026-07-15: display名未設定の実在人物=Sato等も
# 「User 55」表示になり、実DMを誤除外していた=false-negative)。実在人物をテスト扱いしない。
_SEED_NAME_RE = re.compile(r"Spec\s*Admin", re.I)


def _is_seed_thread(t):
    """seed/テストDMスレッドか。真実源(Calendar/Nibu)が is_seed を返すならそれを優先。
    ★ID帯(thread_id>=10000000)での判別は撤去(Nibu確定2026-07-15: この帯は"3人以上の多人数DMの採番帯"であり
    本番業務DMが7本混在。ID帯除外は実在の多人数DMを幻として隠す誤り=Fableの警告した false-negative)。
    残す判別は"内容の"テストマーカー(システム初期化文/テスト名参加者)のみ=自動テスト/スタブ投稿を拾う。"""
    if isinstance(t.get("is_seed"), bool):                # Nibu が真実源で明示するなら機構を信じる(暫定regex不要)
        return t["is_seed"]
    if _SEED_MARK_RE.search(str(t.get("last_message") or "")):   # 「Task message thread initialized.」等=自動テスト投稿
        return True
    names = " ".join(str(p.get("name") or "") for p in (t.get("participants") or []))
    return bool(_SEED_NAME_RE.search(names))               # User N / Spec Admin 等=スタブ・テスト参加者


def _partition_dm_threads(threads, uid=None):
    """seed除外を"一本の経路"で(2箇所重複を排す・Fable鉄則五)。返り: (kept, seed_n)。
    除外件数は必ずログ(消した数を数えぬフィルタは次の機構の嘘になる・Fable)。"""
    kept, seed_n = [], 0
    for t in (threads or []):
        if _is_seed_thread(t):
            seed_n += 1
        else:
            kept.append(t)
    if seed_n:
        try:
            import attention as _a
            _a._alog(f"dm uid={uid}: seed除外{seed_n}件 / 残{len(kept)}件(暫定フィルタ・Nibu確認待ち)")
        except Exception:
            pass
    return kept, seed_n


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
        _allt, _seedn = _partition_dm_threads(data.get("threads") or [], uid)   # seed除外(一本の経路・件数ログ)
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


_DM_NOTIFY_STATE = os.path.join(HERE, "dm_notify_state.json")   # uid -> {thread_id: 最後に処理した updated_at}


def _dm_notify_check(uid):
    """新着DM(相手発の未読)を検知し、前回未通知の新規スレッドだけ返す(M3先回りのDM着信版)。
    read_at 真実源で判定。updated_at 差分で変化したスレッドだけ深掘り=Calendar負荷を抑える。
    初回は現状を"既知"として記録し push しない(起動時に既存backlogを一斉通知しない)。
    プライバシ: 返すのは差出人名と thread_id のみ・DM本文は state にも通知にも残さない。"""
    if not (WRITE_TOKEN and casper_mcp and uid):
        return []
    try:
        raw = casper_mcp.call_tool("get_messages", {"actor_id": int(uid)}, token=WRITE_TOKEN, actor=uid)
        threads = json.loads(raw).get("threads", []) if (raw or "").strip().startswith("{") else []
    except Exception:
        return []
    try:
        st = json.load(open(_DM_NOTIFY_STATE, encoding="utf-8"))
    except Exception:
        st = {}
    seen = st.get(str(uid), {})
    first = str(uid) not in st
    fresh, new_seen = [], {}
    for t in threads:
        if _is_seed_thread(t):
            continue
        tid = str(t.get("thread_id"))
        ts = str(t.get("updated_at") or "")
        new_seen[tid] = ts
        if first or seen.get(tid) == ts:              # 初回=既知扱い / 変化なし=深掘り不要(軽量スキップ)
            continue
        try:                                          # 変化あり: 相手発の未読か確認(自分の送信でtsが動いた場合を除外)
            r = casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(tid)},
                                     token=WRITE_TOKEN, actor=uid)
            msgs = json.loads(r).get("messages", []) if (r or "").strip().startswith("{") else []
        except Exception:
            new_seen[tid] = seen.get(tid, "")         # 確認失敗→ts進めず次回再試行
            continue
        if _thread_is_new(uid, msgs):                 # 相手発の未読あり=新着DM
            peers = [p for p in (t.get("participants") or []) if str(p.get("user_id")) != str(uid)]
            fresh.append({"id": tid, "peer": "、".join(str(p.get("name") or p.get("user_id")) for p in peers[:3])})
    st[str(uid)] = new_seen
    try:
        json.dump(st, open(_DM_NOTIFY_STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return fresh


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
    pdir = os.path.join(pack_paths.VAULT, "20_people")
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


_SELF_UIDS = {"101"}                                      # Casper 自身(system actor)。宛先候補から常に除く


def _resolve_persons(query, exclude=None, cap=5):
    """クエリ中の人物を**全員**解決。単数解決 _resolve_person を単一機構として使い、当たった表記を伏せて次を探す。
    実測2026-07-27(殿御指摘「DM内容が微妙」): 『kiyotomo、Tetsuoに確認するDM』で宛先が tetsuo 一名に落ちた。
    名指しされた者を黙って落とすのは、頼まれた仕事の半分を捨てることである。"""
    q = _URL_RE.sub(" ", query or "")
    out = []
    for _ in range(cap):
        uid, nm = _resolve_person(q, exclude=exclude)
        if not uid or any(u == uid for u, _n in out):
            break
        if str(uid) not in _SELF_UIDS:                    # 自分(Casper)は宛先に成り得ぬ。『casperが変更かける…』の
            out.append((uid, nm))                         #   "casper" を宛先と解き自分宛の下書きを立てた(実測)
        else:
            q = re.sub(re.escape(str(nm)), " ", q, flags=re.I) or q
            continue
        q2 = re.sub(re.escape(str(nm)), " ", q, flags=re.I)
        if q2 == q:                                       # 別名/読み経由の一致で表記を伏せられぬ=打ち切る
            break
        q = q2
    return out


def dm_writing_digest(query, table_md=""):
    """【宛先の目で書け】DM本文の作法を機構が課す。殿御指摘2026-07-27「DM内容が微妙」の芯:
    『先ほど整理した表について、どれが該当しますか』——相手はその表を見ておらぬ。殿との会話を
    相手の文脈と取り違えると、答えようのない問いを送りつけることになる。"""
    if not _DM_INTENT_RE.search(query or ""):
        return ""
    out = ("\n\n## 【DM本文の作法(機構が課す)】\n"
           "**宛先は殿とCasperの会話を一切見ておらぬ。**ゆえ本文で:\n"
           "① 『先ほど』『この表』『上記』等、こちらの会話に依る指示語を使うな。何の件かを名で述べよ。\n"
           "② **答えるに要る材料を本文に必ず載せよ**(候補の一覧・対象の名・現状の値)。"
           "材料なき問いは相手に返せぬ。\n"
           "③ 他の宛先へ相談を頼むな。各人に直接、同じ問いを立てよ(取り次ぎを依頼するのではない)。\n"
           "④ 答えを一案に絞って諮るな。判断を仰ぐ点を開いた形で問え。\n"
           "⑤ 何のために要るのか(この確定で何が前へ進むのか)を1文添えよ。")
    if table_md:
        out += ("\n**本文に載せるべき一覧はこれである(そのまま引き写せ・列を落とすな):**\n" + table_md)
    return out


def dm_recipients_digest(query):
    """名指しされた宛先が複数なら、機構がその全員を明示して渡す(一名に落とさせぬ)。"""
    if not _DM_INTENT_RE.search(query or ""):
        return ""
    ppl = _resolve_persons(query, exclude=None)
    if len(ppl) < 2:
        return ""
    return ("\n\n## 【DMの宛先＝機構が解決した全員】\n"
            + "、".join(f"{nm}(uid{uid})" for uid, nm in ppl)
            + f" の計{len(ppl)}名が名指しされておる。\n"
            "**一名に落とすな。各人へ1通ずつ**下書きを作れ(to_user_id を取り違えぬこと)。"
            "本文には殿が尋ねたい問いを具体的に書け——『データをお送りします』等の定型で済ませるな。"
            "問いが本文から消えたら、その下書きは用を成さぬ。\n"
            "**各人に個別に届くゆえ、本文に他の宛先の名を書くな**(『◯◯とも確認したい』は不要)。"
            "**答えを決めつけるな**——『◯◯のみでよろしいでしょうか』と一案に絞らず、"
            "こちらの分かっている前提を短く示した上で、開いた問いとして尋ねよ。")


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
            # 主語×PJ の交差(Q3): 「<PJ名>での寺島の進捗」型は、主語のそのPJ内割当を機構で確定し、
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
                "\n・別名(略称↔正式名等)を変えて再照会せよ。それでも無ければ『確認できた範囲では見当たらぬ』と"
                "留保付きで述べよ——**存在せぬファイル名を推測で書くな**。")
    except Exception:
        return ""


# cmd_510第2便(実害B機構化=retrieve-then-render・existence_digestと同型): DM読取意図turnで
# dm_threads(L3198)相当の決定的取得結果を注入する。BASE_SYSの【DM取扱い】(丙=服従命令)はFable
# 「丙=凍結せよ」に従い残す(消せば退行の恐れ)——本digestが甲(決定的機構)として先に材料を渡すことで、
# 丙は無害な重複指示になる(掟「服従でなく機構で強制」)。
#
# ゲート発火条件(二つの独立した経路・軍師戦略review point_b):
#   経路1(人物指定つき): roster閉集合に当たる人名 ∧ DM語(_DM_WORD_RE) ∧ 読取意図(_EXIST_Q_RE)
#   経路2(人物指定なし): DM語(_DM_WORD_RE) ∧ 読取意図(_EXIST_Q_RE・一覧要求)
# 読取意図は既存の_EXIST_Q_RE(存在確認ゲートの疑問形判定=「ある/見せて/来てる」等)を流用し新設しない
# ——DM読取「Timから来てると思うけど見せて」は構造的に存在確認「資料はある?」と同形の問いである。
# 人名は必ず_resolve_person経由(roster→_person_alias_index()の順で閉集合から引く・綴りを直書きしない)。
# rosterに無い名は経路1として解決させず経路2相当へ落ちる(cmd_508 AC2の出口検問=_guard_unrostered_person_claim
# に人物主語化を委ねる・既存機構の再利用)。
def dm_threads_digest(who, query):
    """【DM読取=retrieve-then-render】dm_threadsの決定的取得結果をDM読取turnへ注入。
    ★母集合ヘッダを機構が書く: 「DMスレッド全N件を照会。うち貴殿宛の1:1はM件。本日分はK件。」
    ★「届いておりません」は母集合を示した後でのみ言える構造(母集合なき不在断言の禁・
    Calendar/vault二軸で確立済の掟をDMにも適用)。
    ★3者以上のスレッドは暫定は含める方針(殿の御意向確定待ち・dashboard 0-v参照)だが、
    除外へ切り替える場合の構造(EXCLUDE_MULTIPARTY)を先に用意しておく。"""
    try:
        q = query or ""
        if not q or not _DM_WORD_RE.search(q) or not _EXIST_Q_RE.search(q):
            return ""
        if not (who.get("authed") and who.get("uid") and WRITE_TOKEN and casper_mcp):
            return ""
        uid = who.get("uid")
        # 経路1判定(参考記録のみ・母集合の絞り込みには使わない=1:1一覧はどの経路でも同じ全件を見せる)。
        target_uid, target_name = _resolve_person(q, exclude=uid)

        raw = casper_mcp.call_tool("get_messages", {"actor_id": int(uid)}, token=WRITE_TOKEN, actor=uid)
        data = json.loads(raw) if (raw or "").strip().startswith("{") else {}
        all_threads, seed_n = _partition_dm_threads(data.get("threads") or [], uid)
        total_n = len(all_threads)

        EXCLUDE_MULTIPARTY = False   # ★殿御意向確定まで暫定=含める(切替はここ一箇所)
        onetoone, multiparty = [], []
        for t in all_threads:
            peers = [p for p in (t.get("participants") or []) if str(p.get("user_id")) != str(uid)]
            (multiparty if len(peers) >= 2 else onetoone).append(t)
        shown = onetoone if EXCLUDE_MULTIPARTY else all_threads
        excluded_n = len(multiparty) if EXCLUDE_MULTIPARTY else 0

        today = datetime.date.today().isoformat()
        today_n = sum(1 for t in shown if str(t.get("updated_at") or "").startswith(today))

        import concurrent.futures as _cf

        def _chk(t):
            try:
                r = casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(t.get("thread_id"))},
                                         token=WRITE_TOKEN, actor=uid)
                md = json.loads(r) if (r or "").strip().startswith("{") else {}
                return (t, md.get("messages", []))
            except Exception:
                return (t, [])
        with _cf.ThreadPoolExecutor(max_workers=10) as _ex:
            checked = list(_ex.map(_chk, shown))

        header = (f"DMスレッド全{total_n}件を照会。うち貴殿宛の1:1は{len(onetoone)}件。本日分は{today_n}件。")
        if EXCLUDE_MULTIPARTY:
            header += f" 3者以上のスレッド{excluded_n}件は本一覧から除いた。"
        if seed_n:
            header += f"(うちseed/テスト投稿{seed_n}件は除外済)"

        rows = []
        for t, msgs in sorted(checked, key=lambda x: str(x[0].get("updated_at") or ""), reverse=True)[:20]:
            peers = [p for p in (t.get("participants") or []) if str(p.get("user_id")) != str(uid)]
            peer_names = "、".join(str(p.get("name") or p.get("user_id")) for p in peers[:3]) or "(自分)"
            newest = max(msgs, key=lambda m: str(m.get("created_at") or m.get("ts") or ""), default={})
            unread = _thread_is_new(uid, msgs)
            last = str(t.get("last_message") or (newest.get("body") or newest.get("content") or ""))[:80]
            ts = str(t.get("updated_at") or "")[:19]
            tag = "【未読】" if unread else ""
            rows.append(f"- {tag}{peer_names}（{ts}）: {last}")

        if not rows:
            return ("\n\n## 【DM読取=決定的照会結果(唯一の真実源)】\n" + header +
                    "\n上記の母集合の範囲内で、該当スレッドは0件であった。"
                    "\n・**この母集合(全" + str(total_n) + "件照会済)を示した上でのみ『届いておりません』と述べてよい**"
                    "——母集合を示さずに不在を断ずるな。")

        return ("\n\n## 【DM読取=決定的照会結果(唯一の真実源)】\n" + header + "\n"
                + "\n".join(rows) +
                "\n──\n・**上記のスレッド一覧だけが真実源**。ここに無いスレッドを推測で語るな。\n"
                "・本文の詳細を問われたら get_messages で当該スレッドを取得して答えよ(このヘッダの"
                "last行は要約であり全文ではない)。\n"
                "・**母集合を示した後でなければ『届いておりません』とは言えぬ**——実在するスレッドを"
                "『届いておりません』と断ずるな。")
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
        # 発火: 一般PJ語(_PROJ_Q_RE) or online PJ名を直接含む問い(『<PJ名>は今どうなってる?』等の個別PJ照会=
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
    PJ名→無関係な同音語 の幻覚展開を断つ。閉集合(解決済み実体のみ)ゆえ軽い。unique でなければ空。"""
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
                f"当社の実際の工程/職能: {'、'.join(roles)}。\n"
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


# 特定機材群/撮影の機材・ギアリストの問いを検知(殿指示2026-07-10)。機材語＋当該機材語/LED/撮影文脈のAND条件で発火。
_GEAR_Q_RE = re.compile(
    r"(ギアリスト|機材|機器|装置|セットアップ|(gear|equipment).?list|"
    r"(撮影|現場|設営|施工|セット).{0,6}(必要|準備|道具|もの|物))", re.I)


def gear_digest(query):
    """【特定機材群=retrieve-then-render(殿指示2026-07-10)】機材/ギアリストの問いに、ops_spatial_tech.md の
    機材節(デバイス/制御スペック＋技術スタック)を決定的に注入し、qwenが一般知識で製品名(Canon等)を上乗せするのを断つ。
    撮影機材はiPhone/insta360/depthであってCanon等ではない=vault記載に無い機材を足させない。機材問い＋当該機材文脈でなければ空。"""
    try:
        if not query or not _GEAR_Q_RE.search(query):
            return ""
        # Fable P2: 誤ドメイン注入回避——当該機材/空間演出系の固有文脈に限定(汎用の『撮影機材』に当該機材を真実源として被せない)
        try:
            import pack_config as _pc
            _spatial_triggers = _pc.get("domain_triggers", {}).get("spatial_tech", []) or []
        except Exception:
            _spatial_triggers = []
        _spatial_hit = bool(_spatial_triggers) and re.search(
            "(" + "|".join(str(w) for w in _spatial_triggers) + ")", query, re.I)
        if not (_spatial_hit or _pj_resolve(query)[0] == "unique"):
            return ""
        p = os.path.join(pack_paths.VAULT, "30_culture_rules", "ops_spatial_tech.md")
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
        return ("\n\n## 【特定機材群/空間演出 機材の真実源(vault: ops_spatial_tech.md・確定)】\n"
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
                "get_today_tasks(本日締切のみ)や特定PJ(<PJ名>等)に狭めず、全PJの進行中を示せ。"
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


# ── PJ名の表記ゆれ耐性(カタカナ⇄ローマ字): 「カタカナ表記」が正規名「ローマ字表記」と一致せず迷子になる綻びの汎用解 ──
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
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)  # ひらがな→カタカナ折り畳み(病五即効分・cmd_508): 「まるこめ」がカナ/ローマ字と同一skeletonへ落ちぬ穴を塞ぐ
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


def _pj_name_hit(name, text):
    """PJ名がテキストに『実体として』現れたか(単一機構)。素の `in` は ASCII短名が別語に埋没して
    誤爆する: Calendar には実在PJ 'end'(id77) があり `'end' in 'Calendar'` は True——これが
    「**end** には Calendar上 1件」なる別PJへの摩り替えを生んだ(殿ログ2026-07-27 16:37 実害)。
    ASCII名は語境界を要求し、日本語名は境界概念が無いゆえ素の包含で可(名が長く弁別的)。"""
    nm = (name or "").strip()
    if not nm or not text:
        return False
    if nm.isascii():
        if len(nm) < 3:                                   # 'V'/'GS' 級は本文照合の材料にせぬ(誤爆しか生まぬ)
            return False
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(nm)}(?![A-Za-z0-9])", text) is not None
    return nm in text


def _pj_resolve(query):
    """クエリから online PJ を解決(Fable 3値)。返り (status, names, path)。
    status='unique'|'ambiguous'|'none'。閉集合(online PJ)照合ゆえ names は真実源の部分集合(構成上保証)。"""
    q = query or ""
    idx = _pj_index()["idx"]
    qcan = _canonical(q)
    hits = []
    for can, names in idx.items():
        # 生一致は語境界つき(_pj_name_hit)。スケルトン一致は4字以上に限る——3字ASCII名(end/RND/TFT/BLG)は
        # 正準空間で境界が消え 'calendar' 等に埋没するゆえ、生一致(語境界)でのみ拾う。
        if any(_pj_name_hit(nm, q) for nm in names) or (len(can) >= 4 and can in qcan):
            hits += names
    hits = list(dict.fromkeys(hits))
    if not hits:
        return ("none", [], None)
    exact = [nm for nm in hits if _pj_name_hit(nm, q)]   # 決定則: 完全(生)一致 > 部分/スケルトン一致
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
                         r"(見せ|教え|一覧|表示|出し).{0,6}タスク|どんな.{0,4}タスク|"
                         # 『〜のタスクは？』= 最も自然な言い回しが従来どこにも掛からず、機構(表/選択カード)を
                         # 素通りして弱qwenの作文に落ちていた(実測2026-07-27: 3件在るPJを「0件」と作文)。
                         r"タスク(は|って|とは)[^。]{0,8}[？?]\s*$|タスク.{0,6}(何|なに|どれ)", re.I)

# 人物の手持ちを問う意図(『Timは今なにしてる？』『鈴木の担当タスク』『ouは忙しい？』)。
# 実際に人物が解決できた時のみ表を描くゆえ、ここは意図の検出に徹する(人の同定は _resolve_person が正)。
_PERSON_WORK_RE = re.compile(
    r"(何|なに|なん)(を)?(して|やって)(る|いる|ます|んの|の)|"                  # 今なにしてる/何やってますか
    r"(手持ち|抱えて|持って(る|いる)|担当(して(る|いる))?)|"                     # 手持ち/抱えている/担当
    r"(稼働|忙し|空いて(る|いる)|余裕|暇)|"                                      # 稼働状況/忙しい/空いている
    r"の(タスク|案件|仕事|予定|スケジュール|状況|進捗)", re.I)
_PERSON_COLS = ["カット", "タスク", "プロジェクト", "工程", "状態", "期限", "納期"]   # 0件時と一覧時で同一(列の食い違いを断つ)

# 存在否定の出口検問(後段)が使う二つの述語。module級に置くのは検査可能にするため(掟: 緑ゲートに嘘を映す)。
# ① 存在そのものの否定 = 機構が実データで差し止める対象。
_NEG_EXIST_RE = re.compile(r"登録され(ていません|ておりません)|1件も(無|な)い|存在しません|見当たりません|"
                           r"(タスク|task)[^。]{0,16}(ありません|ございません)")
# ② 部分集合への限定 = その否定は真でありうる(『未着手のタスクはありません』はmk=0なら正しい)。
#    ①だけで撃つと、正しい文に「全49件ある」と的外れな訂正を付す(実測2026-07-27・あるPJで発生)。
_NEG_SCOPE_RE = re.compile(r"未完了|未着手|進行中|作業中|残務|確認待ち|承認待ち|レビュー中|"
                           r"遅延|超過|本日|今日|今週|期限切れ|wip|mk|qc", re.I)


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


# 固有名詞でない常用語(これらは資料に必ず在るゆえ、除かねば「『タスク』はCalendarに無い」なる戯言を注入する)。
_NAME_STOP = {"タスク", "プロジェクト", "スケジュール", "カレンダー", "ステータス", "メンバー", "アサイン",
              "チェック", "データ", "ファイル", "リスト", "コメント", "レビュー", "フィードバック",
              "リテイク", "カット", "シーン", "ショット", "クライアント", "スタッフ", "ミーティング",
              "task", "tasks", "project", "projects", "calendar", "status", "aurora", "casper", "vault"}


def _name_tokens(query):
    """クエリ中の『名らしき』生トークン(カタカナ3字以上 / ASCII4字以上)。正準化せぬ=コーパス字面照合に使うゆえ。
    常用語は除く——固有名詞かどうかの判定であって、単に長い語を拾う話ではない。"""
    toks = [t for t in re.findall(r"[゠-ヿ]{3,}|[A-Za-z][A-Za-z0-9]{3,}", query or "")
            if t.lower() not in _NAME_STOP and t not in _NAME_STOP]
    return list(dict.fromkeys(toks))[:6]


_CORPUS_NAME_MEMO = {}                                    # tok(lower) -> [出所]。索引はプロセス寿命ゆえ結果も不変


def _corpus_name_hits(tok, k=3):
    """その名が資料/議事録に実在するか＋出所。RAGと同一コーパス(casper_rag の索引)を引く=母集合その二。
    判定は字面の実在であって類似ではない(推測で『在る』と言わせぬ)。"""
    if not casper_rag or not tok or len(tok) < 3:
        return []
    if tok.lower() in _CORPUS_NAME_MEMO:                  # 全走査は1トークン約26ms=連問で効くゆえ記憶する
        return _CORPUS_NAME_MEMO[tok.lower()]
    try:
        if getattr(casper_rag, "_CACHE", None) is None:
            casper_rag.candidates(tok, n=1)                   # 索引ロードは既存機構に委ねる(二重実装せぬ)
        low, out = tok.lower(), []
        for e in (casper_rag._CACHE or []):
            if low in str(e.get("t", "")).lower() or low in str(e.get("title", "")).lower():
                ttl = e.get("title") or os.path.basename(str(e.get("src") or "")) or "資料"
                if ttl not in out:
                    out.append(ttl)
                if len(out) >= k:
                    break
        _CORPUS_NAME_MEMO[tok.lower()] = out
        return out
    except Exception:
        return []                                         # 走査失敗は記憶せぬ(次回やり直す・空を確定と誤らせぬ)


def _corpus_only_name(query):
    """【Calendar不在 ≠ 全体不在(殿御指摘2026-07-27「Auroraを探してほしかった」)】
    Calendar のPJ名には無いが、資料/議事録には在る名を検出。返り (tok, [出所]) or None。
    母集合を二つ持つ以上、片方の不在で「無い」と名乗ってはならぬ——16:35『そのプロジェクト名に一致が
    ございませぬ』で断ち切った Solafune は、議事録には確かに在った。"""
    if _pj_resolve(query)[0] == "unique":
        return None
    _pj_names = [nm for names in _pj_index()["idx"].values() for nm in names]
    for tok in _name_tokens(query):
        if _pj_resolve(tok)[0] != "none":                     # Calendar に解ける名は対象外(PJ経路が正)
            continue
        # PJ名の一部をなす語は「Calendar に無し」ではない(『ドローン』⊂『ドローン R&D  GS/<略称>連携』)。
        # ここを見落とすと、在るものを無いと告げる注記を自ら注入することになる。
        if any(tok.lower() in nm.lower() or _canonical(tok) in _canonical(nm) for nm in _pj_names):
            continue
        srcs = _corpus_name_hits(tok)
        if srcs:
            return (tok, srcs)
    return None


def _corpus_only_note(query):
    """上の検出を弱qwen向けの拘束文へ。Calendar上のタスク/担当を語らせず、資料の内容は語らせる。"""
    got = _corpus_only_name(query)
    if not got:
        return ""
    tok, srcs = got
    return ("\n\n## 【名の所在: Calendar に無し / 資料に在り】\n"
            f"「{tok}」は Calendar のプロジェクト名には**存在しない**(閉集合照合で確認)。"
            f"一方、資料/議事録には**在る**(出所: {' / '.join(srcs)})。\n"
            f"**Calendar 上のタスク・担当・進捗・納期を「{tok}」の名で語ってはならぬ**(在ると答えれば捏造)。"
            "資料に基づく内容(経緯・論点・決定事項・関係先)は答えてよい。"
            "答える際は必ず『Calendar には案件登録が無い(ゆえにタスク/担当は未登録)』旨を添えよ。"
            "関連しそうな Calendar PJ が別名で在るなら、断定せず『〜かもしれぬ』と候補として示せ。")


_MATERIAL_MIN_CHARS = int(os.environ.get("CASPER_MATERIAL_MIN_CHARS", "300"))
_MATERIAL_MIN_LINES = 5
_MATERIAL_MIN_STRUCT = 3
# 構造のある行(番号見出し / 箇条書き / 「見出し: 値」)。文章の羅列と資料を分ける印。
_MATERIAL_STRUCT_RE = re.compile(
    r"^\s*(?:[0-9０-９]+\s*[.．、)）]|[-・*＊●○■□▪]|#{1,6}\s|[^\s:：]{1,24}\s*[:：]\s*\S)")
# 依頼・問いの印。一つでも在れば「材料の投げ入れ」でなく「頼み事」ゆえ通常経路へ返す。
_MATERIAL_REQUEST_RE = re.compile(
    r"(教え|説明し|まとめ|要約|アップ|保存|登録|起票|送っ|送信|共有して|直し|修正し|消して|削除|"
    r"作っ|作成し|して下さい|してください|してくれ|お願い|頂けま|いただけま|"
    r"どう|なぜ|何故|いつ|誰|どこ|どれ|どの|ですか|ますか|でしょうか|[？?])")


def pasted_material(text):
    r"""【殿御下命2026-08-26】長い資料をそのまま貼っただけの発話を『材料』と判ずる。

    実害(2026-08-26 18:26〜18:29): kiyotomo殿は SORAFUNE の議事録本文を**四度**貼った。
    そのたび Casper は問いとして読み、PJ状況の要約や逆インタビューを返した——
    貼られた本文には一言も触れずに。殿は噛み合わぬまま貼り直しを繰り返し、
    最後は .rtf を投げて諦められた。

    ★『貼っただけ』は問いではない。材料の投げ入れである。
      何をするかは**人が決める**——機構が勝手に要約や起票へ倒すのではなく、
      受け取った事実と選択肢を返す。
    ★返す中身は数え上げた事実のみ(行数/字数/見出し)。中身の解釈はここでは一切せぬ
      (retrieve-then-render: 憶測の入る余地を作らぬ)。

    戻り値: {"lines":int,"chars":int,"heads":[str,...]} / 材料でなければ None
    """
    t = (text or "").strip()
    if len(t) < _MATERIAL_MIN_CHARS:
        return None
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) < _MATERIAL_MIN_LINES:
        return None
    if len([ln for ln in lines if _MATERIAL_STRUCT_RE.match(ln)]) < _MATERIAL_MIN_STRUCT:
        return None
    if _MATERIAL_REQUEST_RE.search(t):
        return None                                  # 頼み事が混じる=材料でなく依頼。通常経路へ返す
    heads = [ln for ln in lines
             if re.match(r"^\s*(?:[0-9０-９]+\s*[.．、)）]|#{1,6}\s)", ln)][:6]
    if not heads:
        heads = lines[:1]
    # 文書の題= 構造行でない先頭行(「SORAFUNE 様　MTG 議事録」)。
    # ★見出し(「1. シナリオ・コンセプト」)を題に流用せぬ——実測でそれが既定に立ち、
    #   資料の名が中の一節になってしまった。題は文書が自ら名乗っている行から採る。
    first = lines[0]
    title_line = "" if _MATERIAL_STRUCT_RE.match(first) else first[:80]
    return {"lines": len(lines), "chars": len(t), "heads": [h[:40] for h in heads],
            "title_line": title_line}


def material_choices(mat, filename=""):
    """材料を受け取った時の返し(決定的・qwen非経由)。事実＋選択肢のカード。"""
    what = (filename or mat.get("title_line")
            or (mat["heads"][0] if mat["heads"] else "貼られた文書"))
    body = ["📄 資料として受け取り申した。**まだ何もしておりませぬ**——何にいたしましょう。", "",
            f"- 行数: {mat['lines']}行 / {mat['chars']}字"]
    if mat["heads"]:
        body.append("- 見出し: " + " / ".join(mat["heads"]))
    prompt = "この資料をどういたしましょう。"
    opts = [
        {"id": "mat_aurora", "label": "📖 Aurora に保存",
         "preview": "共有ノート図書館へ新しい資料として起票(承認カードが出まする)",
         "say": f"いま貼った文書を Aurora に新規保存して。題は「{what}」。本文は貼った通りに。"},
        {"id": "mat_replace", "label": "✏️ 前の資料を差し替え",
         "preview": "直前に扱っていた Aurora 資料の中身を、この本文で入れ替える",
         "say": "いま貼った文書で、直前に扱っていた Aurora 資料を差し替えて。本文は貼った通りに全文。"},
        {"id": "mat_summary", "label": "📝 要点を整理",
         "preview": "この本文だけを材料に要約する(社内データは混ぜぬ)",
         "say": "いま貼った文書の要点だけを整理して。貼った本文以外の情報は混ぜないで。"},
        {"id": "mat_none", "label": "🤝 共有まで(何もせぬ)",
         "preview": "受け取るだけ。以後の会話の前提として覚えておく",
         "say": "いま貼った文書は共有まででよい。何もしないで、以後の前提として覚えておいて。"},
    ]
    return "\n".join(body), {"prompt": prompt, "options": opts}


def _pj_task_choices(query):
    """特定PJのタスク要求だが名前解決が unique でない時、候補PJを選択カードで提示(無言None落ちさせない・3値のnone/ambiguous出口)。
    cmd_508第3便(病三・B02是正): 「名前らしきトークン」から依頼語彙(_PJ_TASK_RE/_STATUS_Q_RE)を差し引く。
    依頼語彙自体がカタカナ(『タスク』『ステータス』)ゆえ、差し引かねば発話者が一度も口にしていないPJ名を
    誤って想定し、的外れな『名前不一致』を宣告してしまう(実測B02)。残余ゼロ=引数なしの分岐を新設。"""
    if not _PJ_TASK_RE.search(query or ""):
        return None
    st, names, _ = _pj_resolve(query)
    if st == "unique":
        return None                                       # unique は table_card が拾う
    if st == "ambiguous":
        cands, prompt = names, "どのプロジェクトのタスクでしょう？下から選んでくだされ。"
    else:                                                 # none: 名前らしきトークンがある時のみ近傍候補
        if not _pj_name_like_residual(query):
            return None                                   # 残余ゼロ=「引数なし」(依頼語彙のみで名前トークンが無い)。
                                                            # ★『名前不一致』の文言は名前トークンが実在する時にしか出さぬ
                                                            # (brief要件)ゆえ、ここは選択カードを出さず _table_card の
                                                            # block⓪(anchorのPJ→_active_tasks_table_c)へ委ねる。
        if _corpus_only_name(query):
            return None                                   # Calendarに無くとも資料に在る名→選択カードで断ち切らず
        cands = _pj_near_candidates(query)                #   通常経路へ落とす(呼び側が _corpus_only_note を注入)
        if not cands:
            return None
        prompt = "そのプロジェクト名に一致がございませぬ。もしやこちらでは？（違えば具体名で仰せを）"
    opts = [{"id": f"pjtask_{nm}", "label": f"{nm} のタスク", "preview": f"{nm} の未完了タスク一覧を表示",
             "say": f"{nm}のタスクを見せて"} for nm in cands[:6]]
    return {"prompt": prompt, "options": opts}


def _pj_name_like_residual(query):
    """『名前らしきトークン』のうち、依頼語彙(_PJ_TASK_RE/_STATUS_Q_RE)の一致範囲に含まれる文字を
    差し引いた残余を返す(stoplist手書き禁止・単一ソース導出)。残余が空=名前トークンは実在しない
    (依頼語彙自体がカタカナで名前らしく見えていただけ)。"""
    q = query or ""
    _covered = set()
    for _re in (_PJ_TASK_RE, _STATUS_Q_RE):
        for m in _re.finditer(q):
            _covered.update(range(m.start(), m.end()))
    out = []
    for m in re.finditer(r"[゠-ヿA-Za-z]{2,}", q):
        if not any(i in _covered for i in range(m.start(), m.end())):
            out.append(m.group(0))
    return out


# ④ table card(Fable設計): 表は機構が真実源からテンプレ描画=LLMは表を書かず継ぎ目の修辞だけ。
# 截ち切れ・転写捏造・全件ダンプが構造的に消える。切り口(並べ替え)はクライアントのチップで(LLM再呼出不要)。
_PROJ_LIST_RE = re.compile(r"(動いて(る|いる)|進行中|稼働中|全.{0,2}(プロジェクト|PJ|案件)|"
                           r"(プロジェクト|PJ|案件).{0,8}(一覧|教え|どれ|ある|全部)|"
                           r"(納期|締切|遅れ|遅延|超過).{0,10}(プロジェクト|PJ|案件|一覧|もの|の))", re.I)

# 停滞FB/確認の"一覧"意図(通知の『停滞FB N件』の実体を見せる)。進捗の真実源はCalendar(vault/legacyでなく)。
# 【二軸classifier】status/進捗(現況=Calendar排他)の問いを検出→vault抑制。知識/文脈の問いはvault許容(現・過去とも)。
# status語彙: 進捗/状態/どうなってる/停滞/確認待ち/納期超過/締切/残り/件数/アサイン状況/タスクの有無・一覧。
_STATUS_Q_RE = re.compile(
    r"進捗|ステータス|状態|どうな(って|ってる|る)|状況|"
    r"停滞|滞留|確認待ち|止ま(って|った)|動いて(い)?(ない|ません)|"
    r"納期|締切|〆|超過|遅延|遅れ|オンスケ|on.?schedule|"
    r"(タスク|作業|task).{0,10}(は\?|ある|残|何件|一覧|教え|見せ|どれ|進|担当|状況|やること)|"
    r"(残り|あと|未(完|着手)).{0,6}(タスク|作業|は|何)|何件|残件|件数|"
    r"(担当|アサイン).{0,8}(状況|一覧|は\?|誰|空き|負荷)", re.I)

# cmd_501: 社内ツールが既にscopeを持つ検索(自社Vimeoライブラリ等)を、外部Web検索の発火判定から除外する。
# 「Vimeoの動画を探して」は"外の事を尋ねる形"に見えるが、実際は既存vimeo_search tool(qwen呼出)が担うべき
# 内部検索であり、外部Web検索(casper_web)が横取りすべきでない(実測2026-08-07で誤発火を確認)。
# ★cmd_508病五: 手書き語彙(vimeo等)から道具台帳(casper_tool_ledger)の名詞導出へ変更。
# 台帳未読込(import失敗)時のみ旧来の固定語彙にfail-soft(挙動を壊さぬ)。
def _build_internal_tool_scope_re():
    try:
        vocab = casper_tool_ledger.self_scoped_search_vocab() if casper_tool_ledger else set()
    except Exception:
        vocab = set()
    if not vocab:
        vocab = {"vimeo", "ヴィメオ", "ビメオ"}                     # fail-soft: 台帳不在時の既定挙動を維持
    return re.compile("(" + "|".join(re.escape(w) for w in sorted(vocab)) + ")", re.I)


_INTERNAL_TOOL_SCOPE_RE = _build_internal_tool_scope_re()

_STALL_LIST_RE = re.compile(
    r"(停滞|滞留|止ま(って|った)|溜ま(って|った)|たまって).{0,8}(FB|ＦＢ|確認|チェック|検収|レビュー)|"
    r"(FB|ＦＢ|確認|チェック|検収|レビュー).{0,8}(停滞|滞留|止ま|溜ま|たまって)|"
    r"確認待ち.{0,12}(動いて|停滞|まま)|(動いて(い)?ま?せ|動いていない).{0,10}確認待ち|"   # 「確認待ちのまま動いていない」(通知の文言・殿指摘2026-07-14)
    r"停滞.{0,6}\d+\s*件", re.I)


# M4 Phase1: アサイン提案の意図（担当未定の割り当てを促す発話）。status一覧(_STATUS…)とは別軸=「誰に振る」。
_ASSIGN_RE = re.compile(
    r"(アサイン|担当決|担当付|担当割|割り?当|割り?振|振り?分)|"
    r"誰に(振|割|任|やらせ|お願い|頼|アサイン)|"
    r"担当(者)?(を|が|は)?\s*(未定|決|付|振|割|いない|空)|"
    r"(未アサイン|担当未定|手が?空|空きスロット|担当不在)", re.I)


# 「自分の」を明示する語（lead+ でも本人アサイン表に振り向ける）。
_MY_RE = re.compile(r"(私|わたし|自分|僕|俺|わし|マイ|自身|me\b)", re.I)


def _my_tasks_table(who):
    """本人に割り当てられた未完タスクの表（接地・LLM非経由）。返り (table_or_None, prose)。
    0件は幻覚でなく明示メッセージ（失敗とゼロを別出口・Fable）。作業者の「アサインある？」の正しい答え。"""
    uid = str(who.get("uid") or "")
    try:
        import casper_notify as _n
        ts = _n._all_tasks()
    except Exception:
        return None, "ただ今タスクを読めませなんだ（時間をおいて再度お尋ねを）。"
    mine = [t for t in ts if str(t.get("assigned_to")) == uid
            and str(t.get("status") or "").lower() not in ("deliver", "omit")]
    if not mine:
        return None, "ただ今、あなた宛にアサインされた（未完の）タスクはございませぬ。"
    try:
        pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        pjn = {}
    mine.sort(key=lambda t: str(t.get("due_date") or "9999"))
    rows = [[((t.get("shotID") or "") + " " + (t.get("name") or "")).strip(),
             pjn.get(str(t.get("project_id"))) or ("PJ" + str(t.get("project_id"))),
             str(t.get("status_label") or t.get("status") or ""),
             str(t.get("due_date") or "")[:10]] for t in mine]
    return ({"title": f"あなたのアサイン（{len(mine)}件）", "columns": ["タスク", "PJ", "状態", "締切"], "rows": rows},
            f"あなた宛のアサインは {len(mine)}件にござる。締切の近い順に下表の通り。")


def _assign_card(query, who):
    """「アサイン待ち/未割当/アサイン提案」意図→アサイン待ちスロット＋実績候補を機構でカード化（純機構・LLM非経由）。
    返り=assign dict or None。
    **閲覧は誰でも可（＝どのタスクが担当未定か、は情報）／割り当て実行だけ権限層(tier≥lead∧audience)で制御。**
    各スロットに can_act（この閲覧者が実際に割り当てられるか）を付す＝作業者は見えるが押せない（誤操作でなく情報提供）。
    ※「私の」を明示した発話は本人アサイン表へ回すのでここでは出さない。"""
    if query and _MY_RE.search(query):
        return None
    if not (casper_assign and casper_authority and _ASSIGN_RE.search(query or "")):
        return None
    uid = str(who.get("uid") or "")
    snap = casper_authority._load_snapshot()
    tier = casper_authority.tier_of(uid, snap)
    try:
        import casper_notify as _n
        tasks = _n._all_tasks()
    except Exception:
        return None
    names = dict(_ROSTER_MAP)
    props = casper_assign.proposals(tasks, names, snap)   # 全 open slots（閲覧は全員可）
    if not props:
        return None
    try:                                               # project_id→名前(画面で「PJ95」でなく実名を出す)
        _pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        _pjn = {}
    for p in props:
        p["project"] = _pjn.get(str(p.get("project_id"))) or ("PJ" + str(p.get("project_id")))
        p["can_act"] = (tier == "admin") or (uid in (p.get("audience") or []))   # 割当実行できるか(閲覧≠実行)
    return {"slots": props[:20], "total": len(props),
            "can_act": any(p["can_act"] for p in props), "viewer_tier": tier}


# M4 Phase2: 日程変更(reschedule)の意図。締切/納期/日程 系＋動かす動詞、または「N日 延ばす/前倒し」。
_RESCHED_RE = re.compile(
    r"(締切|納期|期日|〆切|しめきり|日程|スケジュール|due|デッドライン)[^。]{0,14}"
    r"(変更|変え|延ば|延長|前倒し|繰り上げ|繰り下げ|ずらし|ずらす|遅らせ|早め|伸ば|後ろ倒し|短縮|縮め)|"
    r"(\d+\s*(?:日|週間?|ヶ月|か月))[^。]{0,6}(延ば|延長|前倒し|早め|遅らせ|ずらし|ずらす|伸ば)", re.I)


def _resolve_target_task(query, tasks):
    """query から対象タスクを解決。返り (task or None, 候補list)。優先: #id/タスクN → shotID → name部分一致。"""
    q = query or ""
    m = re.search(r"(?:#|task|タスク)\s*(\d{2,})", q, re.I)
    if m:
        t = next((t for t in tasks if str(t.get("id")) == m.group(1)), None)
        if t:
            return t, [t]
    hits = []
    for t in tasks:                                    # shotID 一致（3字以上・誤爆防止）
        sh = str(t.get("shotID") or t.get("shot_id") or "").strip()
        if sh and len(sh) >= 3 and sh.lower() in q.lower():
            hits.append(t)
    if not hits:
        for t in tasks:                                # name 部分一致（3字以上）
            nm = str(t.get("name") or "").strip()
            if len(nm) >= 3 and nm in q:
                hits.append(t)
    active = [t for t in hits if str(t.get("status") or "").lower() not in ("deliver", "omit")]
    hits = active or hits
    if len(hits) == 1:
        return hits[0], hits
    return None, hits[:8]


def _reschedule_card(query, who):
    """「◯◯の締切を△△に」→対象タスク＋新due＋影響プレビューを機構でカード化（純機構・LLM非経由）。
    返り: {"card":preview} / {"clarify":聞き返し文} / None。解決不能はqwenに委ねず聞き返す。"""
    if not (casper_reschedule and casper_authority and _RESCHED_RE.search(query or "")):
        return None
    uid = str(who.get("uid") or "")
    snap = casper_authority._load_snapshot()
    try:
        import casper_notify as _n
        tasks = _n._all_tasks()
    except Exception:
        return None
    target, cands = _resolve_target_task(query, tasks)
    if not target:
        if cands:
            nm = "、".join((str(t.get("shotID") or "") + " " + str(t.get("name") or "")).strip() + f"(#{t.get('id')})" for t in cands[:6])
            return {"clarify": f"どのタスクの日程を変えまするか？候補: {nm}。タスク名・shotコード・#番号でお指しくだされ。"}
        return {"clarify": "どのタスクの日程を変えまするか？ タスク名かshotコードでお示しくだされ。"}
    new_due, err = casper_reschedule.resolve_new_due(target.get("due_date"), query)
    if err:
        return {"clarify": f"「{target.get('name')}」をいつに変えまするか？ 例: 8月5日 ／ 3日延ばす ／ 来週。"}
    try:
        _pjs = {str(p.get("id")): p for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        _pjs = {}
    proj = _pjs.get(str(target.get("project_id")))
    pv = casper_reschedule.preview(target, tasks, new_due, proj)
    tgt = {"project_id": target.get("project_id"), "assignee": str(target.get("assigned_to") or "")}
    pv["project"] = (proj or {}).get("name") or ("PJ" + str(target.get("project_id")))
    pv["status"] = str(target.get("status") or "")
    pv["can_act"] = casper_authority.allowed("reschedule", uid, tgt,
                                             from_status=str(target.get("status") or "").lower(), snap=snap)[0]
    return {"card": pv}


def _resched_prose(pv):
    """reschedule カードに添える機構散文（LLM非経由）。"""
    d = pv.get("delta_days")
    dirw = "後ろ倒し" if (d or 0) > 0 else ("前倒し" if (d or 0) < 0 else "変更")
    head = (f"「{(pv.get('shot') or '').strip()} {pv.get('task_name')}」の締切を "
            f"{pv.get('old_due')} → {pv.get('new_due')}"
            f"（{abs(d) if d is not None else '?'}日{dirw}）に。")
    warn = any("⚠" in n for n in pv.get("notes", []))
    if pv.get("can_act"):
        return head + ("下記の影響にご注意の上、『この日程で確定』を押してくだされ。" if warn
                       else "影響をご確認の上『この日程で確定』を押してくだされ。")
    return head + "（日程変更の実行はご本人・PM／リード以上が行えまする。影響は下記の通り。）"


# M4 Phase2': MTG助言の意図。会議前議題（この際これも確認）／そろそろ定例。
_MTG_AGENDA_RE = re.compile(
    r"(会議|MTG|ミーティング|打ち?合わせ|定例)[^。]{0,10}(議題|アジェンダ|論点|確認すること|話すこと|準備)|"
    r"次の?(会議|MTG|定例|打ち?合わせ)[^。]{0,6}(議題|何|準備|確認)|この際[^。]{0,6}確認", re.I)
_MTG_DUE_RE = re.compile(
    r"(そろそろ|久しく|しばらく|間隔|頃合)[^。]{0,10}(会議|MTG|定例|ミーティング|打ち?合わせ)|"
    r"(会議|定例|MTG|ミーティング)[^。]{0,10}(そろそろ|開いた方|やった方|時期|頃合|久しく|開けて|やってな)", re.I)


def _meeting_advisory(query, who):
    """MTG助言（読取のみ・LLM非経由）。会議前議題 or そろそろ定例 を機構で。返り {prose, table?} or None。"""
    q = query or ""
    is_agenda = bool(_MTG_AGENDA_RE.search(q))
    is_due = bool(_MTG_DUE_RE.search(q))
    if not (casper_meeting and casper_tools and (is_agenda or is_due)):
        return None
    now = datetime.datetime.now()
    today = now.date().isoformat()
    try:
        events = casper_tools._get("/events?limit=300").get("items", [])
    except Exception:
        return None
    try:
        import casper_notify as _n
        tasks = _n._all_tasks()
    except Exception:
        tasks = []
    try:
        pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        pjn = {}
    if is_due:                                        # 「そろそろ定例」
        due = casper_meeting.meetings_due(events, tasks, now)
        if not due:
            return {"prose": "ただ今、会議間隔から見て『そろそろ定例を』と申し上げるべきPJはございませぬ。"}
        rows = [[pjn.get(d["project_id"], "PJ" + d["project_id"]), f"{d['elapsed']}日前", f"約{d['median']}日", d["last"]] for d in due]
        return {"prose": f"会議間隔から見て、そろそろ定例を開く頃合いのPJが {len(due)}件ござる。下表の通り。",
                "table": {"title": f"そろそろ定例の頃合い（{len(due)}PJ）", "columns": ["PJ", "前回開催から", "通常間隔", "前回開催日"], "rows": rows}}
    # 会議前議題
    up = casper_meeting.upcoming_meetings(events, now)
    if not up:
        return {"prose": "直近48時間に予定された会議はございませぬ。"}
    mtg = up[0]
    title = mtg.get("title") or "会議"
    mname = pjn.get(str(mtg.get("project_id")), "")
    last = casper_meeting.last_meeting_before(events, mtg.get("project_id"), casper_meeting._dt(mtg.get("start_time")))
    ag = casper_meeting.agenda_for(mtg, tasks, today, last_meeting_dt=last)
    if not ag:
        return {"prose": f"次の会議「{title}」{('（' + mname + '）') if mname else ''}に向けての積み残しはございませぬ。"}
    rows = [[a["name"], "・".join(a["reasons"]), (_uid_to_name(a["assigned_to"]) if a["assigned_to"] else "—"), a["due"] or "—"] for a in ag]
    return {"prose": f"次の会議「{title}」{('（' + mname + '）') if mname else ''}に向け、この際これも確認しておきたい点が {len(ag)}件ござる。",
            "table": {"title": f"「{title}」の議題候補（{len(ag)}件）", "columns": ["タスク", "理由", "担当", "締切"], "rows": rows}}


# M4 Phase4: status更新 verb の意図（納品/客先承認/対象外）。
_STATUS_VERB_RE = [
    ("mark_delivered", re.compile(r"納品|デリバ|納めた|納品済|deliver", re.I)),
    ("record_client_approval", re.compile(r"客先承認|クライアント承認|顧客承認|client[^。]{0,4}承認|クライアントOK|客先OK|client_ap", re.I)),
    ("omit_task", re.compile(r"対象外|除外|omit|取り下げ|取りやめ|見送りに(する|し)|やらないこと", re.I)),
]

# 【宣言・定義・引用は命令ではない】status語彙が複数並ぶ本文は「定義表/合意事項の共有」であって
# 「このタスクを納品せよ」ではない。実測2026-07-27 19:05: 殿が9値の定義表を示されたのを
# 'DELIVER' の一語で拾い『どのタスクを「納品」しまするか？候補: …』と問い返した(殿御指摘の一件)。
# 殿は2026-07-23 にも同じ誤読を指摘済(corrections 9b31db89)。語一つで動詞を起こすのを機構で止める。
# 語境界は ASCII のみで見る。\b は日本語の直前後で立たぬ——'AP提出後' の 提 は \w ゆえ \bap\b が落ち、
# 殿の『WT と OMIT は超過カウントしない。AP提出後でも…QCFBに代わる』が語彙2種と数えられ宣言と判じられなかった
# (実測2026-07-27 19:20)。表記ゆれ(qcfb/clientap)も同じ語として数える。
_STATUS_VOCAB_RE = re.compile(r"(?<![A-Za-z0-9])(wt|mk|wip|qc|qc_fb|qcfb|ap|client_ap|clientap|deliver|omit)"
                              r"(?![A-Za-z0-9])", re.I)
_DECLARATIVE_RE = re.compile(r"確定(した|しました|事項)|以下に(確定|定義|決定)|定義(は|を|する)|"
                             r"ルール|規定|仕様|に変更します|の資料|資料を確認|会議を行い|議事録|"
                             # 規則を述べる言い回し(命令形でない断定)。『〜しない』『〜に代わる』『〜扱い』。
                             r"カウントしない|数えない|対象外とする|扱いとする|扱いです|"
                             r"に(代|変)わ(る|ります)|とする$|である", re.I)
# 動作を頼む標識。これが無い status 動詞は「規則の記述」であって「実行の依頼」ではない。
#   ※『て』の後に進行/過去が続くものは依頼でない(『今なにしてるの？』の "にして" を拾わぬため、
#     各分岐に同じ否認先読みを掛ける)。_ACT_NOT = 進行形・過去形の語尾。
_ACT_NOT = r"(?!(る|いる|います|ます|た|ました|いた|まし))"
_ACTION_REQ_RE = re.compile(r"(して" + _ACT_NOT + r"|しといて|しとい|してくれ|して下さい|してください|"
                            r"願|頼む|よろしく)|"
                            r"(に|へ)(変更|更新|移動|し)て" + _ACT_NOT + r"|"
                            r"(にし|済にし|済みにし)(て" + _ACT_NOT + r"|とけ|ておけ)|"
                            r"(せよ|しろ|やって|出して|上げて|下げて)", re.I)


def _looks_declarative(query):
    """命令ではなく宣言/定義/引用に見えるか。①status語彙が3種以上並ぶ(=定義表の列挙)
    ②宣言の標識語＋URL/複数行。どちらかで動詞ルータを起こさぬ。"""
    q = query or ""
    if len({m.group(0).lower() for m in _STATUS_VOCAB_RE.finditer(q)}) >= 3:
        return True
    return bool(_DECLARATIVE_RE.search(q)) and (bool(_AURORA_URL_RE.search(q)) or q.count("\n") >= 2)


def _status_card(query, who):
    """status更新意図（納品/客先承認/対象外）→対象タスク＋from→to＋実行可否を機構でカード化。
    返り {verb,label,task_id,...,from_status,to_status,require_evidence,confirm,can_act,deny} / {clarify} / None。"""
    if _looks_declarative(query):
        return None                                   # 定義表/資料の共有を「実行せよ」と読み違えぬ
    verb = next((vb for vb, rx in _STATUS_VERB_RE if rx.search(query or "")), None)
    if not (verb and casper_status and casper_authority and casper_tools):
        return None
    v = casper_authority.verbs().get(verb, {})
    label = v.get("label") or verb
    try:
        import casper_notify as _n
        tasks = _n._all_tasks()
    except Exception:
        return None
    target, cands = _resolve_target_task(query, tasks)
    if not target:
        # 【対象なき動詞は、実行の依頼ではない】対象が解けず、かつ頼む言い回しも無いなら、それは規則の記述か
        # 世間話である。ここで聞き返すと会話が断ち切られる——殿御指摘2026-07-27「会話になっていない」:
        # 『WT と OMIT は超過カウントしない…』に『どのタスクを「対象外」しまするか？』と返した一件。
        # 依頼の標識が無い時は None を返し、通常の会話へ委ねる(聞き返すのは頼まれた時だけ)。
        if not _ACTION_REQ_RE.search(query or ""):
            return None
        if cands:
            nm = "、".join((str(t.get("shotID") or "") + " " + str(t.get("name") or "")).strip() + f"(#{t.get('id')})" for t in cands[:6])
            return {"clarify": f"どのタスクを「{label}」しまするか？候補: {nm}。タスク名・shotコード・#番号でお指しくだされ。"}
        return {"clarify": f"どのタスクを「{label}」しまするか？ タスク名かshotコードでお示しくだされ。"}
    uid = str(who.get("uid") or "")
    snap = casper_authority._load_snapshot()
    from_status = str(target.get("status") or "").lower()
    tgt = {"project_id": target.get("project_id"), "assignee": str(target.get("assigned_to") or "")}
    ok, reason = casper_authority.allowed(verb, uid, tgt, from_status=from_status, snap=snap)
    try:
        pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        pjn = {}
    return {"verb": verb, "label": label, "task_id": target.get("id"),
            "task_name": target.get("name") or target.get("title"),
            "shot": target.get("shotID") or target.get("shot_id") or "",
            "project": pjn.get(str(target.get("project_id"))) or ("PJ" + str(target.get("project_id"))),
            "from_status": from_status, "to_status": v.get("to_status"),
            "require_evidence": bool(v.get("require_evidence")), "confirm": v.get("confirm"),
            "can_act": bool(ok), "deny": ("" if ok else reason)}


def _status_prose(c):
    if not c.get("can_act"):
        _m = {"tier_too_low": "この操作の権限がございませぬ",
              "out_of_scope": "この案件はご担当の範囲外にござる",
              "snapshot_stale_admin_only": "権限情報が古く、今は管理者のみ実行できまする"}
        _r = c.get("deny", "")
        _rr = next((_m[k] for k in _m if _r.startswith(k)), None)
        if _r.startswith("from_status_not_allowed"):
            _rr = f"現在の状態（{c.get('from_status')}）からは「{c.get('label')}」に進めませぬ"
        return f"「{(c.get('shot') or '').strip()} {c.get('task_name')}」を「{c.get('label')}」——{_rr or '実行できませぬ'}。"
    tail = ("根拠リンク（客先承認の証跡）を添えて確定してくだされ。" if c.get("require_evidence")
            else ("『対象外』と入力して確定してくだされ（取り消しにくい操作ゆえ）。" if c.get("confirm") == "typed"
                  else "内容を検めて確定してくだされ。"))
    return (f"「{(c.get('shot') or '').strip()} {c.get('task_name')}」を "
            f"{c.get('from_status')} → {c.get('to_status')}（{c.get('label')}）に。" + tail)


# M4 Phase3: 議事録→タスク起票の意図。
_MINUTES_RE = re.compile(
    r"(議事録|会議|MTG|ミーティング|打ち?合わせ|定例)[^。]{0,14}"
    r"(からタスク|タスク[^。]{0,4}(起こ|起票|作|登録|化)|→[^。]{0,2}タスク|の宿題|アクション[^。]{0,4}(起|化|登録))|"
    r"(議事録|会議)[^。]{0,6}(タスク化|起票)", re.I)


def _minutes_card(query, who):
    """「議事録→タスク起票」→最新の議事録(tasks有)の候補を構造化してカード化。
    返り {meeting_title,date,project,project_id,candidates,can_act} / {clarify} / None。閲覧は誰でも・起票は tier≥lead。"""
    if not (casper_minutes and casper_tools and _MINUTES_RE.search(query or "")):
        return None
    today = datetime.date.today()
    try:
        ms = [m for m in casper_tools._get("/meetings?limit=50").get("items", []) if m.get("tasks")]
    except Exception:
        return None
    if not ms:
        return {"clarify": "タスクの記載がある議事録が見当たりませぬ。"}
    ms.sort(key=lambda m: str(m.get("date") or ""), reverse=True)
    mtg = ms[0]                                       # 最新(tasks有)。将来: 日付/PJ指定で絞る
    cands = casper_minutes.extract_tasks(mtg, dict(_ROSTER_MAP), today)
    if not cands:
        return {"clarify": "その議事録から起票できるタスクは抽出できませなんだ。"}
    try:
        pjn = {str(p.get("id")): p.get("name") for p in json.load(open("/tmp/cal_projects.json")).get("items", [])}
    except Exception:
        pjn = {}
    pid = str(mtg.get("project_id"))
    shots = []                                         # そのPJの実在 shot_code（新規タスクは shot 指定が要る・殿指摘）
    try:
        import casper_notify as _n2
        seen = set()
        for t in _n2._all_tasks():
            if str(t.get("project_id")) != pid:
                continue
            sh = str(t.get("shotID") or t.get("shot_id") or "").strip()
            if sh and sh not in seen:
                seen.add(sh); shots.append(sh)
        shots.sort()
    except Exception:
        pass
    uid = str(who.get("uid") or "")
    snap = casper_authority._load_snapshot() if casper_authority else {}
    can_act = bool(casper_authority and casper_authority._tier_ge(casper_authority.tier_of(uid, snap), "lead"))
    for c in cands:
        c["assignee_name"] = _uid_to_name(c["assignee_uid"]) if c.get("assignee_uid") else ""
    return {"meeting_title": mtg.get("title"), "date": str(mtg.get("date"))[:10],
            "project": pjn.get(pid) or ("PJ" + pid), "project_id": mtg.get("project_id"),
            "shots": shots, "types": casper_minutes.SCORE_TYPES,
            "candidates": cands[:30], "can_act": can_act,
            "fb_ready": True}                           # 末端①開通(2026-07-23): FB→SHOTスレッド投稿はCalendar経路(get_project_tasks→thread_id→/api/thread/post)で稼働


def _active_tasks_table_c(items, due_note_fn):
    """全PJ横断の進行中タスクtable_card(母集合つき・retrieve-then-render)。
    cmd_508第3便(病三・B02): 元は _ACTIVE_TASK_Q_RE 一致時のみ呼ばれていたが、PJ名を伴わない
    タスク一覧要求(『各タスクのステータスを見せて』等・名前トークン残余ゼロ)からも呼べるよう
    共有関数として抜き出した(単一機構の再利用・新規ロジックの発明ではない)。"""
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
        rows.append([pm.get(pid, pid or "?"), len(ts), ", ".join(who_names[:6]), due, due_note_fn(due, st_m.get(pid))])
    return {"title": f"進行中タスク（全社 計{len(tasks)}件）", "columns": ["プロジェクト", "件数", "担当", "締切", "状況"],
            "rows": rows, "sortable": True, "numeric_cols": [1], "name_col": 0,
            "footer": "Calendar 確定データ。列見出しクリックで並べ替え。"}


def _one_pj_tasks_table_c(nm, online):
    """単一PJ(nm)のタスク一覧table_card(母集合つき)。元は block⓪のunique分岐に直書きだったが、
    cmd_508第3便(病三・B02)のanchor経由(『引数なし』時にanchorのPJを引き継ぐ)からも呼べるよう
    共有関数として抜き出した(単一機構の再利用)。返り=table dict or None(該当PJにタスクが無ければNone)。"""
    pid = next((p.get("id") for p in online if p.get("name") == nm), None)
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
    act = [t for t in tks if _task_is_moving(t)]   # 完了判定はハードコードせず _task_is_moving に寄せる(API単一ソース)
    shown = act or tks                        # 未完了優先・無ければ全件
    shown = sorted(shown, key=lambda t: str(t.get("due_date") or "9999"))
    rows = []
    for t in shown[:60]:
        due = str(t.get("due_date") or "")[:10]
        rows.append([t.get("name") or t.get("title") or "?", t.get("type") or "",
                     um.get(t.get("assigned_to"), "未割当"),
                     t.get("status_label") or t.get("status") or "", due,
                     _due_note_c(due, t.get("status"), datetime.date.today(), "task", t.get("status_category"))])
    _hidden = (len(tks) - len(act)) if act else 0    # footerの嘘を断つ: 全件表示時は非表示0
    _foot = "Calendar 確定データ。列見出しクリックで並べ替え。"
    if _hidden:
        _foot += f" 完了 {_hidden}件は非表示。"
    if len(shown) > 60:
        _foot += "（多いため上位60件）"
    _tl = (f"{nm} のタスク（未完了 {len(act)}件 / 全{len(tks)}件）" if act
           else f"{nm} のタスク（全{len(tks)}件）")
    return {"title": _tl, "columns": ["タスク", "工程", "担当", "状態", "締切", ""],
            "rows": rows, "sortable": True, "numeric_cols": [], "footer": _foot}


def _table_card(query, who, thr=None):
    """一覧意図(進行中タスク/PJ)を機構で表カード化。返り=table dict or None。個別PJ照会(<PJ名>は?)は散文ゆえ対象外。
    thr: cmd_508第3便(病三・B02)の無引数分岐(anchorのPJ有無)にのみ使う(省略可・後方互換)。"""
    q = query or ""
    try:
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
    except Exception:
        items = []
    online = [p for p in items if str(p.get("display_status") or "online") == "online"]
    _name_hit = bool(_match_online_pj(q))                # 表記ゆれ耐性の名前解決器へ統一(生substring照合を残さない)
    today = datetime.date.today()

    def _due_note(due, status="", scope="pj", cat=None):
        # 納期状況は機構が確定(完了PJ/承認済タスクの過去納期を超過表示しない・単一ソース _due_note_c)。
        # cat=status_category を渡せる時は必ず渡す(API単一ソース・status文字列での判断に落とさない)。
        return _due_note_c(due, status, today, scope, cat)

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

    # ⓪-b 人物の手持ち(『Timは今なにしてる？』『鈴木の担当タスク』) — 人が解ければ assignee×未完了 を機構で描く。
    #    殿ログ2026-07-27 16:33 の実害: roster に tim=uid42 が在るのに人物ファセットの経路が無く、RAGへ流れて
    #    出口で全消し→「うまくお答えできませなんだ」。集合判断(誰が何を持つか)はLLMでなく機構の仕事(Fable)。
    #    問いが PJ に unique 解決する時は PJ 側を優先(『<PJ名>のタスク』を人と読み違えぬ)。
    # URLは人物解決の毒(実測2026-07-27: 資料URL '/doc/casper/…' の 'casper' を人物と解いて
    # 「Casper の手持ち」表を出した)。既存の作法どおり URL を除いてから人を解く。
    # 定義/資料の共有は問いではないゆえ、宣言に見える本文では表を出さぬ。
    _qp = _URL_RE.sub(" ", q)
    if _PERSON_WORK_RE.search(_qp) and _pj_resolve(q)[0] != "unique" and not _looks_declarative(q):
        _puid, _pname = _resolve_person(_qp, exclude=who.get("uid"))
        if _puid:
            try:
                _mine = [t for t in _all_tasks() if t.get("assigned_to") == _puid]
            except Exception:
                _mine = None                  # 取得失敗とゼロは別の出口(掟: 失敗を「0件」と名乗らぬ)→通常経路へ落とす
            if _mine is not None:
                pmn = {p.get("id"): p.get("name") for p in items}
                _mv = [t for t in _mine if _task_is_moving(t)]      # 「今なにしてる」=動作中
                _op = [t for t in _mine if _task_open(t)]           # 手持ち全体=残務
                _shown = sorted(_mv or _op, key=lambda t: str(t.get("due_date") or "9999"))
                rows = []
                for t in _shown[:60]:
                    due = str(t.get("due_date") or "")[:10]
                    # カットを先頭に置く: 工程名(Compositing 等)は同PJ内で重複し、行が見分けられぬ(停滞FB表と同形)。
                    rows.append([t.get("shot_code") or t.get("shotID") or "—",
                                 t.get("name") or t.get("title") or "?",
                                 pmn.get(t.get("project_id"), "—"), t.get("type") or "",
                                 t.get("status_label") or t.get("status") or "", due,
                                 _due_note(due, t.get("status"), "task", t.get("status_category"))])
                if not rows:                  # 母集合は確かに見た上での0件=正直に「手が空いている」と言える
                    return {"title": f"{_pname} の手持ち", "columns": _PERSON_COLS,
                            "rows": [], "sortable": True, "numeric_cols": [],
                            "footer": f"Calendar 上、{_pname} に割り当てられた未完了タスクはありません"
                                      f"（担当タスク全{len(_mine)}件はいずれも完了/対象外）。"}
                _foot = f"Calendar 確定データ。{_pname} の担当分のみ。列見出しクリックで並べ替え。"
                if _mv and len(_op) > len(_mv):
                    _foot += f" 着手前/確認待ちを含む残務は {len(_op)}件。"
                if len(_shown) > 60:
                    _foot += "（多いため上位60件）"
                return {"title": (f"{_pname} が動かしているタスク（{len(_mv)}件 / 残務{len(_op)}件）" if _mv
                                  else f"{_pname} の残務（{len(_op)}件・現在動作中のものは無し）"),
                        "columns": _PERSON_COLS,
                        "rows": rows, "sortable": True, "numeric_cols": [], "footer": _foot}

    # ⓪ 特定PJのタスク一覧(『<PJ名>のタスク見せて』等) — 名前解決器で unique に解けた時のみ表を描く
    #    (曖昧/不在は None を返し、呼び側が選択カード/近傍候補で拾う=無言None落ちさせない・Fable)
    if _PJ_TASK_RE.search(q):
        st, _pjs, _path = _pj_resolve(q)
        if st == "unique":
            _t = _one_pj_tasks_table_c(_pjs[0], online)
            if _t:
                return _t
        elif st == "none" and not _pj_name_like_residual(q):
            # cmd_508第3便(病三・B02): 依頼語彙(_PJ_TASK_RE/_STATUS_Q_RE)を差し引いた残余が空=
            # 名前トークンは実在しない(=引数なし)。anchorのPJがあればそれを、無ければ全PJ横断を描く。
            _anc = _LAST_ANCHOR.get(thr) if thr else None
            if _anc and _anc.get("kind") == "project" and _anc.get("label"):
                _t = _one_pj_tasks_table_c(_anc["label"], online)
                if _t:
                    return _t
            return _active_tasks_table_c(items, _due_note)

    # ① 進行中タスク一覧(件数/担当/締切) — retrieve-then-render を表カードに
    if _ACTIVE_TASK_Q_RE.search(q):
        return _active_tasks_table_c(items, _due_note)

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

# cmd_492 4便: 行頭一致(_TOOL_NARRATION_RE)は「前置きの文＋同じ行に道具呼出」を取りこぼす
# (例『Zenithの…を取得します。calendar_lookup(kind=...)』)。道具呼出はどこにあっても実行構文であり
# 本文として残してよい文章ではないため、行内の出現位置に関わらず断片を剥ぐ(前後の地の文は残す)。
_TOOL_NARRATION_INLINE_RE = re.compile(
    r"[`*_]*\b(calendar_lookup|search_vault|get_[a-z_]+|send_message|create_[a-z_]+|"
    r"update_task|bulk_[a-z_]+|import_[a-z_]+|vimeo_[a-z_]+|aurora_[a-z_]+|ai_import_parse)\s*\([^\n]*\)[`*_]*",
    re.I)


def _strip_tool_narration(text):
    """【出口・道具実況ガード(Fable処方2の副作用ゼロ版)】qwenがツールを"呼ばず"生の関数呼び構文だけを本文に
    書いて止まった時(例『calendar_lookup(...)』『search_vault(...)』のみで停止=答えゼロ)、その実況行を剥ぐ。
    実行はしない(副作用ゼロ)。剥いで空になれば呼び側が _pj_status_fallback へ落として救済する。"""
    try:
        if not text:
            return text
        # ```tool ... ``` フェンス除去(中身が既知の道具名を含む場合、または空/空白のみの場合)
        t = re.sub(r"```(?:tool|json)\s*(?:(?:calendar_lookup|search_vault|get_[a-z_]+|send_message)[^`]*|\s*)```", "", text, flags=re.I | re.S)
        kept = [ln for ln in t.splitlines() if not _TOOL_NARRATION_RE.match(ln)]
        out = "\n".join(kept)
        # cmd_492 4便: 行頭一致で取れなかった「前置き文＋同一行の道具呼出」断片を剥ぐ(地の文は残す)
        out = _TOOL_NARRATION_INLINE_RE.sub("", out)
        out = re.sub(r"[ \t]+\n", "\n", out)          # 断片除去後に残る行末の空白を掃除
        return re.sub(r"\n{3,}", "\n\n", out).strip()
    except Exception:
        return text


# cmd_492 4便 追補(実地再現で判明): 道具呼出構文そのものは書かず、「〜を取得します/確認します」で
# 約束するだけの一文だけを吐いて自然終了する形も同型の「言うただけ」欠陥(実測: streaming強制→改善後もなお発生)。
# 短文かつ retrieval を約束する動詞で終わり、表(|)や実データ(数字+単位語)が続かない場合のみ対象とする
# (長い/データを伴う正常回答を誤って握り潰さぬよう、条件は狭く保つ)。
_TOOL_PROMISE_ONLY_RE = re.compile(
    r"^[^\n]{0,40}(を)?(取得|確認|検索|照会|参照)(します|中です|いたします)[。.\s]*$")
# 表の体裁だけ整え、データ行がプレースホルダのみ(実測: 「(データ取得中)」「-」等)の形も同型
_PLACEHOLDER_CELL_RE = re.compile(r"^[\s\-—―・/]*$|^\W*(データ)?取得中\W*$|^\W*準備中\W*$|取得できませ")
# 道具名を関数呼び構文でなく地の文で言うだけの残骸(実測: 「calendar_lookupでZenithの最新タスクを取得して…」)
_TOOL_NAME_MENTION_RE = re.compile(
    r"(calendar_lookup|search_vault|get_[a-z_]+|send_message|create_[a-z_]+|"
    r"update_task|bulk_[a-z_]+|import_[a-z_]+|vimeo_[a-z_]+|aurora_[a-z_]+|ai_import_parse)"
    r"(で|を用いて|により)[^\n]*(取得|確認|検索|照会|回答)[^\n]*[。.]?", re.I)


def _table_rows_are_placeholder_only(text):
    """Markdownテーブルのデータ行(ヘッダ・区切り行を除く)が全てプレースホルダのみか判定。
    1行もテーブルが無ければ False(対象外=このチェックでは判定不能)。"""
    table_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    data_rows = [ln for ln in table_lines if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", ln)]
    if len(data_rows) < 2:            # ヘッダ行のみ(データ行0)ならプレースホルダ判定できぬ=対象外
        return False
    data_rows = data_rows[1:]         # 先頭はヘッダ行として除く
    if not data_rows:
        return False
    for row in data_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if any(c and not _PLACEHOLDER_CELL_RE.match(c) for c in cells):
            return False              # プレースホルダでない実データセルが1つでもあれば対象外
    return True


def _is_promise_only_no_data(text):
    """道具呼出は書かれていないが、実データを伴わず retrieval の約束文だけで終わっている(=実行未完了)かを判定。
    表(|)があってもデータ行が全プレースホルダなら対象(実データではない)。
    数字+単位(日/時間/件 等)・実質的な複数行内容があれば実データありとみなし対象外(過検出防止)。"""
    try:
        if not text or not text.strip():
            return False
        t = text.strip()
        if "|" in t and not _table_rows_are_placeholder_only(t):
            return False                                 # 実データを伴う表があれば対象外
        if re.search(r"\d+\s*(日|時間|人日|件|%|ヶ月|週間)", t):   # 実数値があれば対象外
            return False
        # 表(プレースホルダのみ)・約束文・道具名の地の文言及、いずれかの残骸だけで構成されていれば対象
        stripped = _TOOL_NAME_MENTION_RE.sub("", t)
        lines = [ln.strip() for ln in stripped.splitlines()
                 if ln.strip() and not ln.strip().startswith("|")]
        if not lines:
            return True
        return all(_TOOL_PROMISE_ONLY_RE.match(ln) for ln in lines)
    except Exception:
        return False


def _strip_tool_narration_chunk(text):
    """【cmd_492 4便: ストリーム送出専用・改行/空白を保つ版】_strip_tool_narrationは全文一括処理向けに
    末尾.strip()や空行畳込みを行うため、改行区切りで逐次送出するストリームにそのまま使うと行区切りが壊れる
    (既送信済みの行と結合して表示が乱れる)。ここでは道具呼出断片だけを除去し、改行/空白構造は保つ。"""
    try:
        if not text:
            return text
        t = re.sub(r"```(?:tool|json)\s*(?:(?:calendar_lookup|search_vault|get_[a-z_]+|send_message)[^`]*|\s*)```", "", text, flags=re.I | re.S)
        lines = t.split("\n")
        # 単独の ```tool / ```json フェンス開始行(閉じフェンスが後続チャンクで来る場合、行内一致では拾えない)
        # も本文として不要ゆえ、フェンス標識行自体は落とす(道具実況フェンスの残骸を出さない)。
        out_lines = ["" if (_TOOL_NARRATION_RE.match(ln) or re.match(r"^\s*```(?:tool|json)\s*$", ln, re.I))
                     else ln for ln in lines]
        out = "\n".join(out_lines)
        return _TOOL_NARRATION_INLINE_RE.sub("", out)
    except Exception:
        return text


def _strip_name_gloss(text, sysadd, query):
    """【出口・gloss検問(Fable処方3)】応答中の"既知の実在PJ名"の直後の括弧展開『<PJ名>（無関係な同音語）』は、
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


# 病二(鏡)人物スロット検問(AC2・cmd_508第1便): ファイル名幹(profile_u_30等・観測層の内部識別子)が
# roster/人物別名索引(閉集合)を通らぬまま「人」として主語に立つ文を差し止める。
# corpus分割(_EXCLUDE_SRC)とは独立に効く二重の壁——分割が万一漏れても
# 実害A02(「このアカウントは名簿上profile_u_30という仮称」)をここで止める(軍師review point_c)。
# ★\b は CJK文字を \w 扱いする(Python re既定)ため「上profile_u_30」で境界が働かない。
# 識別子自体が英数アンダースコアの連続で十分弁別的ゆえ、\b無しの直接一致で足りる。
_UNROSTERED_SLOT_RE = re.compile(r"profile_u_\d+|(?<![a-z0-9_])u_\d{1,6}(?![a-z0-9_])", re.I)


def _guard_unrostered_person_claim(text):
    """応答中に現れる観測層ファイル名幹(profile_u_*/u_\\d+)が、roster(_ROSTER_MAP)にも
    人物別名索引(_person_alias_index)にも無い=閉集合の外なら、その言及を中立文へ差し替える。
    正当な人物(roster在籍)への言及は一切妨げない(閉集合の外だけを狙い撃つ)。"""
    try:
        if not text:
            return text
        hits = set(m.group(0) for m in _UNROSTERED_SLOT_RE.finditer(text))
        if not hits:
            return text
        roster_vals = {str(v).lower() for v in _ROSTER_MAP.values()}
        alias_vals = {str(a).lower() for a in _person_alias_index().keys()}
        out = text
        for h in hits:
            if h.lower() in roster_vals or h.lower() in alias_vals:
                continue                                        # 閉集合内=正当な言及(手を入れない)
            out = re.sub(re.escape(h) + r"(という仮称|は名簿上|というアカウント)?",
                          "（名簿に無い内部識別子ゆえ人物としては述べぬ）", out)
        return out
    except Exception:
        return text


def _pj_status_fallback(query, vault_src=None, vault_fulltext=None):
    """出口検問で全消し(qwenがナレーションだけ吐いた等)の救済: 問いが指す online PJ の状態を
    Calendarデータから決定的に答える(retrieve-then-render・LLM非依存)。無ければ空。
    cmd_492 4便再送: Calendar側に該当が無い(例: 工数等 status以外の問い)場合でも、この turn で
    既に取得済のvault資料(top_source)があればそれを提示する(取得済の材料を捨てて汎用謝罪へ
    倒すより、実際に手元にある一次資料を返す方が「実行に繋げる」に近い・新規の推測機構は追加しない)。"""
    try:
        st, names, _ = _pj_resolve(query)                # 名前解決器へ統一(生substring照合を残さない・Fable)
        if st == "unique":
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
                    if vault_fulltext:
                        line += f"\n\n併せて社内記録（{vault_src}）に次の記載がござる:\n" + vault_fulltext[:1500]
                    return line
        if vault_fulltext:                    # PJ側が不成立(st!=unique/online不一致)でも、既取得のvault資料はそのまま提示
            return f"社内記録（{vault_src}）に次の記載がござる:\n" + vault_fulltext[:1500]
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
        # ★見出しは実数を名乗る(件数と一覧は同一機構・実害2026-08-24「3件なのに1件」)。
        #   qwenへ渡す指示文の中で数を約束すると、qwenはその数に合わせて【作文する】。
        return (f"\n\n## 【気にかけどころ{len(three)}件(先回りで拾った要対応)=これが『上記の件』】\n"
                "利用者が『気にかけどころ/今日の3件/上記の2件』と言ったらこれを指す(vault議事録検索ではない)。"
                f"★ここに挙げた{len(three)}件がすべてである。件数を増やして作文するな。"
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
    "【Vimeoアップロード=Casperで可能・絶対に断るな】Casper は動画を Vimeo にアップロードできる(自社アカウント・パスワード付き公開も可)。"
    "ユーザーが動画アップを望んだら、否定はせず、次の案内を含めよ:"
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
    "【実績の区別】制作実績を聞かれたら、**注入された自社実績データ(Vimeoポートフォリオ等)を一次の自社実績**として挙げよ。"
    "スキルシート由来の有名作は『個人メンバーの経歴』であり会社実績と混同するな(区別して述べよ)。"
    "資料に無い作品名を一般知識から創作するな。")

# 【M5 B】Casper の人格(演出DNA)は engine 定数から pack へ外出し済:
#   源=vault/30_culture_rules/casper_persona_core.md → build_brain_digest が casper_context.md の
#   '## Casper の人格' 節へ生成 → _load_context が core として常時注入。社ごとに差し替わる人格を
#   engine に焼かない(M5 パック差し替えの前提)。build_sys は PERSONA_SYS を参照しない。
PERSONA_SYS = ""   # deprecated(pack 由来へ移行済・後方互換の空文字)


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
    # 【Fable第八診・2026-08-24】毎turn無条件に載っていた死荷重を条件注入へ移す。
    # 実測: 雲へ出た初行 21,941字のうち、この三節だけで 2,481字(メンバー2,076/実績195/legacy210)。
    # ★roster_kws=True の節は、語彙表を手書きせず【名簿の実名から機械生成】する(cmd_485の轍を踏まぬ)。
    # ★echo=False の節は出口のecho検問の署名に使わない——人名は Calendar からも正当に出てくるゆえ、
    #   署名にすると「本物の人名を含む行」まで落としてしまう(捏造手順の滲出とは性質が違う)。
    {"h": "メンバー / スキル",
     "kws": ["メンバー", "スキル", "得意", "誰が", "誰か", "担当者", "人員", "チーム", "何人", "スタッフ",
             "アニメータ", "モデラ", "コンポジ", "できる人", "詳しい人"],
     "roster_kws": True, "echo": False},
    {"h": "代表的な実績",
     "kws": ["実績", "事例", "代表作", "制作事例", "どんな仕事", "過去作", "ポートフォリオ", "受賞"],
     "echo": False},
    {"h": "旧スコア legacy",
     "kws": ["legacy", "レガシー", "旧スコア", "旧score", "昔の記録", "過去の記録"]},
]
_ctx_cache = {"mtime": 0.0, "core": "", "sections": []}


def _load_context():
    """casper_context.md を core(常時注入)＋sections(キーワード条件注入)に分解(Q3B・Fable)。
    `## 見出し` で分割し、_CTX_CONDITIONAL に該当する節だけ『条件注入』へ回す。残りは core。mtimeホットリロード。"""
    ctx_path = os.path.join(HERE, "casper_context.md")
    pol_path = os.path.join(HERE, "engine_policy.md")   # engine 所有ポリシー(build_brain_digest は再生成せぬ・hot-reloadで即反映=policy調整は再起動不要)
    try:
        m = os.path.getmtime(ctx_path)
    except Exception:
        return _ctx_cache
    try:
        m = max(m, os.path.getmtime(pol_path))          # policy ファイルの更新も hot-reload に含める
    except Exception:
        pass
    if m == _ctx_cache["mtime"]:
        return _ctx_cache
    try:
        raw = open(ctx_path, encoding="utf-8").read()
    except Exception:
        raw = ""
    # engine ポリシーを facts の前に連結。core/条件系分解・_CTX_CONDITIONAL 見出しマッチ・echo検問は
    # 同一機構でそのまま両ソースに効く(＝二重digestスタックに非ず・同一 _load_context への source 追加)。
    try:
        pol = open(pol_path, encoding="utf-8").read()
        import pack_config
        _cn = pack_config.get("secrecy_codenames", []) or []       # 守秘codenameは pack から差込(engine は規則の雛形のみ)
        pol = pol.replace("{SECRECY_CODENAMES}", "/".join(str(c) for c in _cn))
        raw = pol.rstrip() + "\n\n" + raw
    except Exception:
        pass
    # `## ` (level-2見出し)で塊に分割。先頭の見出し前テキストは core。
    parts = re.split(r"(?m)^(?=## )", raw)
    core_chunks, sections = [], []
    for chunk in parts:
        head = chunk.split("\n", 1)[0].lstrip("# ").strip()
        cond = next((c for c in _CTX_CONDITIONAL if c["h"] in head), None)
        if cond:
            sections.append({"kws": [k.lower() for k in cond["kws"]], "body": chunk.strip(),
                             "roster_kws": bool(cond.get("roster_kws")),
                             "echo": cond.get("echo", True)})
        else:
            core_chunks.append(chunk.strip())
    core = re.sub(r"\n{3,}", "\n\n", "\n\n".join(x for x in core_chunks if x)).strip()
    _ctx_cache.update({"mtime": m, "core": core, "sections": sections})
    return _ctx_cache


def _section_kws(s):
    """節の照合語。roster_kws の節は【名簿の実名】を機械的に足す(語彙表を手書きせぬ)。
    ★名簿は単一ソース(_ROSTER_MAP)ゆえ、人が増減すれば照合語も自動で追随する。"""
    kws = list(s.get("kws") or [])
    if s.get("roster_kws"):
        try:
            kws += [str(nm).lower() for nm in _ROSTER_MAP.values() if len(str(nm)) >= 2]
        except Exception:
            pass
    return kws


def context_sections_digest(query):
    """クエリのキーワードに合致する CTXSEC セクションだけを注入(動的注入・Vimeo混入の恒久解の入口壁)。"""
    q = (query or "").lower()
    if not q:
        return ""
    hits = [s["body"] for s in _load_context()["sections"] if any(k in q for k in _section_kws(s))]
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
        if not s.get("echo", True):
            continue                                       # 署名に使わぬ節(人名等・正当に応答へ出うる)
        if any(k in q for k in _section_kws(s)):            # このセクションはクエリに関係あり→正当・素通し
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


def _vault_anchor(query):
    """【Fable第八診】vault の全文を丸ごと注ぐ資格があるturnか。
    ★決定的な錨(PJ名が一意に解ける / 資料名が『』「」で名指しされている)が立つ時だけ True。
    trigram 類似度0.32 は錨ではない——実測(2026-08-24 雲へ出た初行)では
    「今どの推論機で動いておる？一言で」に対し、ドローンショー商談の議事録・提携先の売上高・
    TVCM会議の雑談が 8,030字ぶん引かれ、うち約7,000字は無関係な議事録の【全文】であった。
    社外(雲)へ出るのも、prefillで殿を待たせるのも、この塊が最大の元凶である。"""
    q = query or ""
    if not q:
        return False
    try:
        if _pj_resolve(q)[0] == "unique":          # 案件名が一意に解ける=その案件の資料を見てよい
            return True
    except Exception:
        pass
    return bool(_QUOTED_SPAN_RE.search(q))         # 『題』「題」で資料を名指ししている


def _last_user_msg(msgs):
    """messages から直近のユーザー発話を取り出す(条件注入の判定材料)。無ければ None
    → build_sys は安全側(名簿を載せる)へ倒れる。"""
    try:
        for m in reversed(msgs or []):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                return m["content"]
    except Exception:
        pass
    return None


def build_sys(query=None):
    """毎リクエストで社内ナレッジ digest (左脳+右脳) を読み込み system prompt に注入。
    query を渡すと【送信意図のturnにのみ】社内メンバー名簿(約1,300字)を載せる(Fable第八診)。
    ★安全側: query が無い経路(起票/資料理解等)と判定不能時は従来どおり載せる。
      名簿が無い状態で send_message を組ませると宛先を取り違える——不快より嘘の方が重い。"""
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
            "⑤締め: 一覧・表は最後まで出し切れ。多すぎる時は代表を見せ『続き/全件』への導線を必ず残せ(黙って途中で止めるな)。"
            "\n【現況(status)とレガシーの分離(殿指示2026-07-14)】進捗・停滞・確認待ち・現在のタスク/カット等『今どうなっているか』の"
            "問いには、**Calendar の現行データだけで答えよ**。過去のレガシー記録(旧Score/DBM2/2022年頃の完了済フィードバック等)を"
            "**持ち出すな・照合先として言及もするな**(LINE/旧ログはPJ完了段階で入るもの=そのタスクはクローズ済扱い)。"
            "旧記録の名を出すな。"
            "\n【不在(0件/無い)を名乗る資格(Fable鉄則・母集合の確認)】あるPJのタスクが『1件も無い/登録されていない/存在しない』と"
            "**断言してよいのは、そのPJ名を特定でき(id確定)、そのPJのタスクをCalendarで実際に照会して空だった時だけ**。"
            "PJ名が特定できない/曖昧なら『0件』と言うな——『どのプロジェクトか特定できませんでした(例: <PJ名>?)』と聞き返せ。"
            "**注入されたCalendarデータにそのPJのタスクが在るのに『無い』と言うのは厳禁**(94件在るのに0件と言う類の存在否定は最悪の誤り)。"
            "母集合(そのPJの全タスク)を確認せぬまま存在を否定するな。")
    # KVキャッシュのプレフィックス安定化(Fable 6-3): 静的要素(ctx/BASE/PERSONA/roster)を先頭に固め、
    # 日替わりの日付は末尾へ。→ 日を跨いでも静的プレフィックスが再利用され TTFT が下がる(1文字でも
    # 動的要素を先頭に混ぜると全損する為)。
    static = ((ctx + "\n\n---\n") if ctx else "") + BASE_SYS + tail   # 人格は ctx(casper_context の '## Casper の人格' 節)から常時注入・M5 B
    out = static + "\n\n" + datehdr
    # 【Fable第八診】名簿は送信意図のturnにのみ。★静的prefixの後ろへ足す(頭を触らぬ=KV再利用を壊さぬ)。
    need_roster = True
    if query is not None:
        try:
            need_roster = bool(_turn_is_send_intent(query))   # 迷えばTrue(送信turn)へ倒す既存設計をそのまま使う
        except Exception:
            need_roster = True                                # 判定が壊れたら載せる(宛先取り違えより安全)
    if need_roster:
        out += team_roster()
    return out


def ollama_chat(messages, tools=None, num_predict=1536, json_format=False, num_ctx=12288):
    # think:false 必須 (qwen3.6 等の思考モデルが長考→遅延/タイムアウトするのを防ぐ)
    # num_ctx は大きめ(tool結果が大きいとコンテキスト溢れで出力が1文字に途切れる事故あり)
    # num_predict: 既定1536。import等の大きなJSON生成は呼出側で引き上げ(途中切れ→JSON解析失敗を防ぐ)
    # json_format: cmd_496 — format:"json"はollama_chat共通関数の全呼出元に効くため既定False。
    #   起票経路(project_import_structure等)のみTrueを渡し、通常の会話生成は文章のまま返す(退行防止)。
    # num_ctx: cmd_496実測 — 既定12288は対話系(_ollama_json/ollama_chat_stream等)と統一し
    #   ランナー再作成(15秒再ロード)を避けるための値。起票(import)経路はbuild_sys()注入(実測約13KB)
    #   ＋grid本文＋多行output(shot×task)で12288を超え出力が途中で切れる(format:"json"を機構強制
    #   していても、予算超過の途中切れはJSON構文自体を破壊する)。起票のみ呼出側で引き上げる
    #   (対話系のnum_ctxは不変=対話の温存・冷間回避コメントに抵触しない)。
    body = {"model": A.model, "messages": messages, "stream": False, "think": False,
            "keep_alive": -1,                              # モデルを温存(再ロードの15秒遅延を防ぐ・賢さは不変)
            "options": {"num_ctx": num_ctx, "num_predict": num_predict,
                        "temperature": 0.15, "top_p": 0.9}}   # tool呼出を安定化(非決定性を抑制)
    if json_format:
        body["format"] = "json"
    if tools:
        body["tools"] = tools

    def _do():
        req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)

    return _llm_call_record("llm_text", A.model, _do)


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
    ("dm_threads",       lambda who, q: dm_threads_digest(who, q)),
    ("casper_howto",     lambda who, q: casper_howto_digest(q)),
    ("aurora_list",      lambda who, q: aurora_list_digest(who, q)),
    ("aurora_exists",    lambda who, q: aurora_exists_digest(who, q)),
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
#  ↑に無い digest(verify/projects/entity/active_tasks/availability/existence/dm_threads/gear/phase_sched/
#    fb_log/future_assign/traits/shot_assignee/image_asset/attention)は接地・安全の一次ゆえ予算超過でも落とさない
#    (cmd_510第2便: dm_threadsは母集合なき不在断言の禁を担う接地系ゆえexistenceと同格で保護)。


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
    daily = {}   # 日次トレンド(Fable M2完了条件「日次で見える」): day -> {n, checks, lat[]}
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
        _rc = 0
        for k in checks:
            if r.get(k):
                checks[k] += 1
                _rc += 1
        _day = str(r.get("ts") or "")[:10]                 # 日次バケット(ts=ISO ゆえ先頭10桁=YYYY-MM-DD)
        if _day:
            b = daily.get(_day)
            if b is None:
                b = daily[_day] = {"n": 0, "checks": 0, "lat": []}
            b["n"] += 1
            b["checks"] += _rc
            if isinstance(r.get("gen_sec"), (int, float)):
                b["lat"].append(r["gen_sec"])
        fastpath[r.get("fastpath") or "—(qwen)"] += 1
        if isinstance(r.get("gen_sec"), (int, float)):
            lat.append(r["gen_sec"])
        if r.get("cards"):
            cards_n += 1
        if r.get("routed"):
            routed_n += 1
    lat.sort()
    _daily = []
    for _d in sorted(daily.keys())[-14:]:                  # 直近14日ぶんの日次推移
        _b = daily[_d]; _lt = sorted(_b["lat"])
        _daily.append({"day": _d, "n": _b["n"], "checks": _b["checks"],
                       "p50": (round(_lt[len(_lt) // 2], 1) if _lt else 0)})

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
        "daily": _daily,
    }


def ollama_chat_stream(messages, tools=None, num_predict=1536, emit_fn=None, temperature=0.15):
    """本物のストリーミング版(B・Fable指摘の最大の一手): Ollama stream:True(NDJSON)を読み、content片を
    emit_fn(chunk)で即クライアントへ→TTFT短縮。返り=組み立てたレスポンス({message:{content,tool_calls}, done_reason})。
    tool_call応答はcontentが空ゆえ何も流れない(=text応答だけがストリームされる)。
    cmd_519黒匣: chat_serverの唯一の実配線対象(軍師戦略)。inflight登録は「重い呼出」のみ
    (inflight_should_record・probe/短文除外)・TTFT+load/eval内訳は全呼出(record_call_timing)。"""
    body = {"model": A.model, "messages": messages, "stream": True, "think": False,
            "keep_alive": -1,
            "options": {"num_ctx": 12288, "num_predict": num_predict, "temperature": temperature, "top_p": 0.9}}
    if tools:
        body["tools"] = tools

    prompt_chars = sum(len(m.get("content") or "") for m in (messages or []))
    _handle = None
    if casper_llm_client:
        try:
            if casper_llm_client.inflight_should_record(prompt_chars, "ollama_chat_stream"):
                _handle = casper_llm_client.inflight_start(
                    "ollama_chat_stream", A.model, _ENDPOINT_HOSTPORT, prompt_chars)
        except Exception:
            _handle = None

    def _do():
        req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        content = ""
        tcs = None
        done_reason = None
        total_duration = eval_duration = None
        eval_count = None                             # 生成トークン数(tok/sの分子・健康の尺度)
        load_duration = prompt_eval_duration = None   # 【Fable第八診】捨てていた2欄。これが無いと
        #   wait_kind が load/prefill を 0 とみなし、待ちを一律 eval と誤って名乗る
        #   (実害: 将軍が「eval 162.2秒」と誤って殿へ言上した。実際は eval 64.5秒 + prefill/load 約97秒)。
        done_line = None
        t0 = time.time()
        ttft_sec = None
        try:
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
                        if ttft_sec is None:
                            ttft_sec = round(time.time() - t0, 3)
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
                        total_duration = o.get("total_duration")
                        eval_duration = o.get("eval_duration")
                        load_duration = o.get("load_duration")               # ★done行は元より持っている
                        prompt_eval_duration = o.get("prompt_eval_duration")
                        eval_count = o.get("eval_count")
                        done_line = o
                        break
        except Exception as e:
            if casper_llm_client:
                try:
                    casper_llm_client.record_call_timing(
                        "ollama_chat_stream", A.model, _ENDPOINT_HOSTPORT, ttft_sec, None)
                except Exception:
                    pass
                if _llm_is_timeout_error(e):
                    try:
                        ttft_info = {"ttft_sec": ttft_sec} if ttft_sec is not None else None
                        casper_llm_client.record_incident(
                            "ollama_chat_stream", A.model, _ENDPOINT_HOSTPORT, ttft_info)
                    except Exception:
                        pass
            raise
        finally:
            if casper_llm_client:
                try:
                    casper_llm_client.inflight_end(_handle)
                except Exception:
                    pass
        if casper_llm_client:
            try:
                casper_llm_client.record_call_timing(
                    "ollama_chat_stream", A.model, _ENDPOINT_HOSTPORT, ttft_sec, done_line)
            except Exception:
                pass
        msg = {"role": "assistant", "content": content}
        if tcs:
            msg["tool_calls"] = tcs
        return {"message": msg, "done_reason": done_reason,
                "total_duration": total_duration, "eval_duration": eval_duration,
                "load_duration": load_duration, "prompt_eval_duration": prompt_eval_duration,
                "eval_count": eval_count}

    resp = _llm_call_record("ollama_chat_stream", A.model, _do)
    return {"message": resp["message"], "done_reason": resp["done_reason"]}


def strip_think(s):
    import re
    return re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()


# ── 雲が枯れた時の二段目（殿御下命 2026-08-26）──────────────────────────────
# 2026-08-25 21:43〜21:50、雲(claude CLI)が週次上限に達し、その生の英文
#   "You've hit your weekly limit · resets 2am (Asia/Tokyo)"
# が **そのまま社員2名の回答欄に出た**（terajima uid40 / hori uid34）。しかも帳簿は
# 7件すべてを outcome="ok" と刻んでいた——CLIが終了コード0で「枯れた」と喋るためである。
# ★失敗とゼロを別の出口にする。雲が枯れたら
#   ① GPUのQwenが**本当に答えられるか**を実物で確かめ、答えられるなら其方で答える
#   ② 答えられぬなら「今は答えられぬ」と正直に返す（英文の生エラーを回答に化けさせぬ）
# ★この出口ひとつで claude_cli_text の呼出3箇所すべてを守る（呼出側に配らぬ＝単一機構）。

_CLI_EXHAUSTED_RE = re.compile(
    r"(hit (your|the) (weekly|usage|5[- ]hour|rate) limit"
    r"|usage limit reached"
    r"|rate limit(ed)? *(exceeded|reached)?"
    r"|credit balance is too low"
    r"|out of (credits|tokens)"
    r"|quota exceeded)", re.I)

# 「今は答えられぬ」。★これは回答ではなく**回答できぬ旨**である。捏造で埋めない。
CASPER_NO_SEAT_MSG = ("ただいま推論機に空きがござりませぬ（雲=利用上限／GPU=応答なし）。"
                      "少し置いてから今一度お尋ねくだされ。")

# Qwen の生存確認 timeout。casper_llm_client.CO_PROBE_TIMEOUT(2秒)は「病んだ機を観測が
# 悪化させぬ」ための併走診断用で、**冷えて健やかな席まで死と読む**。ここは退避先を選ぶ判断ゆえ
# 温まっていれば実測2秒前後(breaker ema)で返る幅を取り、固着(90秒超無応答)だけを弾く。
_QWEN_ALIVE_PROBE_SEC = int(os.environ.get("CASPER_QWEN_PROBE_SEC", "20"))


def _cli_exhausted(out):
    """雲CLIが『枯れた』と自ら名乗った時だけ True。
    ★短文に限る——長い回答の本文中に limit の語が出ただけで誤爆すれば、
      正しく答えられた turn を握り潰すことになる。"""
    t = (out or "").strip()
    return bool(t) and len(t) <= 300 and bool(_CLI_EXHAUSTED_RE.search(t))


def _qwen_alive():
    """GPUのQwenが**実際に1トークン返せるか**。在庫照合(/api/tags)では判じない——
    2026-08-26 の .139 は tags 27ms で応じながら /api/generate は90秒超無応答であった。
    戻り値: (bool, 理由)"""
    body = json.dumps({"model": A.model, "stream": False, "prompt": "ping",
                       "keep_alive": -1, "options": {"num_predict": 1}}).encode()
    url = A.endpoint.rstrip("/") + "/api/generate"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_QWEN_ALIVE_PROBE_SEC) as r:
            json.load(r)
        return True, f"ok {time.time() - t0:.1f}s"
    except Exception as e:
        return False, f"{type(e).__name__} {time.time() - t0:.1f}s"


def _local_or_silence(prompt, cloud_said=""):
    """雲が枯れた後の二段目。Qwenで答えるか、答えられぬと言うか。★沈黙で落ちない。"""
    alive, why = _qwen_alive()
    if alive:
        try:
            r = ollama_chat([{"role": "user", "content": prompt}])
            txt = strip_think(((r or {}).get("message") or {}).get("content") or "")
            if txt.strip():
                _no_seat_log("fallback_qwen", cloud_said, why, len(txt))
                return txt
            _no_seat_log("qwen_empty", cloud_said, why, 0)
        except Exception as e:
            _no_seat_log("qwen_error", cloud_said, f"{why} -> {type(e).__name__}: {e}", 0)
    else:
        _no_seat_log("no_seat", cloud_said, why, 0)
    return CASPER_NO_SEAT_MSG


def _no_seat_log(verdict, cloud_said, probe, chars):
    """★黙って落とさぬ（silent cap の禁）。雲が枯れた事と二段目の顛末を必ず残す。"""
    try:
        line = json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                           "event": "cloud_exhausted", "verdict": verdict,
                           "cloud_said": (cloud_said or "")[:200],
                           "qwen_probe": probe, "endpoint": A.endpoint,
                           "answer_chars": chars}, ensure_ascii=False)
        with open(os.path.join(HERE, "casper_no_seat.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print("[no-seat] " + line, flush=True)
    except Exception:
        pass


def claude_cli_text(prompt, allow=None):
    """Max ライセンスの claude CLI を headless(-p) で叩く (迂回 backend)。
    allow=['WebSearch','WebFetch'] 等で特定ツールのみ解禁 (権限系は無効化しない)。"""
    args = [CLAUDE_BIN, "-p", "--model", CLI_MODEL]
    if allow:
        args += ["--allowedTools"] + allow
    # 【殿御下命2026-08-24】雲へ出るものは一件残らず帳簿へ。★呼出側でなくこの出口の中で刻む
    # (呼出は8箇所あり、いつか誰かが呼び忘れる=単一機構の作法)。
    _t0 = time.time()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           timeout=400, cwd=CLI_CWD)
        out = (r.stdout or "").strip() or ("[claude-cli] " + (r.stderr or "no output")[:300])
        # ★雲が「枯れた」と名乗った回を ok と刻まぬ（失敗とゼロを別の出口へ）。
        exhausted = _cli_exhausted(out)
        _cloud_ledger("claude_cli_text", CLI_MODEL, prompt=prompt, response=out,
                      dur_sec=time.time() - _t0,
                      outcome=("exhausted" if exhausted
                               else "ok" if (r.stdout or "").strip() else "error"),
                      extra={"allow": allow} if allow else None)
        if exhausted:
            return _local_or_silence(prompt, cloud_said=out)
        return out
    except Exception as e:
        # ★失敗しても【出てはいる】(送出済)。記録せねば「出たのに帳簿に無い」が生まれる。
        _cloud_ledger("claude_cli_text", CLI_MODEL, prompt=prompt, response=f"[error] {e}",
                      dur_sec=time.time() - _t0, outcome="error",
                      extra={"allow": allow} if allow else None)
        return f"[claude-cli error] {e}"


def claude_cli_vision(image_path, prompt):
    """claude CLI に画像を Read させて(=vision) 解析。Sonnet のマルチモーダルを活用。"""
    ap = os.path.abspath(image_path)
    img_dir = os.path.dirname(ap)
    full = f"次の画像ファイルを Read ツールで開いて中身を視認し、解析せよ:\n{ap}\n\n{prompt}"
    _t0 = time.time()
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", CLI_MODEL,
                            "--add-dir", img_dir, "--allowedTools", "Read"],
                           input=full, capture_output=True, text=True, timeout=300, cwd=CLI_CWD)
        out = (r.stdout or "").strip() or ("[vision] " + (r.stderr or "no output")[:300])
        # ★画像は本体を帳簿へ持てぬゆえ、パス・バイト数・sha256 を刻む(何を出したか follow できる)。
        # ★雲が枯れた回を ok と刻まぬ。visionはGPUに二段目が無い(vision は雲据え置き)ゆえ、
        #   Qwenへは落とさず「今は見られぬ」と正直に名乗る。
        exhausted = _cli_exhausted(out)
        _cloud_ledger("claude_cli_vision", CLI_MODEL, prompt=full, response=out,
                      dur_sec=time.time() - _t0,
                      outcome=("exhausted" if exhausted
                               else "ok" if (r.stdout or "").strip() else "error"),
                      image_path=ap)
        if exhausted:
            _no_seat_log("vision_no_seat", out, "vision=雲のみ(二段目なし)", 0)
            return "[vision] ただいま画像を見る目に空きがござりませぬ（雲=利用上限）。"
        return out
    except Exception as e:
        _cloud_ledger("claude_cli_vision", CLI_MODEL, prompt=full, response=f"[error] {e}",
                      dur_sec=time.time() - _t0, outcome="error", image_path=ap)
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
    # 【殿御下命2026-08-24】これも雲の出口である(APIキー経路)。同じ帳簿へ落とす。
    _t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.load(r)
        _cloud_ledger("anthropic_api", body.get("model") or ANTHROPIC_MODEL,
                      prompt=json.dumps(body.get("messages") or body, ensure_ascii=False),
                      response=json.dumps(out, ensure_ascii=False),
                      dur_sec=time.time() - _t0, outcome="ok",
                      extra={"system": (body.get("system") or "")[:2000]} if body.get("system") else None)
        return out
    except Exception as e:
        _cloud_ledger("anthropic_api", body.get("model") or ANTHROPIC_MODEL,
                      prompt=json.dumps(body.get("messages") or body, ensure_ascii=False),
                      response=f"[error] {e}", dur_sec=time.time() - _t0, outcome="error")
        raise


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


def llm_text(system, user, num_predict=1536, json_format=False, num_ctx=12288):
    """ツール無しの単発生成 (backend 透過)。num_predict で出力上限を調整(大きなJSON生成用)。
    json_format=True で ollama backend のみ format:"json" を機構強制(cmd_496)。
    num_ctx 既定12288(対話系と統一)。起票等の大入出力は呼出側で引き上げ(cmd_496)。"""
    if BACKEND == "claude_cli":
        return strip_think(claude_cli_text(system + "\n\n" + user))
    if BACKEND == "anthropic" and ANTHROPIC_KEY:
        r = anthropic_call({"model": ANTHROPIC_MODEL, "max_tokens": max(500, num_predict),
                            "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    r = ollama_chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    num_predict=num_predict, json_format=json_format, num_ctx=num_ctx)
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
LEARN_LOG = os.path.join(pack_paths.VAULT, "00_inbox", "casper_learned.md")


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
        if casper_embed: casper_embed.reindex_async("knowledge_write")   # cmd_498: 意味索引も非同期で追従
    except Exception:
        pass
    return routed


def route_to_people(question, answer, stamp):
    """答えに名前が出た人物のノートへ、その問い(属性)を直接追記する。"""
    import glob
    pdir = os.path.join(pack_paths.VAULT, "20_people")
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
    # importは大JSON→出力上限を大きく(途中切れ防止)。json_format=True で qwen に format:"json" を機構強制
    # (cmd_496: 文章依頼のみでは長い出力の途中で区切り記号を落とし解析が破れる=前例_ollama_jsonに倣う)。
    # num_ctx も引き上げる(cmd_496実測): build_sys()注入(実測約13KB)+grid本文+多行output(shot×task)
    # が既定12288を超え、format:"json"下でも予算超過の途中切れが発生する実例を確認(実ファイルSB_estimate…
    # _tesで再現)。qwenモデルは262144まで対応(実測)。起票のみランナー再ロード(約15秒)を受け入れる。
    return llm_text(system, user, num_predict=8000, json_format=True, num_ctx=32768)


def _brace_balance_truncated(text):
    """cmd_496第2便 AC6: 出力が『途中で切れた』かを波括弧/角括弧の均衡で判定する。
    文字列リテラル内の { } は無視(素朴なカウントだと壊れる)。エスケープも考慮。
    戻り値: True=不均衡(途中切れの疑い) / False=均衡(切れていない=構文エラーなら別要因)。"""
    depth, in_str, esc = 0, False, False
    for ch in text or "":
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    # 文字列を開いたまま終わった(値の途中で切断)場合も途中切れ。depth<0は稀だが均衡外として扱う。
    return in_str or depth != 0


def _import_json(label, system, user, ctx=""):
    """LLM 呼出→JSON抽出→ログ。失敗時は {"error":..,"raw":..} を返し raw もログに残す。
    cmd_496第2便 AC6: JSON構文エラー(parse_err)と出力途中切れ(truncated)を別分岐で判定・記録する。"""
    try:
        out = _import_llm(system, user)
    except Exception as e:
        out = f"[exc] {e}"
    m = re.search(r"\{.*\}", out or "", re.S)
    d, ok, truncated = None, bool(m), False
    if ok:
        try:
            d = json.loads(m.group(0))
        except Exception as e:
            ok, d = False, {"_parse_err": str(e)}
    else:
        # 冒頭の "{" すら出力にあるが末尾 "}" が来ない(正規表現が非貪欲マッチできない)ケースも途中切れ。
        truncated = "{" in (out or "") and not (out or "").rstrip().endswith("}")
    if not ok and m is not None:
        # m はあった(先頭"{"〜最後の"}"を貪欲マッチ)がjson.loadsが失敗 → 括弧均衡で切断か構文エラーかを判別。
        truncated = _brace_balance_truncated(m.group(0))
    # cmd_496 AC3: 成功時は1000字で軽量化、解析失敗時は全文を残す(将軍が誤読しかけた観測の穴を是正・失敗箇所を隠さない)。
    _import_log({"fn": label, "llm": IMPORT_LLM, "ok": ok, "truncated": truncated, "ctx": str(ctx)[:300],
                 "out": (out or "") if not ok else (out or "")[:1000]})
    if not ok:
        if truncated:
            # cmd_496第3便 AC12: 分割起票(AC8)導入により単一呼出は_IMPORT_CHUNK_ROWS行以下でしか
            # 発生しなくなった(project_import_structureが超過分を先に分割するため)。ゆえにこの分岐へ
            # 来る途中切れは「行数」でなく「1行の記述量」が原因。旧文言「行数を分けて」は既に満たして
            # おる条件を繰り返すのみで次の一手にならぬ(tetsuo殿の「何度試しても同じ」の再来・軍師進言)。
            err = ("1行あたりの記述が長すぎるようです。お手数ですが、制作指示など長い列を短くして"
                   "お試しくだされ。")
        else:
            # cmd_496 AC4: 社員へは読める日本語で返す(機械のエラー文言は_import_logへ・隠さず全文=AC3)。
            err = "起票案の生成に失敗しました。お手数ですが、もう一度お試しくだされ(繰り返す場合は担当へご連絡を)。"
        return {"error": err, "raw": (out or "")[:500], "truncated": truncated}
    d.setdefault("project", {}); d.setdefault("shots", []); d.setdefault("tasks", [])
    return d


# cmd_496第2便 AC8: 分割起票の1チャンクあたり行数。憶測でなく実測で決定(N境界測定・192.168.44.139固定):
# 30/40行は複数回連続で安定 ok:true、70行は同一条件で成功/途中切れが揺れ(70行=不安定境界)、
# 85/100行は再現して途中切れ(truncated:true)。実データは合成データより1行の記述が長くなり得るため
# 安定圏(30〜40)からさらに余裕を取り、30行=25行の下限余裕を持たせてN=25とした。
_IMPORT_CHUNK_ROWS = 25


def _split_grid_rows(grid_text, chunk_rows=_IMPORT_CHUNK_ROWS):
    """グリッド(TSV/CSV相当)をヘッダー行+chunk_rows行ずつに分割する。
    ヘッダー行(先頭行)は各チャンクに複製して付け、LLMが列の意味を毎回解釈できるようにする。"""
    lines = (grid_text or "").splitlines()
    if not lines:
        return [grid_text or ""]
    header, body = lines[0], lines[1:]
    if len(body) <= chunk_rows:
        return [grid_text]
    chunks = []
    for i in range(0, len(body), chunk_rows):
        part = body[i:i + chunk_rows]
        chunks.append("\n".join([header] + part))
    return chunks


def _merge_import_proposals(results):
    """cmd_496第2便 AC8: 分割起票の結果を機構で結合する。
    project は最初に取れたものを採用(先頭チャンクにヘッダー相当の情報が集まりやすい)。
    shots は code で重複排除、tasks は単純連結(行が主体でありチャンク跨ぎの重複は起きない)。
    cmd_496第3便 AC11(欠陥A是正): 旧実装は `if not any_ok` — 一つでも成功すれば成功扱いとなり、
    一部チャンクが途中切れで失敗しても errors が握り潰され、社員には「N件の起票案」が何事もなく
    提示されていた(軍師実測: 30行を2分割し2番目が失敗→tasks=25件・欠落5件が画面に現れず)。
    失敗チャンクが1つでもあれば merged["_partial"] を必ず載せ、呼出側(preview)で握り潰せぬようにする。"""
    merged = {"project": {}, "shots": [], "tasks": []}
    seen_shot_codes = set()
    any_ok = False
    errors = []
    for r in results:
        if not isinstance(r, dict) or "error" in r:
            errors.append((r or {}).get("error", "unknown error"))
            continue
        any_ok = True
        if not merged["project"]:
            merged["project"] = r.get("project") or {}
        for s in (r.get("shots") or []):
            code = s.get("code")
            if code and code in seen_shot_codes:
                continue
            if code:
                seen_shot_codes.add(code)
            merged["shots"].append(s)
        merged["tasks"].extend(r.get("tasks") or [])
    if not any_ok:
        # 全チャンク失敗 → 単一呼出時と同じ形({"error":...})で返す(呼出側の分岐を増やさない)。
        return {"error": errors[0] if errors else "起票案の生成に失敗しました。", "raw": ""}
    if errors:
        # 部分失敗: 一部チャンクは成功したが、これを黙って"成功"として返してはならぬ(欠陥A本丸)。
        merged["_partial"] = {"failed_chunks": len(errors), "total_chunks": len(results),
                               "errors": errors}
    return merged


def project_import_structure(grid_text, hint=""):
    """Excel 由来グリッド → 新規PJ＋shot/task の構造化提案(JSON)。視認系と同じく Sonnet で精度を出す。
    cmd_496第2便 AC8: グリッドが _IMPORT_CHUNK_ROWS 行を超える場合は分割起票(複数回LLM呼出→結合)する
    (恒久策・途中切れの再発防止)。"""
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
    chunks = _split_grid_rows(grid_text)
    if len(chunks) == 1:
        user = f"ヒント(利用者記入): {hint}\n\n--- グリッド(Excel抽出) ---\n{chunks[0][:12000]}"
        return _import_json("structure", sysp, user, "hint=" + (hint or ""))
    results = []
    for idx, chunk in enumerate(chunks):
        user = (f"ヒント(利用者記入): {hint}\n"
                f"(注: 元グリッドを{len(chunks)}分割した{idx+1}/{len(chunks)}番目。ヘッダー行は共通・分割内で完結して処理せよ)\n\n"
                f"--- グリッド(Excel抽出・分割{idx+1}/{len(chunks)}) ---\n{chunk[:12000]}")
        results.append(_import_json("structure", sysp, user,
                                     f"hint={hint or ''} chunk={idx+1}/{len(chunks)}"))
    return _merge_import_proposals(results)


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
            _thr, _seedn = _partition_dm_threads(d.get("threads", []) or [], uid)   # seed除外(一本の経路・件数ログ)
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
            # Nibu確定2026-07-15: 280xxx/多人数帯とも実業務DMで真実源=Calendarは正常。「出所確認中」格下げは撤去し
            # 通常の新着DMとして述べる(Scoreで見えぬのは/messagesが当事者スレッドのみ表示する仕様=未同期でない)。
            fact_line += f"新着未読DMが{unread_n}件。"
    _al = "未取得" if not task_ok else ("ゼロ" if task_n == 0 else "あり")
    def _gen_greet():
        return strip_think(llm_text(
            "あなたは社内の伴走AI『Casper』。殿への開門の『枕』(挨拶＋気の利いた一言)を1文で。"
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
    opener = re.sub(r"[0-9０-９]+\s*件", "", opener or "").strip()   # 安全網: 盛られた"N件"のみ剥がす('件'必須で
    # C03/Number i/2026年 等の固有名の数字は保護・Fable指摘)。裸の数字は害薄ゆえ触らない(件数は別途機構が述べる)。
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
        # Fable処方: 出所未確認のDMは"確定新着トップ"でなく格下げ見出し＋裏どり導線(クリックで元スレッド)。
        # Nibu が真実源(is_seed/実DMソース)を確定させたら「💬新着DM」に戻す。
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
SEIRI_DIR = os.path.join(pack_paths.VAULT, "60_projects")


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


# 貼られた Aurora 資料URL(base/doc/{slug})。host は殿の環境で揺れるゆえ host は問わず /doc/ の形で見る。
_AURORA_URL_RE = re.compile(r"https?://[^\s、。]+?/doc/[^\s、。]+", re.I)
_AURORA_URL_MEMO = {}                                     # url -> material(取得成功のみ記憶・失敗は都度やり直す)


# ── 資料の錨(pin): 一度名指された資料を turn を跨いで保つ ─────────────────
# 【殿御下命2026-08-27】実害(2026-08-27 14:18〜15:04): kiyotomo殿が資料URLを貼り
# 「変更したい」→「追加」→「BOKAN 担当事項のところに以下追加」と**三turnかけて**頼まれた。
# ★URLを貼った turn は本文が入る(ctx_len 6922)。だが次の turn では消える(ctx_len 2909)。
#   `aurora_url_digest` は**その発話にURLが在る時しか発火せぬ**からである。
#   本文を失ったモデルは、記憶から議事録を**一から捏造**して「修正後の全文」に据えた
#   (実測: 実在せぬ参加者「武井/rui」、実在せぬ節「フェーズ1(レイアウト/アニメーション)」)。
#   承認されておれば、**本物の資料が捏造で丸ごと上書きされていた**。
# ★資料の修正は必ず複数 turn にまたがる(貼る→何を直すか→書け)。
#   1 turn しか生きぬ紐付けは、修正という仕事に対して構造的に短すぎる。
_AURORA_PIN = {}                       # key -> {doc_id,title,slug,material,ts}
_AURORA_PIN_TTL = int(os.environ.get("CASPER_AURORA_PIN_TTL", "1800"))   # 30分
# 錨を外す意図(別の資料・新規作成へ移る)。★人が明示した時だけ外す。
_AURORA_PIN_RELEASE_RE = re.compile(r"(新規|新しく|新しい|別の資料|別の文書|違う資料|もう[1一]つ|new doc)")


def aurora_pin_key(thread, who):
    """錨の鍵。thread が在ればそれ、無ければ session。
    ★thread だけを鍵にすると、thread を持たぬ経路で錨が一切効かぬ(実測: 8/26 のカードは
      thread=None であった)。session へ落として必ず鍵が立つようにする。"""
    t = str(thread or "").strip()
    if t and t.lower() != "none":
        return "th:" + t
    return "sid:" + str((who or {}).get("sid") or "")


def aurora_pin_set(key, ref, material=""):
    if not key or not ref or not ref.get("doc_id"):
        return
    _AURORA_PIN[key] = {"doc_id": ref["doc_id"], "title": ref.get("title", ""),
                        "slug": ref.get("ref", ""), "material": material or "",
                        "ts": time.time()}
    if len(_AURORA_PIN) > 200:                       # 際限なく溜めぬ(古い順に落とす)
        for k in sorted(_AURORA_PIN, key=lambda x: _AURORA_PIN[x]["ts"])[:100]:
            _AURORA_PIN.pop(k, None)


def aurora_pin_get(key):
    """生きている錨を返す。期限切れは畳んで None(『無い』と『古い』を混ぜぬ)。"""
    p = _AURORA_PIN.get(key)
    if not p:
        return None
    if time.time() - p.get("ts", 0) > _AURORA_PIN_TTL:
        _AURORA_PIN.pop(key, None)
        return None
    return p


def aurora_pinned_digest(key, query):
    """発話にURLが無くとも、錨が生きておればその資料を注入する。

    ★これが無いと『貼る→追加→書け』の二手目以降で本文が消え、モデルが記憶から作文する。
    ★『新規/別の資料』と人が明示した時は錨を外す——勝手に前の資料へ吸い寄せぬ。
    """
    if _AURORA_URL_RE.search(query or ""):
        return ""                                    # URLが在る turn は本家(aurora_url_digest)が出す
    if _AURORA_PIN_RELEASE_RE.search(query or ""):
        _AURORA_PIN.pop(key, None)
        return ""
    p = aurora_pin_get(key)
    if not p:
        return ""
    out = ("\n\n## 【いま扱っている Aurora 資料(機構が保持・これが一次資料)】\n"
           f"doc_id: {p['doc_id']}\n題: {p.get('title') or '(無題)'}\n")
    if p.get("material"):
        out += p["material"] + "\n"
        out += ("**上が現在の全文である。**修正を頼まれたら、この全文を土台に直した"
                "**全文**を body に入れて aurora_append を呼べ(doc_id は上の値)。\n"
                "★**記憶から議事録を書き起こすな。** 上に無い参加者・節・決定事項を足せば、"
                "承認された瞬間に本物の資料がその捏造で丸ごと置き換わる。\n")
    else:
        out += ("**本文は取得できておらぬ。**中身を語るな。修正が要るなら本文を取り直せ。\n")
    out += "★呼んでも承認カードが出るだけで、押されるまでは書き込まれておらぬ。完了を断ずるな。\n"
    return out


def aurora_url_digest(query, pin_key=None):
    """【貼られた資料は機構が取りに行く】殿が Aurora の資料URLを貼ったなら、その本文を注入する。
    qwen の tool 選択(aurora_get)に委ねると、URLを渡されても読まずに周辺を作文する——実測
    2026-07-27 19:04: 19ステータス定義の資料URLを渡されたのに Score のタスク一覧を並べ、次には
    『確定事項をお知らせください』と、既に示されたものを問い返した(殿御指摘「理解していない」)。
    掟: 識別子は生成でなく決定的機構で選ぶ。"""
    m = _AURORA_URL_RE.search(query or "")
    if not m:
        return ""
    u = m.group(0).rstrip("　 ")
    if u in _AURORA_URL_MEMO:
        # ★memoで早戻りする時も錨は張り直す(memoは本文の再取得を省くためのもので、
        #   錨を張らぬ理由にはならぬ。ここを飛ばすと二度目のURL貼付で錨が立たぬ)。
        try:
            _r2 = aurora_doc_ref(u)
            if _r2 and _r2.get("found"):
                aurora_pin_set(pin_key, _r2, material=_AURORA_URL_MEMO[u])
        except Exception:
            pass
        return _AURORA_URL_MEMO[u]
    try:
        r = seiri_aurora_fetch(u)
    except Exception as e:
        r = {"ok": False, "error": str(e)[:120]}
    if not r.get("ok"):
        # 取得できぬことを黙らず告げる(読んだ顔で作文させぬ・失敗とゼロを別の出口へ)。
        return ("\n\n## 【貼られたAurora資料: 取得できず】\n"
                f"URL: {u}\n理由: {r.get('error')}\n"
                "**この資料を読めていない。読んだ前提で内容を語るな。**"
                "取得できなかった旨を述べ、本文の貼付か別URLを願え。")
    out = ("\n\n## 【貼られたAurora資料(機構が取得・これが一次資料)】\n"
           f"URL: {u}\n{r.get('material')}\n"
           "**この資料を読んだ上で答えよ。既に示された内容を『お知らせください』と問い返すな。**"
           "資料に書かれていないことは、書かれていないと述べよ(補完で埋めるな)。")
    if r.get("coverage", {}).get("insufficient"):
        out += "\n※図が読めていない(画像主体)。読めた範囲を明示し、足りぬ点は正直に申せ。"
    # 【殿御下命2026-08-26】直す手立てを同じ便で渡す。
    # 実害(18:22:55): 本文は注入されていたのに doc_id が無く、Casperは「編集機能を持っていません」と
    # 答えた(=持っているのに無いと言う嘘)。二分後には「削除しました」と逆の嘘をついた。
    # ★『読める』と『直せる』を別々に渡すと、片方だけ見て機構が揺れる。鍵は資料と同じ便で渡す。
    _ref = aurora_doc_ref(u)
    if _ref and _ref.get("found") and _ref.get("doc_id"):
        # ★錨を据える。これが無いと次の turn で本文も doc_id も消え、モデルが作文する。
        aurora_pin_set(pin_key, _ref, material=r.get("material") or "")
        out += (f"\n\n### この資料は直せる(doc_id={_ref['doc_id']})\n"
                "修正を頼まれたら **aurora_append** を呼べ(doc_id は上の値をそのまま使う)。"
                "**『編集機能が無い/できない』とは言うな——道具は在る。**\n"
                "★aurora_append は中身を**丸ごと入れ替える**。body には抜粋でなく"
                "**修正後の全文**を入れよ(一部だけ渡すと資料の残りが消える)。\n"
                "★呼ぶと承認カードが出る。押されるまでは書き込まれておらぬゆえ、"
                "『削除しました/直しました』と完了を断ずるな。")
    elif _ref and not _ref.get("found"):
        out += ("\n\n### この資料は台帳で特定できておらぬ\n"
                f"名指し: {_ref.get('ref')}。**doc_id が解けておらぬゆえ修正は掛けられぬ。**"
                "勝手に新規作成へ倒すな。特定できぬ旨を述べ、資料の題か検索語を願え。")
    _AURORA_URL_MEMO[u] = out
    return out


_THREAD_RULES_FILE = os.path.join(HERE, "thread_rules.jsonl")
_THREAD_RULES = None                                      # thr -> [規則text] (遅延ロード)


def _thread_rules_load():
    """殿が会話中に示された規則を復元(プロセス再起動/auto-reload を越えて残す)。"""
    global _THREAD_RULES
    if _THREAD_RULES is not None:
        return _THREAD_RULES
    _THREAD_RULES = {}
    try:
        for ln in open(_THREAD_RULES_FILE, encoding="utf-8"):
            if not ln.strip():
                continue
            d = json.loads(ln)
            _THREAD_RULES.setdefault(d.get("thread") or "", [])
            if d.get("text") not in _THREAD_RULES[d["thread"]]:
                _THREAD_RULES[d["thread"]].append(d["text"])
    except Exception:
        pass
    return _THREAD_RULES


def thread_rules_observe(thr, query):
    """【示された規則は履歴の予算に委ねぬ】殿が規則を述べたなら機構が控える。
    実測2026-07-27 19:28: 権限の定義は19:17に確かに示されたのに、履歴予算(4500字)から押し出され、
    『この表に権限も』へ『権限のデータが見つかりません』と答えた。会話の窓は有限ゆえ、
    規則だけは窓の外へ出しても消えぬ場所へ移す(服従でなく機構で保つ)。"""
    q = (query or "").strip()
    if not thr or len(q) < 20 or not _looks_declarative(q):
        return
    st = _thread_rules_load().setdefault(thr, [])
    t = q[:1500]
    if t in st:
        return
    st.append(t)
    del st[:-8]                                           # 直近8件まで(古い規則は新しいものに置き換わる想定)
    try:
        with open(_THREAD_RULES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread": thr, "text": t}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def thread_rules_digest(thr):
    """控えた規則を、履歴とは別枠で必ず注入する(小さいゆえ予算を圧迫せぬ)。"""
    st = _thread_rules_load().get(thr or "") or []
    if not st:
        return ""
    return ("\n\n## 【この会話で殿が示された規則(機構が保持・会話履歴から溢れても消えぬ)】\n"
            + "\n---\n".join(st)
            + "\n**表や説明を作る時は、まずここから値を採れ。**"
            "『見つかりません/ご指示ください』と問い返す前に、ここに書かれていないかを必ず確かめよ。")


_DEIXIS_TABLE_RE = re.compile(r"(この|その|上の|先の|さっきの|今の|前の)(表|テーブル|一覧|リスト|まとめ)", re.I)


def _deixis_table_rows(query, convo):
    """『この表』が指す md 表の行だけを返す(digest と DM本文の接地で同じ抽出を使う=単一機構)。"""
    if not _DEIXIS_TABLE_RE.search(query or ""):
        return ""
    for m in reversed(list(convo or [])):
        if m.get("role") != "assistant":
            continue
        rows = [ln for ln in str(m.get("content") or "").split("\n") if re.match(r"\s*\|.*\|\s*$", ln)]
        if len(rows) >= 3:                                # 見出し＋区切り＋データ1行以上=表と見る
            return "\n".join(rows[:40])
    return ""


# DM本文に指示語だけが残り、答えるに要る材料が無い状態を検出する語。
_DM_DEIXIS_RE = re.compile(r"(先ほど|さきほど|先般|この表|上記|上の表|先の表|前回の表|整理した表|"
                           r"この一覧|添付の表|例の表)", re.I)


def _ground_dm_body(body, table_md):
    """【相手は殿との会話を見ておらぬ】DM本文が『先ほどの表について』と指示語で済ませ、当の表を含まぬなら
    受け手には答えられぬ問いになる。殿御指摘2026-07-27「DM内容が微妙」の芯はここ:
    宛先は kiyotomo/tetsuo であって、その表は殿とCasperの間にしか無かった。機構が材料を添える。"""
    b = str(body or "")
    if not table_md or not b:
        return b
    if re.search(r"\|[^\n]*\|", b):                       # 既に表を含む=材料は足りている
        return b
    if not _DM_DEIXIS_RE.search(b):                       # 指示語で済ませていない=そのままで通じる
        return b
    # 仮置き(想定/推測)の表を社外・社内へそのまま出せば、仮が確定として伝わる。実測2026-07-28: 『想定権限』
    # と自ら断った表が、断り書きを失ったまま tetsuo殿へ送られた。表が仮なら、仮と明記して添える。
    _hedge = bool(re.search(r"想定|推測|仮定|仮置|かもしれ|と思われ|暫定", table_md))
    head = ("【ご確認いただきたい一覧（※当方の仮置きにござる。正否のご確認をお願いいたす）】"
            if _hedge else "【ご確認いただきたい一覧】")
    return b.rstrip() + "\n\n" + head + "\n" + table_md


def deixis_table_digest(query, convo):
    """【『この表』は直前の自分の応答の表である】指示語を機構で接地する。
    実測2026-07-27 19:28(殿御指摘「もう少し理解がいる」): 一手前に自ら9ステータスの表を出したのに
    『この表の中に権限の表記もお願い』へ『どの表を指すか明確ではありません』と問い返した。
    履歴には在ったが、弱qwenがRAG雑音(DBM2レガシー等)に引かれて自分の直前の出力を見失った。
    ゆえ機構が「これがその表だ」と名指して渡す(推測させず、問い返させぬ)。"""
    rows = _deixis_table_rows(query, convo)
    if not rows:
        return ""
    return ("\n\n## 【『この表』が指すもの＝直前の自分の応答の表(機構が特定)】\n" + rows
            + "\n**これが『この表』である。どの表かを問い返すな。**"
            "この表を土台に、求められた列/行を足して**表全体を作り直して**返せ。"
            "列を足す元の値は、この会話で殿が示された内容から採れ。"
            "会話に無い値は推測で埋めず、その欄は『—(未確定)』とし、何が足りぬかを1文で添えよ。")


def seiri_vault_material(project_name, cap=12000):
    """PJ名で vault を横断し、既存の議事録/asset影武者/DB書庫/人物 等から素材を自動収集。
    60_projects(自らの結晶化=citogenesis回避)と汎用短名は除外。総量を上限で抑える。返り値=(素材text, 出典数)。"""
    if not project_name or len(project_name) < 3:
        return "", 0
    import glob
    vault = os.path.join(pack_paths.VAULT)
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
    sysp = ("あなたは社内の伴走AI『Casper』。完了プロジェクトの『整理(offboarding)』の最中。"
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
        if casper_embed: casper_embed.reindex_async("knowledge_write")   # cmd_498: 意味索引も非同期で追従
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
        if casper_embed: casper_embed.reindex_async("knowledge_write")   # cmd_498: 意味索引も非同期で追従
    except Exception:
        pass
    return {"ok": True, "note": f"50_asset_shadows/asset_{slug}.md"}


def graph_data():
    """vault のノード(ノート)＋エッジ([[link]]) を抽出。"""
    import glob
    V = os.path.join(pack_paths.VAULT)
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
    自社 → 各部門(pack org 定義) → 各領域(hp6準拠) → 社員(役割で配属)。
    社員配属は名鑑(役割)からの推定。殿の訂正で ORG 定義を直すだけで反映される。"""
    # 組織図は pack から読む(M5: engine の構造焼き付けを解消)。pack に org 無ければ vault グラフのみ。
    import pack_config as _pc
    _org = _pc.get("org", {}) or {}
    ROOT = _org.get("root", "")
    ORG = _org.get("divisions", {}) or {}
    MEMBERS = [tuple(m) for m in (_org.get("members", []) or [])]
    EXTERNAL = [tuple(e) for e in (_org.get("external", []) or [])]
    nodes, links = [], []
    nmap, seen_e = {}, set()

    def addnode(n):
        if n["id"] not in nmap:
            nmap[n["id"]] = n; nodes.append(n)
        return nmap[n["id"]]

    def addlink(s, t):
        if s != t and (s, t) not in seen_e and (t, s) not in seen_e:
            seen_e.add((s, t)); links.append({"source": s, "target": t})

    # ① 組織骨格: root → 部門 → 領域(root は pack 由来)
    if ROOT:
        addnode({"id": ROOT, "label": ROOT, "group": "root"})
    for div, areas in ORG.items():
        addnode({"id": div, "label": div, "group": "division"})
        if ROOT:
            addlink(ROOT, div)
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
    V = os.path.join(pack_paths.VAULT)
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
    V = os.path.join(pack_paths.VAULT)
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


# ── M4 commit 共通: outbox 監査台帳（Fable監査2026-07-17 で畳む）──
# 【enforcement の唯一のチョークポイントは casper_authority.allowed()】——各 execute() が必ず呼ぶ。
# outbox は**監査台帳＋冪等キー**であって権限ゲートではない（audience=カード表示対象 と allowed=実行可能者 は別集合ゆえ、
# 台帳の approve を権限判定に使うと director 等の正当な書込を弾く）。台帳整合のため、実行者を必ず自レコードの
# audience に含め（approve が None で proposed に固着するバグを塞ぐ）、propose→approve→executing まで進めて返す。
def _m4_ledger_open(verb, tool, args, actor_uid, summary, target, snap):
    """outbox に verb 記録を起こし approve→executing まで進める。返り rec or None。actor は必ず audience に含める。"""
    if not casper_outbox:
        return None
    try:
        aud = casper_authority.audience_for(verb, target or {}, snap) if casper_authority else []
        aud = sorted(set(aud) | ({str(actor_uid)} if actor_uid else set()))   # 実行者を必ず含める＝台帳整合
        rec = casper_outbox.propose(tool, args, str(actor_uid or ""), summary, verb=verb, audience=aud)
        casper_outbox.approve(rec["id"], uid=actor_uid)
        casper_outbox.mark_executing(rec["id"])
        return rec
    except Exception:
        return None


def _m4_ledger_close(rec, ok, info):
    """実行結果を台帳へ確定（sent/failed）。台帳は事実の記録＝enforcement はしない。"""
    if not (rec and casper_outbox):
        return
    try:
        (casper_outbox.mark_sent if ok else casper_outbox.mark_failed)(rec["id"], info)
    except Exception:
        pass


_ROUTE_X_GUARD_OVERRIDE_FILE = "/tmp/casper_gate_route_x_guard_override.json"
_ROUTE_X_GUARD_OVERRIDE_MAX_AGE_SEC = 300  # 5分(cmd_488 subtask_488_impl4: 古いオーバーライドは無視)


def _route_x_guard_enabled():
    """経路X fail-closedガードの有効/無効(cmd_488 subtask_488_impl3・impl4)。
    デフォルトはTrue(現状挙動・本番は常に有効)。/tmp配下のオーバーライドファイル
    (scripts/ 配下ではないためsupervisorのmtime監視の対象外=書いても自動リロードを誘発しない)が
    存在し {"disabled": true, "ts": <epoch>} の場合のみ無効化する。突然変異検証用の注入経路であり、
    本番のchat_server.py自体は一切書き換えない。読めない/壊れている・tsが無い・古すぎる(5分超)場合は
    安全側(有効)に倒す(掟: 緑ゲートに嘘は映らぬ——ゲートがSIGKILL等でfinallyに到達せず後始末に
    失敗しても、本番のガードが外れたまま気付かれずに残らないようにする)。
    無効化が成立した瞬間は必ず警告をstderrへ落とす(サイレントな無防備状態を残さない)。"""
    try:
        with open(_ROUTE_X_GUARD_OVERRIDE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("disabled", False):
            return True
        ts = data.get("ts")
        age = time.time() - float(ts)
        if age > _ROUTE_X_GUARD_OVERRIDE_MAX_AGE_SEC:
            print(f"⚠ route-X guard override は古すぎる(age={age:.0f}s > "
                  f"{_ROUTE_X_GUARD_OVERRIDE_MAX_AGE_SEC}s)ため無視し、ガードを有効側へ倒す。",
                  file=sys.stderr)
            return True
        print(f"⚠ route-X fail-closedガードが無効化されている(override file={_ROUTE_X_GUARD_OVERRIDE_FILE}, "
              f"age={age:.0f}s)。突然変異検証以外でこれが出ている場合は要調査。", file=sys.stderr)
        return False
    except Exception:
        return True


def _notifications_for(uid):
    """/api/notifications の中身(cmd_505)。pending(uid)が空の時に限りcompute→storeを挟んでから
    改めてpendingで読み直す(保険=案1改)。computeの戻りを直接返してはならない——
    storeを経由しないとdedup_key重複防止/既読管理(pending側でread=Falseのみ返す造り)が効かない。
    夜間巡回(_recent_uids拡張・案2)で大半は既にpending命中し、ここは「まだ一度も積まれておらぬ者が
    初めて開いた時」だけ通る(AC5: 画面が待たされぬのはこの保険が滅多に走らぬから)。"""
    if not casper_notify:
        return []
    items = casper_notify.pending(uid)
    if items:
        return items
    try:
        nt = casper_notify.compute(uid)
        if nt:
            casper_notify.store(uid, nt)
    except Exception:
        pass
    return casper_notify.pending(uid)


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
        elif self.path == "/api/push/key":              # M3 Web Push: VAPID公開鍵(applicationServerKey)
            self._json({"key": casper_push.vapid_public_b64() if casper_push else ""})
            return
        elif self.path == "/api/push/prefs":            # M3③: 型別通知ON/OFF設定を返す(本人)
            who = identify(self); uid = who.get("uid")
            self._json(casper_push.get_prefs(uid) if (casper_push and uid) else {})
            return
        elif self.path == "/api/notifications":         # M3: 先回り通知(未読)。本人のもののみ。
            who = identify(self)
            uid = who.get("uid")
            try:
                items = _notifications_for(uid) if uid else []
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
        elif self.path in ("/casper-ca.crt", "/casper-ca.pem"):   # ローカルCA証明書を配布(端末に信頼インストール→携帯Web Push可)
            cp = os.path.join(os.path.expanduser("~"), ".config", "casper", "casper_ca.pem")
            if os.path.exists(cp):
                b = open(cp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                # ★Content-Disposition: attachment を付けぬこと(殿御指摘 2026-08-07)。
                # iOS は attachment を「ダウンロード」として扱い、★プロファイルとして取り込まぬ。
                # 実害: kiyotomo 殿が「プロファイルをインストールできません」「許可するがエラー」と
                # 20分ほど立ち往生された(実ログ 2026-08-07 15:03-15:16)。
                # inline で返せば Safari が構成プロファイルとして受け取り、設定アプリへ渡る。
                self.send_header("Content-Disposition", "inline; filename=casper-ca.crt")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()
        elif self.path == "/manifest.json":         # PWA manifest(iOS: ホーム画面追加→standalone→Web Push可)
            man = json.dumps({
                "name": "Casper", "short_name": "Casper", "start_url": "/", "scope": "/",
                "display": "standalone", "background_color": "#0b1220", "theme_color": "#0b1220",
                "icons": [{"src": "/icon", "sizes": "192x192", "type": "image/jpeg"},
                          {"src": "/icon", "sizes": "512x512", "type": "image/jpeg"}]
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Content-Length", str(len(man)))
            self.end_headers()
            self.wfile.write(man)
        elif self.path == "/sw.js":                 # M3 Web Push: Service Worker(root scope で配信=全体を制御)
            swp = os.path.join(HERE, "sw.js")
            if os.path.exists(swp):
                b = open(swp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Service-Worker-Allowed", "/")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(b)))
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
            _ctx = _load_context()                                  # C(逆混入畳み)検証ゲート: engine_policy.md が受け皿として載っているか
            # 「未確認をtrueと名乗るな」(Fable): ファイル存在でなく、policy が実際に core へ載った sentinel で判定。
            # 在るが読めぬ時は "digest" に倒れ、build_brain_digest の fail-safe が policy を出し続ける=窓ゼロ。
            _pol = "engine" if "回答方針" in _ctx.get("core", "") else "digest"
            _fresh = casper_embed.ensure_fresh() if casper_embed else {}   # cmd_498: 観測時に古ければその場で是正
            self._json({"ok": True, "model": active, "backend": BACKEND,
                        # 雲に座っている間、ローカル分類器を呼ばずに済ませた回数(殿御下命2026-08-24)
                        "classifier_suppressed": _OLLAMA_JSON_SUPPRESSED,
                        "policy": _pol, "ctx_sections": len(_ctx.get("sections", [])),
                        "ctx_core_len": len(_ctx.get("core", "")), "index_freshness": _fresh})
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
        if self.path == "/api/push/subscribe":          # M3 Web Push: ブラウザ購読を登録(本人のみ)
            n = int(self.headers.get("Content-Length", 0) or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            uid = who.get("uid")
            sub = req.get("subscription") or req
            if not (who.get("authed") and uid and casper_push and sub.get("endpoint")):
                self._json({"ok": False, "reason": "unauth or no endpoint"}); return
            try:
                self._json({"ok": True, "count": casper_push.add_sub(uid, sub)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if self.path == "/api/push/prefs":              # M3③: 型別通知ON/OFF設定を保存(本人)
            n = int(self.headers.get("Content-Length", 0) or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self); uid = who.get("uid")
            if not (who.get("authed") and uid and casper_push):
                self._json({"ok": False}); return
            self._json({"ok": True, "prefs": casper_push.set_prefs(uid, req.get("prefs") or req)})
            return
        if self.path == "/api/aurora/upload":           # ファイル(HTML)を Aurora 資料としてアップ(ドロップ→ツール選択)
            n = int(self.headers.get("Content-Length", 0) or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            uid = who.get("uid")
            if not (who.get("authed") and uid and casper_aurora and casper_aurora.configured()):
                self._json({"ok": False, "error": "未認証 or Aurora未接続"}); return
            title = str(req.get("title") or req.get("filename") or "無題").strip()
            html = req.get("content") or ""
            project = str(req.get("project") or "社内").strip() or "社内"
            tags = req.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in re.split(r"[,\s、]+", tags) if t.strip()]
            if not str(html).strip():
                self._json({"ok": False, "error": "本文が空"}); return
            # fail-closed(cmd_488): クライアント側で弾かれるはずのバイナリが万一届いた場合の防壁。
            # f.text()のUTF-8デコードは不正バイトをU+FFFDへ置換するため、多数含む本文は
            # 「バイナリを文字列として読んだ痕跡」であり、黙って保存せず正直に断る。
            if str(html).count("�") >= 3 and _route_x_guard_enabled():
                self._json({"ok": False, "error": "本文にデコード不能な文字が多数含まれるため保存を中止しました(バイナリ形式の可能性)"}); return
            # cmd_488 subtask_488_impl4: 回帰ゲート専用のdry_run。ガード判定(上のif文)を通過した後、
            # casper_aurora.create()へ到達する直前で止める(=本番Auroraへ書き込まない)。
            # ガードで拒否される場合は上のreturnで既に抜けているため、dry_runでも「拒否される」ことは
            # 通常経路と同じ挙動で検証できる。ここに到達した=「ガードで弾かれずに先へ進もうとした」の証明。
            if bool(req.get("dry_run")):
                self._json({"ok": True, "dry_run": True, "title": title}); return
            try:
                # 投稿者は数値uidでなく★ユーザー名を渡す(殿御下命 2026-07-30)。
                # 数値のまま渡すと Aurora の一覧に "31" や、解決できぬ時は "9999999" と出て
                # 誰が上げたか判らぬ(実害: 9999999 名義の資料が34件積まれた)。
                # 他の3経路(報告書ビルダー L7442/L7461・承認カード実行 L7964/L7966)は既に
                # uname を渡しており、本経路(ドロップ→Aurora)だけが数値のまま残っていた。
                # 名前が引けぬ時のみ uid へ退避する(_uid_to_name は未知uidをそのまま返す)。
                uname = _uid_to_name(uid) or str(uid)
                res = casper_aurora.create(title, html, author_id=uname, project=project, tags=tags)
                d = json.loads(res) if isinstance(res, str) and res.strip().startswith("{") else (res or {})
                slug = (d.get("slug") or d.get("id") or "") if isinstance(d, dict) else ""
                url = (casper_aurora.doc_base() + "/doc/" + slug) if slug else ""
                self._json({"ok": bool(slug), "url": url, "title": title})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
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
                    if intent == "aurora":
                        ext = os.path.splitext(fn)[1].lower()
                        src = os.path.join(ASSETS_DIR, "upl_" + who["sid"] + ext)
                        self._json(uploader_to_aurora(src, note, fn, uid))
                        return
                    elif intent in ("daily", "memo", "record"):
                        # 右脳vault に即保存(Calendar 権限不要)
                        # resolve が ASSETS_DIR/upl_{sid}{ext} に保存済の実体を、feed_save が参照する
                        # ASSET_FILES/{safe_filename} へ複製する(両者はディレクトリもファイル名も別物ゆえ、
                        # 複製せねば feed_save は本文抽出できず「保存した」と偽の成功報告になる)。
                        ext = os.path.splitext(fn)[1].lower()
                        src = os.path.join(ASSETS_DIR, "upl_" + who["sid"] + ext)
                        safe_fn = re.sub(r"[^\w.\-]", "_", os.path.basename(fn))[:60] or fn
                        if os.path.exists(src):
                            os.makedirs(ASSET_FILES, exist_ok=True)
                            with open(src, "rb") as _sf, open(os.path.join(ASSET_FILES, safe_fn), "wb") as _df:
                                _df.write(_sf.read())
                        out = feed_save(safe_fn, note or "(daily 記録)",
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
                partial = prop.get("_partial") if isinstance(prop, dict) else None
                resp = {"ok": ("error" not in prop) and not partial, "proposal": prop,
                        "counts": {"shots": len(prop.get("shots", []) or []),
                                   "tasks": len(prop.get("tasks", []) or [])},
                        "grid_preview": (grid or "")[:600]}
                if partial:
                    # cmd_496第3便 AC11(欠陥A是正): 一部成功を黙って"成功"として返してはならぬ。
                    # ok:false とし、欠落を社員へ明示する文言を message に載せる。
                    resp["message"] = (f"{partial['total_chunks']}分割中{partial['failed_chunks']}個が失敗し、"
                                        "一部の行が欠けております。お手数ですが、失敗箇所の行数を減らして"
                                        "お試しくだされ。")
                self._json(resp)
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
        if self.path == "/api/assign/commit":          # M4 Phase1: アサイン確定→W2ガード付きexecute(admin=MCP即時/pm・lead=BFF待ち)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_assign and casper_authority):
                self._json({"ok": False, "error": "assign機構が無効にござる"}); return
            tid = str(req.get("task_id") or ""); au = str(req.get("assignee_uid") or "")
            if not tid or not au:
                self._json({"ok": False, "error": "task_id/assignee_uid が要りまする"}); return
            if not WRITE_TOKEN:
                self._json({"ok": False, "error": "write token 未設定のため実行できませぬ(ニブ殿のtoken待ち)"}); return
            snap = casper_authority._load_snapshot()

            def _read(t):
                try:
                    r = casper_tools._get(f"/tasks/{t}") if casper_tools else None
                    return r if (isinstance(r, dict) and r.get("id") is not None) else None
                except Exception:
                    return None

            def _write(t, u, actor):
                # MCP update_task: assignee は **username**（uidでなく）・actor_id 必須。uid→username へ解決して書く。
                uname = _uid_to_name(u)
                if not uname or uname == "?":
                    return False
                res = (casper_mcp.call_tool("update_task", {"task_id": int(t), "assignee": uname, "actor_id": int(actor)},
                                            token=WRITE_TOKEN, actor=actor) if casper_mcp else "(MCP無効)")
                try:
                    return bool(json.loads(res).get("ok"))
                except Exception:
                    return False
            # 監査台帳(W4冪等)。enforcement は execute 内の allowed()。二重割当はW2(実行直前読み戻し)が防ぐ。
            cur0 = _read(tid)
            orec = _m4_ledger_open("assign", "assign_task", {"task_id": int(tid) if tid.isdigit() else tid, "assigned_to": au},
                                   who.get("uid"), f"アサイン: task {tid} → uid {au}",
                                   {"project_id": (cur0 or {}).get("project_id"), "assignee": ""}, snap)
            ok, info = casper_assign.execute(tid, au, who.get("uid") or "", snap=snap, live_read=_read, live_write=_write)
            _m4_ledger_close(orec, ok, info)
            name = _uid_to_name(au)
            msg = (f"{name} さんに割り当て申した。" if ok else
                   {"toctou_already_assigned": "その間に別の方が担当に入っておりました（二重割当を防ぎました）。画面を更新してくだされ。",
                    "toctou_status_moved": "その間に工程が進んでおりました。画面を更新してくだされ。",
                    "bff_wire_pending": "この権限での割当は Score 側の結線（Elvis殿）待ちにござる。今しばし。",
                    "not_allowed": "この操作の権限がございませぬ。",
                    "read_failed": "タスクの現状を読めませなんだ（時間をおいて再度）。",
                    }.get(info.split(":")[0], f"割り当てできませなんだ（{info}）。"))
            self._json({"ok": ok, "info": info, "message": msg, "assignee": name, "task_id": tid})
            return
        if self.path == "/api/status/commit":          # M4 Phase4: status更新確定→W2ガード付きexecute(納品/客先承認/対象外)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_status and casper_authority and casper_mcp):
                self._json({"ok": False, "error": "status機構が無効にござる"}); return
            verb = str(req.get("verb") or ""); tid = str(req.get("task_id") or "")
            if verb not in casper_status.STATUS_VERBS or not tid:
                self._json({"ok": False, "error": "verb/task_id が要りまする"}); return
            if not WRITE_TOKEN:
                self._json({"ok": False, "error": "write token 未設定のため実行できませぬ"}); return
            vdef = casper_authority.verbs().get(verb, {})
            # typed確認（omit・殿決定: 取り消しにくい操作は明示入力を要求）
            if vdef.get("confirm") == "typed" and str(req.get("confirm") or "").strip() != str(vdef.get("label") or "").strip():
                self._json({"ok": False, "error": f"確認のため「{vdef.get('label')}」と入力してくだされ"}); return
            snap = casper_authority._load_snapshot()

            def _read(t):
                try:
                    r = casper_tools._get(f"/tasks/{t}") if casper_tools else None
                    return r if (isinstance(r, dict) and r.get("id") is not None) else None
                except Exception:
                    return None

            def _write(t, to_status, actor):
                res = casper_mcp.call_tool("update_task", {"task_id": int(t), "status": to_status, "actor_id": int(actor)},
                                           token=WRITE_TOKEN, actor=actor)
                try:
                    return bool(json.loads(res).get("ok"))
                except Exception:
                    return False
            cur0 = _read(tid)
            evidence = req.get("evidence")
            _args = {"task_id": int(tid) if tid.isdigit() else tid, "status": vdef.get("to_status")}
            if evidence:
                _args["evidence"] = str(evidence)[:500]
            orec = _m4_ledger_open(verb, "update_task", _args, who.get("uid"), f"{vdef.get('label')}: task {tid}",
                                   {"project_id": (cur0 or {}).get("project_id"), "assignee": str((cur0 or {}).get("assigned_to") or "")}, snap)
            ok, info = casper_status.execute(verb, tid, who.get("uid") or "", snap=snap, evidence=evidence,
                                             live_read=_read, live_write=_write)
            _m4_ledger_close(orec, ok, info)
            msg = (f"{vdef.get('label')}いたした（{vdef.get('to_status')}）。" if ok else
                   {"evidence_required": "客先承認には根拠リンク（証跡）が要りまする。",
                    "already": "既にその状態にござる。",
                    "not_allowed": "この操作の権限、または現在の状態からは実行できませぬ。",
                    "read_failed": "タスクの現状を読めませなんだ。",
                    }.get(info.split(":")[0], f"実行できませなんだ（{info}）。"))
            self._json({"ok": ok, "info": info, "message": msg, "task_id": tid})
            return
        if self.path == "/api/minutes/commit":         # M4 Phase3: 議事録タスクの起票→bulk_create_tasks(tier≥lead)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_minutes and casper_mcp and casper_authority):
                self._json({"ok": False, "error": "起票機構が無効にござる"}); return
            items = req.get("tasks") or []
            if not items:
                self._json({"ok": False, "error": "起票するタスクを選んでくだされ"}); return
            if not WRITE_TOKEN:
                self._json({"ok": False, "error": "write token 未設定のため起票できませぬ"}); return
            uid = str(who.get("uid") or "")
            if not uid.isdigit():                      # Fable監査: actor_id=28 フォールバックを廃止。uid不明は起票させぬ(actor偽装防止)
                self._json({"ok": False, "error": "本人確認ができませぬ（起票には認証が要りまする）"}); return
            snap = casper_authority._load_snapshot()
            # Fable監査(危険D): 他5verbと同じく casper_authority.allowed("create_task") を通す(tier だけでなく scope/audience)。
            # 議事録のPJ(候補の project_id)に対する create_task 権限で判定＝他PJ議事録からの越境起票を防ぐ。
            _pid = next((str(it.get("project_id")) for it in items if it.get("project_id") is not None), "")
            _ok, _rsn = casper_authority.allowed("create_task", uid, {"project_id": _pid, "assignee": ""}, from_status="", snap=snap)
            if not _ok:
                _msg = {"tier_too_low": "タスク起票の権限がございませぬ（リード／PM以上）",
                        "out_of_scope": "この案件（PJ）はご担当の範囲外ゆえ起票できませぬ",
                        "snapshot_stale_admin_only": "権限情報が古く、今は管理者のみ起票できまする"}.get(_rsn.split(":")[0], f"起票できませぬ（{_rsn}）")
                self._json({"ok": False, "error": _msg}); return
            # bulk_create_tasks は project_id を各itemに持たず、shot(shot_code)→shot_id 自動解決＋type/assignee(username)/due/note。
            # 新規タスクには **shot が必須**（無いと Score が置けぬ＝殿指摘）。shot 未指定は起票せず理由を返す。
            payload, skipped = [], []
            for it in items:
                nm = str(it.get("name") or "").strip()
                if not nm:
                    continue
                shot = str(it.get("shot") or "").strip()
                if not shot:
                    skipped.append(nm + "（shot未指定）"); continue
                t = {"shot": shot, "note": nm}          # 議事録の一文は note に（作業名でなく指示ゆえ）
                typ = it.get("type")
                t["type"] = typ if typ in casper_minutes.SCORE_TYPES else "other"
                if it.get("assignee_uid"):
                    un = _uid_to_name(it["assignee_uid"])
                    if un and un != "?":
                        t["assignee"] = un              # assignee は username
                if it.get("due"):
                    t["due"] = it["due"]
                payload.append(t)
            if not payload:
                self._json({"ok": False, "error": "起票できるタスクがございませぬ（新規タスクには shot 指定が要りまする）",
                            "skipped": skipped}); return
            res = casper_mcp.call_tool("bulk_create_tasks", {"actor_id": int(uid), "tasks": payload},
                                       token=WRITE_TOKEN, actor=uid)
            try:
                rj = json.loads(res)
                ok = bool(rj.get("ok") or rj.get("created") or rj.get("created_count"))
            except Exception:
                rj = {"raw": str(res)[:300]}; ok = False
            _sk = ("／ shot未指定で見送り: " + "、".join(skipped)) if skipped else ""
            self._json({"ok": ok, "result": rj, "count": len(payload), "skipped": skipped,
                        "message": (f"{len(payload)}件のタスクを起票いたした。{_sk}" if ok else f"起票に失敗いたした（{str(res)[:150]}）")})
            return
        if self.path == "/api/thread/post":            # M4 末端①: 議事録FB等を task の SHOT スレッドへ投稿(get_task_thread→send_message thread_id)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_mcp and WRITE_TOKEN):
                self._json({"ok": False, "error": "投稿機構が無効にござる"}); return
            task_id = str(req.get("task_id") or "").strip()
            body = str(req.get("body") or "").strip()
            if not task_id.isdigit() or not body:
                self._json({"ok": False, "error": "task_id(数値)と本文が要りまする"}); return
            if len(body) > 4000:                       # 本文長ガード(Fable監査2026-07-23)
                self._json({"ok": False, "error": "本文が長すぎまする（4000字まで）"}); return
            # 【Fable監査2026-07-23 認可穴①の是正】autonomous(uid=101名義の自律投稿)は「なりすまし機構」ゆえ、
            # identify と同じく **loopback発 or host-secret一致** の内部機構からのみ許す。これが無いと bind=0.0.0.0
            # (LAN公開)上の第三者が autonomous:true を付けて uid101 名義で任意スレへ投稿できる(M2秘匿の再導入)。
            _cip = self.client_address[0] if getattr(self, "client_address", None) else ""
            _hsec = os.environ.get("CASPER_HOST_SECRET", "")
            _trusted = (_cip in ("127.0.0.1", "::1", "localhost")) or (bool(_hsec) and self.headers.get("X-Casper-Host-Secret", "") == _hsec)
            autonomous = bool(req.get("autonomous"))   # True=Casper自律投稿(uid=101名義・内部機構のみ)/既定=承認者本人名義(二値actor)
            if autonomous and not _trusted:
                self._json({"ok": False, "error": "自律投稿は内部機構からのみ許されまする（本人名義でお試しくだされ）"}); return
            uid = str(who.get("uid") or "")
            if not autonomous and not uid.isdigit():   # Fable監査と同旨: 本人不明は投稿させぬ(actor偽装防止)
                self._json({"ok": False, "error": "本人確認ができませぬ（投稿には認証が要りまする）"}); return
            actor = "101" if autonomous else uid
            # 【Fable監査2026-07-23 認可穴②の是正】人間名義(非autonomous)は兄弟endpoint同様 authority を通す。
            # task の project_id/assignee を読み、post_thread(scope=own: 担当本人＋PJのpm/lead＋director/admin)で判定
            # ＝認証はあるが認可なし=他PJスレへ無関係な者が投稿する穴 を塞ぐ。autonomousは trusted 内部機構ゆえ scope 判定を要さぬ。
            snap = casper_authority._load_snapshot() if casper_authority else None
            _task = None
            if casper_tools:
                try:
                    _t = casper_tools._get(f"/tasks/{task_id}")
                    _task = _t if (isinstance(_t, dict) and _t.get("id") is not None) else None
                except Exception:
                    _task = None
            if not autonomous:
                if casper_authority is None or _task is None:
                    self._json({"ok": False, "error": "タスクの現状を読めず、権限を確かめられませぬ"}); return
                _tgt = {"project_id": _task.get("project_id"), "assignee": str(_task.get("assigned_to") or "")}
                _ok, _rsn = casper_authority.allowed("post_thread", uid, _tgt, from_status="", snap=snap)
                if not _ok:
                    _msg = {"tier_too_low": "投稿の権限がございませぬ",
                            "out_of_scope": "この案件（PJ）はご担当の範囲外ゆえ投稿できませぬ",
                            "snapshot_stale_admin_only": "権限情報が古く、今は管理者のみ投稿できまする"}.get(_rsn.split(":")[0], f"投稿できませぬ（{_rsn}）")
                    self._json({"ok": False, "error": _msg}); return
            # ① task の thread_id を引く(Nibu殿 get_task_thread)。未開設/task無しは投稿せず報告(推測でスレ新設せぬ安全弁)
            tr = casper_mcp.call_tool("get_task_thread", {"task_id": int(task_id), "actor_id": int(actor)}, token=WRITE_TOKEN, actor=actor)
            try:
                tj = json.loads(tr)
            except Exception:
                tj = {"error": str(tr)[:200]}
            thread_id = tj.get("thread_id")
            unopened = (thread_id is None and not tj.get("error"))
            if tj.get("error") or thread_id is None:
                self._json({"ok": False, "unopened": unopened,
                            "error": ("スレッド未開設ゆえ投稿いたしませぬ（推測でのスレッド新設はせぬ）" if unopened
                                      else f"thread取得失敗（{tj.get('error') or str(tr)[:120]}）")}); return
            # ② thread_id 指定で投稿。監査台帳(_m4_ledger)を兄弟endpoint同様に通す(Fable監査2026-07-23 穴③)。
            #    to_user_id は thread_id 指定時は無視されるゆえ actor 自身を置く。
            orec = _m4_ledger_open("post_thread", "send_message",
                                   {"task_id": int(task_id), "thread_id": int(thread_id), "autonomous": autonomous, "body_len": len(body)},
                                   actor, f"議事録FB→SHOTスレッド投稿: task {task_id}",
                                   {"project_id": (_task or {}).get("project_id"), "assignee": str((_task or {}).get("assigned_to") or "")}, snap)
            r = casper_mcp.call_tool("send_message",
                                     {"actor_id": int(actor), "to_user_id": int(actor), "body": body, "thread_id": int(thread_id)},
                                     token=WRITE_TOKEN, actor=actor)
            try:
                rj = json.loads(r); ok = bool(rj.get("id"))
            except Exception:
                rj = {"raw": str(r)[:200]}; ok = False
            _m4_ledger_close(orec, ok, ("ok" if ok else str(r)[:120]))
            self._json({"ok": ok, "thread_id": thread_id, "actor": actor, "result": rj,
                        "message": (f"SHOTスレッド(thread {thread_id})へ投稿いたした。" if ok else f"投稿に失敗いたした（{str(r)[:150]}）")})
            return
        if self.path == "/api/tasks/list":             # 末端① UI: FB宛先タスク一覧(get_project_tasks/get_shot_tasks proxy・readonly)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_mcp and WRITE_TOKEN):
                self._json({"ok": False, "error": "一覧機構が無効にござる", "items": []}); return
            uid = str(who.get("uid") or "")
            actor = uid if uid.isdigit() else "101"    # 読取のみゆえ本人不明でも system actor(101)で可
            sid = str(req.get("shot_id") or "").strip(); pid = str(req.get("project_id") or "").strip()
            if sid.isdigit():
                tool, targs = "get_shot_tasks", {"shot_id": int(sid), "limit": 500, "offset": 0, "actor_id": int(actor)}
            elif pid.isdigit():
                tool, targs = "get_project_tasks", {"project_id": int(pid), "limit": 500, "offset": 0, "actor_id": int(actor)}
            else:
                self._json({"ok": False, "error": "project_id か shot_id が要りまする", "items": []}); return
            r = casper_mcp.call_tool(tool, targs, token=WRITE_TOKEN, actor=actor)
            try:
                rj = json.loads(r) if isinstance(r, str) else r
                raw = rj.get("items") or []
            except Exception:
                self._json({"ok": False, "error": f"一覧取得失敗（{str(r)[:120]}）", "items": []}); return
            items = [{"task_id": t.get("id") or t.get("task_id"), "name": t.get("name") or "",
                      "shot_code": t.get("shot_code") or "", "status": t.get("status_label") or t.get("status") or "",
                      "thread_id": t.get("thread_id")} for t in raw if (t.get("id") or t.get("task_id"))]
            self._json({"ok": True, "count": len(items), "items": items}); return
        if self.path == "/api/reschedule/commit":      # M4 Phase2: 日程変更確定→W2ガード付きexecute(本人/admin=MCP即時/pm・lead他者=BFF待ち)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            who = identify(self)
            if not (casper_reschedule and casper_authority):
                self._json({"ok": False, "error": "reschedule機構が無効にござる"}); return
            tid = str(req.get("task_id") or ""); nd = str(req.get("new_due") or "")
            if not tid or not nd:
                self._json({"ok": False, "error": "task_id/new_due が要りまする"}); return
            if not WRITE_TOKEN:
                self._json({"ok": False, "error": "write token 未設定のため実行できませぬ(ニブ殿のtoken待ち)"}); return
            snap = casper_authority._load_snapshot()

            def _read(t):
                try:
                    r = casper_tools._get(f"/tasks/{t}") if casper_tools else None
                    return r if (isinstance(r, dict) and r.get("id") is not None) else None
                except Exception:
                    return None

            def _write(t, new_due, actor):
                # MCP update_task の正しい引数: due(≠due_date)・actor_id 必須。返りJSONの ok を見る。
                res = (casper_mcp.call_tool("update_task", {"task_id": int(t), "due": new_due, "actor_id": int(actor)},
                                            token=WRITE_TOKEN, actor=actor) if casper_mcp else "(MCP無効)")
                try:
                    return bool(json.loads(res).get("ok"))
                except Exception:
                    return False
            cur0 = _read(tid)
            orec = _m4_ledger_open("reschedule", "update_task", {"task_id": int(tid) if tid.isdigit() else tid, "due_date": nd},
                                   who.get("uid"), f"日程変更: task {tid} → {nd}",
                                   {"project_id": (cur0 or {}).get("project_id"), "assignee": str((cur0 or {}).get("assigned_to") or "")}, snap)
            ok, info = casper_reschedule.execute(tid, nd, who.get("uid") or "", snap=snap, live_read=_read, live_write=_write)
            _m4_ledger_close(orec, ok, info)
            msg = (f"締切を {nd} に変更いたした。" if ok else
                   {"task_closed": "そのタスクは既に完了しており、日程変更はできませぬ。",
                    "bff_wire_pending": "この権限での他者タスクの日程変更は Score 側の結線（Elvis殿）待ちにござる。",
                    "not_allowed": "この操作の権限がございませぬ（ご本人・PM／リード以上が変更できまする）。",
                    "read_failed": "タスクの現状を読めませなんだ（時間をおいて再度）。",
                    }.get(info.split(":")[0], f"日程変更できませなんだ（{info}）。"))
            self._json({"ok": ok, "info": info, "message": msg, "new_due": nd, "task_id": tid})
            return
        if self.path.startswith("/api/dropbox/raw"):   # 生バイト転送(base64を経由せぬ正路)
            # 【なぜ生で受けるか】base64(data URL)経由はブラウザがファイル全体を文字列に載せるため
            # 大容量で FileReader が落ちる(殿御指摘2026-07-29「転送エラー: 読込失敗」)。
            # 生バイトを chunk で流せば、ブラウザもサーバも全体を抱えぬ。
            if not (casper_dropbox and casper_dropbox.available()):
                self._json({"ok": False, "error": "Dropbox 転送は未設定にござる"}); return
            import urllib.parse as _up
            total = int(self.headers.get("Content-Length", 0))
            fn = _up.unquote(self.headers.get("X-Filename", "") or "file")
            pw = (self.headers.get("X-Password", "") or "").strip() or None
            folder = _up.unquote(self.headers.get("X-Folder", "") or "").strip()
            _t0 = time.time()

            def _dlog(msg):
                """転送の顛末を必ず残す。記録が無いゆえ『Failed to fetch』の在処が判らなかった
                (殿御報告2026-07-29)。ブラウザが受け取れずとも、機構の側に足跡を残す。"""
                try:
                    with open(os.path.join(HERE, "dropbox_transfer.log"), "a", encoding="utf-8") as _f:
                        _f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
                except Exception:
                    pass

            class _Counting:
                """読めたバイト数を数えながら渡す。途中で尽きた時に『どこまで届いたか』を言えるようにする。"""
                def __init__(self, fh):
                    self.fh, self.n = fh, 0

                def read(self, k):
                    b = self.fh.read(k)
                    self.n += len(b or b"")
                    return b

            _cnt = _Counting(self.rfile)
            _dlog(f"START name={fn} size={total} folder={folder or '-'} pw={'有' if pw else '自動'}")
            try:
                if total <= 0:
                    raise ValueError("本体が空にござる(Content-Length=0)")
                if folder:                             # まとめ経路: フォルダへ流し込むのみ(リンクは後で1本)
                    res = casper_dropbox.upload_into_stream(folder, _cnt, total, fn)
                else:
                    res = casper_dropbox.transfer_stream(_cnt, total, fn, password=pw)
            except Exception as e:
                res = {"ok": False, "error": str(e)[:300]}
            # 【返答の前に必ず読み切る】受け取り切らずに返して閉じると、送信途中のブラウザは
            # 接続を切られ『Failed to fetch』となり、こちらの error 文言が一切届かぬ。
            _rest = total - _cnt.n
            if _rest > 0:
                try:
                    while _rest > 0:
                        _b = self.rfile.read(min(1 << 20, _rest))
                        if not _b:
                            break
                        _rest -= len(_b)
                except Exception:
                    pass
            _dlog(f"END   name={fn} ok={res.get('ok')} recv={_cnt.n}/{total} "
                  f"{round(time.time() - _t0, 1)}s err={str(res.get('error') or '')[:160]}")
            self._json(res)
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
                        # ★最後の関: doc_id がUUIDでなければ叩かぬ。実害(2026-08-27)では slug が
                        #   doc_id として入っており、そのまま投げれば「直したつもりが直っておらぬ」か、
                        #   最悪よその資料を触る。**新規作成へ倒して逃げもせぬ**——黙って別の物を作れば
                        #   『追記したのに新しい資料が出来た』が起きる(まさに殿が踏まれた症状)。
                        _did = a.get("doc_id", "")
                        if not aurora_valid_doc_id(_did):
                            result = (f"(doc_id が不正: {str(_did)[:60]!r}。Aurora の doc_id は UUID にござる。"
                                      "資料のURLを添えてもう一度お申し付けくだされ——機構が台帳から引き直しまする。"
                                      "★勝手に新規作成へ倒すことはいたしませぬ)")
                            ok = False
                            raise RuntimeError(result)
                        result = casper_aurora.append_version(_did, html, author_id=uname)
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
        _llm_call_turn_reset()   # cmd_515手当2: このturn(=このスレッド)の推論機呼出記録を空にする
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
            msgs = [{"role": "system", "content": build_sys(_last_user_msg(msgs)) + fu}] + msgs
        who = identify(self)
        # 【殿御下命2026-08-24】雲の帳簿が「誰の・どの発話が社外へ出たか」を名乗れるよう、
        # このturnの素性を置く(帳簿はここから引く。呼出側で持ち回らない)。
        try:
            _last_user = next((m.get("content") for m in reversed(msgs)
                               if m.get("role") == "user"), "")
            _turn_ctx_set(uid=who.get("uid"), name=who.get("name"), thread=thr,
                          query=(_last_user or "")[:300])
        except Exception:
            pass
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
            # 【Fable第八診】錨の無いturnは引く数と予算を絞る(hybridは足切りsc>0.02ゆえほぼ毎回k件埋まる)
            _k, _bud = (8, 3800) if _vault_anchor(last_user) else (3, 1200)
            hits = (casper_embed.hybrid(last_user, k=_k, budget=_bud) if (casper_embed and last_user)
                    else (casper_rag.search(last_user, k=_k, budget=_bud) if (casper_rag and last_user) else []))
            _canon_turn = (_asks_about_casper(last_user) is True)   # cmd_498穴B手当1: 既存機構の再利用
            if _canon_turn:
                hits = _inject_canon(_prioritize_canon(hits))   # cmd_498第2便穴A手当2: 並べ替えで足りねば直接差し込む
            # 【Fable第八診】全文注入は錨が立つturnのみ(無関係な議事録の全文を社外へ出さぬ)
            src, fulltext = (casper_rag.top_source(last_user)
                             if (casper_rag and last_user and _vault_anchor(last_user)) else (None, None))
            _gstate = _grounding_state(hits, fulltext, last_user)   # cmd_497: 材料の構造の齟齬(hits=0なのにfulltext在り)を検知
            fullnote = _build_grounding_block(_gstate, src, fulltext)   # cmd_497第2便: 注意書きをfulltext有無から独立させて注入(欠陥B是正)
            cal = build_digests(who, last_user)       # Fable M1: Ollama経路と同一の単一表から(entity/availability/gear/phase/fb_log/future_assign 欠落の解消)
            diag_hint = DIAG_HINT
            # cmd_501 L9009裏口対応: WebSearch/WebFetchの許可自体を機構が判ずる(文言だけに頼らぬ)。
            # ollama経路と同一のshould_search/build_queryを通し、通らねばallowへ含めぬ
            # (claude CLI自身がWebSearchを呼べる余地そのものを消す=新しい門と同じ検問をこの裏口にも通す)。
            # ★module級のcasper_webを使う(ここでlocal importすると同一関数do_POST内の他分岐にまで
            # 'casper_web'がローカル変数として扱われUnboundLocalErrorを起こす・cmd_501実測で発見)。
            _pj_st = _pj_resolve(last_user)[0]
            _casper_q = (_asks_about_casper(last_user) is True)
            _web_ok = casper_web.should_search(last_user, pj_status=_pj_st, asks_about_casper=_casper_q)
            _web_query, _web_reason = casper_web.build_query(last_user) if _web_ok else (None, "should_search=False")
            _cli_allow = ["WebSearch", "WebFetch"] if _web_query else []
            if _web_query:
                _web_hint = ("外の事について調べたい場合はWebSearchを使ってよい。検索語は必ず次の語だけを使え: "
                             f"『{_web_query}』(これ以外の語・社内固有名詞を検索語に混ぜるな)。"
                             "Web由来は『(Web: <URL>)』と出所を明記せよ。")
            else:
                _web_hint = ("**WebSearch/WebFetchは今回のturnでは許可されておらぬ(ツールが与えられていない)。"
                             "外部情報が要ると思っても、検索はできぬ旨と代わりの言い方を促す文だけを述べよ。**")
            prompt = (build_sys(_last_user_msg(msgs)) + fu + diag_hint + hist + cal + "\n\n## 関連社内記録(RAG検索):\n" + "\n".join(hits)
                      + fullnote
                      + "\n\n## ユーザーの今回の発言:\n" + last_user
                      + "\n\n直前までの会話の流れも踏まえて答えよ。**左脳(Calendar)・右脳(RAG/資料) のデータは上に注入済**。"
                      "これらが手元の一次データ。**『ツールが接続されていない/このセッションで取得できない』とは絶対に言うな**"
                      "(裏で取得済を注入してある)。注入データに答えが無い時のみ『記録に無い』と述べよ。"
                      "その内容で答えられる限りユーザーに追加共有を求めるな(『ファイルを共有すれば〜』等の条件付け・保留は禁止)。"
                      "**ローカルのファイルを直接読もうとするな**(その手段は無い・注入分だけで判断・『ファイルが見つからない』とは言うな)。"
                      "\n\n## 【外部Web検索について(機構が判定済)】\n" + _web_hint
                      + "\n\n**【捏造厳禁】社内固有の事実(制作実績・作品名・人物・案件・クライアント・数値)は、"
                      "上の注入データ(Calendar/RAG/資料)に明記された物だけを述べよ。"
                      "自分の一般知識・記憶から『それっぽい有名作』を補完・推測するな**"
                      "(例: データに無い映画/ゲーム/CM名を勝手に足さない)。"
                      "データに該当が無ければ『記録にあるのは〜』と在る分だけ挙げ、無い旨を正直に言え。"
                      "Casper として簡潔に答えよ。")
            raw = claude_cli_text(prompt, allow=_cli_allow)
            if _web_query:
                try:
                    casper_web._assert_query_subset(_web_query, last_user)   # AC5機械保証(このバックエンドでも同一機構を通す)
                    _urls = casper_web._URL_RE.findall(raw or "")
                    casper_web._log_search(who.get("uid"), None, last_user, _web_query,
                                           "claude_cli_backend", None, _urls, 0.0)
                except Exception:
                    pass
            else:
                casper_web._log_search(who.get("uid"), None, last_user, None, "claude_cli_backend",
                                       (_web_reason if not _web_ok else "should_search=False"), [], 0.0)
            thinking = ""
            tm = re.search(r"<think>(.*?)</think>", raw or "", re.S)
            if tm:
                thinking = tm.group(1).strip()
            ans = strip_think(raw)
            ans = re.sub(r"\n{3,}", "\n\n", ans).strip()
            ans = _validate_assets(ans)                           # 出口検問: 捏造/asset URLを除去
            ans = _guard_unrostered_person_claim(ans)             # 出口検問(AC2・cmd_508): claude_cli経路でも同一機構を通す
            # 【殿御下命2026-08-26】★この分岐には道具が一つも無い——pending_actions も casper_outbox も
            # 結線されておらず、Aurora保存・DM送信・起票はいずれも起こり得ぬ。にも関わらず完了検問だけが
            # 抜けており、雲に座る間は「保存しました」と言い切っても誰も止めなかった。
            # 雲では**必ず**カード0件ゆえ、完了主張は例外なく嘘である。[]を渡して fail-closed に倒す。
            ans = _guard_completion_claims(ans, [])
            if _web_query:                                        # cmd_501: WebSearchを許可したturnのみ札付け出口検問
                ans = casper_web.grounding_gate(ans, {"ok": True, "urls": casper_web._URL_RE.findall(raw or "")})
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
        # cmd_499(c): 番号のみの返答は、直前turnで機構が示した列挙(_LAST_ENUM)と突合する。
        # 突合できれば検索語でなく前turnの選択として本文へ接ぐ。数字のみの入力で突合できなければ、
        # 見当違いの検索(実害: 「1件のプロジェクトはありません」)へ流さず正直な行き止まり回避文を返す。
        if who and _NUM_ONLY_RE.match(str(ll_user or "")):
            _num_resolved = _resolve_number_reply(ll_user, thr, who)
            if _num_resolved:
                ll_user = _num_resolved
            else:
                self._emit("先ほどの番号でのお答えを受け取れませなんだ。お手数ながら内容でお申し付けくだされ。")
                try:
                    self.wfile.write(b'{"done":true}\n')
                except Exception:
                    pass
                return
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
        _pin_key = aurora_pin_key(thr, who)              # 資料の錨(turnを跨ぐ)
        _au_note = aurora_url_digest(ll_user, pin_key=_pin_key)   # 貼られたAurora資料URL→機構が本文を取得して一次資料として注入
        if not _au_note:                                 # URLが無いturn→錨が生きておればそれを注入
            _au_note = aurora_pinned_digest(_pin_key, ll_user)
        sysadd += _au_note
        _au_resolved = "これが一次資料" in _au_note        # cmd_493: 一次資料が確定した turn か(取得失敗時は False=vault併用を妨げぬ)
        sysadd += deixis_table_digest(ll_user, msgs)      # 『この表』→直前の自分の応答の表を機構が名指して渡す
        sysadd += anchor_digest(thr, who, ll_user)        # cmd_508(病三): 継続形の短文→直前の錨(資料/案件/人物)を機構が名指して渡す
        sysadd += retry_fallback_digest(thr, who, ll_user)  # cmd_510第3便: 60秒内の再打鍵/言い換えを検知→生の材料へ落とす
        _dx_rows = _deixis_table_rows(ll_user, msgs)      # 同じ表を DM本文の接地にも使う(単一機構)
        sysadd += dm_writing_digest(ll_user, _dx_rows)    # DMは宛先の目で書く(指示語禁・材料同梱・取次禁)
        thread_rules_observe(thr, ll_user)                # 殿が述べた規則を控える(履歴予算から溢れても失わぬ)
        sysadd += thread_rules_digest(thr)
        sysadd += dm_recipients_digest(ll_user)           # 複数名宛のDMで宛先が一名に落ちるのを断つ
        if _looks_declarative(ll_user) or len({m.group(0).lower() for m in _STATUS_VOCAB_RE.finditer(ll_user or "")}) >= 3:
            # ステータス語彙を論ずる時は、実装されている対応表を機構から渡す。渡さねば自らの機構と
            # 食い違う説明をする(実測2026-07-27: wt を『超過対象』と述べた——実装では wt=held=非超過)。
            sysadd += ("\n\n## 【機構が実装しているステータス対応(単一ソース casper_status_rules)】\n"
                       "canonical 9値 → status_category(Calendar API が正): "
                       "mk=todo / wip=in_progress / qc・qc_fb=review / ap・client_ap・deliver=completed / "
                       "wt・omit=held。\n"
                       "納期超過の判定: category が completed または held のものは**超過に数えぬ**"
                       "(ゆえ ap/client_ap/deliver/**wt**/omit は非超過。超過対象は mk/wip/qc/qc_fb のみ)。\n"
                       "**この対応を、実装の事実として述べよ。推測で別の割り振りを語るな。**"
                       "合法遷移グラフ(from→to×役職)は Calendar 側に未実装ゆえ、"
                       "『遷移が強制されている』とは言うな(定義と強制は別)。")
        assign_card = _assign_card(ll_user, who)         # M4 Phase1: アサイン提案意図(閲覧全員・実行lead+)→スロット＋候補カード
        mine_table = None; mine_prose = None
        # アサイン意図だが提案カードが出ない＝「私の」明示＝本人のアサイン表を接地で返す(LLMに落として幻覚させない)
        if not assign_card and casper_assign and casper_authority and _ASSIGN_RE.search(ll_user or ""):
            mine_table, mine_prose = _my_tasks_table(who)
        resched = None if (assign_card or mine_prose) else _reschedule_card(ll_user, who)   # M4 Phase2: 日程変更→影響プレビュー/聞き返し
        resched_card = resched.get("card") if resched else None
        resched_reply = (resched.get("clarify") if resched else None) or (_resched_prose(resched_card) if resched_card else None)
        mtg_adv = None if (assign_card or mine_prose or resched) else _meeting_advisory(ll_user, who)   # M4 Phase2': MTG助言(読取)
        mtg_table = mtg_adv.get("table") if mtg_adv else None
        mtg_prose = mtg_adv.get("prose") if mtg_adv else None
        status_res = None if (assign_card or mine_prose or resched or mtg_adv) else _status_card(ll_user, who)   # M4 Phase4: status更新
        status_card = status_res if (status_res and status_res.get("verb")) else None
        status_reply = (status_res.get("clarify") if status_res else None) or (_status_prose(status_card) if status_card else None)
        minutes = None if (assign_card or mine_prose or resched or mtg_adv or status_res) else _minutes_card(ll_user, who)   # M4 Phase3: 議事録→タスク起票
        minutes_card = minutes if (minutes and minutes.get("candidates")) else None
        minutes_reply = None
        if minutes:
            if minutes.get("candidates"):
                minutes_reply = (f"議事録「{minutes.get('meeting_title')}」（{minutes.get('date')}・{minutes.get('project')}）から "
                                 f"{len(minutes['candidates'])}件のタスク候補を起こし申した。"
                                 + ("起票するものを選び、内容を検めて『起票』を押してくだされ。" if minutes.get("can_act")
                                    else "（起票の実行はリード／PM以上が行えまする。下は確認用にござる。）"))
            else:
                minutes_reply = minutes.get("clarify")
        table_card = None if (assign_card or mine_prose or resched or mtg_adv or status_res or minutes) else _table_card(ll_user, who, thr=thr)   # ④ 一覧意図→表カード
        if table_card:
            # 弱qwenは「表カードとして描画済み・再現するな」というメタ指示をそのまま鸚鵡返しし、ユーザーへ
            # 「表示装置が既に描画済み・重複して再現しません」と漏らす(殿指摘2026-07-17: 機械臭く分かりづらい)。
            # → 注記を"要約の作り方"に反転し、画面/自分の制約に触れる語を明示的に禁止(下段6915で機構除去も併走)。
            # 【件数と一覧は同一関数(掟)】内訳を機構が数えて渡す。渡さねば弱qwenは context 中の別物
            # (全社統計等)を当該PJの数として述べる——実測2026-07-27: あるPJ(実 ap40/qc_fb9)を
            # 「wip13件・mk8件」と作文した。それは全社の in_progress/todo の数であった。
            _cols = table_card.get("columns") or []
            _brk = ""
            if "状態" in _cols and table_card.get("rows"):
                import collections as _co
                _si = _cols.index("状態")
                _c2 = _co.Counter(str(r[_si]) for r in table_card["rows"] if len(r) > _si)
                _brk = ("この表の内訳（機構が数えた確定値）: "
                        + "、".join(f"{k} {v}件" for k, v in _c2.most_common()) + "。"
                        "**件数はこの確定値のみを使え。表に無い数字(全社の統計等)を"
                        "この一覧の数として述べるな。**")
            sysadd += ("\n\n## 【この一覧の答え方】"
                       f"聞かれた一覧（{table_card['title']}・全{len(table_card['rows'])}件）は、この下に見やすい表で自動で並ぶ。"
                       f"{_brk}"
                       "あなたの仕事は“表の再掲”ではなく“要約”。"
                       "**表・markdown表・各行の箇条書きで一覧を作り直すな。**"
                       "また『表カード』『表示装置』『描画』『重複』『再現しません』『装置』など、"
                       "画面表示や自分の制約に触れる言葉は一切使うな。"
                       "代わりに部下へ語るように——総数、負荷が偏っている担当やPJ、気になる点——を2〜4文の自然な日本語で述べ、"
                       "末尾に『PJ別・担当別で見たい時や、対応を一緒に考えたい時は言ってくだされ』と一言添えよ。")
        _sched = schedule_csv_export(ll_user, who)       # ① 工程表CSV: 既存タスク→Calendar公式CSVを機構生成(殿指示2026-07-10)
        if _sched:
            _slink, _smeta = _sched
            sysadd += ("\n\n## 【工程表CSVを生成済み(機構・Calendar確定)】"
                       f"{_smeta['pj']} の現在のタスク {_smeta['rows']}件を Calendar公式CSV に書き出した。"
                       f"**回答には必ず次のダウンロードリンクをそのまま改変せず含めよ: {_slink}**。"
                       "『Excelで開け、編集して取り込み直せる』旨を1文添えよ。ガントや全タスクの再掲はするな(冗長)。"
                       "Calendarへ直接反映したい場合は、その旨言えば承認カードで書込む と案内してよい。")
        # 【二軸classifier(Fable設計2026-07-14): 真実源は"種類軸"で決まる】
        # status/進捗の問い(現況)は Calendar が排他的真実源。vault(RAG)を注入経路から外す=status質問がvaultに漏れて
        # legacy/過去知識/捏造を拾う「一つの病」を入口で断つ。table_card等のCalendar機構が発火済ならその母集合が答え。
        # knowledge/文脈の問いのみ vault を引く(現・過去とも・時間除外は不要=legacyも知識としてなら許容)。
        _status_q = bool(table_card or _sched or _STATUS_Q_RE.search(ll_user or ""))
        hits = []   # cmd_492検証中発見: status経路はhits未定義のままtrace emit(L8805)のisinstance判定に到達しNameError→trace全体が握り潰される既存不具合。空リストで安全側初期化のみ(挙動非変更・trace観測を復旧)
        # cmd_493: 貼られたAurora資料URLが一次資料として確定した turn は vault(RAG)を併走させぬ。
        # 実測2026-07-31 20:35: URL解決成功(material 4508字)後もvault top_source(無関係な他人の資料
        # ARKitLedScan…)が並走注入され、弱qwenが後者を『提供された資料』と誤認して答えた
        # (rag_hits=5・ctx_len=14248を実再現で確認)。_status_q(Calendar既存軸)とは別軸ゆえ混ぜぬ
        # (下段L9017の存在否定ガードはCalendar専用の掟であり、Aurora資料には無関係)。
        try:
            if not (_status_q or _au_resolved):           # knowledge経路のみ vault を引く(Aurora一次資料が有る turn は除く)
                _k2, _bud2 = (6, 3800) if _vault_anchor(ll_user) else (3, 1200)   # 【Fable第八診】
                hits = (casper_embed.hybrid(ll_user, k=_k2, budget=_bud2) if (casper_embed and ll_user)
                        else (casper_rag.search(ll_user, k=_k2, budget=_bud2) if (casper_rag and ll_user) else []))
                _canon_turn = (_asks_about_casper(ll_user) is True)   # cmd_498穴B手当1: 既存機構の再利用
                if _canon_turn:
                    hits = _inject_canon(_prioritize_canon(hits))   # cmd_498第2便穴A手当2: 並べ替えで足りねば直接差し込む
                if hits:
                    sysadd += "\n\n## 関連社内記録(右脳vault・意味/字面検索):\n" + "\n".join(hits)
                src, fulltext = (casper_rag.top_source(ll_user)
                                 if (casper_rag and ll_user and _vault_anchor(ll_user)) else (None, None))
                if fulltext:
                    sysadd += ("\n\n## 該当資料(右脳vault・" + src + ") — サムネ等の画像URL `![](/asset/..)` は"
                               "ここから一字一句コピーせよ。これに無い画像URLは創作するな:\n" + fulltext[:7000])
        except Exception:
            pass
        # cmd_501: 一般の調べ物のためのネット検索。qwenのtool呼出には委ねぬ——機構が発火を判ずる
        # (「外の事を尋ねる形」かつ「社内対象が解決せぬ」turnのみ・軍師設計の2条件そのまま)。
        # ★hits/fulltextはゲートに使わぬ(実測で判明した罠): casper_embed.hybrid(k=6)は字面trigram
        # 候補(casper_rag.candidates、閾値sc>0.02という極めて緩い足切り)を土台とするため、無関係語
        # (例:「バルセロナオリンピック」「OpenUSD」)でもほぼ毎回 k件 埋まって返る——「hitsが空でない」は
        # 「社内に関連記録がある」を意味しない(既存コードの hits 利用はすべて『見つかった物を引用表示』
        # 用途であり、本cmdのような『検索要否のゲート』用途には転用不可だった)。
        # ゆえ軍師が明記した2条件(_pj_resolve/_asks_about_casper)に加え、実測で発見した第3条件
        # (自社Vimeoライブラリ検索の意図)も除外する——「Vimeoの動画を探して」は_pj_resolve/_asks_about_casper
        # いずれにも掛からず(社外Web検索と誤判定・実測2026-08-07)、既存のvimeo_search tool(qwen呼出)が
        # 本来担うべき内部検索を機構が横取りしてしまっていた。vault由来の断り文言
        # (「社内記録には見当たりませなんだ」)は_gstate系の出口検問が別途担っており、ここでの二重ゲートは不要。
        _web_search_result = None
        try:
            _web_internal_tool = bool(_INTERNAL_TOOL_SCOPE_RE.search(ll_user or ""))
            if casper_web and not (_status_q or _au_resolved or _web_internal_tool):
                _asks_form = bool(_QUESTION_FORM_RE.search(ll_user or "") or _REQUEST_FORM_RE.search(ll_user or ""))
                _web_pj_st = _pj_resolve(ll_user)[0]
                _web_casper_q = (_asks_about_casper(ll_user) is True)
                if casper_web.should_search(ll_user, pj_status=_web_pj_st, asks_about_casper=_web_casper_q, asks_form=_asks_form):
                    _web_search_result = casper_web.search(ll_user, uid=who.get("uid"), thread=thr, cli_text_fn=claude_cli_text)
                    if _web_search_result.get("ok"):
                        sysadd += casper_web.format_result_block(_web_search_result) + casper_web.WEB_PROMPT_RULE
                    else:
                        sysadd += ("\n\n## 【外部Web検索について(機構が判定済)】"
                                   f"今回は検索を実行しなかった({_web_search_result.get('reason')})。"
                                   "その理由をそのまま丁寧に伝え、別の言い方で尋ね直すよう促せ(黙って諦めるな)。")
                        _web_search_result = None   # blocked時は出口検問の対象にしない(grounding_gateはok=Trueのみ見るが明示)
        except Exception:
            pass
        # cmd_492 第2便: ゼロ照応の引き継ぎ(5条件すべて満たす時のみ・機構が対象を確定的に注入)。
        # sysadd が working(system message)へ凍結される直前(本行の前)に置くこと——この後段の
        # sysadd 追記(_corpus_only_note 等)は working 凍結後ゆえ LLM プロンプトへ届かぬ既存の構造
        # (choices_obj/_snz/_assign_short もこの時点では未定義)。5条件を満たさぬ場合(鮮度切れ/
        # 別人/話題なし/None判定不能)は引き継がず、qwenが自然に聞き返す(新たな聞き返し文言は作らぬ・2-4)。
        # snooze/M4即応(_assign_short)等は後段で routed が立ち ollama_chat_stream 自体を短絡するため、
        # ここで注入しても無害(その turn の system は生成に使われない)。
        _handoff_topic = None            # cmd_492第3便: ここで一度だけ判定した結果を後段(L8863附近)へ持ち回り、
                                          # _needs_prior_context/_topic_handoffのLLM classifier二重呼出を避ける。
        _pq_new_topic = None              # cmd_492第3便: 聞き返し合成が成立した時の新topic(後段で_LAST_TOPICを
                                          # これに更新する・そうしないと後段の再解決でnoiseに埋もれ得るため)。
        try:
            _handoff_topic = _topic_handoff(thr, who, ll_user)
            if _handoff_topic:
                sysadd += topic_handoff_digest(thr, who, ll_user, topic=_handoff_topic)
            else:
                _pq_digest, _pq_new_topic = _pending_question_synthesis(thr, who, ll_user)
                sysadd += _pq_digest
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
            working = [{"role": "system", "content": build_sys(_last_user_msg(working)) + fu + sysadd}] + working
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
             "description": "自社の Vimeo ライブラリ(全動画・公開/非公開問わず)を名前で検索し、一致動画(タイトル・リンク・id)を返す。動画を探す/見せたい時に使う。",
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
        _dm_body_incomplete_hit = False             # cmd_494 2便: MCP tool_calls経路でpid None(中身欠如)が起きたら真実値で最終応答を機構が強制上書きする
        # cmd_501: _web_search_result は上段(sysadd構築時)で機構が既に確定させている(qwenのtool呼出には委ねぬ設計)。
        # ここでリセットせぬ——tool_calls経路(web_search)は保険として残すが、上段の判定を上書きしない。
        MAXIT = 6
        _t0 = time.time()                           # トレース: 生成時間計測の起点
        try:                                         # cmd_509第2便: 実トラフィックの心拍(supervisorの無通信窓判定・AC6条件③用)
            with open(os.path.join(HERE, "casper_traffic.heartbeat"), "w") as _hb:
                _hb.write(str(_t0))
        except Exception:
            pass
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
        _assign_short = bool(assign_card or mine_prose or resched or mtg_adv or status_res or minutes)   # M4機構は即応→LLM/router/choicesを短絡
        if _assign_short and not _snz:              # M4: アサイン/日程/MTG助言/status更新/議事録起票は機構で即応(LLM非経由・最優先)
            if assign_card:
                _nsl = assign_card.get("total", len(assign_card.get("slots", [])))
                if assign_card.get("can_act"):
                    _rep = (f"アサイン待ち（担当未定）のタスクが {_nsl}件ござる。各タスクに"
                            "“これまで誰が担当したか(実績)”から候補を添えてござる。"
                            "候補を選んで押せば、その場で担当が入りまする（心当たりが無ければ流してくだされ）。")
                else:
                    _rep = (f"アサイン待ち（担当未定）のタスクが {_nsl}件ござる。下に一覧と、"
                            "“これまで誰が担当したか(実績)”からの候補を添えてござる。"
                            "（割り当ての実行はリード／PM以上が行えまする。）")
            elif mine_prose:
                _rep = mine_prose
            elif resched:
                _rep = resched_reply
            elif mtg_adv:
                _rep = mtg_prose
            elif status_res:
                _rep = status_reply
            else:
                _rep = minutes_reply
            routed = {"_assign": True, "reply": _rep}
        # Q1(Fable 選択カード): 曖昧な指示語(それ/あの件…)＋action意図で対象候補が複数→qwenに推測(捏造)
        # させず選択カードで人に決めさせる。routerより優先(推測の芽を潰す)。say型ゆえ副作用起票はしない。
        _fdm = None if (_snz or _assign_short) else (_file_delivery_dm(ll_user, who, convo=msgs)
                                                      or _own_response_delivery_dm(ll_user, who, convo=msgs))   # 最優先: 文脈の共有リンク→DM(『これtetsuoに送っといて』の deixis で選択カードに横取りされぬよう choices より先)。URL文脈が無ければcmd_508(病三E01): 直前の自分の応答本文→DM
        choices_obj = None if (_snz or _fdm or _assign_short) else _build_choices(who, ll_user, convo=msgs)   # 内部で deixis＋action意図を判定
        # 【殿御下命2026-08-26】長い資料を貼っただけの発話は『問い』でなく『材料』。
        # 実害: 議事録本文を四度貼られ、四度ともPJ状況要約/逆インタビューを返し、貼られた本文に
        # 一言も触れなかった。何をするかは人が決める——受け取った事実と選択肢だけを返す。
        _material = None
        if not (_snz or _fdm or _assign_short or choices_obj or _looks_like_action(ll_user)):
            _material = pasted_material(ll_user)
        if not choices_obj and not _snz and not _fdm and not _assign_short:    # 名前解決の3値(ambiguous/none)→選択カードで拾う(無言None落ち禁止・Fable)
            choices_obj = _pj_task_choices(ll_user)
        if not choices_obj:
            sysadd += _corpus_only_note(ll_user)          # Calendar不在/資料在りの名は、出所つきで境界を明示(捏造の予防)
        if not _snz and not _assign_short:
            attn_cards = _attention_action_cards(who, ll_user)       # Q4: 今日の3件の overdue/loop を選択カードで(draftは①で承認カード)
        # P2(Fable propose→execute→render): DM等のアクションは制約デコード(format=json)で型付き提案を作り
        # 承認カードを機構生成→自由文tool-callを迂回。確定時は生成ループをスキップ(salvageのモグラ叩き不要に)。
        if not _snz and not _assign_short:          # snooze/アサイン即応確定時は routed を維持(上書き禁止)
            routed = _fdm or (None if choices_obj else (_action_router(ll_user, sysadd, who, convo=msgs, gate=_gate) if _looks_like_action(ll_user) else None))
            if choices_obj:                         # 曖昧→選択カード提示。生成ループはスキップ(routed扱い)
                routed = {"_choices": True, "reply": choices_obj["prompt"]}
            elif _material and not routed:          # 材料の投げ入れ→事実＋選択肢(決定的・qwen非経由)
                _mreply, choices_obj = material_choices(_material)
                routed = {"_choices": True, "reply": _mreply}
        # 【殿御下命2026-08-27】資料修正の決定的経路。
        # 実害(15:22〜16:08): 錨で現本文は渡っていたのに、モデルが道具を呼ばず「承認カードが
        # 表示されます」と約束だけを返し、14turn連続で cards=0。殿は九度「ボタンが出ない」と
        # 訴えられた。★欠けていたのは『モデルが道具を呼ぶ』という運だけ。運に頼るのをやめる。
        if not routed and not choices_obj and not _snz and not _assign_short:
            try:
                _pe = aurora_pin_get(aurora_pin_key(thr, who))
                if (_pe and _pe.get("material") and _AURORA_EDIT_INTENT_RE.search(ll_user or "")
                        and not _AURORA_PIN_RELEASE_RE.search(ll_user or "")):
                    _eb = aurora_edit_compose(_pe, ll_user)
                    if _eb:
                        _eargs = {"doc_id": _pe["doc_id"], "body": _eb}
                        _esum = _action_summary("aurora_append", _eargs)
                        _esum += aurora_shrink_note(_pe["doc_id"], _eb)
                        _esum += aurora_body_drift_note(_pe["doc_id"], _eb)
                        _epid = _register_pending("aurora_append", _eargs, who.get("uid"), _esum,
                                                  origin="user", query=str(ll_user)[:400], trace_id=_tid)
                        if _epid:
                            PENDING_ACTIONS[_epid]["thread"] = thr
                            pending_actions.append({"id": _epid, "tool": "aurora_append",
                                                    "args": _eargs, "summary": _esum})
                            routed = {"_surfaced": True,
                                      "reply": ("修正版を下書きいたしました。**まだ書き込んでおりませぬ**——"
                                                "この下に出る承認カードで本文をお確かめの上、"
                                                "ボタンを押していただければ Aurora に保存されまする。")}
            except Exception:
                pass
        # 追従: Casperが「下書きを表示しますか?」と申し出た直後の裸の肯定(おねがい/はい)=その申し出への同意→浮上
        _affirm_draft = bool(_AFFIRM_RE.match((ll_user or "").strip())) and bool(_DRAFT_OFFER_RE.search(_last_assistant(msgs)))
        # 滞留下書きの浮上: 『下書き見せて/承認待ち確認/気にかけどころ処理』等→実カード(内容+承認/却下)を出す
        # (決定は散文でなくカードで=殿指摘。新規DM作成意図でない時のみ)
        if not routed and (_DRAFT_SURFACE_RE.search(ll_user) or _affirm_draft) and (not _looks_like_action(ll_user) or _DRAFT_ASK_RE.search(ll_user) or _affirm_draft):
            _n, _note = _surface_pending_drafts(who, pending_actions)   # Q3C強処方: 下書きの中身を問う=決定的fast path(qwen非経由・憶測ゼロ)
            if pending_actions:
                routed = {"_surfaced": True, "reply": _note}
        if routed and (routed.get("_surfaced") or routed.get("_choices") or routed.get("_assign")):   # 浮上/選択/アサイン=reply表示のみ(起票しない)
            final = routed["reply"]
        elif routed:
            try:
                summary = _action_summary(routed["tool"], routed["args"])
                pid = _register_pending(routed["tool"], routed["args"], who.get("uid"), summary,
                                        origin="user", query=str(ll_user)[:400], trace_id=_tid)
                if pid is None:                     # cmd_494: 中身欠如→起票せず聞き返す(fail-closed)
                    final = _DM_BODY_INCOMPLETE_MSG
                    routed = None
                else:
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
        # cmd_494 3便(軍師案(3)採用・行単位の保留に変更): 完了断定検問(_guard_completion_claims)は
        # 「カードが立っているか」を見て判定するが、それはturn終了時(pid確定)にしか定まらない。
        # ★当初はquery先読み(_looks_like_action)でturn全体の非ストリーム化を試みたが、実測で
        # 「先ほど話した内容と同じものをtetsuoに共有しておいて」等、query自体には送信語彙が一切無い
        # (qwenがtool_callすら呼ばず素のテキストで完了主張だけ書く)場合に検知が漏れることが判明した
        # (query先読み・tool_call検知のいずれの前兆シグナルも存在しない偽陰性)。
        # ★cmd_494 5便(至急差戻・軍師案(2)採用): 3便まではここで_completion_claim_line_hit(語彙表・
        # 完了主張の"形")を使っていたが、軍師実測(8回)で「とDMします」等の意志表明型が除外語を含まず
        # 素通しされたまま弾かれ続ける(AC3退行)ことが判明した。除外語を積み増す方向は次の語彙で同じ穴が
        # 開く(cmd_485で6巡した轍)ため採らない。判定を「語彙の形」から「カードの有無」へ移す——
        # 送信(送る/DM等)に言及する行は完了断定であれ意志表明であれ一様にその場で保留し(_send_mention_line_hit、
        # 誤検出があってもよい広い一次判定)、turn終了時に_register_pendingの結果(pid確定/None)を見て
        # ①カード成立→正直な下書き告知文 ②カード不成立→正直な聞き返し文、のいずれかへ機械的に差し替える。
        # qwenが生成した原文(意志表明であれ完了断定であれ)は画面に一切出さない。
        _sbuf = [""]; _did_stream = [False]
        _cont = 0                                   # 截ち切れ自動継続の回数(トレース用・routed時も定義)
        _pend = [""]                                # 穴1(Fable): 行バッファ。末尾の不完全行は保留し、截ち切れ時に破棄できる
        _held_claims = []                            # cmd_494 5便: 送信言及行(turn終了までクライアントへ出さず、確定文へ差し替える)
        # cmd_510第1便(実害A止血・層3=門): このturn単位で一度だけ送信意図を判定する(層1)。
        # 層2(_send_mention_line_hit・下記ループ)は一行も変更しない——読取turn(False)の時だけ
        # 判定結果を無視させず「そもそも保留対象にしない」ことで、読取turnでは送信検問ごと眠らせる。
        # 送信turn(True)なら従来通りすべて動く(殺していない)。
        _send_intent_gate = _turn_is_send_intent(ll_user, exclude_uid=who.get("uid"))
        def _semit(c):
            _sbuf[0] += c                            # _sbuf=全生チャンク(replace比較用)
            _pend[0] += c
            if "\n" in _pend[0]:                     # 完成した行(改行まで)だけクライアントへ→壊れた行を画面に出さない
                cut = _pend[0].rfind("\n") + 1
                emit_now = _pend[0][:cut]; _pend[0] = _pend[0][cut:]
                # cmd_492 4便: ストリーム送出は _strip_tool_narration(final一括処理)より先に外へ出る
                # ため、完成行の道具実況/道具呼出断片はここで剥がしてから送る(既送信分は取り消せぬ)。
                # 改行/空白構造を壊さぬ専用版(_strip_tool_narration_chunk)を使う。
                emit_now = _strip_tool_narration_chunk(emit_now)
                if not emit_now:
                    return
                _kept_lines = []
                for _ln in emit_now.splitlines(keepends=True):
                    if _send_intent_gate and _send_mention_line_hit(_ln):   # cmd_494 5便: 送信言及行はここで止め、final確定後に確定文へ差し替える
                        _held_claims.append(_ln)
                    else:
                        _kept_lines.append(_ln)
                emit_now = "".join(_kept_lines)
                if emit_now:
                    _did_stream[0] = True
                    try:
                        self._emit(emit_now)
                    except Exception:
                        pass
        def _flush_pend():                           # 自然終了時: 保留中の完成分をクライアントへ(送信言及行は_held_claimsへ)
            if _pend[0]:
                _tail = _strip_tool_narration_chunk(_pend[0])   # cmd_492 4便: 末尾の未完成行(改行なし終端)も同様に検問
                if _tail:
                    _kept_lines = []
                    for _ln in _tail.splitlines(keepends=True):
                        if _send_intent_gate and _send_mention_line_hit(_ln):
                            _held_claims.append(_ln)
                        else:
                            _kept_lines.append(_ln)
                    _tail = "".join(_kept_lines)
                    if _tail:
                        _did_stream[0] = True
                        try:
                            self._emit(_tail)
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
                                if pid is None:                        # cmd_494 2便: 中身欠如→起票せず(phantom pending_actions登録も禁止・_guard_completion_claimsを無力化させぬ)
                                    _dm_body_incomplete_hit = True
                                    result = ("[未登録] 中身が欠けているため下書きを起票しなかった。"
                                              "**完了報告は一切するな**。次のユーザー入力を待て。")
                                else:
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
                                # 【殿御下命2026-08-26】発話がURL/idで資料を名指ししているなら、それが最優先。
                                # ★スレッドの紐付け(暗黙)より、人が今この発話で指した物(明示)を上に置く。
                                #   18:18の実害は「aurora_appendは在るのにdoc_idを渡す機構が無い」だった。
                                _ref = aurora_doc_ref(ll_user or "")
                                if _ref and _ref.get("found") and _ref.get("doc_id"):
                                    cur = {"doc_id": _ref["doc_id"], "title": _ref.get("title", "")}
                                else:
                                    # 【殿御下命2026-08-27】発話にURLが無い turn でも、錨が生きておれば其れを使う。
                                    # 実害: 「貼る→追加→書け」の二手目以降で資料を見失い、モデルが作文した。
                                    _pn = aurora_pin_get(aurora_pin_key(thr, who))
                                    if _pn:
                                        cur = {"doc_id": _pn["doc_id"], "title": _pn.get("title", "")}
                                new_intent = bool(re.search(r"新規|新しく|新しい|別の|別に|もう[1一]つ|new doc", ll_user or ""))
                                if _ref and _ref.get("found"):
                                    new_intent = False     # 既存を名指ししている以上、新規ではない
                                efn = fn
                                if fn == "aurora_create" and cur and not new_intent:   # 既存資料あり&新規指定なし→同じ資料へ追記
                                    efn = "aurora_append"; args = {"doc_id": cur["doc_id"], "body": args.get("body", "")}
                                elif fn == "aurora_append":
                                    # ★識別子はモデルに作らせぬ。UUIDでない値(slug/題)は棄て、機構の解決値で置く。
                                    #   実害(14:21/14:25): モデルが doc_id に slug を書き、`not args.get("doc_id")`
                                    #   の条件ゆえ機構の本物が押しのけられた。偽物が在る方が空より質が悪い。
                                    if not aurora_valid_doc_id(args.get("doc_id")):
                                        if cur:
                                            args["doc_id"] = cur["doc_id"]
                                        else:
                                            args.pop("doc_id", None)
                                summary = _action_summary(efn, args)
                                if efn == "aurora_append":
                                    # ★版差し替えは中身を丸ごと入れ替える。黙って通さず表に立てる。
                                    summary += aurora_shrink_note(args.get("doc_id", ""), args.get("body", ""))
                                    summary += aurora_body_drift_note(args.get("doc_id", ""), args.get("body", ""))
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
        if _dm_body_incomplete_hit:                 # cmd_494 2便(主たる手当て): pid None(中身欠如)が起きたturnは
            # qwenがどんな文面を書こうと(「送信します」等の虚偽断定含め)一切信用せず、最終応答を機構が完全に差し替える。
            # retrieve-then-renderと同じ筋——材料(pid)が無ければ機構が正直な文を出す。regexの網羅性に依存しない。
            final = _DM_BODY_INCOMPLETE_MSG
        _pre = final
        # ★cmd_494 5便: _salvage_text_toolcall(qwenが送信をテキストで表明しただけの行から宛先/本文を
        # 抽出し実カードへ起票する救済)は、_resolve_send_mentionsより必ず先に走らせる。逆順だと
        # _held_claims相当の文(「〇〇さんへ…送信しました\n> 本文」等)が確定文に差し替わった後の
        # finalしか見えず、salvageが宛先/本文を抽出できずカード成立の芽そのものを摘んでしまう
        # (AC14の「意志表明/完了断定いずれの文言でもカードが成立する」を構造的に阻害する)。
        # 【殿御下命2026-08-27】道具が呼ばれなんだ修正turnを機構が拾う(既存salvageはcreate専用ゆえ)。
        if not pending_actions:
            try:
                _pin2 = aurora_pin_get(aurora_pin_key(thr, who))
                _sb = aurora_append_salvage(final, _pin2, str(ll_user))
                if _sb:
                    _aargs = {"doc_id": _pin2["doc_id"], "body": _sb}
                    _asum = _action_summary("aurora_append", _aargs)
                    _asum += aurora_shrink_note(_pin2["doc_id"], _sb)
                    _asum += aurora_body_drift_note(_pin2["doc_id"], _sb)
                    _apid = _register_pending("aurora_append", _aargs, who.get("uid"), _asum,
                                              origin="user", query=str(ll_user)[:400], trace_id=_tid)
                    if _apid:
                        PENDING_ACTIONS[_apid]["thread"] = thr
                        pending_actions.append({"id": _apid, "tool": "aurora_append",
                                                "args": _aargs, "summary": _asum})
                        final = ("修正版を下書きいたしました。**まだ書き込んでおりませぬ**——"
                                 "下の承認カードで本文をお確かめの上、押していただければ Aurora に保存されまする。")
            except Exception:
                pass
        final, _au_choices = _salvage_text_toolcall(final, who, pending_actions, query=str(ll_user)[:400], trace_id=_tid,
                                       table_md=_dx_rows, choices_obj=choices_obj)   # qwenがツール未呼出でJSON文を書いた時の救済→承認カード
        if _au_choices and not choices_obj:      # ★既にchoices_objが埋まっている場合(下書き選択が先行)は既存を優先し、本カードは出さぬ(安全側)
            choices_obj = _au_choices
            routed = {"_choices": True, "reply": choices_obj["prompt"]}
        _salv = final != _pre; _pre = final
        if not _dm_body_incomplete_hit and _held_claims and not _salv:   # cmd_494 5便: salvage後のpending_actionsを見て確定文へ差し替え
            # ★salvageが既に自前の正直な下書き告知文(下書きしました…)へ書き換えた(_salv=True)場合は
            # その文言をそのまま採用する——ここで重ねて_resolve_send_mentionsを通すと、salvage自身の
            # 告知文(「送信されます」等)がまた_send_mention_line_hitに拾われ、二重差替で文末の句読点
            # 断片だけが残る事故になる(実測)。_salvが立っていない(salvageは何もできず、カード成立/不成立が
            # 別経路——MCP tool_calls等——で決まった)時のみ、ここで送信言及行を確定文へ差し替える。
            final = _resolve_send_mentions(final, _held_claims, pending_actions)
            _pre = final
        try:                                                         # DM本文が指示語で済ませ材料を欠くなら、機構が当の表を添える
            for _a in pending_actions:
                if _a.get("tool") == "send_message" and _a.get("args", {}).get("body"):
                    _nb = _ground_dm_body(_a["args"]["body"], _dx_rows)
                    if _nb != _a["args"]["body"]:
                        _a["args"]["body"] = _nb
                        _revise_pending(_a)               # 台帳も直す(承認時に旧本文が採用されるのを防ぐ)
        except Exception:
            pass
        try:                                                         # 名指しされた宛先が揃うまで機構が下書きを複製(送信は承認要のまま)
            _fan = _fanout_dm_recipients(str(ll_user), who, pending_actions, trace_id=_tid)
            if _fan:
                final = (final.rstrip() + f"\n\n（名指しされた宛先 計{_fan + 1}名分の下書きを揃えてござる。"
                                          "各カードで宛先と本文を確かめてから送信くだされ。）")
        except Exception:
            pass
        final = _validate_assets(final)                              # 出口検問: 捏造/asset URLを除去(qwen経路の主戦場)
        _val = final != _pre; _pre = final
        if _web_search_result is not None and casper_web:            # cmd_501: Web検索を実行したturnのみ札付け出口検問(過剰注入回避)
            final = casper_web.grounding_gate(final, _web_search_result)
        final = _strip_name_gloss(final, sysadd, ll_user)            # 出口検問: 解決済みPJ名の推測括弧展開(丸亀製麺等)を剥ぐ(Fable処方3)
        _gloss = final != _pre; _pre = final
        final = _guard_unrostered_person_claim(final)                # 出口検問(AC2・cmd_508): roster外のファイル名幹(profile_u_*)が人として主語に立つ文を差し止め
        _person_slot_guarded = final != _pre; _pre = final
        final = _guard_completion_claims(final, pending_actions)     # P1: カード無き完了主張を打ち消し(既成事実化の構造封じ)
        _grd = final != _pre; _pre = final
        _enum_src = final                                            # cmd_499(記録用): 検問前の列挙行を控える(検問が削っても番号突合は生かす)
        final = _validate_choices(final, pending_actions, choices=(choices_obj or attn_cards), injected=sysadd)   # Q2: 裸の選択要求(装置なし)を削除+中立誘導(不変条件①)
        _vch = final != _pre; _pre = final
        # cmd_499(c記録側): この turn の応答に列挙行が2件以上あれば、次turnの番号返答突合用に控える
        # (_LAST_TOPICと同型の作法・thread単位・鮮度30分・uid一致。cmd_492の_LAST_TOPIC本体には手を入れない独立機構)。
        try:
            _enum_lines = [m.group("body") for m in
                           (_ENUM_LINE_RE.match(_ln) for _ln in _enum_src.split("\n")) if m]
            if len(_enum_lines) >= 2:
                _LAST_ENUM[thr] = {"lines": _enum_lines, "ts": time.time(), "uid": who.get("uid")}
                if len(_LAST_ENUM) > 200:
                    for _k in list(_LAST_ENUM)[:-200]:
                        _LAST_ENUM.pop(_k, None)
        except Exception:
            pass
        _pre_narr = final
        final = _strip_tool_narration(final)                         # Q7: 道具実況(生の関数呼び構文だけで停止)を剥ぐ→空なら下でfallback救済
        _leaked_toolcall = final != _pre_narr                        # cmd_492 4便: 剥いだ=道具が未実行のまま実況だけで止まった証跡
        final = _strip_context_echo(final, ll_user)                  # Q3B: 非該当セクション(Vimeo手順等)の滲出を出口で除去
        _ech = final != _pre
        final = _guard_casper_howto_claims(final, ll_user)           # cmd_490手当2 B-4: Casper自身の使い方turnで捏造手順/誤った外部依頼提案を落とし正典へ差替
        _promise_only = _is_promise_only_no_data(final)                 # cmd_492 4便追補: 道具呼出構文は無いが約束文だけで実データが無い形
        if (not final.strip() or _leaked_toolcall or _promise_only) and not pending_actions:
            # 出口検問で全消し、道具実況を剥いだ(=実行に繋がらず narration だけが残った)、
            # または約束文だけで実データが無い場合はカード無き限り救済。
            # narration の残骸だけを「回答した」ことにせず、実データ or 正直な不能表明のどちらかへ倒す(掟: 失敗とゼロを別出口へ)。
            # cmd_492 4便再送: この turn で既にvault検索済(L8929附近)なら src/fulltext を fallback へ渡す
            # (未計算=status経路等ではNameErrorを避けるためlocals()経由。新たな検索は起こさない)。
            final = _pj_status_fallback(ll_user, vault_src=locals().get("src"), vault_fulltext=locals().get("fulltext")) \
                or "うまくお答えできませなんだ。恐れ入りますが、今一度 別の言い方でお尋ねくだされ。"
        if _sched and _sched[0] not in final:                        # ① 決定的保証: 工程表CSVリンクがqwen応答から漏れたら機構が付す
            final = (final.rstrip() + f"\n\n{_sched[0]}\n"
                     f"（Excelで開けます。編集して取り込み直すことも可能です／Calendarへ直接反映も承認カードで行えます）")
        if casper_breaker:                          # z8a(qwen)の健全性を記録: 成功可否+レイテンシ→連続失敗でred=クラウド縮退の判断材料
            try:                                     # cmd_509第2便: key を endpoint別(gen:host:port)へ改める(旧"z8a"固定は多義)
                # 【2026-08-24 多人数テストの予行で発覚】★turnの壁時計を latency にしてはならぬ。
                # 壁時計には【他人の順番待ち】が含まれる。推論機は同時要求を直列に捌くゆえ
                # (実測: 完了が0.9/1.7/2.6/3.4秒と階段状)、5人が同時に話しかけると5人目の
                # turnは60秒を超える。それを slow_ms=30000 で「失敗」と数えると、混んだ時ほど
                # breakerが赤へ傾き、テストの最中に退避が発火して声も答えも変わる——
                # 「遅いから壊れた」のではなく【遅さを故障と誤診して自ら壊しに行く】形であった。
                # ★推論機の健康は、推論機自身が申告した所要(server_total)で測る。行列待ちは除く。
                # ★健康は【速さ】で測る。所要そのものは答えの長さに比例するゆえ尺度にならぬ
                #   (実測: 1203字の正しい答えが server 44.8秒 = slow_ms 30秒超 = 「故障」と数えられた)。
                #   100トークンを生むのに要した時間を latency として刻む——答えが長かろうと短かろうと、
                #   健やかな 27b は約4秒、CPUへ溢れた病んだ機は60秒超になる(尺度が健康にのみ比例する)。
                _recs = _llm_call_turn_records()
                _tok = sum((c.get("server_eval_count") or 0) for c in _recs)
                _evs = sum((c.get("server_eval_sec") or 0) for c in _recs)
                _srv = (_evs / _tok * 100.0) if (_tok and _evs) else None
                if _srv is not None:
                    casper_breaker.record(casper_breaker.gen_key(*_ENDPOINT_HOSTPORT.split(":", 1)),
                                          ok=not final.startswith("[error]"),
                                          latency_ms=int(_srv * 1000))
                elif final.startswith("[error]"):
                    # 推論機が一度も応えなかった=申告が無い。失敗そのものは必ず刻む
                    # (「測れなかった」を「健康」と読み替えぬ)。
                    casper_breaker.record(casper_breaker.gen_key(*_ENDPOINT_HOSTPORT.split(":", 1)),
                                          ok=False, latency_ms=0)
            except Exception:
                pass
        # cmd_492 第1便: _LAST_TOPIC記録(記録のみ・まだ判定/注入に使わない・挙動は変えない)。
        # 既存の決定的解決器が既に解決した結果のみを拾う(新たな推測機構は追加しない・掟「接地の機構化」)。
        try:
            _topic = _resolve_turn_topic(ll_user, _handoff_topic, _pq_new_topic,
                                          locals().get("_canon_turn"), locals().get("src"))
            if _topic:
                _topic["uid"] = who.get("uid")
                _topic["ts"] = time.time()
                _LAST_TOPIC[thr] = _topic
                if len(_LAST_TOPIC) > 200:
                    for _k in list(_LAST_TOPIC)[:-200]:
                        _LAST_TOPIC.pop(_k, None)
        except Exception:
            pass
        # cmd_508 第3便(病三): _LAST_ANCHOR記録。_resolve_turn_topicが既に計算した結果(_topic)を
        # 横取りするのみで、新たな解決/推測は一切行わない(既存機構への非干渉・単一の解決結果を二機構で共有)。
        try:
            _record_anchor(thr, who, locals().get("_topic"))
        except Exception:
            pass
        # cmd_510第3便(実害C): 次turnの型2(述語継承)判定材料として、本turnの述語らしき語を記録する。
        try:
            _record_predicate(thr, ll_user)
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
                casper_trace.emit(_trace_payload(
                    trace_id=_tid, query=str(ll_user)[:200], actor=who.get("uid"), thread=thr,
                    routed=routed,
                    fastpath=_fastpath, echoed=_ech, vch=_vch,   # 決定的fast path/echo検問/裸選択検問の発火
                    injected_facts=_inj, resp_ids=_resp_ids, cont=_cont,   # 注入事実/応答ID/継続回数
                    gate=({"intent": _gate.get("intent"), "facet": _gate.get("facet"),
                           "aliases": len(_gate.get("alias_refs") or [])} if _gate else None),
                    pj=(lambda r: {"status": r[0], "n": len(r[1]), "path": r[2]})(_pj_resolve(ll_user)),   # 名前解決の3値/経路(観測)
                    topic=_topic,   # cmd_492第1便: _LAST_TOPIC記録の観測用
                    rag_hits=(len(hits) if isinstance(hits, list) else 0), ctx_len=len(sysadd),
                    gen_sec=round(time.time() - _t0, 1), salvaged=_salv, validated=_val, gloss=_gloss,
                    guarded_claim=_grd, abstained=_abstain,   # 棄権(Fable #3-5/7-5: 棄権率の定点観測)
                    digests_fired=_dig_trace.get("digests_fired"),   # M1: 発火digest(M2観測の種)
                    final_len=len(final), cards=len(pending_actions), fewshot_used=list(_FEWSHOT_USED),
                    stream_claim_held=len(_held_claims),   # cmd_494 3便: ストリームで保留した完了主張行数
                    web_fired=bool(_web_search_result and _web_search_result.get("ok")),   # AC8(cmd_508): 実際に検索を実行し結果を得たか
                    pending_actions=pending_actions,
                    turn_start_ts=_t0,   # AC8(cmd_508): カードpid証跡・cmd_510第3便: turn_start_tsは降車ログの境界切出しに使う
                    send_intent_gate=_send_intent_gate,   # cmd_511第2便AC10: 層1判定値の観測増設
                    llm_calls=_llm_call_turn_records()))   # cmd_515手当2: このturnで推論機を叩いた記録(AC4/AC5)
            except Exception:
                pass
        final, diagram = render_diagram(final)
        # 【出口検問: 存在否定の資格(Fable処方1・掟6 服従でなく機構で強制)】status回答で「PJのタスクが無い/0件/
        # 登録されていない」と否定する時、そのPJが実際にはCalendarにタスクを持つなら、母集合未確認の嘘(94件を0件)。
        # framingで頼むだけでは弱qwenが破るゆえ、機構が実データで否定を差し止め訂正する(存在否定は最も重い機構の嘘)。
        try:
            _neg_re = _NEG_EXIST_RE
            # 撃つ資格は「限定なしの存在否定」があること。『未着手のタスクはありません』は mk=0 なら真ゆえ、
            # そこへ「全49件ある」と付すのは機構の側の的外れ(実測2026-07-27・あるPJで発生)。文単位で選り分ける。
            _sents = [s for s in re.split(r"(?<=[。\n])", final) if s.strip()]
            _bare = [i for i, s in enumerate(_sents)
                     if _neg_re.search(s) and not _NEG_SCOPE_RE.search(s)]
            if _status_q and _bare:
                _online_pjs = json.load(open("/tmp/cal_projects.json")).get("items", [])
                # 【錨は問い、生成文ではない(Fable retrieve-then-render)】訂正対象のPJは、まず「問いが名指し
                # unique解決した実体」から採る。生成文から名を逆引きするのは最後の手段とし、その照合も
                # _pj_name_hit(語境界)に一本化する——素の `in` は 'Calendar' の中の 'end' を拾い、
                # 問われてもおらぬPJの件数で回答を摩り替えた(殿ログ 16:37)。
                _q_st, _q_names, _ = _pj_resolve(ll_user)
                _hit_pj = None
                if _q_st == "unique":
                    _hit_pj = next((p for p in _online_pjs if p.get("name") == _q_names[0]), None)
                if not _hit_pj:
                    # 逆引きの錨は「否定の主語」= 否定した文、無ければ直前の文(そこに主語が置かれる)。
                    # 回答の他所で候補として挙げただけのPJを掴むのは、機構が関連を捏造すること
                    # (実測2026-07-27: Solafuneの問いで候補列挙の RND を掴み、無関係な訂正を付した)。
                    # 逆に窓を否定文のみに狭めると、直前の文で名指されたPJの否定を見逃す(同日実測)。
                    # 併せて長い名から当てる('RND TKPプレヴィズ' を 'RND' と取り違えぬ)。
                    _by_len = sorted(_online_pjs, key=lambda x: -len(str(x.get("name") or "")))
                    for _i in _bare:
                        _win = (_sents[_i - 1] if _i else "") + _sents[_i]
                        _hit_pj = next((p for p in _by_len if _pj_name_hit(p.get("name"), _win)), None)
                        if _hit_pj:
                            break
                if _hit_pj:
                    _cnt = sum(1 for t in _all_tasks() if t.get("project_id") == _hit_pj.get("id"))
                    if _cnt > 0:                          # PJ名が出て否定されているが実際はタスク在り→存在否定の嘘を差し止め
                        # 嘘の文だけを撃ち、残りは生かす(旧: 全文置換=正しい説明ごと消していた)。
                        # 撃つのは限定なしの否定のみ——限定つきの真の文まで消せば、それも改竄である。
                        _bs = set(_bare)
                        _kept = [s for i, s in enumerate(_sents) if i not in _bs]
                        _fix = (f"**{_hit_pj['name']}** には Calendar 上、現在 **{_cnt}件** のタスクが登録されています"
                                "（「無い/0件」は母集合を確認せぬ誤りにつき訂正）。"
                                "特定の条件（工程・確認待ち等）で絞りたい場合は、条件を明示してくだされ。")
                        final = ("".join(_kept).rstrip() + "\n\n" + _fix) if _kept else _fix
        except Exception:
            pass
        # ※出力の文単位legacyスクラブは撤去(Fable審査2026-07-14): 接地はcontext入口(casper_rag除外)で断つのが本筋。
        #   bare"legacy/EVA/NZ2"の文単位除去は正当文まで落とし応答を途切れさせる乱暴さ=修辞の破損。入口で断てている
        #   ゆえ二重防壁の下段(出口)は不要。実ログでlegacy漏れゼロを観測済。
        if table_card:                                    # 表カードがある時、本文が重複md表を再現しても機構で剥がすのみ
            # (qwenが「表を再現するな」指示を無視して全再現する→截ち切れ源。Fable: 服従に頼らず機構で強制)。
            # ※代表名augmentation(「主なものは…全N件は下表の通り」)は撤去(Fable審査: table_cardが名前を網羅済ゆえ
            #   本文で二重render=蛇足。DM下書き等の非一覧応答にまで漏れ、件数不整合/"test"混入を招いた)。剥がすだけ。
            _nod = [ln for ln in final.split("\n") if not re.match(r"\s*\|.*\|", ln)]   # md表行(|…|)を除去
            _txt = "\n".join(_nod)
            # メタ漏れの文を除去: qwenがsystem注記を鸚鵡返しし「表示装置が既に描画済み・重複して再現しません」等を
            #   ユーザーへ漏らす弱点(殿指摘2026-07-17)。文単位で落とし、空になれば下段salvageが要約を復元する。
            _META_LEAK = re.compile(r"(表\s*カード|表示装置|描画(済|し|され)|再現(しません|しない|せず|いたしません)|"
                                    r"重複(して)?.{0,8}(一覧|表|再現)|装置と(の)?重複|一覧を(再度)?(再現|再掲)し)")
            _kept = [s for s in re.split(r"(?<=[。\n])", _txt) if s.strip() and not _META_LEAK.search(s)]
            _nod_txt = re.sub(r"\n{3,}", "\n\n", "".join(_kept)).strip()
            if _nod_txt:
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
        if _dm_body_incomplete_hit:                          # cmd_494 2便: 最終送出直前の再強制(下流の一切の継ぎ足し・復元処理を上書きし、完全に差し替える)
            final = _DM_BODY_INCOMPLETE_MSG
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
            if assign_card:                         # M4 Phase1: アサイン提案カード(スロット＋実績候補・押すと/api/assign/commit)
                self.wfile.write((json.dumps({"assign": assign_card}, ensure_ascii=False) + "\n").encode())
            if mine_table:                          # M4 Phase1: 本人のアサイン表(接地・作業者の「アサインある？」への答え)
                self.wfile.write((json.dumps({"table": mine_table}, ensure_ascii=False) + "\n").encode())
            if resched_card:                        # M4 Phase2: 日程変更カード(影響プレビュー＋確定ボタン→/api/reschedule/commit)
                self.wfile.write((json.dumps({"reschedule": resched_card}, ensure_ascii=False) + "\n").encode())
            if mtg_table:                           # M4 Phase2': MTG助言の表(会議前議題/そろそろ定例・読取のみ)
                self.wfile.write((json.dumps({"table": mtg_table}, ensure_ascii=False) + "\n").encode())
            if status_card:                         # M4 Phase4: status更新カード(納品/客先承認/対象外→/api/status/commit)
                self.wfile.write((json.dumps({"status_card": status_card}, ensure_ascii=False) + "\n").encode())
            if minutes_card:                        # M4 Phase3: 議事録→タスク候補カード(検品→/api/minutes/commit)
                self.wfile.write((json.dumps({"minutes": minutes_card}, ensure_ascii=False) + "\n").encode())
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
                    _nuid = str(r.get("notify") or r.get("who") or "")   # M3①: 約束完了を本人へpush(型ON/OFF尊重)
                    if casper_push and _nuid and casper_push.type_enabled(_nuid, "open_loop"):
                        try:
                            casper_push.push_to_uid(_nuid, {
                                "title": "✅ 約束が完了しました",
                                "body": str(r.get("title") or "")[:180],
                                "tag": "casper-openloop", "url": "/", "sticky": False})
                        except Exception:
                            pass
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
# cmd_507症状②: import副作用で常駐スレッド+HTTP待受が起動し検証プロセスと本番が状態ファイル/portを奪い合う件の抑止口。
# 既定は「起こす」(env未設定なら従来通り) — 検証側がimportより前にCASPER_NO_DAEMON=1をsetdefaultする。
_NO_DAEMON = os.environ.get("CASPER_NO_DAEMON", "").strip() not in ("", "0", "false")
# cmd_512第4便申し送り2是正: import副作用の恒久断ち。CASPER_NO_DAEMON未設定時の既定は
# 従来通り「起こす」が生きるが、それは python3 chat_server.py として直接実行された時のみに限る
# (__name__ == "__main__"の外側では、単純`import chat_server`だけでは何も起動しない)。
if __name__ == "__main__" and not _NO_DAEMON:
    _threading.Thread(target=_warm_model_loop, daemon=True).start()   # 起動直後からモデルを温め続ける
    _threading.Thread(target=_profile_worker, daemon=True).start()    # アイドル便乗で個性プロファイル育成
    _threading.Thread(target=_events_puller, daemon=True).start()     # 全社ログ集約(get_events 増分pull)
    _threading.Thread(target=_digest_refresh_loop, daemon=True).start()  # digest をライブ自動更新(RO非依存・恒久)
def _recent_uids(days=14):
    """直近days日にconversation_log.jsonlへ人の端末(ip=172.17.0.1)から現れたuidの集合(list)。
    cmd_505: 画面通知の巡回先を「既定uid+🔔購読者」だけでなく「実際に使っている者」へ広げる本命(案2)。
    weekly_report.pyのHUMAN_IP抽出と同じ源(cmd_502実測済)を読むだけで新たな記録は作らない。
    末尾一定行数のみ読む(ログは伸び続けるため全読みを避ける・brief記載の配慮)。"""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).date().isoformat()
    uids = []
    seen = set()
    try:
        with open(CONVO_LOG, encoding="utf-8") as f:
            lines = f.readlines()[-4000:]
    except Exception:
        return uids
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ip") != "172.17.0.1":
            continue
        u = str(r.get("uid") or "")
        if not u or u in seen:
            continue
        ts = str(r.get("ts") or "")[:10]
        if ts and ts >= cutoff:
            seen.add(u)
            uids.append(u)
    return uids


def _notify_scheduler():
    """M3 司令塔: 常駐して割り込み政策エンジンを定期実行(既定15分毎)。朝ブリーフ(1日1回)＋閾値割り込みを
    通知ストアへ積む。実送信はせず"積む"だけ(承認/配信は別)。対象uidは環境変数 or 既定[28](殿)。"""
    import threading, time as _t
    if not casper_notify:
        return
    base_uids = [u.strip() for u in os.environ.get("CASPER_NOTIFY_UIDS", "28").split(",") if u.strip()]
    interval = int(os.environ.get("CASPER_NOTIFY_INTERVAL", "900"))   # 秒(既定15分)

    def _targets():
        """通知を配る対象uid(動的): 既定(殿)＋push購読のある全ユーザー＋直近14日に実際に使った者(cmd_505)。
        各uidの通知は compute(uid)/_dm_notify_check(uid) が本人のタスク/DMに絞って算出(帰属の混線なし)。
        直近14日を加えるのが本命(案2): 🔔購読していない者でも夜間巡回でpendingへ積まれ、画面表示が
        「積まれた物を読むだけ」で待たされない(AC5)。base_uids/push購読は従来通り残す(AC2/AC4)。"""
        u = list(base_uids)
        try:
            if casper_push:
                for x in casper_push.subscribed_uids():
                    if x not in u:
                        u.append(x)
        except Exception:
            pass
        try:
            for x in _recent_uids(days=14):
                if x not in u:
                    u.append(x)
        except Exception:
            pass
        return u

    def _push_new(uid, notifs):
        """M3配信: 新規の割り込みだけを殿の端末へ Web Push(閉じてても届く)。compute()がdedup済ゆえ再pushしない。"""
        if not (casper_push and notifs):
            return
        for n in notifs:
            if not casper_push.type_enabled(uid, n.get("type") or ""):   # 型別ON/OFF(ユーザー設定)
                continue
            try:
                casper_push.push_to_uid(uid, {
                    "title": n.get("title") or "Casper",
                    "body": (n.get("body") or "")[:180],
                    "tag": n.get("type") or "casper",
                    "url": "/",
                    "sticky": n.get("level") == "warn",
                })
            except Exception:
                pass

    def _propose_stalls(uid, notifs):
        """M3②: 停滞FBの催促DMを承認カード(outbox proposed)として先行生成。宛先=assigned_to別にまとめ、
        本人が開けば1タップ承認で送れる状態にしておく。純機構の定型文(LLM非使用)。dedupはoutbox側(同一key)。"""
        if not casper_outbox:
            return
        try:                                              # project_id → PJ名(承認者が"どのPJか"を判断できるように)
            _pjs = {str(p.get("id")): p.get("name") for p in (json.load(open("/tmp/cal_projects.json")).get("items") or [])}
        except Exception:
            _pjs = {}
        for n in notifs:
            if n.get("type") != "stalled_fb":
                continue
            by = {}
            for it in ((n.get("action") or {}).get("items") or []):
                a = str(it.get("assigned_to") or "")
                if a and a != str(uid):
                    by.setdefault(a, []).append(it)
            for aid, its in by.items():
                bypj = {}                                 # 一人が複数PJに跨る場合はPJ別にまとめる
                for it in its:
                    pn = _pjs.get(str(it.get("project_id"))) or "（PJ不明）"
                    label = f"{(it.get('shot') or '')} {(it.get('name') or '')}".strip()
                    d = it.get("days")
                    bypj.setdefault(pn, []).append("・" + label + (f"（{d}日停滞）" if d else ""))
                sections = [f"【{pn}】\n" + "\n".join(rows) for pn, rows in bypj.items()]
                body = ("お疲れ様です。下記の確認が滞っております。お手すきにご確認いただけますと助かります。\n\n"
                        + "\n\n".join(sections))
                args = {"to_user_id": int(aid) if aid.isdigit() else aid, "body": body}
                try:
                    casper_outbox.propose("send_message", args, str(uid),
                                          _action_summary("send_message", args), origin="auto")
                except Exception:
                    pass

    def _loop():
        _t.sleep(20)                                   # 起動直後は少し待つ(索引ロード等の混雑回避)
        while True:
            try:
                for uid in _targets():
                    new = casper_notify.compute(uid)   # dedup済=新規イベントのみ返る(状態はcompute内で更新)
                    if new:
                        casper_notify.store(uid, new)  # ストアへ積む(pull表示用)
                        _push_new(uid, new)            # 端末へpush(配信)
                        _propose_stalls(uid, new)      # 催促DM下書きを承認カードとして先行生成(M3②)
            except Exception:
                pass
            _t.sleep(interval)
    threading.Thread(target=_loop, daemon=True).start()

    # DM着信は時間に敏感ゆえ、政策ティックとは別に速い巡回(既定2分)で見張り、新着だけをpush(殿御下問2026-07-15)
    dm_interval = int(os.environ.get("CASPER_DM_TICK", "120"))     # 秒(0で無効)

    def _dm_loop():
        _t.sleep(30)
        while True:
            try:
                for uid in _targets():
                    if not (casper_push and casper_push.type_enabled(uid, "dm")):   # DM通知OFFなら巡回スキップ
                        continue
                    fresh = _dm_notify_check(uid)
                    if fresh:
                        n = len(fresh)
                        peers = "、".join(sorted({f["peer"] for f in fresh if f.get("peer")}))
                        casper_push.push_to_uid(uid, {
                            "title": (f"💬 新着DM {n}件" if n > 1 else "💬 新着DM"),
                            "body": (f"{peers} より" if peers else "新しいDMが届きました"),
                            "tag": "casper-dm", "url": "/", "sticky": True})
            except Exception:
                pass
            _t.sleep(max(60, dm_interval))
    if dm_interval > 0 and casper_push:
        threading.Thread(target=_dm_loop, daemon=True).start()


if __name__ == "__main__":
    print(f"Casper chat -> http://localhost:{A.port}  (model {A.model} @ {A.endpoint})", flush=True)
    if not _NO_DAEMON:
        _notify_scheduler()                                    # M3: 常駐スケジューラ起動(先回り通知)
    if not _NO_DAEMON and casper_embed:
        try: casper_embed.ensure_fresh()                   # cmd_498: 起動時に索引の陳腐化を検知→古ければ非同期是正
        except Exception: pass

# HTTPS リスナー(別ポート・非破壊): Web Push の購読はセキュアコンテキスト必須ゆえ、携帯/別端末が https で入れるように。
# 既存 http(8770)はそのまま。証明書(~/.config/casper/casper_cert.pem)が在る時だけ起動。
def _start_https():
    hp = int(os.environ.get("CASPER_HTTPS_PORT", "8443"))
    cdir = os.path.join(os.path.expanduser("~"), ".config", "casper")
    cert = os.path.join(cdir, "casper_cert.pem")
    key = os.path.join(cdir, "casper_key.pem")
    if hp <= 0 or not (os.path.exists(cert) and os.path.exists(key)):
        return
    try:
        import ssl as _ssl, threading as _th
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)

        def _serve():
            httpsd = ThreadingHTTPServer(("0.0.0.0", hp), H)
            httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
            print(f"Casper chat -> https://localhost:{hp}  (Web Push対応・TLS)", flush=True)
            httpsd.serve_forever()
        _th.Thread(target=_serve, daemon=True).start()
    except Exception as _e:
        print(f"[https] 起動失敗: {_e}", flush=True)


# cmd_509第1便: pidfile自己申告(新設)。supervisorの「自陣の前世代を畳む口」が
# 五点検証の一つとして突合する自己申告情報。他者検証(cmdline/starttime/uid/cwd)
# だけでは偽装/陳腐化に弱いため、chat_server自身が起動時に自分のpidを書き、
# 終了時に消す(二重の担保・軍師point_c裁定)。
_REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
# CASPER_PIDFILE で差替可(検証側が本番と同じ既定パスを奪い合わぬための逃げ道)。
PIDFILE = os.environ.get("CASPER_PIDFILE") or os.path.join(_REPO_ROOT, "queue", "casper_chat_server.pid")


def _write_pidfile():
    try:
        os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
        with open(PIDFILE, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": A.port}, f)
    except Exception as _e:
        print(f"[pidfile] 書出し失敗: {_e}", flush=True)


def _remove_pidfile():
    try:
        with open(PIDFILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("pid") == os.getpid():   # 自分が書いたpidfileのみ消す(他世代のものを誤って消さぬ)
            os.remove(PIDFILE)
    except Exception:
        pass


if __name__ == "__main__" and not _NO_DAEMON:
    import atexit as _atexit
    import signal as _signal
    _write_pidfile()
    _atexit.register(_remove_pidfile)

    def _on_term_signal(signum, frame):
        _remove_pidfile()
        raise SystemExit(0)
    _signal.signal(_signal.SIGTERM, _on_term_signal)
    _signal.signal(_signal.SIGINT, _on_term_signal)

    _start_https()
    # cmd_510第3便(観測の機構・軍師addendum設計): 真の復帰時刻をchat_server自身に自己申告させる
    # (pidfile自己申告=cmd_509第1便と同じ型)。HTTPで外から叩いて確認しない
    # (観測のために本番へ負荷をかけては本末転倒・軍師addendum「retrieve-then-renderと同じ思想」)。
    print(f"[{__import__('datetime').datetime.now().strftime('%F %T')}] listen開始 port={A.port} pid={os.getpid()}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
