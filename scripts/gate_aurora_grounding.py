#!/usr/bin/env python3
r"""接地の注記(純和文捏造を『止めず・黙らせず』映す)の回帰ゲート。全PASSで exit 0。

【なぜ止めぬか——数で決めた】(将軍実測 2026-08-29・実在の aurora_create 37件)
  ・「材料に無い**内容語**」で堰き止める案は成らぬ。承認された正本ほど不在語が多く(平均9.8語)、
    却下された方が少ない(3.0語)——信号が逆。蔵書30207片のDFで篩っても向きは変わらなんだ。
  ・不在語を**日付・数量・状態**に絞ると境界が立つ(承認済4/11件・計6語 / 却下4/14件・計10語 /
    期限切7/12件・計34語[納品済・承認済・制作中・676件…])。捏造の正体は語彙でなく
    **状態と数の主張**であった。
  ・だが承認済でも4/11件が鳴る。堰き止めれば正しい資料を止める——★正しい修正を止める検問は
    無いより悪い(この二週で三度踏んだ)。ゆえ aurora_shrink_note と同じ作法:
    **止めはせぬが、黙って通さぬ。**

守る掟:
 ① 事実の語だけを見る(骨組みの語では鳴らぬ)。
 ② 検問(aurora_write_guard・一本しか無い関)が材料を控える。create も append も。
 ③ 注記は台帳の唯一の口(_register_pending)で足す——起票の口は十を超えるゆえ、
    呼出側に足せば必ずどれかが漏れる(cmd_494 と同じ理由)。
 ④ ★控えの無い本文(=検問を通らずに入った本文)は「控えがござらぬ」と名乗る。
    黙って接地済に見せてはならぬ(失敗とゼロを同じ出口へ流さぬ)。
 ⑤ 何に接地したかを**台帳へ刻む**(従前は本文だけ刻み材料を刻まず、後から誰も検められなんだ)。
 ⑥ ★注記が**画面まで届く**。呼出側の控えた古い summary でなく、台帳の口が書いた方を出す。
 ⑦ 止めぬ(fail-open)。捏造の語が在っても起票は妨げられぬ。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import json
import os
import re
import sys
import tempfile
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["aurora_fact_tokens", "_aurora_body_key", "aurora_material_remember",
          "aurora_material_recall", "aurora_ungrounded_facts", "aurora_grounding_note",
          "aurora_write_guard", "_register_pending"]
WANT_A = ["_FACT_DATE_RE", "_FACT_QTY_RE", "_FACT_STATE_RE",
          "_AURORA_MATERIAL", "_AURORA_MATERIAL_LOCK", "_PROPER_TOKEN_RE"]


def build(src_text, outbox=None, store_dir=None):
    """本番の関だけを抜いて据える(周りは最小の代役)。機構が本物であることを崩さぬ。"""
    tree = ast.parse(src_text)
    picked, seenf, seena = [], set(), set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seenf.add(n.name)
        elif isinstance(n, ast.Assign):
            nm = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if any(x in WANT_A for x in nm):
                picked.append(n); seena.update(nm)
    missing = [w for w in WANT_F + WANT_A if w not in (seenf | seena)]
    if missing:
        return None, missing
    M = {}
    exec("import datetime, json, os, re, threading, uuid", M)
    picked.sort(key=lambda n: n.lineno)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    M["PENDING_ACTIONS"] = {}
    M["casper_outbox"] = outbox
    M["HERE"] = store_dir or tempfile.mkdtemp()
    M["_DM_QUOTED_BODY_RE"] = re.compile(r"「[^」]{4,}」")
    M["_dm_body_complete"] = lambda q, b: True
    M["aurora_edit_compose"] = lambda pin, instr: (str(pin.get("draft") or ""), "")
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

# ★検体。Fable が「素通りする」と実証した当の一文(材料に一語も無い純和文の捏造)。
FAB = ("## 進捗\n\n来月の納品を前倒しすることが決まりました。担当は制作部が引き継ぎます。\n"
       "現在3件が確認済みで、残り2件は検討中にござる。\n")
MAT_REAL = ("kiyotomo: SORAFUNE の件、リサーチの様子を資料にまとめておいてくれる？\n"
            "casper: 承知しました\n")
# 材料に接地した正本(骨組みの語は在るが、材料に無い事実の語は無い)
GOOD = ("## SORAFUNE リサーチ\n\n- 概要: 現場の確認事項をまとめる\n"
        "- 参加者: kiyotomo\n- 本文は打ち合わせの通り\n")

# ── ① 事実の語だけを見る ───────────────────────────────────────────────
print("── ① 何を見るか ──")
ft = M["aurora_fact_tokens"](FAB)
chk("① 状態の主張を拾う(確認済み/検討中)", "確認済み" in ft and "検討中" in ft)
chk("① 日付の主張を拾う(来月)", "来月" in ft)
chk("① 数量の主張を拾う(3件)", "3件" in ft)
chk("① ★骨組みの語では鳴らぬ(概要/参加者/本文)",
    not (set(M["aurora_fact_tokens"](GOOD)) & {"概要", "参加者", "本文"}))
chk("① 空の本文で転ばぬ", M["aurora_fact_tokens"]("") == [])

# ── ② 検問が材料を控える ───────────────────────────────────────────────
print("── ② 検問が材料を控える ──")
_t, _a, _r = M["aurora_write_guard"]("aurora_create", {"title": "リサーチ", "body": FAB},
                                     None, "資料にして", sources=MAT_REAL, who={"uid": "31"})
chk("② create: 検問を通した本文の材料が控えられる", M["aurora_material_recall"](FAB) is not None)
chk("② ★止めておらぬ(fail-open。捏造の語が在っても起票は妨げられぬ)", _r == "")
_pin = {"doc_id": "d1", "material": "## 既存\n- 8月末に納品\n", "draft": "## 既存\n- 8月末に納品\n- 追記\n"}
_t2, _a2, _r2 = M["aurora_write_guard"]("aurora_append", {"body": "捨てられる"}, _pin,
                                        "追記して", sources="", who={"uid": "31"})
chk("② append: 機構が組み直した本文の材料も控えられる",
    M["aurora_material_recall"](_a2["body"]) is not None)
chk("② append の材料には正本が含まれる", "8月末に納品" in M["aurora_material_recall"](_a2["body"]))
chk("② 控えは上限つき(記憶を無限に肥やさぬ)", "> 200" in SRC_TEXT.split("def aurora_material_remember")[1][:900])

# ── ③④⑤ 台帳の唯一の口で注記を足し、証跡を刻む ─────────────────────────
print("── ③④⑤ 台帳の口 ──")
_tmp = tempfile.mkdtemp()
sys.path.insert(0, HERE)
import casper_outbox as _ob                                # noqa: E402
_ob.STORE = os.path.join(_tmp, "outbox.jsonl")
M2, _ = build(SRC_TEXT, outbox=_ob, store_dir=_tmp)

# (a) 合成の赤が宛先(承認カードの要約)まで届くか
M2["aurora_write_guard"]("aurora_create", {"title": "進捗", "body": FAB}, None,
                         "まとめて", sources=MAT_REAL, who={"uid": "31"})
# ★本番と同じ形の要約(_action_summary は『題＋── 本文 ──＋本文』を返す)
CALLER_SUMMARY = "Aurora ノート作成 → タイトル: 進捗\n── 本文 ──\n" + FAB
pid = M2["_register_pending"]("aurora_create", {"title": "進捗", "body": FAB}, "31", CALLER_SUMMARY)
card = M2["PENDING_ACTIONS"][pid]["summary"]
chk("③(a) 合成の赤: 材料に無い事実の語が承認カードの要約に現れる",
    "材料に無い『事実の語』" in card and "来月" in card and "確認済み" in card)
chk("③(a) 止めておらぬ(起票は成る)", bool(pid))
chk("③(a) 押す前の人へ宛てた文言である(承認くだされ)", "承認くだされ" in card)

# (b) 古い赤・正当な本文で誤発火せぬか
M2["aurora_write_guard"]("aurora_create", {"title": "リサーチ", "body": GOOD}, None,
                         "資料にして", sources=MAT_REAL, who={"uid": "31"})
pid_g = M2["_register_pending"]("aurora_create", {"title": "リサーチ", "body": GOOD}, "31", "要約")
chk("③(b) ★誤発火せぬ: 材料に接地した正本では注記が付かぬ",
    M2["PENDING_ACTIONS"][pid_g]["summary"] == "要約")
pid_d = M2["_register_pending"]("send_message", {"body": "来月納品と決まりました"}, "31", "DM要約")
chk("③(b) Aurora 以外の下書きには足さぬ(場違いに鳴らさぬ)",
    M2["PENDING_ACTIONS"][pid_d]["summary"] == "DM要約")

# (c) 本物は依然届く: 検問を通らずに入った本文
pid_u = M2["_register_pending"]("aurora_create",
                                {"title": "謎", "body": "## 謎\n来週までに完了と決まりました。\n"},
                                "31", "要約2")
chk("④(c) ★検問を通らぬ本文は『控えがござらぬ』と名乗る(黙って接地済に見せぬ)",
    "控えがござらぬ" in M2["PENDING_ACTIONS"][pid_u]["summary"])

recs = [json.loads(l) for l in open(_ob.STORE, encoding="utf-8")]
r_fab = next(r for r in recs if r["id"] == pid)
r_good = next(r for r in recs if r["id"] == pid_g)
r_unk = next(r for r in recs if r["id"] == pid_u)
r_dm = next(r for r in recs if r["id"] == pid_d)
chk("⑤ 台帳に接地の証跡が刻まれる(材料そのもの)",
    (r_fab.get("grounding") or {}).get("material_len", 0) > 0
    and "SORAFUNE" in (r_fab["grounding"]["material"] or ""))
chk("⑤ 材料に無い事実の語も刻まれる(後から数えられる)",
    "来月" in (r_fab["grounding"].get("ungrounded") or []))
chk("⑤ 接地しておる本文は不在語ゼロで刻まれる", r_good["grounding"]["ungrounded"] == [])
chk("⑤ ★ゼロと『検問を通らず』を別の欄で区別する(同じ出口へ流さぬ)",
    r_good["grounding"]["guarded"] is True and r_unk["grounding"]["guarded"] is False
    and r_unk["grounding"]["ungrounded"] is None)
chk("⑤ Aurora 以外には grounding を付けぬ", r_dm.get("grounding") is None)
chk("⑤ 台帳の署名が grounding を受ける", "grounding=None)" in open(
    os.path.join(HERE, "casper_outbox.py"), encoding="utf-8").read())

# ── ⑥ 注記が画面まで届く ───────────────────────────────────────────────
print("── ⑥ 画面まで届くか ──")
chk("⑥ ★画面へ出す一点で台帳の口が書いた要約を引き直す",
    "_pcache = PENDING_ACTIONS.get(pa.get(\"id\")) or {}" in SRC_TEXT
    and 'pa = dict(pa, summary=_pcache["summary"], notes=_pcache.get("notes") or "")' in SRC_TEXT)
_ui = SRC_TEXT[SRC_TEXT.index("for pa in pending_actions:              # Stage2"):][:900]
chk("⑥ 引き直しが書き出しより先に在る",
    _ui.index("_pcache") < _ui.index('json.dumps({"confirm": pa}'))
chk("⑥ ★呼出側の控えは古い(引き直さねば注記は人へ届かぬ)——その差を実証",
    "材料に無い" not in CALLER_SUMMARY
    and "材料に無い" in M2["PENDING_ACTIONS"][pid]["summary"])
chk("⑥ 注記は台帳の口ただ一つで足される(呼出側に散らさぬ)",
    SRC_TEXT.count("aurora_grounding_note(") == 2)   # 定義1 + 台帳の口1

# ★最後の一跳ね: 画面は編集可のカードで要約を '──' で切り詰める。末尾へ足した注記は
#   そこで**永久に消える**(既存の aurora_shrink_note も同じ穴に落ちておった)。
HTML = open(os.path.join(HERE, "chat.html"), encoding="utf-8").read()
_sum_fab = M2["PENDING_ACTIONS"][pid]["summary"]
chk("⑥ ★画面の切り詰め('──'の手前)には注記が残らぬ——独立の欄が要る所以(実証)",
    "材料に無い" in _sum_fab and "材料に無い" not in _sum_fab.split("──")[0])
chk("⑥ 台帳の口が注記を独立の欄へも置く", M2["PENDING_ACTIONS"][pid].get("notes", "").find("材料に無い") >= 0)
chk("⑥ 接地しておる本文の欄は空(空でない事にせぬ)", M2["PENDING_ACTIONS"][pid_g].get("notes") == "")
chk("⑥ 画面へ出す一点で注記の欄も渡す", 'notes=_pcache.get("notes") or ""' in SRC_TEXT)
chk("⑥ 画面が注記の欄を受けて帯に描く",
    "const notes=(pa.notes||'').trim();" in HTML and "nt.textContent=notes;" in HTML)
chk("⑥ ★帯がカードに実際に嵌まる(作って捨てておらぬ)", "if(nt) c.appendChild(nt);" in HTML)
chk("⑥ 帯は要約より先に置かれる(押す前に目へ入る)",
    HTML.index("if(nt) c.appendChild(nt);") < HTML.index("c.appendChild(s);"))
chk("⑥ 注記を二重に見せぬ(切り詰めぬ側では要約から除く)",
    ".replace(notes,'').trim()" in HTML)

# ── ★突然変異 ──────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
_m1 = '''    if _gnote:
        summary = str(summary or "") + _gnote'''
assert SRC_TEXT.count(_m1) == 1, "変異が当たっていない(ゲートの自己点検)"
Mx, _ = build(SRC_TEXT.replace(_m1, '''    if False:
        summary = str(summary or "") + _gnote'''), outbox=None, store_dir=_tmp)
Mx["aurora_write_guard"]("aurora_create", {"title": "進捗", "body": FAB}, None, "まとめて",
                         sources=MAT_REAL, who={"uid": "31"})
_px = Mx["_register_pending"]("aurora_create", {"title": "進捗", "body": FAB}, "31", "要約")
chk("★変異(注記を足さぬ): 捏造の語が承認カードに出ぬ(赤化実証)",
    "材料に無い" not in Mx["PENDING_ACTIONS"][_px]["summary"])

_m2s = '''    if mat is None:
        return ("\\n⚠ この本文が何に接地しておるか、機構に控えがござらぬ"'''
assert SRC_TEXT.count(_m2s) == 1, "変異が当たっていない(ゲートの自己点検)"
My, _ = build(SRC_TEXT.replace(_m2s, '''    if mat is None:
        mat = ""
    if False:
        return ("\\n⚠ この本文が何に接地しておるか、機構に控えがござらぬ"'''),
              outbox=None, store_dir=_tmp)
_py = My["_register_pending"]("aurora_create", {"title": "謎", "body": GOOD}, "31", "要約")
chk("★変異(控え無しを空材料として扱う): 検問を通らぬ本文が黙って通る(赤化実証)",
    My["PENDING_ACTIONS"][_py]["summary"] == "要約")

_m3 = '''    for rx in (_FACT_DATE_RE, _FACT_QTY_RE, _FACT_STATE_RE):'''
assert SRC_TEXT.count(_m3) == 1, "変異が当たっていない(ゲートの自己点検)"
Mz, _ = build(SRC_TEXT.replace(_m3, '''    for rx in (_FACT_DATE_RE,):'''))
chk("★変異(状態の語を見ぬ): 実測で最も多く捏造を運んだ『済み/中』を取り落とす(赤化実証)",
    "確認済み" not in Mz["aurora_fact_tokens"](FAB))

# ── ⑧ 自家中毒(模型の応答を材料と名乗らせぬ) ───────────────────────────
print("── ⑧ 材料に模型自身の応答を混ぜぬ ──")
_FAKE_REPLY = "承知しました。\n" + FAB          # 模型の応答(捏造を含む)
_M3, _ = build(SRC_TEXT)
_M3["aurora_write_guard"]("aurora_create", {"title": "進捗", "body": FAB}, None, "保存して",
                          sources="保存して\n" + _FAKE_REPLY,      # 止める側は緩いまま(過剰阻止を避ける)
                          material="保存して",                     # 映す側は人が示した物だけ
                          who={"uid": "31"})
_note8, _ung8, _mat8 = _M3["aurora_grounding_note"]("aurora_create", {"body": FAB})
chk("⑧ ★模型の応答を材料に含めぬ(含めれば捏造が己を材料と名乗る)", "来月" not in (_mat8 or ""))
chk("⑧ ★ゆえに捏造の語で注記が鳴る(自家中毒なら永久に鳴かぬ)", "来月" in (_ung8 or []))
chk("⑧ 救済路が『人の示した物だけ』を材料として渡す配線が在る",
    'material=str(query or ""))' in SRC_TEXT)
chk("⑧ 止める側(sources)には応答を含めたまま(過剰阻止を避ける)",
    'sources=(str(query or "") + "\\n" + str(f or "")), who=who,' in SRC_TEXT)
_M4b, _ = build(SRC_TEXT)
_M4b["aurora_write_guard"]("aurora_create", {"title": "進捗", "body": FAB}, None, "保存して",
                           sources="保存して\n" + _FAKE_REPLY, who={"uid": "31"})   # material を渡さぬ旧来の呼び
_n9, _u9, _m9 = _M4b["aurora_grounding_note"]("aurora_create", {"body": FAB})
chk("⑧ material 未指定の呼びは従前どおり sources を材料とする(後方互換)", "来月" in (_m9 or ""))

# ── ⑦ 口を機械で数える(手書きの一覧にせぬ) ─────────────────────────────
print("── ⑦ Aurora を起票する口の全数 ──")
_lines = SRC_TEXT.splitlines()
_mouths, _unwired = 0, []
for i, ln in enumerate(_lines):
    if '_register_pending("aurora_create"' in ln or '_register_pending("aurora_append"' in ln:
        _mouths += 1
        _ctx = "\n".join(_lines[max(0, i - 16):i + 1])
        if "aurora_write_guard(" not in _ctx and "aurora_material_remember(" not in _ctx:
            _unwired.append(i + 1)
chk(f"⑦ Aurora を起票する口を機械で数えた({_mouths}口)", _mouths >= 5)
chk("⑦ ★全ての口が材料を控えておる(検問を通るか、自ら控えるか)。未配線=" + str(_unwired),
    not _unwired)

_m4 = "if(nt) c.appendChild(nt);"
chk("★変異(帯をカードから外す): 注記は作られても画面に嵌まらず人へ届かぬ(赤化実証)",
    _m4 not in HTML.replace(_m4, "", 1))

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
