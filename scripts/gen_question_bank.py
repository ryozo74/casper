#!/usr/bin/env python3
"""逆インタビューの質問バンクを **Opus(claude CLI 迂回)** で事前生成する。
find_gaps 相当で vault の『未記入の穴』を集め、Opus に良質な選択式質問を一括生成させ
question_bank.jsonl に保存。/learn はここから即座に出題する(切れたらライブ生成にフォールバック)。

実行: python3 gen_question_bank.py        (既定 model=opus)
"""
import glob
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(HERE, "..", "vault")
BANK = os.path.join(HERE, "question_bank.jsonl")
CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN", "claude")
MODEL = os.environ.get("CASPER_BANK_MODEL", "opus")


def find_gaps(max_gaps=80):
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
                gaps.append({"kind": "person", "target": name, "desc": desc, "attr": sec})
    for p in sorted(glob.glob(os.path.join(VAULT, "50_asset_shadows", "*.md"))):
        t = open(p, encoding="utf-8").read()
        m = re.search(r"ニュアンス[・･].{0,6}教訓.*?\n(.*?)(?=\n##|\Z)", t, re.S)
        if m and len(re.sub(r">.*", "", m.group(1)).strip()) < 8:
            nm = re.search(r"name:\s*(.+)", t)
            gaps.append({"kind": "asset", "target": (nm.group(1).strip() if nm else os.path.basename(p)),
                         "desc": "運用上の注意・教訓・改善点", "attr": "ニュアンス・教訓"})
    return gaps[:max_gaps]


def main():
    gaps = find_gaps()
    if not gaps:
        print("穴なし — 生成不要")
        return
    listing = "\n".join(
        f"{i+1}. 対象『{g['target']}』 / 未記入={g['desc']} / 種別={g['kind']}"
        for i, g in enumerate(gaps))
    prompt = (
        "あなたは社内知識の解像度を上げる逆インタビュアー Casper。"
        "以下の『vault に未記入の穴』それぞれに対し、ユーザー(社員)が選ぶだけで答えられる"
        "良質な質問を1つずつ作れ。条件: ①具体的で一段深い解像度を引き出す ②既知の再確認でない"
        "③選択肢は対象に即した現実的な候補を3つ＋『その他』。"
        "出力は **JSON 配列のみ**(前後に文章・コードフェンスを付けるな)。"
        '各要素は {"target":..., "attr":..., "question":..., "choices":[...]}。\n\n'
        "穴一覧:\n" + listing)
    r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                       capture_output=True, text=True, timeout=900, cwd="/tmp")
    out = (r.stdout or "").strip()
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        print("生成失敗。出力先頭:", out[:300])
        return
    arr = json.loads(m.group(0))
    n = 0
    with open(BANK, "w", encoding="utf-8") as f:
        for q in arr:
            if not (q.get("question") and q.get("choices")):
                continue
            ch = [c for c in q["choices"] if c]
            if "その他" not in ch:
                ch.append("その他")
            f.write(json.dumps({"target": q.get("target", ""), "attr": q.get("attr", ""),
                                "question": q["question"], "choices": ch, "src": MODEL},
                               ensure_ascii=False) + "\n")
            n += 1
    print(f"質問バンク生成: {n} 問 -> {BANK}")


if __name__ == "__main__":
    main()
