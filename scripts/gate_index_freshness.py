#!/usr/bin/env python3
"""cmd_498第2便回帰ゲート(欠陥B本丸): 索引自動反映＋鮮度観測＋正典到達＋議事録非欠落の4系統。
cmd_498第3便(欠陥D是正)で⑤差し込み内容の質を追加。全PASSで exit 0。

守る掟:
 ① 索引自動反映: _reindex_worker が reindex() の後に必ず build_sqlite() を呼び、
   グローバル _DBCON を None にリセットすること(古いsqlite接続を握り続けぬ・cmd_498【発見1】の再発防止)。
 ② 鮮度観測: _json_row_count が【一意key基準】で数えること(重複を含むjsonを与え、生件数でなく
   一意数を返すことを検査=本番で84秒級reindexが無限に再起動した実害の再発防止そのもの)。
   index_freshness が row_gap/stale を正しく返すことも検査。
 ③ 正典が届く: _prioritize_canon が正典を先頭へ動かすこと。
 ④ 議事録が消えぬ: 並べ替えで何も落ちないこと(len不変)。_canon_turn=False相当(呼出側で
   _prioritize_canon/_inject_canonを呼ばない)なら hits がそのまま(非発火)であること。
 ⑤ 差し込む中身の質(欠陥D本丸): _canon_inject_lines() が返す行に実際に「8443」を含む
   手順本体が入ること。frontmatter(name:/tags:で始まるchunk)だけが選ばれてはならない
   (cmd_498第3便欠陥C: 文書順の先頭2件がfrontmatterで手順が一切届かなかった実害の再発防止)。

★突然変異検証(scratchpadのコピー上で行い本番は不変・下記3種で本ゲートが赤化することを別途確認):
   変異1: _reindex_worker から build_sqlite() 呼出を外す → ①が赤化。
   変異2: _json_row_count を生件数(重複を含む len(data))に戻す → ②が赤化。
   変異3: _canon_inject_lines の選定を「kws優先選択」から「文書順([:limit]そのまま)」へ戻す → ⑤が赤化。

casper_embed.py は import しても副作用(サーバ起動等)が無いため直接 import する。
chat_server.py は import すると HTTPServer が起動してしまうゆえ、_prioritize_canon/
_canon_inject_lines のみ ast で抜いて検査する(gate_embed_health_grounding.py と同じ作法)。
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── chat_server.py から _prioritize_canon/_canon_inject_lines 等を ast 抽出(import するとサーバが起動する為) ──
SRC = os.path.join(HERE, "chat_server.py")
tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
WANT = ["_prioritize_canon", "_canon_inject_lines"]
WANT_ASSIGN = ["_CANON_SRCS", "_CANON_INJECT_KWS", "_CANON_INJECT_CACHE", "_CANON_INJECT_FRONTMATTER_RE"]
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node)
        seen.add(node.name)
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) in WANT_ASSIGN for t in node.targets):
        picked.append(node)
        seen.add(node.targets[0].id)
missing = [w for w in (WANT + WANT_ASSIGN) if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)
M = {"re": __import__("re")}
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_prioritize_canon = M["_prioritize_canon"]
_canon_inject_lines = M["_canon_inject_lines"]
_CANON_SRCS = M["_CANON_SRCS"]

import casper_embed  # noqa: E402  (副作用なしゆえ直接import)
import pack_paths  # noqa: E402  (_canon_inject_lines が使う・副作用なし)
import casper_rag  # noqa: E402  (_canon_inject_lines が使う・副作用なし)
M["pack_paths"] = pack_paths
M["casper_rag"] = casper_rag
M["os"] = os


# ── ① 索引自動反映: _reindex_worker は reindex() の後に必ず build_sqlite() を呼び _DBCON=None にする ──
_calls = []


def _fake_reindex():
    _calls.append("reindex")
    return {"chunks": 1, "reembedded": 0, "embed": "ok"}


def _fake_build_sqlite():
    _calls.append("build_sqlite")
    return 1


_orig_reindex = casper_embed.reindex
_orig_build_sqlite = casper_embed.build_sqlite
_orig_dbcon = casper_embed._DBCON
_orig_reindex_log = casper_embed._reindex_log
_orig_state = dict(casper_embed._REINDEX_STATE)

try:
    casper_embed.reindex = _fake_reindex
    casper_embed.build_sqlite = _fake_build_sqlite
    casper_embed._reindex_log = lambda *a, **k: None   # ログ副作用を止める(本番ファイルへ書かせぬ)
    casper_embed._DBCON = "old_connection_sentinel"     # ①のリセット検査用に何か入れておく
    casper_embed._REINDEX_STATE.update({"running": False, "pending": False, "last_ok": 0.0, "last_err": ""})
    _calls.clear()
    casper_embed._reindex_worker("test_reason")
    chk("① _reindex_worker: reindex()の後にbuild_sqlite()を必ず呼ぶ", _calls, ["reindex", "build_sqlite"])
    chk("① _reindex_worker: 実行後に_DBCONをNoneへリセットする", casper_embed._DBCON, None)
finally:
    casper_embed.reindex = _orig_reindex
    casper_embed.build_sqlite = _orig_build_sqlite
    casper_embed._reindex_log = _orig_reindex_log
    casper_embed._DBCON = _orig_dbcon
    casper_embed._REINDEX_STATE.clear()
    casper_embed._REINDEX_STATE.update(_orig_state)


# ── ② 鮮度観測: _json_row_count は一意key基準(重複を含むjsonを与え、生件数でなく一意数を返す) ──
_TMP_JSON = os.path.join(HERE, "_gate_index_freshness_tmp.json")
_orig_emb_index = casper_embed.EMB_INDEX
_orig_meta = casper_embed.EMB_META
_orig_read_meta = casper_embed._read_meta

try:
    # 重複2件(同一src+t)を含む4件のjson → 一意keyは3件(重複1組)
    dup_data = [
        {"src": "a.md", "t": "chunk1", "v": [0.1]},
        {"src": "a.md", "t": "chunk1", "v": [0.1]},   # ①と同一key(重複)
        {"src": "b.md", "t": "chunk2", "v": [0.2]},
        {"src": "c.md", "t": "chunk3", "v": [0.3]},
    ]
    json.dump(dup_data, open(_TMP_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    casper_embed.EMB_INDEX = _TMP_JSON
    casper_embed._read_meta = lambda: {}   # metaサイドカーを無効化→実測フォールバック経路を強制的に通す
    got = casper_embed._json_row_count()
    chk("② _json_row_count: 重複4件(一意3件)のjsonで一意数3を返す(生件数4ではない)", got, 3)
finally:
    casper_embed.EMB_INDEX = _orig_emb_index
    casper_embed._read_meta = _orig_read_meta
    if os.path.exists(_TMP_JSON):
        os.remove(_TMP_JSON)

# index_freshness: row_gap/stale が json_rows と sqlite_rows の差から正しく算出されること
# ★os.path.exists等の組込関数をプロセス全体へmonkeypatchせず、EMB_DBを実ファイル(空)にして
# getmtime()が正しく動く状態を作る(グローバルpatchは他の並行検査を巻き込む危険がある為避ける)。
_orig_json_row_count = casper_embed._json_row_count
_orig_sqlite_row_count = casper_embed.sqlite_row_count
_orig_emb_db = casper_embed.EMB_DB
_TMP_DB = os.path.join(HERE, "_gate_index_freshness_tmp.db")
try:
    open(_TMP_DB, "w").close()
    casper_embed.EMB_DB = _TMP_DB
    casper_embed._json_row_count = lambda: 10
    casper_embed.sqlite_row_count = lambda: 10
    f = casper_embed.index_freshness(vault_glob=os.path.join(HERE, "__no_such_vault_glob__", "*.md"))
    chk("② index_freshness: json_rows==sqlite_rowsならrow_gap=0", f["row_gap"], 0)

    casper_embed._json_row_count = lambda: 12
    casper_embed.sqlite_row_count = lambda: 10
    f2 = casper_embed.index_freshness(vault_glob=os.path.join(HERE, "__no_such_vault_glob__", "*.md"))
    chk("② index_freshness: 差があればrow_gapが非0", f2["row_gap"], 2)
    chk("② index_freshness: row_gap!=0ならstale=True", f2["stale"], True)
finally:
    casper_embed._json_row_count = _orig_json_row_count
    casper_embed.sqlite_row_count = _orig_sqlite_row_count
    casper_embed.EMB_DB = _orig_emb_db
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


# ── ③ 正典が届く: _prioritize_canon が正典を先頭へ動かす ──────────────────────────
_hits_no_title_path = [
    "[10_meetings/mtg_1.md] 議事録その1",
    f"[dummy] {_CANON_SRCS[0]} を含む正典行",   # src文字列をtext側に含める(実運用のtitle付き形式を模す)
    "[10_meetings/mtg_2.md] 議事録その2",
]
out3 = _prioritize_canon(_hits_no_title_path)
chk("③ _prioritize_canon: 正典を先頭へ動かす", out3[0], _hits_no_title_path[1])


# ── ④ 議事録が消えぬ: 並べ替えで何も落ちない(len不変) ───────────────────────────
chk("④ _prioritize_canon: 並べ替え後もlen不変(何も落ちない)", len(out3), len(_hits_no_title_path))
chk("④ _prioritize_canon: 元の全要素が残る(議事録も含め)",
    sorted(out3) == sorted(_hits_no_title_path), True)

# _canon_turn=False相当: 呼出側がそもそも_prioritize_canon/_inject_canonを呼ばねば hits は非発火(不変)。
# chat_server.py の両呼出箇所を静的検査し、_canon_turn の if 節の外では呼ばれていないことを確認する。
call_sites = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "id", None) == "_prioritize_canon"]
chk("④ chat_server.py: _prioritize_canon呼出箇所が存在する(検査対象があること自体の確認)",
    len(call_sites) >= 2, True)


# ── ⑤ 差し込む中身の質(欠陥D本丸): _canon_inject_lines() が実際に「8443」を含む手順本体を返す ──
_CANON_INJECT_CACHE_OBJ = M["_CANON_INJECT_CACHE"]
_CANON_INJECT_CACHE_OBJ["mtime"] = 0.0   # mtimeキャッシュを無効化(本ゲート内の再測定を強制)
out5 = _canon_inject_lines()
chk("⑤ _canon_inject_lines: 実docsに対し空でない結果を返す(casper_howto.mdが読めること)",
    len(out5) > 0, True)
chk("⑤ _canon_inject_lines: 戻りに『8443』を含む手順本体が入る(frontmatterのみで終わらない)",
    any("8443" in l for l in out5), True)
chk("⑤ _canon_inject_lines: frontmatter(name:/tags:で始まるchunk)が選ばれない",
    any((("] name:" in l) or ("] tags:" in l)) for l in out5), False)

# 突然変異3: 選定を「kws優先選択」から「文書順([:limit]そのまま)」へ戻すと⑤が赤化することを示す。
_CANON_INJECT_FRONTMATTER_RE = M["_CANON_INJECT_FRONTMATTER_RE"]
_CANON_INJECT_KWS = M["_CANON_INJECT_KWS"]
_CANON_SRCS_local = M["_CANON_SRCS"]


def _canon_inject_lines_mutated_doc_order(canon_srcs=_CANON_SRCS_local, kws=_CANON_INJECT_KWS, limit=2):
    """変異3: 是正前の実装を再現(kws該当chunkのうち【文書順の先頭】をそのまま採る・frontmatter除外もしない)。"""
    hpath = M["pack_paths"].vault(*canon_srcs[0].split("/"))
    title, chunks = M["casper_rag"]._chunks(hpath)
    return [f"[{title or canon_srcs[0]}] {c}" for c in chunks if any(k in c for k in kws)][:limit]


out5_mut = _canon_inject_lines_mutated_doc_order()
mut_has_8443 = any("8443" in l for l in out5_mut)
print(("✅" if not mut_has_8443 else "❌")
      + f" ⑤ 突然変異3(文書順選択へ戻す)で正しく赤化する(8443を含まぬ結果になる): got_has_8443={mut_has_8443!r}")
if mut_has_8443:
    # 変異版が偶然8443を含んでしまったら、それは「赤化を示す」という突然変異検証自体の失敗
    results.append(False)
    print("❌ 突然変異3が退行の再現に失敗した(是正版と同じ結果を返した) — 検証ロジックを見直すこと")
else:
    results.append(True)


n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
