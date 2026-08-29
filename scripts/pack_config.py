#!/usr/bin/env python3
"""パック設定の読取 — M5 seam。engine は「規則の雛形」だけを持ち、bokan 固有の
値(別名・固有名リスト・連絡先等)は pack/<name>/pack.yaml から差し込む(Fable処方)。

これは digest 注入経路ではなく静的 config 値の参照ゆえ、二重 digest スタックには当たらぬ。
yaml.safe_load は既存(casper_authority)と同方式。ファイル不在/壊れでも例外を投げず既定へ縮退する
(fail-closed=examples等は空・呼出元はプレースホルダで動き続ける)。ただし「黙って」縮退はせぬ:
壊れている場合は stderr へ警告を出す(cmd_491 AC1 — 検問(py_compile/pack_lint)をすり抜ける類の
壊れ方でも、運用者が気付けるようにする)。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}

# 差替の要: 既定パックは env で選べる(未設定は bokan=パック第1号)。トイパック差替証明・M6横展開の入口。
DEFAULT_PACK = os.environ.get("CASPER_PACK", "bokan")


def _load(pack=None):
    if pack is None:
        pack = DEFAULT_PACK
    if pack not in _CACHE:
        data = {}
        path = os.path.join(HERE, "pack", pack, "pack.yaml")
        try:
            import yaml
            data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        except Exception as e:
            data = {}
            print(f"⚠️  pack_config: pack[{pack}] の読取に失敗 ({path}): "
                  f"{type(e).__name__}: {e} — 既定(空)へ縮退", file=sys.stderr)
        _CACHE[pack] = data
    return _CACHE[pack]


def get(key, default=None, pack=None):
    v = _load(pack).get(key)
    return default if v is None else v


def aliases(pack=None):
    return get("aliases", {}, pack) or {}
