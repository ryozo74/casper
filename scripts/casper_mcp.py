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
import urllib.request

MCP_URL = os.environ.get("CASPER_MCP_URL", "http://192.168.44.253:8001/mcp/")
RO_TOKEN = os.environ.get("CASPER_RO_TOKEN", "")
WRITE_TOKEN = os.environ.get("CASPER_WRITE_TOKEN", "")
PROTO = "2025-03-26"


def _parse_sse(body):
    """SSE 応答(data: {json})から最後の JSON-RPC 結果を取り出す。"""
    last = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                last = json.loads(line[5:].strip())
            except Exception:
                pass
    return last


def _post(payload, sid=None, token=None, actor=None, timeout=30):
    """MCP へ JSON-RPC POST。(json_result, headers) を返す。"""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {token or RO_TOKEN}"}
    if sid:
        headers["mcp-session-id"] = sid
    if actor:
        headers["X-Actor-User-Id"] = str(actor)
    req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers)
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


def _session(token=None):
    """initialize→initialized でセッションを確立し session id を返す。"""
    res, sess = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": PROTO, "capabilities": {},
                                  "clientInfo": {"name": "casper", "version": "1.0"}}}, token=token)
    if not sess:
        return None
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sess, token=token)
    return sess


def list_tools(token=None):
    """MCP の tools を OpenAI function 定義のリストに変換して返す。失敗時 []。"""
    try:
        sid = _session(token)
        if not sid:
            return []
        res, _ = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid, token=token)
        tools = (res or {}).get("result", {}).get("tools", [])
        out = []
        for t in tools:
            out.append({"type": "function", "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        return out
    except Exception:
        return []


def call_tool(name, arguments, token=None, actor=None):
    """MCP tools/call を実行し、結果テキストを返す。"""
    try:
        sid = _session(token)
        if not sid:
            return "(MCP接続失敗)"
        res, _ = _post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}}},
                       sid=sid, token=token, actor=actor)
        if not res:
            return "(MCP応答なし)"
        if "error" in res:
            return f"(MCPエラー: {res['error'].get('message')})"
        content = res.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(res.get("result", {}), ensure_ascii=False)[:4000]
    except Exception as e:
        return f"(MCP呼出失敗: {e})"


if __name__ == "__main__":
    print("tools:", [t["function"]["name"] for t in list_tools()])
    print("get_projects:", call_tool("get_projects", {})[:200])
