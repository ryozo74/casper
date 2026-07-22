#!/usr/bin/env python3
"""語彙検問 — M5 seam ガード（機構で封じる）。

パックマニフェスト(pack/<name>/pack.yaml)の `vocab` に載る bokan 固有語が、
エンジン(*.py)に直書きされていないかを機械判定する。1件でも在れば seam 違反として
非ゼロ終了。M5「語彙抜去」の完了条件＝本検問が緑になること。CI に載せて再混入を封じる。

  使い方:  python3 pack_lint.py [--pack bokan] [-v]
  緑(exit 0) = エンジンに bokan 語ゼロ。赤(exit 1) = 違反を file:line で列挙。

規律: これは「お説教(プロンプト)」でなく「検問(テスト)」。服従でなく機構で seam を守る。
"""
import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_manifest(pack):
    path = os.path.join(HERE, "pack", pack, "pack.yaml")
    vocab, inc, exc = [], ["*.py"], set()
    section = None
    for ln in open(path, encoding="utf-8"):
        raw = ln.rstrip("\n")
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # トップレベルキー
        if re.match(r"^(vocab|components|engine_scan|exclude|include_glob):", raw) and not raw.startswith(" "):
            section = s.split(":", 1)[0]
            continue
        if raw.startswith("  ") and s.startswith("include_glob:"):
            section = "include_glob"
            m = re.findall(r'"([^"]+)"', s)
            if m:
                inc = m
            continue
        if raw.startswith("  ") and s == "exclude:":
            section = "exclude"
            continue
        # リスト項目
        if s.startswith("- "):
            item = s[2:].strip().strip('"').strip("'")
            if section == "vocab":
                vocab.append(item)
            elif section == "exclude":
                exc.add(item)
    return vocab, inc, exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="bokan")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    vocab, inc, exc = _load_manifest(args.pack)
    if not vocab:
        print("(vocab 未定義 — マニフェストを確認せよ)")
        return 2
    # マッチ器: ASCII語は語境界つき(識別子内の部分一致=偽陽性を排除。例 NINA が
    # nina_notepc_02 に誤マッチするのを防ぐ)。CJK語は\bが効かぬゆえ substring。
    needles = []
    for v in vocab:
        if v.isascii():
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])", re.I)
            needles.append((v, pat))
        else:
            needles.append((v, None))

    files = []
    for g in inc:
        files += glob.glob(os.path.join(HERE, "**", g), recursive=True)
    # pack/ 配下(パックデータ)とマニフェスト除外リストは検問対象外
    def skip(p):
        base = os.path.basename(p)
        rel = os.path.relpath(p, HERE)
        return base in exc or rel.startswith("pack" + os.sep) or "__pycache__" in rel
    files = sorted({p for p in files if not skip(p)})

    violations = []
    for p in files:
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for orig, pat in needles:
                hit = pat.search(line) if pat is not None else (orig in line)
                if hit:
                    violations.append((os.path.relpath(p, HERE), i, orig, line.strip()[:100]))

    if not violations:
        print(f"✅ 語彙検問 PASS — engine に pack[{args.pack}] 語彙ゼロ ({len(files)}ファイル走査)")
        return 0

    # 集計
    by_file = {}
    for rel, ln, term, txt in violations:
        by_file.setdefault(rel, []).append((ln, term, txt))
    print(f"❌ 語彙検問 FAIL — {len(violations)}件の seam 違反 / {len(by_file)}ファイル (pack={args.pack})")
    for rel in sorted(by_file):
        hits = by_file[rel]
        print(f"\n  {rel}  ({len(hits)}件)")
        for ln, term, txt in hits:
            if args.verbose:
                print(f"    :{ln}  [{term}]  {txt}")
            else:
                print(f"    :{ln}  [{term}]")
    # 語ごとの合計
    from collections import Counter
    c = Counter(t for _, _, t, _ in violations)
    print("\n  語彙別:", ", ".join(f"{t}×{n}" for t, n in c.most_common()))
    return 1


if __name__ == "__main__":
    sys.exit(main())
