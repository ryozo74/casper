#!/usr/bin/env python3
"""Casper ↔ Aurora(HTML Archive Server)連携。
- 接続層: Elvis殿の FastMCP(/mcp 8100)を casper_mcp の MCPプロトコルで叩く(config駆動)。
  search_documents/get_document(既存・読取) + create_document/append_version(Elvis殿実装待ち)。
- 筆: note_html() — 会話/内容から「整ったHTMLノート」を生成(Aurora書架へ保存する実体)。
設定: .casper_aurora(AURORA_MCP_URL=... / AURORA_TOKEN=...)。未設定なら接続層は休止し筆のみ動く。"""
import html as _html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def _conf():
    """AURORA_MCP_URL / AURORA_TOKEN を .casper_aurora or env から。未設定は ('','')。"""
    url = os.environ.get("AURORA_MCP_URL", "")
    tok = os.environ.get("AURORA_TOKEN", "")
    p = os.path.join(HERE, ".casper_aurora")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            s = line.strip()
            if "=" not in s or s.startswith("#"):
                continue
            k, v = s.split("=", 1)
            k, v = k.strip().upper(), v.strip().strip('"').strip("'")
            if k.endswith("URL"):
                url = v
            elif k.endswith("TOKEN"):
                tok = v
    return url, tok


def configured():
    u, t = _conf()
    return bool(u and t)


# ---- 接続層(Elvis殿のMCPへ・既存プロトコル流用) -------------------------
def _call(name, args):
    """Aurora MCP ツール呼出。未設定なら None。create/append は Elvis殿実装後に有効。"""
    url, tok = _conf()
    if not (url and tok):
        return None
    try:
        import casper_mcp
        return casper_mcp.call_tool(name, args, token=tok, url=url)
    except Exception as e:
        return f"(Aurora呼出失敗: {e})"


def search(query, limit=8):
    return _call("search_documents", {"query": query, "limit": limit})


def get(doc_id):
    return _call("get_document", {"doc_id": doc_id})


def create(title, html_body, author_id=None, tags=None):     # Elvis殿の書込MCP露出後に有効
    return _call("create_document", {"title": title, "html": html_body,
                                     "author_id": author_id, "tags": tags or []})


def append_version(doc_id, html_body, author_id=None):
    return _call("append_version", {"doc_id": doc_id, "html": html_body, "author_id": author_id})


# ---- 筆: 整ったHTMLノート生成 --------------------------------------------
def _md_inline(s):
    s = _html.escape(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s


def _md_body(md):
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        h = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if h:
            n = len(h.group(1)) + 1
            out.append(f"<h{n}>{_md_inline(h.group(2))}</h{n}>"); i += 1; continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + _md_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>"); i += 1
            out.append("<ul>" + "".join(items) + "</ul>"); continue
        if "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?\s*[-:|\s]+\|", lines[i + 1]):
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            head = rows[0]; body = rows[2:]
            t = "<table><thead><tr>" + "".join(f"<th>{_md_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>"
            out.append(t); continue
        if ln.strip() == "":
            i += 1; continue
        out.append(f"<p>{_md_inline(ln)}</p>"); i += 1
    return "\n".join(out)


_CSS = """
:root{--bg:#fff;--txt:#1f2933;--mut:#6b7280;--line:#e5e7eb;--accent:#2563eb}
*{box-sizing:border-box}body{margin:0;background:#f3f4f6;color:var(--txt);
  font:16px/1.7 -apple-system,"Segoe UI",Roboto,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif}
.wrap{max-width:760px;margin:32px auto;background:var(--bg);padding:40px 48px;border-radius:14px;
  box-shadow:0 1px 3px rgba(0,0,0,.08)}
.meta{color:var(--mut);font-size:13px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:24px}
.tag{display:inline-block;background:#eef2ff;color:var(--accent);border-radius:999px;padding:2px 10px;font-size:12px;margin-right:6px}
h1{font-size:26px;margin:0 0 6px;line-height:1.3}h2{font-size:20px;margin:28px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:22px 0 8px}p{margin:12px 0}ul{margin:12px 0;padding-left:22px}li{margin:5px 0}
code{background:#f3f4f6;border-radius:5px;padding:1px 6px;font-size:14px}a{color:var(--accent)}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left}th{background:#f9fafb}
"""


def note_html(title, body_md, author="", tags=None, ts=""):
    """整ったHTMLノート(自己完結)を返す。Aurora書架へ保存する実体。"""
    tagrow = "".join(f'<span class="tag">{_html.escape(t)}</span>' for t in (tags or []))
    meta = " / ".join(x for x in [f"著者: {_html.escape(author)}" if author else "",
                                  f"作成: {_html.escape(ts)}" if ts else ""] if x)
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{_html.escape(title)}</h1>
<div class="meta">{meta}{('<br>' + tagrow) if tagrow else ''}</div>
{_md_body(body_md)}
</div></body></html>"""


if __name__ == "__main__":
    print("configured:", configured())
    print(note_html("テストノート", "# 見出し\n- 項目1\n- **太字**項目\n\n本文です。",
                    author="ryoji", tags=["test", "casper"], ts="2026-06-24")[:300])
