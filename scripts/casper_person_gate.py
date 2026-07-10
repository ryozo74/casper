#!/usr/bin/env python3
"""Casper 人ごと理解ゲート(per-user comprehension gate) — 入力の接地(Fable諮問 2026-07-10)。

殿構想「人それぞれの表現を理解し、カスタムでき、必要なら聞き返すゲート」。
Fable設計: 新しい関所を建てず、既存装置(選択カード/action_router/traits)に per-user のデータ面を一枚敷く。

原理:
- 曖昧さ=スロット(対象/属性/宛先/形式)が空 or 複数候補。弱モデルには推測させず、機構がその人の既定/別名で解決。
- 出力は「クエリ書き換え」でなく「スロット注釈(サイドチャネル)」。書き換え=黙った推測の亜種ゆえ禁止。
- 加算的オーバーレイ: プロファイルが無ければ resolve() は空を返す=今日のCasperそのもの(優雅な劣化を構造で保証)。

真実源: person_gate/{uid}.yaml (人が編集できるデータ)。スキーマ:
  aliases:        [{say, ref:{type,id,name}, status:active|candidate, uses, misses, last_used, evidence}]
  facet_defaults: [{intent, facet}]           # 空ファセットの既定(read-only限定)
  ask_style:      eager|balanced|lazy
  format_prefs:   {list: table, ...}
"""
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GATE_DIR = os.path.join(HERE, "person_gate")
SIGNALS = os.path.join(HERE, "gate_signals.jsonl")   # ③ 選択カードの選択ログ(教師信号・人ごと既定を学ぶラベル生成)

# 意図の型 = 既存ハンドラ名(Fable: 既存ルート名をそのまま enum に)。facet_defaults の intent はこれに一致させる。
_INTENT_RES = [
    ("active_task_q", re.compile(r"(動いて(る|いる)|進行中|稼働|作業(され|中|して)).{0,6}(タスク|もの|案件)|"
                                 r"タスク.{0,8}(動いて|進行|稼働|作業)|(今日|本日|今).{0,8}(何|どんな|どの).{0,6}(タスク|作業|案件)")),
    ("project_q", re.compile(r"(動いて(る|いる)|進行中|稼働).{0,4}(プロジェクト|PJ|案件)|(プロジェクト|PJ|案件).{0,8}(一覧|教え|どれ|ある)")),
    ("meeting_q", re.compile(r"議事録|会議|ミーティング|打ち合わせ|定例")),
    ("portfolio_q", re.compile(r"実績|ポートフォリオ|portfolio|作品|制作事例")),
]


def _profile(uid):
    if not uid:
        return {}
    p = os.path.join(GATE_DIR, f"{uid}.yaml")
    try:
        import yaml
        return yaml.safe_load(open(p, encoding="utf-8")) or {}
    except Exception:
        return {}


def _intent_of(query):
    for name, rx in _INTENT_RES:
        if rx.search(query or ""):
            return name
    return None


def resolve(uid, query, convo=None):
    """発話をその人のプロファイルで解決したスロット注釈を返す。プロファイル無し=空dict(現行動作)。
    返り: {facet, alias_refs:[{say,ref}], ask_style, format, digest, intent}。digest は sysadd へ一行注入する背景。"""
    prof = _profile(uid)
    if not prof:
        return {}
    q = query or ""
    intent = _intent_of(q)
    out = {"intent": intent, "ask_style": prof.get("ask_style", "balanced"),
           "format": (prof.get("format_prefs") or {})}
    lines = []

    # (a) alias 決定的ルックアップ: その人の言い回し→正規参照(status:active のみ・解釈に使うのはactive)
    hits = []
    for a in (prof.get("aliases") or []):
        if a.get("status", "active") != "active":
            continue
        say = str(a.get("say") or "").strip()
        if say and say in q:
            hits.append(a)
    if hits:
        out["alias_refs"] = [{"say": a.get("say"), "ref": a.get("ref")} for a in hits]
        for a in hits:
            r = a.get("ref") or {}
            nm = r.get("name") or r.get("id") or "?"
            lines.append(f"『{a.get('say')}』はこの方の言い回しで **{r.get('type','対象')}「{nm}」**を指す(前提として扱え)")

    # (b) facet_defaults: 空ファセットの既定(read-only 意図のみ)。前提明示を促す。
    if intent:
        for fd in (prof.get("facet_defaults") or []):
            if fd.get("intent") == intent and fd.get("facet"):
                out["facet"] = fd["facet"]
                _jp = {"deadline": "締切/納期", "assignee": "担当", "status": "状態/進捗", "list": "一覧"}.get(fd["facet"], fd["facet"])
                lines.append(f"この方は『{intent}』の問いでまず **{_jp}** を見たい傾向(その軸を主に据え、"
                             f"『{_jp}を主にお答えします』と前提を一言添えよ)")
                break

    if lines:
        out["digest"] = "\n\n## 【この方の理解ゲート(per-user・前提)】\n- " + "\n- ".join(lines)
    return out


# ── ③ 学習ループ: 選択カードの選択を教師信号に(Fable本命=カードが自らを減らす=北極星への収束) ──
def record_selection(uid, card_type, prompt, chosen_label, chosen_ref=None):
    """人が選択カードで選んだ結果を記録。集計して「毎回同じ選択」を per-user 既定の candidate に昇格提案する素材。"""
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "uid": str(uid or ""),
               "card_type": card_type, "prompt": (prompt or "")[:120],
               "chosen": (chosen_label or "")[:80], "ref": chosen_ref}
        with open(SIGNALS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def promote_candidates(win=5, need=4):
    """gate_signals を人×card_type で集計。直近 win 件中 need 件以上が同一選択なら、
    その人の person_gate に candidate を提案(status:candidate=解釈には使わない・人が active へ昇格)。返り=提案件数。"""
    if not os.path.exists(SIGNALS):
        return 0
    import collections
    by = collections.defaultdict(list)
    for ln in open(SIGNALS, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        by[(r.get("uid"), r.get("card_type"))].append(r)
    proposed = 0
    for (uid, ctype), recs in by.items():
        recent = recs[-win:]
        if len(recent) < need:
            continue
        cnt = collections.Counter(r.get("chosen") for r in recent)
        top, n = cnt.most_common(1)[0]
        if n < need:
            continue
        prof = _profile(uid) or {}
        cands = prof.get("candidates") or []
        key = f"{ctype}:{top}"
        if any(c.get("key") == key for c in cands):        # 既に提案済み→重複しない
            continue
        cands.append({"key": key, "status": "candidate",
                      "note": f"『{ctype}』の選択カードで直近{len(recent)}件中{n}件『{top}』を選択",
                      "evidence": f"{recent[-1].get('ts','')} 時点", "uses": n})
        prof["candidates"] = cands
        try:
            import yaml
            os.makedirs(GATE_DIR, exist_ok=True)
            with open(os.path.join(GATE_DIR, f"{uid}.yaml"), "w", encoding="utf-8") as f:
                yaml.safe_dump(prof, f, allow_unicode=True, sort_keys=False)
            proposed += 1
        except Exception:
            pass
    return proposed


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "promote":
        print("candidate提案:", promote_candidates())
    else:
        uid = sys.argv[1] if len(sys.argv) > 1 else "28"
        q = sys.argv[2] if len(sys.argv) > 2 else "現在動いているタスクは？"
        print(json.dumps(resolve(uid, q), ensure_ascii=False, indent=1))
