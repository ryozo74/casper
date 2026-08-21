#!/usr/bin/env python3
"""cmd_510第2便(実害B機構化)回帰試験: dm_threads_digest の母集合なき不在断言の禁を機械で検査する。
pytest不使用(test_casper_failover.py/test_casper_handoff.pyの慣例に合わせ、素朴なassert方式)。

実害B: L5590 BASE_SYS内「DMの内容を問われたらget_messagesで都度取得してから答えよ」は
プロンプト文字列であり機構ではない。dm_threads(L3198)という決定的読取機構は既に在るが、
DM問合せturnでこれを注入するdigestが存在しなかった(掟「服従でなく機構で強制」の未施工箇所)。

AC4: 「今日TimからDMが来てると思うけど見せて」に対し、母集合つきで答えることを実測で示す。
     実在する投稿を「届いておりません」と断じたら赤。

Usage: python3 test_dm_threads_digest.py
"""
import datetime
import json
import os
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


class _FakeMCP:
    """casper_mcp.call_tool の代替: get_messages を thread_id有無で分岐する最小スタブ。"""

    def __init__(self, threads, messages_by_thread):
        self.threads = threads
        self.messages_by_thread = messages_by_thread

    def call_tool(self, name, args, token=None, actor=None):
        if name != "get_messages":
            return "{}"
        tid = args.get("thread_id")
        if tid is None:
            return json.dumps({"threads": self.threads})
        return json.dumps({"messages": self.messages_by_thread.get(int(tid), [])})


def _today():
    return datetime.date.today().isoformat()


def _mk_threads():
    """殿実害の再現材料: Tim(uid=99)との1:1スレッド(本日更新・相手発未読1件)を含む3スレッド構成。
    ・thread 1: Tim(uid=99)との1:1、本日更新、相手発未読あり=実在の投稿(これを「届いておりません」と
      断じたら赤=殿の2026-08-18 12:31実害の再現)。
    ・thread 2: 別の1:1(uid=55)、旧い更新、既読(未読なし)。
    ・thread 3: 3者スレッド(uid=99,55)、本日更新、未読あり。"""
    today_ts = f"{_today()}T12:00:00"
    old_ts = "2026-08-01T09:00:00"
    threads = [
        {"thread_id": 1, "updated_at": today_ts, "last_message": "打合せの件です",
         "participants": [{"user_id": 1, "name": "殿"}, {"user_id": 99, "name": "Tim"}]},
        {"thread_id": 2, "updated_at": old_ts, "last_message": "了解です",
         "participants": [{"user_id": 1, "name": "殿"}, {"user_id": 55, "name": "kiyotomo"}]},
        {"thread_id": 3, "updated_at": today_ts, "last_message": "三者相談",
         "participants": [{"user_id": 1, "name": "殿"}, {"user_id": 99, "name": "Tim"}, {"user_id": 55, "name": "kiyotomo"}]},
    ]
    messages_by_thread = {
        1: [{"sender_id": 99, "created_at": today_ts, "read_at": None}],       # 相手発・未読=実在の投稿
        2: [{"sender_id": 1, "created_at": old_ts, "read_at": old_ts}],        # 自分発・既読
        3: [{"sender_id": 99, "created_at": today_ts, "read_at": None}],       # 相手発・未読
    }
    return threads, messages_by_thread


def _mk_empty_threads():
    """0件分岐の再現材料: DMスレッドが1件も無い(get_messagesがthreads=[]を返す)構成。
    ★このスタブでのみ dm_threads_digest の0件分岐(L3844-3848)が踏まれる。
    従来 _mk_threads() 一本槍では0件分岐に一本も試験の足が無く、将軍検品にて
    独自変異(母集合ヘッダ除去)を試したところ全緑のまま通ってしまう検査の穴が発覚した
    (cmd_510 subtask_510_impl2是正)。"""
    return [], {}


def _who():
    return {"authed": True, "uid": 1}


def with_fake_env(fn, threads_factory=_mk_threads):
    old_mcp = C.casper_mcp
    old_token = C.WRITE_TOKEN
    old_roster = dict(C._ROSTER_MAP)
    threads, msgs = threads_factory()
    C.casper_mcp = _FakeMCP(threads, msgs)
    C.WRITE_TOKEN = "test-token"
    C._ROSTER_MAP.clear()
    C._ROSTER_MAP.update({"99": "Tim", "55": "kiyotomo"})
    try:
        fn()
    finally:
        C.casper_mcp = old_mcp
        C.WRITE_TOKEN = old_token
        C._ROSTER_MAP.clear()
        C._ROSTER_MAP.update(old_roster)


def test_ac4_person_specified_shows_population_not_denial():
    """AC4本体: 「今日TimからDMが来てると思うけど見せて」に対し、母集合つきで答え、
    実在の投稿(thread 1)を『届いておりません』で握り潰さぬこと。"""
    def run():
        out = C.dm_threads_digest(_who(), "今日Timから DMが来てると思うけど見せて")
        check("AC4-1: digestが発火する(空文字でない)", bool(out), f"len={len(out)}")
        check("AC4-2: 母集合ヘッダに全件数(N)が出る", ("全" in out and "件" in out), out[:200])
        check("AC4-3: Timの実在スレッド(thread 1)の中身が言及される", ("打合せ" in out or "Tim" in out), out[:300])
        # ★実害の核心: 実在スレッドがある(rows非空)のに、機構が不在を断定する行を書いていないこと。
        # (末尾の「母集合を示した後でなければ言えぬ」という*禁止*指示への言及は許容——それ自体は不在断定ではない)
        deny_lines = [ln for ln in out.splitlines() if "届いておりません" in ln and "断ずるな" not in ln and "言えぬ" not in ln]
        check("AC4-4: 実在スレッドありの場合に機構が不在断定行を書いていない", not deny_lines, deny_lines)
    with_fake_env(run)


def test_gate_route1_person_specified():
    """経路1(人物指定つき): roster閉集合の人名 ∧ DM語 ∧ 読取意図で発火。"""
    def run():
        out = C.dm_threads_digest(_who(), "Timからのメッセージ見せて")
        check("経路1: 人物指定ありで発火", bool(out))
    with_fake_env(run)


def test_gate_route2_no_person():
    """経路2(人物指定なし): DM語 ∧ 読取意図(一覧要求)で発火。"""
    def run():
        out = C.dm_threads_digest(_who(), "DM見せて")
        check("経路2: 人物指定なしでも発火(一覧要求)", bool(out))
        out2 = C.dm_threads_digest(_who(), "メッセージ未読ある？")
        check("経路2b: DM語(メッセージ)を伴う一覧要求でも発火", bool(out2))
    with_fake_env(run)


def test_gate_no_fire_on_send_intent():
    """送信意図のturnでは(読取意図が無ければ)発火しない、または少なくとも送信を妨げない設計であること。
    ここでは『DM語はあるが読取意図が無い』turnで暴発しないことのみ検査する(過剰発火の抑制)。"""
    def run():
        out = C.dm_threads_digest(_who(), "kiyotomoにDM送っといて、内容は明日13時に打合せです")
        check("読取意図の無いDM送信turnでは発火しない(過剰発火の抑制)", out == "", out[:200])
    with_fake_env(run)


def test_population_header_counts():
    """母集合ヘッダ: 全N件・1:1のM件・3者以上除外時のL件が件数つきで出ること。"""
    def run():
        out = C.dm_threads_digest(_who(), "DM見せて")
        check("全3件が母集合ヘッダに出る(全N件)", "3" in out, out[:300])
    with_fake_env(run)


def test_unrostered_name_falls_through():
    """rosterに無い名は経路1として解決させず(cmd_508 AC2の人物スロット検問に委ねる=既存機構の再利用)、
    人物を主語に立てない形に倒れること(=経路2相当かdigest非発火のいずれかで、捏造した人物への言及が無い)。"""
    def run():
        out = C.dm_threads_digest(_who(), "Xylophoneという人からDM来てる？見せて")
        check("roster外の名を主語に立てない(捏造人物名を本文に出さない)", "Xylophone" not in out, out[:200])
    with_fake_env(run)


def test_zero_threads_requires_population_header():
    """★cmd_510 subtask_510_impl2是正の本体: 0件分岐(_mk_empty_threads・スレッドが1件も無い)で
    dm_threads_digestを呼んだ際、★母集合ヘッダ(全0件を照会した旨)を示した上でのみ
    『該当スレッドは0件』相当の文言が出ること。母集合ヘッダを伴わない不在断言は禁(実害Bの急所)。"""
    def run():
        out = C.dm_threads_digest(_who(), "DM見せて")
        check("0件分岐-1: digestが発火する(空文字でない)", bool(out), f"len={len(out)}")
        check("0件分岐-2: 母集合ヘッダに全件数(全0件)が出る",
              ("全0件" in out or ("全" in out and "0件" in out)), out[:300])
        check("0件分岐-3: 『該当スレッドは0件』相当の不在言及がある", "0件" in out, out[:300])
        # ★検査の核心: 母集合ヘッダ(「全◯件照会」の言明)を伴わずに不在断言だけが出ることを禁ずる。
        # ヘッダ無しで不在断言のみの行があれば、それは実害Bの急所(母集合なき不在断言)そのもの。
        header_present = ("照会" in out and "件" in out)
        deny_present = ("0件であった" in out or "届いておりません" in out)
        check("0件分岐-4: 不在断言があるなら必ず母集合ヘッダ(照会済み件数)を伴う",
              (not deny_present) or header_present,
              f"header_present={header_present} deny_present={deny_present} out={out[:300]}")
    with_fake_env(run, threads_factory=_mk_empty_threads)


def main():
    tests = [
        test_ac4_person_specified_shows_population_not_denial,
        test_gate_route1_person_specified,
        test_gate_route2_no_person,
        test_gate_no_fire_on_send_intent,
        test_population_header_counts,
        test_unrostered_name_falls_through,
        test_zero_threads_requires_population_header,
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
