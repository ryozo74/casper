#!/usr/bin/env python3
"""雲に座っている間、ローカル分類器を呼ばぬことの回帰ゲート(殿御下命2026-08-24)。全PASSで exit 0。

守る掟:
 ① ローカル(ollama)に座っている時は従来どおり分類器を呼ぶ(退行させぬ)。
 ② ★雲(claude_cli/anthropic)に座っている時は【網に触れぬ】。
   実害: 雲へ移したのは「本文を書く口」だけで、意図判定は従来どおりローカル宛先を
   叩き続けていた。ゆえに殿が別作業へ回された z8a に 27b が再ロードされ、
   「z8aは使わぬ」の御下命が半分しか効いていなかった(2026-08-24 実測)。
 ③ 呼ばなかった時、分類器は三値の None(判定不能)へ落ちる。★False と混ぜてはならぬ——
   「判定不能」と「関係ない」は別物であり、混ぜれば安全側の既定が壊れる。
 ④ 呼ばずに済ませた回数を数え、/health から見える(黙って挙動を変えぬ)。
 ★突然変異: 門を外すと雲に座っていても網へ出る(=②が赤化)。

本番の推論機には一切触れぬ(urlopen を stub し、触れたら検知する)。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
sys.path.insert(0, HERE)

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT = ["_ollama_json", "_asks_about_casper_llm", "_needs_prior_context_llm",
        "_llm_call_record", "_llm_is_timeout_error"]
WANT_ASSIGN = ["BACKEND", "_OLLAMA_JSON_CALL_COUNT", "_OLLAMA_JSON_SUPPRESSED",
               "OLLAMA", "_LLM_CALL_LOCAL", "_TURN_SEQ"]
WANT_CLASS = ["LocalClassifierSuppressed"]

tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node); seen.add(node.name)
    if isinstance(node, ast.ClassDef) and node.name in WANT_CLASS:
        picked.append(node); seen.add(node.name)
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in WANT_ASSIGN for t in node.targets):
        picked.append(node); seen.add(node.targets[0].id)
missing = [w for w in (WANT + WANT_ASSIGN + WANT_CLASS) if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

M = {}
exec("import json, threading, time, os, urllib.request", M)


class _A:                      # 引数オブジェクトの代役(本番の推論機は叩かぬ)
    model = "qwen3.6:27b"
    endpoint = "http://127.0.0.1:1"    # 触れたら分かる死んだ宛先


M["A"] = _A()
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)

# 網に触れたら数える stub(本番の推論機は叩かぬ)
_net = {"n": 0}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **k):
    _net["n"] += 1
    return _Resp()


M["urllib"].request.urlopen = _fake_urlopen
M["json"].load = lambda f: {"message": {"content": '{"about_casper": true}'}}

# ── ① ローカル着座: 従来どおり呼ぶ(退行させぬ) ─────────────────────────────
M["BACKEND"] = "ollama"
M["_OLLAMA_JSON_SUPPRESSED"] = 0
_net["n"] = 0
_r = M["_ollama_json"]("sys", "user")
chk("① ローカル着座: 網へ出る(従来どおり)", _net["n"], 1)
chk_true("① ローカル着座: 中身が返る", isinstance(_r, str) and "about_casper" in _r)
chk("① ローカル着座: 抑止の数は増えぬ", M["_OLLAMA_JSON_SUPPRESSED"], 0)

# ── ②④ 雲着座: 網に触れぬ・数える ───────────────────────────────────────────
for backend in ("claude_cli", "anthropic"):
    M["BACKEND"] = backend
    M["_OLLAMA_JSON_SUPPRESSED"] = 0
    _net["n"] = 0
    raised = None
    try:
        M["_ollama_json"]("sys", "user")
    except Exception as e:
        raised = type(e).__name__
    chk(f"② 雲({backend})着座: ★網に一切触れぬ", _net["n"], 0)
    chk(f"② 雲({backend})着座: 呼ばなかったことを例外で名乗る", raised, "LocalClassifierSuppressed")
    chk(f"④ 雲({backend})着座: 呼ばずに済ませた回数を数える", M["_OLLAMA_JSON_SUPPRESSED"], 1)

# ── ③ 分類器は三値の None(判定不能)へ落ちる ────────────────────────────────
M["BACKEND"] = "claude_cli"
_net["n"] = 0
chk("③ _asks_about_casper_llm は None(判定不能)へ落ちる",
    M["_asks_about_casper_llm"]("キャスパーって携帯で見れるの？"), None)
chk("③ _needs_prior_context_llm も None へ落ちる",
    M["_needs_prior_context_llm"]("進捗はどう？"), None)
chk_true("③ ★None であって False ではない(判定不能と『関係ない』を混ぜぬ)",
         M["_asks_about_casper_llm"]("x") is None and M["_asks_about_casper_llm"]("x") is not False)
chk("③ その間も網には触れておらぬ", _net["n"], 0)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異: 門を外すと雲に座っていても網へ出る
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証(門を外す) ---")
_src = ast.parse(open(SRC, encoding="utf-8").read())
_fn = next(n for n in _src.body if isinstance(n, ast.FunctionDef) and n.name == "_ollama_json")
# 先頭の `if BACKEND in (...)` ブロックを取り除いた変異体を作る
_fn.body = [st for st in _fn.body
            if not (isinstance(st, ast.If) and "BACKEND" in ast.dump(st.test))]
M2 = dict(M)
exec(compile(ast.Module(body=[_fn], type_ignores=[]), SRC, "exec"), M2)
M2["BACKEND"] = "claude_cli"
_net["n"] = 0
try:
    M2["_ollama_json"]("sys", "user")
except Exception:
    pass
chk("★変異(門を外す): 雲に座っていても網へ出てしまう(赤化実証)", _net["n"], 1)
_net["n"] = 0
try:
    M["_ollama_json"]("sys", "user")
except Exception:
    pass
chk("★復元確認: 門を戻せば再び網に触れぬ", _net["n"], 0)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
