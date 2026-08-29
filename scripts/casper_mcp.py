#!/usr/bin/env python3
"""Casper ↔ Calendar MCP ブリッジ (Streamable HTTP クライアント)。
qwen3.6 は MCP ネイティブ非対応ゆえ、ここで MCP の tools/list を OpenAI function 定義へ変換し、
qwen の tool_call を MCP tools/call へ橋渡しする。chat_server から使う。

プロトコル(裏取り済 2026-06-22):
 ① POST initialize → レスポンスヘッダ mcp-session-id 取得
 ② POST notifications/initialized
 ③ 以降 全リクに mcp-session-id ヘッダ。応答は SSE 形式(data: {...})。
"""
import json
import os
import re
import threading
import time
import urllib.request

MCP_URL = os.environ.get("CASPER_MCP_URL", "http://192.168.44.253:8001/mcp/")
RO_TOKEN = os.environ.get("CASPER_RO_TOKEN", "")
WRITE_TOKEN = os.environ.get("CASPER_WRITE_TOKEN", "")
PROTO = "2025-03-26"


def _parse_sse(body):
    """SSE 応答(data: {json})から最後の JSON-RPC 結果を取り出す。
    ★str.splitlines()は使わない: data 内の JSON 文字列値に生の制御文字(例 U+0085 NEL)が
    混入し得る(巨大バイナリをbase64化せず直接JSON文字列に埋める設計時)。splitlines()は
    U+0085等もSSE規格外の改行として分割してしまい、data行が寸断される(cmd_487で実測確認)。
    SSEレコード区切りは仕様上 CRLF/LF のみゆえ、それだけで分割する。"""
    last = None
    for line in re.split(r"\r\n|\n", body):
        line = line.strip("\r")
        if line.startswith("data:"):
            try:
                last = json.loads(line[5:].strip())
            except Exception:
                pass
    return last


def _post(payload, sid=None, token=None, actor=None, timeout=30, url=None):
    """MCP へ JSON-RPC POST。(json_result, headers) を返す。url 未指定なら既定 Calendar MCP。"""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {token or RO_TOKEN}"}
    if sid:
        headers["mcp-session-id"] = sid
    if actor:
        headers["X-Actor-User-Id"] = str(actor)
    req = urllib.request.Request(url or MCP_URL, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        sess = r.headers.get("mcp-session-id")
    ct = None
    if raw.lstrip().startswith("{"):
        try:
            ct = json.loads(raw)
        except Exception:
            ct = None
    if ct is None:
        ct = _parse_sse(raw)
    return ct, sess


def _session(token=None, url=None):
    """initialize→initialized でセッションを確立し session id を返す。"""
    res, sess = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": PROTO, "capabilities": {},
                                  "clientInfo": {"name": "casper", "version": "1.0"}}}, token=token, url=url)
    if not sess:
        return None
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sess, token=token, url=url)
    return sess


# 【Fable第七診】tools定義の短命cache。
# 実測: 毎turn、session確立(initialize+initialized)とtools/listのHTTP往復を無条件に払っていた。
# ツール定義が分単位で変わることはない。★ただし失敗([])はcacheしない——「一時的に取れなかった」
# を「ツールが無い」として固定すれば、それは cache ではなく嘘になる(失敗とゼロを別出口へ)。
_TOOLS_CACHE = {}                     # (url, token) -> {"ts": float, "tools": [...]}
_TOOLS_CACHE_TTL = 300.0              # 秒
_TOOLS_CACHE_LOCK = threading.Lock()


def tools_cache_clear():
    """cacheを捨てる(MCPサーバの入れ替え直後など、待たずに読み直したい時)。"""
    with _TOOLS_CACHE_LOCK:
        _TOOLS_CACHE.clear()


def list_tools(token=None, url=None, use_cache=True):
    """MCP の tools を OpenAI function 定義のリストに変換して返す。失敗時 []。
    既定でTTL cacheを使う(use_cache=Falseで素通し)。"""
    ck = (url or MCP_URL, token or "")
    if use_cache:
        with _TOOLS_CACHE_LOCK:
            hit = _TOOLS_CACHE.get(ck)
        if hit and (time.time() - hit["ts"]) < _TOOLS_CACHE_TTL:
            return list(hit["tools"])
    try:
        sid = _session(token, url=url)
        if not sid:
            return []
        res, _ = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid, token=token, url=url)
        tools = (res or {}).get("result", {}).get("tools", [])
        out = []
        for t in tools:
            out.append({"type": "function", "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        if out:                                   # ★空(=取れなかった)はcacheしない
            with _TOOLS_CACHE_LOCK:
                _TOOLS_CACHE[ck] = {"ts": time.time(), "tools": list(out)}
        return out
    except Exception:
        return []


def call_tool(name, arguments, token=None, actor=None, url=None):
    """MCP tools/call を実行し、結果テキストを返す。"""
    try:
        sid = _session(token, url=url)
        if not sid:
            return "(MCP接続失敗)"
        res, _ = _post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}}},
                       sid=sid, token=token, actor=actor, url=url)
        if not res:
            return "(MCP応答なし)"
        if "error" in res:
            return f"(MCPエラー: {res['error'].get('message')})"
        content = res.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(res.get("result", {}), ensure_ascii=False)[:4000]
    except Exception as e:
        return f"(MCP呼出失敗: {e})"


def call_tools(calls, token=None, actor=None, url=None):
    """1セッションで複数 tools/call をまとめ実行(セッション確立の往復を節約)。
    calls=[(name, args), ...] → 各結果テキストのリストを返す。"""
    try:
        sid = _session(token, url=url)
        if not sid:
            return ["(MCP接続失敗)"] * len(calls)
        out = []
        for name, args in calls:
            res, _ = _post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": name, "arguments": args or {}}},
                           sid=sid, token=token, actor=actor, url=url)
            if not res:
                out.append("(MCP応答なし)")
            elif "error" in res:
                out.append(f"(MCPエラー: {res['error'].get('message')})")
            else:
                content = res.get("result", {}).get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                out.append("\n".join(texts) if texts else json.dumps(res.get("result", {}), ensure_ascii=False)[:4000])
        return out
    except Exception as e:
        return [f"(MCP呼出失敗: {e})"] * len(calls)


if __name__ == "__main__":
    print("tools:", [t["function"]["name"] for t in list_tools()])
    print("get_projects:", call_tool("get_projects", {})[:200])
