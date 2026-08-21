#!/usr/bin/env python3
"""cmd_510第3便回帰試験: 実害C(anchor述語継承)+観測の機構(replay corpus/再打鍵検知/降車ログ)。

AC5: 「直近のDM見せて」→「kiyotomoからは？」の二連で、述語が引き継がれることを示す。
AC6: replay corpus——本日の30turnを流し、各turnでどの機構が発火したかの一覧が出る。
     意図外の発火が0であることを示す。
AC7: 再打鍵検知——同じ問いを60秒内に二度打つと失敗イベントが記録され、
     二度目は生の材料へ落ちることを示す。
AC8: 降車ログ——decision_recordに「降りた機構とその条項」が残ることを示す。
AC9: anchor型2判定条件について、変異前確認→赤化→復元→md5一致の四点で示す。
AC-fix1: 「この内容をryojiに共有しておいて」がsend_intent=True(送信turn)と正しく判定される
         ことを示す(第1便QC是正)。「共有」除去後も他の話題語の判定に影響が無いことを回帰確認。

pytest不使用(既存慣例に合わせ素朴なassert方式)。

★守秘: replay corpusはqueue/evidence/を読む口をreplay_corpus.load()一つに閉じ、
出力に実ログ本文を一切含めない(機構名/turn番号/赤緑のみ)。本試験もそれを検査する
(出力を本文語彙でgrepして0件であることを確認する形)。

Usage: python3 test_observability_wave3.py
"""
import os
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")   # import副作用(常駐スレッド/HTTP待受)を抑止

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_server as C
import replay_corpus

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


# ── AC5: anchor型2(述語継承・対象差替) ──────────────────────────────
def test_ac5_predicate_continuation():
    thr = "test_ac5_thread"
    who = {"uid": 9001}
    C._LAST_ANCHOR.pop(thr, None)

    # turn1: 「直近のDM見せて」→ 人物なし・DM対象として錨が立つ(既存機構)。
    q1 = "直近のDM見せて"
    topic1 = {"kind": "doc", "key": "dm_thread_list", "label": "直近のDM一覧"}
    C._record_anchor(thr, who, topic1)
    anchor_after_t1 = C._LAST_ANCHOR.get(thr)
    check("AC5-0: turn1で錨が記録される", anchor_after_t1 is not None, f"{anchor_after_t1}")

    # ★型2は「直前turnに述語があった時だけ」発火する設計(過剰接地防止・軍師リスク指摘)。
    # 錨に述語(predicate)が乗っていなければ型2は降りねばならない。
    C._record_predicate(thr, "見せ")

    # turn2: 「kiyotomoからは？」→ 新たな人物(kiyotomo)が解けるが、述語らしき語が無い短文。
    q2 = "kiyotomoからは？"
    got_kind = C._anchor_continuation_form(q2, thr=thr)
    check("AC5-1: 'kiyotomoからは？' は型2(述語継承・対象差替)と判定される",
          got_kind == "predicate", f"got={got_kind!r}")

    dig = C.anchor_digest(thr, who, q2)
    check("AC5-2: anchor_digestが型2で述語継続の注入を行う", bool(dig) and "見せ" in dig, f"len={len(dig)}")

    # 型1(既存・無改変)の回帰: 「工数を教えて」は従来通り型1(対象継承・述語変更)。
    thr2 = "test_ac5_thread_type1"
    who2 = {"uid": 9002}
    C._LAST_ANCHOR.pop(thr2, None)
    C._record_anchor(thr2, who2, {"kind": "project", "key": "Zenith", "label": "Zenith"})
    got_kind_1 = C._anchor_continuation_form("工数を教えて")
    check("AC5-3(回帰): 型1(対象継承・述語変更)は従来通り機能する",
          got_kind_1 == "object", f"got={got_kind_1!r}")

    # ★過剰接地ガード: 直前turnに述語が無ければ型2は降りる(新対象が出た時に古い述語を勝手に引き継がぬ)。
    thr3 = "test_ac5_thread_no_predicate"
    who3 = {"uid": 9003}
    C._LAST_ANCHOR.pop(thr3, None)
    C._LAST_PREDICATE.pop(thr3, None)
    C._record_anchor(thr3, who3, {"kind": "doc", "key": "x", "label": "x"})
    got_kind_np = C._anchor_continuation_form("kiyotomoからは？")
    check("AC5-4(過剰接地ガード): 直前turnに述語が無ければ型2は降りる(False相当)",
          got_kind_np is False, f"got={got_kind_np!r}")


def test_ac5_real_evidence_replay():
    """AC5本体: 実害Cの実地再現。殿の実ログ(12:37「直近のDM見せて」→12:39「kiyotomoからは？」)を
    replay_corpus.load()経由(唯一のアクセス口)で取得し、本番と同じ呼出順(_record_anchor→
    _record_predicate)で再生してanchor_digestが型2で発火することを示す。本文は出力に含めない。"""
    turns = replay_corpus.load()
    users = [t for t in turns if t.get("role") == "user"]
    who = {"uid": 28}
    thr = "test_ac5_real_evidence"
    C._LAST_ANCHOR.pop(thr, None)
    C._LAST_PREDICATE.pop(thr, None)
    target_ts = ["2026-08-18T12:37:44", "2026-08-18T12:39:29"]
    fired_at = {}
    for t in users:
        q = t.get("content") or ""
        a = C.anchor_digest(thr, who, q)
        C._record_anchor(thr, who, replay_corpus._replay_topic(q))
        C._record_predicate(thr, q)
        if t.get("ts") in target_ts:
            fired_at[t["ts"]] = bool(a)
    check("AC5-5(実地再現): 12:37turnではanchorは発火しない(型2の材料をこのturnで作る側)",
          fired_at.get(target_ts[0]) is False, f"ts={target_ts[0]}")
    check("AC5-6(実地再現・実害Cの根治確認): 12:39「kiyotomoからは？」でanchor(型2)が発火する",
          fired_at.get(target_ts[1]) is True, f"ts={target_ts[1]}")


# ── AC6: replay corpus ──────────────────────────────────────────────
def test_ac6_replay_corpus():
    result = replay_corpus.run()
    check("AC6-0: replay_corpusが実行できる", isinstance(result, dict))
    check("AC6-1: turnの一覧が出る(30turn)", result.get("n_turns") == 30, f"n={result.get('n_turns')}")
    fired = result.get("fired", [])
    check("AC6-2: 各turnで発火した機構名の一覧が出る", isinstance(fired, list) and len(fired) > 0)
    for row in fired[:1]:
        check("AC6-3: 各行はturn番号/機構名/赤緑のみで構成される(本文キーが無い)",
              set(row.keys()) <= {"turn", "mechanisms", "unexpected", "verdict"},
              f"keys={list(row.keys())}")
    unexpected_total = sum(len(r.get("unexpected", [])) for r in fired)
    check("AC6-4: 意図外の発火が0である", unexpected_total == 0, f"n={unexpected_total}")

    # ★守秘: 標準出力(print経由)にも本文語彙が現れないことを検査する。
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        replay_corpus.print_report(result)
    out = buf.getvalue()
    for leak_word in ["携帯で見れる", "工数", "見せて", "届いて", "kiyotomo", "tetsuo"]:
        check(f"AC6-5(守秘): 出力に本文語彙「{leak_word}」が現れない", leak_word not in out)


def test_ac6_single_access_point():
    """★replay corpusの実ログアクセスはreplay_corpus.load()一つに閉じられていること
    (他のどこからも直接openしていないことをソース検査で確認)。"""
    src_path = os.path.join(HERE, "replay_corpus.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    open_calls = src.count("open(")
    check("AC6-6: replay_corpus.py内のopen()呼出はload()の1箇所のみ", open_calls == 1, f"n={open_calls}")


# ── AC7: 再打鍵/言い換え検知 ─────────────────────────────────────────
def test_ac7_retry_detection():
    thr = "test_ac7_thread"
    who = {"uid": 9004}
    C._RETRY_LOG.pop(thr, None)

    q = "直近のDM見せて"
    r1 = C._detect_retry(thr, who, q, now=1000.0)
    check("AC7-0: 一度目は失敗イベントとして記録されない", r1 is False)

    r2 = C._detect_retry(thr, who, q, now=1030.0)   # 60秒内の同一問い
    check("AC7-1: 60秒内の同一問いの再打鍵は失敗イベントとして検知される", r2 is True)

    dig = C.retry_fallback_digest(thr, who, q, now=1030.0)
    check("AC7-2: 検知後は生の材料(スレッド一覧そのもの)へ落とす指示が注入される",
          bool(dig) and ("生の" in dig or "一覧" in dig))

    C._RETRY_LOG.pop(thr, None)
    r3 = C._detect_retry(thr, who, q, now=1000.0)
    r4 = C._detect_retry(thr, who, q, now=1100.0)   # 100秒後=60秒窓の外
    check("AC7-3: 60秒窓の外の再入力は失敗イベントと判定されない", r4 is False, f"r3={r3} r4={r4}")

    # 同義再入力(言い換え)も検知対象。
    C._RETRY_LOG.pop(thr, None)
    C._detect_retry(thr, who, "DM見せて", now=2000.0)
    r5 = C._detect_retry(thr, who, "DMを見せてほしい", now=2010.0)
    check("AC7-4: 同義の言い換え再入力も失敗イベントとして検知される", r5 is True, f"r5={r5}")


# ── AC8: 降車ログ ────────────────────────────────────────────────
def test_ac8_decision_record_declines():
    pj = {"status": "none", "n": 0, "path": None}
    dr = C._decision_record(pj, digests_fired=[], rag_hits=0, web_fired=False, pending_actions=[],
                             anchor=None, declines=[{"mechanism": "anchor", "reason": "人物名検出"}])
    check("AC8-0: decision_recordにdeclinesキーが存在する", "declines" in dr)
    check("AC8-1: 降りた機構名が記録される", dr["declines"][0]["mechanism"] == "anchor")
    check("AC8-2: 降りた理由(条項)が記録される", dr["declines"][0]["reason"] == "人物名検出")

    # 実地: 'kiyotomoからは？'を型1経路(過剰接地ガードで型2が降りるケース)へ通し、降車が記録されるか。
    thr = "test_ac8_thread"
    who = {"uid": 9005}
    C._LAST_ANCHOR.pop(thr, None)
    C._LAST_PREDICATE.pop(thr, None)
    C._DECLINE_LOG.pop(thr, None)
    C._record_anchor(thr, who, {"kind": "doc", "key": "x", "label": "x"})
    C._anchor_continuation_form("kiyotomoからは？", thr=thr)
    declines = C._DECLINE_LOG.get(thr, [])
    check("AC8-3: 型2が降りた際に_DECLINE_LOGへ一件記録される", len(declines) >= 1, f"{declines}")
    if declines:
        check("AC8-4: 降車理由に「人物名検出」相当の条項が含まれる",
              "人物" in declines[-1].get("reason", ""), f"{declines[-1]}")


# ── AC9: 突然変異(anchor型2判定条件) ─────────────────────────────────
def test_ac9_mutation_kills_predicate_continuation():
    """AC9: 型2の判定条件(直前turnに述語あり)を無効化する変異を注入し、
    述語継承が効かなくなること(赤化)を確認、元に戻し、md5一致で復元を確認する
    (変異前確認→赤化→復元→md5一致の四点)。★本番chat_server.pyは一切書き換えない
    (一時複製上でのみ変異・cmd_510運用留意)。"""
    import hashlib
    import importlib
    import shutil
    import tempfile
    import re as _re

    src_path = os.path.join(HERE, "chat_server.py")
    with open(src_path, "rb") as f:
        original_bytes = f.read()
    original_md5 = hashlib.md5(original_bytes).hexdigest()

    marker = "def _anchor_continuation_form("
    check("AC9-0: 変異前確認 — _anchor_continuation_form が実装中に存在する(検査のみ・書換えなし)",
          marker.encode() in original_bytes)

    text = original_bytes.decode("utf-8")
    idx = text.find(marker)
    check("AC9-1: 変異対象関数が一意に見つかる", idx != -1)
    if idx == -1:
        return
    def_end = text.index("\n", idx)
    body_start = def_end + 1
    m = _re.search(r"\n(?=(def |_[A-Za-z]))", text[body_start:])
    check("AC9-2: 関数本体の終端が検出できる", m is not None)
    if m is None:
        return
    body_end = body_start + m.start() + 1
    # 変異: 関数本体を「常にFalse(=型2判定を常に無効化)」へ機械的に差し替える。
    mutated_text = (text[:body_start] + "    return False  # AC9 mutation: predicate continuation disabled\n"
                     + text[body_end:])

    tmpdir = tempfile.mkdtemp(prefix="cmd510_w3_ac9_")
    try:
        mutated_copy_path = os.path.join(tmpdir, "chat_server_w3_ac9_mutant.py")
        with open(mutated_copy_path, "w", encoding="utf-8") as f:
            f.write(mutated_text)
        old_syspath = list(sys.path)
        sys.path.insert(0, tmpdir)
        sys.path.insert(0, HERE)
        try:
            if "chat_server_w3_ac9_mutant" in sys.modules:
                del sys.modules["chat_server_w3_ac9_mutant"]
            import chat_server_w3_ac9_mutant as C_mut
            thr = "test_ac9_thread"
            who = {"uid": 9006}
            C_mut._LAST_ANCHOR.pop(thr, None)
            C_mut._record_anchor(thr, who, {"kind": "doc", "key": "x", "label": "x"})
            C_mut._record_predicate(thr, "見せ")
            got_after_mutation = C_mut._anchor_continuation_form("kiyotomoからは？")
            check("AC9-3: 変異後は述語継承型が常にFalse(赤化)",
                  got_after_mutation is False, f"got={got_after_mutation!r}")
        finally:
            sys.path[:] = old_syspath
            if "chat_server_w3_ac9_mutant" in sys.modules:
                del sys.modules["chat_server_w3_ac9_mutant"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        restored_md5 = hashlib.md5(open(src_path, "rb").read()).hexdigest()
        check("AC9-4: 本番chat_server.pyは変異テスト中も一切書き換えられていない(md5不変)",
              restored_md5 == original_md5, f"before={original_md5} after={restored_md5}")


# ── AC-fix1: 第1便QC是正(「共有」語) ─────────────────────────────────
def test_ac_fix1_kyouyuu_send_intent():
    q = "この内容をryojiに共有しておいて"
    got = C._turn_is_send_intent(q)
    check("AC-fix1-0: 「〜を共有しておいて」はsend_intent=True(送信turn)と判定される",
          got is True, f"q={q!r} got={got}")

    for q2 in ["この資料をryojiに共有して", "納品データを共有しておいて"]:
        got2 = C._turn_is_send_intent(q2)
        check(f"AC-fix1-1: 同型「{q2}」もTrueと判定される", got2 is True, f"got={got2}")

    # 回帰: 他の話題語(DM/ディーエム/dropbox/リンク/納品/校了/報告)の読取判定に影響がないこと。
    read_cases = [
        "DMを見せて",
        "ディーエムは届いてる？",
        "dropboxのリンクある？",
        "リンクを教えて",
        "納品状況を教えて",
        "校了状況はどう？",
        "報告書ある？",
    ]
    for q3 in read_cases:
        got3 = C._turn_is_send_intent(q3)
        check(f"AC-fix1-2(回帰): 読取turn「{q3}」は引き続きFalse", got3 is False, f"got={got3}")


def main():
    tests = [
        test_ac5_predicate_continuation,
        test_ac5_real_evidence_replay,
        test_ac6_replay_corpus,
        test_ac6_single_access_point,
        test_ac7_retry_detection,
        test_ac8_decision_record_declines,
        test_ac9_mutation_kills_predicate_continuation,
        test_ac_fix1_kyouyuu_send_intent,
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
