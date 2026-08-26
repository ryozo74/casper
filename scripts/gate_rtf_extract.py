#!/usr/bin/env python3
r"""RTF 本文抽出の回帰ゲート(殿御下命 2026-08-26)。全PASSで exit 0。

実害(2026-08-26 18:33): kiyotomo殿が「sorafune 様　MTG.rtf」を投じ「Auroraにアップ」と
書き添えたが、.rtf は casper_extract の対応表に載っておらず「(非対応形式 .rtf)」で落ちた。
macOS テキストエディットの既定書式ゆえ社内で日常的に出る形式である。

守る掟:
 ① 日本語の本文が読める(cp932 の \'xx 連なりも、Word の \uNNNN 表記も)。
 ② 段落の区切りが保たれる。一続きの塊にすると議事録の構造が失われる。
 ③ 本文でないもの(書体名/色定義/著者/スタイル定義)を本文に混ぜぬ。
    ★これが混じると、そのまま Aurora の共有資料に載って全社が読む。
 ④ 解さぬ群は「語を表に足して追いかける」のでなく \* の印を見て群ごと捨てる。
    ★語を数える方式は必ず取りこぼす——実測で『*;;;』が本文の先頭に混じった。
 ⑤ 読めぬファイルは空でなく理由を名乗る(失敗とゼロを別出口へ)。
 ★突然変異: 破棄の機構を殺すと③④が赤化することを実証する。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_extract as E  # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


TMP = tempfile.mkdtemp(prefix="gate_rtf_")


def write_rtf(name, text):
    p = os.path.join(TMP, name)
    open(p, "wb").write(text.encode("latin-1"))
    return p


def sjis(t):
    """日本語を cp932 の \'xx 連なりへ(テキストエディットが吐く形)。"""
    return "".join("\\'%02x" % b for b in t.encode("cp932"))


def uni(t):
    r"""日本語を \uNNNN 表記へ(Word が吐く形)。"""
    return "".join(("\\u%d?" % ord(c)) if ord(c) > 127 else c for c in t)


# ── ① テキストエディット風(cp932・\+改行で段落) ─────────────────────────
BODY1 = ["SORAFUNE 様　MTG 議事録",
         "1. シナリオ・コンセプト",
         "シナリオ: 火災時想定（防衛かは未定）",
         "場所: 体育館等（30m x 30m x 高さ30m の範囲内）"]
rtf1 = ("{\\rtf1\\ansi\\ansicpg932\\cocoartf2761"
        "{\\fonttbl\\f0\\fnil\\fcharset128 HiraginoSans-W3;\\f1\\fnil HelveticaNeue;}"
        "{\\colortbl;\\red255\\green255\\blue255;\\red14\\green14\\blue14;}"
        "{\\*\\expandedcolortbl;;\\cssrgb\\c6700\\c6700\\c6700;}"
        "\\pard\\tx560\\pardirnatural\\partightenfactor0\\f0\\fs28 "
        + "\\\n".join(sjis(l) for l in BODY1) + "}")
p1 = write_rtf("textedit.rtf", rtf1)
r1 = E.extract(p1)

print("── ① テキストエディット風(cp932) ──")
print(r1)
chk("① 日本語の本文が読める", all(l in r1 for l in BODY1))
chk("① 全角括弧・記号が壊れぬ", "（防衛かは未定）" in r1)
chk("② 段落の区切りが保たれる", r1.count("\n") >= len(BODY1) - 1)
chk("③ 書体名が混入せぬ", "Hiragino" not in r1 and "Helvetica" not in r1)
chk("③ 色定義が混入せぬ", "cssrgb" not in r1 and "red255" not in r1)
chk("④ 無視可能destination の残骸が混入せぬ(『*;;;』の再発防止)",
    not r1.lstrip().startswith("*") and ";;" not in r1)

# ── ② Word風(\uNNNN・\par で段落・info/stylesheet あり) ──────────────────
BODY2 = ["9月3週: テストフライト", "9月末: 本番実施"]
rtf2 = ("{\\rtf1\\ansi\\ansicpg1252"
        "{\\stylesheet{\\s0 Normal;}{\\s1 heading 1;}}"
        "{\\info{\\title " + uni("秘密の書体名") + "}{\\author kiyotomo}}"
        "\\pard " + ("\\par ".join(uni(l) for l in BODY2)) + "\\par}")
p2 = write_rtf("word.rtf", rtf2)
r2 = E.extract(p2)

print("── ② Word風(\\uNNNN) ──")
print(r2)
chk("① \\uNNNN 表記の日本語が読める", all(l in r2 for l in BODY2))
chk("② \\par が改行になる", r2.count("\n") >= 1)
chk("③ info群(著者/題)が本文に混入せぬ", "kiyotomo" not in r2 and "秘密の書体名" not in r2)
chk("③ stylesheet が本文に混入せぬ", "Normal" not in r2 and "heading" not in r2)

# ── ⑤ 読めぬもの・空 ─────────────────────────────────────────────────────
chk("⑤ 非対応形式は従前どおり理由を名乗る",
    E.extract(os.path.join(TMP, "x.zzz")).startswith("(非対応形式"))
p3 = write_rtf("empty.rtf", "{\\rtf1\\ansi{\\fonttbl\\f0 X;}}")
r3 = E.extract(p3)
chk("⑤ 中身の無いrtfは空文字でなく『空』と名乗る", r3 == "(rtf: 空)")
chk("⑤ 壊れたファイルでも例外で落ちぬ",
    isinstance(E.extract(write_rtf("broken.rtf", "{\\rtf1\\ansi\\'zz{{{")), str))

# ── ★突然変異: 破棄の機構を殺す ─────────────────────────────────────────
print("\n--- 突然変異検証(破棄群の表と \\* の印を殺す) ---")
_orig_rtf = E._rtf
_src = open(os.path.join(HERE, "casper_extract.py"), encoding="utf-8").read()
# ★集合の先頭1語だけを差し替えると残りが生き、変異が効いていないのに緑になる
#   (実測でこれを踏んだ)。破棄群は**丸ごと**空にする。
_mut = re.sub(r"SKIP = \{[^}]*\}", 'SKIP = set()', _src, count=1)
assert _mut != _src, "破棄群の変異が当たっていない(ゲートの自己点検)"
_mut2 = _mut.replace('if raw.startswith("\\\\*", i):', 'if False:')
assert _mut2 != _mut, "\\* の印の変異が当たっていない(ゲートの自己点検)"
_mut = _mut2
_ns = {"__file__": os.path.join(HERE, "casper_extract.py"), "__name__": "casper_extract_mutated"}
exec(compile(_mut, "casper_extract.py(mutated)", "exec"), _ns)
r1m = _ns["_rtf"](p1)
r2m = _ns["_rtf"](p2)
chk("★変異: 書体名/色定義が本文へ漏れる(赤化実証)",
    ("Hiragino" in r1m or "cssrgb" in r1m or r1m.lstrip().startswith("*")))
chk("★変異: 著者/スタイル定義が本文へ漏れる(赤化実証)",
    ("kiyotomo" in r2m or "Normal" in r2m))
chk("★復元確認: 本物では依然として混入せぬ",
    "Hiragino" not in E.extract(p1) and "kiyotomo" not in E.extract(p2))

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
