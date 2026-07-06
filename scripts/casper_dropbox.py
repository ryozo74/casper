#!/usr/bin/env python3
"""Casper → Dropbox 転送(Transfer) — ファイルを studio bokan の Business Dropbox へ上げ、
パスワード付き共有リンクを返す。Vimeoアップの兄弟(任意ファイル・大容量向け)。

token: .casper_dropbox_token(Business team token・容量/パスワードリンク可)。社内限・書込あり。
- 小(<=140MB): files/upload 一発。大: upload_session(start/append/finish・150MBチャンク)。
- 共有リンクは require_password 付き(Business口ゆえ可)。パスワード未指定は自動生成。
"""
import json
import os
import secrets
import string
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
FOLDER = "/Casper_Transfer"                                # 転送用フォルダ(制作データ庫と分離)
CHUNK = 140 * 1024 * 1024                                  # 140MB(単発上限150MB未満)


def _token():
    p = os.path.join(HERE, ".casper_dropbox_token")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def available():
    return bool(_token())


def _api(url, arg=None, body=None, data=None, ctype="application/json"):
    """Dropbox API 呼び。arg=Dropbox-API-Arg(content系)・body=JSON body(rpc系)・data=バイナリ。"""
    tok = _token()
    headers = {"Authorization": f"Bearer {tok}"}
    if arg is not None:
        headers["Dropbox-API-Arg"] = json.dumps(arg)
        headers["Content-Type"] = "application/octet-stream"
        payload = data or b""
    else:
        headers["Content-Type"] = ctype
        payload = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
        return r.status, (json.loads(raw) if raw and raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error_summary": raw.decode("utf-8", "replace")[:300]}


def _gen_password(n=8):
    aln = string.ascii_letters + string.digits
    return "".join(secrets.choice(aln) for _ in range(n))


def _upload_bytes(path, data):
    """path へ data(bytes)をアップ。大きければ upload_session。返り (ok, err)。"""
    if len(data) <= CHUNK:
        st, r = _api(CONTENT + "/files/upload", arg={"path": path, "mode": "overwrite", "autorename": False,
                                                     "mute": True}, data=data)
        return (st == 200, r if st != 200 else None)
    # upload_session(大容量)
    st, r = _api(CONTENT + "/files/upload_session/start", arg={"close": False}, data=data[:CHUNK])
    if st != 200:
        return False, r
    sid = r.get("session_id"); off = CHUNK
    while off < len(data):
        chunk = data[off:off + CHUNK]
        last = off + len(chunk) >= len(data)
        if last:
            st, r = _api(CONTENT + "/files/upload_session/finish",
                         arg={"cursor": {"session_id": sid, "offset": off},
                              "commit": {"path": path, "mode": "overwrite", "autorename": False, "mute": True}},
                         data=chunk)
        else:
            st, r = _api(CONTENT + "/files/upload_session/append_v2",
                         arg={"cursor": {"session_id": sid, "offset": off}, "close": False}, data=chunk)
        if st != 200:
            return False, r
        off += len(chunk)
    return True, None


def transfer(file_bytes, filename, password=None, direct_download=True):
    """ファイルを Dropbox へ上げてパスワード付き共有リンクを返す。
    返り: {ok, link, password, name, size, path} or {ok:False, error}。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定(.casper_dropbox_token)"}
    safe = "".join(c for c in (filename or "file") if c not in '\\/:*?"<>|').strip() or "file"
    path = f"{FOLDER}/{safe}"
    ok, err = _upload_bytes(path, file_bytes)
    if not ok:
        es = (err or {}).get("error_summary", str(err))[:200] if isinstance(err, dict) else str(err)[:200]
        return {"ok": False, "error": f"アップロード失敗: {es}"}
    pw = password or _gen_password()
    st, r = _api(API + "/sharing/create_shared_link_with_settings",
                 body={"path": path, "settings": {"require_password": True, "link_password": pw,
                                                   "audience": "public", "access": "viewer", "allow_download": True}})
    if st != 200:
        # 既にリンクがある場合は list_shared_links で拾う
        st2, r2 = _api(API + "/sharing/list_shared_links", body={"path": path, "direct_only": True})
        links = (r2 or {}).get("links", []) if st2 == 200 else []
        if not links:
            es = (r or {}).get("error_summary", str(r))[:200]
            return {"ok": False, "error": f"リンク作成失敗: {es}", "uploaded_path": path}
        r = links[0]
    url = r.get("url", "")
    if direct_download and url:
        url = url.replace("&dl=0", "&dl=1").replace("?dl=0", "?dl=1")
        if "dl=" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    return {"ok": True, "link": url, "password": pw, "name": safe, "size": len(file_bytes), "path": path}


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "account":
        st, r = _api(API + "/users/get_current_account", body={})
        print(r.get("name", {}).get("display_name"), "/", r.get("account_type"))
    else:
        print("casper_dropbox:", "token有" if available() else "token無", "/ folder", FOLDER)
