#!/usr/bin/env python3
"""gate: Aurora 資料の**往復で化粧が焼けぬ**ことを証める関（2026-08-28 甲・殿御下命）。

## なぜ要るか（実測・作り話ではない）

kiyotomo殿は 8/27〜8/28 の二日、SORAFUNE 議事録に「見出しを太字にしたり表で整えたりして」を
**八度以上**打たれた。outbox は毎回 `sent` と刻んでいた（v6〜v11・全て uid=31 の正規経路）。
だが現物 d78e9ca6 の v11 を取り寄せると `<table>` 0個・`<strong>` 0個・`<h2>` 0個。
本文はこう潰れていた——「2. BOKAN 担当事項 **項目内容** Flight Simulator (ファイロ)自利率系…」。
「項目」と「内容」が繋がっている＝**表の枡目の継ぎ目が消えた跡**である。

下手人は lost update を防ぐために入れた `aurora_canonical_body`（正本の取り直し）自身であった。
全タグを `re.sub(r"<[^>]+>", "")` で剥ぐため、`</td>` の継ぎ目が失われ表が地の文へ潰れる。
その潰れた地の文が**次の版の材料**になるゆえ、往復のたびに化粧が焼け、二度と戻らぬ。

★**帳簿は緑・現物は白紙**であった。書込の成否だけを見ていては永遠に気づけぬ病ゆえ、
  「往復して構造が残るか」を直に測る関をここに置く。

## 何を測るか

  md --(_md_body/note_html)--> HTML --(_html_to_md)--> md' --(_md_body)--> HTML'
  として HTML と HTML' の**構造の数**（table/strong/見出し/ul/a）が一致すること。

## 赤くなれることの証明（掟: 観測装置は赤くなれると証明するまで観測装置でない）

`--mutate` で**旧実装（全タグ素剥ぎ）**を同じ検体に通す。これが緑のままなら関は嘘をついている
ゆえ exit 1。実運用の判定（引数なし）と同じ検体・同じ assert を使う（別物を測って安心せぬ）。

Usage:
  python3 gate_aurora_roundtrip.py            # 本番実装を検める（0=緑 / 1=赤）
  python3 gate_aurora_roundtrip.py --mutate   # 旧実装が赤くなることを検める（0=関は正しい）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("CASPER_NO_DAEMON", "1")     # import 副作用（常駐の起動）を断つ
_ARGV, sys.argv = sys.argv, ["chat_server.py"]     # module級 argparse が gate の引数を食わぬよう
import chat_server as C                            # noqa: E402
import casper_aurora as A                          # noqa: E402
sys.argv = _ARGV

# 実害そのものの形をした検体（kiyotomo殿の議事録の骨格＝表・太字・見出し二段・箇条・リンク）。
# ★fixture が本番の材料の形をしていなければ、機構が壊れても緑のまま実害が再発する（Fable二度指摘）。
SPECIMEN = """## 2. BOKAN 担当事項

| 項目 | 内容 |
|---|---|
| Flight Simulator (ファイロ) | 自利率系フライトデータを受け取り、**シミュレーション実行** |
| 現場リサーチ | 実施。馬渕、他追加必要か確認 |

### 3. スケジュール

- 8月: 契約書締結
- 9月末: 本番実施

詳細は [Aurora の控え](http://nina_notepc_02:8100/doc/x) を見よ。"""

STRUCT = ("<table", "<strong", "<h3", "<h4", "<ul", "<a href", "<td", "<th")


def _legacy_strip(html_src):
    """2026-08-28 以前の実装（全タグ素剥ぎ）。突然変異の検体としてのみ使う。"""
    import html as _h
    t = re.sub(r"<br\s*/?>", "\n", str(html_src or ""))
    t = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|table|ul|ol)>", "\n", t)
    return _h.unescape(re.sub(r"<[^>]+>", "", t))


def _body_of(html_src):
    """note_html の化粧（題＋meta）を落として本文だけにする（canonical_body と同じ切り方）。"""
    m = re.search(r'(?is)<div class="meta">.*?</div>', html_src)
    return html_src[m.end():] if m else html_src


def roundtrip(to_md):
    """md → HTML → md' → HTML' を回し、(直の構造数, 往復後の構造数, md') を返す。"""
    html1 = A.note_html("SORAFUNE 様 MTG 議事録", SPECIMEN, author="kiyotomo")
    md2 = to_md(_body_of(html1))
    html2 = A._md_body(md2)
    direct = A._md_body(SPECIMEN)
    return ({t: direct.count(t) for t in STRUCT},
            {t: html2.count(t) for t in STRUCT}, md2)


def check(to_md, label):
    before, after, md2 = roundtrip(to_md)
    bad = [t for t in STRUCT if before[t] != after[t]]
    for t in STRUCT:
        mark = "✅" if before[t] == after[t] else "❌"
        print(f"  {mark} {t:9s} 直: {before[t]:2d}  往復後: {after[t]:2d}")
    # ★数だけでは足りぬ。実害の指紋そのもの（枡目が繋がって潰れた跡）を直に検める。
    fused = [w for w in ("項目内容", "現場リサーチ実施") if w in md2.replace(" ", "")]
    if fused:
        print(f"  ❌ 枡目が潰れた跡: {' / '.join(fused)}")
        bad.append("fused_cells")
    print(f"{'🟢' if not bad else '🔴'} {label}: " + ("構造は往復で保たれておる" if not bad
          else "失われた構造 → " + ", ".join(bad)))
    return not bad


def residual_limit():
    """★塞げておらぬ一つ——記して残す(緑を見て『全て直った』と誤らぬため)。

    実地検分(2026-08-28・Aurora doc 27004e4e / Tim殿の日中二言語の報告書)で確認:
    Casper 製でない HTML の**隣り合う inline 要素は依然として繋がる**。
      <span>投稿者</span><span>投稿者</span> → 「投稿者投稿者」
    「項目内容」と**同じ型**の癒着である。ただし:
      ・旧実装でも全く同じに繋がっていた(退行ではない・表と太字が失われぬ分だけ純増)
      ・`_md_body` は <span> を吐かぬゆえ、往復の約定の外にある形である
      ・境目に空白を入れる手は採らぬ——和文は語を空白で切らぬゆえ
        「<span>重要</span>です」が「重要 です」に化け、正常な本文を却って壊す
    ★塞ぐなら「どの inline が同一語の続きか」を知る要があり、別の手当になる。
    """
    return "span癒着(Casper製でないHTML)・未解決"


def main():
    mutate = "--mutate" in _ARGV[1:]
    if mutate:
        print("【突然変異】旧実装（全タグ素剥ぎ）を同じ検体に通す——ここが緑なら関は嘘である")
        ok = check(_legacy_strip, "旧実装")
        if ok:
            print("❌ 関が嘘をついておる: 旧実装（実害を生んだ当人）を緑と判じた")
            return 1
        print("✅ 関は赤くなれる（旧実装を正しく捕らえた）")
        return 0
    print("【本番実装】chat_server._html_to_md の往復")
    print(f"  ⚠ 残る穴(承知の上): {residual_limit()}")
    return 0 if check(C._html_to_md, "本番実装") else 1


if __name__ == "__main__":
    sys.exit(main())
