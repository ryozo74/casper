#!/usr/bin/env python3
r"""承認前プレビューと承認後の帰還文の回帰ゲート(殿御下命2026-08-29・Fable処方「乙」)。全PASSで exit 0。

kiyotomo殿は8/28に**三度**仰せになった——「承認カードの時点で Aurora でどう表示されるか見せられぬか」。
従前は本文の生テキストを textarea に出すのみで、表・太字・見出しが**実際にどう出るか**は
承認して書き込むまで判らなんだ。八度の打ち直しはこれと無縁ではない。

★Fable曰く **実行と同一のレンダラで描け**。別関数で描けば
「プレビューでは太字だったのに実物は違う」という**新しい嘘**が生まれる。

守る掟:
 ① chat_server から Aurora の絵を描く口は `aurora_render_for_write` **ただ一つ**。
    プレビューも執行も同じ関を通る(呼び分けを許さぬ)。
 ② プレビューは**編集中の本文**を描く(見たままが保存される)。
 ③ 承認後の帰還文は真実値——版・URL は結果から**読めた時のみ**添え、読めねば黙る。
 ④ 承認待ちの引き当ては一関(`_load_pending`)。プレビューと執行で解釈が割れぬ。
 ⑤ 覗けるのは本人のみ。名乗りの無い者には開かぬ(丙と同じ掟)。
 ⑥ 画面は**モーダルにせぬ**(殿ルール)。カードの下に畳んで開く帯。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_aurora                                     # noqa: E402

SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()
HTML = open(os.path.join(HERE, "chat.html"), encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["aurora_render_for_write", "aurora_written_note", "_load_pending"]


def build(src_text):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
    missing = [w for w in WANT_F if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import json, os, re", M)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    M["casper_aurora"] = casper_aurora
    M["_uid_to_name"] = lambda uid: {"31": "kiyotomo"}.get(str(uid), "casper")
    M["PENDING_ACTIONS"] = {}
    M["casper_outbox"] = None
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

# ★本番の材料の形をした検体(表・太字・見出し・箇条書き。8/28の実害の指紋を含む)
ARGS = {"title": "SORAFUNE 定例議事録", "tags": ["議事録"],
        "body": ("## 2. BOKAN 担当事項\n\n"
                 "| 項目 | 内容 |\n|---|---|\n| Flight Simulator | 現場リサーチ実施 |\n\n"
                 "- **UE** の検証を継続\n- 次回は8月末\n")}

# ── ① 絵を描く口はただ一つ ───────────────────────────────────────────────
print("── ① 単一レンダラ ──")
_calls = [n for n in ast.walk(ast.parse(SRC_TEXT))
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "make_note"]
chk("① chat_server で casper_aurora.make_note を呼ぶのは1箇所のみ", len(_calls) == 1)
chk("① その1箇所は aurora_render_for_write の中に在る",
    "    return casper_aurora.make_note(" in SRC_TEXT
    and SRC_TEXT.index("def aurora_render_for_write(") < SRC_TEXT.index("    return casper_aurora.make_note("))
chk("① プレビューの口が同じ関を呼ぶ",
    'self._json({"ok": True, "title": a.get("title", ""),\n'
    '                        "html": aurora_render_for_write(a, who.get("uid") or pend.get("uid"))})' in SRC_TEXT)
chk("① 執行も同じ関を呼ぶ", "html = aurora_render_for_write(a, actor)" in SRC_TEXT)

html_prev = M["aurora_render_for_write"](ARGS, "31")
html_exec = M["aurora_render_for_write"](ARGS, "31")
chk("① ★プレビューと執行の絵が一字一句同じ", html_prev == html_exec)
chk("① 表が実際に描かれる(生テキストのままでない)", html_prev.count("<table>") == 1 and "<th>項目</th>" in html_prev)
chk("① 太字が実際に描かれる", "<strong>UE</strong>" in html_prev)
chk("① 見出し・箇条書きも描かれる", "<h3>" in html_prev and "<ul>" in html_prev)
chk("① 自己完結(そのまま画面に嵌められる)", html_prev.startswith("<!DOCTYPE html") and "<style>" in html_prev)
chk("① 著者は本人の名(casper でなく)", "kiyotomo" in html_prev)

# ── ② 編集中の本文を描く ─────────────────────────────────────────────────
print("── ② 見たままが保存される ──")
chk("② サーバは req の body で args を上書きする",
    'if req.get("body") is not None:        # ★編集中の本文をそのまま描く(見たままが保存される)' in SRC_TEXT)
_edited = dict(ARGS); _edited["body"] = ARGS["body"] + "\n- **追記**した行\n"
chk("② 編集した本文が絵に現れる", "<strong>追記</strong>" in M["aurora_render_for_write"](_edited, "31"))
chk("② 画面は textarea の現在値を送る", "body: ta?ta.value:undefined});" in HTML.split("/api/aurora/preview")[1][:200])

# ── ③ 帰還文は真実値 ─────────────────────────────────────────────────────
print("── ③ 承認後の帰還文 ──")
n1 = M["aurora_written_note"]("aurora_append", ARGS, '{"version": 12, "id": "d78e9ca6"}')
chk("③ 何をしたかを名乗る", "書き改め" in n1 and "SORAFUNE 定例議事録" in n1)
chk("③ 結果に在る版を伝える", "v12" in n1)
n2 = M["aurora_written_note"]("aurora_append", ARGS, "{}")
chk("③ ★結果に版が無ければ版を騙らぬ", "v" not in n2.replace("Aurora", "").replace("version", ""))
n3 = M["aurora_written_note"]("aurora_create", {"title": "新資料"}, '{"id": "abc"}')
chk("③ 新規作成は『新しい資料として』と名乗る", "新しい資料" in n3)
n4 = M["aurora_written_note"]("aurora_append", ARGS, "壊れた文字列")
chk("③ 結果が読めずとも転ばぬ(空約束もせぬ)", isinstance(n4, str) and "v" not in n4.replace("Aurora", ""))
chk("③ 画面は帰還文が在ればそれを出す(生JSONを貼らぬ)",
    "rEl.textContent = res.message || ('✅ 実行しました: '" in HTML)
chk("③ サーバが帰還文を返す配線が在る", '"message": _msg,' in SRC_TEXT)

# ── ④ 引き当ては一関 ─────────────────────────────────────────────────────
print("── ④ 承認待ちの引き当て ──")
chk("④ _load_pending を通す箇所が2つ(プレビューと執行)", SRC_TEXT.count("_load_pending(") == 3)
M["PENDING_ACTIONS"] = {"p1": {"tool": "aurora_append", "args": ARGS, "uid": "31"}}
chk("④ キャッシュから引ける", (M["_load_pending"]("p1") or {}).get("uid") == "31")
chk("④ 無い物は無いと申す(でっち上げぬ)", M["_load_pending"]("nope") is None)

# ── ⑤ 覗けるのは本人のみ ─────────────────────────────────────────────────
print("── ⑤ 誰に開くか ──")
_ep = SRC_TEXT[SRC_TEXT.index('if self.path == "/api/aurora/preview":'):
               SRC_TEXT.index('if self.path == "/api/confirm":')]
chk("⑤ 名乗りの無い者には開かぬ", "どなたか判じられませなんだ" in _ep)
chk("⑤ 他人のカードは覗けぬ", "本人のみ検められまする" in _ep)
chk("⑤ Aurora以外のカードには絵を出さぬ",
    'pend.get("tool") not in ("aurora_create", "aurora_append")' in _ep)
chk("⑤ 名乗りの検めが本人照合より先(uid空で str(who['uid']) を引いて転ばぬ)",
    _ep.index("どなたか判じられませなんだ") < _ep.index("本人のみ検められまする"))

# ── ⑥ 画面(モーダルにせぬ) ───────────────────────────────────────────────
print("── ⑥ 画面 ──")
_rc = HTML[HTML.index("function renderConfirm(b, pa){"):]
_rc = _rc[:_rc.index("\nfunction ", 10)]
chk("⑥ 見え方の釦が在る", "Auroraでの見え方" in _rc)
chk("⑥ Aurora のカードにのみ出す",
    "const isAurora=(pa.tool==='aurora_create'||pa.tool==='aurora_append');" in _rc)
chk("⑥ ★モーダルでなくカード内の帯(殿ルール)",
    "pvWrap" in _rc and "position:fixed" not in _rc and "showModal" not in _rc)
chk("⑥ 実物の絵を嵌める枠が在る", "iframe" in _rc and "srcdoc" in _rc)
chk("⑥ 枠の中の script は走らせぬ", "setAttribute('sandbox','')" in _rc)
chk("⑥ 畳める(開きっぱなしにせぬ)", "見え方を閉じる" in _rc)
chk("⑥ 描けなんだ時は理由を出す(無言で閉じぬ)", "見え方を描けませなんだ" in _rc)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
_old_render = '''    return casper_aurora.make_note(a.get("title", ""), a.get("body", ""),
                                   author=_uid_to_name(actor_uid), tags=a.get("tags"))'''
assert SRC_TEXT.count(_old_render) == 1, "変異が当たっていない(ゲートの自己点検)"
mut = SRC_TEXT.replace(_old_render, '''    return "<html><body><pre>" + str(a.get("body", "")) + "</pre></body></html>"''')
M2, _ = build(mut)
_m2 = M2["aurora_render_for_write"](ARGS, "31")
chk("★変異(別レンダラで描く): 表も太字も出ず、執行の絵と食い違う(赤化実証)",
    "<table>" not in _m2 and _m2 != html_prev)

_old_ver = '''    ver = rd.get("version") or rd.get("version_no") or rd.get("v")
    if ver:'''
assert SRC_TEXT.count(_old_ver) == 1, "変異が当たっていない(ゲートの自己点検)"
mut2 = SRC_TEXT.replace(_old_ver, '''    ver = rd.get("version") or rd.get("version_no") or rd.get("v") or 1
    if ver:''')
M3, _ = build(mut2)
chk("★変異(読めぬ版を1と騙る): 結果に無い版を名乗ってしまう(赤化実証)",
    "v1" in M3["aurora_written_note"]("aurora_append", ARGS, "{}"))

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
