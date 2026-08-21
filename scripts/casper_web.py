#!/usr/bin/env python3
"""cmd_501: 一般の調べ物のためのネット検索(社外)。

殿の御裁定(本則): 社員が自分の言葉で外の事を尋ねる時のみ検索する。Casperが社内情報を
もとに検索語を作ること(社内から拾うた語を検索語に混ぜる)は禁ずる。守秘コードネームは
語られた言葉のままでも弾く。

設計(軍師・実測に基づく):
  検索先 = claude CLI の WebSearch(既存 claude_cli_text 経由・正規の道具・新規外部通信を増やさぬ)。
  検索語 = qwen/claude に作らせぬ。build_query() が社員の発話から【削るだけ】で作る
           (足す操作を一切持たぬ設計。検索語⊆発話が構造的に保証される)。
  守秘検問 = _secrecy_hit()。語境界＋日本語文脈の非対称判定(前が日本語ならコードネームでない)。
  送信前assert = search() が実際に外へ投げる直前に「クエリの全トークンが発話中に在るか」を
           機械的に確認する(AC5の機械保証)。破れれば送信せず例外。
  記録 = web_search_log.jsonl に1検索1行(blocked時も記録=弾いた事実も証跡)。

pack.yaml は読むだけ(M5 seam)。本モジュールは書き換えぬ。
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))

_JP = r"぀-ヿ一-鿿"   # ひらがな・カタカナ・漢字(コードネームの前方非対称判定に使う)

LOG_PATH = os.path.join(HERE, "web_search_log.jsonl")
BLOCK_PATH = os.path.join(HERE, "casper_web_block.yaml")

# --- 論点a: 検索語の切り出し(削るだけ・一語も足さぬ) ---
# 「〜を調べて/教えて/知りたい/検索して」等の依頼形語尾のみを削る。中身には一切手を触れぬ。
_REQUEST_TAIL_RE = re.compile(
    r"(に(つい)?て)?\s*"
    r"(調べ(て|てくれ|てください|て下さい|てほしい|てもらえ(る|ますか)?)|"
    r"検索(して|してくれ|してください|して下さい)|"
    r"探(して|してくれ|してください|して下さい)|"
    r"教え(て|てくれ|てください|て下さい)|"
    r"知りたい|知りたいです|知りたいんです(けど|が)?)"
    r"[。.、,！!？?]*\s*$"
)


def _strip_request_form(user_text):
    """依頼形の語尾だけを削る。他は一切変更しない(足さぬ・要約せぬ)。"""
    q = (user_text or "").strip()
    if not q:
        return q
    stripped = _REQUEST_TAIL_RE.sub("", q).strip()
    return stripped if stripped else q   # 全部削れて空になったら安全側で原文を返す(質より安全)


def _mk_code_re(code):
    """コードネーム1件の検問正規表現を作る(軍師設計)。
    ASCII語: 語境界(前後)を要求。ただし前方は「直前が日本語文字でない」ことも要求する
    (非対称: 「コンバトラーV」のような案件名の一部を弾かない)。後方は助詞を許す通常の語境界。
    日本語コードネームはそのまま(境界概念が薄いASCII語と違い、素の包含で十分弁別的)。"""
    if re.fullmatch(r"[A-Za-z0-9_]+", code):
        return re.compile(rf"(?<![A-Za-z0-9_{_JP}])" + re.escape(code) + r"(?![A-Za-z0-9_])")
    return re.compile(re.escape(code))


_code_re_cache = {"codes": None, "res": []}


def _codename_res(pack=None):
    try:
        import pack_config
        codes = pack_config.get("secrecy_codenames", [], pack) or []
    except Exception:
        codes = []
    if codes != _code_re_cache["codes"]:
        _code_re_cache["codes"] = list(codes)
        _code_re_cache["res"] = [(c, _mk_code_re(c)) for c in codes]
    return _code_re_cache["res"]


def _secrecy_hit(text, pack=None):
    """守秘コードネームが text 中に(語境界的に)在れば、そのコードネームを返す。無ければ None。"""
    t = text or ""
    for code, rx in _codename_res(pack):
        if rx.search(t):
            return code
    return None


# --- 論点c: 取引先名の遮断(現裁定=案3=弾かぬ・casper_web_block.yaml は空/コメントアウト状態) ---
_block_cache = {"mtime": 0.0, "names": []}


def _blocklist():
    """casper_web_block.yaml(取引先名の遮断リスト)を読む。mtimeホットリロード。
    第1便では空/コメントアウト状態で出荷(殿裁可が出るまで案3=現裁定=一切弾かず、を維持)。"""
    try:
        m = os.path.getmtime(BLOCK_PATH)
    except Exception:
        return []
    if m == _block_cache["mtime"]:
        return _block_cache["names"]
    names = []
    try:
        import yaml
        data = yaml.safe_load(open(BLOCK_PATH, encoding="utf-8")) or {}
        names = data.get("block", []) or []
    except Exception:
        names = []
    _block_cache.update({"mtime": m, "names": names})
    return names


def _blocklist_hit(text):
    t = text or ""
    for name in _blocklist():
        if name and str(name) in t:
            return name
    return None


# --- 論点a: 発火判定(qwenのtool呼出に委ねぬ・機構が判ずる) ---
# ★2026-08-17 止血(将軍・Fable診断「逆さ門」): 既定を open から closed へ反転した。
#
# 【何が起きていたか — 実ログ web_search_log.jsonl の実利用42件より】
#   「これで大丈夫です。elvis/ou にDMしてください」        → 外部送信(楽天のELVIS商品検索が返る)
#   「Excel から新規プロジェクト起票…りょうじにDMをお願い」 → ★社内の障害報告が人名ごと外部へ
#   「auroraにアップして」                                 → 外部送信(AWS Auroraのマニュアルが返る)
#   「tkpのスケジュールある？」                            → 外部送信(貸会議室業者tkp.jpが返る)
#
# 【真因】旧実装は「疑問形または依頼形」なら既定で True を返した。
#   依頼形 _ASK_DELEGATE_RE は てくれ|てください|ておいて|お願い|て$ ——
#   ★社員がCasperに物を頼む言葉のほぼ全部である。
#   止める側は「内の事と確定できた数パターン」の除外のみ。
#   ★開いた世界の道具を、閉じた除外リストで守ることはできぬ(Fable)。
#
# 【殿の御裁定(2026-08-06)】
#   「一般の調べ物ならOK。★社内情報をもとに検索するのはダメ。
#     ユーザーがXXに関してのYYを調べて、ならOK」
#   ——旧実装には「外の事」を判ずる機構が無く、御裁定が守られていなかった。
#
# 【この止血の方針】明示の検索意図がある時【のみ】発火する(白表)。
#   ★検索し損ねは「Webで調べまするか？」の一枚のカードで回収できるが、
#   ★一度外へ出た発話は回収できぬ。ゆえに迷えば発火せぬ側に倒す。
#   恒久策(未知語判定・人名veto・貼付veto等)は cmd_508 で扱う。
_EXPLICIT_SEARCH_RE = re.compile(
    r"(ググ|ぐぐ|検索し|検索www|ウェブで|webで|ネットで|ネット検索|"
    r"調べて|調べられ|調べる|お調べ|しらべて|"
    r"最新(の)?(情報|ニュース|動向|事情)|世間で|一般的に(は)?(どう|なん)|"
    r"世の中で|外部の|他社(では|は)|市場(で|の)|相場)"
)


def should_search(user_text, pj_status="none", asks_about_casper=False, asks_form=True, uid=None):
    """このturnで検索を発火すべきか。呼出側(chat_server.py)が既存の確定的機構
    (_pj_resolve/_asks_about_casper/疑問形・依頼形regex)の結果を渡す(ここでは再計算しない=単一機構の原則)。
    pj_status: _pj_resolve(q)[0] の値。'unique'なら社内の問い→検索せぬ。
    asks_about_casper: _asks_about_casper(q) is True の値。Casper自身の問い→検索せぬ。
    asks_form: 疑問形/依頼形の形をしているか。
    uid: 発話者。★名乗らぬ者に外への口は開かぬ(Fable進言)。"""
    if not asks_form:
        return False
    if pj_status == "unique":
        return False
    if asks_about_casper:
        return False
    # ★以下、止血で足した白表条件。いずれも「発火せぬ」側へ倒す。
    if uid in (None, "", "None"):
        return False                                   # 素性の知れぬ呼出(直叩き・試験ハーネス)は通さぬ
    ut = user_text or ""
    if "\n" in ut or len(ut) > 120:
        return False                                   # 貼り付け(仕様書・ログ等)は社内文書と見なす
    if not _EXPLICIT_SEARCH_RE.search(ut):
        return False                                   # ★明示の検索意図が無ければ発火せぬ(既定closed)
    return True


# --- 論点a: 検索語の構築(build_query) ---
def build_query(user_text, pack=None):
    """検索語は社員の発話からのみ作る。digest/rag/Calendarの語を一切足さぬ。
    戻り: (query:str|None, reason:str)  None=検索せぬ(理由つき)"""
    ut = user_text or ""
    q = _strip_request_form(ut)
    hit = _secrecy_hit(q, pack) or _secrecy_hit(ut, pack)
    if hit:
        return None, f"守秘語({hit})を含むゆえ検索いたしませぬ"
    bhit = _blocklist_hit(q) or _blocklist_hit(ut)
    if bhit:
        return None, f"取引先名({bhit})を含むゆえ検索いたしませぬ"
    if not q:
        return None, "検索語が空ゆえ検索いたしませぬ"
    return q, "ok"


def _assert_query_subset(query, user_text):
    """AC5の機械保証: query の全トークンが user_text 中に文字列として現れることを確認する。
    破れれば送信せず例外(送信前assert・将軍指定の必須要件)。"""
    ut = user_text or ""
    for tok in query.split():
        if tok not in ut:
            raise ValueError(f"web_search assert失敗: token={tok!r} が user_text に無い(送信せず)")


def _log_search(uid, thread, user_text, query_sent, provider, blocked, result_urls, elapsed):
    rec = {
        "ts": time.time(), "uid": uid, "thread": thread, "user_text": user_text,
        "query_sent": query_sent, "provider": provider, "blocked": blocked,
        "result_urls": result_urls or [], "elapsed": elapsed,
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


_URL_RE = re.compile(r"https?://[^\s\)\]>]+")


def search(user_text, uid=None, thread=None, pack=None, cli_text_fn=None):
    """検索の実行と検問を一括で行う。呼出側はこれ1つを呼べばよい。
    戻り: dict {ok, query, text, urls, blocked, reason}
      ok=False の時は検索を実行しておらぬ(blocked/reason参照)。ok=True時は claude CLI の
      WebSearch/WebFetch を通した生テキストと、そこから拾えたURL一覧を返す。
    cli_text_fn: 実行する検索関数(必須・呼出側=chat_server.pyがclaude_cli_textを渡す)。
      ★ここで`import chat_server`をしてはならぬ——chat_server.pyはモジュール末尾で
      ThreadingHTTPServer(...).serve_forever()を`if __name__=="__main__"`ガード無しに実行して
      おり、稼働中プロセスの内部から自分自身を`import chat_server`すると新たなモジュール実行として
      同じportへ再bindしようとしEADDRINUSEで例外になる(cmd_501実測で発見)。cli_text_fn省略時は
      未検索のまま案内文を返す(fail-closed・外部送信を試みぬ)。"""
    t0 = time.time()
    query, reason = build_query(user_text, pack)
    if query is None:
        _log_search(uid, thread, user_text, None, "claude_cli", reason, [], time.time() - t0)
        return {"ok": False, "query": None, "text": "", "urls": [], "blocked": reason, "reason": reason}
    try:
        _assert_query_subset(query, user_text)
    except ValueError as e:
        _log_search(uid, thread, user_text, None, "claude_cli", str(e), [], time.time() - t0)
        return {"ok": False, "query": None, "text": "", "urls": [], "blocked": str(e), "reason": str(e)}
    if cli_text_fn is None:
        _log_search(uid, thread, user_text, None, "claude_cli", "cli_text_fn未指定", [], time.time() - t0)
        return {"ok": False, "query": None, "text": "", "urls": [], "blocked": "cli_text_fn未指定", "reason": "cli_text_fn未指定"}
    prompt = (
        "以下の語について、Web検索(WebSearch)を用いて最新の情報を調べ、簡潔に日本語で要約せよ。"
        "検索で得た事実には必ず出典URLを添えよ。検索語以外の推測(社内事情・固有名詞の補完等)は行うな。\n\n"
        f"検索語: {query}"
    )
    try:
        raw = cli_text_fn(prompt, allow=["WebSearch", "WebFetch"])
    except Exception as e:
        # ★2026-08-17 是正(Fable診断): 旧実装はここで raw に "[web_search error] ..." を入れ、
        # ★そのまま ok=True で返していた。エラー文が「ネットで調べた事」として
        # ★プロンプトへ注入される——「失敗とゼロを別の出口へ」を検索module自身が破っていた。
        elapsed = time.time() - t0
        _log_search(uid, thread, user_text, query, "claude_cli", f"検索失敗: {e}", [], elapsed)
        return {"ok": False, "query": query, "text": "", "urls": [],
                "blocked": None, "reason": f"検索失敗: {e}"}
    urls = list(dict.fromkeys(_URL_RE.findall(raw or "")))
    elapsed = time.time() - t0
    _log_search(uid, thread, user_text, query, "claude_cli", None, urls, elapsed)
    return {"ok": True, "query": query, "text": raw or "", "urls": urls, "blocked": None, "reason": "ok"}


# --- 論点d: 結果の札付け ---
def format_result_block(result):
    """検索結果を専用の見出しで注入するブロックを組む(Calendar/vaultの注入とは別見出し)。
    result は search() の戻り。ok=False なら空文字列(過剰注入を避ける)。"""
    if not result or not result.get("ok"):
        return ""
    return "\n\n## ネットで調べた事(社外・出所URL付き)\n" + result.get("text", "")


WEB_PROMPT_RULE = (
    "\n\n上の『ネットで調べた事』は社外の情報である。社内の記録(Calendar/vault)と"
    "混ぜて述べるな。ネット由来を述べる時は必ず『(Web: <URL>)』と出所を添えよ。"
)


_WEB_MARK_RE = re.compile(r"\(Web[:：]")

# ★2026-08-17 是正(cmd_508病四・Fable診断): 旧実装は「(Webの印が無い」というだけの理由で
# 使われもしなかったURLを機構が末尾に押印していた——出所を偽らせぬはずの検問が
# 無出所の答に偽の出所を捺す事故。押印の前に★使用の証拠(答文とURL周辺文の文字n-gram一致)を
# 要求し、一致した出典に限って付す。一致ゼロなら何も貼らぬ(出典欠落の方が出典過多より安全)。
_GATE_NGRAM_N = 3          # 文字n-gram長(日本語は空白無しゆえ語分割でなくn-gram・fewshot_digestと同型)
_GATE_NGRAM_MIN_OVERLAP = 2   # この個数以上一致すれば「使用された」とみなす閾値(実測により調整)
_GATE_URL_WINDOW_FALLBACK = 120   # 文境界が見当たらぬ場合のみ使う前後文字数の保険


def _char_ngrams(s, n=_GATE_NGRAM_N):
    s = re.sub(r"[\s、。・！？\n]", "", str(s or ""))
    if len(s) < n:
        return set()
    return set(s[i:i + n] for i in range(len(s) - n + 1))


def _url_context_windows(text, urls):
    """各URLについて、そのURLが属する文(直前の文境界〜直後の文境界)を「周辺文」として切り出す。
    ★文境界(。/\n)で区切ることで、隣接するURLの文が混じり込み使用証拠が誤って重複判定される
    (別出典の文がお互いの周辺窓に漏れ込む)ことを防ぐ。境界が見当たらぬ長文のみ固定長窓で保険を掛ける。
    同一URLが複数回出れば全出現の窓を束ねる(見落とし防止)。"""
    out = {}
    for u in urls:
        windows = []
        start = 0
        while True:
            idx = text.find(u, start)
            if idx < 0:
                break
            lo_boundary = max(text.rfind("。", 0, idx), text.rfind("\n", 0, idx))
            hi_dot = text.find("。", idx + len(u))
            hi_nl = text.find("\n", idx + len(u))
            candidates = [h for h in (hi_dot, hi_nl) if h >= 0]
            hi_boundary = min(candidates) if candidates else -1
            lo = lo_boundary + 1 if lo_boundary >= 0 else max(0, idx - _GATE_URL_WINDOW_FALLBACK)
            hi = hi_boundary + 1 if hi_boundary >= 0 else min(len(text), idx + len(u) + _GATE_URL_WINDOW_FALLBACK)
            windows.append(text[lo:hi])
            start = idx + len(u)
        out[u] = " ".join(windows)
    return out


def grounding_gate(answer_text, result):
    """出口検問: 検索を実行したturnの応答に「(Web」の印が一つも無ければ、機構が末尾へ
    出所一覧を付す(cmd_497の_GROUNDING_NOTEと同型・文言だけに頼らぬ機械的担保)。
    ★cmd_508是正: 押印前に使用証拠(答文とURL周辺文のn-gram一致)を要求し、
    一致した出典に限って付す。一致ゼロのURLは貼らぬ。全URLが不一致なら何も足さぬ。
    検索を実行せなんだ(result is None または ok=False)turnでは何も足さぬ。"""
    if not result or not result.get("ok"):
        return answer_text
    if _WEB_MARK_RE.search(answer_text or ""):
        return answer_text
    urls = result.get("urls") or []
    if not urls:
        return answer_text
    ans_grams = _char_ngrams(answer_text)
    if not ans_grams:
        return answer_text
    src_text = result.get("text") or ""
    windows = _url_context_windows(src_text, urls)
    used = []
    for u in urls:
        win_grams = _char_ngrams(windows.get(u, ""))
        if len(ans_grams & win_grams) >= _GATE_NGRAM_MIN_OVERLAP:
            used.append(u)
    if not used:
        return answer_text
    note = "\n\n※上記のうちネット由来: " + ", ".join(used)
    return (answer_text or "") + note
