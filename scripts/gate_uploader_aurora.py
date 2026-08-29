#!/usr/bin/env python3
r"""投じた資料を Aurora へ載せる道の回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

実害(2026-08-26 18:33): kiyotomo殿が「sorafune 様　MTG.rtf」を投じ「Auroraにアップ」と
書き添えたが、uploader の行先は qc / daily / reference の三つしか無かった。note に書かれた
「Auroraにアップ」は記録されるだけで読まれず、既定の qc→daily へ流れ、Aurora には一度も届かぬまま
同じ資料が三度投じられた。

守る掟:
 ① .rtf を含む実ファイルから本文を抽出し、Aurora の下書き(承認カード)を立てる。
 ② **書き込まぬ**。承認カードを立てるだけ。直書きの裏口を作れば門が二つになる。
 ③ 抽出できなかった時、その失敗文言を本文として載せぬ。
    ★casper_extract は失敗を「(非対応形式 .xxx)」と括弧書きで返す。これをそのまま載せれば
      その一行が全社の共有資料になる。失敗・空・成功をそれぞれ別の出口で名乗らせる。
 ④ 題は人が付けられる。空ならファイル名に倒す(勝手に作文せぬ)。
 ⑤ 画面(chat.html)に行先が出ており、返った承認カードが描かれる。
    ★機構が在っても画面に無ければ、殿の手からは存在せぬのと同じ。
 ★突然変異: 抽出失敗の検問を殺すと③が赤化することを実証する。
"""
import ast
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()
import casper_extract  # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


TREE = ast.parse(SRC_TEXT)
WANT = ["uploader_to_aurora"]
picked = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in WANT]
if len(picked) != len(WANT):
    print(f"❌ chat_server.py に機構が見当たらぬ: {WANT}")
    sys.exit(1)

TMP = tempfile.mkdtemp(prefix="gate_upl_aurora_")
PROPOSED = []
REMEMBERED = []


def build(nodes):
    M = {}
    exec("import os, re", M)
    M["casper_extract"] = casper_extract
    M["_action_summary"] = lambda tool, a: f"Aurora ノート作成 → タイトル: {a.get('title')}"
    # ★取込は逐語の運搬ゆえ、材料=抜いた本文そのもの(接地の注記が偽で鳴かぬように控える)。
    #   本体の検めは gate_aurora_grounding.py。此処では呼ばれても転ばぬ事のみ見る。
    M["aurora_material_remember"] = lambda body, material: REMEMBERED.append((body, material))
    M["_register_pending"] = lambda tool, args, uid, summary, **kw: (
        PROPOSED.append({"tool": tool, "args": args, "uid": uid, "kw": kw}) or f"pid{len(PROPOSED)}")
    # ★本物のAurora書込を握る。呼ばれたら②違反として掴まえる。
    class _Au:
        called = []

        @staticmethod
        def create(*a, **k):
            _Au.called.append(("create", a, k)); return "{}"

        @staticmethod
        def append_version(*a, **k):
            _Au.called.append(("append_version", a, k)); return "{}"
    M["casper_aurora"] = _Au
    exec(compile(ast.Module(body=nodes, type_ignores=[]), SRC, "exec"), M)
    return M, _Au


M, AU = build(picked)
F = M["uploader_to_aurora"]

# ── 実ファイルを作る(実害と同じ .rtf) ────────────────────────────────────
BODY = ["SORAFUNE 様　MTG 議事録",
        "1. シナリオ・コンセプト",
        "シナリオ: 火災時想定（防衛かは未定）"]


def sjis(t):
    return "".join("\\'%02x" % b for b in t.encode("cp932"))


rtf = ("{\\rtf1\\ansi\\ansicpg932{\\fonttbl\\f0\\fnil\\fcharset128 HiraginoSans-W3;}"
       "{\\colortbl;\\red255\\green255\\blue255;}\\pard\\f0\\fs28 "
       + "\\\n".join(sjis(l) for l in BODY) + "}")
P_RTF = os.path.join(TMP, "sorafune MTG.rtf")
open(P_RTF, "wb").write(rtf.encode("latin-1"))

# ── ①②④ 正道 ───────────────────────────────────────────────────────────
PROPOSED.clear(); AU.called.clear()
r = F(P_RTF, "SORAFUNE 様 MTG 議事録", "sorafune MTG.rtf", "31")
chk("① .rtf から下書きが立つ", r.get("ok") is True)
chk("① 承認カードが返る", bool(r.get("confirm")) and r["confirm"]["tool"] == "aurora_create")
chk("① 抽出した本文が下書きに入る", all(l in r["confirm"]["args"]["body"] for l in BODY))
chk("① 書体名など本文でないものが混ざらぬ", "Hiragino" not in r["confirm"]["args"]["body"])
chk("② この時点では書き込んでおらぬ", r.get("written") is False and not AU.called)
chk("② 台帳へは『提案』として1件だけ積む", len(PROPOSED) == 1 and PROPOSED[0]["tool"] == "aurora_create")
chk("② 文言が『まだ書き込んでおらぬ』と正直に言う", "まだ書き込んでおりませぬ" in r.get("message", ""))
chk("④ 人が付けた題が使われる", r["confirm"]["args"]["title"] == "SORAFUNE 様 MTG 議事録")

PROPOSED.clear()
r2 = F(P_RTF, "", "sorafune MTG.rtf", "31")
chk("④ 題が空ならファイル名に倒す(作文せぬ)", r2["confirm"]["args"]["title"] == "sorafune MTG")

# ── ③ 失敗を本文として載せぬ ─────────────────────────────────────────────
print("── ③ 失敗の出口 ──")
P_BAD = os.path.join(TMP, "shot.zzz")
open(P_BAD, "wb").write(b"\x00\x01\x02")
PROPOSED.clear(); AU.called.clear()
rb = F(P_BAD, "何かの資料", "shot.zzz", "31")
chk("③ 非対応形式は ok=False で名乗る", rb.get("ok") is False)
chk("③ 失敗の理由が判る", rb.get("reason") == "extract_failed")
chk("③ 失敗時にカードを立てぬ", not rb.get("confirm") and not PROPOSED)
chk("③ 『(非対応形式…)』を本文にせぬ", "(非対応形式" not in str(rb.get("confirm")))

P_EMPTY = os.path.join(TMP, "empty.txt")
open(P_EMPTY, "w").write("   \n  ")
PROPOSED.clear()
re_ = F(P_EMPTY, "空の資料", "empty.txt", "31")
chk("③ 空は『空』として失敗と別に名乗る", re_.get("reason") == "empty" and re_.get("ok") is False)
chk("③ 空でもカードを立てぬ", not PROPOSED)

PROPOSED.clear()
rn = F(os.path.join(TMP, "missing.rtf"), "x", "missing.rtf", "31")
chk("③ 実体が無い時は no_file として名乗る(例外で落ちぬ)", rn.get("reason") == "no_file")
chk("③ 実体が無ければカードを立てぬ", not PROPOSED)

# ── ⑤ 画面に道がある ─────────────────────────────────────────────────────
print("── ⑤ 画面(chat.html) ──")
HTML = open(os.path.join(HERE, "chat.html"), encoding="utf-8").read()
chk("⑤ 用途に Aurora の行先が出ている", "'aurora','📖 Aurora" in HTML)
chk("⑤ その行先が intent='aurora' で送られる", "'aurora',res.recognized" in HTML)
chk("⑤ 返った承認カードを描く配線がある",
    bool(re.search(r"if\(r\.confirm\)\{[^}]*renderConfirm\(b,r\.confirm\)", HTML, re.S)))
chk("⑤ 失敗を成功の見た目で出さぬ配線がある", "if(r.ok===false)" in HTML)

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証(抽出失敗の検問を殺す) ---")
mut_src = SRC_TEXT.replace('    if body.startswith("("):', '    if False:', 1)
assert mut_src != SRC_TEXT, "変異が当たっていない(ゲートの自己点検)"
mut_tree = ast.parse(mut_src)
mut_nodes = [n for n in mut_tree.body if isinstance(n, ast.FunctionDef) and n.name in WANT]
M2, _ = build(mut_nodes)
PROPOSED.clear()
rm = M2["uploader_to_aurora"](P_BAD, "何かの資料", "shot.zzz", "31")
chk("★変異: 『(非対応形式 .zzz)』が本文として共有資料に載る(赤化実証)",
    rm.get("ok") is True and "非対応形式" in rm.get("confirm", {}).get("args", {}).get("body", ""))
PROPOSED.clear()
chk("★復元確認: 本物では依然として弾く", F(P_BAD, "x", "shot.zzz", "31").get("ok") is False)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
