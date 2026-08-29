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

import pack_config

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

# cmd_491 AC3: 固有名は pack から受け取る(gate_*.py を engine_scan exclude から
# 外しても pack_lint が緑のままであるため)。PJ名解決(_pj_resolve)の検査には
# 解決可能な名が要る(空語では試験の意味を成さぬ)ゆえ、examples.project_names[0] を
# 「試験用のPJ名」として使う。カナ表記は project_names_kana(同じ並びの対)から取る
# (無ければローマ字名で代用=カナ⇄ローマ字試験は素通りするが安全側)。
_examples = pack_config.get("examples", {}) or {}
_PJ = (_examples.get("project_names") or ["sample-pj"])[0]
_PJ_KANA = (dict(zip(_examples.get("project_names", []),
                      _examples.get("project_names_kana", []))).get(_PJ)) or _PJ

# cmd_494第7便 AC22/23: resolver検査(⑤⑥⑦)はCalendar実データ(/tmp/cal_projects.json)に
# 依存させぬ——PJがcompleted/offlineへ遷移する・該当PJ名が0件になる等の経時変化でゲートが
# 赤くなり、そのたび「コードは無罪」の裏取りを要していた(赤の意味が「コード退行」と「実データの
# 経時変化」の二通りに割れていた=将軍裁可済の是正)。固定の合成索引を _PJ_ALIAS に直接注入し、
# _pj_index() のファイル読取分岐(os.path.getmtime)を通らせぬことで、回帰ゲート本体を実データから
# 完全に切り離す。実データ側の確認は gate_pjname_smoke.py へ分離済(AC23)。
# cmd_508第3便(病三・AC7是正): pack由来PJ名は[0](先頭1件)だけでなく project_names 全件を
# 索引へ注入する——AC7の「閉集合全数」property testが全PJ名を対象にできるようにする為
# (是正前は_PJ=[0]のみがindexに在り、他名は解決不能で全数試験が原理的に不可能だった)。
_FIXED_PJ_INDEX_RAW = {
    # canonical skeleton は実行時に _canonical() で導出する(下のブロック参照・手計算の齟齬を避ける)。
    "Zenith": "Zenith",
    "Score 検証": "Score 検証",                      # 空白入り名(cmd_494第6便 FAIL①の再現材料)
    "ドローン R&D  GS/ZQX連携": "ドローン R&D  GS/ZQX連携",  # 部分語『ドローン』のPJ(FAIL②の再現材料)
    "end": "end",                                    # 実害再現(埋没誤爆させぬ・ASCII短名境界試験)
}
for _nm in (_examples.get("project_names") or []):
    _FIXED_PJ_INDEX_RAW[_nm] = _nm                    # pack由来の全PJ名(cmd_491 AC3・cmd_508 AC7全数)

WANT = ["_KANA2ROMA", "_KANA_SMALL", "_kana_to_romaji", "_translit_kana_runs",
        "_canonical", "_PJ_ALIAS", "_pj_index", "_pj_name_hit", "_pj_resolve",
        "_PERSON_WORK_RE", "_PERSON_COLS", "_NAME_STOP", "_name_tokens", "_PJ_TASK_RE",
        "_NEG_EXIST_RE", "_NEG_SCOPE_RE",
        "_STATUS_VOCAB_RE", "_DECLARATIVE_RE", "_AURORA_URL_RE", "_looks_declarative",
        "_ACT_NOT", "_ACTION_REQ_RE", "_DEIXIS_TABLE_RE", "deixis_table_digest",
        "_ASK_INTENT_RE", "_NOT_FILE_REF_RE", "_FILE_REF_RE", "_SELF_UIDS",
        "_deixis_table_rows", "_DM_DEIXIS_RE", "_ground_dm_body",
        "_ASK_KEEP_RE", "_AURORA_SAVE_REQ_RE"]

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

# 固定索引を _PJ_ALIAS に直接注入する。_pj_index() は /tmp/cal_projects.json の mtime を見て
# 「前回と同じmtimeならキャッシュ(_PJ_ALIAS)をそのまま返す」ので、os.path.getmtime を固定値へ
# 差し替え、_PJ_ALIAS["mtime"] を同じ固定値に揃えることで、実ファイルの中身に一切触れず
# キャッシュ分岐だけを恒久的に通らせる(ファイル自体が無い環境でも動く=CI非依存)。
_FIXED_MTIME = 0.0
M["os"].path.getmtime = lambda _p: _FIXED_MTIME
_fixed_idx = {}
for _nm in _FIXED_PJ_INDEX_RAW:
    _can = M["_canonical"](_nm)
    _fixed_idx.setdefault(_can, [])
    if _nm not in _fixed_idx[_can]:
        _fixed_idx[_can].append(_nm)
M["_PJ_ALIAS"]["mtime"] = _FIXED_MTIME
M["_PJ_ALIAS"]["idx"] = _fixed_idx

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
chk("'GS' は材料にせぬ", _hit("GS", "GS/ZQX連携について"), False)

# ── ③ 日本語名は境界概念が無いゆえ素の包含で可 ───────────────────────
chk("日本語長名は素通り", _hit("ドローン R&D  GS/ZQX連携", "ドローン R&D  GS/ZQX連携 は進行中"), True)
chk("日本語名: 無関係文では拾わぬ", _hit("富士急ハイランド3", "Calendar 上には登録されておりません。"), False)

# ── ④ 空・None の異常系(判定不能を True に化けさせぬ) ────────────────
chk("name が空", _hit("", "なんでも"), False)
chk("text が空", _hit("end", ""), False)
chk("name が None", _hit(None, "end"), False)

# ── ⑤ resolver 3値: 埋没誤爆せず、正当な解決は保つ ───────────────────
# 固定合成索引(_FIXED_PJ_INDEX_RAW)に対する検査ゆえ、Calendar実データの経時変化(PJが
# completed/offlineへ遷移する・該当PJ名が消える等)の影響を受けぬ(cmd_494第7便)。
st, n, _ = _resolve("Calendarのタスクを見せて")
chk("'Calendar' を含む問いが PJ 'end' に化けぬ", "end" in n, False)
chk(f"{_PJ} 生一致", _resolve(f"{_PJ}のタスク")[:2], ("unique", [_PJ]))
chk(f"{_PJ_KANA}→{_PJ} スケルトン一致", _resolve(f"{_PJ_KANA}のタスク")[:2], ("unique", [_PJ]))
chk("Zenith 生一致", _resolve("Zenithの状況")[:2], ("unique", ["Zenith"]))
chk("空白入り名 'Score 検証'", _resolve("Score 検証のタスク")[:2], ("unique", ["Score 検証"]))
chk("人名の問いは PJ に化けぬ", _resolve("Timは今なにしてるの？")[0], "none")
chk("名の無い一般の問いは none", _resolve("今日の締切は？")[0], "none")

# ── ⑥ 人物ファセットの分岐(『Timは今なにしてる？』が無言で落ちぬこと) ──
#    実害(殿ログ 16:33): roster に tim=uid42 が在るのに経路が無く「うまくお答えできませなんだ」。
_PW = M["_PERSON_WORK_RE"]
for q in ["Timは今なにしてるの？", "koheiの担当タスクは？", "ouは忙しい？", "terajimaの手持ちは",
          "鈴木のスケジュール", "tetsuoは空いてる？"]:
    chk(f"人物意図を検知: {q}", bool(_PW.search(q)), True)
for q in ["今日の締切は？", "進行中のプロジェクトは？", "議事録を要約して"]:
    chk(f"人物意図でないものは無視: {q}", bool(_PW.search(q)), False)
chk("0件時と一覧時で列は同一(食い違いを断つ)", len(M["_PERSON_COLS"]), 7)
# PJ優先: 『{_PJ}のタスク』は人でなく PJ の表へ(分岐の前提条件そのものを検査・固定索引を使用)
chk("PJ unique が勝つ(人物分岐に入らぬ)", _resolve(f"{_PJ}のタスクを見せて")[0], "unique")
chk("人名のみの問いは PJ unique にならぬ", _resolve("Timは今なにしてるの？")[0] == "unique", False)

# ── ⑦ 名らしきトークンの切り出し(Calendar不在≠全体不在 の入口) ──────
#    常用語を固有名詞と誤れば「『タスク』は Calendar に存在しない」なる注記を自ら注入する。
_nt = M["_name_tokens"]
chk("固有名詞を拾う(Solafune)", _nt("Solafuneの案件の担当はだれ？タスクは何があるの？"), ["Solafune"])
chk("常用語『タスク』は名に非ず", "タスク" in _nt("ドローンの自立飛行に関してのタスクは？"), False)
chk("『ドローン』は名として拾う", "ドローン" in _nt("ドローンの自立飛行に関してのタスクは？"), True)
chk("常用語のみの問いは空", _nt("プロジェクトのステータスは？"), [])
chk("短すぎるASCIIは拾わぬ", _nt("ouは？"), [])
# PJ名の一部をなす語を「Calendar に無し」と誤らぬこと(在るものを無いと告げる注記の予防・固定索引を使用)
_names = [nm for v in M["_pj_index"]()["idx"].values() for nm in v]
_part = lambda tok: any(tok.lower() in nm.lower() or M["_canonical"](tok) in M["_canonical"](nm)
                        for nm in _names)
chk("『ドローン』は PJ名の一部と判る", _part("ドローン"), True)
chk("『Solafune』は PJ名の一部でない", _part("Solafune"), False)

# ── ⑧ タスク一覧意図の検出(機構を素通りして作文に落ちぬこと) ─────────
#    実測2026-07-27: 『〜のタスクは？』が どの分岐にも掛からず弱qwenへ流れ、3件在るPJを「0件」と作文した。
_TR = M["_PJ_TASK_RE"]
for q in ["ドローンの自立飛行に関してのタスクは？", f"{_PJ}のタスクは？", f"{_PJ}のタスクを見せて",
          "タスクって何があるの", "どんなタスクがある？"]:
    chk(f"一覧意図を検知: {q}", bool(_TR.search(q)), True)
for q in ["このタスクは終わった", "今日の締切は？", "議事録を要約して", "タスクを新規作成して"]:
    chk(f"一覧意図でないものは無視: {q}", bool(_TR.search(q)), False)

# ── ⑨ 存在否定の出口検問が「撃つ資格」を持つ文の選別 ──────────────────
#    限定なしの存在否定のみ撃つ。部分集合の否定(未着手0件等)は真でありうるゆえ撃たぬ
#    (実測2026-07-27: 正しい文へ「全49件ある」と的外れな訂正を付した)。
_NE, _NS = M["_NEG_EXIST_RE"], M["_NEG_SCOPE_RE"]
_bare = lambda s: bool(_NE.search(s)) and not _NS.search(s)
chk("限定なしの否定は撃つ: 登録されていません", _bare(f"{_PJ} にタスクは登録されていません。"), True)
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

# ── ⑪ 規則の記述と実行の依頼を分ける(会話を断ち切らぬ) ────────────────
#    実測2026-07-27 19:20(殿御指摘「会話になっていない」): 『WT と OMIT は超過カウントしない。
#    AP提出後でもクライアントからのリテイクが来たらQCFBに代わる』に対し、'除外' の一語で
#    『どのタスクを「対象外」しまするか？』と返し、会話が断ち切られた。
_AR = M["_ACTION_REQ_RE"]
_RULE_MSG = ("WT (保留) と OMIT (除外) 　は超過カウントしない。"
             "ディレクターが AP提出後でもクライアントからのリテイクが来たらQCFBに代わる。")
chk("規則の記述は宣言と判る", _LD(_RULE_MSG), True)
chk("規則の記述に依頼の標識は無い", bool(_AR.search(_RULE_MSG)), False)
chk("status語彙は日本語直前でも数える(AP提出後)",
    sorted({m.group(0).lower() for m in M["_STATUS_VOCAB_RE"].finditer(_RULE_MSG)}),
    ["ap", "omit", "qcfb", "wt"])
for q in ["ac3102を納品済にして", "このタスクをomitにして", "#2869 を対象外にしといて",
          "c01を納品してくれ", "c01をdeliverに変更して"]:
    chk(f"実行の依頼と判る: {q}", bool(_AR.search(q)), True)
for q in ["Timは今なにしてるの？", "いま作業してます", "何をしていたの", "APは超過にするな",
          f"{_PJ}のタスクは？"]:
    chk(f"依頼でないと判る: {q}", bool(_AR.search(q)), False)

# ── ⑫ 『この表』の接地(直前の自分の応答を指せること) ──────────────────
#    実測2026-07-27 19:28(殿御指摘「もう少し理解がいる」): 一手前に自ら出した9ステータスの表を
#    『この表の中に権限も』と言われ『どの表を指すか明確ではありません』と問い返した。
_DTD = M["deixis_table_digest"]
_CONVO = [{"role": "user", "content": "9つのステータスを表に"},
          {"role": "assistant", "content": "まとめました。\n\n| ステータス | 意味 |\n| :-- | :-- |\n"
                                           "| WT | 保留 |\n| MK | 制作準備 |"},
          {"role": "user", "content": "この表の中に権限の表記もお願い"}]
_d = _DTD("この表の中に権限の表記もお願い", _CONVO)
chk("『この表』で直前の表を特定する", bool(_d) and "| WT | 保留 |" in _d, True)
chk("問い返しを禁ずる指示が入る", "問い返すな" in _d, True)
chk("指示語が無ければ何も注入せぬ", _DTD("9つのステータスを表にまとめて", _CONVO), "")
chk("直前に表が無ければ注入せぬ",
    _DTD("この表に権限も", [{"role": "assistant", "content": "表はまだ作っておりませぬ。"}]), "")
for q, w in [("この表の中に権限の表記もお願い", True), ("上の一覧に列を足して", True),
             ("さっきのまとめを直して", True), (f"{_PJ}のタスクは？", False),
             ("表を新しく作って", False)]:
    chk(f"指示語検出({w}): {q}", bool(M["_DEIXIS_TABLE_RE"].search(q)), w)

# ── ⑬ DM: 問いを立てる依頼を「ファイル配信」と読み違えぬ ────────────────
#    実測2026-07-27(殿御指摘「DM内容が微妙」): 『この表の中で〜どれか？をkiyotomo、Tetsuoに確認するDM』へ、
#    配信の定型『データをお送りします。ご確認ください。＋URL』を返し、問いが本文から消え宛先も一名に落ちた。
_ASK, _NFR, _FR = M["_ASK_INTENT_RE"], M["_NOT_FILE_REF_RE"], M["_FILE_REF_RE"]
_DM_ASK = "じゃあこの表の中で、casperが変更かける必要があるステータスはどれが？をkiyotomo、Tetsuoに確認するDMをお願い"
chk("尋ねる意図と判る(=配信でない)", bool(_ASK.search(_DM_ASK)), True)
chk("『この表』はファイル参照でない", bool(_NFR.search(_DM_ASK)), True)
for q in ["このファイルをtetsuoにDMして", "このリンクをkiyotomoに送っといて"]:
    chk(f"正当な配信は配信のまま: {q}",
        bool(_ASK.search(q)) or bool(_NFR.search(q)), False)
    chk(f"文脈参照は生きている: {q}", bool(_FR.search(q)), True)
chk("『この表を送って』は表参照(ファイルに非ず)", bool(_NFR.search("この表をtetsuoに送って")), True)
chk("Casper自身(uid101)は宛先候補から除く", "101" in M["_SELF_UIDS"], True)

# ── ⑭ DM本文の接地: 宛先は殿との会話を見ておらぬ ──────────────────────
#    実測2026-07-27(殿御指摘「DM内容が微妙」の芯): 『先ほど整理した表について、どれが該当しますか』を
#    kiyotomo/tetsuo へ送ろうとした。相手はその表を見ておらぬゆえ、答えようのない問いであった。
_G = M["_ground_dm_body"]
_TBL = "| ステータス | 権限 |\n| :-- | :-- |\n| WT | オート |\n| AP | ディレクター |"
_g1 = _G("先ほど整理した表について、どれが該当しますか。", _TBL)
chk("指示語のみのDMには材料を添える", "| WT | オート |" in _g1, True)
chk("添える時は見出しを付ける", "【ご確認いただきたい一覧】" in _g1, True)
chk("既に表を含む本文は触らぬ", _G("一覧です。\n| A | B |\n以上。", _TBL).count("ステータス"), 0)
chk("指示語が無ければ触らぬ",
    _G("ステータス9値のうちどれをCasperが変更すべきかご教示ください。", _TBL),
    "ステータス9値のうちどれをCasperが変更すべきかご教示ください。")
chk("添える表が無ければ触らぬ", _G("先ほどの表について", ""), "先ほどの表について")
chk("本文が空なら触らぬ", _G("", _TBL), "")
chk("直前応答から表を抽出できる",
    bool(M["_deixis_table_rows"]("この表に権限も",
         [{"role": "assistant", "content": "整理しました\n| a | b |\n| :- | :- |\n| 1 | 2 |"}])), True)
for q, w in [("先ほどの件について", True), ("この表のうちどれか", True), ("上記のとおり", True),
             ("ステータス定義の件でご確認です", False)]:
    chk(f"DM内の指示語検出({w}): {q}", bool(M["_DM_DEIXIS_RE"].search(q)), w)

# ── ⑮ アンケート🙅の二件(2026-07-28)を機構で塞ぐ ─────────────────────
#    ①「Auroraに保存するボタンがない」: 救済が応答の言い回し(承認ボタン/保存しますか…)しか見ておらず、
#      『保存してよろしいでしょうか？承認いただければ』を取り落とした。錨を殿の依頼へ移す。
_AU = M["_AURORA_SAVE_REQ_RE"]
for q in ["そしたら、この表をAurora資料にしてアップして", "Auroraに保存して", "この表をauroraにアップして",
          "Auroraノートにして", "オーロラに登録しといて"]:
    chk(f"Aurora保存の依頼と判る: {q}", bool(_AU.search(q)), True)
for q in ["Auroraの資料を読んで説明して", f"{_PJ}のタスクは？"]:
    chk(f"保存依頼でないものは無視: {q}", bool(_AU.search(q)), False)

#    ②「DMが飛んでいない」の実体: 唯一の問いの文に相手の名が入っており剥がれ、
#      表だけで問いの無いDMが tetsuo殿へ実際に送られた(state=sent)。問いの有無で守る。
_AK = M["_ASK_KEEP_RE"]
for b, w in [("どれになりますか？", True), ("ご確認をお願いいたす", True), ("ご教示ください", True),
             ("いただけますでしょうか", True),
             ("| ステータス | 意味 |\n| WT | 保留 |", False), ("以上が一覧です。", False)]:
    chk(f"問い/依頼の残存判定({w}): {b[:20]}", bool(_AK.search(b)), w)

# ── ⑯ AC7(cmd_508・病五即効分〜第3便是正): ひらがな/カタカナ/ローマ字が同一skeletonへ落ちること ──
#    実害: 現行_canonicalはカタカナのみローマ字化しひらがなは素通り→ひらがな表記が別skeletonに
#    落ち、_pj_resolveが永遠に一致しない(brief記載の実測を機械証明化)。online PJ名の閉集合
#    (2026-08-17 /tmp/cal_projects.json スナップショット時点で音韻カナ表記を持つ実例)のうち、
#    固有名は pack(cmd_491 AC3の作法どおり)から受け取る(engine_scanのvocab違反回避)。
#    ★cmd_508第3便是正(軍師QC1条件1): 是正前は examples.project_names[0](先頭1件)のみを
#    検査しており「閉集合全数」というAC7原文の要求に届いていなかった(実測: pack examples
#    2件中1件のみ検査対象・カナ表記を持つ1件のみ)。ここを examples.project_names 全件のループへ改める。
#    名自体が音韻としてカナ表記されうる名について、ひらがな/カタカナ/名そのものが
#    同一skeletonへ落ちることを assert する。
#    ★頭字語(AGL/GS等・音でなく文字を読む)は「アグル」のようなカナ発話が実在しない試験データに
#    なる(実測で判明)ため対象外。「Zenith→ゼニス」のような無関係な和訳読みも、_pj_indexが名の
#    文字列そのものからしかcanonicalを導出しない構造ゆえ対象外(実装の性質に忠実な閉集合)。
#    ★no silent caps: skipした名と全体件数中の検査件数をgate出力へ明示する(黙って落とさない)。
_ac7_names = _examples.get("project_names") or []
_ac7_kana_map = dict(zip(_ac7_names, _examples.get("project_names_kana", [])))
_ac7_tested, _ac7_skipped = [], []
for _nm in _ac7_names:
    _kana = _ac7_kana_map.get(_nm) or ""
    if not _kana or _kana == _nm:
        _ac7_skipped.append(_nm)                      # カナ表記が空/名と同一(頭字語等)=試験データとして不適
        continue
    _hira = "".join(chr(ord(_c) - 0x60) if "ァ" <= _c <= "ヶ" else _c for _c in _kana)  # カタカナ→ひらがな(試験データ生成用の逆変換)
    _c_nm = M["_canonical"](_nm)
    _c_kana = M["_canonical"](_kana)
    _c_hira = M["_canonical"](_hira)
    chk(f"AC7 全数[{_nm}]: pack由来PJ名とカタカナ表記が同一skeleton", _c_kana, _c_nm)
    chk(f"AC7 全数[{_nm}]: pack由来PJ名とひらがな表記が同一skeleton", _c_hira, _c_nm)
    chk(f"AC7 全数[{_nm}]: カタカナ表記とひらがな表記が同一skeleton", _c_hira, _c_kana)
    # resolver全体(_pj_resolve)でもひらがな入力からonline PJ名を解決できることを確認(実利用面を検証・
    # _pj_index の索引キーは名自体のcanonicalゆえ、名と同じ読みの入力のみが解決対象になる構造どおり)
    _st, _names, _ = _resolve(f"{_hira}のタスクは？")
    chk(f"AC7 resolver[{_nm}]: ひらがな表記からpack由来PJ名をunique解決", (_st, _names), ("unique", [_nm]))
    _ac7_tested.append(_nm)
print(f"ℹ️  AC7 全数検査: {len(_ac7_tested)}/{len(_ac7_names)}件を検査(skip={_ac7_skipped or 'なし'}"
      f"・理由=カナ表記が空/頭字語で音韻試験データ不適)")
if not _ac7_names:
    print("ℹ️  pack examples に project_names が無いためAC7音韻カナ試験は skip(skeleton性質はgate_pjname本体で別途検査済)")

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
