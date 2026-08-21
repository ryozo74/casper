#!/usr/bin/env python3
"""Aurora一覧照会・存在確認の決定的注入機構(cmd_503)の回帰ゲート(純機構・インメモリ・読取のみ)。
全PASSで exit 0。

実害(2026-07-31 20:21・殿の実発話): 「Aurora内の今日アップデートした資料って何？」に対し
qwenが「現在のシステム連携ではリアルタイムで照会できません」と自ら文言を作り、2日前のvault
ファイルを最新と称した。この文言はコードのどこにも無い(qwenの捏造)。casper_tools.pyへは
足さない(qwenへ渡さない)——gate方式(機構が決定的に呼ぶ)で直す。

守る掟:
 ① _aurora_list_turn: Aurora語+一覧/更新の意図語+疑問形or依頼形 の三条件を満たすturnのみTrue。
 ② _resolve_since: 相対日付を機構が決定的に解く(qwenに日付を作らせない)。
 ③ aurora_list_digest: 判定Trueのturnで list_documents を機構が呼び、題・投稿者・時刻を
    整形注入する。JSON文字列はjson.loadsしてから使う(将軍・軍師実測の落とし穴)。
 ④ 0件時は母集合(since無し再照会の全体件数+直近1件)を示す(「無い」と「その期間に無い」の区別)。
 ⑤ _aurora_exists_turn/aurora_exists_digest: 個別資料の存在確認。exists/deletedを区別して答える。

chat_server.py を import すると server が起動してしまうゆえ、ast で当該機構のみを抜いて検査する。
casper_aurora はモジュール属性差替(stub)でネットワーク非依存のまま検査する。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "chat_server.py")
WANT = ["_QUESTION_FORM_RE", "_DESIRE_FORM_RE", "_REQUEST_FORM_RE", "_ASK_DELEGATE_RE",
        "_AURORA_LIST_RE", "_AURORA_LIST_INTENT_RE", "_aurora_list_turn",
        "_resolve_since", "_aurora_fmt_doc_line", "aurora_list_digest",
        "_AURORA_EXISTS_INTENT_RE", "_AURORA_NAMED_DOC_RE", "_aurora_exists_turn", "aurora_exists_digest"]

tree = ast.parse(open(SRC, encoding="utf-8").read())
picked, seen = [], set()
for node in tree.body:
    names = ([node.name] if isinstance(node, (ast.FunctionDef,)) else
             ([node.names[0].asname or node.names[0].name] if isinstance(node, (ast.Import, ast.ImportFrom)) else
              [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]))
    for nm in names:
        if nm in WANT:
            picked.append(node)
            seen.add(nm)
missing = [w for w in WANT if w not in seen]
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

M = {}
exec("import re, os, json, datetime, urllib.request", M)


class _StubAurora:
    """casper_aurora の stub。configured()/list_documents()の戻りをテストごとに差替える。
    list_documentsは実casper_aurora.list_documentsと同じ契約(常にbareなlist)を守る——
    実装側のbugでMCP生封筒(dict)が漏れ出た場合を検知するのは別途_unwrap_list単体試験で行う。"""
    def __init__(self):
        self._configured = True
        self._docs = []

    def configured(self):
        return self._configured

    def list_documents(self, since=None, limit=None, uploaded_by=None):
        if since:
            return [d for d in self._docs if str(d.get("uploaded_at", ""))[:10] >= since]
        return list(self._docs)


_stub_au = _StubAurora()
M["casper_aurora"] = _stub_au
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
_turn = M["_aurora_list_turn"]
_since = M["_resolve_since"]
_digest = M["aurora_list_digest"]
_ex_turn = M["_aurora_exists_turn"]
_ex_digest = M["aurora_exists_digest"]

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ══════════════════════════════════════════════════════════════════════════
# ★実casper_aurora._unwrap_list単体試験(実測是正: 0件時はcasper_mcp.call_toolが
# MCP生封筒({"content":[],"structuredContent":{"result":[]}})へ流れ落ちる——非0件時のみ
# bareなlistが直に返る非対称。呼出側でdictのまま扱うとfor文がキー文字列を回し件数が化ける実害を
# 実測で発見(2026-08-06未来日付照会で確認)。list_documents/document_existsのjson.loads後、
# 必ずこれを通す設計になっているかを検査する。)
# ══════════════════════════════════════════════════════════════════════════
print("--- casper_aurora._unwrap_list 単体試験(0件時MCP生封筒の吸収) ---")
sys.path.insert(0, HERE)
import casper_aurora as _real_au

chk("bareなlistはそのまま", _real_au._unwrap_list([1, 2, 3]), [1, 2, 3])
chk("MCP生封筒(0件・structuredContent.result)は空listへ正規化",
    _real_au._unwrap_list({"content": [], "structuredContent": {"result": []}, "isError": False}), [])
chk("MCP生封筒(非0件・structuredContent.result)も正規化",
    _real_au._unwrap_list({"content": [], "structuredContent": {"result": [{"a": 1}]}}), [{"a": 1}])
chk("dict直下result形も正規化", _real_au._unwrap_list({"result": [{"b": 2}]}), [{"b": 2}])
chk("未知dict形は空list(捏造せず安全側)", _real_au._unwrap_list({"other": "junk"}), [])
chk("Noneは空list", _real_au._unwrap_list(None), [])

import datetime as _dt

TODAY = _dt.date(2026, 8, 6)

_LIST_INPUTS = ["Aurora内の今日アップデートした資料って何？", "オーロラに今日上がった資料ある？",
                "Auroraの資料一覧を教えて", "オーロラに追加されたドキュメント教えてくれ"]
_NON_LIST_INPUTS = ["進行中のプロジェクトを教えて", "kiyotomoの手持ちタスクは？",
                    "Auroraに保存しておいて", "議事録をまとめて"]

# ══════════════════════════════════════════════════════════════════════════
# ① _aurora_list_turn: 実害の発話・類似形はTrue
# ══════════════════════════════════════════════════════════════════════════
for q in _LIST_INPUTS:
    chk(f"一覧照会turnはTrue: {q}", _turn(q), True)

# ── 非該当: Aurora語が無い/意図語が無い/疑問・依頼形が無い ──
for q in _NON_LIST_INPUTS:
    chk(f"非一覧turnはFalse: {q}", _turn(q), False)
chk("空queryはFalse", _turn(""), False)
chk("Aurora語のみ(意図語無し)はFalse", _turn("Auroraって何"), False)

# ══════════════════════════════════════════════════════════════════════════
# ② _resolve_since: 相対日付の機構的解決(対応表通りか)
# ══════════════════════════════════════════════════════════════════════════
chk("今日→本日ラベル", _since("今日アップデートした資料", TODAY), ("2026-08-06", "本日"))
chk("本日→本日ラベル", _since("本日の資料", TODAY), ("2026-08-06", "本日"))
chk("昨日→昨日ラベル", _since("昨日の資料", TODAY), ("2026-08-05", "昨日"))
chk("今週→直近1週間ラベル", _since("今週上がった資料", TODAY), ("2026-07-30", "直近1週間"))
chk("今月→直近1ヶ月ラベル", _since("今月の資料", TODAY), ("2026-07-07", "直近1ヶ月"))
chk("絶対日付(2026-07-01)を解く", _since("2026-07-01の資料", TODAY), ("2026-07-01", "2026-07-01"))
chk("期間指定なしはNone", _since("資料一覧見せて", TODAY), (None, ""))

# ══════════════════════════════════════════════════════════════════════════
# ③ aurora_list_digest: 判定Falseのturnは注入ゼロ
# ══════════════════════════════════════════════════════════════════════════
chk("非該当turnは注入ゼロ", _digest(None, "進行中のプロジェクトを教えて"), "")

# ══════════════════════════════════════════════════════════════════════════
# ④ aurora_list_digest: 該当turn+ヒット在り→題・投稿者・時刻を整形注入し「そのまま述べよ」と命ずる
# ══════════════════════════════════════════════════════════════════════════
_stub_au._docs = [
    {"id": "1", "slug": "a", "title": "明石奏 顔アップターンアラウンド ダッシュボード",
     "uploaded_by": "ashigaru2", "uploaded_at": "2026-08-06T11:17:24", "version": 2, "deleted_at": None},
    {"id": "2", "slug": "b", "title": "社内サービス接続先一覧(IP/ポート)",
     "uploaded_by": "ops-desk", "uploaded_at": "2026-08-06T11:13:35", "version": 1, "deleted_at": None},
]
_out = _digest(None, "Aurora内の今日アップデートした資料って何？")
chk_true("題を含む", "明石奏 顔アップターンアラウンド ダッシュボード" in _out)
chk_true("投稿者を含む", "ashigaru2" in _out)
chk_true("そのまま述べよ、の指示を含む", "そのまま述べよ" in _out)
chk_true("『リアルタイムで照会できません』を書かせぬ抑止文を含む", "リアルタイムで照会できません" in _out and "述べるな" in _out)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 0件時(AC3): 母集合(since無し再照会の全体件数+直近1件)を示す
# ══════════════════════════════════════════════════════════════════════════
_out0 = _digest(None, "Aurora内の今日アップデートした資料って何？")
_stub_au._docs = [
    {"id": "1", "slug": "a", "title": "古い資料", "uploaded_by": "casper",
     "uploaded_at": "2026-08-01T09:00:00", "version": 1, "deleted_at": None},
]
_out0 = _digest(None, "Aurora内の今日アップデートした資料って何？")
chk_true("0件時: 0件である旨を述べる", "0件でござった" in _out0)
chk_true("0件時: 全体件数(母集合)を示す", "1件が登録されており" in _out0)
chk_true("0件時: 直近1件の題を示す", "古い資料" in _out0)
chk_true("0件時: 『無い』と『その期間に無い』を区別する注記あり", "その期間に無い" in _out0 or "母集合" in _out0)

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 未接続時は正直な出口(捏造禁止)
# ══════════════════════════════════════════════════════════════════════════
_stub_au._configured = False
_out_disc = _digest(None, "Aurora内の今日アップデートした資料って何？")
chk_true("未接続時は正直な出口文言(捏造禁止)", "接続が現在できませなんだ" in _out_disc)
_stub_au._configured = True

# ══════════════════════════════════════════════════════════════════════════
# ⑦ _aurora_exists_turn / aurora_exists_digest(AC2)
# ══════════════════════════════════════════════════════════════════════════
_stub_au._docs = [
    {"id": "1", "slug": "a", "title": "明石奏 顔アップターンアラウンド ダッシュボード",
     "uploaded_by": "ashigaru2", "uploaded_at": "2026-08-06T11:17:24", "version": 2, "deleted_at": None},
]
chk("存在確認turnはTrue", _ex_turn("Auroraに『明石奏 顔アップターンアラウンド ダッシュボード』という資料ある？"), True)
chk("『という資料』形もTrue(引用符無し)", _ex_turn("Auroraに明石奏ダッシュボードという資料ある？"), True)
chk("一覧照会turnは_ex_turn側ではFalse(排他・named formが無い『資料ある』は一覧側)",
    _ex_turn("Auroraに今日上がった資料ある？"), False)

_out_exists = _ex_digest(None, "Auroraに『明石奏 顔アップターンアラウンド ダッシュボード』という資料ある？")
chk_true("実在資料: 在る、と答える", "在り申す" in _out_exists)
chk_true("実在資料: バージョンを含む", "version 2" in _out_exists)

_out_notfound = _ex_digest(None, "Auroraに『架空の存在しない資料タイトルです』という資料ある？")
chk_true("架空資料: 見当たらぬ、と答える", "見当たりませなんだ" in _out_notfound)
chk_true("架空資料: 『在る』とは言わない", "在り申す" not in _out_notfound)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異検証①: json.loads相当(list_documents戻りの型)を外す→件数が化けることを検知
# _StubAurora.list_documentsは既にlist型(json.loads済相当)を返す設計。ここでは
# casper_aurora.list_documentsが【文字列のまま】返る変異(json.loads未実施の再現)を模擬し、
# aurora_list_digestがそれを無警戒に扱うと壊れる(=json.loadsの要否をゲートが検知できる証拠)。
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証①(json.loads省略の検知) ---")
_stub_au._docs = [
    {"id": "1", "slug": "a", "title": "本日資料", "uploaded_by": "casper",
     "uploaded_at": "2026-08-06T09:00:00", "version": 1, "deleted_at": None},
]
_pre1 = _digest(None, "Aurora内の今日アップデートした資料って何？")
_pre1_ok = "本日資料" in _pre1 and "そのまま述べよ" in _pre1
chk_true("①変異前確認: 正常経路は題を注入する", _pre1_ok)

_orig_list_documents = _stub_au.list_documents


def _mutated_list_documents_returns_string(since=None, limit=None, uploaded_by=None):
    """json.loads省略を模擬: dictのlistでなく生のJSON文字列を返す(呼出側がlen()を取ると
    文字数を件数と誤認する実測済の落とし穴を再現)。"""
    import json as _j
    docs = _orig_list_documents(since=since, limit=limit, uploaded_by=uploaded_by)
    return _j.dumps(docs, ensure_ascii=False)


_stub_au.list_documents = _mutated_list_documents_returns_string
_post1 = _digest(None, "Aurora内の今日アップデートした資料って何？")
_mutation1_killed = ("本日資料" not in _post1)   # 文字列をdocsとして扱うと辞書アクセスで例外→except握り潰しで空文字
chk_true("②③変異適用後: JSON文字列のまま渡すと題が注入されない(赤化実証)", _mutation1_killed)
_stub_au.list_documents = _orig_list_documents
_post1_restore = _digest(None, "Aurora内の今日アップデートした資料って何？")
chk_true("④復元確認: 元に戻すと再び題が注入される", "本日資料" in _post1_restore)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異検証②: 0件時の母集合提示を外す→AC3違反を検知
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証②(0件時の母集合提示の除去検知) ---")
_stub_au._docs = [
    {"id": "1", "slug": "a", "title": "古い資料", "uploaded_by": "casper",
     "uploaded_at": "2026-08-01T09:00:00", "version": 1, "deleted_at": None},
]
_pre2 = _digest(None, "Aurora内の今日アップデートした資料って何？")
_pre2_ok = "登録されており" in _pre2
chk_true("①変異前確認: 0件時は母集合が注入される", _pre2_ok)

_orig_digest_fn = M["aurora_list_digest"]


def _mutated_digest_no_superset(who, query):
    """AC3違反を模擬: 0件時に『0件でござった』とだけ言い、母集合(全体件数/直近1件)を示さない変異体。"""
    out = _orig_digest_fn(who, query)
    if "0件でござった" in out:
        return "\n\n## Aurora の資料一覧(機構が取得)\n本日以降にアップロードされた資料は0件でござった。\n"
    return out


M["aurora_list_digest"] = _mutated_digest_no_superset
_post2 = M["aurora_list_digest"](None, "Aurora内の今日アップデートした資料って何？")
_mutation2_killed = ("登録されており" not in _post2)
chk_true("②③変異適用後: 母集合提示を外すと欠落する(赤化実証=AC3違反をゲートが検知)", _mutation2_killed)
M["aurora_list_digest"] = _orig_digest_fn
_post2_restore = M["aurora_list_digest"](None, "Aurora内の今日アップデートした資料って何？")
chk_true("④復元確認: 元に戻すと母集合が再び注入される", "登録されており" in _post2_restore)

# ══════════════════════════════════════════════════════════════════════════
# ★突然変異検証③: 日付解決を「今日」固定にする→「今週」等が誤ることを検知
# ══════════════════════════════════════════════════════════════════════════
print("\n--- 突然変異検証③(日付解決の固定化検知) ---")
_pre3 = _since("今週上がった資料", TODAY)
_pre3_ok = (_pre3 == ("2026-07-30", "直近1週間"))
chk_true("①変異前確認: 「今週」は7日前へ正しく解決される", _pre3_ok)


def _mutated_since_today_fixed(query, today=None):
    """日付解決を「今日」固定にする変異体(「今週」等の相対日付語を無視する劣化を再現)。"""
    t = today or _dt.date.today()
    return t.isoformat(), "本日"


M["_resolve_since"] = _mutated_since_today_fixed
_post3 = M["_resolve_since"]("今週上がった資料", TODAY)
_mutation3_killed = (_post3 != _pre3)
chk_true("②③変異適用後: 「今週」が誤って『本日』扱いになる(赤化実証)", _mutation3_killed)
M["_resolve_since"] = _since
_post3_restore = M["_resolve_since"]("今週上がった資料", TODAY)
chk_true("④復元確認: 元に戻すと「今週」が正しく直近1週間へ解決される", _post3_restore == ("2026-07-30", "直近1週間"))

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
