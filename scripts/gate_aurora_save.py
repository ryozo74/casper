#!/usr/bin/env python3
"""Aurora「言うただけ」問題(cmd_485/486)の回帰ゲート(純機構・インメモリ・読取のみ)。全PASSで exit 0。

守る掟:
 ① 層1(_wants_aurora_save): 保存意図は語彙列挙でなく構造(Aurora語+委任性)で判定。三値
    (True|False|None)。読取(閲覧/検索)の依頼は先に陰性確定して落とす(順序が肝)。
 ② 層2(_guard_completion_claims): 承認カード(pending_actions)が無いのに「登録しました」等の完了表明が
    あれば打ち消す。Aurora系動詞(登録/保存/作成/起票/資料化/呼び出/表示)まで拡張済(2026-07-30)。
    ツール名を名乗る嘘(「aurora_createを呼び出しました」)も同じ構造で拾うこと。
 ③ 逆方向の異常系(過剰打ち消し禁止): pending_actions有りなら打ち消さぬ／読取の完了表明(読みました等)は
    アクション完了表明でないので打ち消さぬ。
 ④ (cmd_486是正) 分類器が判定不能(None)の時、陰性(False)へ潰さず聞き返しchoicesへ回すこと。
    Noneのままfalsyとして扱う実装ミスは型で検査する(bool(False)とNoneを区別)。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該機構のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る)。
_wants_aurora_save の LLM枝(_wants_aurora_save_llm)は灰色時のみ呼ばれる設計だが、本ゲートは
下層の _ollama_json をモジュール属性差替(stub)することでネットワーク非依存のまま3系統
(save=true/save=false/例外=判定不能)を検査する(cmd_486是正・肝は_ollama_jsonの下層を差し替え、
_wants_aurora_save_llm自体のJSON解析・None返却ロジックは検査対象に含めたままにすること)。
"""
import ast
import os
import sys

import pack_config

HERE = os.path.dirname(os.path.abspath(__file__))
# cmd_491 AC3: PJ名は pack から受け取る(gate_*.py を engine_scan exclude から外しても
# pack_lint が緑のままであるため)。ここでは「Aurora保存意図でない通常のPJ問い」の
# 具体例として使うのみで、実PJ解決の可否は問わない。
_PJ = (pack_config.get("examples", {}).get("project_names") or ["sample-pj"])[0]
SRC = os.path.join(HERE, "chat_server.py")
WANT = ["_AURORA_SAVE_REQ_RE", "_AURORA_READ_RE", "_wants_aurora_save",
        "_guard_completion_claims", "_ASK_KEEP_RE",
        "_AURORA_WORD_RE", "_ASK_DELEGATE_RE", "_SAVE_HINT_RE", "_wants_aurora_save_llm",
        "_AURORA_SAVED_REF_RE", "_AURORA_IMMEDIATE_REQUEST_TAIL_RE", "_BENEFIT_COMPLETION_NEG_RE",
        "_COMPLETION_VERB_COMM_RE", "_COMPLETION_VERB_AURORA_ONLY_RE", "_COMPLETION_VERB_GENERIC_RE",
        "_COMPLETION_TAIL_RE", "_COMPLETION_READ_EXCL_RE", "_COMPLETION_UNDONE_EXCL_RE",
        "_COMPLETION_COMM_RE", "_COMPLETION_AURORA_ONLY_RE", "_COMPLETION_GENERIC_RE",
        "_COMPLETION_VERB_MUTATE_RE", "_COMPLETION_MUTATE_RE", "_COMPLETION_DOC_NOUN_RE",
        "_DECLINE_LOG",   # 2026-08-26: 本ゲートは以前から NameError で落ちていた(=何も守れていなかった)
        # ↑2026-08-26: 資料/議事録の削除・編集・更新を「した」と偽る主張の検問

        "_completion_claim_line_hit", "_COMPLETION_GAP_RE",
        "_AURORA_EDIT_READ_VERB_RE", "_AURORA_IMMEDIATE_TAIL_VERB_BOUNDARY_NEG",
        "_AURORA_CLAUSE_SPLIT_RE", "_aurora_edit_read_verb_same_clause", "_aurora_read_verb_same_clause",
        "_aurora_clause_delegate_form",
        "_aurora_save_unknown_choices", "_aurora_save_title_unknown_choices", "_resolve_aurora_note_title",
        "_salvage_text_toolcall", "_ollama_json", "_AURORA_SAVE_UNKNOWN_PROMPT",
        "_AU_LAST_ROUTE", "_trace_payload", "_decision_record"]

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

# 是正②(cmd_494 6便・将軍指示): WANT取りこぼしでpicked内の関数が未抽出の呼び出し先シンボルを参照していると、
# execはこの時点では黒く、実行して初めてNameErrorで死ぬ(=190件が黙って消える)。本ゲートが実際に呼ぶ
# エントリ関数(ENTRYPOINTS)から辿れる呼び出し木がWANTで閉じているかを静的検査し、取りこぼしがあれば
# ここで明示的に赤で落とす(黒い例外に化けさせぬ)。picked内の未呼出関数(将来コード等)の内部欠落は対象外
# ——本ゲートが実際に踏む経路のみを保証する(踏まぬ経路の欠落まで赤にすると無関係な既存差分で誤爆する)。
ENTRYPOINTS = ["_wants_aurora_save", "_guard_completion_claims", "_wants_aurora_save_llm"]
# _ollama_json は本ゲートが M["_ollama_json"]=lambda... で常時差替える契約上のstub境界(ファイル冒頭の
# 掟通り)。実体側(A/OLLAMA参照)は本番実行時にのみ使われ本ゲートでは踏まぬので、木の下降はここで止める。
_STUB_BOUNDARY = {"_ollama_json"}
_fn_by_name = {getattr(n, "name", None): n for n in picked if isinstance(n, ast.FunctionDef)}


def _called_names(fn_node):
    _local_names = {a.arg for a in fn_node.args.args}
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Assign):
            _local_names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            _local_names.add(n.id)
    out = set()
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in _local_names:
            out.add(n.id)
    return out


_BUILTIN_NAMES = set(vars(__builtins__)) if isinstance(__builtins__, type(sys)) else set(__builtins__)
_PROVIDED = seen | _BUILTIN_NAMES | {"re", "os", "json", "urllib"}
_visited, _unresolved = set(), {}
_queue = list(ENTRYPOINTS)
while _queue:
    fname = _queue.pop()
    if fname in _visited or fname not in _fn_by_name:
        continue
    _visited.add(fname)
    if fname in _STUB_BOUNDARY:
        continue
    for used in _called_names(_fn_by_name[fname]):
        if used in _fn_by_name and used not in _visited:
            _queue.append(used)
        elif used not in _PROVIDED:
            _unresolved.setdefault(fname, set()).add(used)
if _unresolved:
    print(f"❌ WANT取りこぼし(実行経路が未抽出シンボルを参照): {dict((k, sorted(v)) for k, v in _unresolved.items())}")
    sys.exit(1)

M = {}
exec("import re, os, json, urllib.request", M)
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_wants = M["_wants_aurora_save"]
_guard = M["_guard_completion_claims"]
_wants_llm = M["_wants_aurora_save_llm"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_is(name, got, expected_identity_fn, label):
    """bool(False)とNoneを型で区別する検査(★AC4必須・部分一致でなくisで判定)。"""
    ok = expected_identity_fn(got)
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={label}"))


# ══════════════════════════════════════════════════════════════════════════
# cmd_486(4-2) stub注入: M["_ollama_json"] を差替え、_wants_aurora_save_llmの下層のみ
# モックすることでネットワーク非依存のまま3系統(save=true/save=false/例外=判定不能)を検査する。
# _wants_aurora_save_llm自体(JSON解析・None返却)は差し替えず検査対象に含めたまま。
# ══════════════════════════════════════════════════════════════════════════
_KIYOTOMO4 = ["Auroraにまとめてくれた？", "Auroraに「status内容 0729」をまとめて",
              "Auroraにまとめて", "Auroraにまとめてさせて下さい"]
_UNKNOWN_WORDS3 = ["Auroraに書き起こして", "Auroraに記録しておいて", "Auroraへ整理しておいて"]

# ── ① stub save=true下: kiyotomo4発話+未知語3件 → _wants is True(AC2の本体) ──────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{"save": true}'
for q in _KIYOTOMO4 + _UNKNOWN_WORDS3:
    chk_is(f"AC2 stub save=true下でTrue: {q}", _wants(q), lambda v: v is True, "True(is)")
chk_is("AC2 _wants_aurora_save_llm単体もTrue(stub save=true)", _wants_llm("Auroraにまとめて"), lambda v: v is True, "True(is)")

# ── ② stub save=false下: 同じ7件 → _wants is False(分類器を信じる側の検査) ──────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{"save": false}'
for q in _KIYOTOMO4 + _UNKNOWN_WORDS3:
    chk_is(f"AC1(層2) stub save=false下でFalse: {q}", _wants(q), lambda v: v is False, "False(is)")

# ── ③ stub 例外下: 同じ7件 → _wants is None(★unknownの検査。Falseでなくisで区別) ────────
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
for q in _KIYOTOMO4 + _UNKNOWN_WORDS3:
    chk_is(f"AC1/AC4 stub 例外(TimeoutError)下でNone(判定不能): {q}", _wants(q), lambda v: v is None, "None(is・Falseは不合格)")
chk_is("AC4 _wants_aurora_save_llm単体もNone(stub例外)", _wants_llm("Auroraにまとめて"), lambda v: v is None, "None(is)")

# ── ③' 不正JSON・bool以外のsave値 → いずれもNone(AC4) ──────────────────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{save:}'                 # 不正JSON
chk_is("AC4 不正JSON応答でNone", _wants_llm("Auroraにまとめて"), lambda v: v is None, "None(is)")
M["_ollama_json"] = lambda system, user, num_predict=60: '{"save": "yes"}'         # bool以外の値
chk_is("AC4 saveがbool以外の値でNone", _wants_llm("Auroraにまとめて"), lambda v: v is None, "None(is)")
M["_ollama_json"] = lambda system, user, num_predict=60: '{"other": true}'         # keyの欠落
chk_is("AC4 saveキー欠落でNone", _wants_llm("Auroraにまとめて"), lambda v: v is None, "None(is)")

# ── ④ 呼出側の聞き返し生成を関数単体で検査(choicesが生成されること) ────────────────
_unknown_choices = M["_aurora_save_unknown_choices"]()
chk("AC1 unknown聞き返しchoicesが生成される(prompt在り)", bool(_unknown_choices.get("prompt")), True)
chk("AC1 unknown聞き返しchoicesのoptionsが2件", len(_unknown_choices.get("options", [])), 2)
_immediate_say = _unknown_choices["options"][0]["say"]
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())          # 分類器が死んだままの状態を維持
chk_is(f"AC1 聞き返しのsay再投入は即断路でTrue(2クリック実証): {_immediate_say}",
       _wants(_immediate_say), lambda v: v is True, "True(is・即断路は分類器非依存)")

# 環境を素の例外stubに戻す(以降の全ケースは意味判定に落ちてもNoneに倒れ、規則ベースの検査に影響せぬ)
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())

# ── AC1本体: kiyotomo殿 実発話4件がstub例外下でNone(未knownの検査そのもの) ──────────
for q in _KIYOTOMO4:
    chk_is(f"AC1 分類器例外時に無言で落ちぬ(None・聞き返しへ): {q}", _wants(q), lambda v: v is None, "None(is)")

# ── 【層1】AC4-② 明示的保存動詞+依頼形(即断路・規則ベースで決定的に陽性であるべき) ──────
for q in ["この表をAurora資料にしてアップして", "status内容 0729をAuroraにアップして",
          "Auroraに保存して", "オーロラに登録しといて"]:
    chk(f"AC4-2 即断路で保存意図を検知(規則ベース): {q}", _wants(q), True)

# ── 【層1】AC2/AC4-③ 陰性 — 読取依頼で保存カードが立たぬこと(順序=読取が先) ─────────
for q in ["Auroraの資料を読んで説明して", "Auroraの資料を検索して",
          "Auroraで前に作った資料を見せて", "Auroraに何がある？", f"{_PJ}のタスクは？"]:
    chk(f"AC2 読取は保存意図でないと判る: {q}", _wants(q), False)

# ── 【層1】AC5 未知の新語 — stub例外下でNone(未知語もkiyotomo4と同様fail-closedに聞き返す) ──
for q in _UNKNOWN_WORDS3:
    chk_is(f"AC5 未知語はstub例外下でNone: {q}", _wants(q), lambda v: v is None, "None(is)")

# ── 【層1】灰色分岐が実在すること(読取語+保存示唆語の共起はLLMへ委ねる設計の確認) ──
_gray_q = "Auroraの資料を読んでまとめて"
chk_is("灰色入力はstub例外下でNone(判定不能を正しく伝播)", _wants(_gray_q), lambda v: v is None, "None(is)")

# ── ★AC2完了条件: gate内に「期待値False」の検査が1つも残っていないことをgrepで確認 ──
# (この行自体が誤って検索対象文字列を含まぬよう、部分文字列を連結して構成する)
_gate_text = open(os.path.abspath(__file__), encoding="utf-8").read()
_removed_marker = "意味判定経路" + "はfail-closed" + "(z8a非依存でFalse)"
chk("★旧「AC1意味判定経路はfail-closed・期待値False」検査は本gateから撤去済(grep自己確認)",
    _gate_text.count(_removed_marker), 0)

# ── 【層2】AC3/AC4-④ 異常系 = カード無しで完了を名乗る → 正直な注記へ落ちること ──────
# 汎用動詞(作成/資料化/表示)はAurora語がその行に無いと発火せぬ(欠陥3是正)ため、ここでは
# Aurora語を伴う形で試験する(将軍実測はAurora語なしの表作成が誤爆した事例=別セクションで検証)。
_NOTE = "Auroraへの保存はまだ実行しておりませぬ"
for text in ["Auroraに登録しました。", "Auroraに保存しました。", "aurora_create を呼び出しました。",
             "Aurora資料を作成しました。", "Auroraに起票いたしました。", "Auroraの資料化が完了しました。",
             "Auroraに表示しました。"]:
    got = _guard(text, [])
    chk(f"AC3 カード無き完了表明を打ち消す: {text}", _NOTE in got and text.strip("。") not in got, True)

# ── 【層2】逆方向の異常系 — 過剰打ち消しの禁止 ────────────────────────────────
chk("pending_actions有りなら打ち消さぬ",
    _guard("Auroraに保存しました。", [{"id": "x", "tool": "aurora_create", "args": {}}]),
    "Auroraに保存しました。")
chk("読取の完了表明は打ち消さぬ(アクション完了表明でない)",
    _guard("Auroraの資料を読みました。", []), "Auroraの資料を読みました。")

# ── 【層2】既存回帰(DM経路)の温存 — 動詞拡張がDM側の挙動を壊さぬこと ───────────
chk("既存回帰: 送信しました は打ち消される(カード無し)",
    "はまだ実行しておりませぬ" in _guard("先方に送信しました。", []) or
    "お申し付け" in _guard("先方に送信しました。", []), True)
chk("既存回帰: カードありのDMは打ち消さぬ",
    _guard("先方に送信しました。", [{"id": "x", "tool": "send_message", "args": {}}]),
    "先方に送信しました。")

# ══════════════════════════════════════════════════════════════════════════
# AC6(cmd_485差戻是正・境界テスト) — 将軍実測の欠陥3件を二度と再現せぬための番犬。
# 正常系だけの緑は不可(掟)。以下は将軍が実際に本番投入した入力そのもの。
# ══════════════════════════════════════════════════════════════════════════

# ── ① 欠陥1是正の境界: 長文読取の陰性(将軍実測3件・距離窓を越えていた実例) ──────
# 「Auroraの資料を、来週の会議で使うので印刷しやすい形で見せてくれ」は(cmd_486是正・欠陥2で
# 読取語も節単位化した結果)読取語「見せ」がAurora語と別節(読点で分割)に落ちるため、即断路では
# 陰性確定せず意味判定(K)へ回る。stub例外下ではNone(判定不能)が正しい——Falseへの固定は
# 「読取語が同一節に無ければ意味判定に委ねる」という(cmd_486是正)の構造そのものと矛盾するため、
# ここはFalse/None両方を許容する(Trueは不可=保存意図と誤認する退行のみを禁ずる)。
for q in ["Auroraの中の技術資料でRTAB-Mapに触れているものを検索して",
          "Auroraにある資料のうち一番新しいものを教えて"]:
    chk(f"AC6-① 長文読取は陰性(将軍実測): {q}", _wants(q), False)
_q_ac6_gray = "Auroraの資料を、来週の会議で使うので印刷しやすい形で見せてくれ"
chk_is(f"AC6-① 長文読取(読取語が別節)は保存意図と誤認せぬ(False/None・Trueは不可): {_q_ac6_gray}",
       _wants(_q_ac6_gray), lambda v: v is not True, "False or None(Trueは不可)")

# ── ② 欠陥2是正の境界: 注記の主語が事実と異なって付かぬこと(完全一致・部分一致は禁の反省) ──
for text in ["先方にDMを送信しました。", "殿へ報告しました。", "Discordに投稿しました。"]:
    got = _guard(text, [])
    # 部分一致(text.strip("。") not in got 等)では欠陥2の穴を見逃した反省を踏まえ、
    # ここは「Auroraという語そのものが出力に一切含まれない」という的確な条件で厳密検査する。
    chk(f"AC6-② Aurora注記の誤帰属なし(将軍実測): {text}", "Aurora" not in got and "aurora" not in got.lower(), True)

# ── ③ 欠陥3是正の境界: Aurora無関係の作成/表示が全文保持されること(過剰打ち消し禁止) ──
_q3a = "ご要望の表を作成しました。以下の通りです。\n| A | B |\n| 1 | 2 |"
_q3b = "比較表を下に表示しました。"
chk("AC6-③ Aurora無関係の表作成は全文保持(将軍実測)", _guard(_q3a, []), _q3a)
chk("AC6-③ Aurora無関係の表示は全文保持(将軍実測)", _guard(_q3b, []), _q3b)

# ══════════════════════════════════════════════════════════════════════════
# AC7(cmd_485_impl3是正・境界テスト) — 軍師コード実査で特定した真因(即断路が読取判定を
# 追い越す評価順序)の番犬。過去形・受身形で「既に保存済みの物」を指す読取依頼が、
# 即断路(_AURORA_SAVE_REQ_RE)の語彙に一致するせいで保存依頼と誤判定されていた実害。
# ══════════════════════════════════════════════════════════════════════════

# ── ① 誤起票が実測された4件(是正後は全て陰性化すること) ──────────────────────
for q in ["Auroraへ保存した資料を見せて",
          "Auroraにアップ済みの資料を検索して",
          "Auroraに登録されている資料の一覧を出して",
          "Auroraへ以前アップした議事録の中から、納期に関する記述だけを拾って説明してくれ"]:
    chk(f"AC7-① 即断路の完了・受身形は保存依頼でない(実害・台帳2026-07-30T18:40:09): {q}", _wants(q), False)

# ── ② 陽性が壊れていないこと(退行検査・即断路=規則ベースで決定的なもののみ) ────────
for q in ["Auroraにアップして", "この表をAurora資料にしてアップして", "Auroraに保存して"]:
    chk(f"AC7-② 明示的保存依頼は引き続き陽性(退行検査・即断路): {q}", _wants(q), True)
# 「書きとどめて」は明示的保存動詞列挙(_AURORA_SAVE_REQ_RE)の対象外語彙ゆえ意味判定(K)経路。
# stub例外下でNone(判定不能・cmd_486三値化)のみ確認。True側の正答は実プロセス検証で確認する。
chk_is("AC7-② 未知の保存語は意味判定経路・stub例外下でNone: Auroraに書きとどめて",
       _wants("Auroraに書きとどめて"), lambda v: v is None, "None(is)")

# ══════════════════════════════════════════════════════════════════════════
# AC8(cmd_485_impl4是正・境界テスト) — 残穴A(即断路が読取語・時制語なしの既存物言及を
# すり抜ける)・残穴B((1-B)依頼形が既存物の再提示要求を保存依頼と誤認)の番犬。
# 個別文面でなく"型"で組む(将軍指摘: 前回・前々回とも文面の複写に留まった反省)。
# ══════════════════════════════════════════════════════════════════════════

# ── ①〜④ 残穴A・B の型別境界(将軍実測・全て陰性であること) ─────────────────
for label, q in [
    ("①過去形+体言止め", "前回Auroraに上げたやつ、もう一度出して"),
    ("②推量形", "Auroraにアップしておいたはずだが確認して"),
    ("③受身+伝聞", "Auroraに保存されたと思うけど確認して"),
    ("④受益+完了(残穴B)", "Auroraへまとめてもらったやつ、もう一度出して"),
]:
    chk(f"AC8-{label} 既存物言及・再提示要求は保存依頼でない(実害・台帳2026-07-30): {q}", _wants(q), False)

# ── ⑤ 退行検査(即断路=規則ベースで決定的な明示的保存動詞のみ・陽性維持必須) ──────
for q in ["Auroraに保存して", "この表をAurora資料にしてアップして", "オーロラに登録しといて"]:
    chk(f"AC8-⑤退行 陽性は引き続き陽性(明示保存・即断路): {q}", _wants(q), True)
# kiyotomo4発話・未知語(書き起こして/整理しておいて/記録しておいて)は意味判定(K)経路。
# stub例外下でNone(判定不能・cmd_486三値化)のみ確認。True側の退行なきことは実プロセス検証で確認する。
for q in ["Auroraにまとめてくれた？", "Auroraに「status内容 0729」をまとめて",
          "Auroraにまとめて", "Auroraにまとめてさせて下さい",
          "Auroraに書き起こして", "Auroraへ整理しておいて", "Auroraに記録しておいて"]:
    chk_is(f"AC8-⑤退行 意味判定経路はstub例外下でNone: {q}", _wants(q), lambda v: v is None, "None(is)")

# ══════════════════════════════════════════════════════════════════════════
# AC9(cmd_485_impl5是正・境界テスト) — 残穴C(編集依頼誤認)・残穴D(語中一致の罠)・
# 退行(丁寧形の陰性化)の番犬。個別文面でなく"型"で組む(軍師指摘: 型抽出が時制・完了に偏っていた)。
# ══════════════════════════════════════════════════════════════════════════

# ── ⑥ 編集・加工依頼(既存物が目的語・陰性であるべき・残穴C) ─────────────────
for q in ["Auroraに載せた資料の体裁を整えて",
          "Auroraへ記録した議事録の日付を修正して",
          "Auroraにまとめてあった内容を要約して"]:
    chk(f"AC9-⑥ 編集・加工依頼は保存依頼でない(実害・台帳2026-07-30T19:04:42): {q}", _wants(q), False)

# ── ⑦ 存在言及+一括読取(陰性であるべき・残穴D) ────────────────────────────
chk("AC9-⑦ 存在言及+一括読取は保存依頼でない(実害): Auroraにアップされてるやつ全部リストアップして",
    _wants("Auroraにアップされてるやつ全部リストアップして"), False)

# ── ⑧ 語中一致の罠(陰性であるべき・回帰の番犬) ────────────────────────────
for q in ["Auroraでリストアップして", "Auroraでピックアップして", "Auroraでクローズアップして"]:
    chk(f"AC9-⑧ 語中一致の罠は保存動詞として拾わぬ(回帰番犬): {q}", _wants(q), False)

# ── ⑨ 丁寧形の陽性維持(退行検査・必須) ─────────────────────────────────
for q in ["Auroraへ保存をお願い", "Auroraに保存頼む",
          "Auroraへの登録をお願いしたい", "Auroraにアップをお願いします",
          "Auroraへアップを頼みたい"]:                       # (M・impl6是正) 頼みたい(連用形+たい)
    chk(f"AC9-⑨ 丁寧形(助詞介在)は引き続き陽性(退行検査): {q}", _wants(q), True)

# ══════════════════════════════════════════════════════════════════════════
# AC10(cmd_485_impl6・軍師推奨の生成的組合せテスト) — 個別文面の暗記でなく機械的組合せで
# 「人が思いつかなかった組合せ」も試験に入れる(軍師 subtask_485_qc5 advisory)。
# LLM意味判定はネットワーク依存のためgate外(実プロセス検証)で確認する設計上、
# ここは②編集/読取動詞×依頼形(陰性期待・即断路に掛からず規則側で決定的に陰性となる組合せ)と
# ③"アップ"語中一致の複合語(陰性期待)を機械的に総当たりする。
# ══════════════════════════════════════════════════════════════════════════

# ── ② 編集/読取動詞リスト×依頼形リストの組合せ(陰性期待) ──────────────────
_EDIT_READ_VERBS = ["整え", "要約", "修正", "直し", "削除", "一覧化", "翻訳", "変換", "確認", "見せ", "探し"]
_DELEGATE_TAILS = ["てくれ", "てください", "ておいて", "お願い"]
for v in _EDIT_READ_VERBS:
    for t in _DELEGATE_TAILS:
        q = f"Auroraの資料を{v}{t}"
        chk(f"AC10-② 編集/読取動詞×依頼形は陰性(生成的組合せ): {q}", _wants(q), False)

# ── ③ "アップ"を語中に含むカタカナ複合語の組合せ(語境界の罠) ────────────────
# 「リストアップ/ピックアップ/クローズアップ」は _AURORA_EDIT_READ_VERB_RE の列挙語彙ゆえ
# 規則側で決定的にFalse。「バージョンアップ/レベルアップ」は列挙語彙に無く即断路の語境界否定
# 先読みにも掛からぬため意味判定(K)経路へ回る——stub例外下ではNone(cmd_486三値化により
# Falseでなく判定不能が正しく伝播する。旧ゲートはLLM例外がFalseに倒れていた前提で全てFalseと
# 書いていたが、これは(A)是正の意図(判定不能をFalseへ潰さない)そのものと矛盾するため更新した)。
_KATAKANA_TRAP_RULE_WORDS = ["リストアップ", "ピックアップ", "クローズアップ"]
_KATAKANA_TRAP_LLM_WORDS = ["バージョンアップ", "レベルアップ"]
for w in _KATAKANA_TRAP_RULE_WORDS:
    for t in _DELEGATE_TAILS:
        q = f"Auroraで{w}{t}"
        chk(f"AC10-③ 語中一致複合語×依頼形は規則側で陰性(生成的組合せ): {q}", _wants(q), False)
for w in _KATAKANA_TRAP_LLM_WORDS:
    for t in _DELEGATE_TAILS:
        q = f"Auroraで{w}{t}"
        chk_is(f"AC10-③ 未列挙複合語×依頼形は意味判定経路・stub例外下でNone: {q}",
               _wants(q), lambda v: v is None, "None(is)")

# ══════════════════════════════════════════════════════════════════════════
# AC11(cmd_486・4-3 異常系) — (A)unknown経路・(D)節跨ぎを異常系として追加。
# 正常系のみの緑は不可(掟)。stub状態はここまでの最終状態(例外stub)を引き継ぐ。
# ══════════════════════════════════════════════════════════════════════════

# ── (D) 節跨ぎ是正: Aurora語と無関係な節の編集/読取語に殺されぬこと ────────────
chk_is("AC11-(D) 節跨ぎ: 「Auroraにまとめて、あとで確認する」はFalseでなくNoneまたはTrue",
       _wants("Auroraにまとめて、あとで確認する"), lambda v: v is not False, "True or None(Falseは不可)")
chk("AC11-(D)退行: 「Auroraに載せた資料の体裁を整えて」は同一節ゆえ引き続きFalse",
    _wants("Auroraに載せた資料の体裁を整えて"), False)
chk_is(f"AC11-(D) 節跨ぎ2件目: 「{_PJ}の進捗を確認した上で、Auroraにまとめて」はFalse不可",
       _wants(f"{_PJ}の進捗を確認した上で、Auroraにまとめて"), lambda v: v is not False, "True or None(Falseは不可)")

# ── (A) unknown経路: stub例外下でNone(既にAC1/AC4/AC5/AC8で確認済・ここでは型検査を再強調) ──
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_r = _wants("Auroraにまとめてさせて下さい")
chk("AC11-(A) unknownはbool(False)ではない(型で区別)", isinstance(_r, bool), False)
chk_is("AC11-(A) unknownはNoneそのもの", _r, lambda v: v is None, "None(is)")

# ══════════════════════════════════════════════════════════════════════════
# AC12(cmd_486_impl2是正・将軍検品NG是正) — 配線検査(欠陥1)。
# 「部品は試すが配線は試さない」の反省: gateがこれまで検査していたのは
#  ① _wants_aurora_save がNoneを返すこと ② _aurora_save_unknown_choices()がchoicesを生むこと
# の2つを個別に検めるのみで、③その二つが呼出側(_salvage_text_toolcall)で実際に結線されている
# ことは検めていなかった。将軍が chat_server.py の写しへ
#   `if _au_unknown and not choices_obj: return final, _aurora_save_unknown_choices()`
# を丸ごと削除する突然変異を加えても、①②の単体検査は緑のままだった(将軍実測・差し戻し)。
# 本セクションは _salvage_text_toolcall という統合された経路そのものを通し、分類器が
# 判定不能に倒れた時に実際に choices が返ることを検査する(単体関数呼出ではない)。
# ══════════════════════════════════════════════════════════════════════════
_salvage = M["_salvage_text_toolcall"]

# ── ① 分類器例外(=判定不能)下で、統合経路から聞き返しchoicesが実際に返ること ──────
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_final, _choices = _salvage("何かしらの応答文（承認ボタン等の語彙表には一致せぬ）",
                             {"uid": "u1"}, [], query="Auroraにまとめてさせて下さい")
chk("AC12-① 配線: 分類器例外下で_salvage_text_toolcallがchoicesを返す(prompt在り)",
    bool(_choices) and bool(_choices.get("prompt")), True)
chk("AC12-① 配線: choicesのoptionsが2件(unknown聞き返しカードそのもの)",
    len((_choices or {}).get("options", [])), 2)
chk("AC12-① 配線: choicesの中身がunknown経路のprompt文言と一致",
    (_choices or {}).get("prompt"), M["_AURORA_SAVE_UNKNOWN_PROMPT"])

# ── ② choices_obj が既に埋まっている場合は本カードを重複させぬこと(呼出側責務の温存確認) ──
_final2, _choices2 = _salvage("何かしらの応答文", {"uid": "u1"}, [],
                               query="Auroraにまとめてさせて下さい", choices_obj={"prompt": "既存の別choices"})
chk("AC12-② choices_obj既存時は重複生成せぬ(None)", _choices2, None)

# ── ③ pending_actions既存時は本経路自体が不要(Noneであること・既存仕様の温存確認) ──
_final3, _choices3 = _salvage("何かしらの応答文", {"uid": "u1"},
                               [{"id": "x", "tool": "send_message", "args": {}}],
                               query="Auroraにまとめてさせて下さい")
chk("AC12-③ pending_actions既存時はchoices不要(None)", _choices3, None)

# ══════════════════════════════════════════════════════════════════════════
# AC13(cmd_487追加AC・trace観測配線検査) — casper_trace.emitへ層1の判定値/決着経路を
# 記録する_AU_LAST_ROUTEが、_salvage_text_toolcallの統合経路を通して正しく更新されることを
# 検査する(AC12と同じ「部品でなく配線」の掟)。5経路すべてを網羅する。
# ══════════════════════════════════════════════════════════════════════════
_AU_ROUTE = M["_AU_LAST_ROUTE"]

# ── ① null: Aurora語なしのturn → decision=None, route=None ──────────────────
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_salvage("何かしらの応答文", {"uid": "u1"}, [], query=f"{_PJ}のタスクは？")
chk("AC13-① null: Aurora無関係turnでau_decision=None", _AU_ROUTE.get("decision"), None)
chk("AC13-① null: Aurora無関係turnでau_route=None", _AU_ROUTE.get("route"), None)

# ── ② immediate: 明示的保存動詞+依頼形の即断路 → decision=true, route=immediate ─────
_salvage("何かしらの応答文", {"uid": "u1"}, [], query="Auroraに保存して")
chk("AC13-② immediate: au_decision=true", _AU_ROUTE.get("decision"), "true")
chk("AC13-② immediate: au_route=immediate", _AU_ROUTE.get("route"), "immediate")

# ── ③ llm(true): 分類器がsave=trueを返す灰色経路 → decision=true, route=llm ────────
M["_ollama_json"] = lambda system, user, num_predict=60: '{"save": true}'
_salvage("何かしらの応答文", {"uid": "u1"}, [], query="Auroraにまとめて")
chk("AC13-③ llm(true): au_decision=true", _AU_ROUTE.get("decision"), "true")
chk("AC13-③ llm(true): au_route=llm", _AU_ROUTE.get("route"), "llm")

# ── ④ rule_negative: 規則で陰性確定(読取依頼) → decision=false, route=rule_negative ──
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_salvage("何かしらの応答文", {"uid": "u1"}, [], query="Auroraの資料を読んで説明して")
chk("AC13-④ rule_negative: au_decision=false", _AU_ROUTE.get("decision"), "false")
chk("AC13-④ rule_negative: au_route=rule_negative", _AU_ROUTE.get("route"), "rule_negative")

# ── ⑤ unknown_askback: 分類器例外stub下の灰色入力 → decision=unknown, route=unknown_askback ──
M["_ollama_json"] = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
_salvage("何かしらの応答文（承認ボタン等の語彙表には一致せぬ）", {"uid": "u1"}, [], query="Auroraにまとめて")
chk("AC13-⑤ unknown_askback: au_decision=unknown", _AU_ROUTE.get("decision"), "unknown")
chk("AC13-⑤ unknown_askback: au_route=unknown_askback", _AU_ROUTE.get("route"), "unknown_askback")

# ── ⑥ pending_actions既存時は判定自体が走らずnull/nullへ戻ること(早期returnの確認) ──
M["_ollama_json"] = lambda system, user, num_predict=60: '{"save": true}'
_salvage("何かしらの応答文", {"uid": "u1"}, [{"id": "x", "tool": "send_message", "args": {}}], query="Auroraに保存して")
chk("AC13-⑥ pending_actions既存時はau_decision=None(判定スキップ)", _AU_ROUTE.get("decision"), None)
chk("AC13-⑥ pending_actions既存時はau_route=None(判定スキップ)", _AU_ROUTE.get("route"), None)

# ══════════════════════════════════════════════════════════════════════════
# AC14(cmd_487是正・欠陥1: 配線検査) — casper_trace.emitへ実際に渡るpayloadに
# au_decision/au_routeの2キーが載ることを検査する(「部品は試すが配線は試さない」の再発防止)。
# AC13は_AU_LAST_ROUTEという部品が正しく更新されることは守るが、その値がemit呼出側の
# payload組立(元は8552行付近のインライン辞書リテラル)へ実際に渡されることまでは守っていなかった
# ——将軍実測: その2行を丸ごと削除する突然変異を加えても186/186緑のままだった実害。
# 是正: payload組立を _trace_payload(...) という単一の小関数へ切り出し、ここでその関数を
# 直接呼び出して返り値dictにau_decision/au_routeキーが存在することを検査する。
# これで「_trace_payload内のau_decision/au_route行が消える」突然変異は、この関数を呼んで
# キーの有無を見るだけで機構的に赤化する(emitを実行するcasper_trace自体は呼ばず、
# ネットワーク非依存を保つ)。
# ══════════════════════════════════════════════════════════════════════════
_trace_payload = M["_trace_payload"]
_AU_ROUTE["route"] = "immediate"
_AU_ROUTE["decision"] = "true"
_payload = _trace_payload(
    trace_id="t1", query="q", actor="u1", thread="th1", routed={"tool": "x"},
    fastpath=None, echoed=False, vch=False,
    injected_facts={}, resp_ids={}, cont=0,
    gate=None, pj={"status": "unique", "n": 1, "path": "x"},
    rag_hits=0, ctx_len=0,
    gen_sec=0.1, salvaged=False, validated=False, gloss=False,
    guarded_claim=False, abstained=False,
    digests_fired=None, final_len=10, cards=0, fewshot_used=[])
chk("AC14 配線: _trace_payload返り値にau_decisionキーが存在", "au_decision" in _payload, True)
chk("AC14 配線: _trace_payload返り値にau_routeキーが存在", "au_route" in _payload, True)
chk("AC14 配線: au_decisionの値が_AU_LAST_ROUTEと一致(実接続の確認)", _payload.get("au_decision"), "true")
chk("AC14 配線: au_routeの値が_AU_LAST_ROUTEと一致(実接続の確認)", _payload.get("au_route"), "immediate")

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
