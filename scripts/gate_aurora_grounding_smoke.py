#!/usr/bin/env python3
r"""接地の注記を**実データ**へ当てる煙試験(合成の門 gate_aurora_grounding.py とは別建て)。

★合成の門は「機構が動くか」を証す。此方は「本番の台帳に当てて**誤って鳴りすぎぬか**」を測る。
  実データ試験を合成の門に混ぜれば、データが変わる度に門が赤くなり、やがて誰も見なくなる。

【材料の出所】
  ・新しい下書きには台帳に `grounding.material` が刻まれておる(2026-08-29 の手当以降)。**それを正とする。**
  ・それ以前の記録には材料が無い。ゆえ会話記録から**再構成**する(直前の人の発話8つ・同uid・60分以内
    ＋発端の発話＋題)。★これは本番の sources より**狭い**(注入された資料・錨の正本を含まぬ)ゆえ、
    ここで数える発火は**上限**である——本番ではこれより鳴らぬ。

【合否の見方】
  承認された正本(sent)で鳴りすぎておらぬこと。却下/期限切れ(捏造を含む群)では鳴ること。
  ★数そのものは動く。ゆえ閾値は緩く置き、**向き**が保たれておるかを見る。
"""
import ast
import datetime
import json
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_outbox as ob                                  # noqa: E402

SRC_TEXT = open(os.path.join(HERE, "chat_server.py"), encoding="utf-8").read()
CONVO = os.path.join(HERE, "conversation_log.jsonl")

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ★本番の関そのものを抜いて使う(写しを作らぬ——写しは本体と食い違う)
_M = {}
exec("import re, threading", _M)
_tree = ast.parse(SRC_TEXT)
_want_f = {"aurora_fact_tokens", "aurora_ungrounded_facts"}
_want_a = {"_FACT_DATE_RE", "_FACT_QTY_RE", "_FACT_STATE_RE"}
_picked = [n for n in _tree.body
           if (isinstance(n, ast.FunctionDef) and n.name in _want_f)
           or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in _want_a for t in n.targets))]
exec(compile(ast.Module(body=_picked, type_ignores=[]), "chat_server.py", "exec"), _M)
chk("本番の関を抜いて使えておる", len(_picked) == len(_want_f) + len(_want_a))

_rows = []
if os.path.exists(CONVO):
    for ln in open(CONVO, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("role") == "user":
            _rows.append(r)


def _ts(x):
    try:
        return datetime.datetime.fromisoformat(str(x))
    except Exception:
        return None


def material_for(rec):
    """材料。台帳に刻まれておればそれを正とし、無ければ会話記録から再構成する(狭い側=上限)。"""
    g = rec.get("grounding") or {}
    if g.get("material"):
        return g["material"], "台帳"
    t0, uid = _ts(rec.get("ts")), str(rec.get("uid") or "")
    src = []
    if t0:
        near = [r for r in _rows if str(r.get("uid") or "") == uid and _ts(r.get("ts"))
                and 0 <= (t0 - _ts(r.get("ts"))).total_seconds() <= 3600]
        near.sort(key=lambda r: r.get("ts") or "")
        src += [str(r.get("content") or "") for r in near[-8:]]
    src.append(str(rec.get("query") or ""))
    src.append(str((rec.get("args") or {}).get("title") or ""))
    return "\n".join(src), "再構成"


recs = [r for r in ob._load() if r.get("tool") == "aurora_create"]
print(f"\n【実データ】aurora_create {len(recs)} 件\n")
tally = {}
for state in ("sent", "rejected", "expired", "proposed", "failed"):
    grp = [r for r in recs if r.get("state") == state]
    if not grp:
        continue
    fired, words = 0, 0
    print(f"── {state} ({len(grp)}件) ──")
    for r in grp:
        body = str((r.get("args") or {}).get("body") or "")
        mat, how = material_for(r)
        ung = _M["aurora_ungrounded_facts"](body, mat)
        fired += 1 if ung else 0
        words += len(ung)
        print(f"  {str(r.get('ts'))[5:16]} [{how}] 不在{len(ung):3d}  {' / '.join(ung[:10])}")
    tally[state] = (fired, len(grp), words)
    print(f"  → 鳴った {fired}/{len(grp)} 件・語 計{words}\n")

s_f, s_n, s_w = tally.get("sent", (0, 0, 0))
r_f, r_n, r_w = tally.get("rejected", (0, 0, 0))
e_f, e_n, e_w = tally.get("expired", (0, 0, 0))

print("--- 判定 ---")
chk("承認された正本が**半数を超えて**鳴ってはおらぬ(注記が景色になれば誰も読まぬ)",
    s_n == 0 or s_f <= s_n / 2)
chk("承認された正本の不在語は少ない(1件あたり平均2語以下)",
    s_n == 0 or (s_w / s_n) <= 2.0)
chk("★捏造を含む群(却下+期限切れ)では鳴る(黙らせただけになっておらぬ)",
    (r_f + e_f) >= 1 and (r_w + e_w) >= 5)
chk("★向きが保たれておる: 捏造群の方が1件あたりの不在語が多い",
    (r_n + e_n) == 0 or s_n == 0 or ((r_w + e_w) / (r_n + e_n)) > (s_w / s_n))
chk("台帳の刻みを正として読める配線が在る(新しい記録は再構成に頼らぬ)",
    '"grounding": grounding}' in open(os.path.join(HERE, "casper_outbox.py"), encoding="utf-8").read())

n_ok, n = sum(1 for x in results if x), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
