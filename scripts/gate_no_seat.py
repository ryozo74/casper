#!/usr/bin/env python3
"""雲が枯れた時の二段目の回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

守る掟:
 ① 雲が枯れたら、まず GPU の Qwen を試す。
 ② Qwen の生存は **/api/generate で実物を1トークン出させて** 確かめる。
    ★/api/tags(在庫照合)で判じてはならぬ——2026-08-26 の .139 は tags 27ms で応じながら
      /api/generate は90秒超無応答であった。在庫は「答えられる」の証明にならぬ。
 ③ Qwen も答えられぬ時は **「今は答えられぬ」と正直に返す**。
    雲の生エラー英文を回答に化けさせぬ／それらしい答えを捏造せぬ。
 ④ 顛末は必ず一行残る(silent cap の禁)。Qwenで答えた回・答えられなかった回を後から数えられる。
 ★突然関異: 生存確認を「在庫照合(tags相当・常に真)」へすり替えると、
   固着した席を生きていると誤認し③が赤化することを実証する。

本番の推論機は叩かない(urlopen/ollama_chatをstub)。
"""
import ast
import json
import os
import sys
import tempfile

HERE_G = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE_G)
SRC = os.path.join(HERE_G, "chat_server.py")

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT = ["_cli_exhausted", "_qwen_alive", "_local_or_silence", "_no_seat_log"]
WANT_ASSIGN = ["_CLI_EXHAUSTED_RE", "CASPER_NO_SEAT_MSG", "_QWEN_ALIVE_PROBE_SEC"]
tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node); seen.add(node.name)
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in WANT_ASSIGN for t in node.targets):
        picked.append(node); seen.add(node.targets[0].id)
missing = [w for w in (WANT + WANT_ASSIGN) if w not in seen]
if missing:
    print(f"❌ chat_server.py に二段目の機構が見当たらぬ: {missing}")
    sys.exit(1)

_TMP = tempfile.mkdtemp(prefix="gate_no_seat_")

M = {}
exec("import os, re, json, time, datetime, urllib.request", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)


class _A:
    endpoint = "http://10.0.0.9:11434"
    model = "qwen3.6:27b"


M["A"] = _A
M["HERE"] = _TMP
M["strip_think"] = lambda s: (s or "").strip()

LOG = os.path.join(_TMP, "casper_no_seat.jsonl")


def _log_rows():
    if not os.path.exists(LOG):
        return []
    return [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]


def _reset():
    if os.path.exists(LOG):
        os.remove(LOG)


# ── urlopen stub: 叩かれたURLを記録し、席の様子を切り替える ────────────────
HITS = []


class _Body:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self, *a):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_urlopen(seat):
    """seat='warm' 即答 / 'wedged' timeout / 'down' 接続不能"""
    def _open(req, timeout=None):
        HITS.append(getattr(req, "full_url", str(req)))
        if seat == "warm":
            return _Body({"response": "p", "done": True})
        if seat == "wedged":
            raise TimeoutError("timed out")
        raise OSError("connection refused")
    return _open


class _UL:
    class request:
        Request = staticmethod(lambda url, data=None, headers=None: type(
            "R", (), {"full_url": url, "data": data, "headers": headers or {}})())
        urlopen = None


def set_seat(seat):
    HITS.clear()
    _UL.request.urlopen = staticmethod(make_urlopen(seat))
    M["urllib"] = _UL


# ── ①② 席が温かい: Qwenで答え、生存確認は /api/generate を叩く ─────────────
_reset(); set_seat("warm")
M["ollama_chat"] = lambda msgs, **k: {"message": {"content": "GSは進行中にござる"}}
out = M["_local_or_silence"]("GSの状況は？", cloud_said="You've hit your weekly limit")
chk("① 雲が枯れてもQwenが答える", out, "GSは進行中にござる")
chk_true("② 生存確認は /api/generate(実物を1トークン出させる)",
         any(h.endswith("/api/generate") for h in HITS))
chk_true("② 在庫照合 /api/tags で判じておらぬ",
         not any("/api/tags" in h for h in HITS))
chk("④ Qwenで答えた回が帳簿に残る", (_log_rows() or [{}])[0].get("verdict"), "fallback_qwen")

# ── ③ 席が固着(tagsは応じるが生成は返らぬ型): 正直に「答えられぬ」 ──────────
_reset(); set_seat("wedged")
M["ollama_chat"] = lambda msgs, **k: {"message": {"content": "これは呼ばれてはならぬ"}}
out = M["_local_or_silence"]("GSの状況は？", cloud_said="You've hit your weekly limit")
chk("③ 席が無ければ『今は答えられぬ』と返す", out, M["CASPER_NO_SEAT_MSG"])
chk_true("③ 雲の生エラー英文を回答に化けさせぬ", "weekly limit" not in out)
chk_true("③ それらしい答えを捏造せぬ", "GS" not in out)
chk("④ 答えられなかった回も帳簿に残る", (_log_rows() or [{}])[0].get("verdict"), "no_seat")
chk_true("④ 何秒でどう落ちたかが残る(型の追跡ができる)",
         "TimeoutError" in str((_log_rows() or [{}])[0].get("qwen_probe")))

# ── ③ 席が落ちている ──────────────────────────────────────────────────────
_reset(); set_seat("down")
out = M["_local_or_silence"]("GSの状況は？")
chk("③ 席が落ちていても『今は答えられぬ』(例外で沈黙せぬ)", out, M["CASPER_NO_SEAT_MSG"])
chk("④ 落ちた回も帳簿に残る", len(_log_rows()), 1)

# ── ③ 席は生きているがQwenが空/落ちた ─────────────────────────────────────
_reset(); set_seat("warm")
M["ollama_chat"] = lambda msgs, **k: {"message": {"content": "   "}}
chk("③ Qwenが空を返したら『今は答えられぬ』(空を答えとして出さぬ)",
    M["_local_or_silence"]("問い"), M["CASPER_NO_SEAT_MSG"])
chk("④ 空の回は qwen_empty として別に名乗る", (_log_rows() or [{}])[0].get("verdict"), "qwen_empty")

_reset(); set_seat("warm")


def _boom(msgs, **k):
    raise RuntimeError("ollama落ち")


M["ollama_chat"] = _boom
chk("③ Qwenが落ちても『今は答えられぬ』", M["_local_or_silence"]("問い"), M["CASPER_NO_SEAT_MSG"])
chk("④ 落ちた回は qwen_error として別に名乗る", (_log_rows() or [{}])[0].get("verdict"), "qwen_error")

# ── ① 枯れの検知そのもの ──────────────────────────────────────────────────
chk_true("① 実害の文字列を枯れと判ずる",
         M["_cli_exhausted"]("You've hit your weekly limit · resets 2am (Asia/Tokyo)"))
chk_true("① usage limit reached も枯れ", M["_cli_exhausted"]("Claude AI usage limit reached"))
chk_true("① 正常な答えは枯れと判ぜぬ",
         not M["_cli_exhausted"]("同時実行数の limit は8にござる。" * 20))
chk_true("① 空は枯れと判ぜぬ(空は別の失敗)", not M["_cli_exhausted"](""))

# ── ★突然変異: 生存確認を『在庫照合(常に真)』へすり替える ───────────────────
print("\n--- 突然変異検証(生存確認を在庫照合へすり替える) ---")
_reset(); set_seat("wedged")
_orig_alive = M["_qwen_alive"]
M["_qwen_alive"] = lambda: (True, "tags在庫あり(=すり替え)")
M["ollama_chat"] = lambda msgs, **k: {"message": {"content": "固着した席が答えたことになる"}}
mutated = M["_local_or_silence"]("GSの状況は？")
chk_true("★変異: 在庫で判ずると固着した席を生きていると誤認する(赤化実証)",
         mutated != M["CASPER_NO_SEAT_MSG"])
M["_qwen_alive"] = _orig_alive
_reset(); set_seat("wedged")
chk("★復元確認: 実物probeへ戻せば再び正直に答えられぬと言う",
    M["_local_or_silence"]("GSの状況は？"), M["CASPER_NO_SEAT_MSG"])

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
