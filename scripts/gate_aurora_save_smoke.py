#!/usr/bin/env python3
"""cmd_486(4-4) 実z8a疎通スモーク(CI/常用のgate_aurora_save.pyから分離)。
gate_aurora_save.py は _ollama_json をstub差替してネットワーク非依存を保つ設計だが、
意味判定(_wants_aurora_save_llm)の実際の正答率はz8aへの実疎通なしには確認できない
(掟: 緑ゲートに嘘は映らぬ・意味判定の正答をgate内で主張しない)。
本スクリプトは実z8a(192.168.44.119:11434)へ到達し、kiyotomo4発話・未知語のTrue側正答と
(D)節跨ぎ・題名解決を確認する。z8a未起動/到達不能時はスキップ(exit 0・CIを壊さない)。
実行: python3 gate_aurora_save_smoke.py
"""
import ast
import os
import sys
import types
import urllib.request

import pack_config

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
# cmd_491 AC3: PJ名は pack から受け取る(gate_*.py を engine_scan exclude から外しても
# pack_lint が緑のままであるため)。
_PJ = (pack_config.get("examples", {}).get("project_names") or ["sample-pj"])[0]
ENDPOINT = "http://192.168.44.119:11434"
MODEL = "qwen3.6:27b"

WANT = ["_AURORA_WORD_RE", "_AURORA_READ_RE", "_AURORA_SAVED_REF_RE", "_BENEFIT_COMPLETION_NEG_RE",
        "_AURORA_EDIT_READ_VERB_RE", "_AURORA_IMMEDIATE_TAIL_VERB_BOUNDARY_NEG",
        "_AURORA_IMMEDIATE_REQUEST_TAIL_RE", "_ASK_DELEGATE_RE", "_SAVE_HINT_RE",
        "_AURORA_CLAUSE_SPLIT_RE", "_aurora_edit_read_verb_same_clause", "_aurora_read_verb_same_clause",
        "_aurora_clause_delegate_form",
        "_wants_aurora_save", "_wants_aurora_save_llm", "_resolve_aurora_note_title",
        "_aurora_save_unknown_choices", "_ollama_json", "_AURORA_SAVE_UNKNOWN_PROMPT",
        "_AU_LAST_ROUTE"]


def _z8a_reachable():
    try:
        with urllib.request.urlopen(ENDPOINT.rstrip("/") + "/api/tags", timeout=3):
            return True
    except Exception:
        return False


def main():
    if not _z8a_reachable():
        print(f"⚠ z8a({ENDPOINT})に到達できず。実疎通スモークをスキップする(CIを壊さぬためexit 0)。")
        return 0

    tree = ast.parse(open(SRC, encoding="utf-8").read())
    picked, seen = [], set()
    for node in tree.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef) else
                 [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
        for nm in names:
            if nm in WANT:
                picked.append(node)
                seen.add(nm)
    missing = [w for w in WANT if w not in seen]
    if missing:
        print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
        return 1

    M = {}
    exec("import re, os, json, urllib.request, urllib.error", M)
    M["A"] = types.SimpleNamespace(model=MODEL, endpoint=ENDPOINT)
    M["OLLAMA"] = ENDPOINT.rstrip("/") + "/api/chat"
    # ★2026-08-29 是正: 抜き出した _ollama_json は module 変数を参照する。備えねば
    #   NameError が例外として握り潰され、**模型を一度も呼ばぬまま None** が並ぶ
    #   ——生試験の面をした空試験になっていた(8/24の雲着座の手当以降ずっと)。
    #   ここで local backend を明示して据える(雲着座時に呼ばぬ掟は production の話であり、
    #   本スモークは『ローカル分類器そのもの』の正答を測る道具ゆえ ollama を名乗らせる)。
    M["BACKEND"] = "ollama"
    M["_OLLAMA_JSON_CALL_COUNT"] = 0
    M["_OLLAMA_JSON_SUPPRESSED"] = 0

    class LocalClassifierSuppressed(RuntimeError):
        pass
    M["LocalClassifierSuppressed"] = LocalClassifierSuppressed
    # 横断呼出台帳(cmd_519)は本スモークの検分対象ではない。呼出をそのまま通す薄い身代わりを据える
    # (据えねば NameError が握り潰されて None になり、模型を呼ばぬまま『不正解』と数える)。
    _calls = {"n": 0}

    def _rec(site, model, fn):
        _calls["n"] += 1
        return fn()
    M["_llm_call_record"] = _rec
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    _wants = M["_wants_aurora_save"]

    results = []

    def chk(name, got, ok_fn, label):
        ok = ok_fn(got)
        results.append(ok)
        print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={label}"))

    print("── AC1 kiyotomo4発話(実z8a・分類器健全時のTrue正答率) ──")
    for q in ["Auroraにまとめてくれた？", "Auroraに「status内容 0729」をまとめて",
              "Auroraにまとめて", "Auroraにまとめてさせて下さい"]:
        chk(f"実z8a True正答: {q}", _wants(q), lambda v: v is True, "True(is)")

    print("── AC5 未知の新語(実z8a・True側の正答。分類器の裁量ゆえ個別Falseは即バグでない) ──")
    for q in ["Auroraに書き起こして", "Auroraに記録しておいて", "Auroraへ整理しておいて"]:
        r = _wants(q)
        print(f"  参考(非assert): {q!r} -> {r!r}")

    print("── AC6 (D)節跨ぎ(実z8a・Falseは不可) ──")
    for q in ["Auroraにまとめて、あとで確認する", f"{_PJ}の進捗を確認した上で、Auroraにまとめて"]:
        chk(f"実z8a 節跨ぎはFalse不可: {q}", _wants(q), lambda v: v is not False, "True or None(Falseは不可)")

    print("── AC3 既存陰性の退行なし(実z8aでもFalse維持) ──")
    for q in ["Auroraに載せた資料の体裁を整えて", "Auroraへ記録した議事録の日付を修正して",
              "Auroraにアップされてるやつ全部リストアップして", "前回Auroraに上げたやつ、もう一度出して"]:
        chk(f"実z8a 陰性維持: {q}", _wants(q), lambda v: v is False, "False(is)")

    # ★空試験の禁: 「模型に訊いた」と称して一度も訊いておらぬ試験は、赤も緑も嘘になる。
    #   実際に推論機を叩いた回数を数え、0なら本スモークそのものを不合格とする
    #   (2026-08-29: harness が module 変数を欠き NameError→None に化け、
    #    模型を一度も呼ばぬまま「不正解4件」と数えていた。数えておれば即座に露見した)。
    chk(f"★実疎通の証跡: 推論機を実際に叩いた回数({_calls['n']}回)",
        _calls["n"], lambda v: v > 0, "1回以上(0なら空試験)")

    n_ok, n = sum(results), len(results)
    print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
    return 0 if n_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
