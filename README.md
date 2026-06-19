# Casper — 作業場 (1st 領 / git-ignored)

入力負担ゼロの伴走型AI。**デュアルブレイン**: 左脳=Score/Calendar API(定量ハードデータ) ×
右脳=Obsidian(定性・暗黙知)。構想全容は memory `project_casper_concept` 参照。

## 構成
```
projects/casper/
  README.md                  ← これ
  vault/                     ← 右脳: Obsidian vault (Windows の Obsidian で開く)
  scripts/read_score_db.py   ← 左脳リーダー (Score SQLite 読取専用)
  docs/db_understanding_verification.md  ← DB理解検証の記録
```

## Obsidian vault を開く
Windows の Obsidian アプリ →「フォルダーをVaultとして開く」→
`H:\multi-agent-shogun-main\projects\casper\vault` を選択。

## テスト第一段 (殿御下命 2026-06-17) — 進捗
目的:「まずは DB への理解が正しいかを確認」
- [x] **DB 読み取り** — `read_score_db.py` 実装・実走。理解は正しいと実証 (docs/db_understanding_verification.md)
- [x] **Obsidian 導入** — vault 骨格新設 (下記)
- [x] **ローカルLLM アクセス** — z8a Ollama qwen3:14b 疎通確認 (docs/local_llm_access.md)
- [x] **統合**: LLM に DB schema を渡し理解を説明させ突合 → **✅PASS** (全5表+システム種別を正答)
- ⚠️ **PII egress 制約**: 実データ→LLM は分類器が遮断。本実装は殿の明示許可要 (docs 参照)
- ⏳ **Calendar DB 広域アクセス** — ニブ待ち (殿御下命)

## 左脳リーダーの使い方
```bash
python3 projects/casper/scripts/read_score_db.py          # 人間可読
python3 projects/casper/scripts/read_score_db.py --json   # LLM 投入用
```
