#!/usr/bin/env python3
"""Casper learn_bank — 自己改善ループ 柱1(flywheel)の心臓(Fable5設計・北極星)。

人が触れた成果物(承認時の編集差分 body_orig / 🙅修正リスト corrections / feedback の負例)を
"教師信号"として集め、クラウド(claude)で命令形一文の"規則(lesson)"に蒸留し learn_bank.jsonl へ。
fewshot_digest がこれを類似上位数件だけプロンプト末尾に注入し、qwenの"型"を矯正する(使うほど賢くなる)。

鉄則(Fable): ①モデル自身の判断を教師にするな=人が触れたものだけ ②蒸留はクラウド(qwenに自己蒸留させぬ)
③規則一文のみ・DM全文は入れぬ(num_ctx溢れ+丸写し防止) ④rejectedから学ぶな(方向情報なし)
⑤PII: クラウドへ出す前に人名スクラブ ⑥上限50則・(古さ×未使用)で追出し。

レコード: {id, ts, kind: edit|correction|feedback, situation, model_out, human_final, lesson,
          embedding_key, uses, score}
"""
import datetime
import json
import os
import re
import subprocess
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "learn_bank.jsonl")
FEEDBACK = os.path.join(HERE, "feedback_log.jsonl")
CORRECTIONS = os.path.join(HERE, "corrections.jsonl")
OUTBOX = os.path.join(HERE, "casper_outbox.jsonl")
ROSTER = os.path.join(HERE, "roster_cache.json")
SEEN = os.path.join(HERE, ".learn_bank_seen.json")     # 既処理signal id(二重蒸留防止)
MAX_RULES = 50
CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN") or "claude"
CLI_MODEL = os.environ.get("CASPER_CLI_MODEL", "sonnet")
CLI_CWD = "/tmp/casper_cli"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_jsonl(p):
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _pii_scrub(text):
    """クラウドへ出す前に人名を〈相手〉へ(社内PII egress方針・Fable⑤)。"""
    t = str(text or "")
    try:
        roster = json.load(open(ROSTER, encoding="utf-8"))
        names = sorted({str(v) for v in roster.values() if v}, key=len, reverse=True)
        for nm in names:
            if len(nm) >= 2:
                t = re.sub(re.escape(nm), "〈相手〉", t)
    except Exception:
        pass
    return t


def _gather_signals():
    """人が触れた教師信号を集める(未処理のみ)。返り=[{sid, kind, situation, model_out, human_final}]。"""
    seen = set()
    if os.path.exists(SEEN):
        try:
            seen = set(json.load(open(SEEN, encoding="utf-8")))
        except Exception:
            pass
    sigs = []
    # ① corrections(🙅修正リスト・what_was_wrong=方向情報あり=最上質)
    for c in _load_jsonl(CORRECTIONS):
        sid = "corr:" + str(c.get("id"))
        if sid in seen:
            continue
        sigs.append({"sid": sid, "kind": "correction",
                     "situation": _pii_scrub(c.get("question", "")),
                     "model_out": _pii_scrub((c.get("answer_excerpt") or ""))[:300],
                     "human_final": _pii_scrub(c.get("what_was_wrong", ""))})
    # ② outbox の body_orig(承認時の編集差分=モデル案→人の完成形の三つ組・最上質)
    for r in _load_jsonl(OUTBOX):
        if not r.get("body_orig"):
            continue
        sid = "edit:" + str(r.get("id"))
        if sid in seen:
            continue
        final_body = (r.get("args") or {}).get("body", "")
        sigs.append({"sid": sid, "kind": "edit",
                     "situation": _pii_scrub(r.get("query", "") or r.get("summary", "")),
                     "model_out": _pii_scrub(r.get("body_orig", ""))[:300],
                     "human_final": _pii_scrub(final_body)[:300]})
    # ③ feedback の負例(wrong_*・方向は弱いがパターン抽出には有用・good/rejectedは除外=Fable④)
    for f in _load_jsonl(FEEDBACK):
        v = str(f.get("verdict", ""))
        if not v.startswith("wrong"):
            continue
        sid = "fb:" + str(f.get("ts")) + ":" + str(f.get("question", ""))[:20]
        if sid in seen:
            continue
        sigs.append({"sid": sid, "kind": "feedback",
                     "situation": _pii_scrub(f.get("question", "")),
                     "model_out": _pii_scrub(f.get("answer_excerpt", ""))[:200],
                     "human_final": f"利用者が『{v}』(欲しい内容と違う/形式が違う)と評価"})
    return sigs, seen


_DISTILL_SYS = (
    "あなたはAIアシスタントCasperの品質改善担当。以下は『Casperの応答に人が下した修正・指摘・低評価』の記録。"
    "各記録から、Casperが次に同じ状況で守るべき**命令形の規則を一文**抽出せよ。\n"
    "規則の条件: ①命令形一文(40字以内目安) ②具体的な状況が再現できる語を含む(例『進行中タスクを問われたら』)"
    "③固有名詞・DM全文は入れない(〈相手〉のまま) ④『〜に注意』でなく『〜せよ/〜するな』の断定形。\n"
    "重複・些末は捨て、本質的な規則だけJSON配列で返せ: "
    '[{"situation":"どんな状況か短く","lesson":"命令形の規則一文"}]  JSON以外書くな。'
)


def _distill(sigs):
    """信号群→claude(cloud)で規則群に蒸留。返り=[{situation, lesson}]。"""
    if not sigs:
        return []
    lines = []
    for i, s in enumerate(sigs[:40]):
        lines.append(f"[{i+1}] 状況:{s['situation'][:80]} / Casper応答:{s['model_out'][:120]} / "
                     f"人の修正・指摘:{s['human_final'][:120]}")
    prompt = _DISTILL_SYS + "\n\n記録:\n" + "\n".join(lines)
    try:
        os.makedirs(CLI_CWD, exist_ok=True)
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", CLI_MODEL], input=prompt,
                           capture_output=True, text=True, timeout=300, cwd=CLI_CWD)
        out = (r.stdout or "").strip()
        m = re.search(r"\[.*\]", out, re.S)
        return json.loads(m.group(0)) if m else []
    except Exception as e:
        print("[distill error]", e)
        return []


def _dedup_key(lesson):
    return re.sub(r"\s+", "", str(lesson))[:40]


def run():
    sigs, seen = _gather_signals()
    if not sigs:
        print("新規の教師信号なし(既に蒸留済)。")
        return 0
    print(f"教師信号 {len(sigs)}件を蒸留中(claude {CLI_MODEL})…")
    rules = _distill(sigs)
    if not rules:
        print("蒸留結果なし。")
        return 0
    bank = _load_jsonl(BANK)
    existing = {_dedup_key(b.get("lesson")) for b in bank}
    added = 0
    for ru in rules:
        lesson = str(ru.get("lesson", "")).strip()
        if not lesson or _dedup_key(lesson) in existing:      # 重複はスキップ(簡易・後にembed cosine>0.9へ)
            continue
        existing.add(_dedup_key(lesson))
        bank.append({"id": uuid.uuid4().hex[:10], "ts": _now(), "kind": "distilled",
                     "situation": str(ru.get("situation", ""))[:80], "lesson": lesson[:120],
                     "embedding_key": str(ru.get("situation", "")) + " " + lesson, "uses": 0, "score": 1.0})
        added += 1
    # 上限50則: (古さ×未使用)で追出し=uses昇順→ts昇順
    bank.sort(key=lambda b: (b.get("uses", 0), b.get("ts", "")))
    bank = bank[-MAX_RULES:]
    tmp = BANK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for b in bank:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    os.replace(tmp, BANK)
    for s in sigs:
        seen.add(s["sid"])
    json.dump(sorted(seen), open(SEEN, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"蒸留完了: 新規 {added}則 / bank計 {len(bank)}則 → {BANK}")
    return added


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        for b in _load_jsonl(BANK):
            print(f"  [{b.get('uses',0)}回] {b.get('lesson')}  ({b.get('situation','')})")
    else:
        run()
