#!/usr/bin/env python3
"""Casper ↔ Aurora(HTML Archive Server)連携。
- 接続層: Elvis殿の FastMCP(/mcp 8100)を casper_mcp の MCPプロトコルで叩く(config駆動)。
  search_documents/get_document(既存・読取) + create_document/append_version(Elvis殿実装待ち)。
- 筆: note_html() — 会話/内容から「整ったHTMLノート」を生成(Aurora書架へ保存する実体)。
設定: .casper_aurora(AURORA_MCP_URL=... / AURORA_TOKEN=...)。未設定なら接続層は休止し筆のみ動く。"""
import html as _html
import json as _json
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


def doc_base():
    """文書ビューの基底URL(MCP url から /mcp を外す)。例: http://192.168.44.155:8100"""
    url, _ = _conf()
    return re.sub(r"/mcp/?$", "", url).rstrip("/")


def search(query, limit=8):
    """検索結果に各ノートの**閲覧URL**(base/doc/{slug})を同梱→Casperがユーザーにリンクを渡せる。"""
    r = _call("search_documents", {"query": query, "limit": limit})
    if not r:
        return r
    try:
        d = _json.loads(r) if isinstance(r, str) else r
        base = doc_base()
        for doc in (d.get("results") or d.get("items") or []):
            ref = doc.get("slug") or doc.get("id")
            if ref:
                doc["url"] = f"{base}/doc/{ref}"
        return _json.dumps(d, ensure_ascii=False)
    except Exception:
        return r


def get(doc_id):
    # Elvis Aurora の get_document は引数 id を要求（append_version は doc_id ゆえ非対称・要注意）
    return _call("get_document", {"id": doc_id})


def create(title, html_body, author_id="casper", project="社内", work="ノート", tags=None, scope="public"):
    # Elvis Aurora の create_document 必須: title, html, author_id(str), project, work
    return _call("create_document", {"title": title, "html": html_body,
                                     "author_id": str(author_id or "casper"),
                                     "project": project, "work": work,
                                     "tags": tags or [], "scope": scope})


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
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');
:root{--page:#070b14;--bg:#0f1828;--txt:#e5e7eb;--mut:#8b97a8;--line:rgba(255,255,255,.12);--accent:#38bdf8}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--txt);
  font:16px/1.75 "Outfit",-apple-system,"Segoe UI",Roboto,"Noto Sans JP",sans-serif}
.wrap{max-width:900px;margin:32px auto;background:var(--bg);padding:40px 48px;border-radius:16px;
  border:1px solid var(--line);box-shadow:0 8px 30px rgba(0,0,0,.35)}
.meta{color:var(--mut);font-size:13px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:24px}
.tag{display:inline-block;background:rgba(56,189,248,.13);color:#bae6fd;border:1px solid rgba(56,189,248,.35);
  border-radius:999px;padding:2px 11px;font-size:12px;margin-right:6px}
h1{font-size:28px;margin:0 0 6px;line-height:1.3;color:#f1f5f9;font-weight:700}
h2{font-size:21px;margin:30px 0 10px;border-left:4px solid var(--accent);padding-left:12px;color:#e5e7eb;font-weight:600}
h3{font-size:16px;margin:22px 0 8px;color:#cbd5e1}p{margin:12px 0}ul{margin:12px 0;padding-left:22px}li{margin:6px 0}
code{background:rgba(255,255,255,.08);border-radius:5px;padding:1px 6px;font-size:14px}a{color:var(--accent)}
img{max-width:100%;border-radius:8px}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left}th{background:rgba(255,255,255,.05)}
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


# --- Aurora レンダラ連携(STATE→ダッシュHTML) + ローカル保管 ---
AURORA_SKILL = os.environ.get("CASPER_AURORA_SKILL",
                              os.path.join(HERE, "..", "..", "..", "skills", "aurora"))
NOTES_DIR = os.path.join(HERE, "aurora_notes")   # backend接続前のローカル保管


def render_state(state):
    """Aurora STATE(dict)→ render_local.py で『非素人』ダッシュHTMLを生成。STATE系ノート(status/分析)用。"""
    import json as _j
    import subprocess
    import tempfile
    rl = os.path.join(AURORA_SKILL, "scripts", "render_local.py")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as sf:
        _j.dump(state, sf, ensure_ascii=False)
        sp = sf.name
    out = sp + ".html"
    r = subprocess.run(["python3", rl, "--state", sp, "--out", out], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("render_local失敗: " + (r.stderr or "")[:300])
    return open(out, encoding="utf-8").read()


def save_local(html, slug):
    """Aurora backend 接続前: aurora_notes/ にローカル保管(接続後は create() で書架へ移送)。"""
    os.makedirs(NOTES_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", slug)[:60] or "note"
    p = os.path.join(NOTES_DIR, safe + ".html")
    open(p, "w", encoding="utf-8").write(html)
    return p


def make_note(title, body_md="", author="", tags=None, state=None, ts=""):
    """ノートHTML生成。state指定=Auroraダッシュ描画 / 無し=document(prose: 議事録/レポート)。"""
    return render_state(state) if state else note_html(title, body_md, author=author, tags=tags, ts=ts)


if __name__ == "__main__":
    print("configured:", configured(), "/ skill:", os.path.isdir(AURORA_SKILL))
    print("doc筆:", note_html("テストノート", "# 見出し\n- 項目1\n- **太字**項目", author="ryoji", ts="2026-06-24")[:120])
