#!/usr/bin/env python3
"""Casper 評価ハーネス(ゴールデンセット/リグレッション) — Fable5棚卸し #5-1/5-2の実装。

過去の失態を「決定的アサーション(プログラム判定)」のテストケースにし、あらゆる変更
(プロンプト/モデル/index/num_ctx)の後に自動実行して回帰を捕まえる。LLM-judgeは使わない
(Casperの失敗モードはほぼ全て機械判定可能=安く速く再現可能・Fable 5-2)。

使い方:
  python3 casper_eval.py              # 全ケース実行
  python3 casper_eval.py <部分名>     # 名前に一致するケースだけ
実装:
  各ケースを localhost:8770/api/chat へ流し、応答ストリーム(text＋confirmカード)を復元して
  アサーション群を評価。承認カードの有無(=アクション台帳のレシート)も判定材料にする。
"""
import json
import os
import re
import sys
import urllib.request

ENDPOINT = os.environ.get("CASPER_EVAL_ENDPOINT", "http://localhost:8770/api/chat")
ACTOR = os.environ.get("CASPER_EVAL_ACTOR", "28")
HERE = os.path.dirname(os.path.abspath(__file__))
CASES_JSONL = os.path.join(HERE, "cases.jsonl")            # 昇格済ゴールデン(人が承認したもの)
PENDING_JSONL = os.path.join(HERE, "cases_pending.jsonl")  # 失敗トレース由来の候補(人の審査待ち)
TRACE_JSONL = os.path.join(HERE, "casper_trace.jsonl")


def _online_pj_names():
    """現在online のPJ名(アサーションの真実源に使う・ハードコード陳腐化を避ける)。"""
    try:
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        return [p.get("name") for p in items if str(p.get("display_status") or "online") == "online" and p.get("name")]
    except Exception:
        return []


def run_chat(messages, thread="eval"):
    """/api/chat へPOSTし、(text, cards) を復元。text=連結本文、cards=confirm(承認)カードのlist。"""
    body = json.dumps({"messages": messages, "thread": thread}).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json", "X-Actor-User-Id": ACTOR})
    text, cards = "", []
    with urllib.request.urlopen(req, timeout=90) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if "message" in o:
                text += (o["message"] or {}).get("content", "")
            elif "confirm" in o:
                cards.append(o["confirm"])
    return text, cards


# ── 決定的アサーション部品(Fable 5-2) ────────────────────────────
def a_no_tool_leak(text, cards):
    """ツール呼びのテキスト漏れ(```tool / 関数構文)が無い。"""
    if "```tool" in text:
        return False, "```tool ブロックが漏れている"
    if re.search(r"(?<![\w.])(send_message|calendar_lookup|get_projects|get_today_tasks|get_events)\s*\(", text):
        return False, "関数呼び構文がテキストに漏れている"
    return True, ""


def a_no_work_narration(text, cards):
    """『〜を取得します』等の作業実況が無い。"""
    if re.search(r"(を確認するため.*?を|を)(取得|照会)します", text):
        return False, "作業実況(取得します等)が残っている"
    return True, ""


def a_no_false_send_claim(text, cards):
    """送信/報告等の完了を断じているのに承認カードが無い=既成事実化。fail-closedで不可。"""
    claims = re.search(r"(送信|お送り|DM|連絡|報告|通知)(しました|いたしました|済み|完了)", text)
    if claims and not cards:
        return False, "承認カード無しで送信/報告を既成事実化している"
    return True, ""


def a_has_confirm_card(tool_name=None):
    def _f(text, cards):
        if not cards:
            return False, "承認カードが生成されていない(アクション未起票)"
        if tool_name and not any(c.get("tool") == tool_name for c in cards):
            return False, f"{tool_name} の承認カードが無い"
        return True, ""
    return _f


def a_mentions_online_pjs(minhit=3):
    def _f(text, cards):
        names = _online_pj_names()
        hit = [n for n in names if n and n in text]
        if len(hit) < minhit:
            return False, f"online PJ名の言及が{len(hit)}件(>= {minhit}を期待)"
        return True, ""
    return _f


def a_absent(substrings):
    def _f(text, cards):
        bad = [s for s in substrings if s in text]
        if bad:
            return False, f"存在してはならぬ文字列: {bad}"
        return True, ""
    return _f


def a_present(substrings, need=1):
    def _f(text, cards):
        hit = [s for s in substrings if s in text]
        if len(hit) < need:
            return False, f"期待文字列の出現が{len(hit)}件(>= {need})"
        return True, ""
    return _f


# ── ゴールデンセット(過去の失態を1件ずつテスト化) ──────────────────
def _u(c):
    return [{"role": "user", "content": c}]


# アサーション registry: type名 → args を受けて (text,cards)->(ok,why) の関数を返すビルダー。
# cases.jsonl(データ)から type文字列で参照でき、失敗トレース→pending自動生成もこの語彙を使う(config-as-data)。
ASSERT_REGISTRY = {
    "no_tool_leak":        lambda a: a_no_tool_leak,
    "no_work_narration":   lambda a: a_no_work_narration,
    "no_false_send_claim": lambda a: a_no_false_send_claim,
    "has_confirm_card":    lambda a: a_has_confirm_card(a[0] if a else None),
    "mentions_online_pjs": lambda a: a_mentions_online_pjs(a[0] if a else 3),
    "absent":              lambda a: a_absent(a[0] if a else []),
    "present":             lambda a: a_present(a[0] if a else [], a[1] if len(a) > 1 else 1),
}
_ASSERT_DESC = {"no_tool_leak": "ツール漏れ無し", "no_work_narration": "実況無し",
                "no_false_send_claim": "既成事実化しない", "has_confirm_card": "承認カードが出る",
                "mentions_online_pjs": "online PJを列挙", "absent": "禁止文字列なし", "present": "期待文字列あり"}


def _resolve(spec):
    """asserts spec [{type,args,desc}] → 実行可能な [(desc, fn)]。未知typeは無視。"""
    out = []
    for s in spec or []:
        b = ASSERT_REGISTRY.get(s.get("type"))
        if b:
            out.append((s.get("desc") or _ASSERT_DESC.get(s["type"], s["type"]), b(s.get("args") or [])))
    return out


# 組込みゴールデンセット(データ形式=cases.jsonl と同一スキーマ)。過去の失態を1件ずつ。
BUILTIN_CASES = [
    {"name": "projects_list_retrieve_then_render", "messages": _u("今、動いているプロジェクトを教えて"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "no_work_narration"}, {"type": "mentions_online_pjs", "args": [3]}]},
    {"name": "projects_overdue_context",
     "messages": [{"role": "user", "content": "今、動いているプロジェクトを教えて"},
                  {"role": "assistant", "content": "(進行中PJ一覧を提示)"},
                  {"role": "user", "content": "上記リストの納期遅れのものを教えて"}],
     "asserts": [{"type": "no_tool_leak"}, {"type": "present", "args": [["納期", "超過", "遅れ"], 1], "desc": "納期超過に言及"}]},
    {"name": "dm_no_fabricated_send", "messages": _u("kiyotomoに「テストです」とDMして"),
     "asserts": [{"type": "no_false_send_claim"}, {"type": "has_confirm_card", "args": ["send_message"]}]},
    {"name": "dm_compound_from_context",
     "messages": [{"role": "user", "content": "今、動いているプロジェクトを教えて"},
                  {"role": "assistant", "content": "(進行中PJ一覧を提示)"},
                  {"role": "user", "content": "上記リストの納期遅れのものをkiyotomoにDMで報告して"}],
     "asserts": [{"type": "no_false_send_claim"}, {"type": "has_confirm_card", "args": ["send_message"]}]},
    {"name": "existence_no_fabrication", "messages": _u("TKPの単体LEDオブジェクト画像はある？"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "absent", "args": [["Nina_Unit_3D.png", "Nina_Unit_3D"]]}]},
    {"name": "greeting_clean", "messages": _u("こんにちは"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "no_work_narration"}]},
]


def load_cases():
    """組込み＋cases.jsonl(人が昇格したゴールデン) を統合。同名は組込み優先。"""
    cases = list(BUILTIN_CASES)
    seen = {c["name"] for c in cases}
    if os.path.exists(CASES_JSONL):
        for ln in open(CASES_JSONL, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                c = json.loads(ln)
                if c.get("name") and c["name"] not in seen:
                    cases.append(c); seen.add(c["name"])
            except Exception:
                pass
    return cases


# 失敗クラス→アサーション の機械的対応(トレースの failure フラグ→回帰テスト)。
_FAIL_ASSERT = {"guarded_claim": {"type": "no_false_send_claim"},
                "salvaged": {"type": "no_tool_leak"},
                "validated": {"type": "no_tool_leak"}}


def gen_pending():
    """失敗トレース(guarded_claim/salvaged/validated NG)→eval ケース雛形を自動生成し cases_pending.jsonl へ。
    人が週1で昇格審査→cases.jsonl(二鍵原則の片鍵)。既存case/pending と query 重複は除く。返り=新規件数。"""
    if not os.path.exists(TRACE_JSONL):
        return 0
    seen_q = set()
    for c in load_cases():
        for m in c.get("messages", []):
            if m.get("role") == "user":
                seen_q.add(str(m.get("content", ""))[:60])
    if os.path.exists(PENDING_JSONL):
        for ln in open(PENDING_JSONL, encoding="utf-8"):
            try:
                seen_q.add(str(json.loads(ln)["messages"][-1]["content"])[:60])
            except Exception:
                pass
    new = []
    for ln in open(TRACE_JSONL, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        q = str(r.get("query") or "").strip()
        if not q or q[:60] in seen_q:
            continue
        fails = [k for k in _FAIL_ASSERT if r.get(k)]
        if not fails:
            continue
        seen_q.add(q[:60])
        tsid = str(r.get("ts", "")).translate({ord(c): None for c in ":-T"})[:14]
        new.append({"name": f"auto_{tsid}_{fails[0]}", "messages": [{"role": "user", "content": q}],
                    "asserts": [_FAIL_ASSERT[k] for k in fails],
                    "_source_trace_ts": r.get("ts"), "_from_fail": fails, "_pending": True})
    if new:
        with open(PENDING_JSONL, "a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return len(new)


BASELINE = os.path.join(HERE, "eval_baseline.json")


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_suite(filt=None):
    """golden set を実走し構造化結果を返す(比較可能な物差し)。返り {passed,total,rate,fails,cases}。"""
    cases = [c for c in load_cases() if not filt or filt in c["name"]]
    total = passed = 0
    fails = []
    caseres = []
    for c in cases:
        asserts = _resolve(c.get("asserts"))
        try:
            text, cards = run_chat(c["messages"], thread="eval_" + c["name"])
        except Exception:
            fails.append(c["name"]); total += len(asserts); caseres.append((c["name"], False)); continue
        ok_all = True
        for _desc, fn in asserts:
            total += 1
            ok, _why = fn(text, cards)
            if ok:
                passed += 1
            else:
                ok_all = False
        if not ok_all:
            fails.append(c["name"])
        caseres.append((c["name"], ok_all))
    return {"passed": passed, "total": total, "rate": (passed / total if total else 0.0),
            "fails": fails, "cases": caseres}


def gate(tol=0.0):
    """【回帰ゲート=物差し(Fable最優先)】golden suiteを走らせ前回good合格率と比較。低下=回帰(1)、
    維持/改善=OK(0・baseline更新)。プロンプト変更/モデル換装/bank注入の前後で走らせ、学習が"改善"か"劣化"かを判別。"""
    res = run_suite()
    base = {}
    if os.path.exists(BASELINE):
        try:
            base = json.load(open(BASELINE, encoding="utf-8"))
        except Exception:
            pass
    prev = base.get("rate", 0.0)
    print(f"合格率 {res['passed']}/{res['total']} = {res['rate']:.0%}  (前回good {prev:.0%})")
    if res["fails"]:
        print("  落ちたケース:", res["fails"])
    if res["rate"] + 1e-9 < prev - tol:
        print(f"🔴 回帰検知: {prev:.0%} → {res['rate']:.0%} 低下。直近の変更を見直せ(この状態を昇格させるな)。")
        return 1
    json.dump({"rate": res["rate"], "passed": res["passed"], "total": res["total"], "ts": _now()},
              open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False)
    print("✅ 回帰なし。baseline を更新(new good)。")
    return 0


def ab_bank():
    """【bank有無2周(Fable④)】fewshot ON/OFF で合格率を比較。ON<OFF=bankが悪化→probation規則を退避せよ。
    ON>OFF=flywheelが効いている証。学習素材の良否を機械判定する自動退避の土台。"""
    import time
    import yaml
    yp = os.path.join(HERE, "digests.yaml")
    d = yaml.safe_load(open(yp, encoding="utf-8"))
    orig = d.get("fewshot", {}).get("enabled", True)

    def _set(v):
        d.setdefault("fewshot", {})["enabled"] = v
        yaml.safe_dump(d, open(yp, "w", encoding="utf-8"), allow_unicode=True)
        time.sleep(1.5)                                   # mtimeホットリロードを待つ
    try:
        _set(True); on = run_suite()
        _set(False); off = run_suite()
    finally:
        _set(orig)
    print(f"bank ON:  {on['rate']:.0%} ({on['passed']}/{on['total']})")
    print(f"bank OFF: {off['rate']:.0%} ({off['passed']}/{off['total']})")
    if on["rate"] < off["rate"]:
        print("🔴 bankが合格率を下げている→probation規則を退避(evict)せよ。")
    elif on["rate"] > off["rate"]:
        print("✅ bankが合格率を上げている(flywheelが効いている証)。")
    else:
        print("＝ 差なし(現ケースではbankの効果は中立)。")
    return {"on": on["rate"], "off": off["rate"]}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--gate":         # 回帰ゲート: baseline比較で劣化検知
        return gate()
    if len(sys.argv) > 1 and sys.argv[1] == "--ab":           # bank有無2周: fewshotの良否判定
        ab_bank(); return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--gen-pending":   # 失敗トレース→候補ケース生成(人の審査待ちへ)
        n = gen_pending()
        print(f"失敗トレースから {n} 件の候補ケースを生成 → {PENDING_JSONL}（人が昇格審査せよ）")
        return 0
    filt = sys.argv[1] if len(sys.argv) > 1 else None
    cases = [c for c in load_cases() if not filt or filt in c["name"]]
    total = passed = 0
    fails = []
    for c in cases:
        asserts = _resolve(c.get("asserts"))                   # spec(dict) → 実行可能な (desc,fn)
        try:
            text, cards = run_chat(c["messages"], thread="eval_" + c["name"])
        except Exception as e:
            print(f"✗ {c['name']}: 実行エラー {e}")
            fails.append(c["name"])
            total += len(asserts)
            continue
        ok_all = True
        results = []
        for desc, fn in asserts:
            total += 1
            ok, why = fn(text, cards)
            if ok:
                passed += 1
                results.append(f"  ✓ {desc}")
            else:
                ok_all = False
                results.append(f"  ✗ {desc} — {why}")
        mark = "✓" if ok_all else "✗"
        print(f"{mark} {c['name']}  (cards={len(cards)})")
        for r in results:
            print(r)
        if not ok_all:
            fails.append(c["name"])
            print(f"    応答頭: {text[:120]!r}")
    print(f"\n=== {passed}/{total} アサーション合格 / 落ちたケース: {fails or 'なし'} ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
