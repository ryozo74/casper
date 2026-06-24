#!/usr/bin/env python3
"""Casper → Vimeo アップロード(tus・再開可能)。studiobokan アカウントへ動画を上げ、
パスワード付き公開リンクを返す。トークンは .casper_vimeo_token(upload/edit/create スコープ)。"""
import json
import os
import urllib.request
import urllib.error

API = "https://api.vimeo.com"
ACCEPT = "application/vnd.vimeo.*+json;version=3.4"
HERE = os.path.dirname(os.path.abspath(__file__))


def _token():
    for fn in (os.path.join(HERE, ".casper_vimeo_token"),
               os.path.join(HERE, "CASPER_VIMEO_TOKEN.txt")):
        if os.path.exists(fn):
            for line in open(fn, encoding="utf-8"):
                s = line.strip()
                if "=" in s and s.split("=", 1)[0].upper().endswith("TOKEN"):
                    return s.split("=", 1)[1].strip().strip('"').strip("'")
                if s and not s.startswith("#"):
                    return s
    return os.environ.get("CASPER_VIMEO_TOKEN", "")


def _api(method, url, data=None, ctype="application/json", token=None):
    h = {"Authorization": f"bearer {token or _token()}", "Accept": ACCEPT}
    if data is not None:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read(), dict(r.headers)


def upload(file_path, name=None, description="", password=None, on_progress=None):
    """動画を tus でアップロード→{link, uri, password, name, size} を返す。
    password 指定時はパスワード付き公開(privacy view=password)に設定。"""
    size = os.path.getsize(file_path)
    name = name or os.path.basename(file_path)
    body = {"upload": {"approach": "tus", "size": str(size)},
            "name": name, "description": description}
    if password:
        body["privacy"] = {"view": "password"}
        body["password"] = str(password)
    # ① 動画リソース作成 + tus upload_link 取得
    st, raw, _ = _api("POST", f"{API}/me/videos", data=json.dumps(body).encode())
    v = json.loads(raw)
    upload_link = v["upload"]["upload_link"]
    uri, link = v.get("uri"), v.get("link")
    # ② tus PATCH でファイル本体を送信(64MBチャンク・再開可能)
    CHUNK = 64 * 1024 * 1024
    offset = 0
    with open(file_path, "rb") as f:
        while offset < size:
            f.seek(offset)
            chunk = f.read(CHUNK)
            req = urllib.request.Request(
                upload_link, data=chunk, method="PATCH",
                headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(offset),
                         "Content-Type": "application/offset+octet-stream"})
            with urllib.request.urlopen(req, timeout=1800) as r:
                offset = int(r.headers.get("Upload-Offset", offset + len(chunk)))
            if on_progress:
                on_progress(offset, size)
    return {"link": link, "uri": uri, "password": password, "name": name, "size": size}


def create_upload(size, name="upload.mp4", description="", password=None, token=None):
    """tus アップロードを作成し upload_link を返す(本体送信はブラウザが直接 PATCH→大容量対応)。"""
    body = {"upload": {"approach": "tus", "size": str(int(size))},
            "name": name, "description": description}
    if password:
        body["privacy"] = {"view": "password"}
        body["password"] = str(password)
    st, raw, _ = _api("POST", f"{API}/me/videos", data=json.dumps(body).encode(), token=token)
    v = json.loads(raw)
    uri = v.get("uri") or ""
    upl = v["upload"]["upload_link"]
    # パスワード/限定動画は hash 付きリンクでないと「存在しない」になる→player_embed_url から hash を取り共有リンク構築
    return {"upload_link": upl, "uri": uri, **_share_links(uri, fallback=v.get("link"), token=token)}


def _share_links(uri, fallback=None, token=None):
    """uri から hash 付き共有リンク・埋め込みURLを取得({link, embed})。"""
    vid = (uri or "").split("/")[-1]
    try:
        _, raw, _ = _api("GET", f"{API}{uri}?fields=link,player_embed_url", token=token)
        v = json.loads(raw)
        pe = v.get("player_embed_url") or ""
        h = pe.split("h=")[-1].split("&")[0] if "h=" in pe else ""
        link = f"https://vimeo.com/{vid}/{h}" if h else (v.get("link") or fallback)
        return {"link": link, "embed": pe}
    except Exception:
        return {"link": fallback or (f"https://vimeo.com/{vid}" if vid else None), "embed": ""}


def search(query, per_page=8, token=None):
    """studio bokan の Vimeo ライブラリ(全動画)を名前検索→[{name,link,id,uri,duration}]。"""
    import urllib.parse
    q = urllib.parse.urlencode({"query": query, "per_page": per_page,
                                "fields": "name,link,uri,duration,privacy.view,player_embed_url", "sort": "relevant"})
    st, raw, _ = _api("GET", f"{API}/me/videos?{q}", token=token)
    d = json.loads(raw)
    out = []
    for v in (d.get("data") or []):
        uri = v.get("uri") or ""
        vid = uri.split("/")[-1]
        pe = v.get("player_embed_url") or ""
        h = pe.split("h=")[-1].split("&")[0] if "h=" in pe else ""
        link = f"https://vimeo.com/{vid}/{h}" if h else v.get("link")    # 限定動画は hash 付きで
        out.append({"name": v.get("name"), "link": link, "embed": pe, "uri": uri,
                    "id": vid, "duration": v.get("duration"),
                    "privacy": (v.get("privacy") or {}).get("view")})
    return out


def set_password(video_id, password, token=None):
    """video_id(数値 or /videos/ID or URL) にパスワード付き公開を設定→{link,password,name}。"""
    s = str(video_id).strip().rstrip("/")
    vid = s.split("/")[-1] if "/" in s else s
    uri = f"/videos/{vid}"
    body = {"privacy": {"view": "password"}, "password": str(password)}
    st, raw, _ = _api("PATCH", f"{API}{uri}", data=json.dumps(body).encode(), token=token)
    v = json.loads(raw) if raw else {}
    sl = _share_links(uri, fallback=v.get("link"), token=token)
    return {"link": sl["link"], "embed": sl.get("embed"), "uri": uri,
            "password": str(password), "name": v.get("name")}


if __name__ == "__main__":
    import sys
    r = upload(sys.argv[1], name=(sys.argv[2] if len(sys.argv) > 2 else None),
               password=(sys.argv[3] if len(sys.argv) > 3 else None))
    print(json.dumps(r, ensure_ascii=False))
