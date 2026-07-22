#!/usr/bin/env python3
"""パック/vault パスの単一解決点 — M5 パック差替の要(Fable検証ゲート step3 の下地)。

engine コードは vault を直参照(os.path.join(HERE,"..","vault"))せず、ここを import する。
CASPER_VAULT / CASPER_PACK env で丸ごと差替可能(未設定は bokan=パック第1号)。
これで「移植=再著述でなくパック差替」を機械証明する土台が整う(トイパック差替→golden緑+diff空)。

  VAULT     … 右脳(知識/facts/人格)の root。既定 = scripts/../vault
  PACK      … 現在のパック名(pack_config.DEFAULT_PACK と同源)
  PACK_DIR  … pack/<PACK>/ の絶対パス
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

PACK = os.environ.get("CASPER_PACK", "bokan")
VAULT = os.path.abspath(os.environ.get("CASPER_VAULT", os.path.join(HERE, "..", "vault")))
PACK_DIR = os.path.join(HERE, "pack", PACK)


def vault(*parts):
    """VAULT 配下のパスを組む。engine は os.path.join(HERE,'..','vault',...) の代わりにこれを使う。"""
    return os.path.join(VAULT, *parts)
