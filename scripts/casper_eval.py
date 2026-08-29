#!/usr/bin/env python3
"""Casper 評価ハーネス(ゴールデンセット/リグレッション) — Fable5棚卸し #5-1/5-2の実装。

過去の失態を「決定的アサーション(プログラム判定)」のテストケースにし、あらゆる変更
(プロンプト/モデル/index/num_ctx)の後に自動実行して回帰を捕まえる。LLM-judgeは使わない
(Casperの失敗モードはほぼ全て機械判定可能=安く速く再現可能・Fable 5-2)。

使い方:
  python3 casper_eval.py              # 全ケース実行
  python3 casper_eval.py <部分名>     # 名前に一致するケースだけ
実装:
  各ケースを localhost:8770/api/chat へ流し、応答ストリーム(text＋confirmカード)を復元して
  アサーション群を評価。承認カードの有無(=アクション台帳のレシート)も判定材料にする。
"""
import json
import os
import re
import sys
import urllib.request

ENDPOINT = os.environ.get("CASPER_EVAL_ENDPOINT", "http://localhost:8770/api/chat")
ACTOR = os.environ.get("CASPER_EVAL_ACTOR", "28")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 【殿御下命2026-08-29・丙】loopback だけでは最早内部機構を名乗れぬ。合鍵を提げて呼ぶ。
# ★合鍵は casper_secrets.host_secret() が無ければ作る——ゆえハーネスが黙って匿名へ落ちることは無い。
try:
    import casper_secrets as _casper_secrets
    HOST_SECRET = _casper_secrets.host_secret()
except Exception:
    HOST_SECRET = ""
try:
    import casper_outbox                                     # fixture(下書きseed)用・真実源への直接投入
except Exception:
    casper_outbox = None
CASES_JSONL = os.path.join(HERE, "cases.jsonl")            # 昇格済ゴールデン(人が承認したもの)
PENDING_JSONL = os.path.join(HERE, "cases_pending.jsonl")  # 失敗トレース由来の候補(人の審査待ち)
TRACE_JSONL = os.path.join(HERE, "casper_trace.jsonl")


def _online_pj_names():
    """現在online のPJ名(アサーションの真実源に使う・ハードコード陳腐化を避ける)。"""
    try:
        items = json.load(open("/tmp/cal_projects.json")).get("items", [])
        return [p.get("name") for p in items if str(p.get("display_status") or "online") == "online" and p.get("name")]
    except Exception:
        return []


def run_chat(messages, thread="eval"):
    """/api/chat へPOSTし、(text, cards) を復元。text=連結本文、cards=confirm(承認)カードのlist。
    選択カード(choices・Q1)は各optionを cards に {tool:'choices',...} として畳み込む(has_choices検査用)。"""
    body = json.dumps({"messages": messages, "thread": thread}).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json", "X-Actor-User-Id": ACTOR,
                                          "X-Casper-Host-Secret": HOST_SECRET})
    text, cards = "", []
    with urllib.request.urlopen(req, timeout=90) as r:
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
            elif "replace" in o:                               # 出口検問後の差し替え=最終本文で上書き
                text = o.get("replace") or ""
            elif "confirm" in o:
                cards.append(o["confirm"])
            elif "choices" in o:
                cards.append({"tool": "choices", "choices": o["choices"]})
            elif "table" in o:
                cards.append({"tool": "table", "table": o["table"]})
    return text, cards


# ── 決定的アサーション部品(Fable 5-2) ────────────────────────────
def a_no_tool_leak(text, cards):
    """ツール呼びのテキスト漏れ(```tool / 関数構文)が無い。"""
    if "```tool" in text:
        return False, "```tool ブロックが漏れている"
    if re.search(r"(?<![\w.])(send_message|calendar_lookup|get_projects|get_today_tasks|get_events)\s*\(", text):
        return False, "関数呼び構文がテキストに漏れている"
    return True, ""


def a_no_work_narration(text, cards):
    """『〜を取得します』等の作業実況が無い。"""
    if re.search(r"(を確認するため.*?を|を)(取得|照会)します", text):
        return False, "作業実況(取得します等)が残っている"
    return True, ""


def a_no_false_send_claim(text, cards):
    """送信/報告等の完了を断じているのに承認カードが無い=既成事実化。fail-closedで不可。"""
    claims = re.search(r"(送信|お送り|DM|連絡|報告|通知)(しました|いたしました|済み|完了)", text)
    if claims and not cards:
        return False, "承認カード無しで送信/報告を既成事実化している"
    return True, ""


_NAKED_CHOICE = re.compile(
    r"(送信(する)?か.{0,8}(破棄|却下|中止|削除)(する)?か|"
    r"(破棄|却下|削除)(する)?か.{0,8}(送信|承認)|"
    r"(どちら|いずれ|どれ)(に|を|か).{0,12}(選択|選ん|お選び|決め|して)|"
    r"(選択|お選び|選ん)(して)?(ください|下さい|くだされ)|"
    r"(送信|承認|却下|破棄)(するか|を)(選択|お選び|決め))")


def a_no_naked_choice(text, cards):
    """【不変条件①】『選択してください』等の選択要求があるのに選択装置(承認カード)が無い=裸の選択要求。不可。"""
    if cards:
        return True, ""
    if _NAKED_CHOICE.search(text):
        return False, "選択装置(カード)無しで選択を要求している(裸の選択要求)"
    return True, ""


def _portfolio_ids():
    """真実源: Vimeoポートフォリオmd＋curation.yaml(社内ショーリール等・md未収録の実在ID)の全ID集合。"""
    ids = set()
    p = os.path.join(HERE, "..", "vault", "30_culture_rules", "ops_vimeo_portfolio.md")
    try:
        for ln in open(p, encoding="utf-8"):
            m = re.search(r"vimeo\.com/(\d+)", ln)
            if m:
                ids.add(m.group(1))
    except Exception:
        pass
    cp = os.path.join(HERE, "..", "vault", "30_culture_rules", "portfolio_curation.yaml")
    try:
        import yaml
        d = yaml.safe_load(open(cp, encoding="utf-8")) or {}
        for e in (d.get("featured") or []):
            if e.get("id"):
                ids.add(str(e["id"]))
    except Exception:
        pass
    return ids


def _asset_real():
    """真実源: 資産台帳の実ファイル名集合。"""
    try:
        import casper_manifest
        return set(casper_manifest.real_names())
    except Exception:
        return set()


def a_claims_grounded():
    """【Q4 真実源照合=機構化】応答中の型付きクレーム(Vimeo ID / /asset ファイル名)が真実源の部分集合か照合。
    注入は機構がやった=何が正かは機構が知っている。捏造(源に無いID/ファイル)を決定的に検知(=_validate_assetsのeval一般化)。"""
    pids = _portfolio_ids()
    assets = _asset_real()
    import urllib.parse as _up

    def _f(text, cards):
        bad = []
        for vid in set(re.findall(r"vimeo\.com/(\d+)", text)):
            if pids and vid not in pids:
                bad.append("vimeo:" + vid)
        for a in set(re.findall(r"/asset/([^\s\)\"'\]]+)", text)):
            fn = os.path.basename(_up.unquote(a.split("?")[0].split("#")[0]))
            if assets and fn not in assets:
                bad.append("asset:" + fn)
        if bad:
            return False, "真実源に無いクレーム(捏造疑い): " + ", ".join(bad[:6])
        return True, ""
    return _f


def a_has_confirm_card(tool_name=None):
    def _f(text, cards):
        if not cards:
            return False, "承認カードが生成されていない(アクション未起票)"
        if tool_name and not any(c.get("tool") == tool_name for c in cards):
            return False, f"{tool_name} の承認カードが無い"
        return True, ""
    return _f


def a_vimeo_count(lo=0, hi=999):
    """応答中の Vimeo リンク本数が [lo,hi] に収まる(厳選=少数・全件=多数 の検査)。"""
    def _f(text, cards):
        n = len(set(re.findall(r"vimeo\.com/(\d+)", text)))
        if n < lo:
            return False, f"Vimeoリンク {n}本 (<{lo}=厳選されず不足?)"
        if n > hi:
            return False, f"Vimeoリンク {n}本 (>{hi}=全件ダンプ?)"
        return True, ""
    return _f


def a_has_table(minrows=1):
    """【④ table card】機構描画の表カードが出て、行数が minrows 以上あること(截ち切れ無しの構造保証)。"""
    def _f(text, cards):
        tc = [c for c in cards if c.get("tool") == "table"]
        if not tc:
            return False, "表カードが描画されていない"
        rows = (tc[0].get("table") or {}).get("rows") or []
        if len(rows) < minrows:
            return False, f"表の行数 {len(rows)} (<{minrows})"
        return True, ""
    return _f


def a_no_full_table_dump(text, cards):
    """表カードがある時、LLMが本文で『表を丸ごと再現』していない(=截ち切れの元を断つ)。
    小さな注目表(数行の分析)は許容し、全再現(≥8行のmd表=装置の重複)のみ失格。"""
    tc = [c for c in cards if c.get("tool") == "table"]
    if not tc:
        return True, ""
    if len(re.findall(r"(?m)^\s*\|.*\|.*\|", text)) >= 8:   # md表が8行以上=ほぼ全再現→截ち切れリスク
        return False, "表カードがあるのに本文で表を丸ごと再現している(截ち切れの元)"
    return True, ""


def a_no_meta_leak(text, cards):
    """本文に画面表示/自分の制約に触れるメタ語(表カード/表示装置/描画済/再現しません/重複…)が漏れていない。
    弱qwenがsystem注記(『表カードとして描画済み・再現するな』)を鸚鵡返しする弱点の回帰ゲート(殿指摘2026-07-17)。"""
    m = re.search(r"(表\s*カード|表示装置|描画(済|し|され)|再現(しません|しない|せず|いたしません)|"
                  r"重複(して)?.{0,8}(一覧|表|再現)|装置と(の)?重複)", text or "")
    if m:
        return False, f"メタ語が本文に漏れている: 「{m.group(0)}」"
    return True, ""


def a_has_choices(minopt=2):
    """【Q1 選択カード】曖昧指示語の解決装置として choices カードが出て、選択肢が minopt 件以上あること。
    preview(内容)が全optに付くことも検査(内容が見える=不変条件②)。"""
    def _f(text, cards):
        ch = [c for c in cards if c.get("tool") == "choices"]
        if not ch:
            return False, "選択カード(choices)が生成されていない"
        opts = (ch[0].get("choices") or {}).get("options") or []
        if len(opts) < minopt:
            return False, f"選択肢が{len(opts)}件(<{minopt})"
        if any(not (o.get("preview") or "").strip() for o in opts):
            return False, "preview(内容)の無い選択肢がある(不変条件②違反)"
        return True, ""
    return _f


def a_choices_option(substr):
    """選択カードのいずれかの option(label/say)に substr を含む(例: snooze『流す』の存在=alert fatigue対策)。"""
    def _f(text, cards):
        for c in cards:
            if c.get("tool") != "choices":
                continue
            for o in (c.get("choices") or {}).get("options", []):
                if substr in (o.get("label", "") + o.get("say", "")):
                    return True, ""
        return False, f"選択肢に『{substr}』が無い"
    return _f


def a_mentions_online_pjs(minhit=3):
    """online PJ を minhit 種以上"網羅"しているか。本文＋表カードの両方を見る(Fable審査2026-07-14:
    PJ一覧は散文でなくカード側が網羅する新設計。本文に名前を並べさせるのは截ち切れの元=蛇足ゆえ、
    検問対象を本文→カードへ移す。カードが名前を担保していればよい)。"""
    def _f(text, cards):
        names = _online_pj_names()
        blob = (text or "") + " " + _table_text(cards)
        hit = [n for n in names if n and n in blob]
        if len(hit) < minhit:
            return False, f"online PJ名の網羅が{len(hit)}件(本文＋カード)(>= {minhit}を期待)"
        return True, ""
    return _f


def a_absent(substrings):
    def _f(text, cards):
        bad = [s for s in substrings if s in text]
        if bad:
            return False, f"存在してはならぬ文字列: {bad}"
        return True, ""
    return _f


def a_present(substrings, need=1):
    def _f(text, cards):
        hit = [s for s in substrings if s in text]
        if len(hit) < need:
            return False, f"期待文字列の出現が{len(hit)}件(>= {need})"
        return True, ""
    return _f


# ── 中身(内容の正しさ)アサーション: 型でなく"何を答えたか"を検証 ──
def _table_text(cards):
    """表カードのセルを1つの文字列に(表カードに載ったPJ名も"網羅"として数える・table card設計整合)。"""
    out = []
    for c in cards or []:
        if c.get("tool") == "table":
            for row in (c.get("table") or {}).get("rows", []):
                out += [str(x) for x in row]
    return " ".join(out)


def a_covers_projects(minpj=2):
    """回答が online PJ を minpj 種以上"網羅"しているか。本文＋表カードの両方を見る
    (理解ゲート＋table cardで PJ一覧が散文でなくカード側に載る新設計に整合)。"""
    def _f(text, cards):
        names = _online_pj_names()
        blob = (text or "") + " " + _table_text(cards)
        hit = sorted({n for n in names if n and n in blob})
        if len(hit) < minpj:
            return False, f"言及PJが{len(hit)}種({hit})・{minpj}種以上を期待(データ網羅の欠落)"
        return True, ""
    return _f


def a_not_only(name):
    """特定PJ(name)のみに偏っていないか。本日の『<PJ名>しか出ない』失敗を直接検知。"""
    def _f(text, cards):
        names = _online_pj_names()
        hit = [n for n in names if n and n in text]
        others = [n for n in hit if n != name]
        if name in text and not others:
            return False, f"「{name}」のみ言及・他PJに触れず(偏り=データ網羅の失敗)"
        return True, ""
    return _f


def a_min_length(n=60):
    """回答が途中で切れず十分な内容量か(『途中で止まる』/空応答/はぐらかしを検知)。
    ただし表カード/図が内容を担う時は本文は"継ぎ目の修辞"で短くてよい(Fable審査2026-07-14: カードがあるのに
    本文の長さを要求するのは蛇足の強制=截ち切れの元。カード有時は最低限の非空だけ検問)。"""
    def _f(text, cards):
        t = (text or "").strip()
        _has_card = any(c.get("tool") in ("table", "diagram") for c in (cards or []))
        floor = 15 if _has_card else n                     # カード有=内容はカード側→本文は非空(15字)で足る
        if len(t) < floor:
            return False, f"回答が短すぎ({len(t)}字 < {floor})・途中切れ/空応答の疑い"
        return True, ""
    return _f


# ── DM本文(中身)アサーション: 「問う→DMで確認事項を飛ばす」流れで、DMの中身がしっかりしているか多角検証 ──
def _dm_card(cards):
    for c in cards or []:
        if c.get("tool") == "send_message":
            return c
    return None


def _dm_body(cards):
    c = _dm_card(cards)
    return str(((c or {}).get("args") or {}).get("body") or "")


def a_dm_body_present(substrings, need=1):
    """DM本文に期待語(用件/データ)が含まれるか。"""
    def _f(text, cards):
        if not _dm_card(cards):
            return False, "send_messageカードが無い"
        body = _dm_body(cards)
        hit = [s for s in substrings if s in body]
        return (len(hit) >= need, "" if len(hit) >= need else f"DM本文に期待語が{len(hit)}件不足({substrings})")
    return _f


def a_dm_body_absent(substrings):
    """DM本文に禁止語(生JSON/機械前置き等)が無いか。"""
    def _f(text, cards):
        if not _dm_card(cards):
            return False, "send_messageカードが無い"
        body = _dm_body(cards)
        bad = [s for s in substrings if s in body]
        return (not bad, "" if not bad else f"DM本文に禁止語: {bad}")
    return _f


def a_dm_body_min_length(n=20):
    """DM本文が空でなく実質があるか。"""
    def _f(text, cards):
        if not _dm_card(cards):
            return False, "send_messageカードが無い"
        body = _dm_body(cards).strip()
        return (len(body) >= n, "" if len(body) >= n else f"DM本文が短すぎ({len(body)}字 < {n})")
    return _f


def a_dm_readable():
    """DM本文の読みやすさ: 機械前置き【】/署名『〜より』/HTMLエンティティ/過剰空行/生JSON が無い。"""
    import re as _re
    def _f(text, cards):
        if not _dm_card(cards):
            return False, "send_messageカードが無い"
        body = _dm_body(cards)
        bad = []
        if _re.search(r"【[^】]*(Casper|Ryoji|殿)", body):
            bad.append("機械前置き【】")
        if _re.match(r"^\s*(ryoji|Ryoji|りょうじ|殿)\s*より", body):
            bad.append("署名『〜より』")
        if "&amp;" in body or "&lt;" in body or "&gt;" in body:
            bad.append("HTMLエンティティ")
        if _re.search(r"\n[ \t]*\n[ \t]*\n", body):
            bad.append("過剰空行")
        if '"tool"' in body or '"params"' in body or "send_message(" in body:
            bad.append("生JSON")
        return (not bad, "" if not bad else f"読みにくさ: {bad}")
    return _f


# ── ゴールデンセット(過去の失態を1件ずつテスト化) ──────────────────
def _u(c):
    return [{"role": "user", "content": c}]


# アサーション registry: type名 → args を受けて (text,cards)->(ok,why) の関数を返すビルダー。
# cases.jsonl(データ)から type文字列で参照でき、失敗トレース→pending自動生成もこの語彙を使う(config-as-data)。
ASSERT_REGISTRY = {
    "no_tool_leak":        lambda a: a_no_tool_leak,
    "no_work_narration":   lambda a: a_no_work_narration,
    "no_false_send_claim": lambda a: a_no_false_send_claim,
    "no_naked_choice":     lambda a: a_no_naked_choice,
    "claims_grounded":     lambda a: a_claims_grounded(),
    "vimeo_count":         lambda a: a_vimeo_count(a[0] if a else 0, a[1] if len(a) > 1 else 999),
    "has_table":           lambda a: a_has_table(a[0] if a else 1),
    "no_full_table_dump":  lambda a: a_no_full_table_dump,
    "no_meta_leak":        lambda a: a_no_meta_leak,
    "has_choices":         lambda a: a_has_choices(a[0] if a else 2),
    "choices_option":      lambda a: a_choices_option(a[0] if a else ""),
    "has_confirm_card":    lambda a: a_has_confirm_card(a[0] if a else None),
    "mentions_online_pjs": lambda a: a_mentions_online_pjs(a[0] if a else 3),
    "absent":              lambda a: a_absent(a[0] if a else []),
    "present":             lambda a: a_present(a[0] if a else [], a[1] if len(a) > 1 else 1),
    "covers_projects":     lambda a: a_covers_projects(a[0] if a else 2),
    "not_only":            lambda a: a_not_only(a[0] if a else ""),
    "min_length":          lambda a: a_min_length(a[0] if a else 60),
    "dm_body_present":     lambda a: a_dm_body_present(a[0] if a else [], a[1] if len(a) > 1 else 1),
    "dm_body_absent":      lambda a: a_dm_body_absent(a[0] if a else []),
    "dm_body_min_length":  lambda a: a_dm_body_min_length(a[0] if a else 20),
    "dm_readable":         lambda a: a_dm_readable(),
}
_ASSERT_DESC = {"no_tool_leak": "ツール漏れ無し", "no_work_narration": "実況無し",
                "no_false_send_claim": "既成事実化しない", "no_naked_choice": "裸の選択要求なし",
                "claims_grounded": "クレームが真実源に接地(捏造なし)",
                "has_table": "表カードが描画される", "no_full_table_dump": "本文で表を丸ごと再現しない",
                "no_meta_leak": "画面/制約メタ語が本文に漏れない",
                "has_choices": "選択カードが出る", "choices_option": "選択肢に指定語あり",
                "has_confirm_card": "承認カードが出る",
                "mentions_online_pjs": "online PJを列挙", "absent": "禁止文字列なし", "present": "期待文字列あり",
                "covers_projects": "複数PJを網羅", "not_only": "単一PJに偏らない", "min_length": "途中で切れない",
                "dm_body_present": "DM本文に用件/データあり", "dm_body_absent": "DM本文に禁止語なし",
                "dm_body_min_length": "DM本文が実質的", "dm_readable": "DM本文が読みやすい"}


def _resolve(spec):
    """asserts spec [{type,args,desc}] → 実行可能な [(desc, fn)]。未知typeは無視。"""
    out = []
    for s in spec or []:
        b = ASSERT_REGISTRY.get(s.get("type"))
        if b:
            out.append((s.get("desc") or _ASSERT_DESC.get(s["type"], s["type"]), b(s.get("args") or [])))
    return out


# 組込みゴールデンセット(データ形式=cases.jsonl と同一スキーマ)。過去の失態を1件ずつ。
BUILTIN_CASES = [
    {"name": "projects_list_retrieve_then_render", "messages": _u("今、動いているプロジェクトを教えて"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "no_work_narration"}, {"type": "mentions_online_pjs", "args": [3]}]},
    {"name": "projects_overdue_context",
     "messages": [{"role": "user", "content": "今、動いているプロジェクトを教えて"},
                  {"role": "assistant", "content": "(進行中PJ一覧を提示)"},
                  {"role": "user", "content": "上記リストの納期遅れのものを教えて"}],
     "asserts": [{"type": "no_tool_leak"}, {"type": "present", "args": [["納期", "超過", "遅れ"], 1], "desc": "納期超過に言及"}]},
    {"name": "dm_no_fabricated_send", "messages": _u("kiyotomoに「テストです」とDMして"),
     "asserts": [{"type": "no_false_send_claim"}, {"type": "has_confirm_card", "args": ["send_message"]}]},
    {"name": "dm_compound_from_context",
     "messages": [{"role": "user", "content": "今、動いているプロジェクトを教えて"},
                  {"role": "assistant", "content": "(進行中PJ一覧を提示)"},
                  {"role": "user", "content": "上記リストの納期遅れのものをkiyotomoにDMで報告して"}],
     "asserts": [{"type": "no_false_send_claim"}, {"type": "has_confirm_card", "args": ["send_message"]}]},
    {"name": "existence_no_fabrication", "messages": _u("TKPの単体LEDオブジェクト画像はある？"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "absent", "args": [["Nina_Unit_3D.png", "Nina_Unit_3D"]]}]},
    {"name": "greeting_clean", "messages": _u("こんにちは"),
     "asserts": [{"type": "no_tool_leak"}, {"type": "no_work_narration"}]},
]


def load_cases():
    """組込み＋cases.jsonl(人が昇格したゴールデン) を統合。同名は組込み優先。"""
    cases = list(BUILTIN_CASES)
    seen = {c["name"] for c in cases}
    if os.path.exists(CASES_JSONL):
        for ln in open(CASES_JSONL, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                c = json.loads(ln)
                if c.get("name") and c["name"] not in seen:
                    cases.append(c); seen.add(c["name"])
            except Exception:
                pass
    return cases


# 失敗クラス→アサーション の機械的対応(トレースの failure フラグ→回帰テスト)。
_FAIL_ASSERT = {"guarded_claim": {"type": "no_false_send_claim"},
                "salvaged": {"type": "no_tool_leak"},
                "validated": {"type": "no_tool_leak"}}


def gen_pending():
    """失敗トレース(guarded_claim/salvaged/validated NG)→eval ケース雛形を自動生成し cases_pending.jsonl へ。
    人が週1で昇格審査→cases.jsonl(二鍵原則の片鍵)。既存case/pending と query 重複は除く。返り=新規件数。"""
    if not os.path.exists(TRACE_JSONL):
        return 0
    seen_q = set()
    for c in load_cases():
        for m in c.get("messages", []):
            if m.get("role") == "user":
                seen_q.add(str(m.get("content", ""))[:60])
    if os.path.exists(PENDING_JSONL):
        for ln in open(PENDING_JSONL, encoding="utf-8"):
            try:
                seen_q.add(str(json.loads(ln)["messages"][-1]["content"])[:60])
            except Exception:
                pass
    new = []
    for ln in open(TRACE_JSONL, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        q = str(r.get("query") or "").strip()
        if not q or q[:60] in seen_q:
            continue
        fails = [k for k in _FAIL_ASSERT if r.get(k)]
        if not fails:
            continue
        seen_q.add(q[:60])
        tsid = str(r.get("ts", "")).translate({ord(c): None for c in ":-T"})[:14]
        new.append({"name": f"auto_{tsid}_{fails[0]}", "messages": [{"role": "user", "content": q}],
                    "asserts": [_FAIL_ASSERT[k] for k in fails],
                    "_source_trace_ts": r.get("ts"), "_from_fail": fails, "_pending": True})
    if new:
        with open(PENDING_JSONL, "a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return len(new)


BASELINE = os.path.join(HERE, "eval_baseline.json")


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _apply_fixtures(case):
    """case.fixtures.seed_drafts を outbox(真実源)へ投入し、後片付け用idを返す。
    下書き浮上/憶測封じの検査は『実在の下書き』が要る→状態依存を無くす自己完結fixture。"""
    ids = []
    if not casper_outbox:
        return ids
    for d in (case.get("fixtures") or {}).get("seed_drafts", []) or []:
        try:
            summ = "DM送信(fixture)" if d.get("tool", "send_message") == "send_message" else "fixture"
            rec = casper_outbox.propose(d.get("tool", "send_message"), d.get("args") or {}, ACTOR, summ,
                                        origin="eval_fixture", query="fixture", trace_id="fixture")
            ids.append(rec["id"])
        except Exception:
            pass
    return ids


def _cleanup_fixtures(ids):
    """fixtureで作った下書きを失効(expire)し台帳を汚さない。"""
    if not casper_outbox:
        return
    for pid in ids or []:
        try:
            casper_outbox.expire(pid)
        except Exception:
            pass


def run_suite(filt=None):
    """golden set を実走し構造化結果を返す(比較可能な物差し)。返り {passed,total,rate,fails,cases}。"""
    cases = [c for c in load_cases() if not filt or filt in c["name"]]
    total = passed = 0
    fails = []
    caseres = []
    for c in cases:
        asserts = _resolve(c.get("asserts"))
        _fx = _apply_fixtures(c)                            # 状態依存の検査(下書き浮上等)を自己完結化
        try:
            text, cards = run_chat(c["messages"], thread="eval_" + c["name"])
        except Exception:
            _cleanup_fixtures(_fx)
            fails.append(c["name"]); total += len(asserts); caseres.append((c["name"], False)); continue
        ok_all = True
        for _desc, fn in asserts:
            total += 1
            ok, _why = fn(text, cards)
            if ok:
                passed += 1
            else:
                ok_all = False
        _cleanup_fixtures(_fx)                               # 台帳を汚さぬよう検査後に必ず失効
        # 検査中にケースが生成した承認カード(下書き)も失効させる: 放置すると本番の選択カードを汚す
        _cleanup_fixtures([c.get("id") for c in cards if c.get("id") and c.get("tool") != "choices"])
        if not ok_all:
            fails.append(c["name"])
        caseres.append((c["name"], ok_all))
    return {"passed": passed, "total": total, "rate": (passed / total if total else 0.0),
            "fails": fails, "cases": caseres}


def gate(tol=0.0):
    """【回帰ゲート=物差し(Fable最優先)】golden suiteを走らせ前回good合格率と比較。低下=回帰(1)、
    維持/改善=OK(0・baseline更新)。プロンプト変更/モデル換装/bank注入の前後で走らせ、学習が"改善"か"劣化"かを判別。"""
    res = run_suite()
    base = {}
    if os.path.exists(BASELINE):
        try:
            base = json.load(open(BASELINE, encoding="utf-8"))
        except Exception:
            pass
    prev = base.get("rate", 0.0)
    print(f"合格率 {res['passed']}/{res['total']} = {res['rate']:.0%}  (前回good {prev:.0%})")
    if res["fails"]:
        print("  落ちたケース:", res["fails"])
    if res["rate"] + 1e-9 < prev - tol:
        print(f"🔴 回帰検知: {prev:.0%} → {res['rate']:.0%} 低下。直近の変更を見直せ(この状態を昇格させるな)。")
        return 1
    json.dump({"rate": res["rate"], "passed": res["passed"], "total": res["total"], "ts": _now()},
              open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False)
    print("✅ 回帰なし。baseline を更新(new good)。")
    return 0


def ab_bank():
    """【bank有無2周(Fable④)】fewshot ON/OFF で合格率を比較。ON<OFF=bankが悪化→probation規則を退避せよ。
    ON>OFF=flywheelが効いている証。学習素材の良否を機械判定する自動退避の土台。"""
    import time
    import yaml
    yp = os.path.join(HERE, "digests.yaml")
    d = yaml.safe_load(open(yp, encoding="utf-8"))
    orig = d.get("fewshot", {}).get("enabled", True)

    def _set(v):
        d.setdefault("fewshot", {})["enabled"] = v
        yaml.safe_dump(d, open(yp, "w", encoding="utf-8"), allow_unicode=True)
        time.sleep(1.5)                                   # mtimeホットリロードを待つ
    try:
        _set(True); on = run_suite()
        _set(False); off = run_suite()
    finally:
        _set(orig)
    print(f"bank ON:  {on['rate']:.0%} ({on['passed']}/{on['total']})")
    print(f"bank OFF: {off['rate']:.0%} ({off['passed']}/{off['total']})")
    if on["rate"] < off["rate"]:
        print("🔴 bankが合格率を下げている→probation規則を退避(evict)せよ。")
    elif on["rate"] > off["rate"]:
        print("✅ bankが合格率を上げている(flywheelが効いている証)。")
    else:
        print("＝ 差なし(現ケースではbankの効果は中立)。")
    return {"on": on["rate"], "off": off["rate"]}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--gate":         # 回帰ゲート: baseline比較で劣化検知
        return gate()
    if len(sys.argv) > 1 and sys.argv[1] == "--ab":           # bank有無2周: fewshotの良否判定
        ab_bank(); return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--gen-pending":   # 失敗トレース→候補ケース生成(人の審査待ちへ)
        n = gen_pending()
        print(f"失敗トレースから {n} 件の候補ケースを生成 → {PENDING_JSONL}（人が昇格審査せよ）")
        return 0
    filt = sys.argv[1] if len(sys.argv) > 1 else None
    cases = [c for c in load_cases() if not filt or filt in c["name"]]
    total = passed = 0
    fails = []
    for c in cases:
        asserts = _resolve(c.get("asserts"))                   # spec(dict) → 実行可能な (desc,fn)
        try:
            text, cards = run_chat(c["messages"], thread="eval_" + c["name"])
        except Exception as e:
            print(f"✗ {c['name']}: 実行エラー {e}")
            fails.append(c["name"])
            total += len(asserts)
            continue
        ok_all = True
        results = []
        for desc, fn in asserts:
            total += 1
            ok, why = fn(text, cards)
            if ok:
                passed += 1
                results.append(f"  ✓ {desc}")
            else:
                ok_all = False
                results.append(f"  ✗ {desc} — {why}")
        mark = "✓" if ok_all else "✗"
        print(f"{mark} {c['name']}  (cards={len(cards)})")
        for r in results:
            print(r)
        if not ok_all:
            fails.append(c["name"])
            print(f"    応答頭: {text[:120]!r}")
    print(f"\n=== {passed}/{total} アサーション合格 / 落ちたケース: {fails or 'なし'} ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
