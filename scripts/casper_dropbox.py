#!/usr/bin/env python3
"""Casper → Dropbox 転送(Transfer) — ファイルを自社の Business Dropbox へ上げ、
パスワード付き共有リンクを返す。Vimeoアップの兄弟(任意ファイル・大容量向け)。

token: .casper_dropbox_token(Business team token・容量/パスワードリンク可)。社内限・書込あり。
- 小(<=140MB): files/upload 一発。大: upload_session(start/append/finish・150MBチャンク)。
- 共有リンクは require_password 付き(Business口ゆえ可)。パスワード未指定は自動生成。
"""
import json
import os
import secrets
import string
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))   # multi-agent-shogun-main/
REPORTS_DIR = os.path.join(HERE, "reports")
STATE_PATH = os.path.join(REPORTS_DIR, "_state.json")          # run_observation.py と共有(キーで名前空間分離)
INBOX_WRITE = os.path.join(ROOT, "scripts", "inbox_write.sh")
API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
FOLDER = "/Casper_Transfer"                                # 転送用フォルダ(制作データ庫と分離)
CHUNK = 140 * 1024 * 1024                                  # 140MB(単発上限150MB未満)
_SPACE_CACHE_TTL = 300                                     # 容量照会キャッシュ(5分・cmd_513手当2)


def _token():
    p = os.path.join(HERE, ".casper_dropbox_token")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def available():
    return bool(_token())


_HOME = {"path": None}                                     # home_path のキャッシュ(1プロセス1回だけ問う)


def home_prefix():
    """team アカウントの『メンバーフォルダ』への接頭辞を返す(個人口なら "")。

    【なぜ要るか】Business team token の既定 root は **team space の直下**で、殿のメンバーフォルダ
    (自社ルート配下)の外にござる。そこへ置いたファイルの共有リンクは、社外の相手にサインインを
    求めることがある(殿御指摘2026-07-29「アカウントがないとダウンロードできない」)。
    ゆえ転送物はメンバーフォルダ配下へ置く=通常の個人共有と同じ振る舞いにする。
    root_info: root_namespace_id == home_namespace_id なら team space でない=接頭辞不要。"""
    if _HOME["path"] is not None:
        return _HOME["path"]
    _HOME["path"] = ""
    try:
        st, r = _api(API + "/users/get_current_account", body={})
        ri = (r or {}).get("root_info") or {} if st == 200 else {}
        if str(ri.get("root_namespace_id")) != str(ri.get("home_namespace_id")):
            hp = str(ri.get("home_path") or "").rstrip("/")
            if hp.startswith("/"):
                _HOME["path"] = hp
    except Exception:
        pass
    return _HOME["path"]


def base_folder():
    """転送用フォルダの実パス(メンバーフォルダ配下)。"""
    return home_prefix() + FOLDER


def _api(url, arg=None, body=None, data=None, ctype="application/json"):
    """Dropbox API 呼び。arg=Dropbox-API-Arg(content系)・body=JSON body(rpc系)・data=バイナリ。"""
    tok = _token()
    headers = {"Authorization": f"Bearer {tok}"}
    if arg is not None:
        # Dropbox-API-Arg は HTTPヘッダ=ASCIIのみ可。日本語等は必ず \uXXXX にエスケープ(ensure_ascii=True明示)。
        headers["Dropbox-API-Arg"] = json.dumps(arg, ensure_ascii=True)
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


def _dl1(url):
    """【dl=1 は必須】アカウント無しで落とせるかは、この一文字に懸かっておる。実測(2026-07-29・
    未ログインの実ブラウザでパスワード投入まで通した結果):
      ・dl=1 → パスワード通過と同時に**ダウンロードが始まる**(アカウント不要) ○
      ・dl=0 → プレビュー頁に着くだけ。Downloadボタンは在るが匿名では落ちず「Log in / Sign up」のみ ×
    殿御指摘2026-07-29「アカウントがないとダウンロードできない」の正体はこれ。
    フォルダのリンクでも dl=1 は zip 一括ダウンロードとして効く。"""
    if not url:
        return url
    url = url.replace("&dl=0", "&dl=1").replace("?dl=0", "?dl=1")
    if "dl=" not in url:
        url += ("&" if "?" in url else "?") + "dl=1"
    return url


def _gen_password(n=8):
    aln = string.ascii_letters + string.digits
    return "".join(secrets.choice(aln) for _ in range(n))


def _fmt_bytes(n):
    """人が読める形へ(TiB/GiB/MiB)。cmd_513手当2: 数字は機構が取った生値をそのまま貼る——
    ここは表示の単位変換のみで、値そのものの捏造・丸めすぎはしない(小数1桁まで)。"""
    if n is None:
        return "不明"
    n = float(n)
    for unit, div in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3), ("MiB", 1024 ** 2)):
        if n >= div:
            return f"{n / div:.2f}{unit}"
    return f"{n:.0f}B"


_SPACE_CACHE = {"ts": 0.0, "data": None}                   # プロセス内キャッシュ(取得時刻も保持)


def get_space_usage(force=False):
    """team の容量(上限/使用量)を機構が取りに行く(cmd_513手当2: users/get_space_usage)。
    返り {ok, used, allocated, over, checked_at} or {ok:False, error}。
    ★数字はqwenに書かせず、ここで取った生値をそのまま呼び出し側が貼る(接地の原則)。
    ★短時間キャッシュあり(_SPACE_CACHE_TTL秒)。古い値を新しい値のように見せぬため checked_at を必ず添える。"""
    now = time.time()
    if not force and _SPACE_CACHE["data"] is not None and (now - _SPACE_CACHE["ts"]) < _SPACE_CACHE_TTL:
        return _SPACE_CACHE["data"]
    if not _token():
        return {"ok": False, "error": "token未設定"}
    try:
        st, r = _api(API + "/users/get_space_usage", body={})
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if st != 200 or not isinstance(r, dict):
        es = (r or {}).get("error_summary", str(r))[:200] if isinstance(r, dict) else str(r)[:200]
        return {"ok": False, "error": es}
    alloc_obj = r.get("allocation") or {}
    # ★team口(Business)は容量が団体プール共有ゆえ、判定すべきは団体全体の allocation.used/allocated であり、
    # トップレベルの used(呼出メンバー個人の消費量)ではない(実測2026-08-19: トップレベルused=44.7TiBだが
    # 団体超過はallocation.used=46.2TiB側でのみ再現・将軍実測42.00/42.03TiBと一致するのはallocation側)。
    if alloc_obj.get(".tag") == "team":
        used, alloc = alloc_obj.get("used"), alloc_obj.get("allocated")
    else:                                                    # 個人枠等・team以外の形
        used, alloc = r.get("used"), alloc_obj.get("allocated")
    if used is None or alloc is None:
        return {"ok": False, "error": "get_space_usage応答に used/allocated が無い"}
    data = {"ok": True, "used": used, "allocated": alloc, "over": max(0, used - alloc),
             "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _SPACE_CACHE["ts"], _SPACE_CACHE["data"] = now, data
    return data


def _space_usage_note():
    """失敗メッセージに載せる『上限/使用量/超過分(取得時刻)』の一文。照会自体が失敗したら
    数字を捏造せず正直に『容量照会できず』と名乗る(手当2★の掟)。"""
    su = get_space_usage()
    if not su.get("ok"):
        return f"(容量照会できず: {su.get('error', '不明')})"
    return (f"(上限 {_fmt_bytes(su['allocated'])} / 使用 {_fmt_bytes(su['used'])} / "
            f"超過 {_fmt_bytes(su['over'])} ・{su['checked_at']}時点)")


def _is_insufficient_space(err):
    """APIエラーが容量超過(insufficient_space)かを判定する単一機構(4箇所から呼ぶ)。"""
    if isinstance(err, dict):
        es = err.get("error_summary", "") or json.dumps(err, ensure_ascii=False)
    else:
        es = str(err or "")
    return "insufficient_space" in es


def _classify_upload_error(err):
    """失敗理由を人の言葉へ翻訳する単一の出口。容量超過は専用メッセージ+実測値、
    それ以外は現状通りの『アップロード失敗: <APIの生文字列>』のまま(手当1★: 他の失敗理由と
    混ぜない/生の文字列は付記として残す=診断のため後から読めることに意味がある)。"""
    es = (err or {}).get("error_summary", str(err))[:200] if isinstance(err, dict) else str(err)[:200]
    if _is_insufficient_space(err):
        _notify_space_alert_once(es)
        return f"Dropboxの容量が上限を超えており申した{_space_usage_note()} [詳細: {es}]"
    return f"アップロード失敗: {es}"


def _load_state():
    try:
        return json.loads(open(STATE_PATH, encoding="utf-8").read())
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def _notify_space_alert_once(raw_error):
    """手当3(将軍追加): 同一理由(容量超過)の連続失敗を一度だけkaroへ報せる。
    ★既存の赤の届け先(cmd_512でashigaru1が構築したinbox_write.sh経由のkaro通知)へ相乗り
    (新しい通知経路を作らない)。★毎回は吠えぬ——_state.jsonにフラグを立て、次に容量超過が
    解消してから再発した時のみ再度鳴らす(cmd_512瑕疵Dの教訓: 赤が日常になれば読まれなくなる)。"""
    state = _load_state()
    if state.get("dropbox_space_alert_active"):
        return                                              # 既に報せ済(未解消)ゆえ今回は黙る
    state["dropbox_space_alert_active"] = True
    state["dropbox_space_alert_first_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["dropbox_space_alert_last_error"] = raw_error
    _save_state(state)
    msg = f"Dropbox容量超過でアップロード失敗中{_space_usage_note()} [{raw_error}]"
    try:
        subprocess.run(["bash", INBOX_WRITE, "karo", msg, "observation_red", "casper_dropbox"], check=False)
    except Exception:
        pass


def _clear_space_alert():
    """容量が空いて成功に戻った時、次回また超過したら再び報せられるようフラグを下ろす。"""
    state = _load_state()
    if state.get("dropbox_space_alert_active"):
        state["dropbox_space_alert_active"] = False
        state["dropbox_space_alert_cleared_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_state(state)


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


def upload_stream(path, fh, total):
    """file-like から読みながらアップ(全体をメモリに載せぬ)。返り (ok, err)。

    【なぜ要るか】従来は「ブラウザがファイル全体を base64 文字列にする → サーバが全体を bytes で持つ」
    造りで、大きなファイルはブラウザ側の FileReader が落ちた(殿御指摘2026-07-29『転送エラー: 読込失敗』)。
    base64 は容量が1.33倍に膨れ、文字列の上限にも当たる。生バイトを chunk ごとに流せば、
    ブラウザもサーバも全体を抱えずに済む。"""
    if total <= CHUNK:
        return _upload_bytes(path, fh.read(total))
    first = fh.read(CHUNK)
    st, r = _api(CONTENT + "/files/upload_session/start", arg={"close": False}, data=first)
    if st != 200:
        return False, r
    sid = r.get("session_id")
    off = len(first)
    while off < total:
        chunk = fh.read(min(CHUNK, total - off))
        if not chunk:
            return False, {"error_summary": f"入力が途切れ申した(受領 {off}/{total} バイト)"}
        last = off + len(chunk) >= total
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


def verify_gated(url, timeout=20):
    """【毎回、匿名で確かめる】そのリンクが「パスワードを要する状態」になっておるかを、
    認証もcookieも持たぬ素の要求で検査する。返り True=パスワード頁で止まる(正常) / False=素通り。

    殿御疑義2026-07-29「パスワード無くてもダウンロードできるよ？」——実測すると、殿の環境では
    Dropboxに**所有者として署名済**ゆえ、権限がリンクのパスワードに優先して素通りになる(Dropboxの仕様)。
    社外の相手(未署名)は password 頁で止まる。ゆえ『効いているか』は匿名でしか確かめられぬ。
    人の目で確かめられぬものは、機構が毎回確かめて申告する。"""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "casper-link-check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            head = r.read(200000)
            ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype:
            return False                                   # 実体が返った=素通り(パスワードが効いておらぬ)
        low = head.decode("utf-8", "replace").lower()
        return ("password required" in low) or ("/sm/password" in low) or ("パスワード" in low)
    except Exception:
        return None                                        # 判定不能(通信不可等)は False と混ぜぬ


def _link_for(path, pw):
    """path にパスワード付き公開リンクを作る/直す(単一ファイル・フォルダ共通の単一機構)。返り link情報 or None。"""
    settings = {"require_password": True, "link_password": pw,
                "audience": "public", "access": "viewer", "allow_download": True}
    st, r = _api(API + "/sharing/create_shared_link_with_settings", body={"path": path, "settings": settings})
    if st == 200:
        return r
    st2, r2 = _api(API + "/sharing/list_shared_links", body={"path": path, "direct_only": True})
    links = (r2 or {}).get("links", []) if st2 == 200 else []
    if not links:
        return None
    # 既存リンクには"実際にパスワードを設定し直す"(生成pwと実リンクの食い違いを断つ)。
    stm, rm = _api(API + "/sharing/modify_shared_link_settings",
                   body={"url": links[0].get("url", ""), "settings": settings})
    return rm if (stm == 200 and (rm or {}).get("url")) else links[0]


def transfer_stream(fh, total, filename, password=None, direct_download=True):
    """生バイト(file-like)を受けて Dropbox へ流し、パスワード付きリンクを返す。base64 を経由せぬ正路。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定(.casper_dropbox_token)"}
    safe = _safe(filename, "file")
    path = f"{base_folder()}/{safe}"
    ok, err = upload_stream(path, fh, total)
    if not ok:
        return {"ok": False, "error": _classify_upload_error(err)}
    _clear_space_alert()
    pw = password or _gen_password()
    r = _link_for(path, pw)
    if not r:
        return {"ok": False, "error": "リンク作成失敗", "uploaded_path": path}
    url = _dl1(r.get("url", "")) if direct_download else r.get("url", "")
    return {"ok": True, "link": url, "password": pw, "name": safe, "size": total, "path": path,
            "gated": verify_gated(url),                    # 匿名でパスワードが要る状態かを機構が毎回検査
            "audience": ((r.get("link_permissions") or {}).get("effective_audience") or {}).get(".tag"),
            "visibility": ((r.get("link_permissions") or {}).get("resolved_visibility") or {}).get(".tag")}


def _safe_rel(rel, fallback="file"):
    """相対パスを段ごとに清める(フォルダ投函で階層を保つため。'..' や空段は落とす)。"""
    segs = [s for s in str(rel or "").replace("\\", "/").split("/") if s and s not in (".", "..")]
    segs = [_safe(s, "") for s in segs]
    segs = [s for s in segs if s]
    return "/".join(segs) or fallback


def upload_into_stream(folder, fh, total, filename):
    """まとめ用: /base/<folder>/<相対パス> へ流し込む(リンクは作らぬ)。
    filename に 'sub/dir/a.png' の形が来れば階層を保つ(フォルダごと投函された時の構造を壊さぬ)。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定"}
    path = f"{base_folder()}/{_safe(folder, 'batch')}/{_safe_rel(filename)}"
    ok, err = upload_stream(path, fh, total)
    if not ok:
        return {"ok": False, "error": _classify_upload_error(err)}
    _clear_space_alert()
    return {"ok": True, "path": path, "size": total}


def transfer(file_bytes, filename, password=None, direct_download=True):
    """ファイルを Dropbox へ上げてパスワード付き共有リンクを返す。
    返り: {ok, link, password, name, size, path} or {ok:False, error}。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定(.casper_dropbox_token)"}
    safe = "".join(c for c in (filename or "file") if c not in '\\/:*?"<>|').strip() or "file"
    path = f"{base_folder()}/{safe}"
    ok, err = _upload_bytes(path, file_bytes)
    if not ok:
        return {"ok": False, "error": _classify_upload_error(err)}
    _clear_space_alert()
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
        # 【重要】既存リンクに"実際にパスワードを設定し直す"。怠ると生成pwが実リンクと食い違い
        # 「表示/DMのパスワードが違う(開けない)」バグになる(同一ファイル再アップ時に発生・殿指摘2026-07-13)。
        stm, rm = _api(API + "/sharing/modify_shared_link_settings",
                       body={"url": r.get("url", ""),
                             "settings": {"require_password": True, "link_password": pw,
                                          "audience": "public", "access": "viewer", "allow_download": True}})
        if stm == 200 and (rm or {}).get("url"):
            r = rm                                   # PW設定済みの最新リンク情報で上書き(pw と実リンクが一致)
    url = _dl1(r.get("url", "")) if direct_download else r.get("url", "")
    return {"ok": True, "link": url, "password": pw, "name": safe, "size": len(file_bytes), "path": path,
            "gated": verify_gated(url),
            "audience": ((r.get("link_permissions") or {}).get("effective_audience") or {}).get(".tag"),
            "visibility": ((r.get("link_permissions") or {}).get("resolved_visibility") or {}).get(".tag")}


def _safe(name, fallback):
    return "".join(c for c in (name or "") if c not in '\\/:*?"<>|').strip() or fallback


def upload_into(folder, file_bytes, filename):
    """/Casper_Transfer/<folder>/<filename> へアップ(リンクは作らない=バッチ用)。返り {ok, path, size} or {ok:False,error}。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定(.casper_dropbox_token)"}
    path = f"{base_folder()}/{_safe(folder, 'batch')}/{_safe(filename, 'file')}"
    ok, err = _upload_bytes(path, file_bytes)
    if not ok:
        return {"ok": False, "error": _classify_upload_error(err)}
    _clear_space_alert()
    return {"ok": True, "path": path, "size": len(file_bytes)}


def share_folder(folder, password=None):
    """/Casper_Transfer/<folder> にパスワード付き共有リンクを1つ作る(複数ファイルを1リンクで共有)。
    返り {ok, link, password, folder} or {ok:False,error}。"""
    if not _token():
        return {"ok": False, "error": "Dropbox token 未設定"}
    path = f"{base_folder()}/{_safe(folder, 'batch')}"
    pw = password or _gen_password()
    st, r = _api(API + "/sharing/create_shared_link_with_settings",
                 body={"path": path, "settings": {"require_password": True, "link_password": pw,
                                                   "audience": "public", "access": "viewer", "allow_download": True}})
    if st != 200:
        st2, r2 = _api(API + "/sharing/list_shared_links", body={"path": path, "direct_only": True})
        links = (r2 or {}).get("links", []) if st2 == 200 else []
        if not links:
            es = (r or {}).get("error_summary", str(r))[:200]
            return {"ok": False, "error": f"フォルダ共有リンク作成失敗: {es}"}
        r = links[0]
        # 単一ファイル側と同じく、既存リンクにパスワードを設定し直す(生成pwと実リンクの食い違いを断つ)。
        stm, rm = _api(API + "/sharing/modify_shared_link_settings",
                       body={"url": r.get("url", ""),
                             "settings": {"require_password": True, "link_password": pw,
                                          "audience": "public", "access": "viewer", "allow_download": True}})
        if stm == 200 and (rm or {}).get("url"):
            r = rm
    # 【まとめリンクにも dl=1】此処が付いておらなんだ。複数ファイルを1リンクで送る経路(chat.html の
    # dropboxTransferFiles 複数分岐)は dl=0 のまま渡しており、相手はプレビュー頁で止まり
    # 「アカウントが無いと落とせぬ」に至る。フォルダは dl=1 で zip 一括ダウンロードになる。
    _u = _dl1(r.get("url", ""))
    return {"ok": True, "link": _u, "password": pw, "folder": path,
            "gated": verify_gated(_u),
            "audience": ((r.get("link_permissions") or {}).get("effective_audience") or {}).get(".tag"),
            "visibility": ((r.get("link_permissions") or {}).get("resolved_visibility") or {}).get(".tag")}


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "account":
        st, r = _api(API + "/users/get_current_account", body={})
        print(r.get("name", {}).get("display_name"), "/", r.get("account_type"))
    else:
        print("casper_dropbox:", "token有" if available() else "token無", "/ folder", base_folder())
