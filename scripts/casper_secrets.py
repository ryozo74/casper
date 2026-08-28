#!/usr/bin/env python3
"""Casper secrets loader — 平文token退避(M2秘匿・2026-07-15)。

9p(H:\\ = Windows ドライブ)上のファイルは Unix 権限が効かず -rwxrwxrwx のまま=実質誰でも読める。
ゆえ機微値(JWT署名鍵/write/RO/Aurora token)は ext4 home の ~/.config/casper/secrets.env (0600) に置く。
プロセス起動時に load_into_env() を呼べば os.environ へ載り、各モジュールの "env優先" 読込がそのまま home 値を拾う
(chat_server/casper_tools/casper_aurora/casper_mcp は皆 os.environ.get(KEY) を第一優先で読む)。既存の
working-tree ファイル読込は fallback として温存(env が空の時のみ)。値の追加/更新は secrets.env のみを編集する。
"""
import hmac
import os
import secrets as _pysecrets

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


def _append(path, key, value):
    """secrets.env へ KEY=VALUE を追記する(親ディレクトリ 0700 / ファイル 0600 を保つ)。既存キーには触れぬ。"""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write("%s=%s\n" % (key, value))
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def host_secret(path=HOME_SECRETS):
    """内部機構(gate/eval/自律投稿)だけが名乗れる合鍵。**無ければ機構が自ら作る**。

    【殿御下命2026-08-29・丙】loopback を無条件に信じる造りを廃した。ゆえ本鍵が唯一の内部経路になる。
    ★鍵が不在の時に「内部経路も消える」では、ハーネスが黙って匿名へ落ち、
      それは *失敗とゼロを同じ出口へ流す* こと([[feedback_casper_fix_iron_rules]])に他ならぬ。
      ゆえ不在なら 0600 で生成して secrets.env へ永続化し、以後 server も harness も同じ値を読む。
    """
    v = (os.environ.get("CASPER_HOST_SECRET") or _parse(path).get("CASPER_HOST_SECRET") or "").strip()
    if not v:
        v = _pysecrets.token_hex(32)
        _append(path, "CASPER_HOST_SECRET", v)
    os.environ["CASPER_HOST_SECRET"] = v
    return v


def host_secret_matches(presented, path=HOME_SECRETS):
    """提示された合鍵が正しいか。定数時間で比べる。
    ★空/未提示は常に偽——**鍵を持たぬ者が内部機構を名乗る道は無い**(loopback であろうと)。"""
    p = (presented or "").strip()
    if not p:
        return False
    try:
        return hmac.compare_digest(p, host_secret(path))
    except Exception:
        return False


def get(key, default=""):
    """env優先→home secrets.env。単発参照用。"""
    return os.environ.get(key) or _parse().get(key, default)


if __name__ == "__main__":
    import stat
    ok = os.path.exists(HOME_SECRETS)
    mode = oct(stat.S_IMODE(os.stat(HOME_SECRETS).st_mode)) if ok else "-"
    print(f"secrets.env: {HOME_SECRETS} exists={ok} mode={mode} keys={list(_parse().keys())}")
    print(f"host_secret: len={len(host_secret())} (無ければ生成し 0600 で永続化)")
