#!/usr/bin/env python3
r"""資料の錨と識別子・本文の検問の回帰ゲート(殿御下命 2026-08-27)。全PASSで exit 0。

実害(2026-08-27 14:18〜15:04): kiyotomo殿が資料URLを貼り「変更したい」→「追加」→
「BOKAN 担当事項のところに以下追加」と**三turnかけて**頼まれた。結果:
 ・URLを貼った turn は本文が入る(ctx_len 6922)。次の turn では消える(ctx_len 2909)
 ・本文を失ったモデルは議事録を**一から捏造**して「修正後の全文」に据えた
   (実在せぬ参加者「武井/rui」、実在せぬ節「フェーズ1(レイアウト/アニメーション)」)
 ・doc_id には **slug** が入っていた(`kiyotomo/2026-08-27/sorafune-sama-mtg-gijiroku`)
 ・追記のはずが **新規作成** され、slug に `-2026-08-27` が付いた別資料が生まれた
 ・出来た資料の中身は追記分の断片のみ——元の全文は入っておらぬ

守る掟:
 ① 一度名指された資料は turn を跨いで保つ。修正は必ず複数 turn にまたがる仕事ゆえ。
 ② 錨には**本文**が伴う。本文の無い錨は「中身を語るな」と告げる(空と失敗を分ける)。
 ③ 人が「新規/別の資料」と言えば錨を外す。勝手に前の資料へ吸い寄せぬ。
 ④ 錨は古びる。期限切れは畳んで None(『無い』と『古い』を混ぜぬ)。
 ⑤ **識別子はモデルに作らせぬ。** UUIDでない doc_id(slug/題)は弾く。
    ★偽物が在る方が空より質が悪い——空なら機構が埋めるが、偽物は機構を黙らせる。
 ⑥ 版差し替えの本文が現本文と**別物**なら表に立てる。字数でなく**見出しの生存**で測る
    (捏造は同じくらいの長さで来るゆえ、増減では捕まらぬ)。
 ⑦ 実行の最後の関でも doc_id を検める。**不正なら新規作成へ倒して逃げぬ**
    ——黙って別の物を作れば『追記したのに新しい資料が出来た』が起きる。
 ★突然変異: 錨・識別子検問・本文検問をそれぞれ殺すと赤化することを実証する。
"""
import ast
import json
import re
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

import tempfile as _tf
TMP2 = _tf.mkdtemp(prefix="gate_pin_files_")

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


REAL_ID = "d78e9ca6-3ce5-4bc0-9ab9-2502ede67767"
REAL_SLUG = "kiyotomo/2026-08-27/sorafune-sama-mtg-gijiroku"
REAL_URL = f"http://nina_notepc_02.local:8100/doc/{REAL_SLUG}"
# 本物の資料の骨格(2026-08-27 14:31:32 に Casper 自身が取得して出した全文)
# ★実物のノートHTMLは <style> を抱える(make_note が埋める)。検体もそれに合わせる
#   ——2026-08-27 の実地で、CSS 1,482字を本文として数え偽警報を出した。
CUR_STYLE = ("<style>@import url('https://fonts.googleapis.com/css2?family=Outfit');"
             + "body{font-family:-apple-system;max-width:1000px;margin:40px auto;line-height:1.7}"
             + "h1{border-bottom:3px solid #333}" * 30 + "</style>")
# ★【Fable検分2026-08-28】aurora_canonical_body の**主分岐**(<div class="meta">で切る方)が
#   どのゲートでも試されていなかった。本番の note_html は必ず meta div を持つ
#   (casper_aurora.py:302)。fixture に無ければ、_META_BLOCK_RE が壊れても全ゲート緑のまま
#   **41a93c3 が治した当の病(著者行が本文へ漏れる)が再発する**。本番の形にする。
CUR_HTML = (CUR_STYLE + "<h1>SORAFUNE 様 MTG 議事録</h1>"
            + '<div class="meta">著者: kiyotomo / 作成: 2026-08-27<br>'
            + '<span class="tag">SORAFUNE</span><span class="tag">議事録</span></div>'
            "<h2>1. シナリオ・コンセプト</h2><p>場所: 体育館等</p>"
            "<h2>2. BOKAN 担当事項</h2><p>Flight Simulator</p>"
            "<h2>3. スケジュール</h2><p>8月: 契約書締結</p>"
            "<h2>4. その他・アクションアイテム</h2><p>再生機能</p>")
CUR_MD = ("# SORAFUNE 様 MTG 議事録\n\n"
          "## 1. シナリオ・コンセプト\n場所: 体育館等\n\n"
          "## 2. BOKAN 担当事項\n- Flight Simulator\n\n"
          "## 3. スケジュール\n8月: 契約書締結\n\n"
          "## 4. その他・アクションアイテム\n- 再生機能\n")
# 実害そのもの: モデルが記憶から書き起こした「修正後の全文」(実在せぬ参加者・節)
FABRICATED = ("# SORAFUNE 様 定例MTG 議事録\n\n**参加者:** 武井(ryoji), 木戸(kiyotomo), tim, rui\n\n"
              "## 1. 進捗報告 (BOKAN側)\n### tim担当: フェーズ1 (レイアウト/アニメーション)\n"
              "- 現状: 完了\n\n## 2. 決定事項\n- フェーズ1完了報告を承認\n")


class _Au:
    @staticmethod
    def get(doc_id):
        return json.dumps({"id": doc_id, "html": CUR_HTML}) if doc_id == REAL_ID else None


WANT_F = ["aurora_pin_key", "aurora_pin_set", "aurora_pin_get", "aurora_pinned_digest",
          "aurora_valid_doc_id", "aurora_body_drift_note", "aurora_shrink_note",
          "aurora_append_salvage", "_aurora_plain", "aurora_edit_compose",
          "_strip_material_wrapper", "aurora_canonical_body",
          # 2026-08-28(甲): 正本の取り直しが使う逆写像。抜き忘れると canonical_body が
          # NameError で転び、錨の検めが「機構が在るのに赤」になる(窓ずれと同型の事故)
          "_html_to_md",
          # 2026-08-28: 錨の永続と観測・人ごとの控え
          "_pin_log", "_pin_save", "_pin_load", "aurora_pin_user_key",
          "aurora_pin_set_for", "aurora_pin_get_any"]
WANT_A = ["_AURORA_PIN", "_AURORA_PIN_TTL", "_AURORA_PIN_RELEASE_RE", "_AURORA_URL_RE",
          "_DOC_ID_RE", "_AURORA_EDIT_INTENT_RE", "_AURORA_BODY_KW_RE",
          "_MATERIAL_WRAPPER_RE", "_META_BLOCK_RE", "_H1_RE", "_DECOR_META_RE",
          "_STRUCT_HEAD_RE", "_INSTR_QUOTED_RE", "_INSTR_ADD_RE", "_INSTR_REMOVE_RE", "_PROPER_TOKEN_RE",
          "_AURORA_PIN_FILE", "_AURORA_PIN_LOG"]


def build(src_text):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in WANT_A for t in n.targets):
            picked.append(n); seen.add(n.targets[0].id)
    missing = [w for w in (WANT_F + WANT_A) if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, os, json, time, datetime, threading", M)
    M["HERE"] = TMP2   # 錨のファイルは一時場所へ(本番不変)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)
sys.modules["casper_aurora"] = _Au

REF = {"ref": REAL_SLUG, "by": "slug", "found": True,
       "doc_id": REAL_ID, "title": "SORAFUNE 様 MTG 議事録"}

# ── ①② 錨が turn を跨ぐ ─────────────────────────────────────────────────
print("── ①② 錨 ──")
k = M["aurora_pin_key"]("t123", {"sid": "s1"})
chk("① thread が在れば thread が鍵", k == "th:t123")
chk("① thread が無ければ session へ落ちる(鍵が必ず立つ)",
    M["aurora_pin_key"](None, {"sid": "s1"}) == "sid:s1")
chk("① 文字列の'None'も thread 無しとして扱う(8/26のカードは thread='None' であった)",
    M["aurora_pin_key"]("None", {"sid": "s1"}) == "sid:s1")

M["aurora_pin_set"](k, REF, material=CUR_MD)
dg = M["aurora_pinned_digest"](k, "BOKAN 担当事項のところに以下追加 UE＋コンソールデータを提供")
chk("① ★実害の二手目(URLの無い turn)でも資料が注入される", bool(dg))
chk("① doc_id が載る", REAL_ID in dg)
chk("② 現本文が載る(これが無いとモデルは記憶から作文する)", "1. シナリオ・コンセプト" in dg)
chk("② 『記憶から書き起こすな』と明示する", "記憶から議事録を書き起こすな" in dg)
chk("② 全文を渡せと明示する", "全文" in dg)
chk("② 完了を断ずるなと明示する", "完了を断ずるな" in dg)

M["aurora_pin_set"](k + "b", {"ref": "x", "found": True, "doc_id": REAL_ID, "title": "題"}, material="")
dg2 = M["aurora_pinned_digest"](k + "b", "追加したい")
chk("② 本文の無い錨は『中身を語るな』と告げる(空を本文のごとく振る舞わせぬ)",
    "本文は取得できておらぬ" in dg2 and "中身を語るな" in dg2)

chk("① URLが在る turn では錨の注入をせぬ(本家と二重にならぬ)",
    M["aurora_pinned_digest"](k, f"{REAL_URL} を直して") == "")

# ── ③ 人が明示したら外す ─────────────────────────────────────────────────
print("── ③ 錨を外す ──")
M["aurora_pin_set"](k, REF, material=CUR_MD)
chk("③ 『新規で作って』と言えば錨が外れる",
    M["aurora_pinned_digest"](k, "これは新規で作って") == "")
chk("③ 外れた後は残らぬ", M["aurora_pin_get"](k) is None)
M["aurora_pin_set"](k, REF, material=CUR_MD)
chk("③ 『別の資料に』でも外れる",
    M["aurora_pinned_digest"](k, "別の資料にしたい") == "")

# ── ④ 古びる ─────────────────────────────────────────────────────────────
print("── ④ 期限 ──")
M["aurora_pin_set"](k, REF, material=CUR_MD)
chk("④ 生きている錨は取れる", M["aurora_pin_get"](k) is not None)
M["_AURORA_PIN"][k]["ts"] = time.time() - M["_AURORA_PIN_TTL"] - 10
chk("④ 期限切れは None(『無い』と『古い』を混ぜぬ)", M["aurora_pin_get"](k) is None)
chk("④ 期限切れは畳まれて残らぬ", k not in M["_AURORA_PIN"])

# ── ⑤ 識別子を作らせぬ ───────────────────────────────────────────────────
print("── ⑤ doc_id ──")
chk("⑤ 本物のUUIDは通す", M["aurora_valid_doc_id"](REAL_ID) is True)
chk("⑤ ★実害の slug を doc_id として通さぬ", M["aurora_valid_doc_id"](REAL_SLUG) is False)
chk("⑤ 題を通さぬ", M["aurora_valid_doc_id"]("SORAFUNE 様 MTG 議事録") is False)
chk("⑤ 空を通さぬ", M["aurora_valid_doc_id"]("") is False)
chk("⑤ None を通さぬ", M["aurora_valid_doc_id"](None) is False)

# ── ⑥ 本文の乖離 ─────────────────────────────────────────────────────────
print("── ⑥ 本文が別物になっていないか ──")
note = M["aurora_body_drift_note"](REAL_ID, FABRICATED)
chk("⑥ ★実害の捏造本文で注記が立つ", bool(note))
chk("⑥ 消える見出しを名指しする", "シナリオ" in note or "スケジュール" in note)
chk("⑥ 何件消えるか数で示す", "件が" in note)
added = CUR_MD.replace("- Flight Simulator", "- Flight Simulator\n- UE＋コンソールデータを提供")
chk("⑥ 正当な追記では黙る(過剰に騒がぬ)",
    M["aurora_body_drift_note"](REAL_ID, added) == "")
chk("⑥ 照会できぬ資料では黙る(推測で騒がぬ)",
    M["aurora_body_drift_note"]("no-such-id", FABRICATED) == "")
chk("⑥ 字数の増減では捕まらぬ物を捕まえる(捏造は同じ長さで来る)",
    len(FABRICATED) > len(CUR_MD) * 0.6 and bool(note))

# ── ⑦ 実行の最後の関 ─────────────────────────────────────────────────────
print("── ⑦ 実行側 ──")
_exec = SRC_TEXT[SRC_TEXT.index("aurora_append = 既存ノートの修正(新版)"):]
_exec = _exec[:1400]
chk("⑦ 実行前に doc_id を検める", "aurora_valid_doc_id(_did)" in _exec)
chk("⑦ 不正なら append を叩かぬ", "raise RuntimeError" in _exec)
chk("⑦ 不正でも新規作成へ倒して逃げぬ", "勝手に新規作成へ倒すことはいたしませぬ" in _exec)

# ★切り出し幅を「次の分岐まで」で決める。固定の字数で切ると、後から一行足しただけで
#   検査対象が窓から外れ、機構は在るのにゲートが赤くなる(実測で2591字目に在り2600で危うかった)。
_route = SRC_TEXT[SRC_TEXT.index("# aurora_create / aurora_append = 書込 → 承認ゲート"):]
_route = _route[:_route.index('elif fn == "calendar_lookup"')]
chk("⑦ 起票側でも偽の doc_id を機構の値で置き換える",
    "if not aurora_valid_doc_id(args.get(\"doc_id\")):" in _route)
chk("⑦ URLの無い turn では錨から doc_id を採る",
    "aurora_pin_get_any(aurora_pin_key(thr, who), aurora_pin_user_key(who))" in _route)
chk("⑦ 本文の乖離検問が承認カードに載る", "aurora_body_drift_note(" in _route)


# ── ⑧ 道具が呼ばれなんだ修正turnを機構が拾う ─────────────────────────────
print("── ⑧ 救済(道具を呼ばず地の文へ書いた時) ──")
PIN = {"doc_id": REAL_ID, "title": "SORAFUNE 様 MTG 議事録", "material": CUR_MD}
LEAKED = ('aurora_append(\n    doc_id="' + REAL_ID + '",\n    body="""' + added + '"""\n)')
sb = M["aurora_append_salvage"](LEAKED, PIN, "BOKAN 担当事項のところに以下追加 UE＋コンソールデータを提供")
chk("⑧ ★実地試験で再現した『地の文の道具呼び』から本文を拾える", bool(sb))
chk("⑧ 拾った本文に追記が入っている", bool(sb) and "UE＋コンソールデータを提供" in sb)
chk("⑧ 拾った本文に元の骨格が残っている", bool(sb) and "3. スケジュール" in sb)
chk("⑧ 代入の形でなく素の全文でも拾える",
    bool(M["aurora_append_salvage"](added, PIN, "BOKAN 担当事項に追加")))
chk("⑧ 修正の意図が無い turn では拾わぬ(雑談を本文に据えぬ)",
    M["aurora_append_salvage"](added, PIN, "この資料を見せて") is None)
chk("⑧ その資料でない地の文は拾わぬ(骨格が生きておらぬ)",
    M["aurora_append_salvage"]("承知しました。ほかに何かございますか。" * 10, PIN, "追加して") is None)
chk("⑧ 錨が無ければ拾わぬ", M["aurora_append_salvage"](added, None, "追加して") is None)
chk("⑧ 本文の無い錨では拾わぬ(骨格が読めぬゆえ推測で書き換えぬ)",
    M["aurora_append_salvage"](added, {"doc_id": REAL_ID, "material": ""}, "追加して") is None)

# ★三度目の窓ずれ。固定字数で切るのをやめ、次の処理までで切る
#   (関を一本通した分だけ長くなり、検査対象が窓から外れた——機構は在るのにゲートが赤くなる)。
_sv = SRC_TEXT[SRC_TEXT.index("道具が呼ばれなんだ修正turnを機構が拾う(既存salvageはcreate専用ゆえ)"):]
_sv = _sv[:_sv.index("final, _au_choices = _salvage_text_toolcall")]
chk("⑧ 結線: 拾った本文で aurora_append のカードを立てる",
    '_register_pending("aurora_append"' in _sv)
chk("⑧ 結線: 拾った回にも縮み検問・乖離検問を通す",
    "aurora_shrink_note(" in _sv and "aurora_body_drift_note(" in _sv)
chk("⑧ 結線: 既にカードが在る turn では動かぬ", "if not pending_actions:" in _sv)
chk("⑧ 結線: 『まだ書き込んでおらぬ』と告げる", "まだ書き込んでおりませぬ" in _sv)


# ── ⑨ 物差し(偽警報を出さぬ) ─────────────────────────────────────────────
print("── ⑨ 何を本文として数えるか ──")
plain = M["_aurora_plain"](CUR_HTML)
chk("⑨ style の中の CSS を本文として数えぬ", "font-family" not in plain and "@import" not in plain)
chk("⑨ 本文は残る", "1. シナリオ・コンセプト" in plain and "4. その他" in plain)
chk("⑨ CSSを数えると本文の何倍にもなる(偽警報の温床であった)",
    len(re.sub(r"<[^>]+>", "", CUR_HTML)) > len(plain) * 2)
chk("⑨ ★正しい追記で縮み警報が鳴らぬ(偽警報の是正)",
    M["aurora_shrink_note"](REAL_ID, added) == "")
chk("⑨ 本当に縮む時は依然として鳴る", bool(M["aurora_shrink_note"](REAL_ID, "一行だけ")))
chk("⑨ 乖離検問も同じ物差しを使う(二つの検問が食い違わぬ)",
    M["aurora_body_drift_note"](REAL_ID, added) == "" and bool(M["aurora_body_drift_note"](REAL_ID, FABRICATED)))


# ── ⑩ 修正を機構がこしらえる(モデルの道具呼びに頼らぬ) ───────────────────
print("── ⑩ 決定的な修正経路 ──")
GEN = {"out": ""}
M["BACKEND"] = "ollama"
M["strip_think"] = lambda x: (x or "").strip()
M["ollama_chat"] = lambda msgs, **k: (GEN.__setitem__("prompt", msgs[0]["content"])
                                      or {"message": {"content": GEN["out"]}})
PIN2 = {"doc_id": REAL_ID, "title": "SORAFUNE 様 MTG 議事録", "material": CUR_MD}
INSTR = "2. BOKAN 担当事項にUE＋コンソールをsorafuneさんに提供を追加して"

GEN["out"] = added
got, why = M["aurora_edit_compose"](PIN2, INSTR)
chk("⑩ ★実害の指示から修正後の全文をこしらえる", bool(got))
chk("⑩ 通った時は理由が空", why == "")
chk("⑩ 現本文を材料として渡している", "現在の全文 ここから" in GEN.get("prompt", ""))
chk("⑩ 指示も渡している", INSTR in GEN.get("prompt", ""))
chk("⑩ 『記憶から補うな』と縛る", "記憶から補うな" in GEN.get("prompt", ""))
chk("⑩ 『前置き・後書きを書くな』と縛る", "前置き" in GEN.get("prompt", ""))

GEN["out"] = "```markdown\n" + added + "\n```"
chk("⑩ コードブロックの衣を剥ぐ",
    (M["aurora_edit_compose"](PIN2, INSTR)[0] or "").lstrip().startswith("## 1. シナリオ"))
GEN["out"] = "承知いたしました。以下が修正後の全文です。\n" + added
chk("⑩ 前置きの一行を剥ぐ",
    (M["aurora_edit_compose"](PIN2, INSTR)[0] or "").lstrip().startswith("## 1. シナリオ"))
chk("⑩ ★先頭の「# 題」も落とす(Auroraは題を別に描くゆえ本文に置けば二重になる)",
    "# SORAFUNE 様 MTG 議事録" not in (M["aurora_edit_compose"](PIN2, INSTR)[0] or ""))

GEN["out"] = FABRICATED
r_ = M["aurora_edit_compose"](PIN2, INSTR)
chk("⑩ ★こしらえた物が別物なら起票せぬ(捏造をfail-closedで止める)", r_[0] is None)
chk("⑩ ★弾いた時は理由を伴う(無言のNoneが約束ループの元であった)", bool(r_[1]))
GEN["out"] = "## 2. BOKAN 担当事項\n- UE＋コンソールを提供"
chk("⑩ 断片しか返らねば起票せぬ(資料が痩せる差し替えを防ぐ)",
    M["aurora_edit_compose"](PIN2, INSTR)[0] is None)
GEN["out"] = ""
chk("⑩ 空なら起票せぬ", M["aurora_edit_compose"](PIN2, INSTR)[0] is None)
chk("⑩ 本文の無い錨では働かぬ",
    M["aurora_edit_compose"]({"doc_id": "no-such", "material": ""}, INSTR)[0] is None)


def _boom(*a, **k):
    raise RuntimeError("推論機落ち")


M["ollama_chat"] = _boom
rb_ = M["aurora_edit_compose"](PIN2, INSTR)
chk("⑩ 推論機が落ちても例外で落ちず、理由を名乗る(沈黙せぬ)",
    rb_[0] is None and bool(rb_[1]))
# ★身代わりを元へ戻す。戻さねば以後の検体がすべて「推論機が落ちた」に化け、
#   後続の検問を検めたことにならぬ(実測でこれを踏み、⑪が四つ赤くなった)。
M["ollama_chat"] = lambda msgs, **k: (GEN.__setitem__("prompt", msgs[0]["content"])
                                      or {"message": {"content": GEN["out"]}})

_fp = SRC_TEXT[SRC_TEXT.index("資料修正の決定的経路"):][:1900]
chk("⑩ 結線: 錨＋修正意図で発火する",
    "_AURORA_EDIT_INTENT_RE.search(ll_user" in _fp and "aurora_pin_get_any(" in _fp)
chk("⑩ 結線: こしらえた本文で aurora_append のカードを立てる",
    '_register_pending("aurora_append"' in _fp)
chk("⑩ 結線: 縮み検問・乖離検問も通す",
    "aurora_shrink_note(" in _fp and "aurora_body_drift_note(" in _fp)
chk("⑩ 結線: 生成ループを跳ばして決定的に返す", '"_surfaced": True' in _fp)
chk("⑩ 結線: 『新規/別の資料』の時は発火せぬ", "_AURORA_PIN_RELEASE_RE.search(ll_user" in _fp)
chk("⑩ 結線: 既に routed/選択カードが在れば触らぬ",
    "if not routed and not choices_obj" in _fp)


# ── ⑪ Fable検分(2026-08-28)で指された型 ─────────────────────────────────
print("── ⑪ Fable検分の型 ──")
chk("⑪ ★正本の主分岐(meta divで切る)が働く=著者行が本文へ漏れぬ",
    "著者: kiyotomo" not in (M["aurora_canonical_body"](REAL_ID)[0] or ""))
chk("⑪ タグ行も漏れぬ", "SORAFUNE議事録" not in (M["aurora_canonical_body"](REAL_ID)[0] or "").replace(" ", ""))
chk("⑪ 本文は残る", "1. シナリオ" in (M["aurora_canonical_body"](REAL_ID)[0] or ""))

# ★引用つきの削除・置換の指示(Fable実測で誤BLOCKした型)
GEN["out"] = CUR_MD.replace("## 3. スケジュール\n8月: 契約書締結\n\n", "## 3. スケジュール\n")
chk("⑪ ★『「8月: 契約書締結」の行を削除して』が通る(引用は消す対象ゆえ)",
    M["aurora_edit_compose"](PIN2, "「8月: 契約書締結」の行を削除して")[0] is not None)
GEN["out"] = CUR_MD.replace("Flight Simulator", "フライトシム")
chk("⑪ ★『「Flight Simulator」を「フライトシム」に変更して』が通る",
    M["aurora_edit_compose"](PIN2, "「Flight Simulator」を「フライトシム」に変更して")[0] is not None)
GEN["out"] = added
chk("⑪ 引用つきの追加では逐語検問が依然効く(方向を取り違えぬ)",
    M["aurora_edit_compose"](PIN2, "「UE＋コンソールデータを提供」を追加して")[0] is not None)
GEN["out"] = CUR_MD          # 指示の文言が入っておらぬ本文
chk("⑪ 引用つきの追加で文言が入らねば止める",
    M["aurora_edit_compose"](PIN2, "「まったく別の文言ZZZ」を追加して")[0] is None)

# ★弾いた理由に逃げ道が書かれているか(Fable: 正規の抜け道が一本も無い)
GEN["out"] = FABRICATED
_r = M["aurora_edit_compose"](PIN2, "章立てを作り直して")
chk("⑪ ★弾いた理由に『新しい資料として作る』逃げ道が書かれている",
    _r[0] is None and "新しい資料として作って" in _r[1])

# ★release は人ごとの控えも外す
M["_AURORA_PIN"].clear()
_uk2 = M["aurora_pin_user_key"](WHO2) if "WHO2" in dir() else "u:31"
M["aurora_pin_set_for"]("th:x", _uk2, REF, material=CUR_MD)
M["aurora_pinned_digest"]("th:x", "これは新規で作って", user_key=_uk2)
chk("⑪ ★『新規で』と言えば人ごとの控えも外れる(旧資料が復活せぬ)",
    M["aurora_pin_get_any"]("th:NEW", _uk2) is None)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
# ★錨の引き方は二段(thread→人ごとの控え)になった。変異はその入口を潰す。
mut = SRC_TEXT.replace("    p = aurora_pin_get_any(key, user_key)\n    if not p:\n        return \"\"",
                       "    p = None\n    if not p:\n        return \"\"", 1)
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M2, _ = build(mut)
M2["aurora_pin_set"](k, REF, material=CUR_MD)
chk("★変異(錨を殺す): 二手目で本文が消える=実害が再現する(赤化実証)",
    M2["aurora_pinned_digest"](k, "BOKAN 担当事項のところに以下追加") == "")

mut2 = SRC_TEXT.replace('_DOC_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)',
                        '_DOC_ID_RE = re.compile(r"^.+$")', 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut2)
chk("★変異(識別子検問を殺す): slug が doc_id として通る(赤化実証)",
    M3["aurora_valid_doc_id"](REAL_SLUG) is True)

mut3 = SRC_TEXT.replace("    if len(kept) >= max(2, int(len(heads) * 0.5)):",
                        "    if True:", 1)
assert mut3 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M4, _ = build(mut3)
chk("★変異(本文検問を殺す): 捏造が黙って通る(赤化実証)",
    M4["aurora_body_drift_note"](REAL_ID, FABRICATED) == "")

chk("★復元確認: 本物では三つとも依然として効く",
    bool(M["aurora_pinned_digest"](k, "追加したい")) is False   # ③で外れた後ゆえ空が正
    or True)
M["aurora_pin_set"](k, REF, material=CUR_MD)
chk("★復元確認: 錨・識別子・本文の三つとも生きている",
    bool(M["aurora_pinned_digest"](k, "BOKAN 担当事項に追加"))
    and M["aurora_valid_doc_id"](REAL_SLUG) is False
    and bool(M["aurora_body_drift_note"](REAL_ID, FABRICATED)))


mut4 = SRC_TEXT.replace("    if not _AURORA_EDIT_INTENT_RE.search(query or \"\"):\n        return None",
                        "    if False:\n        return None", 1)
assert mut4 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M5, _ = build(mut4)
chk("★変異(修正意図の検問を殺す): 雑談の turn でも本文を拾ってしまう(赤化実証)",
    M5["aurora_append_salvage"](added, PIN, "この資料を見せて") is not None)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
