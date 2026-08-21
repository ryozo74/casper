#!/usr/bin/env python3
"""PJ名解決器の「実データ」smoke(回帰ゲートに非ず・任意実行・SKIPを容認する)。

cmd_494第7便: gate_pjname.py本体は固定合成データへ移し、Calendar実データ
(/tmp/cal_projects.json)の経時変化(PJのcompleted/offline遷移・該当PJ名の消失等)から
完全に独立させた——それは「コードは無罪か」を都度裏取りする手間を無くすためであり、
「実データが今どう見えているか」を確認する意義まで捨てたわけではない。その確認はこちらへ。

回帰ゲート(gate_pjname.py)とは別建て:
 - 本ファイルはCI必須ゲートではない。exit 1でパイプラインを止めぬ想定で単独実行する。
 - /tmp/cal_projects.json が無い・索引が空の環境ではSKIP(緑と数えぬ・赤とも数えぬ)。
 - 実データ依存ゆえ、PJの状態変化で緑/SKIPが入れ替わっても「コード退行」を意味しない。
   退行の検知は gate_pjname.py(固定データ)の役目。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該関数のみを抜いて検査する。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

WANT = ["_KANA2ROMA", "_KANA_SMALL", "_kana_to_romaji", "_translit_kana_runs",
        "_canonical", "_PJ_ALIAS", "_pj_index", "_pj_name_hit", "_pj_resolve"]

tree = ast.parse(open(SRC, encoding="utf-8").read())
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
exec("import re, os, json", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_resolve = M["_pj_resolve"]

if not (os.path.exists("/tmp/cal_projects.json") and M["_pj_index"]()["idx"]):
    print("⏭  /tmp/cal_projects.json 不在または索引空ゆえ smoke省略（SKIP=未検証・退行とは無関係）")
    sys.exit(0)

idx = M["_pj_index"]()["idx"]
names = [nm for v in idx.values() for nm in v]
print(f"ℹ️  Calendar online PJ数(索引後): {len(names)}")

st, n, _ = _resolve("Calendarのタスクを見せて")
ok1 = "end" not in n
print(("✅" if ok1 else "❌") + f" 'Calendar' を含む問いが PJ 'end' に化けぬ: got names={n!r}")

st2, n2, _ = _resolve("今日の締切は？")
ok2 = st2 == "none"
print(("✅" if ok2 else "❌") + f" 名の無い一般の問いは none: got={st2!r}")

print("\nℹ️  本ファイルはCI必須ゲートではない(smoke)。実データの現況把握が目的で、"
      "FAILがあっても回帰ゲート(gate_pjname.py)の合否には影響しない。")
sys.exit(0)
