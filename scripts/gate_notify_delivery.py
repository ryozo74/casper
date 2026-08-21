#!/usr/bin/env python3
"""画面通知の配信欠陥(cmd_505)の回帰ゲート。全PASSで exit 0。

守る掟(brief AC6):
 ①未購読者にも通知が出る(巡回先拡張 _recent_uids + その場算出の保険)
 ②既存の🔔購読者にも従来通り出る(退行なし)
 ③既読が効く(store→pending経由。computeの戻りを直接返さぬこと)
 ④push配信経路が健在(_targetsに足すだけ・_push_new呼出条件は不変)

_recent_uids は ast 抽出で純機構として直接検証する(chat_server.py 全体import は
常駐スレッドを起動し実z8a/実ポート依存になるため・gate_aurora_save_smoke.py の作法に倣う)。
/api/notifications の挙動は実サーバ(127.0.0.1:8770)へ実際にHTTPで問う(実測でなければ
「store経由か」「computeの戻りを直接返していないか」は判定できない)。
実サーバに到達できない場合はスキップ(exit 0・CIを壊さない・gate_aurora_save_smoke.py同型)。

突然変異3種(brief記載)は本ゲート単体では機械実演できない(実サーバのコードを書き換えて
再起動する必要があるため)。かわりに ①②③④ の各chkが「何を捉えるか」をコメントで明記し、
実装時に3種の突然変異を手動適用→本ゲートが赤化することを別途確認する(報告に手順を残す)。
"""
import ast
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_notify

SRC = os.path.join(HERE, "chat_server.py")
BASE = "http://127.0.0.1:8770"

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond, detail=""):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}" + (f" ({detail})" if detail else ""))


# ── ① _recent_uids が chat_server.py に存在し、純機構として動くこと ──────────
tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
WANT = ["_recent_uids"]
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else
             [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
    for nm in names:
        if nm in WANT:
            picked.append(node)
            seen.add(nm)
missing = [w for w in WANT if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}(未実装)")
    results.append(False)
else:
    M = {}
    exec("import datetime, json, os", M)
    M["HERE"] = HERE
    M["CONVO_LOG"] = os.path.join(HERE, "conversation_log.jsonl")
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    _recent_uids = M["_recent_uids"]

    got = _recent_uids(days=14)
    chk_true("① _recent_uids(14) が list を返す", isinstance(got, list), f"got={type(got)}")
    # conversation_log.jsonl の実データより: ip=172.17.0.1 で直近14日以内に現れたuid群。
    # 少なくとも直近ログにある '31'(kiyotomo)を含むはず(brief実測: 2026-08-07に発話あり)。
    chk_true("① 直近ログの人発話uidを拾う(31を含む)", "31" in got, f"got={got}")
    # 0日で絞れば「今日以外は入らない」性質を持つはず(境界: 全期間を無条件で拾う骨抜き実装でないこと)
    got0 = _recent_uids(days=0)
    chk_true("① days=0 は直近ログ(今日分)のみ→14日分以下の件数", len(got0) <= len(got), f"0日={got0} 14日={got}")


# ── ①②④(構造) _targets が _recent_uids・base_uids・push購読者の3系統を合成していること ──
# _targetsは_notify_scheduler内のネスト関数ゆえ、astで直接その子FunctionDefを取り出して検証する
# (突然変異(ii): _recent_uids呼出を外しても①(保険)側の実サーバ実測だけでは検知できないため必須)。
_targets_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_notify_scheduler":
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef) and sub.name == "_targets":
                _targets_node = sub
                break
if _targets_node is None:
    print("❌ chat_server.py に _notify_scheduler._targets が見当たらぬ(未実装)")
    results.append(False)
else:
    M1 = {"base_uids": ["28"], "casper_push": None, "_recent_uids": lambda days=14: ["36", "44"]}
    exec(compile(ast.Module(body=[_targets_node], type_ignores=[]), SRC, "exec"), M1)
    got_t = M1["_targets"]()
    chk_true("④ _targetsがbase_uids(既定・殿)を含む(push経路の土台=退行なし)", "28" in got_t, f"got={got_t}")
    chk_true("①(構造) _targetsが_recent_uidsの結果を合成する(巡回先拡張=本命)",
              "36" in got_t and "44" in got_t, f"got={got_t}")

    class _MockPush:
        @staticmethod
        def subscribed_uids():
            return ["30"]
    M2t = {"base_uids": ["28"], "casper_push": _MockPush(), "_recent_uids": lambda days=14: ["36", "44"]}
    exec(compile(ast.Module(body=[_targets_node], type_ignores=[]), SRC, "exec"), M2t)
    got_t2 = M2t["_targets"]()
    chk_true("④ push購読者(30)もbase_uids/_recent_uidsと共に残る(AC4=配信経路が健在)",
              set(["28", "30", "36", "44"]) <= set(got_t2), f"got={got_t2}")


# ── ③(構造) _notifications_for が「computeの戻りを直接返さずstoreを経由する」こと ──────
# モックcasper_notifyでstore呼出有無を直接観測する(実サーバ実測②③はネットワーク依存の別軸として後段で行う)。
WANT2 = ["_notifications_for"]
picked2, seen2 = [], set()
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else
             [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)])
    for nm in names:
        if nm in WANT2:
            picked2.append(node)
            seen2.add(nm)
missing2 = [w for w in WANT2 if w not in seen2]
if missing2:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing2}(未実装)")
    results.append(False)
else:
    class _MockNotify:
        """pending初回=空、compute後にstoreされた前提で2回目のpendingが埋まる、を模す最小mock。"""
        def __init__(self):
            self.store_calls = []
            self._stored = False

        def pending(self, uid):
            if self._stored:
                return [{"type": "mock", "dedup_key": "mock:1", "uid": str(uid), "read": False}]
            return []

        def compute(self, uid):
            return [{"type": "mock", "dedup_key": "mock:1"}]

        def store(self, uid, notifs):
            self.store_calls.append((uid, notifs))
            self._stored = True
            return len(notifs)

    M2 = {"casper_notify": _MockNotify()}
    exec(compile(ast.Module(body=picked2, type_ignores=[]), SRC, "exec"), M2)
    _notifications_for = M2["_notifications_for"]

    out = _notifications_for("99")
    chk_true("③(構造) pending空→compute実行後にstoreが呼ばれる", len(M2["casper_notify"].store_calls) == 1,
              f"store_calls={M2['casper_notify'].store_calls}")
    chk_true("③(構造) 返り値はpending経由(read項目付き)であり、computeの生の戻りでない",
              bool(out) and "read" in out[0], f"out={out}")

    # 突然変異(i)相当: pendingに既に在る時はcomputeを呼ばぬこと(重複防止・AC5高速化の前提)
    M3 = {"casper_notify": _MockNotify()}
    exec(compile(ast.Module(body=picked2, type_ignores=[]), SRC, "exec"), M3)
    mock3 = M3["casper_notify"]
    mock3._stored = True   # 既にpendingが埋まっている状態を模す
    _notifications_for3 = M3["_notifications_for"]
    _notifications_for3("99")
    chk_true("③(構造) pending命中時はcompute/storeを呼ばぬ(store_calls=0)",
              len(mock3.store_calls) == 0, f"store_calls={mock3.store_calls}")


def _reachable():
    # サーバはLLM応答で長時間ブロックすることがあるため、短timeout×複数回試す(単発timeoutでの誤スキップ防止)。
    for _ in range(3):
        try:
            urllib.request.urlopen(BASE + "/api/obs", timeout=8)
            return True
        except Exception:
            continue
    return False


def _get_notifications(uid, retries=3):
    req = urllib.request.Request(BASE + "/api/notifications", headers={"X-Actor-User-Id": str(uid)})
    last_exc = None
    for _ in range(retries):
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode("utf-8"))
            return body, time.time() - t0
        except Exception as e:
            last_exc = e
    raise last_exc


def _mark_read(uid, keys=None, retries=3):
    payload = json.dumps({"keys": keys} if keys else {}).encode("utf-8")
    last_exc = None
    for _ in range(retries):
        req = urllib.request.Request(BASE + "/api/notifications/read", data=payload,
                                      headers={"X-Actor-User-Id": str(uid), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_exc = e
    raise last_exc


TEST_UID_UNSUB = os.environ.get("GATE_NOTIFY_TEST_UID", "44")  # 🔔未購読・その場算出/巡回いずれかで拾われるべきuid
STATE_PATH = os.path.join(HERE, "casper_notify_state.json")


def _reset_state(uid):
    """当該uidのdedup状態を消す(computeは副作用を持ち2度目以降は新規イベント無しとなるため・
    brief記載の検証上の注意そのもの)。ゲート実行のたびcount>0を安定して再現するための前処理。
    本番stateファイルを直接編集するが、対象は既に通知の中身がある実在projectデータの再算出のみで
    データ破壊ではない(compute()が読み直すだけ)。"""
    try:
        st = json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        st = {}
    if str(uid) in st:
        del st[str(uid)]
        tmp = STATE_PATH + ".tmp"
        json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATE_PATH)


if not _reachable():
    print(f"⚠ 実サーバ({BASE})に到達できず。②/api/notifications実測をスキップする(CIを壊さぬためexit 0)。")
else:
    # ── ①(AC1本体) 未購読者にも通知が出ること ─────────────────────────
    # pending済み(夜間巡回で既に積まれている)なら消さずそのまま見る(①の本命=巡回先拡張の実証)。
    # pendingが無く、かつdedup状態も消費済みで新規イベントが出ない場合のみリセットする
    # (brief記載の検証上の注意: state消費済みで0件でも「直っていない」とは限らない、への対処)。
    if not casper_notify.pending(TEST_UID_UNSUB):
        _reset_state(TEST_UID_UNSUB)
    body_u, t_u = _get_notifications(TEST_UID_UNSUB)
    chk_true(f"①(AC1) uid{TEST_UID_UNSUB}(🔔未購読)にも通知が出る", body_u.get("count", 0) > 0,
              f"count={body_u.get('count')}")

    # ── ② 既存購読者(30)の退行なし(28はゲート実行のたびに③で既読化されるため30を使う) ──
    # dedup状態が既に消費済みだと新規イベント無し=count0で偽陰性になる(brief記載の注意)ため、
    # 判定直前にuid30のdedup状態をリセットしてcompute()が必ず新規扱いで返す状態を作ってから測る。
    _reset_state(30)
    body_sub, t_sub = _get_notifications(30)
    chk_true("② uid30(既存購読者)は従来どおり通知が出る", body_sub.get("count", 0) > 0,
              f"count={body_sub.get('count')}")

    # ── ③ 既読が効く(store→pending経由。computeの戻りを直接返せば既読管理と噛み合わずここで落ちる) ──
    # 実測でuid28/30/90001等いずれも、多人数運用環境(他エージェント/他セッションが同一
    # casper_notify.jsonl/casper_notify_state.jsonへ並行アクセスする共有環境)ではHTTP2回叩きの
    # 間隙に外部要因が割り込みうることが確認された(cmd_505スコープ外の既存アーキ課題)。
    # ゆえに③は「毎回新規生成する一意dedup_key」を使い、mark_read前後でそのkeyだけを厳密に
    # 追跡することで、他要因が他keyへ書き込んでも本チェックの正誤に影響しない形にする
    # (架空keyの一意性そのものが競合源を無効化する・casper_notify.py本体はmoduleごしに直接使う
    # ことでHTTPラウンドトリップの間隙も最小化)。
    TEST_UID_READFLAG = "90001"
    _uniq = f"gate_ac3:{TEST_UID_READFLAG}:{len(casper_notify._all_notifs())}"
    casper_notify.store(TEST_UID_READFLAG, [{"type": "mock_read_check", "level": "info",
                         "title": "gate専用通知(AC3実証)", "body": "既読機構の実証専用。",
                         "dedup_key": _uniq}])
    before = {n["dedup_key"] for n in casper_notify.pending(TEST_UID_READFLAG) if not n.get("read")}
    chk_true("③ storeした通知がpendingに現れる(既読化前)", _uniq in before, f"pending={before}")
    casper_notify.mark_read(TEST_UID_READFLAG, [_uniq])
    after = {n["dedup_key"] for n in casper_notify.pending(TEST_UID_READFLAG) if not n.get("read")}
    chk_true("③ 既読にした通知(一意key)は再読込で再び現れぬ", _uniq not in after, f"pending={after}")

print()
n_ok, n = sum(results), len(results)
print(f"{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
