#!/usr/bin/env python3
r"""内容を運ぶ Aurora 書込の単一の関の回帰ゲート(Fable診断 2026-08-27 の本丸)。全PASSで exit 0。

Fable の指摘:
「outbox に注ぐ口が8つ以上ある。8/27 の捏造カード三枚はすべてモデル自身の tool_calls が
  捏造 body を args に載せ、そのまま _register_pending に届いたもの。
  compose の検問はこの経路を**素通り**する。」
「**モデル起源の args を outbox に直接登録することを禁じよ。**
  モデルの tool_call は『意図の合図』に降格させ、body は常に同じパイプラインで機構が組み直す。」
「今は**新しい道を作りながら古い道を塞いでいない**。捏造は今も古い道から入れる。」

守る掟:
 ① aurora_append では**モデルの body を捨てる**。正本が在るのだから模型に書かせる理由が無い。
    機構が正本＋指示から組み直した本文だけを台帳へ入れる。
 ② 資料が特定できねば起票せぬ(どこへ書くか判らぬまま内容だけ受け取らぬ)。
 ③ aurora_create には正本が無いゆえ『捨てて組み直す』ができぬ。代わりに
    **この turn の材料に接地しているか**を検める(retrieve-then-render の書込側)。
 ④ 材料は『人が実際に示した物』だけ——人の発話・貼付・機構が取得した資料。
    ★モデルの記憶は材料ではない。
 ⑤ 弾いた時は必ず理由を伴う(無言の拒否は約束ループを再演させる)。
 ⑥ 執行路は一本。入口(tool_calls / salvage)はいずれもこの関を通る。
 ★突然変異: 関を素通りさせると、実測の捏造 body がそのまま台帳へ届くことを実証する。
"""
import ast
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


TMP = tempfile.mkdtemp(prefix="gate_wg_")
REAL_ID = "d78e9ca6-3ce5-4bc0-9ab9-2502ede67767"
CUR_MD = ("## 1. シナリオ・コンセプト\n場所: 体育館等\n\n"
          "## 2. BOKAN 担当事項\n- Flight Simulator\n\n"
          "## 3. スケジュール\n8月: 契約書締結\n\n"
          "## 4. その他・アクションアイテム\n- 再生機能\n")
GOOD = CUR_MD.replace("- Flight Simulator", "- Flight Simulator\n- UE＋コンソールを提供")
# ★実害そのもの(8/27 14:21/14:25): モデルが記憶から書き起こした「修正後の全文」
FABRICATED = ("# SORAFUNE 様 定例MTG 議事録\n\n**参加者:** 武井(ryoji), 木戸(kiyotomo), tim, rui\n\n"
              "## 1. 進捗報告 (BOKAN側)\n### tim担当: フェーズ1 (レイアウト/アニメーション)\n"
              "- 現状: 完了\n\n## 2. 決定事項\n- フェーズ1完了報告を承認\n")

WANT_F = ["aurora_write_guard", "aurora_turn_sources", "aurora_edit_compose",
          "aurora_canonical_body", "_strip_material_wrapper",
          # 検問は材料を控える(接地の注記の土台・gate_aurora_grounding.py が本体を検める)
          "aurora_material_remember", "_aurora_body_key",
          # 止めぬ代わりに映す機構(同じ検体で鳴ることを此処でも突き合わせる)
          "aurora_grounding_note", "aurora_material_recall", "aurora_ungrounded_facts",
          "aurora_fact_tokens"]
WANT_A = ["_PROPER_TOKEN_RE", "_INSTR_QUOTED_RE", "_INSTR_REMOVE_RE", "_MATERIAL_WRAPPER_RE",
          "_DECOR_META_RE", "_STRUCT_HEAD_RE", "_AURORA_MATERIAL", "_AURORA_MATERIAL_LOCK",
          "_FACT_DATE_RE", "_FACT_QTY_RE", "_FACT_STATE_RE"]


class _Au:
    @staticmethod
    def get(doc_id):
        return None                    # 正本の取り直しは失敗させ、錨の material で凌がせる


def build(src_text, gen_out):
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
    M["HERE"] = TMP
    M["BACKEND"] = "ollama"
    M["strip_think"] = lambda x: (x or "").strip()
    M["ollama_chat"] = lambda msgs, **k: {"message": {"content": gen_out[0]}}
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


sys.modules["casper_aurora"] = _Au
GEN = [GOOD]
M, missing = build(SRC_TEXT, GEN)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

PIN = {"doc_id": REAL_ID, "title": "SORAFUNE 様 MTG 議事録", "material": CUR_MD}
INSTR = "2. BOKAN 担当事項にUE＋コンソールを追加して"

# ── ① append: モデルの body を捨てる ─────────────────────────────────────
print("── ① append はモデルの body を捨てる ──")
GEN[0] = GOOD
tool, args, why = M["aurora_write_guard"]("aurora_append",
                                          {"doc_id": REAL_ID, "body": FABRICATED},
                                          PIN, INSTR, sources=INSTR)
chk("① 起票へ進む", why == "")
chk("① ★モデルの捏造 body が台帳へ渡らぬ(実害の本体)",
    "rui" not in args["body"] and "フェーズ1" not in args["body"])
chk("① 機構が組み直した本文になっている", "UE＋コンソールを提供" in args["body"])
chk("① 元の骨格が残る", all(h in args["body"] for h in ["1. シナリオ", "2. BOKAN", "3. スケジュール"]))
chk("① doc_id は機構の値で置く(モデルの申告を使わぬ)", args["doc_id"] == REAL_ID)

GEN[0] = FABRICATED
tool, args, why = M["aurora_write_guard"]("aurora_append",
                                          {"doc_id": REAL_ID, "body": GOOD},
                                          PIN, INSTR, sources=INSTR)
chk("① 組み直した本文が別物なら起票せぬ(compose の検問も生きている)", bool(why))
chk("⑤ 弾いた時は理由を伴う", len(why) > 8)

# ── ② 資料が特定できねば起票せぬ ─────────────────────────────────────────
print("── ② 行き先が判らぬ時 ──")
GEN[0] = GOOD
_t, _a, why2 = M["aurora_write_guard"]("aurora_append", {"body": GOOD}, None, INSTR)
chk("② 錨が無ければ起票せぬ", bool(why2))
chk("② その理由が『どの資料か判らぬ』と判る", "特定" in why2 or "URL" in why2)

# ── ③④ create: この turn の材料に接地しているか ─────────────────────────
print("── ③④ create の接地 ──")
SRC_OK = "SORAFUNE のMTGでした。シナリオは火災時想定、場所は体育館等です。"
_t, _a, why3 = M["aurora_write_guard"](
    "aurora_create", {"title": "SORAFUNE MTG", "body": "シナリオ: 火災時想定\n場所: 体育館等"},
    None, "これをAuroraに保存して", sources=SRC_OK)
chk("③ 材料に沿う本文は通る", why3 == "")

_t, _a, why4 = M["aurora_write_guard"](
    "aurora_create", {"title": "SORAFUNE MTG", "body": FABRICATED},
    None, "これをAuroraに保存して", sources=SRC_OK)
chk("③ ★材料に無い語を並べた本文は起票せぬ(15:27の捏造カードの型)", bool(why4))
chk("③ どの語が新出かを名指しする", "rui" in why4 or "フェーズ" in why4 or "武井" in why4)

_t, _a, why5 = M["aurora_write_guard"]("aurora_create", {"title": "x", "body": "短"},
                                       None, "保存して", sources=SRC_OK)
chk("③ 空に近い本文は起票せぬ", bool(why5))

chk("④ 材料は人の発話から集める",
    "火災時想定" in M["aurora_turn_sources"](
        [{"role": "user", "content": "シナリオは火災時想定"},
         {"role": "assistant", "content": "承知しました"}]))
chk("④ ★モデルの発話は材料に数えぬ",
    "承知しました" not in M["aurora_turn_sources"](
        [{"role": "user", "content": "シナリオは火災時想定"},
         {"role": "assistant", "content": "承知しました"}]))
chk("④ 錨の本文も材料に数える(機構が取得した一次資料ゆえ)",
    "BOKAN" in M["aurora_turn_sources"]([], pin=PIN))
# ★検体は20字以上にする(短すぎる本文は別の理由『空に近い』で弾かれ、検問を検めたことにならぬ)。
chk("④ 複合語の差を新出と数えぬ(コンソール→コンソールデータ)",
    M["aurora_write_guard"](
        "aurora_create",
        {"title": "x", "body": "本日の決定として、UE＋コンソールデータを提供することといたしました。"},
        None, "保存して", sources="本日の決定 UE＋コンソールを提供 いたしました")[2] == "")

# ── ⑥ 執行路は一本 ───────────────────────────────────────────────────────
print("── ⑥ 結線(入口はどこも同じ関を通る) ──")
# ★「本丸」の語は salvage 側の注記にも書いた。index() は先に現れる方を掴むゆえ、
#   tool_calls 経路だけに在る語で切る(検体の取り違えで機構を見誤らぬ)。
_tc = SRC_TEXT[SRC_TEXT.index("モデル起源の body を台帳へ直に入れさせぬ"):][:2200]
chk("⑥ モデルの tool_calls 経路が関を通る", "aurora_write_guard(" in _tc)
chk("⑥ 弾かれた時は台帳へ入れぬ", "_wreason" in _tc and "else:" in _tc)
# ★注記の中の「continue」という語まで拾って赤くなった(実測)。**実行文だけを見る**——
#   注釈行(#で始まる行)を落としてから検める。ゲートが文字を数えて機構を見誤らぬように。
_head = _tc.split("summary = _action_summary")[0]
_code = "\n".join(l for l in _head.split("\n") if not l.strip().startswith("#"))
chk("⑥ ★弾いても tool 結果は必ずモデルへ返す(continue で輪を飛ばさぬ)",
    "continue" not in _code)
chk("⑥ 古い道(salvage)も同じ関を通る",
    SRC_TEXT.count("aurora_write_guard(") >= 3)      # def + 2箇所
_sv = SRC_TEXT[SRC_TEXT.index('_t2, args, _wr = aurora_write_guard('):][:600]
chk("⑥ salvage も弾かれたら起票せぬ", "if _wr:" in _sv and "return final, None" in _sv)
chk("⑥ 起票できなんだ時(pid None)も pending へ積まぬ", "if pid is None:" in _sv)


# ── ⑦ Fable検分(2026-08-28)で指された残る口 ─────────────────────────────
print("── ⑦ Fable検分で指された残る口 ──")
# ★純和文の捏造(固有名ゼロ)は**意図してここを通す**(2026-08-29)。
#   量で見る手も内容語の不在で見る手も実測で否まれ、堰き止めれば正当な資料を止める
#   ——正しい修正を止める検問は無いより悪い。ゆえ**止めずに映す**へ切り替えた。
#   ★但し「通る」だけを緑にすれば、映す手当が壊れた日に此処は緑のまま嘘をつく。
#     ゆえ同じ検体で**注記が鳴ること**まで此処で突き合わせる(本体は gate_aurora_grounding.py)。
_pure = ("来月の納品を前倒しすることが決まりました。担当は制作部が引き継ぎます。"
         "併せて検収の日程も見直すこととし、関係者へ周知いたします。")
_t, _a, _w = M["aurora_write_guard"]("aurora_create", {"title": "決定事項", "body": _pure},
                                     None, "保存して", sources="今日の打合せの記録を残して")
chk("⑦ 固有名を含まぬ純和文の捏造は**止めぬ**(過剰阻止を避ける・意図された fail-open)",
    _w == "")
_gn, _gu, _gm = M["aurora_grounding_note"]("aurora_create", {"body": _pure})
chk("⑦ ★止めぬ代わりに**必ず映る**: 材料に無い事実の語が注記に並ぶ(来月/前倒し等)",
    "材料に無い" in _gn and any(t in (_gu or []) for t in ("来月", "前倒し", "決定")))
chk("⑦ ★映す機構が材料を控えておる(控え無しなら『控えがござらぬ』と名乗る)", _gm is not None)

# ★append salvage の口も関を通っているか(Fable: 見出し50%だけで台帳へ入っていた)
_as = SRC_TEXT[SRC_TEXT.index("_sb = aurora_append_salvage(final"):]
_as = _as[:_as.index("final, _au_choices = _salvage_text_toolcall")]
chk("⑦ ★append salvage も関を通る", "aurora_write_guard(" in _as)
chk("⑦ 弾かれたら起票せぬ", "_wr3" in _as and "_aargs = None" in _as)
chk("⑦ 関が組み直した本文で検問・要約を作る(元の地の文でなく)",
    '_aargs.get("body", "")' in _as)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
mut = SRC_TEXT.replace('''    if tool == "aurora_append":
        # ★正本が在るのだから、body を模型に書かせる理由が無い。**モデルの body は捨てる。**''',
                       '''    if tool == "aurora_append":
        return tool, a, ""
        # ★正本が在るのだから、body を模型に書かせる理由が無い。**モデルの body は捨てる。**''', 1)
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M2, _ = build(mut, GEN)
GEN[0] = GOOD
_t, _a2, _w2 = M2["aurora_write_guard"]("aurora_append",
                                        {"doc_id": REAL_ID, "body": FABRICATED},
                                        PIN, INSTR, sources=INSTR)
chk("★変異(関を素通りさせる): 実測の捏造 body がそのまま台帳へ届く(赤化実証)",
    _w2 == "" and "rui" in _a2["body"])
chk("★復元確認: 本物では依然として捨てられる",
    "rui" not in M["aurora_write_guard"]("aurora_append",
                                         {"doc_id": REAL_ID, "body": FABRICATED},
                                         PIN, INSTR, sources=INSTR)[1]["body"])

mut2 = SRC_TEXT.replace("        if new:\n            return tool, a, (\"この turn の材料に無い語",
                        "        if False:\n            return tool, a, (\"この turn の材料に無い語", 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut2, GEN)
chk("★変異(create の接地検問を殺す): 捏造の新規作成が通る(赤化実証)",
    M3["aurora_write_guard"]("aurora_create", {"title": "x", "body": FABRICATED},
                             None, "保存して", sources=SRC_OK)[2] == "")

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
