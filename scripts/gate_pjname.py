#!/usr/bin/env python3
"""PJ名の同定=接地の回帰ゲート（純機構・インメモリ・読取のみ）。全PASSで exit 0。

守る掟:
 ① 名の一致は「実体として現れたか」であり、部分文字列ではない。ASCII短名は語境界を要求する。
    実害(殿ログ 2026-07-27 16:37): Calendar に実在するPJ 'end'(id77) が `'end' in 'Calendar'` で
    ヒットし、問われてもおらぬPJの件数で回答が丸ごと摩り替わった(「**end** には Calendar上 1件」)。
 ② 錨は「問い」であり「生成文」ではない。生成文から実体を逆引きするのは最後の手段(retrieve-then-render)。
 ③ 語彙の境界を踏む: 短名(V/GS)・埋没(Calendar/latest)・カナ⇄ローマ字・日本語長名・人名問い。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該関数のみを抜いて検査する
(名前が変わった/消えたらゲートが落ちる=機構の在処も同時に守る)。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
WANT = ["_KANA2ROMA", "_KANA_SMALL", "_kana_to_romaji", "_translit_kana_runs",
        "_canonical", "_PJ_ALIAS", "_pj_index", "_pj_name_hit", "_pj_resolve",
        "_PERSON_WORK_RE", "_PERSON_COLS", "_NAME_STOP", "_name_tokens", "_PJ_TASK_RE",
        "_NEG_EXIST_RE", "_NEG_SCOPE_RE",
        "_STATUS_VOCAB_RE", "_DECLARATIVE_RE", "_AURORA_URL_RE", "_looks_declarative"]

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
_hit, _resolve = M["_pj_name_hit"], M["_pj_resolve"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


# ── ① 実害の再現: ASCII短名が別語に埋没しても拾わぬ ──────────────────
chk("'end' は 'Calendar' に埋没しても拾わぬ(実害の再現)",
    _hit("end", "Calendar 上には登録されておりません。"), False)
chk("'end' が実体として現れれば拾う", _hit("end", "end のタスクは3件"), True)
chk("'end' 行頭でも拾う", _hit("end", "end には未完了が残っております"), True)
chk("'test' は 'latest' に埋没せぬ", _hit("test", "the latest build"), False)
chk("'RND' は 'RNDX' に埋没せぬ", _hit("RND", "RNDX という別物"), False)
chk("'RND' 単体は拾う", _hit("RND", "RND の進捗は？"), True)

# ── ② 2字以下(V/GS)は本文照合の材料にせぬ ────────────────────────────
chk("'V' は材料にせぬ", _hit("V", "コンバトラーV の件"), False)
chk("'GS' は材料にせぬ", _hit("GS", "GS/NINA連携について"), False)

# ── ③ 日本語名は境界概念が無いゆえ素の包含で可 ───────────────────────
chk("日本語長名は素通り", _hit("ドローン R&D  GS/NINA連携", "ドローン R&D  GS/NINA連携 は進行中"), True)
chk("日本語名: 無関係文では拾わぬ", _hit("富士急ハイランド3", "Calendar 上には登録されておりません。"), False)

# ── ④ 空・None の異常系(判定不能を True に化けさせぬ) ────────────────
chk("name が空", _hit("", "なんでも"), False)
chk("text が空", _hit("end", ""), False)
chk("name が None", _hit(None, "end"), False)

# ── ⑤ resolver 3値: 埋没誤爆せず、正当な解決は保つ ───────────────────
# ※ /tmp/cal_projects.json が無い環境では索引が空=全て none。その時は⑤を飛ばす(嘘の緑を出さぬ)。
if os.path.exists("/tmp/cal_projects.json") and M["_pj_index"]()["idx"]:
    st, n, _ = _resolve("Calendarのタスクを見せて")
    chk("'Calendar' を含む問いが PJ 'end' に化けぬ", "end" in n, False)
    chk("marukome 生一致", _resolve("marukomeのタスク")[:2], ("unique", ["marukome"]))
    chk("マルコメ→marukome スケルトン一致", _resolve("マルコメのタスク")[:2], ("unique", ["marukome"]))
    chk("Zenith 生一致", _resolve("Zenithの状況")[:2], ("unique", ["Zenith"]))
    chk("空白入り名 'Score 検証'", _resolve("Score 検証のタスク")[:2], ("unique", ["Score 検証"]))
    chk("人名の問いは PJ に化けぬ", _resolve("Timは今なにしてるの？")[0], "none")
    chk("名の無い一般の問いは none", _resolve("今日の締切は？")[0], "none")
else:
    print("⏭  /tmp/cal_projects.json 不在ゆえ resolver 実データ検査は省略（SKIP=未検証・緑と数えぬ）")

# ── ⑥ 人物ファセットの分岐(『Timは今なにしてる？』が無言で落ちぬこと) ──
#    実害(殿ログ 16:33): roster に tim=uid42 が在るのに経路が無く「うまくお答えできませなんだ」。
_PW = M["_PERSON_WORK_RE"]
for q in ["Timは今なにしてるの？", "koheiの担当タスクは？", "ouは忙しい？", "terajimaの手持ちは",
          "鈴木のスケジュール", "tetsuoは空いてる？"]:
    chk(f"人物意図を検知: {q}", bool(_PW.search(q)), True)
for q in ["今日の締切は？", "進行中のプロジェクトは？", "議事録を要約して"]:
    chk(f"人物意図でないものは無視: {q}", bool(_PW.search(q)), False)
chk("0件時と一覧時で列は同一(食い違いを断つ)", len(M["_PERSON_COLS"]), 7)
if os.path.exists("/tmp/cal_projects.json") and M["_pj_index"]()["idx"]:
    # PJ優先: 『marukomeのタスク』は人でなく PJ の表へ(分岐の前提条件そのものを検査)
    chk("PJ unique が勝つ(人物分岐に入らぬ)", _resolve("marukomeのタスクを見せて")[0], "unique")
    chk("人名のみの問いは PJ unique にならぬ", _resolve("Timは今なにしてるの？")[0] == "unique", False)

# ── ⑦ 名らしきトークンの切り出し(Calendar不在≠全体不在 の入口) ──────
#    常用語を固有名詞と誤れば「『タスク』は Calendar に存在しない」なる注記を自ら注入する。
_nt = M["_name_tokens"]
chk("固有名詞を拾う(Solafune)", _nt("Solafuneの案件の担当はだれ？タスクは何があるの？"), ["Solafune"])
chk("常用語『タスク』は名に非ず", "タスク" in _nt("ドローンの自立飛行に関してのタスクは？"), False)
chk("『ドローン』は名として拾う", "ドローン" in _nt("ドローンの自立飛行に関してのタスクは？"), True)
chk("常用語のみの問いは空", _nt("プロジェクトのステータスは？"), [])
chk("短すぎるASCIIは拾わぬ", _nt("ouは？"), [])
if os.path.exists("/tmp/cal_projects.json") and M["_pj_index"]()["idx"]:
    # PJ名の一部をなす語を「Calendar に無し」と誤らぬこと(在るものを無いと告げる注記の予防)
    _names = [nm for v in M["_pj_index"]()["idx"].values() for nm in v]
    _part = lambda tok: any(tok.lower() in nm.lower() or M["_canonical"](tok) in M["_canonical"](nm)
                            for nm in _names)
    chk("『ドローン』は PJ名の一部と判る", _part("ドローン"), True)
    chk("『Solafune』は PJ名の一部でない", _part("Solafune"), False)

# ── ⑧ タスク一覧意図の検出(機構を素通りして作文に落ちぬこと) ─────────
#    実測2026-07-27: 『〜のタスクは？』が どの分岐にも掛からず弱qwenへ流れ、3件在るPJを「0件」と作文した。
_TR = M["_PJ_TASK_RE"]
for q in ["ドローンの自立飛行に関してのタスクは？", "marukomeのタスクは？", "marukomeのタスクを見せて",
          "タスクって何があるの", "どんなタスクがある？"]:
    chk(f"一覧意図を検知: {q}", bool(_TR.search(q)), True)
for q in ["このタスクは終わった", "今日の締切は？", "議事録を要約して", "タスクを新規作成して"]:
    chk(f"一覧意図でないものは無視: {q}", bool(_TR.search(q)), False)

# ── ⑨ 存在否定の出口検問が「撃つ資格」を持つ文の選別 ──────────────────
#    限定なしの存在否定のみ撃つ。部分集合の否定(未着手0件等)は真でありうるゆえ撃たぬ
#    (実測2026-07-27: 正しい文へ「全49件ある」と的外れな訂正を付した)。
_NE, _NS = M["_NEG_EXIST_RE"], M["_NEG_SCOPE_RE"]
_bare = lambda s: bool(_NE.search(s)) and not _NS.search(s)
chk("限定なしの否定は撃つ: 登録されていません", _bare("marukome にタスクは登録されていません。"), True)
chk("限定なしの否定は撃つ: 1件も無い", _bare("このPJにはタスクが1件も無い。"), True)
chk("限定つきは撃たぬ: 未着手", _bare("未着手のタスクはありません。"), False)
chk("限定つきは撃たぬ: 未完了", _bare("未完了のタスクはございません。"), False)
chk("限定つきは撃たぬ: 本日締切", _bare("本日締切のタスクはありません。"), False)
chk("限定つきは撃たぬ: 納期超過", _bare("納期超過のタスクは存在しません。"), False)
chk("否定でない文は撃たぬ", _bare("全49件のタスクが登録されています。"), False)

# ── ⑩ 宣言/定義/引用は命令ではない ────────────────────────────────────
#    実測2026-07-27 19:05: 9値の定義表を貼られ 'DELIVER' の一語で動詞ルータが起き
#    『どのタスクを「納品」しまするか？』と問い返した(殿は7/23にも同じ誤読を指摘済)。
_LD = M["_looks_declarative"]
_DEF_TABLE = ("http://nina_notepc_02:8100/doc/casper/2026-07-24/tasuku-19 の資料を確認した。"
              "会議を行い以下に確定した\nWT オート\nMK 制作\nWIP アーティスト\nQC アーティスト\n"
              "QC_FB ディレクター\nAP ディレクター\nCLIENT_AP 制作\nDELIVER アーティスト\nOMIT 制作")
chk("定義表は宣言(命令に非ず)", _LD(_DEF_TABLE), True)
chk("status語彙3種以上=列挙と見る", _LD("WIP と QC と AP の話"), True)
chk("実際の命令は宣言でない: 納品にして", _LD("ac3102を納品済にして"), False)
chk("実際の命令は宣言でない: omit", _LD("このタスクをomitにして"), False)
chk("普通の問いは宣言でない", _LD("Timは今なにしてるの？"), False)
chk("資料URLのみ(宣言標識なし)は宣言でない", _LD("http://x/doc/a/b/c"), False)

# Aurora資料URLの検出(貼られたら機構が取りに行く前提そのもの)
_AU = M["_AURORA_URL_RE"]
chk("Aurora資料URLを拾う",
    bool(_AU.search("http://nina_notepc_02:8100/doc/casper/2026-07-24/tasuku-19　の資料を確認した")), True)
chk("URL末尾の全角空白を含めぬ",
    _AU.search("http://h:8100/doc/a/b　の資料").group(0), "http://h:8100/doc/a/b")
chk("/doc/ を持たぬURLは対象外", bool(_AU.search("https://example.com/foo/bar")), False)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
