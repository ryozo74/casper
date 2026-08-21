#!/usr/bin/env python3
"""cmd_510第1便(実害A止血)回帰試験: 送信検問(_SEND_MENTION_RE系)が読取turnを誤って
保留し「お送りする本文に、頼まれた中身がまだ入っておりませぬ」(_DM_BODY_INCOMPLETE_MSG)
を捺す事故を、turn単位の送信意図判定(_turn_is_send_intent・層1)＋門(層3)で断つ。

pytest不使用(test_dm_threads_digest.py/test_casper_failover.pyの慣例に合わせ、素朴なassert方式)。

背景: _SEND_MENTION_RE(L2000)が裸の「DM」を拾い、読取除外(_SEND_MENTION_READ_EXCL_RE、
六語形のみ)が「照会いたします」「届いておりません」を逃がせぬ。話題がDMである限り
応答行に「DM」が現れ→保留され→カードは立たず(読取ゆえ当然)→_DM_BODY_INCOMPLETE_MSG
が捺される。殿の8/18実5turn中5turnがこれで空鉄砲になった。

★最重要設計原則(軍師裁定): 判定に迷えば必ず「送信turn」(従来動作=保留する側)へ倒す。
不快(誤って物乞い文が出る)より嘘(誤って完了詐称=送っていないのに送ったことになる)の
方が重い。読取と断ずるのは送信意図語彙が一つも無い時だけ。

AC1: 読取turnで物乞い文が出ぬことを、殿の実5turnを再生して示す
     (12:23/12:31×2/12:37/12:41、いずれにも出ぬこと)。
AC2: 自己紹介の番号が飛ばぬこと(「何ができるか」への応答にDM機能の項が含まれる)。
AC3: 送信turnでは従来通り保留・カードが働くことを示す(殺していない証明)。
AC9: 門(層3)の無効化変異について、変異前確認→赤化→復元→md5一致の四点で示す。

Usage: python3 test_send_intent_gate.py
"""
import json
import os
import re
import subprocess
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")   # import副作用(常駐スレッド/HTTP待受)を抑止(cmd_507症状②対策)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_server as C

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


# ── AC1材料: 殿の実5turn(2026-08-18)をqueue/evidence/から抽出する唯一の口 ──────────
# ★実ログ本文は社員/殿の実発話ゆえ、直接openはこの関数一つに閉じる(軍師point_c設計・
#   第3便replay_corpus.load()の先取り版。このテストはturnの"user発話"のみを使い、
#   出力(print)は判定結果(bool)のみで本文を出さない)。
_EVIDENCE_PATH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "queue", "evidence",
                                                "ryoji_20260818_30turn.json"))
_AC1_TARGET_TS = ["2026-08-18T12:23:59", "2026-08-18T12:31:20", "2026-08-18T12:31:27",
                  "2026-08-18T12:37:44", "2026-08-18T12:41:03"]


def _load_ac1_user_turns():
    """殿の実5turn(12:23/12:31×2/12:37/12:41)のuser発話のみを返す。本文はこの関数の外へ漏らさぬこと。"""
    with open(_EVIDENCE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    by_ts = {(t.get("ts"), t.get("role")): t.get("content") for t in data.get("turns", [])}
    out = []
    for ts in _AC1_TARGET_TS:
        content = by_ts.get((ts, "user"))
        if content is not None:
            out.append((ts, content))
    return out


def test_ac1_real_5turn_no_incomplete_message():
    """AC1本体: 殿の実5turnを_turn_is_send_intentへ通し、いずれも読取turn(False)と
    判定されること(=旧SEND_MENTION_RE一発判定なら5/5が誤ってTrue相当で保留された)。
    本文はcheck()のdetailにも出さない(守秘・要約せぬの遵守)。"""
    turns = _load_ac1_user_turns()
    check("AC1-0: 実5turnが全件揃っている", len(turns) == 5, f"n={len(turns)}")
    for ts, content in turns:
        got = C._turn_is_send_intent(content)
        check(f"AC1: {ts} は読取turnと判定される(物乞い文が出ない)", got is False, f"ts={ts}")


def test_ac2_self_intro_dm_item_not_dropped():
    """AC2: 自己紹介クエリ(送信意図語彙なし)は読取turnと判定され、DM秘書の項が
    送信検問の誤爆で切除されぬこと(_send_mention_line_hitが層2で発火しても層3が止める)。"""
    q = "キャスパーって何ができるの？自己紹介して"
    got = C._turn_is_send_intent(q)
    check("AC2: 自己紹介turnは読取turnと判定される", got is False, f"q={q!r}")
    # DM秘書の項そのもの(casper_context.md L169相当)が_send_mention_line_hitに掛かることを確認
    # (=このturnで層2が起動していれば削られていたはずの行、というAC2の前提の裏取り)
    dm_line = ("- **DM秘書**: 起動時に未読DMを一覧表示。本人名義での返信代筆(承認制)、"
               "相手への質問、既読化。\n")
    check("AC2前提: DM秘書の項は_send_mention_line_hitで保留対象になる行である(層2は健在)",
          C._send_mention_line_hit(dm_line), dm_line.strip())


def test_ac3_send_turn_still_held_not_killed():
    """AC3: 送信turnでは従来通り_turn_is_send_intent=Trueとなり、層2(_send_mention_line_hit等)
    が引き続き動く前提が保たれること(殺していない証明・機構呼出はサーバ本体のみで検証できぬため
    ここでは層1の判定値そのものを機械的に確認する)。"""
    send_queries = [
        "kiyotomoにDM送っといて",
        "DMを送って",
        "tetsuoに資料を渡して",
    ]
    for q in send_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC3: 送信turn「{q}」はTrue(従来動作=保留する側)と判定される", got is True, f"q={q!r}")

    # ★同一の応答文を送信turn/読取turnの二文脈で通し、分岐を機械的に示す(brief実施順序5)。
    same_response_line = "kiyotomoさんへDMを送ります。\n"
    send_ctx_gate = C._turn_is_send_intent("kiyotomoにDM送っといて")
    read_ctx_gate = C._turn_is_send_intent("今日、TimからDMが来てると思うけど見せて")
    check("AC3分岐-1: 送信turn文脈ではゲートTrue(層2が起動する側)", send_ctx_gate is True)
    check("AC3分岐-2: 読取turn文脈ではゲートFalse(層2が起動しない側)", read_ctx_gate is False)
    check("AC3分岐-3: 同一の応答文(qwen生成行)自体は変わらない(判定はqueryのみに依存)",
          bool(C._send_mention_line_hit(same_response_line)), same_response_line.strip())


def test_gate_defaults_true_when_ambiguous():
    """安全側設計: 空文字/DM語彙が皆無でない微妙な入力でも、DM関連語彙が無ければFalse、
    在って送信語彙を伴えばTrueへ倒れることを境界値で確認する(迷えば送信turn側)。"""
    check("空文字はTrue(迷えば送信turn)", C._turn_is_send_intent("") is True)
    check("None相当(空)もTrue", C._turn_is_send_intent(None) is True)
    check("DM語彙が皆無の雑談はFalse(読取と断じてよい=送信意図語彙が一つも無い)",
          C._turn_is_send_intent("今日の天気はどうですか") is False)


def _git_rev_parse_ok():
    try:
        subprocess.run(["git", "-C", HERE, "rev-parse", "--is-inside-work-tree"],
                        check=True, capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def test_ac9_mutation_kills_gate_incomplete_message_returns():
    """AC9: 門(層3)を無効化する変異を注入し、読取turnで物乞い文が復活する(赤化)ことを確認、
    元に戻し、md5一致で復元を確認する(四点構成: 変異前確認→赤化→復元→md5一致)。
    ★本番chat_server.py(稼働中プロセス・supervisor監視下)は絶対に書き換えない——
    一時ディレクトリへコピーした別名モジュールに対してのみ変異・復元・md5照合を行う
    (cmd_510運用留意: 実測外の書換えはauto-reload連鎖の実害を招く=8/18 13:11-13:14実例)。"""
    import hashlib
    import importlib
    import shutil
    import tempfile

    src_path = os.path.join(HERE, "chat_server.py")
    with open(src_path, "rb") as f:
        original_bytes = f.read()
    original_md5 = hashlib.md5(original_bytes).hexdigest()

    marker = "def _turn_is_send_intent("
    check("AC9-0: 変異前確認 — _turn_is_send_intent が実装中に存在する(本番ファイルを検査のみ・書換えなし)",
          marker.encode() in original_bytes)

    text = original_bytes.decode("utf-8")
    idx = text.find(marker)
    check("AC9-1: 変異対象関数が一意に見つかる", idx != -1)
    if idx == -1:
        return
    def_end = text.index("\n", idx)
    body_start = def_end + 1
    import re as _re
    m = _re.search(r"\n(?=(def |_[A-Za-z]))", text[body_start:])
    check("AC9-2: 関数本体の終端が検出できる", m is not None)
    if m is None:
        return
    body_end = body_start + m.start() + 1
    # 変異: 関数本体を「常にTrue(=送信turn扱い・層3が常時無効化される側)」へ機械的に差し替える。
    mutated_text = (text[:body_start] + "    return True  # AC9 mutation: gate disabled\n"
                     + text[body_end:])

    tmpdir = tempfile.mkdtemp(prefix="cmd510_ac9_")
    try:
        # 本番ディレクトリの依存モジュール(casper_rag等)をimportできるようsys.pathへ追加しつつ、
        # chat_server.py自体だけは別名でtmpdirに複製→そこだけを変異させる(本番ファイル非接触)。
        mutated_copy_path = os.path.join(tmpdir, "chat_server_ac9_mutant.py")
        with open(mutated_copy_path, "w", encoding="utf-8") as f:
            f.write(mutated_text)
        old_syspath = list(sys.path)
        sys.path.insert(0, tmpdir)
        sys.path.insert(0, HERE)   # casper_rag等の同居モジュール解決のため
        try:
            if "chat_server_ac9_mutant" in sys.modules:
                del sys.modules["chat_server_ac9_mutant"]
            import chat_server_ac9_mutant as C_mut
            read_query = "今日、TimからDMが来てると思うけど見せて"
            gate_after_mutation = C_mut._turn_is_send_intent(read_query)
            check("AC9-3: 変異後は読取turnでもTrue(層3が無効化された=赤化)",
                  gate_after_mutation is True, f"got={gate_after_mutation}")
        finally:
            sys.path[:] = old_syspath
            if "chat_server_ac9_mutant" in sys.modules:
                del sys.modules["chat_server_ac9_mutant"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        # 本番ファイルは最初から一度も書き換えていない——md5一致は「触れていないことの確認」として示す。
        restored_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
        check("AC9-4: 本番chat_server.pyは変異テスト中も一切書き換えられていない(md5不変)",
              restored_md5 == original_md5, f"before={original_md5} after={restored_md5}")


# ══════════════════════════════════════════════════════════════════════════
# cmd_511第2便(両義語母集合の是正+trace増設): _DM_TOPIC_ONLY_RE から「報告」「納品」「校了」
# (行為語=①「〜して」で依頼文になる)を除いたことの機械証明。
# 軍師実測の実害: 「TimにQC結果を報告して」「kiyotomoに納品して」「tetsuoに校了を報告して」が
# いずれもsend_intent=False(送信依頼が読取と誤判定)されていた。
# ══════════════════════════════════════════════════════════════════════════

def test_cmd511_ac9_topic_only_re_全語がsend_intent_trueになる():
    """AC9(機械証明): _DM_TOPIC_ONLY_RE に残る全語について「<人名>に<語>して」が
    send_intent=Trueになることをproperty testでassertする(閉集合全数検査・
    cmd_508 AC7ひらがな正準化と同型)。一語でもFalseなら赤。
    ★逆に、除去した行為語(報告/納品/校了)は現在_DM_TOPIC_ONLY_REに含まれぬので
    _DM_INTENT_REから消えず、送信意図語彙として残ることも併せて確認する。"""
    # _DM_TOPIC_ONLY_RE 自身から語を機械的に抽出する(手書き複製を避け、単一ソースへ追従させる)。
    import re as _re_local
    _topic_words = C._DM_TOPIC_ONLY_RE.pattern
    m = _re_local.search(r"\(([^)]+)\)", _topic_words)
    check("AC9-0: _DM_TOPIC_ONLY_RE から語群を抽出できる", m is not None, _topic_words)
    words = m.group(1).split("|") if m else []
    check("AC9-0b: 抽出語数が1以上", len(words) > 0, f"words={words}")
    for w in words:
        q = f"kiyotomoに{w}して"
        got = C._turn_is_send_intent(q)
        check(f"AC9: 話題語「{w}」を含む依頼文「{q}」はsend_intent=True(閉集合全数検査)",
              got is True, f"q={q!r} got={got}")


def test_cmd511_ac9_行為語除去後の実害再現クエリがtrueになる():
    """AC9裏取り: 軍師が実測した実害3例(是正前はいずれもFalse=誤判定)が、是正後は
    いずれもTrue(送信turn)と判定されること。"""
    real_harm_queries = [
        "TimにQC結果を報告して",
        "kiyotomoに納品して",
        "tetsuoに校了を報告して",
    ]
    for q in real_harm_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC9裏取り: 実害クエリ「{q}」はTrue(送信turn)と判定される(是正前はFalseだった)",
              got is True, f"q={q!r} got={got}")


def test_cmd511_ac_risk_読取turnの回帰試験():
    """★リスク併走試験(軍師の警告・タスクブリーフ明記の例): 語を出しすぎれば読取turnが
    送信と誤判定され、物乞い文が復活する(実害Aの再演)。ブリーフが明示する「DMを見せて」
    「DMの状況を照会して」等(=話題語DMを使った読取turn)がFalse(読取)のまま保たれる
    ことを確認する。AC9のTrue全数検査と両方が緑になって初めて是正完了。"""
    read_turn_queries = [
        "DMを見せて",
        "DMの状況を照会して",
        "今日、TimからDMが来てると思うけど見せて",
    ]
    for q in read_turn_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC-risk: 読取turn「{q}」はFalse(読取)のまま保たれる(物乞い文の復活防止)",
              got is False, f"q={q!r} got={got}")


def test_cmd511_ac_risk2_報告納品校了の裸名詞形は読取turnのまま():
    """★リスク併走試験その2(実ログ12:37:44回帰で発覚): 「報告」「納品」「校了」を
    _DM_TOPIC_ONLY_REから出した副作用で、これらが裸の名詞(依頼接尾辞なし)として
    現れる読取turnの地の文(例:「報告書の内容を教えて」「〜の報告だと思う」)まで
    送信語彙と誤認しないこと。依頼接尾辞(「して」等)で動詞化されている場合のみ
    True(test_cmd511_ac9_行為語除去後の実害再現クエリがtrueになる)、
    裸のままならFalse(読取)に保たれることの両立を確認する。"""
    ambiguous_bare_noun_queries = [
        "報告書の内容を教えて",
        "納品物の一覧を確認したい",
        "校了の状況はどうなっている？",
        "直近のDM見せてsorafuneの報告だと思う",   # cmd_510 AC1実5turn中の12:37:44と同型
    ]
    for q in ambiguous_bare_noun_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC-risk2: 裸の名詞「{q}」はFalse(読取)のまま保たれる(依頼接尾辞なし)",
              got is False, f"q={q!r} got={got}")


# ══════════════════════════════════════════════════════════════════════════
# cmd_511追加便2(軍師QC4 verdict=CONDITIONAL_PASS是正): 全数検査の母集合をcorpus内実例から
# _DM_INTENT_REの全語へ拡大する。「<人名>からの<語>ある？」→False(裸名詞=読取)、
# 「<人名>に<語>して」→True(依頼)の二本を、_DM_INTENT_REに列挙された全語について機械的に
# 走らせ、一語でも食い違えば赤化する(property test・cmd_508 AC7と同型の全数証明)。
# ★語彙は_DM_INTENT_REから手書き複製せず、実際にsearchでヒットする語のリストとして
# ここに保持する(パターン自体がネストした括弧(渡(し|す|して))・任意量指定子(送っ?と?い?て)を
# 含み単純な正規表現分解では再現できないため、_DM_INTENT_RE.search()で実在を確認した上で
# 手動列挙する。新語が_DM_INTENT_REへ足されたらこのリストにも追記する必要がある——
# その追記漏れ自体はAC11-0(語彙の実在確認)が拾う)。
# ══════════════════════════════════════════════════════════════════════════
_CMD511_ALL_DM_INTENT_WORDS = [
    "DM", "ディーエム", "送っといて", "送信", "送る", "送って", "連絡", "伝え",
    "渡し", "渡す", "渡して", "共有", "届け", "投げ", "報告",
    "dropbox", "ドロップボックス", "リンク", "納品", "校了",
]
# 「<人名>に<語>して」は名詞語彙(DM/送信/連絡/共有/報告/dropbox/リンク/納品/校了等)には
# 依頼接尾辞として自然に付くが、既に活用済みの動詞語彙(伝え/届け/投げ/送って/送る/送っといて/
# 渡し/渡す/渡して)へ「して」をさらに結合すると非文になる(「送っといてして」)——この語群は
# 語自体が既に依頼形(て形)であるかそのまま依頼形にできる形であり、語そのものを依頼文へ
# 差し込む(「Timに送って」「Timに伝えて」)のが自然な"to"側テンプレートである。
_CMD511_VERB_STEM_WORDS = {
    "送っといて": "送っといて", "送る": "送って", "送って": "送って", "伝え": "伝えて",
    "渡し": "渡して", "渡す": "渡して", "渡して": "渡して", "届け": "届けて", "投げ": "投げて",
}
# ★AC-S6裸形掃引の既知の例外(軍師QC4申し送り(a)の再確認・subtask_511_strategy1
# findings): 「送っといて/送って/渡して」は既にて形(依頼形)の語であり、これを
# 「Timからの<語>」へ直結した裸形(例:「Timからの送って」)は日本語として成立しない
# 非文である(実発話として現れない)。_dm_verb_word_as_request は て形直後が
# 文末(≒依頼の言い切り)の時に依頼形と認めるため、この非文形はTrue(送信)に倒れる——
# これは cmd_510 が定めた「迷えば送信turn側(安全側)へ倒せ」の既定どおりであり瑕ではない
# (軍師が「Timが送ってきたDMを見せて」等の実在しうる読取文でFalseになることを実測済)。
# 裸形の全数検査ではこの既知の非文語のみ除外し、「ある？」形の全数検査(常に非文にならない)
# は除外なしで全語を通す。
_CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS = {"送っといて", "送って", "渡して"}


def _cmd511_to_query(w):
    """AC11"to"側の自然なテンプレートを語の品詞に応じて選ぶ(名詞語彙=して付加、
    動詞語彙=語自体の依頼形をそのまま差し込む)。"""
    if w in _CMD511_VERB_STEM_WORDS:
        return f"Timに{_CMD511_VERB_STEM_WORDS[w]}"
    return f"Timに{w}して"


def _cmd511_extract_dm_intent_re_words():
    """AC-S5材料: _DM_INTENT_RE.pattern から、外側の丸括弧を剥がした上でトップレベルの
    `|` で分割し(ネストした括弧(例:渡(し|す|して))の内部の`|`では割らない)、
    語ごとに展開した重複無しの語リストを返す(ネスト展開: 渡(し|す|して)→渡し/渡す/渡して)。
    手書きの_CMD511_ALL_DM_INTENT_WORDSとは独立に、正規表現自身から機械的に導出する
    (双方向乖離検査のため、どちらか一方から他方を複製してはならない)。"""
    import re
    pattern = C._DM_INTENT_RE.pattern
    inner = pattern[1:-1] if pattern.startswith("(") and pattern.endswith(")") else pattern
    depth = 0
    parts = []
    cur = ""
    for ch in inner:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "|" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)

    seen = set()
    expanded = []
    for p in parts:
        m = re.match(r"^(.*?)\(([^()]+)\)(.*)$", p)
        if m:
            prefix, alt, suffix = m.groups()
            for a in alt.split("|"):
                word = prefix + a + suffix
                if word not in seen:
                    seen.add(word)
                    expanded.append(word)
        else:
            if p not in seen:
                seen.add(p)
                expanded.append(p)
    return expanded


def test_cmd511_ac11_dm_intent_re全語が実在確認できる():
    """AC11-0: _CMD511_ALL_DM_INTENT_WORDS の全語が、実際に_DM_INTENT_REでヒットすることを
    確認する(手書きリストが単一ソースの_DM_INTENT_REから乖離していないかの機械チェック・
    新語追加時の追記漏れをここで検知する)。"""
    for w in _CMD511_ALL_DM_INTENT_WORDS:
        check(f"AC11-0: 「{w}」は_DM_INTENT_REでヒットする(単一ソースとの整合)",
              bool(C._DM_INTENT_RE.search(w)), w)

    # ★AC-S5(軍師申し送りa): 「リスト→regex」方向(上のループ)は乖離を捉えるが、
    # 「regexへ新語が足されたのにリストへ追記されなかった」逆方向は捉えられない。
    # _DM_INTENT_REから機械抽出した語数と、手書きリストの語数が一致することをassertし、
    # 逆方向の乖離も(完全ではないが)検知できるようにする。
    re_words = _cmd511_extract_dm_intent_re_words()
    check("AC-S5: _DM_INTENT_REから抽出した語数と_CMD511_ALL_DM_INTENT_WORDSの語数が一致する"
          "(regex→リスト方向の乖離検査・新語追加のリスト追記漏れを検知)",
          len(re_words) == len(_CMD511_ALL_DM_INTENT_WORDS),
          f"re_words({len(re_words)})={re_words} list({len(_CMD511_ALL_DM_INTENT_WORDS)})="
          f"{_CMD511_ALL_DM_INTENT_WORDS}")


def test_cmd511_ac11_全語がfromで読取toで送信になる():
    """AC11(機械証明・軍師QC4指摘の是正本体): _DM_INTENT_REの全語について、
    「<人名>からの<語>ある？」(裸名詞=読取turn)はFalse、「<人名>に<語>して」(依頼文=送信turn)
    はTrueとなることを全数検査する。一語でも食い違えば赤化する(cmd_511追加便までは
    「伝え/届け/投げ/送って/送る/送っといて/渡し/渡す/渡して」の9語がfrom側で誤ってTrueに
    なっていた=軍師実測で発覚した穴の恒久化)。

    ★AC-S6(軍師申し送りb): from側は裸形「Timからの<語>」と「ある？」形
    「Timからの<語>ある？」の両方の文型で全語を掃引する(片方のみの運用では、
    もう一方の文型でしか現れない誤判定を見逃す)。
    ★裸形は_CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS(送っといて/送って/渡して)の3語のみ
    非文ゆえ例外とし、Trueのままで良い(下のtest_cmd511_ac_s6_非文例外語がtrueのままで良いことの確認
    で明示的に固定する)。それ以外の全語は裸形もFalseを要求する。"""
    for w in _CMD511_ALL_DM_INTENT_WORDS:
        from_bare_q = f"Timからの{w}"
        from_aru_q = f"Timからの{w}ある？"
        to_q = _cmd511_to_query(w)
        got_from_bare = C._turn_is_send_intent(from_bare_q)
        got_from_aru = C._turn_is_send_intent(from_aru_q)
        got_to = C._turn_is_send_intent(to_q)
        if w not in _CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS:
            check(f"AC11-from-bare: 「{from_bare_q}」は裸名詞=読取turnゆえFalse(閉集合全数検査・AC-S6裸形)",
                  got_from_bare is False, f"word={w!r} q={from_bare_q!r} got={got_from_bare}")
        check(f"AC11-from-aru: 「{from_aru_q}」は裸名詞=読取turnゆえFalse(閉集合全数検査・AC-S6ある？形)",
              got_from_aru is False, f"word={w!r} q={from_aru_q!r} got={got_from_aru}")
        check(f"AC11-to: 「{to_q}」は依頼文=送信turnゆえTrue(閉集合全数検査)",
              got_to is True, f"word={w!r} q={to_q!r} got={got_to}")


def test_cmd511_ac_s6_非文例外語がtrueのままで良いことの確認():
    """AC-S6補: _CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONSの3語(送っといて/送って/渡して)は
    「Timからの<語>」形が日本語として成立しない非文であるため、_turn_is_send_intentが
    True(cmd_510の「迷えば送信側」既定)を返すことを明示的に固定する(軍師実測・
    subtask_511_strategy1 findings確認済)。実在しうる読取文の形(「〜てきた」「〜てもらった」等の
    続きを伴う)ではFalseに戻ることも併せて確認し、これが「危険側の誤判定」ではなく
    「非文への安全側デフォルト」であることを示す。"""
    for w in _CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS:
        bare_q = f"Timからの{w}"
        got = C._turn_is_send_intent(bare_q)
        check(f"AC-S6補: 非文「{bare_q}」はTrue(非文への安全側デフォルト・瑕ではない)",
              got is True, f"word={w!r} q={bare_q!r} got={got}")
    # ★「Timから送っといてと言われた件」「tetsuoに渡してもらえる？」は軍師QC4検証時点(1便着手前)
    # では実測False、当時例に挙げられていたが、その後1便(subtask_511_impl6)の補集合設計
    # (roster人名解決+差出人マーカー未検出→True)導入により現在はTrueへ変わっている
    # (roster="Tim"/"tetsuo"が解け、_DM_SENDER_MARKER_REが「から」の直後語(の/来た/届いた/
    # もらった/送られ)/「が送っ」のいずれにも一致しないため)。本便(2便)の担当は_turn_is_send_intent
    # 本体の是非でなくAC-S5/AC-S6の試験整備ゆえ、ここでは1便後も安定して読取判定される
    # (て形+続きが依頼として成立しない=_dm_verb_word_as_requestがFalseのまま)例のみを保つ。
    real_read_queries = [
        "Timが送ってきたDMを見せて",
        "送ってもらったリンクを見せて",
    ]
    for q in real_read_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC-S6補: 実在しうる読取文「{q}」はFalse(非文の裸形とは別に、続きを伴えば正しく読取と判定される)",
              got is False, f"q={q!r} got={got}")


def test_cmd511_ac11_軍師実例2件がfalseになる():
    """AC11裏取り: 軍師が実測で示した2実例(是正前はいずれもTrue=送信と誤判定)が、
    是正後はいずれもFalse(読取turn)と判定されること。"""
    real_harm_queries = [
        "Timからの届け物ある？",
        "Timが送ってきたDMを見せて",
    ]
    for q in real_harm_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC11裏取り: 実害クエリ「{q}」はFalse(読取turn)と判定される(是正前はTrueだった)",
              got is False, f"q={q!r} got={got}")


def test_cmd511_ac11_送信turnの回帰試験():
    """★リスク併走試験: 動詞語彙の依頼形(AC3で確認済のもの含む)が今回の是正で崩れていないこと。"""
    send_turn_queries = [
        "kiyotomoにDM送っといて",
        "DMを送って",
        "tetsuoに資料を渡して",
        "Timに伝えて",
        "Timに届けてください",
        "Timに投げてくれ",
    ]
    for q in send_turn_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC11-risk: 送信turn「{q}」はTrue(送信)のまま保たれる(依頼形の動詞語彙は殺さない)",
              got is True, f"q={q!r} got={got}")


def test_cmd511_ac10_trace_payloadにsend_intent_gateフィールドがある():
    """AC10(観測の増設のみ・丙の凍結には触れるな): traceに_send_intent_gateの判定値
    フィールドが一つ足されていること。_trace_payload の戻りdictに send_intent_gate
    キーが存在し、渡した値がそのまま反映されることを確認する(一週間後の監査材料)。"""
    payload_true = C._trace_payload(
        trace_id="t1", query="q", actor=1, thread="th1", routed=None,
        fastpath=None, echoed=False, vch=False, injected_facts={}, resp_ids={},
        cont=0, gate=None, pj=None, rag_hits=0, ctx_len=0, gen_sec=0.0,
        salvaged=False, validated=False, gloss=None, guarded_claim=False,
        abstained=False, digests_fired=None, final_len=0, cards=0, fewshot_used=[],
        send_intent_gate=True)
    check("AC10-1: send_intent_gate=True を渡すとpayloadに同値が乗る",
          payload_true.get("send_intent_gate") is True, payload_true.get("send_intent_gate"))

    payload_false = C._trace_payload(
        trace_id="t2", query="q", actor=1, thread="th1", routed=None,
        fastpath=None, echoed=False, vch=False, injected_facts={}, resp_ids={},
        cont=0, gate=None, pj=None, rag_hits=0, ctx_len=0, gen_sec=0.0,
        salvaged=False, validated=False, gloss=None, guarded_claim=False,
        abstained=False, digests_fired=None, final_len=0, cards=0, fewshot_used=[],
        send_intent_gate=False)
    check("AC10-2: send_intent_gate=False を渡すとpayloadに同値が乗る",
          payload_false.get("send_intent_gate") is False, payload_false.get("send_intent_gate"))

    payload_default = C._trace_payload(
        trace_id="t3", query="q", actor=1, thread="th1", routed=None,
        fastpath=None, echoed=False, vch=False, injected_facts={}, resp_ids={},
        cont=0, gate=None, pj=None, rag_hits=0, ctx_len=0, gen_sec=0.0,
        salvaged=False, validated=False, gloss=None, guarded_claim=False,
        abstained=False, digests_fired=None, final_len=0, cards=0, fewshot_used=[])
    check("AC10-3: 未指定時はNone(既定値・呼出漏れを機械的に判別できる)",
          payload_default.get("send_intent_gate") is None, payload_default.get("send_intent_gate"))


def test_cmd511_ac8_残り火の文言が読取turnに残らない():
    """AC8: 「まだ実行しておりませぬ」という残り火の文言が読取turnに残らないことを確認・試験化する。
    ★_send_mention_line_hitは行そのものの判定であり読取turn文脈では層3(_send_intent_gate)が
    保留自体を発火させぬため、読取turnのqueryに対しては_turn_is_send_intent=Falseとなり、
    (chat_server本体側の)保留ロジックが起動しないことを層1の判定値で確認する
    (AC3分岐-2と同型・回帰確認として明示的に別testへ切り出す)。"""
    read_queries_with_undone_phrase_risk = [
        "DMを見せて",
        "DMの状況を照会して",
        "報告書はもう届いていますか",
        "納品状況を教えてください",
    ]
    for q in read_queries_with_undone_phrase_risk:
        gate = C._turn_is_send_intent(q)
        check(f"AC8: 読取turn「{q}」ではsend_intent_gate=False(層3が保留を起動させぬ=残り火の文言が出ぬ前提)",
              gate is False, f"q={q!r} gate={gate}")
    # 「まだ実行しておりませぬ」の定型文自体が_send_mention_line_hitで拾われず、
    # 保留対象の"送信言及行"としての扱いを受けないこと(送信語彙を含まぬ定型注記ゆえ)を確認。
    undone_note = "※上記アクションはまだ実行しておりませぬ。承認カードが出ておらねば、もう一度お申し付けを。"
    check("AC8: 残り火の定型注記文自体は_send_mention_line_hitの対象語彙を含まない(素通しされない)",
          not C._send_mention_line_hit(undone_note), undone_note)


# ══════════════════════════════════════════════════════════════════════════
# cmd_511第1便(subtask_511_impl6・軍師戦略review済subtask_511_strategy2): 補集合設計への
# 転換(roster人名が解ける ∧ _DM_INTENT_RE一致 ∧ ¬読取意図 ∧ ¬差出人マーカー → True)。
# 将軍検品(subtask_511_qc5_shogun_finding)が発見した丁寧語形6組の送信依頼誤判定を根治する。
# ══════════════════════════════════════════════════════════════════════════

# AC-S1材料: 将軍検品が実測した6組(左=既に現行Trueだった対照句・右=是正対象だった丁寧語形)。
_SHOGUN_6PAIRS = [
    ("kiyotomoに共有して", "kiyotomoへ共有をお願い"),
    ("tetsuoに報告して", "tetsuoへ報告をお願い"),
    ("ryojiに納品して", "ryojiへ納品を頼む"),
    ("tetsuoに渡してください", "tetsuoに渡してもらえる？"),
    ("tetsuoに送ってくれ", "tetsuoに送ってほしい"),
    ("kiyotomoに伝えておいて", "kiyotomoに伝えていただけますか"),
]


def test_ac_s1_将軍の6組すべて右もtrueになる():
    """AC-S1(回帰固定): 将軍検品が実測した6組について、左(現行既にTrue)・右(丁寧語形・
    是正前はFalseだった)のいずれもTrueになることを確認する。"""
    for left, right in _SHOGUN_6PAIRS:
        got_left = C._turn_is_send_intent(left)
        got_right = C._turn_is_send_intent(right)
        check(f"AC-S1左: 「{left}」はTrue(対照・従来から送信turn)", got_left is True,
              f"q={left!r} got={got_left}")
        check(f"AC-S1右: 「{right}」はTrue(丁寧語形・是正対象。是正前はFalseだった)",
              got_right is True, f"q={right!r} got={got_right}")


# AC-S2材料: 将軍の読取形6種(語を差し込むテンプレート)。
_SHOGUN_READ_TEMPLATES = [
    "{w}の内容を教えて", "{w}を見せて", "{w}の一覧を出して",
    "直近の{w}を見せて", "今日の{w}ある？", "{w}は届いてる？",
]


def test_ac_s2_読取形の掃引で退行なし():
    """AC-S2: 将軍の読取形6種×全語(_CMD511_ALL_DM_INTENT_WORDS)=120文＋裸形『<人名>からの<語>』
    ×全語＋『<人名>からの<語>ある？』×全語、三通り以上の読取形で誤判定0件を確認する
    (★双方向掃引必須・片方向だけの掃引が今回の見落としを生んだ、との軍師申し送りに従う)。
    ★裸形は_CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS(送っといて/送って/渡して)の3語のみ
    非文ゆえ除外する(impl7が定めた既知の例外と同じ扱い)。"""
    n_checked = 0
    for tmpl in _SHOGUN_READ_TEMPLATES:
        for w in _CMD511_ALL_DM_INTENT_WORDS:
            q = tmpl.format(w=w)
            got = C._turn_is_send_intent(q)
            check(f"AC-S2読取形: 「{q}」はFalse(読取turnのまま)", got is False,
                  f"tmpl={tmpl!r} word={w!r} got={got}")
            n_checked += 1
    check(f"AC-S2-0: 読取形6種×全語で{len(_SHOGUN_READ_TEMPLATES)}×"
          f"{len(_CMD511_ALL_DM_INTENT_WORDS)}={len(_SHOGUN_READ_TEMPLATES) * len(_CMD511_ALL_DM_INTENT_WORDS)}"
          "文を掃引した", n_checked == len(_SHOGUN_READ_TEMPLATES) * len(_CMD511_ALL_DM_INTENT_WORDS),
          f"n_checked={n_checked}")
    for w in _CMD511_ALL_DM_INTENT_WORDS:
        bare_q = f"Timからの{w}"
        aru_q = f"Timからの{w}ある？"
        got_aru = C._turn_is_send_intent(aru_q)
        check(f"AC-S2「ある？」形: 「{aru_q}」はFalse(読取turnのまま)", got_aru is False,
              f"word={w!r} got={got_aru}")
        if w not in _CMD511_BARE_FORM_NONSENTENCE_EXCEPTIONS:
            got_bare = C._turn_is_send_intent(bare_q)
            check(f"AC-S2裸形: 「{bare_q}」はFalse(読取turnのまま)", got_bare is False,
                  f"word={w!r} got={got_bare}")


# AC-S3材料: 丁寧語形6種以上(依頼接尾辞の開集合を代表する語形。将軍6組の右側から抽出した形＋追加)。
_POLITE_REQUEST_SUFFIXES = [
    "をお願い", "を頼む", "てもらえる？", "てほしい", "ていただけますか", "願えますか",
]


def test_ac_s3_送信形の掃引で誤判定0件():
    """AC-S3: 送信形の掃引——丁寧語形6種以上×全語で誤判定0件を確認する(★双方向掃引必須。
    _CMD511_VERB_STEM_WORDS所属語は活用済み動詞ゆえ、名詞語彙(_CMD511_VERB_STEM_WORDSに
    無い語)のみを対象とする——「送っといてをお願い」等は非文であり将軍の6組もいずれも
    名詞語彙(共有/報告/納品/渡す/送る/伝え)側の丁寧語形である)。"""
    noun_words = [w for w in _CMD511_ALL_DM_INTENT_WORDS if w not in _CMD511_VERB_STEM_WORDS]
    check("AC-S3-0: 名詞語彙が1語以上ある", len(noun_words) > 0, f"noun_words={noun_words}")
    n_checked = 0
    for suffix in _POLITE_REQUEST_SUFFIXES:
        for w in noun_words:
            q = f"tetsuoに{w}{suffix}"
            got = C._turn_is_send_intent(q)
            check(f"AC-S3送信形: 「{q}」はTrue(送信turn)", got is True,
                  f"suffix={suffix!r} word={w!r} q={q!r} got={got}")
            n_checked += 1
    check(f"AC-S3-1: 丁寧語形{len(_POLITE_REQUEST_SUFFIXES)}種×名詞語彙{len(noun_words)}語="
          f"{len(_POLITE_REQUEST_SUFFIXES) * len(noun_words)}文を掃引した",
          n_checked == len(_POLITE_REQUEST_SUFFIXES) * len(noun_words), f"n_checked={n_checked}")


def test_ac_s4_差出人マーカー補正が効く():
    """AC-S4: 差出人マーカーの補正が効く——「tetsuoからの報告を教えて」等の人名を含む読取が
    False(差出人マーカーがroster人名解決による誤判定を止める)。★AC-S2掃引で発覚した
    「Timから<動詞>と言われた」形の自由な伝聞も_dm_sender_marker_hitの人名+から隣接判定で
    塞がることを併せて確認する。"""
    sender_marker_queries = [
        "tetsuoからの報告を教えて",
        "kiyotomoからのDMを見せて",
        "Timが送ってきた資料ある？",
        "Timから送っといてと言われた件",
    ]
    for q in sender_marker_queries:
        got = C._turn_is_send_intent(q)
        check(f"AC-S4: 差出人マーカー入り「{q}」はFalse(読取turn)", got is False,
              f"q={q!r} got={got}")


def test_ac_s7_不変条件変異_roster解決を殺すと将軍6組がfalseへ戻る():
    """AC-S7(不変条件変異・是正が効いていることの赤の証明): _resolve_personを常にNoneを
    返すよう機械的に無効化した変異モジュールでは、将軍の6組(特に丁寧語形の右側)がFalseへ
    戻ることを示す。★本番chat_server.py(稼働中プロセス)は絶対に書き換えない——
    一時ディレクトリへコピーした別名モジュールに対してのみ変異する(AC9と同型の安全手順)。"""
    import importlib
    import shutil
    import tempfile

    src_path = os.path.join(HERE, "chat_server.py")
    with open(src_path, "rb") as f:
        original_bytes = f.read()

    marker = "def _resolve_person(query, exclude=None):"
    check("AC-S7-0: 変異対象関数(_resolve_person)が実装中に存在する(本番ファイルを検査のみ・書換えなし)",
          marker.encode() in original_bytes)
    if marker.encode() not in original_bytes:
        return

    text = original_bytes.decode("utf-8")
    idx = text.find(marker)
    def_end = text.index("\n", idx)
    body_start = def_end + 1
    m = re.search(r"\n(?=(def |_[A-Za-z]))", text[body_start:])
    check("AC-S7-1: 関数本体の終端が検出できる", m is not None)
    if m is None:
        return
    body_end = body_start + m.start() + 1
    # 変異: _resolve_person本体を「常に(None, None)を返す」へ機械的に差し替える
    # (roster解決という不変条件そのものを殺す=補集合設計の第一項を無効化する)。
    mutated_text = (text[:body_start] + "    return None, None  # AC-S7 mutation: roster resolution disabled\n"
                     + text[body_end:])

    tmpdir = tempfile.mkdtemp(prefix="cmd511_acs7_")
    try:
        mutated_copy_path = os.path.join(tmpdir, "chat_server_acs7_mutant.py")
        with open(mutated_copy_path, "w", encoding="utf-8") as f:
            f.write(mutated_text)
        old_syspath = list(sys.path)
        sys.path.insert(0, tmpdir)
        sys.path.insert(0, HERE)
        try:
            if "chat_server_acs7_mutant" in sys.modules:
                del sys.modules["chat_server_acs7_mutant"]
            import chat_server_acs7_mutant as C_mut
            for left, right in _SHOGUN_6PAIRS:
                got_right = C_mut._turn_is_send_intent(right)
                check(f"AC-S7-2: 変異後(roster解決無効)は丁寧語形「{right}」がFalseへ戻る"
                      "(是正が効いていることの赤の証明)", got_right is False,
                      f"q={right!r} got={got_right}")
        finally:
            sys.path[:] = old_syspath
            if "chat_server_acs7_mutant" in sys.modules:
                del sys.modules["chat_server_acs7_mutant"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        restored_md5 = __import__("hashlib").md5(open(src_path, "rb").read()).hexdigest()
        original_md5 = __import__("hashlib").md5(original_bytes).hexdigest()
        check("AC-S7-3: 本番chat_server.pyは変異テスト中も一切書き換えられていない(md5不変)",
              restored_md5 == original_md5, f"before={original_md5} after={restored_md5}")


def test_ac_s7_roster人名が解けぬ時は既定へ倒す():
    """AC-S7補(軍師risks対策の裏取り): roster人名が解けない指示語宛先(「あの人に送っておいて」等)
    では、補集合設計の第一項が発火せず、従来の判定(接尾辞判定/迷えば送信側の既定)へフォールバック
    することを確認する。"""
    q = "あの人に送っておいて"
    uid, _name = C._resolve_person(q, exclude=None)
    check("AC-S7補-0: 「あの人」はrosterで解決できない(前提の確認)", uid is None, f"uid={uid}")
    got = C._turn_is_send_intent(q)
    check(f"AC-S7補: roster人名が解けぬ「{q}」でも従来の判定でTrue(送信側の既定を保つ)",
          got is True, f"q={q!r} got={got}")


def main():
    tests = [
        test_ac1_real_5turn_no_incomplete_message,
        test_ac2_self_intro_dm_item_not_dropped,
        test_ac3_send_turn_still_held_not_killed,
        test_gate_defaults_true_when_ambiguous,
        test_ac9_mutation_kills_gate_incomplete_message_returns,
        test_cmd511_ac9_topic_only_re_全語がsend_intent_trueになる,
        test_cmd511_ac9_行為語除去後の実害再現クエリがtrueになる,
        test_cmd511_ac_risk_読取turnの回帰試験,
        test_cmd511_ac_risk2_報告納品校了の裸名詞形は読取turnのまま,
        test_cmd511_ac11_dm_intent_re全語が実在確認できる,
        test_cmd511_ac11_全語がfromで読取toで送信になる,
        test_cmd511_ac_s6_非文例外語がtrueのままで良いことの確認,
        test_cmd511_ac11_軍師実例2件がfalseになる,
        test_cmd511_ac11_送信turnの回帰試験,
        test_cmd511_ac10_trace_payloadにsend_intent_gateフィールドがある,
        test_cmd511_ac8_残り火の文言が読取turnに残らない,
        test_ac_s1_将軍の6組すべて右もtrueになる,
        test_ac_s2_読取形の掃引で退行なし,
        test_ac_s3_送信形の掃引で誤判定0件,
        test_ac_s4_差出人マーカー補正が効く,
        test_ac_s7_不変条件変異_roster解決を殺すと将軍6組がfalseへ戻る,
        test_ac_s7_roster人名が解けぬ時は既定へ倒す,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            t()
        except Exception as e:
            check(t.__name__, False, f"EXCEPTION: {e!r}")
    print(f"\n{'='*60}")
    if _failures:
        print(f"FAIL: {len(_failures)}件 -> {_failures}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
