#!/usr/bin/env python3
r"""貼られた資料を『材料』と判ずる入口の回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

実害(2026-08-26 18:26〜18:29): kiyotomo殿は SORAFUNE の議事録本文を**四度**貼った。
そのたび Casper は「問い」として読み、PJ状況の要約や逆インタビューを返した——
貼られた本文には一言も触れずに。殿は噛み合わぬまま貼り直しを繰り返し、最後は諦められた。

守る掟:
 ① 長い・多行・構造のある貼り付けは『材料』と判ずる。
 ② **頼み事が混じっていれば材料ではない。** 「これをAuroraにアップして」は通常経路へ返す。
    ★ここを誤ると、殿の依頼を機構が握り潰して選択カードに変えてしまう(害の作り替え)。
 ③ 短い発話・ただの会話・問いは材料に倒さぬ(過剰発動の禁)。
 ④ 返す中身は**数え上げた事実のみ**(行数/字数/見出し)。内容の解釈をここでせぬ。
    ★retrieve-then-render: 憶測の入る余地を作らない。
 ⑤ 勝手に保存も要約もせぬ。「まだ何もしておりませぬ」と告げ、選択肢を人へ返す。
 ⑥ 選択肢は自足文(say)を持つ——押せばその指示として再投入される。
 ★突然変異: 依頼語の検問を殺すと②が赤化する(依頼が握り潰される)ことを実証する。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["pasted_material", "material_choices"]
WANT_A = ["_MATERIAL_MIN_CHARS", "_MATERIAL_MIN_LINES", "_MATERIAL_MIN_STRUCT",
          "_MATERIAL_STRUCT_RE", "_MATERIAL_REQUEST_RE"]


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
    exec("import os, re", M)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

# ── ★実害そのもの: kiyotomo殿が四度貼った議事録(18:27:21 の一通) ─────────
REAL_PASTE = """SORAFUNE 様　MTG 議事録
1. シナリオ・コンセプト
シナリオ: 火災時想定（防衛かは未定）
場所: 体育館等（30m x 30m x 高さ30m の範囲内）

2. BOKAN 担当事項
Flight Simulator (ファイロ): 自利率系フライトデータを受け取り、シミュレーション実行。
現場リサーチ: 実施。
3Dデータ: GS（空間座標系含む）。
インターフェイス/ブリッジ: Unreal Engineとの接続部分の構築。
3. スケジュール
8月: 契約書締結
9月1〜2週: インターフェイス調整
9月3週: テストフライト
9月末: 本番実施
4. その他・アクションアイテム
Unreal Engine資料: ジェイソンの企画内容を反映した資料提供。
再生機能: シミュレーターに再生機能を追加。
情報共有: 今後はSlack等で連携。"""

print("── ① 材料と判ずる ──")
mat = M["pasted_material"](REAL_PASTE)
chk("① ★実害の貼り付けを材料と判ずる", mat is not None)
chk("④ 行数を数える(解釈でなく数え上げ)", mat and mat["lines"] == 18)
chk("④ 字数を数える", mat and mat["chars"] == len(REAL_PASTE.strip()))
chk("④ 見出しを本文から取る(でっち上げぬ)",
    mat and any("シナリオ・コンセプト" in h for h in mat["heads"])
    and all(h in REAL_PASTE for h in mat["heads"]))

print("── ② 頼み事が混じれば材料ではない(依頼を握り潰さぬ) ──")
for name, q in [
        ("これをAUroraにアップして", REAL_PASTE + "\nこれをAUroraにアップして"),
        ("まとめて", REAL_PASTE + "\n\nまとめて下さい"),
        ("要約して", REAL_PASTE + "\n要約して"),
        ("この行を消して", REAL_PASTE + "\n上の技術概要の行を削除して下さい"),
        ("問いかけ", REAL_PASTE + "\nこの案件の担当は誰ですか？")]:
    chk(f"② 『{name}』は通常経路へ返す", M["pasted_material"](q) is None)

print("── ③ 過剰に発動せぬ ──")
for name, q in [
        ("短い発話", "GSの状況は？"),
        ("ただの雑談", "承知いたした。ありがとう。"),
        ("短い箇条書き", "1. あ\n2. い\n3. う"),
        ("長いが一続きの文章", "あ" * 800),
        ("長いが行数が足りぬ", "1. あああ\n2. いいい\n" + "う" * 400)]:
    chk(f"③ 『{name}』は材料に倒さぬ", M["pasted_material"](q) is None)

chk("④ 文書の題は『自ら名乗っている先頭行』から採る(見出しを流用せぬ)",
    mat and mat.get("title_line") == "SORAFUNE 様　MTG 議事録")
chk("④ 先頭行が構造行なら題は空(でっち上げぬ)",
    (M["pasted_material"]("1. あああああああああ\n2. いいいいいいいいい\n3. ううううううううう\n"
                          "4. えええええええええ\n5. おおおおおおおおお\n" + "か" * 300) or {}
     ).get("title_line") == "")

print("── ⑤⑥ 返し方 ──")
reply, ch = M["material_choices"](mat)
chk("⑤ 『まだ何もしておらぬ』と告げる", "まだ何もしておりませぬ" in reply)
chk("⑤ 勝手に要約や保存の結果を書かぬ", "削除しました" not in reply and "保存しました" not in reply)
chk("④ 返しに載るのは数えた事実だけ",
    f"{mat['lines']}行" in reply and f"{mat['chars']}字" in reply)
chk("⑥ 選択肢が4つ出る", len(ch["options"]) == 4)
chk("⑥ Aurora保存の道がある", any(o["id"] == "mat_aurora" for o in ch["options"]))
chk("⑥ 既存資料の差し替えの道がある", any(o["id"] == "mat_replace" for o in ch["options"]))
chk("⑥ 『何もせぬ』も選べる(押させるための偽の選択にせぬ)",
    any(o["id"] == "mat_none" for o in ch["options"]))
chk("⑥ どの選択肢も自足文(say)を持つ",
    all(o.get("say") and len(o["say"]) > 10 for o in ch["options"]))
chk("⑥ 保存の say に使う題は文書の題(見出しでない)",
    any("SORAFUNE 様　MTG 議事録" in o["say"] for o in ch["options"] if o["id"] == "mat_aurora"))
chk("⑥ 差し替えの say は『全文』を要求する(丸ごと入替で資料が欠けぬよう)",
    any("全文" in o["say"] for o in ch["options"] if o["id"] == "mat_replace"))

print("── ⑦ 結線 ──")
_seg = SRC_TEXT[SRC_TEXT.index("_material = None"):]
_seg = _seg[:2500]
chk("⑦ 依頼らしき発話では材料判定を掛けぬ", "_looks_like_action(ll_user)" in _seg)
chk("⑦ 材料なら生成ループを跳ばして決定的に返す",
    'routed = {"_choices": True, "reply": _mreply}' in _seg)
chk("⑦ 既存の選択カードが在る時は上書きせぬ", "elif _material and not routed:" in _seg)

print("\n--- 突然変異検証(依頼語の検問を殺す) ---")
mut = SRC_TEXT.replace("    if _MATERIAL_REQUEST_RE.search(t):\n        return None",
                       "    if False:\n        return None", 1)
assert mut != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M2, _ = build(mut)
chk("★変異: 『これをAuroraにアップして』が材料へ倒れ、依頼が握り潰される(赤化実証)",
    M2["pasted_material"](REAL_PASTE + "\nこれをAUroraにアップして") is not None)
chk("★復元確認: 本物では依然として依頼を通常経路へ返す",
    M["pasted_material"](REAL_PASTE + "\nこれをAUroraにアップして") is None)

mut2 = SRC_TEXT.replace('_MATERIAL_MIN_CHARS = int(os.environ.get("CASPER_MATERIAL_MIN_CHARS", "300"))',
                        '_MATERIAL_MIN_CHARS = 0', 1)
assert mut2 != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
M3, _ = build(mut2)
chk("★変異(長さの閾値を殺す): 短い箇条書きまで材料に倒れる(過剰発動の赤化実証)",
    M3["pasted_material"]("1. あ\n2. い\n3. う\n4. え\n5. お") is not None)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
