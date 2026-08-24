#!/usr/bin/env python3
"""雲の帳簿の回帰ゲート(殿御下命2026-08-24)。全PASSで exit 0。

守る掟:
 ① 雲への出口は三つ(claude_cli_text / claude_cli_vision / anthropic_api)。
    そのいずれから出ても【必ず】帳簿へ一行残る。呼出側の善意に頼らぬ。
 ② 中身(送出プロンプト・受信本文)が残る。殿が後から内容を検分できねば帳簿ではない。
 ③ 失敗した時も残る。★送出は済んでいるのだから「失敗＝出ていない」ではない。
    ここを落とすと「出たのに帳簿に無い」が生まれる(失敗とゼロを別出口へ)。
 ④ 截った時は截ったと名乗る(truncated + 全長 + sha256)。「全部載っている」と嘘をつかせぬ。
 ⑤ turnの素性(誰の・どの発話)が添う。
 ⑥ 帳簿の不在(一度も雲へ出ておらぬ)と 0件 を区別する。
 ★突然変異: 出口から record 呼出を外すと①が赤化することを実証する。

本番の雲は叩かない(subprocess/urlopenをstub)。帳簿も一時ファイルへ差し替える(本番不変)。
"""
import ast
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT = ["claude_cli_text", "claude_cli_vision", "anthropic_call",
        "_cloud_ledger", "_turn_ctx_set", "_turn_ctx", "_llm_call_turn_reset", "_turn_memo"]
WANT_ASSIGN = ["_LLM_CALL_LOCAL", "_TURN_SEQ", "CLAUDE_BIN", "CLI_MODEL", "CLI_CWD",
               "ANTHROPIC_URL", "ANTHROPIC_KEY", "ANTHROPIC_MODEL"]
tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node); seen.add(node.name)
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in WANT_ASSIGN for t in node.targets):
        picked.append(node); seen.add(node.targets[0].id)
missing = [w for w in (WANT + WANT_ASSIGN) if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

import casper_cloud_ledger  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="gate_cloud_ledger_")
_ORIG_LEDGER = casper_cloud_ledger.LEDGER
casper_cloud_ledger.LEDGER = os.path.join(_TMP, "ledger.jsonl")

M = {}
exec("import os, json, time, shutil, subprocess, threading, urllib.request", M)
M["casper_cloud_ledger"] = casper_cloud_ledger
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)


def _rows():
    p = casper_cloud_ledger.LEDGER
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def _reset_ledger():
    if os.path.exists(casper_cloud_ledger.LEDGER):
        os.remove(casper_cloud_ledger.LEDGER)


# ── ⑥ 帳簿の不在と0件は別物 ────────────────────────────────────────────────
_reset_ledger()
chk("⑥ 帳簿が無い時 read() は空(=例外でなく空・crashさせぬ)", casper_cloud_ledger.read(7), [])
chk("⑥ 帳簿の不在は exists=False として名乗る(0件と混ぜぬ)",
    casper_cloud_ledger.summarize(7)["exists"], False)


class _R:
    def __init__(self, out="", err=""):
        self.stdout, self.stderr, self.returncode = out, err, 0


# ── ①②⑤ claude_cli_text: 出れば必ず一行・中身つき・素性つき ─────────────────
_reset_ledger()
M["_llm_call_turn_reset"]()
M["_turn_ctx_set"](uid="28", name="殿", thread="th-1", query="社の見積を要約して")
M["subprocess"].run = lambda *a, **k: _R("要約にござる")
_out = M["claude_cli_text"]("これは社外へ出る本文である(見積・金額を含む)")
rows = _rows()
chk("① claude_cli_text: 雲へ出たら帳簿に1行", len(rows), 1)
chk("① door名が正しい", rows[0]["door"], "claude_cli_text")
chk_true("② 送出した本文が残る(内容の検分ができる)", "社外へ出る本文" in rows[0]["prompt"])
chk_true("② 受信した本文も残る", "要約にござる" in rows[0]["response"])
chk("⑤ 誰の発話かが残る", (rows[0].get("ctx") or {}).get("uid"), "28")
chk_true("⑤ どの発話かが残る", "見積" in ((rows[0].get("ctx") or {}).get("query") or ""))
chk("① 成否が残る", rows[0]["outcome"], "ok")

# ── ③ 失敗しても残る(送出は済んでいる) ───────────────────────────────────────
_reset_ledger()


def _boom(*a, **k):
    raise RuntimeError("cli落ち")


M["subprocess"].run = _boom
M["claude_cli_text"]("失敗する呼出でも社外へは出ている")
rows = _rows()
chk("③ 失敗時も帳簿に残る(『出たのに帳簿に無い』を作らぬ)", len(rows), 1)
chk("③ 失敗はoutcome=errorとして別出口で名乗る", rows[0]["outcome"], "error")
chk_true("③ 失敗時も送出本文は残る", "社外へは出ている" in rows[0]["prompt"])

# ── ① claude_cli_vision: 画像の素性(パス/バイト/sha)が残る ──────────────────
_reset_ledger()
_img = os.path.join(_TMP, "shot.png")
open(_img, "wb").write(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
M["subprocess"].run = lambda *a, **k: _R("画像には見積表が写っており申す")
M["claude_cli_vision"](_img, "この画像を読め")
rows = _rows()
chk("① claude_cli_vision: 帳簿に1行", len(rows), 1)
chk("① door名が正しい", rows[0]["door"], "claude_cli_vision")
chk_true("① 画像の素性(パス/バイト数/sha256)が残る",
         rows[0].get("image", {}).get("bytes") == 108 and rows[0]["image"].get("sha256"))

# ── ① anthropic_api: APIキー経路も同じ帳簿へ ────────────────────────────────
_reset_ledger()


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return '{"content":[{"type":"text","text":"雲の答"}]}'.encode("utf-8")


M["urllib"].request.urlopen = lambda *a, **k: _Resp()
M["json"].load = lambda f: json.loads(f.read().decode())
M["anthropic_call"]({"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "社の秘密"}]})
rows = _rows()
chk("① anthropic_api: 帳簿に1行(APIキー経路も見逃さぬ)", len(rows), 1)
chk("① door名が正しい", rows[0]["door"], "anthropic_api")
chk_true("② 送出messagesの中身が残る", "社の秘密" in rows[0]["prompt"])

# ── ④ 截った時は截ったと名乗る ───────────────────────────────────────────────
_reset_ledger()
_orig_max = casper_cloud_ledger.MAX_TEXT
casper_cloud_ledger.MAX_TEXT = 50
M["subprocess"].run = lambda *a, **k: _R("短い答")
_long = "あ" * 500
M["claude_cli_text"](_long)
rows = _rows()
chk("④ 截った事実を名乗る", rows[0]["prompt_truncated"], True)
chk("④ 全長を併記する(何文字出たかは判る)", rows[0]["prompt_chars"], 500)
chk_true("④ sha256を添える(截った本文の同一性を後から照合できる)", bool(rows[0]["prompt_sha"]))
chk("④ 截られていないものはtruncated=Falseと名乗る", rows[0]["resp_truncated"], False)
casper_cloud_ledger.MAX_TEXT = _orig_max

# ── ⑥ 0件(帳簿は在るが期間内に無い)は空listで、不在とは別 ────────────────────
chk("⑥ 帳簿が在れば exists=True(件数0でも『不在』とは言わぬ)",
    casper_cloud_ledger.summarize(7)["exists"], True)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異: 出口から record 呼出を外すと①が赤化する(=配線が効いている証拠)
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証(帳簿への結線を殺す) ---")
_reset_ledger()
_orig_cloud_ledger = M["_cloud_ledger"]
M["_cloud_ledger"] = lambda *a, **k: None          # 記録しない変異体
M["subprocess"].run = lambda *a, **k: _R("答")
M["claude_cli_text"]("この本文は社外へ出るが帳簿には残らぬ(変異)")
chk("★変異(結線を殺す): 雲へ出たのに帳簿が空になる(赤化実証)", len(_rows()), 0)
M["_cloud_ledger"] = _orig_cloud_ledger
M["claude_cli_text"]("復元後は再び残る")
chk("★復元確認: 結線を戻せば再び帳簿に残る", len(_rows()), 1)

casper_cloud_ledger.LEDGER = _ORIG_LEDGER
n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
