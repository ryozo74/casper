#!/usr/bin/env python3
"""パック設定の読取 — M5 seam。engine は「規則の雛形」だけを持ち、bokan 固有の
値(別名・固有名リスト・連絡先等)は pack/<name>/pack.yaml から差し込む(Fable処方)。

これは digest 注入経路ではなく静的 config 値の参照ゆえ、二重 digest スタックには当たらぬ。
yaml.safe_load は既存(casper_authority)と同方式。ファイル不在/壊れでも例外を投げず既定へ縮退。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}

# 差替の要: 既定パックは env で選べる(未設定は bokan=パック第1号)。トイパック差替証明・M6横展開の入口。
DEFAULT_PACK = os.environ.get("CASPER_PACK", "bokan")


def _load(pack=None):
    if pack is None:
        pack = DEFAULT_PACK
    if pack not in _CACHE:
        data = {}
        try:
            import yaml
            path = os.path.join(HERE, "pack", pack, "pack.yaml")
            data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        except Exception:
            data = {}
        _CACHE[pack] = data
    return _CACHE[pack]


def get(key, default=None, pack=None):
    v = _load(pack).get(key)
    return default if v is None else v


def aliases(pack=None):
    return get("aliases", {}, pack) or {}
