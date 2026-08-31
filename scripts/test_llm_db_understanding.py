#!/usr/bin/env python3
"""Casper 統合テスト: ローカルLLM(z8a Ollama)が Score DB を読み、理解が正しいか検証。

フロー: read_score_db.py で左脳DBを読取 → その JSON を qwen3:14b に渡し
「このDBは何か/各表の役割/気づいたデータ特性」を説明させる → 人間(将軍)が突合。

Usage: python3 test_llm_db_understanding.py
依存: 標準ライブラリのみ。Ollama endpoint へ LAN 到達できること。
"""
import argparse, json, subprocess, sys, os, urllib.request

MODEL = "qwen3:14b"
HERE = os.path.dirname(__file__)

ap = argparse.ArgumentParser()
# 宛先 endpoint をコマンドに露出させる(分類器の宛先審査・殿の明示 allow 用)
# ★2026-08-31: 既定を禁足席に焼き付けておらぬ(台帳=casper_endpoint から引く)。
#   cron や手動の一発物は env を source せぬゆえ、焼き付き既定は必ずいつか実運用へ漏れる。
try:
    import casper_endpoint as _ep0
    _DEF_EP = _ep0.gen_endpoint(strict=False)
except Exception:
    _DEF_EP = "http://127.0.0.1:11434"          # 台帳が読めぬ時は手元へ(他人の機を叩かぬ)
ap.add_argument("--endpoint", default=_DEF_EP,
                help="Ollama base URL (既定は casper_endpoints.env の台帳)")
# --full: 実データ(PII含む)を送出。既定は PII を除く schema-only。
ap.add_argument("--full", action="store_true",
                help="send real rows incl. PII (requires 殿's egress grant)")
a = ap.parse_args()
OLLAMA = a.endpoint.rstrip("/") + "/api/chat"

# 1) 左脳DBを読取 (read_score_db.py --json)
raw = subprocess.check_output(
    [sys.executable, os.path.join(HERE, "read_score_db.py"), "--json"], text=True)
db = json.loads(raw)

if a.full:
    payload = db  # 実データ(サンプル行)込み — 本番 Casper の経路を検証
    mode_note = "スキーマ＋実データ(サンプル行)"
    q3 = "user_id 系カラムのデータ形式に気づいた特徴はあるか(あれば具体的に)"
else:
    payload = {"tables": {  # PII を一切送らない
        t: {"row_count": info["row_count"],
            "columns": [{"name": c["name"], "type": c["type"]} for c in info["columns"]]}
        for t, info in db["tables"].items()}}
    mode_note = "スキーマのみ(実データ無し)"
    q3 = "列名から、user_id 系カラムには何が入ると推測されるか"

# 2) LLM へ問う
prompt = f"""あなたは社内システムのDBを分析するアシスタントです。
以下は、あるSQLite DBの{mode_note}(JSON)です。これだけを根拠に答えてください。

{json.dumps(payload, ensure_ascii=False)}

次を簡潔な日本語で答えよ:
1. このDBはどんな業務システムのものと推測されるか
2. 各テーブルの役割を1行ずつ
3. {q3}
/no_think"""

req = urllib.request.Request(
    OLLAMA,
    data=json.dumps({"model": MODEL,
                     "messages": [{"role": "user", "content": prompt}],
                     "stream": False}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=180) as r:
    resp = json.load(r)

answer = resp["message"]["content"]
print("=" * 70)
print("LLM (qwen3:14b @ z8a) の DB 理解:")
print("=" * 70)
print(answer.strip())
print("=" * 70)
print(f"[meta] model={resp.get('model')} "
      f"eval_count={resp.get('eval_count')} "
      f"total_ms={resp.get('total_duration',0)//1_000_000}")
