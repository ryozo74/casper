#!/usr/bin/env python3
"""cmd_496第2便/第3便: PJ一括起票(Excel取込)の途中切れ・分割・結合の回帰ゲート（純機構・読取のみ）。
全PASSで exit 0。

守る掟:
 ① 途中切れ(truncated)判定は文字列リテラル内の { } / エスケープを正しく無視する状態機械であり、
    素朴な文字数カウントに劣化していないこと(軍師QC subtask_496_qc2 ac6_independent_verification の
    意地の悪い12件を流用)。
 ② 分割(_split_grid_rows)は境界(chunk_rows前後)でオフバイワンなく、行の合計が常に一致し、
    ヘッダー行が全チャンクへ複製されること。
 ③ 結合(_merge_import_proposals)は「一つでも成功すれば成功扱い」に劣化しないこと。
    ★特に部分失敗(一部チャンクのみ失敗)時、失敗が黙って握り潰されず merged["_partial"] に
    必ず現れること(cmd_496第3便 欠陥Aの再発防止そのもの)。全失敗・重複排除も併せて確認する。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該関数のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る)。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

WANT = ["_brace_balance_truncated", "_split_grid_rows", "_merge_import_proposals",
        "_IMPORT_CHUNK_ROWS"]

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
    sys.exit(1)

M = {}
exec("import re, os, json", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_truncated = M["_brace_balance_truncated"]
_split = M["_split_grid_rows"]
_merge = M["_merge_import_proposals"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── ① _brace_balance_truncated: 軍師QC(subtask_496_qc2)の意地の悪い12件を流用 ──────────
_TRUNC_CASES = [
    ('{"a":"}"}', False),          # 文字列内の } を閉じと誤カウントせぬ
    ('{"a":"{{{"}', False),        # 文字列内の { を開きと誤カウントせぬ
    ('{"a":"\\""}', False),        # エスケープされた " を終端と誤らぬ
    ('{"a":"c:\\\\"}', False),     # 末尾バックスラッシュのエスケープ
    ('{"a":1,}', False),           # 構文エラーだが均衡=truncatedでない
    ('{"a" 1}', False),            # コロン欠落の構文エラー=均衡
    ('{"tasks":[{"note":"照明の調整', True),   # 実害の形(値の途中で切断)を検知
    ('{}', False),                 # 空オブジェクトは均衡
    ('{"a":{"b":1}}', False),      # 正常なネストは均衡
    ('{"a":{"b":1}', True),        # ネスト途中で切れ
    ('{"a":"unterminated', True),  # 文字列を開いたまま終わる
    ('', False),                   # 空文字は均衡(depth=0)
]
for i, (text, want) in enumerate(_TRUNC_CASES):
    chk(f"_brace_balance_truncated 境界{i+1}: {text[:24]!r}", _truncated(text), want)

# ── ② _split_grid_rows: n=24/25/26/30/100 で行の合計一致・ヘッダー複製 ──────────────
CHUNK_ROWS = M["_IMPORT_CHUNK_ROWS"]
for n in (24, 25, 26, 30, 100):
    grid = "\n".join(["HEADER"] + [f"row{i}" for i in range(n)])
    chunks = _split(grid, CHUNK_ROWS)
    total_body_rows = 0
    headers_ok = True
    for c in chunks:
        lines = c.splitlines()
        if not lines or lines[0] != "HEADER":
            headers_ok = False
        total_body_rows += max(0, len(lines) - 1)
    chk(f"_split_grid_rows n={n}: 行の合計一致", total_body_rows, n)
    chk(f"_split_grid_rows n={n}: 全チャンクへヘッダー複製", headers_ok, True)
    expect_chunks = 1 if n <= CHUNK_ROWS else -(-n // CHUNK_ROWS)  # ceil
    chk(f"_split_grid_rows n={n}: チャンク数", len(chunks), expect_chunks)

# 境界そのもの(chunk_rows行ちょうど・chunk_rows+1行)が単一↔分割で正しく切り替わるか
chk(f"_split_grid_rows n={CHUNK_ROWS}(境界ちょうど): 単一チャンク",
    len(_split("\n".join(["H"] + [f"r{i}" for i in range(CHUNK_ROWS)]), CHUNK_ROWS)), 1)
chk(f"_split_grid_rows n={CHUNK_ROWS + 1}(境界+1): 2分割",
    len(_split("\n".join(["H"] + [f"r{i}" for i in range(CHUNK_ROWS + 1)]), CHUNK_ROWS)), 2)

# ── ③ _merge_import_proposals: 全失敗・部分失敗(欠陥A是正の期待値)・重複排除 ──────────
# 全成功: _partial は付かない
_all_ok = [
    {"project": {"name": "P"}, "shots": [{"code": "S1"}], "tasks": [{"note": "t1"}]},
    {"project": {}, "shots": [{"code": "S2"}], "tasks": [{"note": "t2"}]},
]
_m_ok = _merge(_all_ok)
chk("_merge 全成功: _partial なし", "_partial" in _m_ok, False)
chk("_merge 全成功: tasks件数", len(_m_ok["tasks"]), 2)
chk("_merge 全成功: error キーなし", "error" in _m_ok, False)

# 全失敗: {"error":...} 形式(単一呼出と同形)で返り、_partial は付かない
_all_fail = [
    {"error": "e1", "raw": ""},
    {"error": "e2", "raw": ""},
]
_m_fail = _merge(_all_fail)
chk("_merge 全失敗: error キーあり", "error" in _m_fail, True)
chk("_merge 全失敗: _partial なし(単一呼出と同形を維持)", "_partial" in _m_fail, False)

# ★部分失敗(欠陥A是正の本丸): 軍師が実測した「30行を2分割し2番目が失敗」を再現。
# 修正前は tasks=25件・_partial無しで ok:true 相当に化けていた欠陥。
_partial_case = [
    {"project": {"name": "P"}, "shots": [], "tasks": [{"note": f"t{i}"} for i in range(25)]},
    {"error": "1行あたりの記述が長すぎるようです。", "raw": "...", "truncated": True},
]
_m_partial = _merge(_partial_case)
chk("_merge 部分失敗: _partial が必ず付く(欠陥A是正)", "_partial" in _m_partial, True)
chk("_merge 部分失敗: error キーは付かない(呼出側でokを別途判定する設計)", "error" in _m_partial, False)
chk("_merge 部分失敗: failed_chunks", _m_partial.get("_partial", {}).get("failed_chunks"), 1)
chk("_merge 部分失敗: total_chunks", _m_partial.get("_partial", {}).get("total_chunks"), 2)
chk("_merge 部分失敗: tasksは成功分のみ(欠落は_partialで可視化・黙って消えぬ)",
    len(_m_partial["tasks"]), 25)
chk("_merge 部分失敗: errorsに失敗理由が残る", len(_m_partial.get("_partial", {}).get("errors", [])), 1)

# 重複排除: shots は code で重複排除、project は最初に取れたものを採用
_dup = [
    {"project": {"name": "First"}, "shots": [{"code": "C001", "name": "a"}], "tasks": []},
    {"project": {"name": "Second"}, "shots": [{"code": "C001", "name": "b"}, {"code": "C002"}], "tasks": []},
]
_m_dup = _merge(_dup)
chk("_merge 重複排除: shots件数(C001は1件に畳まれる)", len(_m_dup["shots"]), 2)
chk("_merge project採用: 最初に取れたものを維持", _m_dup["project"].get("name"), "First")

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
