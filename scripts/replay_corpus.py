#!/usr/bin/env python3
"""cmd_510第3便(観測の機構・Fable「次に建てるべきは答えの機構でなく観測の機構」):
実ログ全turnをdigest組立(build_digests)に通し、どの機構が発火したかをturnごとに列挙する検査。

★守秘(軍師point_c設計): 実ログ本文はqueue/evidence/配下(=リポジトリ外)から動かさず、
それを読む口をload()一つに閉じる。他のどこからも直接openしない。
出力(run()の戻り値・print_report())は機構名/turn番号/赤緑のみに限り、本文は一切含めない。

★アクセスの単一化: load()は「そこに在るもの全部」(queue/evidence/配下の*.jsonファイル全て)を
回す設計とし、将来の増補(新しい日付名のログが増える)を一覧の手書きなしに拾う。

★意図外の発火の定義: decision_record(cmd_508 AC8)が既に「機構が実際に何を引いたか」を
turnごとに機械的に記録している。本モジュールはそれを流用し、各機構が持つ最小限の発火条件
(既存正規表現・既存判定関数)をuser発話へ独立に照合し直すことで、「digestは発火したが、
その発火条件をこのturnのuser発話が満たしていない」場合を意図外(unexpected)として検出する。
新しい判定機構は作らない——各digestが内部で使っている既存の条件関数/正規表現をここでも
呼ぶだけである(単一ソースの再利用)。

cmd_511第1便(軍師戦略review subtask_511_strategy1準拠・三盲点対策):
  盲点3(認証盲)対策: who に authed:True を与える。ただし★MCP実呼びは casper_mcp.call_tool
    を replay専用スタブへ差し替えることで止める(authed付与とスタブ注入は必ず同時)。
  盲点2(出口層未検査)対策: 出口層三関数(_turn_is_send_intent/_send_mention_line_hit/
    _resolve_send_mentions ※brief記載の_resolve_held_send_linesは本体では
    _resolve_send_mentionsの名で実装されている・同一シグネチャ)をreplayへ含める。
  盲点1(同語反復)対策そのものはAC1(門の突然変異→赤化確認)で機械的に証明する
    (このモジュール自身は既存条件の再利用のみで、証明は複製上の変異試験が担う)。

Usage: python3 replay_corpus.py
"""
import copy
import glob
import json
import os
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 瑕疵C是正で判明した副作用: chat_server.py はモジュール読込時(import時)にargparseで
# sys.argvをparse_args()する(--endpoint/--model/--portのみ許容)。本モジュール自身が
# 受け取る --live-llm 等の独自フラグがsys.argvに残ったままimportすると、chat_server側の
# parserがunrecognized argumentsとして即クラッシュする(main()に辿り着く前に落ちる)。
# ゆえにimport前に本モジュール専用フラグをsys.argvから除いておく(main()側は元のargvを
# 別途保持して判定に使う)。
_OWN_FLAGS = ("--live-llm", "--live-mcp")
_argv_flags = [a for a in sys.argv[1:] if a in _OWN_FLAGS]
sys.argv[1:] = [a for a in sys.argv[1:] if a not in _OWN_FLAGS]

import chat_server as C

# ★実ログの実体はqueue/evidence/配下(=リポジトリ外・symlink先)。ここでしかopenしない。
_EVIDENCE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "..", "queue", "evidence"))

# 機構名→「この機構が発火してよい最小条件」を検査する関数。既存のdigest内部で使っている
# 条件判定(正規表現/解決関数)をそのまま呼ぶ(新語彙表・新判定は作らない)。
# 条件が未知の機構(ここに列挙のない名)は判定対象外とする(false unexpectedを出さない=fail-safe)。
_EXPECTATION_CHECKS = {
    "dm_threads": lambda q: bool(C._DM_WORD_RE.search(q or "") and C._EXIST_Q_RE.search(q or "")),
    "existence": lambda q: bool(C._EXIST_Q_RE.search(q or "")),
    # cmd_512第6便是正: active_tasks_digestの実ゲートは_ACTIVE_TASK_Q_RE(chat_server.py
    # L4595-4599・L4607)であり、_PJ_TASK_REは別機構(projectsのPJ別タスク一覧・L5577)の
    # ゲートで別の語彙。旧マッピングは別機構の条件を誤って流用しており、turn9/10の
    # 「不発火」は実際には正しい不発火(母集合の取り違えによる誤検知)だった。
    "active_tasks": lambda q: bool(C._ACTIVE_TASK_Q_RE.search(q or "")),
}

# ★point_b(2): dm_threads_digestはauthed付与で casper_mcp.call_tool("get_messages", ...) を
# 実際に叩く(軍師実測済)。常用replayではこれを止め、固定スタブへ差し替える(再現性・無負荷)。
# ★AC2(authed付与後の発火実測)だけは一度きりの実測として別途 --live-mcp で行う(スタブを外す)。
_STUB_THREADS = {
    "threads": [
        {"thread_id": 1, "updated_at": "2026-08-01T00:00:00", "participants": [
            {"user_id": 28}, {"user_id": 99}]},
    ]
}
_STUB_MESSAGES = {"messages": []}


class _StubCasperMCP:
    """★replay専用スタブ。casper_mcp.call_toolと同じ呼び出し形(引数/戻り値=JSON文字列)を
    真似るだけで、実際にはCalendarへ一切アクセスしない(point_b(2)・軍師戦略review)。"""

    def call_tool(self, name, args, token=None, actor=None):
        if name == "get_messages" and "thread_id" not in (args or {}):
            return json.dumps(_STUB_THREADS)
        if name == "get_messages":
            return json.dumps(_STUB_MESSAGES)
        return json.dumps({})


# ★手当6(cmd_512第1便・軍師実測: replay 8分52秒中8分41秒がLLM=_asks_about_casper_llm経由の
# _ollama_json呼出・13turn×40秒。しかもNone(判定不能)を返しており緑で通っていた)。
# replayではLLM分類器へ一切回さず、_asks_about_casperの入口にある既存の規則側の門
# (_pj_resolve/_QUESTION_FORM_RE/_REQUEST_FORM_RE=単一ソース)だけで判ずる。
# ★corpusへ固定値を書く方式は採らない(軍師裁定: 固定すべき正解が今存在しない・陳腐化する)。
# ★門を通過したturn(=「疑問形/依頼形」かつ「案件語なし」)は、Casper自身への問いの形を
# 満たしている以上、規則側の情報としてはTrueより他に判じようがない
# (LLMだけが持つ「案件の話か否かの意味理解」を欠く以上、新しい語彙・新しい閾値を足して
# 代用することはしない=既存の門をそのまま流用するだけ)。
def _replay_asks_about_casper_llm_stub(query):
    return True


def _replay_asks_about_casper(query):
    """C._asks_about_casperの規則側の門(_pj_resolve/_QUESTION_FORM_RE/_REQUEST_FORM_RE)を
    そのまま再利用し、LLM分類器(_asks_about_casper_llm)へは回さない版。"""
    q = query or ""
    if not q:
        return False
    if C._pj_resolve(q)[0] == "unique":
        return False
    if not (C._QUESTION_FORM_RE.search(q) or C._REQUEST_FORM_RE.search(q)):
        return False
    return _replay_asks_about_casper_llm_stub(q)


def load():
    """queue/evidence/配下の*.jsonファイル全てを読み、{"turns":[{ts,role,content}...]}の形へ結合して返す。
    ★実ログ本文を返す唯一の関数。呼出側はここから先、本文を外部出力(print/return)に含めてはならない。
    ★一覧を手書きせず、そこに在るファイル全部を対象とする(将来の増補への対応)。"""
    out = []
    for path in sorted(glob.glob(os.path.join(_EVIDENCE_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for t in data.get("turns", []):
            out.append(t)
    return out


def _replay_topic(query):
    """本番の_resolve_turn_topic相当を簡易再現する(replay専用・handoff/pending_question等の
    会話文脈依存部分は評価対象外のためTrue/None固定で省く)。決定的解決器(_pj_resolve/_resolve_persons/
    top_source相当)のうちreplayで再現可能な部分のみを使い、_record_anchorへ渡す形を本番と揃える。
    新たな推測機構は追加しない(既存の解決器の結果を横取りするだけ・本番_resolve_turn_topicと同じ作法)。"""
    _pj_st, _pj_names, _ = C._pj_resolve(query)
    if _pj_st == "unique":
        return {"kind": "project", "key": _pj_names[0], "label": _pj_names[0]}
    _ppl = C._resolve_persons(query)
    if len(_ppl) == 1:
        _puid, _pnm = _ppl[0]
        return {"kind": "person", "key": _puid, "label": _pnm}
    return None


def _replay_exit_layer(user_query, assistant_text):
    """★盲点2対策(軍師point_b): 出口層三関数を実ログのassistant応答文を材料にLLM無しで再生する。
    held_lines は本番(L10238/L10255相当)と同じ規則——send_intent_gateがTrueの時だけ、
    assistant応答の各行を_send_mention_line_hitへ通し、Trueの行を集める。
    pending_actions は replay では承認カードを起票せぬため常に空(=本番の「カード無し」経路を辿る・
    _resolve_send_mentionsが_DM_BODY_INCOMPLETE_MSGへ差し替える側)。三関数とも副作用なしゆえ安全に呼べる。"""
    gate = C._turn_is_send_intent(user_query)
    held = []
    if gate:
        for ln in (assistant_text or "").splitlines(keepends=True):
            if C._send_mention_line_hit(ln):
                held.append(ln)
    resolved = C._resolve_send_mentions(assistant_text or "", held, [])
    return {
        "send_intent_gate": gate,
        "held_line_count": len(held),
        "resolved_changed": resolved != (assistant_text or ""),
    }


def run(use_live_mcp=False, live_llm=False):
    """全turnをbuild_digests + anchor_digest + retry_fallback_digest + 出口層三関数へ通し、
    turnごとの発火機構と意図外発火の有無を列挙する。戻り値に本文は含めない(機構名/turn番号/赤緑のみ)。

    use_live_mcp=True の時のみ casper_mcp.call_tool を実物のまま使う(=AC2の一度きりの実測用)。
    既定Falseでは _StubCasperMCP へ差し替え、Calendarへの実アクセスを止める(point_b(2))。

    ★手当6: live_llm=False(既定)では C._asks_about_casper を規則側のみで判ずる
    _replay_asks_about_casper へ差し替え、_asks_about_casper_llm(=_ollama_json経由)を
    一切呼ばない。live_llm=True(週1回の --live-llm 専用)の時のみ本物のLLM判定へ戻し、
    規則側の判定との食い違いを台帳へ記録する(赤にはしない・記録のみ・陳腐化検知用)。"""
    turns = load()
    who = {"uid": 28, "authed": True}   # 殿(uid28)固定+盲点3対策のauthed付与(必ずスタブ注入と同時)

    _orig_mcp = C.casper_mcp
    if not use_live_mcp:
        C.casper_mcp = _StubCasperMCP()
    _orig_asks = C._asks_about_casper
    _llm_calls_before = C.ollama_json_call_count()
    if not live_llm:
        C._asks_about_casper = _replay_asks_about_casper
    try:
        # ★replay専用thread鍵(point_b(1))。本番の実threadキーを一切踏まない
        # (_LAST_ANCHOR/_LAST_PREDICATE/_RETRY_LOG/_DECLINE_LOG はthread別dictのため、
        #  本番キーを使うと本番利用者の錨・再打鍵記録を書き換える事故になる)。
        thr = "replay:corpus:0"
        C._LAST_ANCHOR.pop(thr, None)
        C._LAST_PREDICATE.pop(thr, None)
        C._RETRY_LOG.pop(thr, None)
        C._DECLINE_LOG.pop(thr, None)

        fired_rows = []
        turn_no = 0
        pending_assistant = None
        for i, t in enumerate(turns):
            if t.get("role") != "user":
                continue
            turn_no += 1
            content = t.get("content") or ""
            # 直後のcasper応答(role="casper")をこのturnの出口層材料として拾う(在れば)。
            assistant_text = ""
            if i + 1 < len(turns) and turns[i + 1].get("role") == "casper":
                assistant_text = turns[i + 1].get("content") or ""

            _dig_trace = {}
            C.build_digests(who, content, trace=_dig_trace)
            _ = C.anchor_digest(thr, who, content)
            _ = C.retry_fallback_digest(thr, who, content)
            # 本番と同じ更新順(L10424/10428相当): まず本turnの対象を錨として記録し、
            # 次に本turnの述語らしき語を次turn向けに記録する。
            C._record_anchor(thr, who, _replay_topic(content))
            C._record_predicate(thr, content)

            exit_layer = _replay_exit_layer(content, assistant_text)

            fired = list(_dig_trace.get("digests_fired") or [])
            unexpected = []
            evaluated = 0
            for name in fired:
                check = _EXPECTATION_CHECKS.get(name)
                if check is None:
                    continue
                evaluated += 1
                if not check(content):
                    unexpected.append(name)          # 発火したが条件を満たさぬ(過発火)
            # ★AC1対策: 「発火してよい」だけでなく「発火すべきに発火せなんだ」も同じ単一ソースで検査する
            # (門をreturn ""へ変異させて沈黙させる攻撃は、過発火側の検査だけでは捉えられぬ・盲点3と同型)。
            missing = []
            for name, check in _EXPECTATION_CHECKS.items():
                if name not in fired and check(content):
                    missing.append(name)
                    evaluated += 1

            # ★将軍検品是正(cmd_511第3便・至急): 出口層(exit_layer)はこれまでprint_reportに
            # 印字されるだけでverdictの判定式に入っておらず、_turn_is_send_intentを常時Trueへ
            # 変異させても(send_gate 1→19・held行 0→5)verdictはunexpected/missing側のみで
            # 決まるためred=2のまま不変という盲点があった(将軍実測で発覚)。
            # ★是正: held_line_count>0(=実際に本文行が保留され差替が起きた)は、それ自体が
            # 実際に本文が抑制された証跡であり、既存の単一ソース(_replay_exit_layerが返す値)
            # をそのまま判定に使うだけで新しい語彙・新しい判定機構は増やさない。
            # ★本evidenceコーパス(queue/evidence/配下)は殿の実被害turnのみで構成され、
            # 送信依頼(送信turn)を一件も含まない(load()時点の実データで確認済・全turn読取)。
            # ゆえheld_line_count>0はこのコーパスにおいて常に「読取turnで送信保留が誤発火した」
            # ことを意味し、意図外発火(exit_unexpected)として扱ってよい。
            # ★cmd_511追加便是正(軍師具申(2)): send_intent_gate単独(held=0でもgate=True)も
            # 同じ理由(全turn読取コーパス)でそれ自体が意図外発火。held側の判定だけでは
            # 「gate=True・held=0のまま緑」という辻褄崩れ(turn2)を検出できなかった盲点を塞ぐ。
            exit_unexpected = []
            if exit_layer.get("send_intent_gate"):
                exit_unexpected.append("exit_layer_send_gate")
                evaluated += 1
            if exit_layer.get("held_line_count", 0) > 0:
                exit_unexpected.append("exit_layer_held_lines")
                evaluated += 1

            fired_rows.append({
                "turn": turn_no,
                "mechanisms": fired,
                "unexpected": unexpected,
                "missing": missing,
                "exit_unexpected": exit_unexpected,
                "evaluated": evaluated,
                "unevaluated": len(fired) - (evaluated - len(missing)),
                "exit_layer": exit_layer,
                "verdict": "red" if (unexpected or missing or exit_unexpected) else "green",
            })

        llm_calls = C.ollama_json_call_count() - _llm_calls_before
        return {"n_turns": len(turns), "n_user_turns": turn_no, "fired": fired_rows,
                "llm_calls": llm_calls}
    finally:
        C.casper_mcp = _orig_mcp
        C._asks_about_casper = _orig_asks


def print_report(result):
    """機構名/turn番号/赤緑のみを出力する(本文を出さない・守秘の機械的担保)。
    ★軍師qc3_correction具申: 未評価数と被覆率を併記する。"""
    print(f"replay corpus: turns={result.get('n_turns')} user_turns={result.get('n_user_turns')}")
    n_red = 0
    n_red_digest = 0     # ★軍師補足(1): digest由来(unexpected/missing)とexit_layer由来を分けて集計
    n_red_exit = 0
    n_fired_total = 0
    n_evaluated_total = 0
    for row in result.get("fired", []):
        mark = "🔴" if row["verdict"] == "red" else "🟢"
        exl = row.get("exit_layer") or {}
        exit_note = f"  [出口層: send_gate={exl.get('send_intent_gate')} held={exl.get('held_line_count')}]"
        print(f"  {mark} turn {row['turn']}: {', '.join(row['mechanisms']) or '(発火なし)'}"
              + (f"  [意図外(過発火): {', '.join(row['unexpected'])}]" if row["unexpected"] else "")
              + (f"  [意図外(不発火): {', '.join(row['missing'])}]" if row.get("missing") else "")
              + (f"  [意図外(出口層): {', '.join(row['exit_unexpected'])}]" if row.get("exit_unexpected") else "")
              + exit_note)
        if row["verdict"] == "red":
            n_red += 1
        if row["unexpected"] or row.get("missing"):
            n_red_digest += 1
        if row.get("exit_unexpected"):
            n_red_exit += 1
        n_fired_total += len(row["mechanisms"])
        n_evaluated_total += row.get("evaluated", 0)
    n_unevaluated_total = n_fired_total - n_evaluated_total
    coverage = (n_evaluated_total / n_fired_total * 100) if n_fired_total else 0.0
    print(f"合計: {len(result.get('fired', []))}turn中 意図外発火 {n_red}件"
          f"（内訳: digest由来 {n_red_digest}件・出口層由来 {n_red_exit}件）")
    print(f"被覆率: {n_evaluated_total}/{n_fired_total}件発火中評価済み ({coverage:.1f}%)  未評価: {n_unevaluated_total}件")
    print(f"LLM呼出回数: {result.get('llm_calls')}件"
          + ("(手当6: 規則側のみで判定=0件のはず)" if result.get("llm_calls") == 0 else "★想定外(0件でない)"))
    # ★機械可読サマリ行(呼出元スクリプトのparse用・人向け行の文言変更に影響されない安定した形)。
    print(f"MACHINE_SUMMARY n_red={n_red} llm_calls={result.get('llm_calls')} "
          f"n_user_turns={result.get('n_user_turns')}")


_DIVERGENCE_LEDGER = os.path.join(HERE, "reports", "casper_howto_llm_divergence.jsonl")


def check_llm_divergence():
    """★週1回のみ(--live-llmオプション)実行: 規則側判定(_replay_asks_about_casper)と
    実LLM判定(C._asks_about_casper_llm)の食い違いをturnごとに台帳へ記録する(赤にはしない・
    記録のみ・規則側判定の陳腐化検知用)。門(gate)を通過したturn=LLMが実際に呼ばれるturnのみ対象。
    ★本文は台帳へ書かない(守秘・本モジュール冒頭の掟)。query長のみ記録する。"""
    turns = load()
    rows = []
    turn_no = 0
    for t in turns:
        if t.get("role") != "user":
            continue
        turn_no += 1
        content = t.get("content") or ""
        if not content:
            continue
        if C._pj_resolve(content)[0] == "unique":
            continue
        if not (C._QUESTION_FORM_RE.search(content) or C._REQUEST_FORM_RE.search(content)):
            continue
        rule_side = True                        # 門を通過した以上、規則側の判定は常にTrue(_replay_asks_about_casperと同じ)
        llm_side = C._asks_about_casper_llm(content)
        rows.append({
            "turn": turn_no, "query_len": len(content),
            "rule_side": rule_side, "llm_side": llm_side,
            "diverged": llm_side is not True,
        })
    n_diverged = sum(1 for r in rows if r["diverged"])
    return {"n_gated_turns": len(rows), "n_diverged": n_diverged, "rows": rows}


def _append_divergence_ledger(div_result, ts):
    os.makedirs(os.path.dirname(_DIVERGENCE_LEDGER), exist_ok=True)
    with open(_DIVERGENCE_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, **div_result}, ensure_ascii=False) + "\n")


def main():
    use_live_mcp = "--live-mcp" in _argv_flags
    live_llm = "--live-llm" in _argv_flags
    result = run(use_live_mcp=use_live_mcp)
    print_report(result)
    # ★AC8の機械証明: 規則側判定(既定経路)では_ollama_json呼出回数が0であることをassertで示す。
    # 「grepしてurlopenが無い」ではなく、実行後の実カウンタ値で証明する(間接呼出も捕捉)。
    assert result.get("llm_calls") == 0, (
        f"手当6是正: replay(規則側のみ)は_ollama_json呼出0件のはずが{result.get('llm_calls')}件発生した")

    if live_llm:
        import time as _time
        div = check_llm_divergence()
        print(f"[--live-llm] 規則側とLLM判定の食い違い: {div['n_diverged']}/{div['n_gated_turns']}件"
              f"(台帳へ記録: {_DIVERGENCE_LEDGER})")
        _append_divergence_ledger(div, ts=_time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    n_red = sum(1 for r in result.get("fired", []) if r["verdict"] == "red")
    sys.exit(1 if n_red else 0)


if __name__ == "__main__":
    main()
