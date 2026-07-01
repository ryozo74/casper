#!/usr/bin/env python3
"""動向層＝経験層: 各人の『動向帯』を自動蒸留 (learn_users.py の弟)。
多源(会話ログ/dev_log/threads/org DM逐語/Score現場ログ)を人別に集約し、LLMで
件・乗り替わり・人物理解(軸②)・先読み(推測明示)に蒸留 → vault/25_activity/u_<uid>.md。

蒸留役の使い分け(2026-07-01 実測で確定):
  深い蒸留(既定) = Opus(claude CLI 迂回)   … 軸②の癖/正確さ/予防先読みが鋭い
  日次の軽い更新  = Qwen(Ollama)  … CASPER_ACT_MODEL=qwen3.6:27b で切替・安価/PII安全

実行: python3 distill_activity.py                (会話のある全uid)
      python3 distill_activity.py <uid>          (指定uidのみ)
      CASPER_ACT_MODEL=qwen3.6:27b python3 distill_activity.py 34   (軽量=qwen)
冪等: 既存帯があれば『前回の帯』として渡し、新素材で更新。元データは非破壊(読むだけ)。
"""
import collections
import datetime
import glob
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ACTIVITY = os.path.join(HERE, "..", "vault", "25_activity")
CONVO = os.path.join(HERE, "conversation_log.jsonl")
DEVLOG = os.path.join(HERE, "dev_log.jsonl")
THREADS = os.path.join(HERE, "threads")
SCOREDB = os.path.join(HERE, "..", "..", "..", "score_be", "score.db")

CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN", "claude")
MODEL = os.environ.get("CASPER_ACT_MODEL", "opus")          # 既定=Opus(深)。qwen3.6:27b で軽量
OLLAMA = os.environ.get("CASPER_ENDPOINT", "http://192.168.44.119:11434").rstrip("/") + "/api/chat"
MIN_EVENTS = 4                                              # これ未満の活動しかないuidはskip

NAMES = {"28": "ryoji", "31": "kiyotomo", "34": "hori", "30": "tetsuo", "40": "terajima", "29": "kohei"}


def _write_token():
    for fn in (".casper_write_token", "CASPER_WRITE_TOKEN.txt"):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            for line in open(p, encoding="utf-8").read().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                return line.split("=", 1)[1].strip() if "=" in line and line.split("=", 1)[0].upper().endswith("TOKEN") else line
    return os.environ.get("CASPER_WRITE_TOKEN", "")


def uname(uid):
    return NAMES.get(str(uid), str(uid))


def local_events():
    """conversation_log + dev_log を uid 別に集める。"""
    by = collections.defaultdict(list)
    for fn, tag in ((CONVO, "conv"), (DEVLOG, "dev")):
        if not os.path.exists(fn):
            continue
        for ln in open(fn, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            uid = str(d.get("uid") or "")
            if not uid:
                continue
            ts = d.get("ts", "")
            if tag == "conv" and d.get("role") == "user":
                by[uid].append((ts, f"[{tag}/発言] {str(d.get('content',''))[:400]}"))
            elif tag == "dev":
                if d.get("user"):
                    by[uid].append((ts, f"[dev/Q] {str(d.get('user',''))[:300]}"))
                if d.get("answer"):
                    by[uid].append((ts, f"[dev/A] {str(d.get('answer',''))[:400]}"))
    return by


def score_rows():
    """score.db を uid 別に(現場の肉声: blocker/handover/next_priority 等)。"""
    by = collections.defaultdict(list)
    if not os.path.exists(SCOREDB):
        return by
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{SCOREDB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        for t in ("timecard_logs", "routine_logs", "bug_reports", "qc_delegations"):
            try:
                for r in c.execute(f"SELECT * FROM {t}"):
                    d = dict(r)
                    uid = str(d.get("user_id") or d.get("reporter_user_id") or d.get("requested_by") or "")
                    if uid:
                        by[uid].append(f"[score/{t}] {json.dumps(d, ensure_ascii=False)[:260]}")
            except Exception:
                pass
        c.close()
    except Exception:
        pass
    return by


def org_dm(uid, tok):
    """get_messages で org DM 全スレッドの逐語を集める(token要)。"""
    if not tok:
        return []
    try:
        import casper_mcp
        d = json.loads(casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "limit": 40}, token=tok, actor=int(uid)))
        threads = sorted(d.get("threads", []), key=lambda t: str(t.get("updated_at") or ""), reverse=True)[:8]
        out = []
        for t in threads:
            tid = t.get("thread_id")
            parts = "/".join(str(p.get("name") or p.get("user_id")) for p in t.get("participants", []))
            out.append(f"[DM thread {tid} ({parts})]")
            try:
                full = json.loads(casper_mcp.call_tool("get_messages", {"actor_id": int(uid), "thread_id": int(tid)}, token=tok, actor=int(uid)))
                for m in (full.get("messages") or [])[-12:]:
                    snd = uname(m.get("sender_id") or m.get("user_id") or "?")
                    body = str(m.get("body") or m.get("content") or "").strip()
                    if body and body != "Task message thread initialized." and not body.lstrip().startswith("@") or len(body) > 12:
                        out.append(f"  [{str(m.get('created_at') or '')[:16]}] {snd}: {body[:220]}")
            except Exception:
                out.append(f"  (last) {str(t.get('last_message'))[:200]}")
        return out
    except Exception:
        return []


def gather(uid, tok):
    """1uid分の全素材を時系列テキストに。"""
    parts = [f"# 素材 uid={uid} ({uname(uid)})", "", "## org DM 逐語(get_messages)"]
    dm = org_dm(uid, tok)
    parts += dm if dm else ["(なし/未取得)"]
    parts += ["", "## 会話・dev ログ(時系列)"]
    ev = sorted(local_events().get(uid, []), key=lambda x: x[0])
    parts += [f"[{ts[:16]}] {txt}" for ts, txt in ev[-80:]] or ["(なし)"]
    parts += ["", "## Score現場ログ"]
    sc = score_rows().get(uid, [])
    parts += sc if sc else ["(なし)"]
    return "\n".join(parts), len(ev) + len(dm)


PROMPT_TMPL = (
    "以下は社員 {name}(uid{uid}) の生データ(会話/org DM逐語/Score現場ログ)。これを『動向帯』に蒸留せよ。\n"
    "出力形式(このmarkdown構造のみ・前後の説明文不要):\n"
    "## uid={uid} ({name}) — 動向バンド\n"
    "### 件（topic/PJ別）\n各件に: 相手/関係者・期間・状態遷移・乗り替わり(担当/論点/主体が別へ移る継ぎ目)・現況(未決はOPEN LOOP印)・要旨\n"
    "### 乗り替わり ハイライト\n### 人物理解(軸②・通じ合いの質)\n通信の癖・リテラシー・語彙の癖を証拠ベースで\n"
    "### 先読み候補\nopen loopへの先回り。各項 必ず「(推測)」で始める\n\n"
    "【掟】①事実と解釈を峻別②推測は「(推測)」明示③材料に無い事は創作するな(捏造禁止が最優先)"
    "④状態(〜した?/上がった?)は帯の古記述を結末と誤認せず実物照会前提⑤揺れる語(動画/資料/データ)は同一対象へ串刺し"
    "⑥**時刻は絶対表記(MM-DD HH:MM)で刻め**。『N時間後』等の相対表現は避け、ETAも絶対時刻に換算せよ"
    "(この帯は as-of {asof} 時点のスナップショットゆえ、後で読むと相対表現は陳腐化する)。\n"
    "{prev}\n## 素材:\n{material}")


def distill(uid, material, prev="", asof=""):
    prev_blk = ("\n## 前回の帯(これを踏まえ更新):\n" + prev + "\n") if prev else ""
    prompt = PROMPT_TMPL.format(name=uname(uid), uid=uid, prev=prev_blk, material=material[:14000], asof=asof)
    if MODEL.startswith("qwen"):                       # 軽量=ローカルqwen
        try:
            body = {"model": MODEL, "think": False, "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0.2, "num_predict": 1400}}
            req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=300).read()).get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"(蒸留失敗 qwen: {e})"
    try:                                               # 既定=Opus(claude CLI)
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                           capture_output=True, text=True, timeout=400, cwd="/tmp")
        return (r.stdout or "").strip()
    except Exception as e:
        return f"(蒸留失敗 opus: {e})"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    tok = _write_token()
    os.makedirs(ACTIVITY, exist_ok=True)
    uids = [only] if only else sorted(set(local_events().keys()) | set(score_rows().keys()))
    n = 0
    for uid in uids:
        if not uid:
            continue
        material, cnt = gather(uid, tok)
        if cnt < MIN_EVENTS:
            print(f"skip u_{uid} (活動{cnt}件・{MIN_EVENTS}未満)")
            continue
        path = os.path.join(ACTIVITY, f"u_{uid}.md")
        prev = ""
        if os.path.exists(path):
            prev = open(path, encoding="utf-8").read()[:4000]
        asof = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        band = distill(uid, material, prev, asof=asof)
        # as-of を帯冒頭に明示 = 時間軸のズレ対策。状態問合せは live(左脳)で裏取りする前提(帯は物語/先読み用)。
        hdr = (f"<!-- distill_activity.py / model={MODEL} / 素材{cnt}件 / 元データ非破壊 -->\n"
               f"> **as-of {asof} 時点のスナップショット**。現在の状態はこの帯でなく live(Calendar)で裏取りせよ。\n\n")
        open(path, "w", encoding="utf-8").write(hdr + band + "\n")
        print(f"OK u_{uid} ({uname(uid)}) -> 25_activity/u_{uid}.md (素材{cnt}件・model={MODEL})")
        n += 1
    if n:                                              # 帯更新→RAG索引を差分再index(意味検索の時間軸ズレ対策)
        try:
            import casper_embed
            print(f"再index(差分): {casper_embed.reindex()}")
        except Exception as e:
            print(f"再index skip: {e}")
    print(f"=== 完了: {n} uid の動向帯を蒸留 (model={MODEL}) ===")


if __name__ == "__main__":
    main()
