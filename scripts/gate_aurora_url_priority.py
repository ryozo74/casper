#!/usr/bin/env python3
"""貼られたAurora資料URLが無関係な他資料に負ける欠陥(cmd_493)の回帰ゲート(純機構・インメモリ・読取のみ)。
全PASSで exit 0。

守る掟:
 ① 一次資料を機構が取得したturn(aurora_url_digest成功)では、vault(RAG/casper_rag.top_source)を
    併走注入せぬこと。実測2026-07-31 20:35: ou殿の実URL+実文言で、材料は正しく注入(4508字)されて
    いたのに、RAG top_source が無関係な他人の資料(asset_ARKitLedScan_DesignSpec.md)を並走注入し、
    弱qwenがそちらを『提供された資料』と誤認して答えた(rag_hits=5・ctx_len=14248)。
 ② 取得失敗時(AC3・退行検査)は正直な出口("読めていない。読んだ前提で語るな")を温存し、
    かつ vault 併用を妨げぬこと(一次資料が無いturnまでvaultを止める理由は無い)。
 ③ 掟(語でなく機構): 優先順位は指示文の強さでなく、vault注入そのものを止める構造で強制する
    (chat_server.py 8560-8580行付近の _au_resolved ゲート配線を検査)。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該機構のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る・gate_aurora_save.py と同じ流儀)。
"""
import ast
import os
import sys

import pack_config

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
WANT = ["_AURORA_URL_RE", "_AURORA_URL_MEMO", "aurora_url_digest", "seiri_aurora_fetch",
        # 2026-08-26: 本文と同じ便で「直せる」鍵(doc_id)も渡すようになった
        "aurora_doc_ref", "_AURORA_DOC_URL_RE", "_AURORA_DOC_ID_RE",
        # 2026-08-27: 解決した資料を turn を跨いで保つ錨
        "aurora_pin_set", "aurora_pin_get", "aurora_pin_key", "_AURORA_PIN",
        "_AURORA_PIN_TTL", "_AURORA_PIN_RELEASE_RE",
        "aurora_pin_set_for", "aurora_pin_get_any", "aurora_pin_user_key",
        "_pin_log", "_pin_save", "_AURORA_PIN_FILE", "_AURORA_PIN_LOG"]

# cmd_495: 固有名は pack から受け取る(gate_pjname.pyと同じ流儀・cmd_491 AC3の趣旨を本ゲートにも適用)。
_examples = pack_config.get("examples", {}) or {}
_PJ = (_examples.get("project_names") or ["sample-pj"])[0]

_src_text = open(SRC, encoding="utf-8").read()
tree = ast.parse(_src_text)
picked, seen = [], set()
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef,)) else
             [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
    for nm in names:
        if nm in WANT:
            picked.append(node)
            seen.add(nm)
missing = [w for w in WANT if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

M = {}
exec("import re, os, json, time, datetime, threading", M)
M["HERE"] = HERE   # 錨のファイル定義が参照する
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_digest = M["aurora_url_digest"]
_memo = M["_AURORA_URL_MEMO"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


_REAL_URL = ("http://nina_notepc_02.local:8100/doc/elvis/2026-07-30/"
             "hikou-fairu-kirikae-kinou-irai-shiyousho-hikou-tan-an-sekkan")
_REAL_Q = f"{_REAL_URL} この内容を簡単に教えてください"

# ══════════════════════════════════════════════════════════════════════════
# ① 貼られた資料が使われる(AC1・AC4本体) — 取得成功時、一次資料マーカーと本文が注入されること。
# ══════════════════════════════════════════════════════════════════════════
_memo.clear()
M["seiri_aurora_fetch"] = lambda u: {
    "ok": True, "title": "飛行ファイル切替機能 依頼仕様書", "url": u,
    "material": "【Aurora資料: 飛行ファイル切替機能 依頼仕様書】\n最小間隔50cm厳守。start_epoch信号で世代識別。",
    "coverage": {"insufficient": False}}
_note = _digest(_REAL_Q)
chk_true("AC1 取得成功時: URLが本文に含まれる", _REAL_URL in _note)
chk_true("AC1 取得成功時: 一次資料マーカーが立つ(vaultゲートの発火条件そのもの)", "これが一次資料" in _note)
chk_true("AC1 取得成功時: 資料本文(material)が実際に注入される", "飛行ファイル切替機能" in _note and "start_epoch" in _note)
chk_true("AC1 取得成功時: 既知内容の再問いを禁ずる指示が含まれる", "お知らせください" in _note)

# メモ化: 同一URLの2回目呼出はネットワークを叩かず同じnoteを返すこと(既存仕様の温存確認)
_note2 = _digest(_REAL_Q)
chk("AC1 メモ化: 同一URLの2回目は同一note(キャッシュ経由)", _note2, _note)

# ══════════════════════════════════════════════════════════════════════════
# ② 取得失敗時に正直に断る(AC3・退行検査) — 一次資料マーカーが立たず、読んだ前提で語るなと明記。
# ══════════════════════════════════════════════════════════════════════════
_memo.clear()
M["seiri_aurora_fetch"] = lambda u: {"ok": False, "error": "Aurora資料の形式を解釈できませんでした。"}
_fail_note = _digest(_REAL_Q)
chk_true("AC3 取得失敗時: 一次資料マーカーが立たない(vaultを止めぬ条件そのもの)",
         "これが一次資料" not in _fail_note)
chk_true("AC3 取得失敗時: 取得できず、の正直な出口が残る", "取得できず" in _fail_note)
chk_true("AC3 取得失敗時: 読んだ前提で語るな、の禁止文言が残る", "読んだ前提で内容を語るな" in _fail_note)
chk_true("AC3 取得失敗時: 資料本文(material)は注入されない(捏造の材料を与えぬ)",
         "start_epoch" not in _fail_note)

# ══════════════════════════════════════════════════════════════════════════
# ③ URLが無いturnは何も注入しない(既存仕様の温存確認・回帰番犬)
# ══════════════════════════════════════════════════════════════════════════
_memo.clear()
chk("既存回帰: Aurora URLの無い発話はnoteが空文字", _digest(f"{_PJ}の状況は？"), "")

# ══════════════════════════════════════════════════════════════════════════
# ④ 他人の無関係な資料が根拠に現れぬこと(AC2) — vaultゲート配線検査(部品でなく配線・
#    gate_aurora_save.pyのAC12/AC14と同じ「部品は試すが配線は試さない」への対処)。
#    chat_server.py本体のvault注入if文が _au_resolved を条件に含み、かつ _au_resolved が
#    _au_note のマーカー判定そのものであることをソーステキストで検査する(名前/条件が消える
#    突然変異を機械的に赤化する)。
# ══════════════════════════════════════════════════════════════════════════
chk_true("AC2配線: _au_resolved が _au_note の一次資料マーカーから決定的に定まる",
         '_au_resolved = "これが一次資料" in _au_note' in _src_text)
chk_true("AC2配線: vault注入のif条件に _au_resolved が含まれる(RAG併走を止める本体)",
         'if not (_status_q or _au_resolved):' in _src_text)
# _au_note/_au_resolved の代入が、vaultブロック(_status_q定義)より前に位置すること
# (後段で定義したのでは間に合わぬ・実行順序そのものの検査)。
# ★呼出の形(引数)は変わり得る。名で探して行順だけを検める(形に縛られぬ)。
_au_note_pos = _src_text.index('_au_note = aurora_url_digest(ll_user')
_vault_gate_pos = _src_text.index('if not (_status_q or _au_resolved):')
chk_true("AC2配線: _au_note/_au_resolved の算出が vault ゲート判定より先に実行される(行順)",
         _au_note_pos < _vault_gate_pos)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
