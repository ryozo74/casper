#!/usr/bin/env python3
"""Casper secrets loader — 平文token退避(M2秘匿・2026-07-15)。

9p(H:\\ = Windows ドライブ)上のファイルは Unix 権限が効かず -rwxrwxrwx のまま=実質誰でも読める。
ゆえ機微値(JWT署名鍵/write/RO/Aurora token)は ext4 home の ~/.config/casper/secrets.env (0600) に置く。
プロセス起動時に load_into_env() を呼べば os.environ へ載り、各モジュールの "env優先" 読込がそのまま home 値を拾う
(chat_server/casper_tools/casper_aurora/casper_mcp は皆 os.environ.get(KEY) を第一優先で読む)。既存の
working-tree ファイル読込は fallback として温存(env が空の時のみ)。値の追加/更新は secrets.env のみを編集する。
"""
import os

HOME_SECRETS = os.path.join(os.path.expanduser("~"), ".config", "casper", "secrets.env")


def _parse(path=HOME_SECRETS):
    out = {}
    try:
        for ln in open(path, encoding="utf-8"):
            s = ln.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def load_into_env(path=HOME_SECRETS, override=False):
    """secrets.env の各KEYを os.environ に載せる(既存envは override=False なら尊重)。返り: 載せたキー数。
    起動時に1度呼ぶ想定。冪等。ファイル不在/読取不可でも例外を投げず 0 を返す。"""
    n = 0
    for k, v in _parse(path).items():
        if v and (override or not os.environ.get(k)):
            os.environ[k] = v
            n += 1
    return n


def get(key, default=""):
    """env優先→home secrets.env。単発参照用。"""
    return os.environ.get(key) or _parse().get(key, default)


if __name__ == "__main__":
    import stat
    ok = os.path.exists(HOME_SECRETS)
    mode = oct(stat.S_IMODE(os.stat(HOME_SECRETS).st_mode)) if ok else "-"
    print(f"secrets.env: {HOME_SECRETS} exists={ok} mode={mode} keys={list(_parse().keys())}")
