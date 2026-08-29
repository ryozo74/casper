#!/usr/bin/env python3
"""cmd_501 回帰ゲート(gate_web_search)。5系統:
  ① 一般検索が通る
  ② 社内問いで発火せぬ
  ③ 守秘語で弾く
  ④ 弾きすぎぬ(退行検査)
  ⑤ 社内語が混ざらぬ(送信ログの機械保証)
テストを実装に合わせて書かず、軍師実測の境界10件+cmd_501本文の実発話例をそのまま使う。
インメモリ純関数検証(外部通信なし・search()のクエリ構築部のみ検査、実際のWeb呼出はモック)。
全PASSで exit 0。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casper_web as W

try:                                    # pack由来の実PJ名を使う(cmd_491 M5 seam: engineに固有名を直書きせぬ)
    import pack_config as _pc
    _PJ = (_pc.get("examples", {}) or {}).get("project_names", ["社内案件"])[0]
except Exception:
    _PJ = "社内案件"

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ============================================================
# ① 一般検索が通る(build_query が発話をそのまま/削るだけで通す)
# ============================================================
q, reason = W.build_query("RTAB-Mapの最新版について調べて")
chk_true("① RTAB-Map 検索語が空でない", bool(q))
chk("① RTAB-Map reason=ok", reason, "ok")
chk_true("① RTAB-Map 語尾が削れている(調べては消える)", "調べて" not in (q or ""))

q2, reason2 = W.build_query("VFXについて調べて")
chk_true("① VFX 検索語が空でない", bool(q2))
chk("① VFX reason=ok", reason2, "ok")


# ============================================================
# ② 社内問いで発火せぬ(should_search が pj_status=unique で False)
# ============================================================
chk("② PJ一意解決→検索せぬ", W.should_search(f"{_PJ}の状況は？", pj_status="unique"), False)
chk("② Casper自身の問い→検索せぬ", W.should_search("Casperの使い方を教えて", asks_about_casper=True), False)
# ★2026-08-29 訂正: 従前この行は「通常の外部の問いなら検索する」を期待しており、
#   長らく赤のまま放置されていた。だが production は**意図して絞られた**——殿御裁定
#   「社内情報をもとに検索するのはダメ」を受けた止血で、①名乗る者のみ ②明示の検索意図
#   がある時のみ発火する白表に倒してある(casper_web.should_search)。
#   ★門を実装に合わせて甘くするのではない。**止血が黙って緩まぬこと**を此処で検める。
#   (恒久策=未知語判定・人名veto等は cmd_508 の宿題として残る)
chk("② 明示の検索意図＋名乗り有り→検索する",
    W.should_search("RTAB-Mapについて調べて", pj_status="none", uid="31"), True)
chk("② ★名乗らぬ者には外への口を開かぬ(直叩き・試験ハーネス)",
    W.should_search("RTAB-Mapについて調べて", pj_status="none", uid=None), False)
chk("② ★明示の検索意図が無ければ発火せぬ(既定closed・迷えば出さぬ側へ)",
    W.should_search("RTAB-Mapについて教えて", pj_status="none", uid="31"), False)
chk("② ★貼り付け(改行入り)は社内文書と見なし外へ出さぬ",
    W.should_search("次の仕様を調べて\n第1章 概要", pj_status="none", uid="31"), False)
chk("② ★長文(120字超)も外へ出さぬ",
    W.should_search("あ" * 121 + "を調べて", pj_status="none", uid="31"), False)
chk("② 疑問形/依頼形でない平叙文→検索せぬ", W.should_search("今日は晴れです", pj_status="none", asks_form=False), False)

# chat_server.py の実配線を模す: pj_status='unique' の turn は should_search() で止め、
# build_query() 側の呼出自体を行わない(=web_search_log.jsonl に1行も残らぬ・AC2の実証形)。
_fires_2 = W.should_search(f"{_PJ}の状況は？", pj_status="unique")
chk("② AC2実証: PJ名+状況は？→search()を呼ばぬゆえログ0件", _fires_2, False)


# ============================================================
# ③ 守秘語で弾く(_secrecy_hit・pack由来のcodenameを使う)
# ============================================================
_PACK = None   # 既定pack(bokan)を使う。secrecy_codenames=[AGL, V, EVA, E122, Number_i]
q3, reason3 = W.build_query("AGLの類似案件を調べて", pack=_PACK)
chk("③ AGL→検索せぬ(None)", q3, None)
chk_true("③ AGL→理由に守秘語と明記", "守秘語" in reason3 and "AGL" in reason3)

q4, reason4 = W.build_query("Number_iの資料を探して", pack=_PACK)
chk("③ Number_i→検索せぬ", q4, None)

q5, reason5 = W.build_query("Vについて調べて", pack=_PACK)
chk("③ 『Vについて調べて』→検索せぬ", q5, None)
q6, _ = W.build_query("Vの類似案件を調べて", pack=_PACK)
chk("③ 『Vの類似案件を調べて』→検索せぬ", q6, None)
q7, _ = W.build_query("Vの資料を探して", pack=_PACK)
chk("③ 『Vの資料を探して』→検索せぬ", q7, None)


# ============================================================
# ④ 弾きすぎぬ(退行検査・軍師実測10/10の境界)
# ============================================================
_NOT_BLOCKED = [
    "コンバトラーVのタスクを見せて",
    "コンバトラーV プロモーション映像",
    "Vimeoの動画を探して",
    "VFXについて調べて",
    "CSVで出して",
    "MVPについて",
    "動画をVimeoにアップする手順",
]
for t in _NOT_BLOCKED:
    q, reason = W.build_query(t, pack=_PACK)
    chk_true(f"④ 巻き添え無し: {t!r}", q is not None and reason == "ok")


# ============================================================
# ⑤ 社内語が混ざらぬ(送信前assert・機械保証)
# ============================================================
try:
    W._assert_query_subset(f"{_PJ} 状況", "RTAB-Mapについて調べて")
    chk_true("⑤ 発話に無い語→assert失敗するはず", False)
except ValueError:
    chk_true("⑤ 発話に無い語→assert失敗(正)", True)

try:
    W._assert_query_subset("RTAB-Map 最新版", "RTAB-Mapの最新版について調べて")
    chk_true("⑤ 発話部分集合→assert成功(正)", True)
except ValueError:
    chk_true("⑤ 発話部分集合→assert成功のはず", False)

# search() 経由でも同じ保証が効くこと(cli_text_fnをモックしAPI非依存で検査)
_pj_text = f"{_PJ}の状況は？"
r = W.search(_pj_text, cli_text_fn=lambda p, allow=None: "モック応答")
chk("⑤ 社内問いに近い文言でも守秘/取引先語なければ通る場合がある→ここではsecrecy語なしゆえ実行される",
    r["ok"], True)
chk_true("⑤ query_sentは発話の部分集合", all(tok in _pj_text for tok in (r["query"] or "").split()))

r2 = W.search("AGLの類似案件を調べて", cli_text_fn=lambda p, allow=None: "呼ばれてはならぬ")
chk("⑤ AGL→search()もblocked", r2["ok"], False)
chk_true("⑤ AGL→blocked理由が記録される", bool(r2["blocked"]))


# ============================================================
# ⑥ grounding_gate(出口検問)専用検査(subtask_501_impl2・軍師QC指摘の穴埋め)
#    突然変異(iii): grounding_gateを素通しにする/印判定を常にTrueにする変異を
#    適用してもgate_web_search.pyが緑のまま——という構造的な穴を塞ぐ。
# ★cmd_508病四是正: 押印前に使用証拠(答文とURL周辺文のn-gram一致)を要求するようになった。
#   result["text"]を持たぬ/答文と無関係なurlsは、もう機械的に押印されぬ(AC6)。
# ============================================================
_R_OK_NOMARK_NOTEXT = {"ok": True, "urls": ["https://example.com/a", "https://example.com/b"]}
_note_notext = W.grounding_gate("これはRTAB-Mapの説明です。", _R_OK_NOMARK_NOTEXT)
chk("⑥ AC6 result[text]が無い(使用証拠を判定できぬ)→何も貼らぬ(出典欠落の方が安全側)",
    _note_notext, "これはRTAB-Mapの説明です。")

# 実際に答文へ採り入れた出典(text中でURL近傍の文言が答文と重なる)は付く
# ★grounding_gateはURLが属する文(直前の文境界〜直後の文境界)のみを周辺文とみなすゆえ、
# 引用元の事実とURLは同一文中に置く(実運用のcli_text_fn出力もURL直近に事実を書く形が通常)。
_SRC_TEXT_USED = (
    "RTAB-MapはSLAMの一手法であり最新版はv0.21です https://example.com/a 。"
    "別件のニュースはこちら https://example.com/b を参照(本文には無関係)。"
)
_R_OK_MIXED = {"ok": True, "urls": ["https://example.com/a", "https://example.com/b"], "text": _SRC_TEXT_USED}
_answer_used_a = "RTAB-MapはSLAMの一手法であり最新版はv0.21です。"
_note_mixed = W.grounding_gate(_answer_used_a, _R_OK_MIXED)
chk_true("⑥ AC6 答文に採り入れた出典(a)のみ押印される", "https://example.com/a" in _note_mixed)
chk_true("⑥ AC6 答文に採り入れておらぬ出典(b)は押印されぬ(無出所の答に偽の出所を捺さぬ)",
         "https://example.com/b" not in _note_mixed)

# 一致ゼロ(答文が検索結果と無関係)なら何も貼らぬ
_answer_unrelated = "これは全く別件の話です。"
_note_zero = W.grounding_gate(_answer_unrelated, _R_OK_MIXED)
chk("⑥ AC6 一致ゼロ→何も貼らぬ", _note_zero, _answer_unrelated)

_R_OK_MARKED = {"ok": True, "urls": ["https://example.com/a"]}
_answer_marked = "これはRTAB-Mapの説明です(Web: https://example.com/a)。"
_note2 = W.grounding_gate(_answer_marked, _R_OK_MARKED)
chk("⑥ ok=True・印あり→何も足さぬ(不変)", _note2, _answer_marked)

_answer_noresult = "これは案内文です。検索は実行しておりません。"
_note3 = W.grounding_gate(_answer_noresult, None)
chk("⑥ result=None(検索せず)→何も足さぬ(不変)", _note3, _answer_noresult)


n = len(results)
p = sum(results)
print(f"\n=== gate_web_search: {p}/{n} = {p * 100 // n if n else 0}% ===")
sys.exit(0 if p == n else 1)
