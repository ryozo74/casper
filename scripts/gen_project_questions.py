#!/usr/bin/env python3
"""プロジェクト種別フォーカスの逆インタビュー質問バンクを Opus で事前生成。
狙い: 完了/進行PJで『次の類似PJに効く知見(段取り・落とし穴・見積・ツール・体制)』を選択式で引き出し、
project_question_bank.jsonl に保存。/learn が人物バンクと併せて出題する。

実行: python3 gen_project_questions.py   (model=opus)
"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "project_question_bank.jsonl")
CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN", "claude")
MODEL = os.environ.get("CASPER_BANK_MODEL", "opus")

# 自社が手掛けるPJ種別(汎用)。新種別はここに足せば質問が増える。
PROJECT_KINDS = [
    "TVCM / 広告映像",
    "MV (ミュージックビデオ)",
    "VFX / 実写合成 (映画・ドラマ)",
    "3DCG アニメーション",
    "LED / プロジェクションマッピング・展示映像",
    "ゲーム内映像 / プロモーション映像",
    "バーチャルプロダクション (VP / インカメラVFX)",
    "モーションキャプチャ / パフォーマンスキャプチャ",
]

# 各種別で引き出したい知見の軸
ASPECTS = "段取り/工程の勘所、よくある落とし穴・リスク、見積/工数の目安、必要な体制・人員、使うツール・パイプライン、クライアント対応で注意する点"


def main():
    listing = "\n".join(f"{i+1}. {k}" for i, k in enumerate(PROJECT_KINDS))
    prompt = (
        "あなたは制作スタジオの知見を蓄積する逆インタビュアー Casper。"
        "以下の各『プロジェクト種別』について、経験者(PM/監督/アーティスト)が選ぶだけで答えられ、"
        f"かつ『次に類似PJが来た時の参考になる知見』({ASPECTS})を引き出す良質な質問を、各種別2問ずつ作れ。"
        "条件: ①具体的で実務的 ②選択肢は現実的な候補3つ＋『その他』 ③種別の特性を踏まえる。"
        "出力は **JSON配列のみ**(前後の文章・コードフェンス禁止)。"
        '各要素 {"kind":<種別>, "aspect":<軸>, "question":<質問>, "choices":[...]}。\n\n'
        "種別一覧:\n" + listing)
    r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                       capture_output=True, text=True, timeout=900, cwd="/tmp")
    out = (r.stdout or "").strip()
    import re
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        print("生成失敗:", out[:300]); return
    arr = json.loads(m.group(0))
    n = 0
    with open(BANK, "w", encoding="utf-8") as f:
        for q in arr:
            if not (q.get("question") and q.get("choices")):
                continue
            ch = [c for c in q["choices"] if c]
            if "その他" not in ch:
                ch.append("その他")
            f.write(json.dumps({"kind": q.get("kind", ""), "aspect": q.get("aspect", ""),
                                "question": q["question"], "choices": ch, "src": MODEL,
                                "type": "project"}, ensure_ascii=False) + "\n")
            n += 1
    print(f"PJ種別 質問バンク生成: {n}問 -> {BANK}")


if __name__ == "__main__":
    main()
