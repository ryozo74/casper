#!/usr/bin/env python3
"""病二(鏡)の回帰ゲート(cmd_508第1便・純機構・インメモリ・読取のみ)。全PASSで exit 0。

守る掟:
 ① Casperが自動生成する社員個性プロファイル(20_people/profile_u_*.md)は本人の発話を原文
    引用しているため、字面検索の索引に混じると本人が同じ言葉を打つと必ず自分の調書が釣れる
    (実害2026-08-17実測: tetsuo発話「この内容を共有」→profile_u_30がcasper_rag.candidates()
    上位に混入)。字面検索三経路(candidates/search/top_source)すべてが _EXCLUDE_SRC を通るので、
    一行の追加で三経路同時に塞がる(AC1)。
 ② _excluded は前方一致(startswith)。除外パターンを "20_people" とだけ書くと人物ノート全体
    (正当な人物情報)が消える。"20_people/profile_" に限定されていることを機構で守る(危険な罠)。
 ③ ファイル名幹(profile_u_30等・観測層の内部識別子)がroster/人物別名索引(閉集合)を通らぬまま
    「人」として主語に立つ文は、corpus分割とは独立した二重の壁(出口検問)で差し止める(AC2)。
    corpus分割が万一漏れても実害A02(「このアカウントは名簿上profile_u_30という仮称」)は
    ここで止まる。roster在籍者への正当な言及は一切妨げない。

casper_rag.py は import してもサーバは起動しない(純関数モジュール)ので直接 import する。
chat_server.py は import すると server が起動してしまうゆえ、ast で当該関数のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る)。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

import casper_rag
import pack_config

# cmd_491 AC3作法: 固有名は pack から受け取る(engine_scan vocab違反回避)。
_PJ = (pack_config.get("examples", {}).get("project_names") or ["sample-pj"])[0]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── ① AC1字面: _excluded が profile_* を三経路(candidates/search/top_source)すべてで塞ぐ ──
chk("_EXCLUDE_SRC に 20_people/profile_ を含む", "20_people/profile_" in casper_rag._EXCLUDE_SRC, True)
chk("profile_u_30.md は _excluded=True", casper_rag._excluded("20_people/profile_u_30.md"), True)
chk("profile_u_1.md も _excluded=True(閉集合全数の一部)", casper_rag._excluded("20_people/profile_u_1.md"), True)
chk("相対パス先頭 './' があっても除外される", casper_rag._excluded("./20_people/profile_u_30.md"), True)

# ── ② 危険な罠: "20_people" とだけ書くと人物ノート全体が消える。限定パターンを機構で守る ──
chk("正当な人物ノート(profile_接頭辞なし)は除外されぬ",
    casper_rag._excluded("20_people/_kiyotomo.md"), False)
chk("正当な人物ノート(01_黒丸クロマル.md)は除外されぬ",
    casper_rag._excluded("20_people/01_黒丸クロマル.md"), False)
chk("legacy除外は既存のまま健在(regressionでない)",
    casper_rag._excluded("80_legacy_score/x.md"), True)

# ── ③ AC1実測: 実索引(casper_rag_index.json)を通した候補列挙で profile_* が現れぬこと ──
#    実害の再現クエリ(profile_u_30.md本文が原文引用している言い回し)を使う。
_PROBE_QUERIES = ["この内容を共有して", "工数を教えて"]
if os.path.exists(casper_rag.INDEX):
    for q in _PROBE_QUERIES:
        cands = casper_rag.candidates(q, n=60)
        hit = any("profile_" in (e.get("src") or "") for e in cands)
        chk(f"AC1実測(candidates): 『{q}』でprofile_*が混入せぬ", hit, False)
        rs = casper_rag.search(q, k=8)
        hit_s = any("profile_u_" in r for r in rs)
        chk(f"AC1実測(search): 『{q}』でprofile_*が混入せぬ", hit_s, False)
    src, _ft = casper_rag.top_source("この内容を共有して")
    chk("AC1実測(top_source): profile_*が選ばれぬ", (src or "").startswith("20_people/profile_"), False)
else:
    print("ℹ️  casper_rag_index.json 不在ゆえ実測smoke分は skip(_excluded単体検査は上で完了済)")

# ── ④ AC1意味検索経路(casper_embed): candidates()を土台とするため _excluded を継承する構造の確認 ──
#    ★casper_embed.py には _excluded 相当が独自には無い(軍師reviewの見落とし穴指摘)。
#    search()がcasper_rag.candidates()を呼ぶ配線がある限り継承される。配線そのものをast検査で守る。
_embed_src = os.path.join(HERE, "casper_embed.py")
_embed_tree = ast.parse(open(_embed_src, encoding="utf-8").read())
_search_fn = next((n for n in ast.walk(_embed_tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "search"), None)
chk("casper_embed.pyにsearch()が存在", _search_fn is not None, True)
if _search_fn:
    _src_text = ast.get_source_segment(open(_embed_src, encoding="utf-8").read(), _search_fn) or ""
    chk("casper_embed.search()がcasper_rag.candidates()を呼ぶ配線(_excluded継承の証跡)",
        "casper_rag.candidates(" in _src_text, True)

# ── ⑤ AC2: 人物スロット検問(roster外のファイル名幹を人として主語に立たせぬ) ──
tree = ast.parse(open(SRC, encoding="utf-8").read())
WANT = ["_UNROSTERED_SLOT_RE", "_guard_unrostered_person_claim", "_ROSTER_MAP",
        "_PERSON_ALIAS", "_person_alias_index"]
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
    sys.exit(1)

M = {}
exec("import re, os, json, glob", M)
import pack_paths
M["pack_paths"] = pack_paths
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_guard = M["_guard_unrostered_person_claim"]
M["_ROSTER_MAP"].update({"30": "tetsuo", "101": "casper"})   # roster固定注入(実データ非依存)

# 実害A02の再現: 「このアカウントは名簿上profile_u_30という仮称」
_A02 = "このアカウントは名簿上profile_u_30という仮称で扱われております。"
_out_a02 = _guard(_A02)
chk("AC2実害再現: profile_u_30という仮称が主語に立たぬ", "profile_u_30" in _out_a02, False)
chk("AC2実害再現: 中立文へ差し替わる", "内部識別子" in _out_a02, True)

# 素のトークンだけの場合も止まること
chk("AC2: 素のprofile_u_30トークンも差し止める",
    "profile_u_30" in _guard("profile_u_30は誰ですか"), False)
chk("AC2: u_数字単体パターンも差し止める",
    "u_5" in _guard("u_5というアカウントについて教えて"), False)

# roster在籍者(tetsuo)への正当な言及は一切妨げない(過剰抑止でないことの確認)
chk("AC2: roster在籍者(tetsuo)への言及は不変",
    _guard("tetsuoさんの担当タスクは3件です。"), "tetsuoさんの担当タスクは3件です。")
chk("AC2: 通常文は完全に不変(素通り)",
    _guard(f"{_PJ}のタスクは進行中です。"), f"{_PJ}のタスクは進行中です。")
chk("AC2: 空文字は空文字のまま", _guard(""), "")

# 人物別名索引(_person_alias_index)にある別名は閉集合内=正当(過剰抑止でないこと)
_alias_idx = M["_person_alias_index"]()
if _alias_idx:
    _sample_alias = next(iter(_alias_idx))
    _txt = f"{_sample_alias}さんの状況は？"
    chk(f"AC2: 人物別名索引内の別名『{_sample_alias}』は不変", _guard(_txt), _txt)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
