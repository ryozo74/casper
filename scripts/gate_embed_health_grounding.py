#!/usr/bin/env python3
"""cmd_497回帰ゲート: 埋込断の高速フォールバック+材料ゼロでの断言防止(純機構・実サーバ不要)。
全PASSで exit 0。

守る掟:
 ① embed_alive(): 期限内はHTTPを叩かず即答(_probe呼出0回)。期限切れのみ_probeを1回叩く。
 ② embed_alive(): downと判った後、TTL_DOWN経過で自動的に再挑戦しupへ戻る(手動リセット不要)。
 ③ _grounding_state(hits=[], fulltext="長文", ...) は "thin" を返す(材料ゼロなのに全文だけ在る=実害の形)。
 ④ _grounding_state(hits=[8件], fulltext=..., ...) は "grounded" を返し、注意書きを一切足さない(退行なし)。
 ⑤ (cmd_497第2便・欠陥B) 「配線」検査: _grounding_state の戻り値だけでなく、実際にプロンプトへ注入される
   文字列(_build_grounding_block)まで検査する。hits=[] fulltext="" の"none"分岐で_GROUNDING_NOTE["none"]の
   文言が★実際に注入されることを確認する(部品=戻り値だけ見て緑にしない・配線まで見る)。
 ★突然変異検証: available()からembed_alive()を外す変異、_grounding_stateのthin分岐を潰す変異の
   2つで本ゲートが赤化することを別途確認する(scratchpadのコピー上で行い本番は不変)。

casper_embed.py は import しても副作用(サーバ起動等)が無いため直接 import する。
chat_server.py は import すると HTTPServer が起動してしまうゆえ、_grounding_state / _build_grounding_block
のみ ast で抜いて検査する(gate_import_truncate.py と同じ作法)。
"""
import ast
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import pack_config as _pc
    _PJ = (_pc.get("examples", {}).get("project_names") or ["<PJ名>"])[0]
except Exception:
    _PJ = "<PJ名>"

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── chat_server.py から _grounding_state のみ ast 抽出(import するとサーバが起動する為) ──
SRC = os.path.join(HERE, "chat_server.py")
tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
WANT = ["_grounding_state", "_build_grounding_block"]
WANT_ASSIGN = ["_GROUNDING_NOTE"]
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node)
        seen.add(node.name)
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) in WANT_ASSIGN for t in node.targets):
        picked.append(node)
        seen.add("_GROUNDING_NOTE")
missing = [w for w in (WANT + WANT_ASSIGN) if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)
M = {}
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_grounding_state = M["_grounding_state"]
_build_grounding_block = M["_build_grounding_block"]
_GROUNDING_NOTE = M["_GROUNDING_NOTE"]

import casper_embed  # noqa: E402  (副作用なしゆえ直接import)


# ── ① embed_alive(): 期限内はHTTPを叩かず即答(_probe呼出0回) ──────────────────────
_probe_calls = []


def _fake_probe_ok(timeout=None):   # timeout: 冷間の確認probe(本体の三値化に追随・2026-08-30)
    _probe_calls.append(time.time())
    return True


def _fake_probe_down(timeout=None):   # timeout: 冷間の確認probe(本体の三値化に追随・2026-08-30)
    _probe_calls.append(time.time())
    return False


_orig_probe = casper_embed._probe
_orig_health = dict(casper_embed._EMB_HEALTH)
_orig_time = time.time

try:
    # ① 健全状態でTTL内 → _probeを一切呼ばずに即答する
    casper_embed._EMB_HEALTH.update({"ok": True, "ts": 1000.0, "fails": 0})
    casper_embed._probe = _fake_probe_ok
    _probe_calls.clear()
    time.time = lambda: 1010.0   # TTL_OK=60秒以内
    got = casper_embed.embed_alive()
    chk("① embed_alive: TTL内は_probe呼出0回", len(_probe_calls), 0)
    chk("① embed_alive: TTL内はok値をそのまま返す", got, True)

    # ② 埋込断(_probeが常にFalse)時、hybrid相当の応答速度に効くのはembed_oneの短timeoutだが、
    #    ここではavailable()がembed_alive()を経由してdown判定を即答できることを確認する
    #    (2秒以内=AC1の核心は「TTL内はHTTPを叩かない」ことそのもの)。
    casper_embed._EMB_HEALTH.update({"ok": False, "ts": 1000.0, "fails": 1})
    casper_embed._probe = _fake_probe_down
    _probe_calls.clear()
    time.time = lambda: 1010.0   # TTL_DOWN=30秒以内 → まだ疑わない
    t0 = _orig_time()
    got = casper_embed.embed_alive()
    elapsed = _orig_time() - t0
    chk("① embed_alive: down中もTTL内は_probe呼出0回", len(_probe_calls), 0)
    chk("① embed_alive: down中はFalseを即答", got, False)
    chk("① embed_alive: TTL内応答は2秒未満(AC1)", elapsed < 2.0, True)

    # ── ② 復旧で自動復帰: downのままTTL_DOWN経過 → 自動的に_probeが叩かれupへ戻る ──────
    casper_embed._EMB_HEALTH.update({"ok": False, "ts": 1000.0, "fails": 1})
    casper_embed._probe = _fake_probe_ok   # 実際には復旧している状況を模す
    _probe_calls.clear()
    time.time = lambda: 1031.0   # TTL_DOWN=30秒を経過
    got = casper_embed.embed_alive()
    chk("② embed_alive: TTL_DOWN経過で自動的に_probeを叩く", len(_probe_calls), 1)
    chk("② embed_alive: 手動リセットなしでupへ復帰(AC2)", got, True)
    chk("② embed_alive: _EMB_HEALTH.ok が更新される", casper_embed._EMB_HEALTH["ok"], True)

    # ── ①' available(): embed_alive()もdb_available()も見る(欠陥Aの是正がこの一行に集約) ──
    _orig_db_available = casper_embed.db_available
    casper_embed.db_available = lambda: True
    casper_embed._EMB_HEALTH.update({"ok": False, "ts": 1000.0, "fails": 1})
    casper_embed._probe = _fake_probe_down
    time.time = lambda: 1010.0   # TTL内・down状態を維持
    chk("①' available(): dbは在れど埋込サーバがdownならFalse(欠陥Aの是正)",
        casper_embed.available(), False)
    casper_embed._EMB_HEALTH.update({"ok": True, "ts": 1010.0, "fails": 0})
    chk("①' available(): dbも埋込サーバも健全ならTrue",
        casper_embed.available(), True)
    casper_embed.db_available = _orig_db_available

    # ── ③ 材料ゼロで断言せぬ: hits=0だがfullnoteだけ在る(実害の形)は"thin" ──────────────
    chk("③ _grounding_state: hits=0・fulltext長文 → thin(実害の形)",
        _grounding_state([], "長文長文長文" * 100, "携帯ではいりたいんだけど"), "thin")
    chk("③ _grounding_state: hits=0・fulltextも無し → none(材料ゼロ)",
        _grounding_state([], "", "何か聞きたい"), "none")
    chk("③ _grounding_state: hits=0・fulltext=None → none",
        _grounding_state([], None, "何か聞きたい"), "none")

    # ── ④ 十分な時は答える: hits在り → grounded(注意書きを一切足さない・退行なし) ─────────
    chk("④ _grounding_state: hits=8件・fulltext在り → grounded(従来通り)",
        _grounding_state(["h"] * 8, "全文", f"{_PJ}の状況は？"), "grounded")
    chk("④ _grounding_state: hits=1件のみでもgrounded(件数閾値で切らない)",
        _grounding_state(["h"], "全文", "おはよう"), "grounded")
    chk("④ _grounding_state: hits在り・fulltext無しでもgrounded",
        _grounding_state(["h"] * 8, None, "ARKitLedScanを説明して"), "grounded")

    # ── ⑤ 配線検査(欠陥B): _grounding_stateの戻り値ではなく実注入文字列を見る ──────────
    # none分岐: hits=[] fulltext="" → _build_grounding_blockの出力に_GROUNDING_NOTE["none"]の
    # 文言が★実際に含まれること(以前はfullnoteがfulltext有無に閉じ込められ永久に注入されなかった)。
    _none_state = _grounding_state([], "", "何か聞きたい")
    _none_block = _build_grounding_block(_none_state, None, "")
    chk("⑤ 配線: none分岐で_GROUNDING_NOTE['none']の文言が実際に注入される",
        _GROUNDING_NOTE["none"] in _none_block, True)

    # thin分岐: 引き続き正しく注入される(退行なし)
    _thin_state = _grounding_state([], "長文長文長文" * 100, "携帯ではいりたいんだけど")
    _thin_block = _build_grounding_block(_thin_state, "src.md", "長文長文長文" * 100)
    chk("⑤ 配線: thin分岐で_GROUNDING_NOTE['thin']の文言が実際に注入される",
        _GROUNDING_NOTE["thin"] in _thin_block, True)
    chk("⑤ 配線: thin分岐で全文も併せて注入される",
        "長文長文長文" in _thin_block, True)

    # grounded分岐: 何も注入されない(空文字列)
    _grounded_state = _grounding_state(["h"] * 8, None, f"{_PJ}の状況は？")
    _grounded_block = _build_grounding_block(_grounded_state, None, None)
    chk("⑤ 配線: grounded分岐は何も注入しない(空文字列)",
        _grounded_block, "")

finally:
    casper_embed._probe = _orig_probe
    casper_embed._EMB_HEALTH.clear()
    casper_embed._EMB_HEALTH.update(_orig_health)
    time.time = _orig_time


n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
