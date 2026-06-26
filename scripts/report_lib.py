#!/usr/bin/env python3
"""Casper 報告書ビルダー核: 種別ライブラリ + 構成(ページング)提案 + 第一稿生成 + ブロック構造化HTML。

設計(Aurora doc 6f5cbbb1):
- 北極星: Casper は"そぎ落とすアドバイザー"。無駄を書かせず構成と内容を助言し短く鋭い資料に。
- 起点: 目的→種別判定→PJアンカー→構成(ページング)提案=第一稿の主戦場→穴だけ質問→第一稿。
- 1スレ1資料・版で育つ。ブロック構造化(data-bid)でクリック編集を可能に。

llm: chat_server.llm_text(system,user,num_predict) を引数で受ける(循環import回避)。
"""
import json
import re
import html as _H

try:
    import casper_aurora as _au
except Exception:
    _au = None


# ============ 種別ライブラリ ============
# 各種別: 型(framework) / 章立て(sections) / データ源 / 穴質問 / 既定ページ割り
REPORT_TYPES = {
    "research": {
        "label": "リサーチ報告",
        "when": "調査・比較・技術検討の結果を報告したい時",
        "framework": "目的 → 調査軸 → 候補/事実 → 比較評価 → 結論/推奨",
        "sections": [
            {"key": "purpose", "title": "目的・背景", "hint": "何を・なぜ調べたか(1〜2行)"},
            {"key": "scope", "title": "調査範囲・評価軸", "hint": "比較候補と評価軸"},
            {"key": "findings", "title": "調査結果(事実)", "hint": "候補ごとの事実・spec(表が有効)"},
            {"key": "eval", "title": "比較・評価", "hint": "評価軸での優劣・トレードオフ"},
            {"key": "conclusion", "title": "結論・推奨", "hint": "結論を先頭に1〜2行で断言"},
        ],
        "data_sources": ["ユーザー提供ファイル(PPT/Excel/PDF)", "Webリンク(spec/一次情報)", "vault(過去調査)"],
        "questions": [
            {"key": "goal", "q": "この調査の目的は？(何を判断したい)", "type": "text"},
            {"key": "candidates", "q": "比較する候補は？(機体/部品/手法 等)", "type": "text"},
            {"key": "axes", "q": "重視する評価軸は？", "type": "choice",
             "options": ["コスト", "性能", "重量/サイズ", "入手性/納期", "信頼性/実績"]},
            {"key": "constraint", "q": "制約(予算/規制/納期)はある？", "type": "text"},
        ],
        "default_pages": [
            {"title": "概要・調査", "section_keys": ["purpose", "scope", "findings"]},
            {"title": "評価・結論", "section_keys": ["eval", "conclusion"]},
        ],
    },
    "pj_final": {
        "label": "PJ最終報告",
        "when": "プロジェクト/案件の完了・締めを報告したい時",
        "framework": "概要 → 成果物 → 工程/予実 → 品質・課題・学び → 申し送り",
        "sections": [
            {"key": "overview", "title": "概要", "hint": "PJの目的と結果を1〜2行で"},
            {"key": "deliverables", "title": "成果物", "hint": "納品物・リンク・点数"},
            {"key": "process", "title": "工程・予実", "hint": "日程/工数の予定対実績"},
            {"key": "quality", "title": "品質・課題", "hint": "品質結果と起きた課題"},
            {"key": "learnings", "title": "学び・申し送り", "hint": "次回への教訓(箇条書き)"},
        ],
        "data_sources": ["ユーザー提供ファイル", "Calendar(PJ実績/タスク/担当)", "既存の中間報告", "Vimeo(納品物)"],
        "questions": [
            {"key": "result", "q": "このPJの成果(納品物)は？", "type": "text"},
            {"key": "schedule", "q": "日程は予定通りだった？", "type": "choice",
             "options": ["予定通り", "一部遅延", "大幅遅延", "前倒し"]},
            {"key": "issues", "q": "品質課題・トラブルは？", "type": "text"},
            {"key": "learnings", "q": "次回への学び・改善点は？", "type": "text"},
        ],
        "default_pages": [
            {"title": "概要・成果", "section_keys": ["overview", "deliverables"]},
            {"title": "工程・品質・学び", "section_keys": ["process", "quality", "learnings"]},
        ],
    },
}


def types_list():
    """種別一覧(フロントの選択肢用)。"""
    return [{"key": k, "label": v["label"], "when": v["when"], "framework": v["framework"]}
            for k, v in REPORT_TYPES.items()]


def _sec(rtype, key):
    for s in REPORT_TYPES[rtype]["sections"]:
        if s["key"] == key:
            return s
    return {"key": key, "title": key, "hint": ""}


# ============ 構成(ページング)提案 = 第一稿の主戦場 ============
def propose_structure(rtype, goal, anchor, llm=None):
    """種別の既定ページ割りを土台に、目的/アンカーへ合わせた構成＋ページング案を返す。
    qwen には"短い助言"だけ任せ、骨格は決定論的(取り違え防止)。"""
    t = REPORT_TYPES.get(rtype)
    if not t:
        return {"error": f"unknown type {rtype}"}
    pages = []
    for p in t["default_pages"]:
        pages.append({"title": p["title"],
                      "sections": [{"key": k, "title": _sec(rtype, k)["title"], "hint": _sec(rtype, k)["hint"]}
                                   for k in p["section_keys"]]})
    advice = ""
    if llm:
        try:
            sysp = ("あなたは資料作成の構成アドバイザー。冗長を嫌い、短く鋭い構成に導く。"
                    "与えた構成案に対し、この報告の目的に照らした**構成・ページングの助言を2〜3文**で(削るべき章・統合案・順序があれば具体的に)。前置き禁止・助言本文のみ。")
            usr = (f"種別: {t['label']}（型: {t['framework']}）\n目的: {goal}\n対象(アンカー): {anchor}\n"
                   f"現在の構成案: " + " / ".join(f"P{i+1}:{p['title']}[" + ",".join(s['title'] for s in p['sections']) + "]"
                                                 for i, p in enumerate(pages)))
            advice = (llm(sysp, usr, 400) or "").strip()[:600]
        except Exception:
            advice = ""
    return {"type": rtype, "label": t["label"], "framework": t["framework"],
            "pages": pages, "data_sources": t["data_sources"], "advice": advice}


def questions_for(rtype):
    """その種別の穴質問(選択式優先)。"""
    return REPORT_TYPES.get(rtype, {}).get("questions", [])


# ============ 第一稿生成(そぎ落とし) ============
def generate_draft(rtype, structure, answers, context="", llm=None):
    """各章を簡潔に生成。LLMは {sections:{key:[blocks]}} のJSONを返す。block: {t:'p'|'ul'|'h3', text|items}。"""
    t = REPORT_TYPES.get(rtype)
    if not t:
        return {"error": "unknown type"}
    sec_keys = [s["key"] for p in structure.get("pages", []) for s in p["sections"]]
    if not sec_keys:
        sec_keys = [s["key"] for s in t["sections"]]
    sched = {s["key"]: _sec(rtype, s["key"]) for s in t["sections"]}
    plan = "\n".join(f"- {k}（{sched[k]['title']}）: {sched[k]['hint']}" for k in sec_keys)
    ans = "\n".join(f"- {k}: {v}" for k, v in (answers or {}).items() if v)
    schema = ('{"sections":{' + ",".join(f'"{k}":[]' for k in sec_keys) + '}}')
    sysp = ("あなたは studio bokan の伴走AI Casper。報告書の第一稿を書く。**鉄則: 結論ファースト・冗長禁止・"
            "1章は要点のみ・箇条書きを活用・水増し文を書かない**。各章を以下ブロックの配列で出力。"
            "block種: {\"t\":\"p\",\"text\":\"...\"}(段落) / {\"t\":\"ul\",\"items\":[\"..\"]}(箇条書き) / {\"t\":\"h3\",\"text\":\"小見出し\"}。"
            "厳密なJSONのみ(説明・コードフェンス禁止)。未確定は推測せず『(要確認)』と置く。スキーマ:\n" + schema)
    usr = (f"種別: {t['label']}（型: {t['framework']}）\n\n■章立て(この順・keyで出力):\n{plan}\n\n"
           f"■ユーザー回答:\n{ans or '(なし)'}\n\n■参考データ:\n{(context or '(なし)')[:2500]}")
    raw = ""
    if llm:
        try:
            raw = llm(sysp, usr, 4000) or ""
        except Exception as e:
            raw = f"[exc]{e}"
    m = re.search(r"\{.*\}", raw, re.S)
    data = None
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = None
    secs = (data or {}).get("sections") if isinstance(data, dict) else None
    if not isinstance(secs, dict):
        # フォールバック: 各章プレースホルダ(qwenがJSON失敗時も資料は出す)
        secs = {k: [{"t": "p", "text": "(要記入) " + sched[k]["hint"]}] for k in sec_keys}
    # 欠章を補完
    for k in sec_keys:
        if k not in secs or not secs[k]:
            secs[k] = [{"t": "p", "text": "(要記入) " + sched[k]["hint"]}]
    return {"sections": secs, "ok": isinstance(data, dict)}


# ============ ブロック構造化HTML(data-bid でクリック編集可能に) ============
def _block_html(bid, b):
    t = b.get("t", "p")
    if t == "h3":
        return f'<h3 class="rblk" data-bid="{bid}">{_H.escape(str(b.get("text", "")))}</h3>'
    if t == "ul":
        lis = "".join(f"<li>{_H.escape(str(x))}</li>" for x in (b.get("items") or []))
        return f'<ul class="rblk" data-bid="{bid}">{lis}</ul>'
    return f'<p class="rblk" data-bid="{bid}">{_H.escape(str(b.get("text", "")))}</p>'


def render_blocks_html(title, meta, structure, sections_blocks):
    """ページ＋ブロック(data-bid)構造のHTML。Aurora トーン。プレビュー/編集はこのHTMLを対象に。"""
    css = (_au._CSS if _au else "body{font-family:sans-serif;max-width:860px;margin:auto}")
    parts = [f'<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{_H.escape(title)}</title><style>{css}',
             '.rpage{border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:8px 20px 18px;margin:18px 0;'
             'background:rgba(255,255,255,.02)}.rpage>.pgh{font-size:12px;letter-spacing:.08em;opacity:.6;'
             'text-transform:uppercase;margin:6px 0 2px}.rblk{scroll-margin-top:60px}</style></head><body>',
             f'<div class="wrap"><h1>{_H.escape(title)}</h1>',
             f'<div class="meta">{_H.escape(meta)}</div>']
    pages = structure.get("pages") or [{"title": "", "sections": [{"key": k} for k in sections_blocks]}]
    for pi, pg in enumerate(pages):
        parts.append(f'<section class="rpage" data-page="{pi+1}"><div class="pgh">ページ {pi+1} — {_H.escape(pg.get("title",""))}</div>')
        for s in pg["sections"]:
            k = s["key"]
            parts.append(f'<h2 class="rblk" data-bid="{k}-h">{_H.escape(_sec_title(structure, k, s))}</h2>')
            for i, b in enumerate(sections_blocks.get(k, [])):
                parts.append(_block_html(f"{k}-{i}", b))
        parts.append('</section>')
    parts.append('</div></body></html>')
    return "".join(parts)


def _sec_title(structure, key, s):
    if s.get("title"):
        return s["title"]
    for pg in structure.get("pages", []):
        for ss in pg["sections"]:
            if ss["key"] == key and ss.get("title"):
                return ss["title"]
    return key


# ============ ブロック編集(クリック編集の保存) ============
def patch_block(html, bid, new_inner):
    """data-bid=<bid> の要素の中身を new_inner(エスケープ済テキスト or 簡易HTML)で差し替え。"""
    esc = _H.escape(new_inner)
    pat = re.compile(r'(<(\w+)([^>]*\bdata-bid="' + re.escape(bid) + r'"[^>]*)>)(.*?)(</\2>)', re.S)
    if not pat.search(html):
        return html, False
    return pat.sub(lambda m: m.group(1) + esc + m.group(5), html, count=1), True
