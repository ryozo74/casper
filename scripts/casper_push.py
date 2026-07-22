#!/usr/bin/env python3
"""Casper Web Push — 自前実装(M3 先回りの配信チャネル・2026-07-15)。

依存は cryptography のみ(pywebpush 不要)。VAPID(RFC 8292)＋ payload暗号化 aes128gcm(RFC 8291/8188) を
標準機能で実装し、ブラウザの PushManager 購読へ暗号化通知を飛ばす。Casperが閉じていても殿の端末へ届く。

構成:
  - VAPID鍵: ~/.config/casper/vapid.json (0600) に EC P-256 鍵を1度生成し永続。public_b64 が applicationServerKey。
  - 購読ストア: push_subs.json (uid -> [subscription,...])。subscription = {endpoint, keys:{p256dh, auth}}。
  - send(sub, payload): 1件へ暗号化POST。404/410 は購読失効ゆえ呼び側で除去。
"""
import base64
import json
import os
import time
import urllib.request

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization

HERE = os.path.dirname(os.path.abspath(__file__))
VAPID_FILE = os.path.join(os.path.expanduser("~"), ".config", "casper", "vapid.json")
SUBS_FILE = os.path.join(HERE, "push_subs.json")
try:                                                    # VAPID subject(連絡先)は配備固有ゆえ env→pack/config から。
    import pack_config as _pc
    VAPID_SUB = os.environ.get("CASPER_VAPID_SUB") or _pc.get("vapid_sub", "mailto:casper@example.com")
except Exception:
    VAPID_SUB = os.environ.get("CASPER_VAPID_SUB", "mailto:casper@example.com")  # Apple push は .local 等を弾く→実ドメインmailto


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s):
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode() if isinstance(s, str) else s)


# ── VAPID鍵(1度生成→永続) ──────────────────────────────────────────────
def _load_or_make_vapid():
    try:
        d = json.load(open(VAPID_FILE, encoding="utf-8"))
        priv = ec.derive_private_key(int.from_bytes(_unb64u(d["private"]), "big"), ec.SECP256R1())
        return priv, d["public"]
    except Exception:
        pass
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
    val = priv.private_numbers().private_value.to_bytes(32, "big")
    d = {"private": _b64u(val), "public": _b64u(pub)}
    os.makedirs(os.path.dirname(VAPID_FILE), exist_ok=True)
    try:
        os.chmod(os.path.dirname(VAPID_FILE), 0o700)
    except Exception:
        pass
    json.dump(d, open(VAPID_FILE, "w", encoding="utf-8"))
    try:
        os.chmod(VAPID_FILE, 0o600)
    except Exception:
        pass
    return priv, d["public"]


def vapid_public_b64():
    """ブラウザ PushManager.subscribe({applicationServerKey}) に渡す VAPID 公開鍵(base64url)。"""
    return _load_or_make_vapid()[1]


def _vapid_header(endpoint):
    """endpoint の origin を aud とする VAPID JWT を作り Authorization ヘッダ値を返す。"""
    import jwt as _jwt
    from urllib.parse import urlsplit
    priv, pub = _load_or_make_vapid()
    u = urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    token = _jwt.encode({"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUB},
                        priv, algorithm="ES256")
    return f"vapid t={token}, k={pub}"


# ── RFC 8291 aes128gcm ペイロード暗号化 ────────────────────────────────
def _encrypt(payload, p256dh_b64, auth_b64):
    ua_pub_bytes = _unb64u(p256dh_b64)                      # クライアント公開鍵(65B uncompressed)
    auth_secret = _unb64u(auth_b64)                        # 16B
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    as_priv = ec.generate_private_key(ec.SECP256R1())      # サーバ側 ephemeral
    as_pub_bytes = as_priv.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_pub)
    salt = os.urandom(16)

    # IKM = HKDF(salt=auth_secret, ikm=shared, info="WebPush: info"||0x00||ua_pub||as_pub, L=32)
    key_info = b"WebPush: info\x00" + ua_pub_bytes + as_pub_bytes
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(shared)
    # CEK / NONCE (RFC 8188, salt=ランダムsalt)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)

    data = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, ensure_ascii=False).encode()
    plaintext = bytes(data) + b"\x02"                       # 単一レコードの区切り 0x02
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext, None)

    rs = 4096
    header = salt + rs.to_bytes(4, "big") + bytes([len(as_pub_bytes)]) + as_pub_bytes
    return header + ciphertext


def send(sub, payload, ttl=86400):
    """1購読へ暗号化Web Pushを送る。返り=(ok, status)。410/404 は購読失効(呼び側で除去せよ)。"""
    keys = (sub or {}).get("keys") or {}
    endpoint = (sub or {}).get("endpoint")
    if not (endpoint and keys.get("p256dh") and keys.get("auth")):
        return (False, 0)
    body = _encrypt(payload, keys["p256dh"], keys["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Authorization": _vapid_header(endpoint),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (True, r.status)
    except urllib.error.HTTPError as e:
        return (False, e.code)
    except Exception:
        return (False, 0)


# ── 購読ストア ─────────────────────────────────────────────────────────
def _load_subs():
    try:
        return json.load(open(SUBS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_subs(d):
    try:
        json.dump(d, open(SUBS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def add_sub(uid, sub):
    """uid の購読を登録(endpoint重複は上書き)。"""
    d = _load_subs()
    lst = [s for s in d.get(str(uid), []) if s.get("endpoint") != sub.get("endpoint")]
    lst.append(sub)
    d[str(uid)] = lst
    _save_subs(d)
    return len(lst)


def subs(uid):
    return _load_subs().get(str(uid), [])


def subscribed_uids():
    """push購読が1件以上あるuidの一覧(通知ループが"誰に配るか"の動的ソース=全ユーザー対応)。"""
    return [k for k, v in _load_subs().items() if v]


# ── 通知の型別 ON/OFF(ユーザー設定・既定=全ON) ────────────────────────
PREFS_FILE = os.path.join(HERE, "notify_prefs.json")
NOTIFY_TYPES = ["morning_brief", "new_overdue", "stalled_fb", "dm", "open_loop"]


def get_prefs(uid):
    """uid の型別通知設定(未設定は全ON)。"""
    try:
        p = json.load(open(PREFS_FILE, encoding="utf-8")).get(str(uid), {})
    except Exception:
        p = {}
    return {t: bool(p.get(t, True)) for t in NOTIFY_TYPES}


def set_prefs(uid, prefs):
    try:
        d = json.load(open(PREFS_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    cur = d.get(str(uid), {})
    for t in NOTIFY_TYPES:
        if isinstance(prefs, dict) and t in prefs:
            cur[t] = bool(prefs[t])
    d[str(uid)] = cur
    try:
        json.dump(d, open(PREFS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return get_prefs(uid)


def type_enabled(uid, ntype):
    """uid が ntype の通知を受け取る設定か(既定True)。ntype未知は素通し(True)。"""
    if ntype not in NOTIFY_TYPES:
        return True
    return get_prefs(uid).get(ntype, True)


def remove_sub(uid, endpoint):
    d = _load_subs()
    lst = [s for s in d.get(str(uid), []) if s.get("endpoint") != endpoint]
    d[str(uid)] = lst
    _save_subs(d)


def push_to_uid(uid, payload):
    """uid の全購読へ送信。失効(404/410)は自動除去。返り={sent,failed,removed}。"""
    sent = failed = removed = 0
    for s in list(subs(uid)):
        ok, st = send(s, payload)
        if ok:
            sent += 1
        else:
            failed += 1
            if st in (404, 410):
                remove_sub(uid, s.get("endpoint"))
                removed += 1
    return {"sent": sent, "failed": failed, "removed": removed}


if __name__ == "__main__":
    priv, pub = _load_or_make_vapid()
    print("VAPID public (applicationServerKey):", pub)
    print("subs file:", SUBS_FILE, "登録uid:", list(_load_subs().keys()))
