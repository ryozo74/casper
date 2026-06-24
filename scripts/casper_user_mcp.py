#!/usr/bin/env python3
"""ユーザー別 個人MCP レジストリ。
各ユーザーが自分の外部ツール(個人の Slack / Drive / Notion 等の MCP サーバ)を登録し、
**本人のトークンで本人の権限**として Casper 経由で使えるようにする。

設計:
- 保存: scripts/.casper_user_mcp.json  (uid -> [ {name,url,transport,token,enabled}, ... ])
  ファイル権限 0600。git-ignore 必須(個人トークンを含む)。
- ツールは衝突回避のため「<server>__<tool>」へ名前空間化して qwen に渡す。
- call() は名前空間を割って該当サーバへ casper_mcp 経由でルーティング(本人 token・本人 uid を actor)。

セキュリティ要点:
- uid は呼び出し側(chat_server.identify)が JWT 検証済の値のみ渡すこと。なりすまし時に他人のMCPを触らせない。
- 個人の外部 SaaS を叩く=社外送信。PII egress 方針(ローカルLLMガード)との整合は呼び出し側で判断。
"""
import json
import os
import re

import casper_mcp

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, ".casper_user_mcp.json")
SEP = "__"   # 名前空間区切り


def _load():
    if not os.path.exists(STORE):
        return {}
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE)
    try:
        os.chmod(STORE, 0o600)          # 個人トークン保護(ファイル権限)
    except Exception:
        pass


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]", "", name)[:24] or "srv"


def raw_servers(uid):
    """内部用: token 込みのサーバ定義リスト。"""
    if not uid:
        return []
    return _load().get(str(uid), [])


def servers(uid):
    """表示用: token をマスクしたサーバ定義リスト。"""
    out = []
    for s in raw_servers(uid):
        out.append({"name": s.get("name"), "url": s.get("url"),
                    "transport": s.get("transport", "http"),
                    "enabled": s.get("enabled", True),
                    "has_token": bool(s.get("token"))})
    return out


def add(uid, name, url, token="", transport="http"):
    """サーバを追加/更新(同名は上書き)。"""
    if not (uid and name and url):
        return {"error": "uid/name/url は必須"}
    data = _load()
    lst = data.get(str(uid), [])
    lst = [s for s in lst if s.get("name") != name]      # 同名置換
    lst.append({"name": name, "url": url, "token": token or "",
                "transport": transport or "http", "enabled": True})
    data[str(uid)] = lst
    _save(data)
    return {"ok": True, "servers": servers(uid)}


def remove(uid, name):
    data = _load()
    lst = [s for s in data.get(str(uid), []) if s.get("name") != name]
    data[str(uid)] = lst
    _save(data)
    return {"ok": True, "servers": servers(uid)}


def set_enabled(uid, name, on):
    data = _load()
    for s in data.get(str(uid), []):
        if s.get("name") == name:
            s["enabled"] = bool(on)
    _save(data)
    return {"ok": True, "servers": servers(uid)}


def tools(uid):
    """有効サーバの tools/list を「<server>__<tool>」へ名前空間化して合成。失敗サーバは黙ってスキップ。"""
    out = []
    for s in raw_servers(uid):
        if not s.get("enabled", True):
            continue
        try:
            ts = casper_mcp.list_tools(token=s.get("token") or None, url=s.get("url"))
        except Exception:
            ts = []
        pfx = _slug(s.get("name", "srv"))
        for t in ts:
            fn = dict(t["function"])
            fn["name"] = f"{pfx}{SEP}{fn['name']}"
            fn["description"] = f"[{s.get('name')}] " + (fn.get("description") or "")
            out.append({"type": "function", "function": fn})
    return out


def names(uid):
    """この uid の名前空間化ツール名の集合(chat_server のディスパッチ判定用)。"""
    return {t["function"]["name"] for t in tools(uid)}


def call(uid, fn, args, actor=None):
    """名前空間化ツール名を割って該当サーバへルーティング実行。"""
    if SEP not in fn:
        return "(個人MCP: 名前空間不正)"
    pfx, tool = fn.split(SEP, 1)
    for s in raw_servers(uid):
        if not s.get("enabled", True):
            continue
        if _slug(s.get("name", "srv")) == pfx:
            return casper_mcp.call_tool(tool, args, token=s.get("token") or None,
                                        url=s.get("url"), actor=actor or uid)
    return f"(個人MCP: サーバ {pfx} 未登録)"


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "list":
        print(json.dumps(servers(sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
