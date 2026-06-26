# Casper — 入力負担ゼロの伴走型AI (1st 領 / git-ignored)

**デュアルブレイン**: 左脳=Calendar/Score API(定量ハードデータ) × 右脳=Obsidian vault(定性・暗黙知)。
構想全容は memory `project_casper_concept` 参照。本書は**現状(2026-06-24時点)の実装・機能・運用**の要約。

## アーキテクチャ
```
ブラウザ(chat.html) ── HTTP ──> chat_server.py :8770
                                   ├─ qwen3.6:27b @ Ollama 192.168.44.119:11434 (think:false / keep_alive)
                                   ├─ 左脳: casper_mcp.py → Nibu Calendar MCP (192.168.44.253:8001/mcp/)
                                   │         + casper_tools (readonly REST)
                                   ├─ 右脳: casper_rag / casper_embed (Obsidian vault 全文/意味検索)
                                   ├─ casper_vimeo.py → Vimeo API (studiobokan)
                                   └─ casper_aurora.py → Aurora/HTML Archive Server (Elvis・準備中)
```

### 主要スクリプト (scripts/)
- `chat_server.py` — 本体HTTPサーバ。qwen 自律tool-calling(最大6反復)＋RAG/digest注入＋図解(mermaid/canvas)。
- `chat.html` — チャットUI(DMセクション/添付/ツールメニュー/承認カード)。
- `casper_mcp.py` — Calendar MCP ブリッジ(tools/list 動的取込・write token + X-Actor-User-Id)。
- `casper_vimeo.py` — Vimeo 検索/アップロード(tus)/パスワード設定。
- `casper_aurora.py` — Aurora(HTML Archive Server)連携: 接続層(MCP流用・config駆動)＋筆(`note_html`=整ったHTMLノート生成)。
- `casper_tools.py` / `casper_rag` / `casper_embed` — 左脳REST・右脳RAG。
- `casper_context.md` — Casper が毎回読む社内ナレッジ要約(左脳+右脳 digest)。

## 機能(現状)
### 会話AI
qwen が tool を自律呼出。左脳(Calendar)・右脳(vault RAG)・各種digestを system に注入し、社内の具体事実に基づき回答。捏造禁止・簡潔・締め文句禁止。

### DM秘書
- **開門ブリーフィング**: 起動時に未読DMを時系列リスト表示(相手＋冒頭)。
- **サイドバー**: 未読(新規)DMスレッド一覧。クリックで会話全文を**送信者名＋時刻付き・新規表示**。
- **返信/質問**: DM閲覧中はメッセージ欄を無効化→[Scoreで見る][✍返信][❓質問]ボタン。返信=本人名義代筆(Stage2承認カード)、質問=社内知識回答。いずれも**スレッド化**保存。
- **既読化**: ✅ニブ殿提供の `mark_read(actor_id, thread_id)` を dm_messages に結線済。**Casperで開いたら自動既読化**(未読リストから落ちる)。
- **宛先誤り対策**: 社内名簿(username→uid)＋通称辞書(Elvis=ou)を注入・**推測禁止**・未知名は送らず確認・承認カードに宛先名を赤枠強調。

### Vimeo (studiobokan アカウント・共有トークン)
- **ライブ検索**: アカウント全動画(~2567本・公開/非公開問わず)を名前検索 → `vimeo_search` ツール。
- **アップロード**: ブラウザから Vimeo へ**直接 tus 分割送信**(サーバ経由せず・大容量GB級OK)。添付→「🎬 Vimeoへアップ」→タイトル/説明/パスワード入力。
- **パスワード付き公開＋共有リンク**: hash付きリンク(`vimeo.com/ID/HASH`)を返却・チャットにプレイヤー埋め込み。`vimeo_set_password` ツール。
- トークン: `.casper_vimeo_token`(scope: upload/edit/create/delete・git管理外)。

### Aurora 連携(共有ノート図書館・**稼働中** 2026-06-25)
**Aurora = ユーザー紐づきの会社の共有図書館。Casper はその司書。** 詳細設計: `docs/aurora_design_memo.md`。
- **構成**: 棚=Elvis殿の HTML Archive Server(FastAPI/SQLite FTS5/FastMCP・`http://192.168.44.155:8100/mcp/`) / 筆=note整形(markdown→HTML) / 司書=Casper(検索・サジェスト・作成)。
- **狙い**: 全ノートを Casper が検索・理解し、会話中に関連ノートを能動サジェスト。作成は双方向。追記・削除・復元は **Git的に履歴**。
- **結線済ツール**: `aurora_search`(全文検索)/`aurora_get`(本文取得)/`aurora_create`(新規作成・**書込はStage2承認制**)。backend は計8ツール(read4+write4)。
- **接続**: `.casper_aurora`(AURORA_MCP_URL＋write token・git管理外・600)。anti-spoof=Calendar同型(actor_idで実uid担保)。write token が read も兼ねる。
- **すみ分け**: Elvis殿=棚と倉庫番(保存/履歴/検索基盤/書込口/認証)、こちら=司書と筆(検索/サジェスト/HTML生成/結線)。

### その他
- **編集ロールバック**: 各ユーザー発言の「✏️編集」で、その地点まで巻き戻し→打ち直し→再生成。
- **/import**: Excel → プロジェクト/ショット/タスク → Calendar CSV。不足必須項目は vault から推測し赤字提示。
- **逆インタビュー(/learn)**: 質問バンク(209問)で穴ドリブン知識化。
- **認証**: `/api/login`(Calendar OAuth→uid を JWT に格納し identify が確実解決)。Vimeo等の共有トークンは利用者の権限不問。

## 運用
- **supervisor** (`scripts/casper_supervisor.sh`): `chat_server.py` の変更を**自動リロード(手動再起動不要)**＋死活監視(server＋メンション番犬)＋qwenモデル温存(warm-upで冷間15〜40秒の再ロードを防ぐ)。
  - ⚠️ 監視対象は `chat_server.py` のみ。`casper_vimeo.py`/`casper_mcp.py` 等を変えたら `touch chat_server.py` で反映。
- **ログ**(全マシンの会話がサーバ集約・ここから閲覧可): `dev_log.jsonl` / `conversation_log.jsonl` / `vimeo_debug.log` / `dm_debug.log`。
- 起動: supervisor が `python3 chat_server.py --endpoint http://192.168.44.119:11434 --model qwen3.6:27b --port 8770` を管理。

## 保留・申し送り
- **Aurora連携**: Elvis殿の書込MCP/履歴/更新検知/Bearer(Q1〜Q5)到着待ち → `casper_aurora.py` に結線＋サジェスト/索引同期を実装。詳細 `docs/aurora_design_memo.md`。
- vision のローカル化(殿「後ほど」保留・現状クラウドSonnet)。
- supervisor 監視対象の拡張(casper_vimeo.py / casper_aurora.py 等)は次回 supervisor 再起動時に。
