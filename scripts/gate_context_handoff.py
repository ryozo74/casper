#!/usr/bin/env python3
"""cmd_492 第3便 対象引き継ぎ機構(_topic_handoff/_pending_question_synthesis)の回帰ゲート
(純機構・インメモリ・書込ゼロ)。全PASSで exit 0。

守る掟(3系統・正常系のみの緑は不可):
 ① 引き継ぎ成立(AC1/AC2型): 対象省略turnで直前の話題(doc/project/person)が確定的に注入される。
 ② 話題転換で引きずらぬ(AC4型・★特に重要): 新対象が明示されたturnでは前対象を引き継がない。
 ③ 聞き返し後に答える(AC3・第3便の新機能): 聞き返し(pending_question記録)→対象判明で、
    元の問い+新対象が合成されたdigestが生成される。

chat_server.py を import すると server が起動してしまうゆえ、gate_aurora_save.py と同じ手法
(ast で当該機構のみを抜いて exec)で検査する(名前が変わった/消えたらゲートが落ちる=機構の在処も守る)。
_needs_prior_context の LLM 枝(_needs_prior_context_llm)は M["_needs_prior_context_llm"] を
差し替えて stub し、ネットワーク非依存のまま True/False/None 3系統を検査する。
"""
import ast
import json
import os
import sys
import time

import pack_config

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")

# cmd_495: 固有名は pack から受け取る(gate_pjname.pyと同じ流儀・cmd_491 AC3の趣旨を本ゲートにも適用)。
# 話題転換の言い立て役(前対象Zenithとは別のPJ名を名指しする体)には examples.project_names[0] を使う。
_examples = pack_config.get("examples", {}) or {}
_OTHER_PJ = (_examples.get("project_names") or ["other-pj"])[0]

WANT = ["_LAST_TOPIC", "_TOPIC_HANDOFF_FRESH_SEC", "_PJ_ALIAS",
        # 【Fable第七診】_needs_prior_context が形ゲート(疑問形/依頼形)とturn-local memoを
        # 使うようになったため、その依存もここへ載せる。載せねばゲートは実機構でなく
        # 「抽出できた部分だけ」を検査することになる(検問器自身の差分を見よ・cmd_491の教訓)。
        "_QUESTION_FORM_RE", "_REQUEST_FORM_RE", "_DESIRE_FORM_RE", "_ASK_DELEGATE_RE",
        "_LLM_CALL_LOCAL", "_TURN_SEQ", "_turn_memo",
        "_needs_prior_context", "_needs_prior_context_llm",
        "_topic_handoff", "_pending_question_synthesis", "topic_handoff_digest",
        "_pj_resolve", "_pj_index", "_pj_name_hit", "_canonical",
        "_kana_to_romaji", "_translit_kana_runs", "_KANA2ROMA", "_KANA_SMALL",
        "_ollama_json",
        # cmd_492 4便: 引き継ぎ後に道具呼出が文字列のまま出て終わる欠陥(道具実況漏れ)の検査用
        "_TOOL_NARRATION_RE", "_TOOL_NARRATION_INLINE_RE", "_strip_tool_narration",
        "_strip_tool_narration_chunk", "_is_promise_only_no_data",
        "_table_rows_are_placeholder_only", "_TOOL_PROMISE_ONLY_RE",
        "_PLACEHOLDER_CELL_RE", "_TOOL_NAME_MENTION_RE",
        # cmd_492 4便再送: _pj_status_fallback は /tmp/cal_projects.json (実行時データ)に依存するため
        # 元は対象外だったが、本便でこの関数自体を改修(vault資料の持ち越し)したため検査対象に加える。
        # 「純機構・インメモリ・書込ゼロ」は維持する——実ファイルを読ませず、json.load を差し替えて
        # インメモリ辞書を返すスタブにする(下記 _StubJson)。
        "_pj_status_fallback",
        # cmd_492 5便: _LAST_TOPIC記録先決定を純関数として切り出した(canon_turnのtop_source noise対策)
        "_resolve_turn_topic"]

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
exec("import re, os, json, threading, time, datetime", M)   # threading: _LLM_CALL_LOCAL(turn-local memo)が要る


class _FakeRag:
    """casper_rag スタブ: top_source を差替え可能にし、vault実データ/ネットワーク非依存にする。"""
    def __init__(self):
        self.doc = None                 # (src, fulltext) を差し替えて使う。Noneなら常に(None,None)

    def top_source(self, query, threshold=0.32):
        return self.doc if self.doc else (None, None)


M["casper_rag"] = _FakeRag()
# _resolve_persons(roster/翻字チェーン一式・本ゲートの検査対象外)はstubで空返却に固定する。
# 人物解決自体(_resolve_person等)は本ゲートの対象外——doc/project経路の優先順位検査に
# 集中するため、実ロスターへの依存を切る(person-kindの実地確認はcmd_492検証済 AC1/AC2で担保)。
M["_resolve_persons"] = lambda query, exclude=None, cap=5: []

# cmd_492 4便再送: _pj_status_fallback/_pj_index は /tmp/cal_projects.json を直接読む(実行時データ)。
# 「純機構・インメモリ・書込ゼロ」を保つため、os.path.getmtime と json.load(open(...)) を差し替え、
# 呼出元がそのパスを渡した時だけインメモリの _CAL_PROJECTS_STUB を返す(他ファイルには影響させない・
# 実ファイルは一切読ませない)。
_CAL_PATH = "/tmp/cal_projects.json"
_CAL_PROJECTS_STUB = {"items": [{"id": 55, "name": "Zenith", "display_status": "online",
                                  "status": "in-progress", "end_date": "2099-09-30T00:00:00"}]}


class _StubOsPath:
    def __getattr__(self, name):
        return getattr(os.path, name)

    def getmtime(self, path):
        if path == _CAL_PATH:
            return 1.0                  # 固定mtime(スタブが常に採用される・実ファイルのmtimeとは無関係)
        return os.path.getmtime(path)


class _StubOs:
    def __getattr__(self, name):
        return getattr(os, name)

    @property
    def path(self):
        return _StubOsPath()


class _StubFile:
    def read(self):
        return json.dumps(_CAL_PROJECTS_STUB)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_open(path, *a, **kw):
    if path == _CAL_PATH:
        return _StubFile()
    return open(path, *a, **kw)


M["os"] = _StubOs()
M["open"] = _stub_open
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)

_needs = M["_needs_prior_context"]
_handoff = M["_topic_handoff"]
_pq_synth = M["_pending_question_synthesis"]
_digest = M["topic_handoff_digest"]
_LAST_TOPIC = M["_LAST_TOPIC"]
_FRESH_SEC = M["_TOPIC_HANDOFF_FRESH_SEC"]
_fake_rag = M["casper_rag"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_is(name, got, expected_identity_fn, label):
    ok = expected_identity_fn(got)
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={label}"))


def reset():
    _LAST_TOPIC.clear()
    _fake_rag.doc = None


WHO = {"uid": "u1"}
WHO_OTHER = {"uid": "u2"}
THR = "gate_thr"


# ══════════════════════════════════════════════════════════════════════════
# stub注入: M["_needs_prior_context_llm"]を差し替え、_needs_prior_context自身の
# 前置ゲート(_pj_resolveのみ・top_source不採用)は差し替えず検査対象に含めたまま。
# ══════════════════════════════════════════════════════════════════════════
M["_needs_prior_context_llm"] = lambda q: True    # 既定: 「対象要る」寄りにstub(呼出側のロジックを見る)


# ── ① 引き継ぎ成立系(AC1/AC2型): 直前話題(doc/project/person)が対象省略turnへ確定的に注入される ──
reset()
_LAST_TOPIC[THR] = {"kind": "doc", "key": "spec.md", "label": "spec.md", "uid": "u1", "ts": time.time()}
chk("① doc引き継ぎ成立: _topic_handoffが対象を返す",
    (_handoff(THR, WHO, "工数を教えて") or {}).get("label"), "spec.md")
digest = _digest(THR, WHO, "工数を教えて")
chk("① digestに対象名が注入される", "spec.md" in digest, True)

reset()
_LAST_TOPIC[THR] = {"kind": "project", "key": "Zenith", "label": "Zenith", "uid": "u1", "ts": time.time()}
chk("① project引き継ぎ成立: _topic_handoffが対象を返す",
    (_handoff(THR, WHO, "進捗はどう？") or {}).get("label"), "Zenith")

reset()
_LAST_TOPIC[THR] = {"kind": "person", "key": 40, "label": "terajima", "uid": "u1", "ts": time.time()}
chk("① person引き継ぎ成立: _topic_handoffが対象を返す",
    (_handoff(THR, WHO, "担当は？") or {}).get("label"), "terajima")

# 引き継ぎ打ち切り条件(いずれか1つで打ち切り・掟「緑ゲートに嘘は映らぬ」=異常系も検査)
reset()
_LAST_TOPIC[THR] = {"kind": "doc", "key": "spec.md", "label": "spec.md", "uid": "u1",
                     "ts": time.time() - _FRESH_SEC - 60}     # 鮮度切れ(31分前)
chk("① 鮮度切れで引き継がない(None)", _handoff(THR, WHO, "工数を教えて"), None)

reset()
_LAST_TOPIC[THR] = {"kind": "doc", "key": "spec.md", "label": "spec.md", "uid": "u1", "ts": time.time()}
chk("① 別人の話題は引き継がない(None)", _handoff(THR, WHO_OTHER, "工数を教えて"), None)

reset()
chk("① 直前話題が無ければ引き継がない(None)", _handoff(THR, WHO, "工数を教えて"), None)

reset()
_LAST_TOPIC[THR] = {"kind": "doc", "key": "spec.md", "label": "spec.md", "uid": "u1", "ts": time.time()}
M["_needs_prior_context_llm"] = lambda q: None      # 分類器判定不能
chk("① needs=None(判定不能)なら引き継がない(None・失敗とゼロを別出口)", _handoff(THR, WHO, "工数を教えて"), None)
M["_needs_prior_context_llm"] = lambda q: True      # 以降のケース用に戻す


# ── ② 話題転換で引きずらぬ(AC4型・★特に重要): 対象が明示されたturnは前対象を引き継がない ──
reset()
_LAST_TOPIC[THR] = {"kind": "doc", "key": "spec.md", "label": "spec.md", "uid": "u1", "ts": time.time()}
chk_is("② PJ名一意解決(明示)ならneeds_prior_context=False(話題転換扱い)",
       _needs("Zenithです"), lambda v: v is False, "False(is)")
chk("② PJ名明示turnは_topic_handoffが引き継がない(None)", _handoff(THR, WHO, "Zenithです"), None)

reset()
_LAST_TOPIC[THR] = {"kind": "project", "key": "Zenith", "label": "Zenith", "uid": "u1", "ts": time.time()}
M["_needs_prior_context_llm"] = lambda q: False     # 対象転換(別PJ名指し)をLLM側も陰性判定と仮定
chk("② 話題転換turn(needs=False)は_topic_handoffが引き継がない(None)",
    _handoff(THR, WHO, f"{_OTHER_PJ}の状況は？"), None)
digest2 = _digest(THR, WHO, f"{_OTHER_PJ}の状況は？")
chk("② 話題転換turnのdigestは空(前対象Zenithが漏れない)", digest2, "")
M["_needs_prior_context_llm"] = lambda q: True


# ── ③ 聞き返し後に答える(AC3・本便新機能): pending_question→対象判明で合成digestが生成される ──
reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time()}
pq_digest, new_topic = _pq_synth(THR, WHO, "Zenithです")
chk("③ 合成成立: 新topicがproject/Zenithに解決される",
    (new_topic or {}).get("kind"), "project")
chk("③ 合成digestに元の問い(工数を教えて)が含まれる", "工数を教えて" in pq_digest, True)
chk("③ 合成digestに新対象(Zenith)が含まれる", "Zenith" in pq_digest, True)

# pending_questionでない/鮮度切れ/別人 → 合成不成立((""),None)
reset()
_LAST_TOPIC[THR] = {"kind": "project", "key": "Zenith", "label": "Zenith", "uid": "u1", "ts": time.time()}
chk("③ 前turnがpending_questionでなければ合成不成立", _pq_synth(THR, WHO, "Zenithです"), ("", None))

reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time() - _FRESH_SEC - 60}
chk("③ pending_questionが鮮度切れなら合成不成立", _pq_synth(THR, WHO, "Zenithです"), ("", None))

reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time()}
chk("③ 別人からのpending_questionは合成不成立", _pq_synth(THR, WHO_OTHER, "Zenithです"), ("", None))

reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time()}
chk("③ 今turnも対象が判明しなければ合成不成立(PJ名なし)", _pq_synth(THR, WHO, "うーん、わからない"), ("", None))

# doc経由の合成(PJ/人物いずれも解決しない時のみtop_sourceへフォールバック・優先順位検査)
reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time()}
_fake_rag.doc = ("asset_spec.md", "本文...")
pq_digest3, new_topic3 = _pq_synth(THR, WHO, "asset_specです")
chk("③ PJ/人物いずれも不成立時はtop_source(doc)へフォールバック",
    (new_topic3 or {}).get("kind"), "doc")
chk("③ docフォールバック合成digestにも元の問いが含まれる", "工数を教えて" in pq_digest3, True)

# ★優先順位検査(top_source noise対策): PJ名が一意解決する時はtop_sourceに勝つ(noise越しでも正しいPJを掴む)
reset()
_LAST_TOPIC[THR] = {"kind": "pending_question", "key": "工数を教えて", "label": "工数を教えて",
                     "uid": "u1", "ts": time.time()}
_fake_rag.doc = ("noise_doc.md", "ノイズ本文(trigram類似度で常に何か返る想定)")
pq_digest4, new_topic4 = _pq_synth(THR, WHO, "Zenithです")
chk("★③ PJ名一意解決時はtop_source noiseに負けずprojectを選ぶ",
    (new_topic4 or {}).get("kind"), "project")
chk("★③ 選ばれた対象はZenith(noise_docでない)", (new_topic4 or {}).get("label"), "Zenith")


# ══════════════════════════════════════════════════════════════════════════
# ④ cmd_492 4便: 引き継ぎ成立後、道具呼出が文字列のまま出て終わる欠陥(道具実況漏れ)の検査。
#    実測(将軍3回・毎回異なる異常終了): 「Zenithです」→「工数を教えて」の系列で
#    ・応答が空(0字)のまま無言落下
#    ・『Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')』
#      のように、前置き文＋同一行の道具呼出構文がそのまま出て終わる(cmd_485同型の「言うただけ」欠陥)。
#    _TOOL_NARRATION_RE は行頭一致のみで「前置き文＋同一行の道具呼出」を取りこぼしていたのが真因。
#    _strip_tool_narration が地の文は残しつつ道具呼出断片だけを確実に剥ぐことを検査する。
# ══════════════════════════════════════════════════════════════════════════
_strip = M["_strip_tool_narration"]

chk("④ 前置き文+同一行の道具呼出(実測ケース1): 道具呼出断片が残らない",
    "calendar_lookup(" not in _strip(
        "Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')"),
    True)
chk("④ 前置き文+同一行の道具呼出: 前置きの地の文は残る(全消しにしない)",
    "取得します" in _strip(
        "Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')"),
    True)
chk("④ 前置き文+改行+道具呼出(実測ケース2 project_id='zenith'相当): 道具呼出が残らない",
    "calendar_lookup(" not in _strip(
        "Zenithプロジェクトのタスク一覧と工数を取得します。\ncalendar_lookup(kind='tasks', project_id='zenith')"),
    True)
chk("④ 道具呼出のみ(前置きなし・従来ケース): 剥いで空になる(fallback救済に委ねる)",
    _strip("calendar_lookup(kind='tasks', project_id='Zenith')"), "")
chk("④ 道具呼出を含まない正常文はそのまま(過剰マッチしない)",
    _strip("Zenithの工数は合計120時間でござる。"), "Zenithの工数は合計120時間でござる。")

# ★突然変異検証: 4便是正(_TOOL_NARRATION_INLINE_RE)を無効化すると④が落ちることを確認し、
# ゲートが実際にこの欠陥配線を検出できることを実証する(将軍指導: 緑ゲートに嘘は映らぬ)。
_mut_before = M["_TOOL_NARRATION_INLINE_RE"]
_mut_null = __import__("re").compile(r"(?!)")     # 何にもマッチしない(inline strip を無効化する変異)
M["_TOOL_NARRATION_INLINE_RE"] = _mut_null
_mut_leak = "calendar_lookup(" in _strip(
    "Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')")
chk("★④ 突然変異(inline strip無効化)でゲートが欠陥を検出する(漏れが再現する)", _mut_leak, True)
M["_TOOL_NARRATION_INLINE_RE"] = _mut_before      # 復元(以降のテストに影響させない)
chk("④ 変異復元後は再び漏れない(復元確認)",
    "calendar_lookup(" not in _strip(
        "Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')"),
    True)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ cmd_492 4便(実地再現で判明・真因追補): 実配線は _strip_tool_narration(final一括)ではなく
#    _semit/_flush_pend のストリーム逐次送出(改行が来た時点でクライアントへ即送信)を経由する。
#    最初の是正(_strip_tool_narrationのみ)を本番へ投入して実測した所、生の道具呼出がなお漏れた
#    (streamで既に送出済み・final側の掃除が届く前にbyteが出た後だった)。
#    ゆえ改行/空白を壊さぬ専用の _strip_tool_narration_chunk を追加しストリーム側に配線した。
#    このゲートは _strip_tool_narration_chunk 単体の断片除去を検査する(行構造保持=既存表示を壊さない事も検査)。
# ══════════════════════════════════════════════════════════════════════════
_strip_chunk = M["_strip_tool_narration_chunk"]

chk("⑤ 実測ケース(改行あり・完成行としてstream送出される形): 道具呼出断片が残らない",
    "calendar_lookup(" not in _strip_chunk(
        "Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')\n"),
    True)
chk("⑤ 正常な完成行(改行あり)はそのまま素通し(streamの行区切りを壊さない)",
    _strip_chunk("項目A: 値1\n"), "項目A: 値1\n")
chk("⑤ 正常な空行付き完成行も改行構造を保つ",
    _strip_chunk("項目B: 値2\n\n"), "項目B: 値2\n\n")
chk("⑤ 前置き文+同一行の道具呼出(改行あり): 前置きの地の文と改行は残る",
    _strip_chunk("Zenithプロジェクトのタスク一覧と工数を取得します。calendar_lookup(kind='tasks', project_id='Zenith')\n"),
    "Zenithプロジェクトのタスク一覧と工数を取得します。\n")
chk("⑤ _flush_pend相当(改行なし終端の道具呼出のみ): 空文字列になる(何も漏らさない)",
    _strip_chunk("calendar_lookup(kind='tasks', project_id='zenith')"), "")

# ══════════════════════════════════════════════════════════════════════════
# ⑥ cmd_492 4便 追補(実地7連続実投入で判明): 道具呼出構文を一切書かず、「〜取得します/確認します」
#    という約束文だけ、またはプレースホルダのみの表(実測『(データ取得中)』『-』)＋道具名の地の文言及で
#    自然終了する形も同型の「言うただけ」欠陥。_is_promise_only_no_data がこれを判定し、
#    exit-guard 側で _pj_status_fallback または正直な不能表明へ倒す(chat_server.py L8982附近)。
# ══════════════════════════════════════════════════════════════════════════
_promise = M["_is_promise_only_no_data"]

chk("⑥ 実測ケース(約束文のみ・実データ無し): promise-only判定=True",
    _promise("Zenithのタスク詳細を取得します。"), True)
chk("⑥ 実測ケース(プレースホルダのみの表+道具名地の文言及): promise-only判定=True",
    _promise("Zenithのタスク一覧と工数を確認します。\n\n"
              "| タスク名 | 担当者 | 期限 | 状態 | 備考 |\n|---|---|---|---|---|\n"
              "| (データ取得中) | - | - | - | - |\n\n"
              "calendar_lookupでZenithの最新タスクを取得して詳細を回答します。"),
    True)
chk("⑥ 実データを伴う表がある正常回答は対象外(過検出しない)",
    _promise("Zenithのタスク一覧と工数を確認します。\n\n"
              "| タスク名 | 担当者 | 期限 | 状態 | 工数(日) |\n|---|---|---|---|---|\n"
              "| Zenith_01 | tetsuo | 2026-08-15 | in-progress | 5.0 |\n\n合計工数: 15日"),
    False)
chk("⑥ 実データ(箇条書き+数値)を伴う正常回答は対象外(過検出しない)",
    _promise("Zenithのタスク一覧を取得しました。\n\n"
              "| タスク名 | 担当者 | 期限 | 状態 | 備考 |\n|---|---|---|---|---|\n"
              "| Zenith_01 | tetsuo | 2026-08-15 | in-progress | 進行中 |\n\n"
              "**工数見積もり:**\n*   **Zenith_01**: 残り約5日（現在進行中）"),
    False)
chk("⑥ 空文字列はpromise-only対象外(既存の空チェックに委ねる・二重救済しない)",
    _promise(""), False)

# ══════════════════════════════════════════════════════════════════════════
# ⑦ cmd_492 4便再送(将軍実測2026-08-05・カ老申送り): promise-only判定(⑥)は個々のnarrationを
#    正しく検出できていたが、_pj_status_fallback がstatus/納期以外(例: 工数)の問いには常に
#    空文字を返し、呼出側が汎用謝罪文へ倒すだけだった——この turn で既に取得済のvault資料
#    (top_source)を活かさず捨てていた。本便で _pj_status_fallback に vault_src/vault_fulltext を
#    渡せるよう改修し、Calendar側に無くとも既取得の一次資料を提示するようにした。
#    実行時データ(/tmp/cal_projects.json)への依存は os.path.getmtime/open を差し替えて遮断し、
#    「純機構・インメモリ・書込ゼロ」を保ったまま検査する。
# ══════════════════════════════════════════════════════════════════════════
_pj_fallback = M["_pj_status_fallback"]

chk("⑦ PJ一意解決+vault無し: 従来通りstatus/納期のみ(既存挙動を壊さない)",
    "ステータス" in _pj_fallback("Zenithです") and "社内記録" not in _pj_fallback("Zenithです"),
    True)
_with_vault = _pj_fallback("Zenithの工数を教えて", vault_src="asset_20260701.md", vault_fulltext="人月80万")
chk("⑦ PJ一意解決+vault有り(本便新機能): status本文とvault資料の両方が乗る",
    ("ステータス" in _with_vault) and ("人月80万" in _with_vault) and ("asset_20260701.md" in _with_vault),
    True)
chk("⑦ PJ不明(status不成立)+vault無し: 空文字列のまま(既存挙動)",
    _pj_fallback("こんにちは"), "")
_novault_pj_only_vault = _pj_fallback("こんにちは", vault_src="x.md", vault_fulltext="本文Y")
chk("⑦ PJ不明(status不成立)+vault有り(本便新機能): vault資料のみでも空を返さない(掟: 失敗とゼロを別出口へ)",
    ("本文Y" in _novault_pj_only_vault) and ("x.md" in _novault_pj_only_vault),
    True)

# ★突然変異検証: ⑦是正(vault_fulltext分岐)を無効化すると「PJ不明+vault有り」ケースが
# 再び空文字列に戻る(=実効性の実証)。
import types as _types
_orig_src = open(SRC, encoding="utf-8").read()
_mut_src = _orig_src.replace(
    'if vault_fulltext:                    # PJ側が不成立(st!=unique/online不一致)でも、既取得のvault資料はそのまま提示\n'
    '            return f"社内記録（{vault_src}）に次の記載がござる:\\n" + vault_fulltext[:1500]\n',
    '# MUTATED: vault-only fallback disabled\n')
_mut_applied = _mut_src != _orig_src
if _mut_applied:
    _mtree = ast.parse(_mut_src)
    _mpicked = [n for n in _mtree.body if getattr(n, "name", None) == "_pj_status_fallback"]
    _MM = dict(M)   # 他の機構(_pj_resolve等)は共有し、_pj_status_fallbackのみ差し替える
    exec(compile(ast.Module(body=_mpicked, type_ignores=[]), SRC, "exec"), _MM)
    _mut_fb = _MM["_pj_status_fallback"]
    _mut_leak = _mut_fb("こんにちは", vault_src="x.md", vault_fulltext="本文Y")
    chk("★⑦ 突然変異(vault-only分岐削除)でゲートが欠陥を検出する(空文字列に戻る)",
        _mut_leak, "")
else:
    chk("★⑦ 突然変異パッチが対象コードに一致せず適用不能(要目視確認)", False, True)

# ══════════════════════════════════════════════════════════════════════════
# ⑧ cmd_492 5便是正(将軍実測2026-08-05): 「キャスパーって携帯で見れるの？」(canon_turn=True・
#    Casper自身の使い方を尋ねるturn)はtop_source()のtrigram noise(閾値0.32ゆえ無関係な議事録等にも
#    常に何か当たる)をdoc topicとして記録してはならない——記録すると次の正当な引き継ぎ質問
#    (例:「どうやってみることが出来るの？」)がその無関係docへ誤誘導され、「読み取れなかった」と
#    だけ答えて終わる退行を招く(将軍実測で3/3回再現・本便で発見・是正)。
# ══════════════════════════════════════════════════════════════════════════
_resolve_topic = M["_resolve_turn_topic"]
# 本系統は「対象不要(needs_prior_context=False)」turnのdoc記録経路を検査する対象ゆえ、Falseへ固定する
# (LLM分類器の揺らぎを排し、canon_turnガードの実効性そのものを検査する・掟「純機構・インメモリ」)。
M["_needs_prior_context_llm"] = lambda q: False

chk("⑧ 引き継ぎ成立済(handoff_topic有り)は前対象を維持(Noneを返し上書きしない)",
    _resolve_topic("工数を教えて", {"kind": "project", "key": "Zenith", "label": "Zenith"}, None, False, "noise.md"),
    None)
chk("⑧ 聞き返し合成成立(pq_new_topic有り)は新対象で上書き",
    _resolve_topic("Zenithです", None, {"kind": "project", "key": "Zenith", "label": "Zenith"}, False, "noise.md"),
    {"kind": "project", "key": "Zenith", "label": "Zenith"})
chk("⑧ 対象不要+PJ/人物いずれも不成立+canon_turn=True(本便の主眼): top_source noiseをdoc記録しない",
    _resolve_topic("キャスパーって携帯で見れるの？", None, None, True, "10_meetings/mtg_17_GS 検証 会議.md"),
    None)
chk("⑧ 対象不要+PJ/人物いずれも不成立+canon_turn=False: 従来通りtop_sourceをdocとして記録する(退行なし)",
    _resolve_topic("こんにちは", None, None, False, "10_meetings/mtg_17_GS 検証 会議.md"),
    {"kind": "doc", "key": "10_meetings/mtg_17_GS 検証 会議.md", "label": "10_meetings/mtg_17_GS 検証 会議.md"})
chk("⑧ canon_turn=Trueでもsrc_resolvedが無ければ元々Noneのまま(退行判定に無関係な経路)",
    _resolve_topic("キャスパーって携帯で見れるの？", None, None, True, None),
    None)

# ★突然変異検証: ⑧是正(canon_turnガード)を無効化すると、canon_turn=Trueでもtop_source noiseが
# 再びdoc topicとして記録される(=実効性の実証)。
_mut8_src = _orig_src.replace(
    "    if canon_turn:\n"
    "        return None\n",
    "    # MUTATED: canon_turn guard disabled\n")
_mut8_applied = _mut8_src != _orig_src
if _mut8_applied:
    _m8tree = ast.parse(_mut8_src)
    _m8picked = [n for n in _m8tree.body if getattr(n, "name", None) == "_resolve_turn_topic"]
    _MM8 = dict(M)
    exec(compile(ast.Module(body=_m8picked, type_ignores=[]), SRC, "exec"), _MM8)
    _mut8_fn = _MM8["_resolve_turn_topic"]
    _mut8_leak = _mut8_fn("キャスパーって携帯で見れるの？", None, None, True,
                           "10_meetings/mtg_17_GS 検証 会議.md")
    chk("★⑧ 突然変異(canon_turnガード削除)でゲートが欠陥を検出する(noise docが再びdoc記録される)",
        _mut8_leak,
        {"kind": "doc", "key": "10_meetings/mtg_17_GS 検証 会議.md",
         "label": "10_meetings/mtg_17_GS 検証 会議.md"})
else:
    chk("★⑧ 突然変異パッチが対象コードに一致せず適用不能(要目視確認)", False, True)

n = len(results); p = sum(results)
print(f"\n=== gate_context_handoff: {p}/{n} = {p*100//n if n else 0}% ===")
sys.exit(0 if p == n else 1)
