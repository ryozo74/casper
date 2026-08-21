#!/usr/bin/env python3
"""Casper 評価ハーネス(holdout 16問) — cmd_500の実装。

殿裁可済の holdout.json(実発話16問)を実プロセス(localhost:8770/api/chat)へ投じ、
criteria.json の機械条件で pass/fail/review の三値判定を行う。holdout.json は
一切書き換えない(参照のみ)。学習・自動生成には使わない(eval/README.md鉄則)。

使い方:
  python3 run_holdout.py                    # 第1巡のみ(既定)
  python3 run_holdout.py --round2           # 第1巡でfail/reviewだった問だけ第2巡(flaky検出)
  python3 run_holdout.py --uid 30           # 全問を指定uidで(既定は各問srcのuidを自動解決)

出力: eval/results/<run_id>.json (型別pass/fail/review/flaky件数、不合格問の応答全文、
holdout.jsonのsha256、実行時刻、uid、所要)。
"""
import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CASPER_DIR = os.path.dirname(HERE)                          # projects/casper/scripts
sys.path.insert(0, CASPER_DIR)
import casper_breaker                                        # cmd_509第2便: allow()ガード用

# cmd_507症状②: chat_server.pyをimportした際に常駐スレッド+HTTP待受が起動し、検証プロセスと
# 本番が状態ファイル/portを奪い合う件の抑止。setdefaultゆえ呼び出し元が明示指定していれば尊重する。
os.environ.setdefault("CASPER_NO_DAEMON", "1")

ENDPOINT = os.environ.get("CASPER_EVAL_ENDPOINT", "http://localhost:8770/api/chat")
CONFIRM_ENDPOINT = os.environ.get("CASPER_EVAL_CONFIRM_ENDPOINT", "http://localhost:8770/api/confirm")
HOLDOUT_JSON = os.path.join(HERE, "holdout.json")
CRITERIA_JSON = os.path.join(HERE, "criteria.json")
OUTBOX_JSONL = os.path.join(CASPER_DIR, "casper_outbox.jsonl")
RESULTS_DIR = os.path.join(HERE, "results")
REPORTS_DIR = os.path.join(CASPER_DIR, "reports")
LOCK_PATH = os.path.join(REPORTS_DIR, "_holdout.lock")
ENDPOINTS_ENV = os.path.join(CASPER_DIR, "casper_endpoints.env")

_INTERRUPTED_BY_RELOAD = False


def _acquire_lock():
    """cmd_507第1便: supervisor auto-reloadとholdout測定の干渉を断つロック。
    1行目にepoch秒(ts)のみ・2行目以降にpid/whoを添える(シェルからも読みやすい形・軍師進言)。
    mtimeでなく中身のtsを見ることをsupervisor側に要求する(mtimeは触られうるため)。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\n")
        f.write(f"pid={os.getpid()}\n")
        f.write("who=run_holdout\n")


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def _signal_handler(signum, frame):
    _release_lock()
    sys.exit(128 + signum)

# src の人物名 → Casper uid(/api/users で実測・2026-08-06)。殿=ryoji。
SRC_UID = {"tetsuo": "30", "殿": "28", "ryoji": "28", "kiyotomo": "31", "ou": "36",
           "将軍作成": "28"}                                 # 将軍作成(退行検査問)は殿相当のuidで代表させる
DEFAULT_UID = "28"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _current_gen_key():
    """casper_endpoints.envのCASPER_OLLAMA(=supervisorがchat_server起動に使う宛先=真実源)から
    breakerのgen_keyを組む。★台帳を読む口はcasper_breaker.pyただ一つ——ここでもbreaker.jsonを
    直接パースせず、casper_breaker.gen_key()を呼ぶ。"""
    endpoint = "http://192.168.44.119:11434"
    if os.path.exists(ENDPOINTS_ENV):
        with open(ENDPOINTS_ENV, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("CASPER_OLLAMA="):
                    endpoint = s.split("=", 1)[1].strip()
    hostport = endpoint.rstrip("/").split("://", 1)[-1]
    host, _, port = hostport.partition(":")
    return casper_breaker.gen_key(host, port or "11434")


def _resolve_uid(item, override=None):
    if override:
        return override
    src = item.get("src", "")
    for name, uid in SRC_UID.items():
        if src.startswith(name):
            return uid
    return DEFAULT_UID


def run_chat(messages, thread, uid):
    """/api/chat へPOSTし (text, cards) を復元。projects/casper/scripts/casper_eval.py の
    run_chat()と同一契約(NDJSONストリーム: message/replace/confirm/choices/table を復元)。"""
    body = json.dumps({"messages": messages, "thread": thread}).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json", "X-Actor-User-Id": uid})
    text, cards = "", []
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if "message" in o:
                text += (o["message"] or {}).get("content", "")
            elif "replace" in o:
                text = o.get("replace") or ""
            elif "confirm" in o:
                cards.append(o["confirm"])
            elif "choices" in o:
                cards.append({"tool": "choices", "choices": o["choices"]})
            elif "table" in o:
                cards.append({"tool": "table", "table": o["table"]})
    return text, cards


def _outbox_line_count():
    if not os.path.exists(OUTBOX_JSONL):
        return 0
    with open(OUTBOX_JSONL, encoding="utf-8") as f:
        return sum(1 for _ in f)


def reject_card(pid, uid):
    """★最優先事項: 却下は必ず {"id": pid, "approve": false} 形式で送る。
    brief記載の{"action":"reject"}は実装(chat_server.py L8818)がreq.get("approve")しか読まぬため
    使わない(軍師実測済)。"""
    body = json.dumps({"id": pid, "approve": False}).encode()
    req = urllib.request.Request(CONFIRM_ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json", "X-Actor-User-Id": uid})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _enum_line_count(text):
    return len(re.findall(r"(?m)^\s*\d+[\.\)]", text or ""))


_NEGATION_MARKERS = ("不要", "は要りません", "は不要です", "は無く", "ではなく", "しない", "せぬ", "不問", "必要ありません", "必要はありません")
_SENT_SPLIT = re.compile(r"[。！？\n]")


def _is_negated(text, word):
    """must_not語と同一の文(句点/改行区切り)に否定語が在るか(例: 『アプリストアでのダウンロードや
    招待URLは不要です』はng回避の正しい説明であり違反ではない)。
    smoketest(C02実測 2026-08-06)で判明した誤検知の是正: 素朴な部分文字列一致だけでは
    ng文言を正しく否定した回答まで fail 判定してしまう。前後12字の窓では『アプリストアでの
    ダウンロードや招待URLは不要です』のように主語が離れた否定に届かず、文単位に広げた。"""
    for sent in _SENT_SPLIT.split(text):
        if word in sent and any(neg in sent for neg in _NEGATION_MARKERS):
            return True
    return False


def judge(item_id, criteria, text, cards, outbox_delta_pids):
    """criteria.json の1問分の条件で (verdict, reasons) を返す。verdict は pass/fail/review。
    reasons は根拠(どのcriteria条件に当たったか)のlist(AC5: 軍師の裏取り用)。"""
    c = criteria.get(item_id) or {}
    decidable = c.get("decidable", "")
    reasons = []
    text = text or ""

    if c.get("untestable"):
        # cmd_500第2便・軍師裁定: holdoutのexpectを実際には測れておらぬ問をfail/passで水増し/
        # 水減しせず、第四区分untestableとして正直に申告する(点を上げるための緩和ではない)。
        # score算出の分母(pass+fail)からも除外する(summarize()側)。
        reasons.append(f"untestable: {c['untestable']}")
        return "untestable", reasons

    def _hit_any_negated(words):
        """否定除外あり。must_not専用: 『その語が悪い意味で使われたか』を問うため、
        文脈が否定されておれば違反ではない(is_negated判定が必要)。"""
        return [w for w in (words or []) if w in text and not _is_negated(text, w)]

    def _hit_any_plain(words):
        """否定除外なし。must_any/count_markers用: 『その語に触れたか』だけを問うため、
        同じ文に別件の否定語(『確認せぬ』『0件』等)があっても触れていれば充足する
        (欠陥A是正・cmd_500第2便: is_negatedをmust_not以外に掛けると、無関係な否定文脈に
        巻き込まれて正しい言及まで不充足と誤判定してしまう)。"""
        return [w for w in (words or []) if w in text]

    must_not_hit = _hit_any_negated(c.get("must_not"))
    if must_not_hit:
        reasons.append(f"must_not条件に該当: {must_not_hit}")
        return "fail", reasons

    if c.get("must"):
        missing = [w for w in c["must"] if w not in text]
        if missing:
            reasons.append(f"must条件が本文に無い: {missing}")
            return "fail", reasons
        reasons.append(f"must条件を充足: {c['must']}")

    if c.get("must_any"):
        hit = _hit_any_plain(c["must_any"])
        if not hit:
            reasons.append(f"must_any条件をどれも満たさぬ: {c['must_any']}")
            return "fail", reasons
        reasons.append(f"must_any条件を充足: {hit}")

    structural = c.get("structural")
    if structural == "enum_lines>=2":
        n = _enum_line_count(text)
        if n < 2:
            reasons.append(f"箇条書き行数 {n} 本(<2) → 検問で削られた疑い")
            return "fail", reasons
        reasons.append(f"箇条書き行数 {n} 本(>=2)")
    elif structural == "denial_without_count":
        denial_hit = _hit_any_negated(c.get("denial_phrases"))
        count_hit = _hit_any_plain(c.get("count_markers"))
        if denial_hit and not count_hit:
            reasons.append(f"母集合語({c.get('count_markers')})を伴わず断定語{denial_hit}のみ")
            return "fail", reasons
        if not denial_hit:
            reasons.append("断定語が本文に無い(このstructural条件は不発火)")
        else:
            reasons.append(f"断定語{denial_hit}に母集合語{count_hit}を伴う")

    if c.get("needs_card"):
        has_card = bool(outbox_delta_pids) or any(k.get("tool") not in ("choices", "table") for k in cards)
        if not has_card:
            reasons.append("承認カードが立たなかった(needs_card条件不成立)")
            return "fail", reasons
        reasons.append(f"承認カード成立(outbox新規pid={outbox_delta_pids})")

    if decidable == "甲":
        reasons.append("甲(両面機械照合可)・must/must_not/structural条件をすべて通過")
        return "pass", reasons
    if decidable == "乙":
        reasons.append("乙(ng側のみ機械化確実)・ng条件には当たらなんだが、expect側は近似ゆえ機械ではpassと断ぜず人の目へ回す")
        return "review", reasons
    if decidable == "丙":
        reasons.append("丙(カード有無のみ機械化可)・本文の質は必ず人の目へ回す")
        return "review", reasons
    reasons.append(f"未知のdecidable値: {decidable!r}")
    return "review", reasons


def run_one(item, criteria, run_id, uid_override=None):
    """1問を実投入。prevが在れば先に投げ応答を控え(捨てない)、続けてqを同じthreadで投げる。
    thread は問ごとに分離(eval_{run_id}_{item_id})——同一threadの使い回しは前問の_LAST_TOPIC汚染を招く(cmd_492の教訓)。"""
    item_id = item["id"]
    uid = _resolve_uid(item, uid_override)
    thread = f"eval_{run_id}_{item_id}"
    prev_text = None
    prev = item.get("prev", "")
    # D01のprevは「（Casperが①②と列挙した直後）」のような状況記述であり、実際に人が打った文ではない
    # (地の文をそのまま投げると無関係な話題が注入され、『本文が無関係』という誤った不合格を作って
    # しまう(smoketest実測で判明)。判定は必ずreviewへ回し断定はしない)。
    # E01も同型の地の文prevだが、cmd_500第2便でcriteria.json側にprev_substitute(地の文の代わりに
    # 投げる実発話)を設けた——状況が実際に人へ翻訳可能(D01と違い再現不能ではない)と軍師が裁定した
    # ため、地の文をスキップするのではなく実発話へ差し替えて文脈ありの状態で代筆の質を測る。
    prev_substitute = (criteria.get(item_id) or {}).get("prev_substitute")
    is_pseudo_prev = bool(re.match(r"^[（(]", prev.strip())) if prev else False
    if is_pseudo_prev and prev_substitute:
        prev_text, _prev_cards = run_chat([{"role": "user", "content": prev_substitute}], thread, uid)
    elif prev and not is_pseudo_prev:
        prev_text, _prev_cards = run_chat([{"role": "user", "content": prev}], thread, uid)

    before_n = _outbox_line_count()
    ans, cards = run_chat([{"role": "user", "content": item["q"]}], thread, uid)
    after_n = _outbox_line_count()

    outbox_delta_pids = []
    if after_n > before_n and os.path.exists(OUTBOX_JSONL):
        with open(OUTBOX_JSONL, encoding="utf-8") as f:
            lines = f.readlines()
        for ln in lines[before_n:after_n]:
            try:
                rec = json.loads(ln)
                if rec.get("state") == "proposed":
                    outbox_delta_pids.append(rec.get("id"))
            except Exception:
                pass
    # cards(ストリームのconfirm行)にもpidが載る。両方を突合して確実な方を採用。
    for c in cards:
        pid = c.get("id")
        if pid and pid not in outbox_delta_pids:
            outbox_delta_pids.append(pid)

    rejects = []
    if item_id in ("E01", "G01") and outbox_delta_pids:
        for pid in outbox_delta_pids:
            try:
                resp = reject_card(pid, uid)
                rejects.append({"pid": pid, "response": resp})
            except Exception as e:
                rejects.append({"pid": pid, "error": str(e)})

    verdict, reasons = judge(item_id, criteria, ans, cards, outbox_delta_pids)

    return {
        "id": item_id, "type": item.get("type", ""), "decidable": criteria.get(item_id, {}).get("decidable", ""),
        "uid": uid, "thread": thread,
        "prev": prev, "prev_sent": (prev_substitute if (is_pseudo_prev and prev_substitute)
                                     else (None if is_pseudo_prev else prev)),
        "prev_answer": prev_text, "q": item["q"],
        "answer": ans, "cards": cards, "outbox_pids": outbox_delta_pids,
        "rejects": rejects, "verdict": verdict, "reasons": reasons,
    }


def run_round(items, criteria, run_id, uid_override=None):
    out = []
    for item in items:
        t0 = time.time()
        rec = run_one(item, criteria, run_id, uid_override)
        rec["elapsed_sec"] = round(time.time() - t0, 1)
        out.append(rec)
        print(f"  [{rec['id']}] {rec['verdict']}  ({rec['elapsed_sec']}s)  {rec['reasons'][:1]}")
    return out


def summarize(round1, round2_by_id):
    """型別pass/fail/review/flaky/untestable集計。flakyは第2巡で結果が割れた問(1勝1敗)。
    review・untestableは分母(pass+fail)から外して点を高く見せない(件数と分母を両方明記)。
    untestable(cmd_500第2便新設・G01用): holdoutのexpectを実際には測れておらぬ問を示す
    第四区分。fail確定にして点を下げも、passにして点を上げもしない——測れておらぬと
    正直に申告する。"""
    by_type = {}
    flaky_ids = []
    final = {}
    for r in round1:
        iid = r["id"]
        v1 = r["verdict"]
        final[iid] = {"round1": v1, "round2": None, "final": v1}
        if iid in round2_by_id:
            v2 = round2_by_id[iid]["verdict"]
            final[iid]["round2"] = v2
            if v1 == "pass" and v2 == "pass":
                final[iid]["final"] = "pass"
            elif v1 == "fail" and v2 == "fail":
                final[iid]["final"] = "fail"
            elif {v1, v2} == {"pass", "fail"}:                 # 1勝1敗=揺れ(brief明記の狭義flaky)
                final[iid]["final"] = "flaky"
                flaky_ids.append(iid)
            else:
                # 少なくとも一方がreview(かつpass/fail両立ではない)。briefが明記する3類型
                # (両fail/1勝1敗/両pass)のどれにも当たらぬ組合せ。reviewを安易にfail/passへ
                # 丸めず、machineでは断ぜられぬまま「review」として残す(分母から外す・隠さぬ原則)。
                final[iid]["final"] = "review"
    for iid, f in final.items():
        t = next(r["type"] for r in round1 if r["id"] == iid)
        by_type.setdefault(t, {"pass": 0, "fail": 0, "review": 0, "flaky": 0, "untestable": 0, "ids": []})
        by_type[t][f["final"]] += 1
        by_type[t]["ids"].append({"id": iid, "final": f["final"]})
    n_pass = sum(1 for f in final.values() if f["final"] == "pass")
    n_fail = sum(1 for f in final.values() if f["final"] == "fail")
    n_review = sum(1 for f in final.values() if f["final"] == "review")
    n_flaky = sum(1 for f in final.values() if f["final"] == "flaky")
    n_untestable = sum(1 for f in final.values() if f["final"] == "untestable")
    score = n_pass / (n_pass + n_fail) if (n_pass + n_fail) else 0.0
    return {
        "by_type": by_type, "final_by_id": final, "flaky_ids": flaky_ids,
        "counts": {"pass": n_pass, "fail": n_fail, "review": n_review, "flaky": n_flaky,
                   "untestable": n_untestable, "total": len(final)},
        "score_pass_over_pass_plus_fail": round(score, 4),
        "score_note": "review・untestable件数は分母(pass+fail)から除外している(点を高く見せぬため"
                       "件数を併記)。reviewを合格扱いにしていない。untestableはholdoutのexpectを"
                       "実際には測れておらぬ問(cmd_500第2便新設・G01)——fail確定にもpass水増しにも"
                       "せず、測れておらぬと正直に申告する。",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round2", action="store_true", help="第1巡でfail/reviewの問のみ第2巡実行(既定off)")
    ap.add_argument("--uid", default=None, help="全問をこのuidで実行(既定は各問srcから自動解決)")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    run_id = args.run_id or f"run_{int(time.time())}"

    # cmd_509第2便・軍師point「この一行があれば全損は防げた」: 推論機不通時は測定を実行せず、
    # 正直に「測れなかった」と記録して終える(掟「失敗とゼロを別出口へ」)。
    gen_key = _current_gen_key()
    if not casper_breaker.allow(gen_key):
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = {"run_id": run_id, "started_at": started_at, "elapsed_sec": 0.0,
                  "breaker_key": gen_key, "breaker_state": casper_breaker.state(gen_key),
                  "skipped": True, "skip_reason": "推論機不通につき測定せず(casper_breaker.allow()=False)"}
        os.makedirs(RESULTS_DIR, exist_ok=True)
        out_path = os.path.join(RESULTS_DIR, f"{run_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"推論機不通につき測定せず(breaker key={gen_key} state={result['breaker_state']})。結果: {out_path}")
        return 1

    holdout = json.load(open(HOLDOUT_JSON, encoding="utf-8"))
    criteria_doc = json.load(open(CRITERIA_JSON, encoding="utf-8"))
    criteria = criteria_doc["items"]
    holdout_sha256 = _sha256(HOLDOUT_JSON)
    items = holdout["items"]

    t_start = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"=== run_id={run_id}  holdout sha256={holdout_sha256[:16]}...  items={len(items)} ===")

    # cmd_507第1便: 測定中はsupervisorのauto-reloadを保留させるロックを張る。
    # 正常終了はtry/finallyで、異常終了/Ctrl-CはSIGTERM/SIGINTハンドラで、
    # kill -9等ハンドラも走らぬ強制終了はsupervisor側のTTL判定(20分)で解ける(AC2三重防御)。
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    _acquire_lock()
    interrupted_by_reload = False
    try:
        print("--- 第1巡 ---")
        try:
            round1 = run_round(items, criteria, run_id, args.uid)
        except (ConnectionRefusedError, urllib.error.URLError) as e:
            interrupted_by_reload = True
            print(f"!!! 中断: {e} (auto-reloadによる接続断の疑い)")
            round1 = []

        round2 = []
        round2_by_id = {}
        if args.round2 and not interrupted_by_reload:
            redo = [it for it in items if next(r["verdict"] for r in round1 if r["id"] == it["id"]) in ("fail", "review")]
            if redo:
                print(f"--- 第2巡(fail/reviewのみ再投入: {[it['id'] for it in redo]}) ---")
                try:
                    round2 = run_round(redo, criteria, run_id + "_r2", args.uid)
                except (ConnectionRefusedError, urllib.error.URLError) as e:
                    interrupted_by_reload = True
                    print(f"!!! 中断(第2巡): {e} (auto-reloadによる接続断の疑い)")
                round2_by_id = {r["id"]: r for r in round2}
            else:
                print("--- 第2巡: 第1巡で全問pass、再投入対象なし ---")
    finally:
        _release_lock()

    elapsed = round(time.time() - t_start, 1)

    if interrupted_by_reload:
        # 掟「失敗とゼロを別出口へ」: 「測って落ちた」と「測れなかった」を分ける。
        # pass/fail集計はせず、中断の事実だけを記す。
        result = {
            "run_id": run_id, "started_at": started_at, "elapsed_sec": elapsed,
            "holdout_sha256": holdout_sha256, "holdout_version": holdout.get("version"),
            "criteria_version": criteria_doc.get("version"),
            "round2_enabled": args.round2,
            "interrupted_by_reload": True,
            "round1": round1, "round2": [],
        }
        os.makedirs(RESULTS_DIR, exist_ok=True)
        out_path = os.path.join(RESULTS_DIR, f"{run_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"\n=== 中断(interrupted_by_reload=true) === 結果: {out_path}")
        return 1

    summary = summarize(round1, round2_by_id)

    fail_review_full = [r for r in round1 if r["verdict"] in ("fail", "review")]
    for r2 in round2:
        fail_review_full.append(r2)

    result = {
        "run_id": run_id, "started_at": started_at, "elapsed_sec": elapsed,
        "holdout_sha256": holdout_sha256, "holdout_version": holdout.get("version"),
        "criteria_version": criteria_doc.get("version"),
        "round2_enabled": args.round2,
        "interrupted_by_reload": False,
        "summary": summary,
        "round1": round1,
        "round2": round2,
        "fail_or_review_full_answers": fail_review_full,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"{run_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    counts = summary["counts"]
    print(f"\n=== 集計 ===")
    print(json.dumps(counts, ensure_ascii=False))
    # 将軍裁定1(cmd_500第2便): 未測定は分母から静かに落とさず、対象問題数・未測定数を必ず表に出す。
    print(f"対象問題数={counts['total']}問 内訳: pass={counts['pass']} fail={counts['fail']} "
          f"review={counts['review']}(分母除外) untestable={counts['untestable']}(分母除外・測定不能ゆえ) "
          f"flaky={counts['flaky']}")
    print(f"score(pass/(pass+fail)) = {summary['score_pass_over_pass_plus_fail']:.0%}  "
          f"※分母=pass+fail={counts['pass']+counts['fail']}問(review{counts['review']}件・"
          f"untestable{counts['untestable']}件は分母から除外・隠していない。全{counts['total']}問中の"
          f"内訳は上記の通り明示)")
    print(f"結果: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
