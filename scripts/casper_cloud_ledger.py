#!/usr/bin/env python3
"""雲(Anthropic)へ出た内容の帳簿 — 殿御下命 2026-08-24。

【なぜ在るか】
GPU全滅時、Casper は最下段の座席=雲(claude_cli / Anthropic API)へ自動降段する。
その間、社の情報は社外(Anthropic)を経由する。殿は「頻度と内容を後で確認したい」と
仰せゆえ、**雲へ出た一件残らず**をここへ刻む。

【掟】
 ・記録は雲への出口(claude_cli_text / claude_cli_vision / anthropic_call)の【中】で行う。
   呼出側に任せれば、いつか誰かが呼び忘れる(単一機構の作法)。
 ・本文は截らずに残すのを既定とする。截った時は必ず truncated=True と全長を併記し、
   sha256 を添える(「全部載っている」と嘘をつかせぬ)。
 ・画像は本体を持てぬゆえ、パス・バイト数・sha256 を刻む(何を出したかは follow できる)。
 ・帳簿の書込に失敗しても本番の応答は止めぬ。ただし失敗は黙らず stderr へ出す
   (センサーの沈黙を作らぬ)。
 ・本ファイルは scripts/*.jsonl ゆえ git 管理外。0600 で置く(中身は社の情報である)。

CLI:
  python3 casper_cloud_ledger.py report              # 直近7日の頻度
  python3 casper_cloud_ledger.py report --days 30
  python3 casper_cloud_ledger.py list --days 1       # 中身(本文つき)を並べる
  python3 casper_cloud_ledger.py list --days 7 --door vision --full
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "casper_cloud_ledger.jsonl")
MAX_TEXT = int(os.environ.get("CASPER_CLOUD_LEDGER_MAX", "40000"))   # 1件あたりの保存上限(文字)


def _sha(s):
    try:
        return hashlib.sha256((s or "").encode("utf-8", "replace")).hexdigest()[:16]
    except Exception:
        return ""


def _clip(s):
    """截った事実を隠さぬ。戻り: (本文, truncated, 全長, sha256)"""
    s = s if isinstance(s, str) else ("" if s is None else str(s))
    n = len(s)
    if n <= MAX_TEXT:
        return s, False, n, _sha(s)
    return s[:MAX_TEXT], True, n, _sha(s)


def record(door, model, prompt=None, response=None, dur_sec=None, outcome="ok",
           ctx=None, image_path=None, extra=None):
    """雲へ出た一件を刻む。door: claude_cli_text / claude_cli_vision / anthropic_api。
    ★本番を止めぬ(例外を投げぬ)が、黙りもせぬ(失敗はstderrへ)。"""
    try:
        p, p_tr, p_len, p_sha = _clip(prompt)
        r, r_tr, r_len, r_sha = _clip(response)
        rec = {
            "ts": time.time(),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "door": door,                       # どの出口から出たか
            "model": model,
            "outcome": outcome,                 # ok / error (失敗とゼロを別出口へ)
            "dur_sec": round(dur_sec, 2) if isinstance(dur_sec, (int, float)) else None,
            "prompt_chars": p_len, "prompt_truncated": p_tr, "prompt_sha": p_sha,
            "prompt": p,
            "resp_chars": r_len, "resp_truncated": r_tr, "resp_sha": r_sha,
            "response": r,
        }
        if image_path:                          # 画像は本体を持てぬゆえ素性だけ刻む
            rec["image"] = {"path": image_path}
            try:
                b = open(image_path, "rb").read()
                rec["image"]["bytes"] = len(b)
                rec["image"]["sha256"] = hashlib.sha256(b).hexdigest()[:16]
            except Exception as e:
                rec["image"]["read_error"] = str(e)[:120]
        if ctx:
            rec["ctx"] = {k: ctx.get(k) for k in ("uid", "name", "thread", "trace_id", "query")
                          if ctx.get(k) is not None}
        if extra:
            rec["extra"] = extra
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        newly = not os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(line)
        if newly:
            try:
                os.chmod(LEDGER, 0o600)         # 中身は社の情報ゆえ本人のみ
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[cloud_ledger] 記録に失敗: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def read(days=7):
    """直近days日の記録を古い順で返す。帳簿が無ければ [](=ゼロ)。"""
    if not os.path.exists(LEDGER):
        return []
    cut = time.time() - days * 86400
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if float(d.get("ts", 0)) >= cut:
                out.append(d)
    return out


def summarize(days=7):
    """頻度の要約。日別 × 出口 × 人。"""
    recs = read(days)
    by_day, by_door, by_uid, chars = {}, {}, {}, 0
    for d in recs:
        day = str(d.get("at", ""))[:10]
        by_day[day] = by_day.get(day, 0) + 1
        by_door[d.get("door", "?")] = by_door.get(d.get("door", "?"), 0) + 1
        uid = str((d.get("ctx") or {}).get("uid") or "-")
        by_uid[uid] = by_uid.get(uid, 0) + 1
        chars += int(d.get("prompt_chars") or 0)
    return {"days": days, "n": len(recs), "prompt_chars_total": chars,
            "by_day": dict(sorted(by_day.items())), "by_door": by_door, "by_uid": by_uid,
            "ledger": LEDGER, "exists": os.path.exists(LEDGER)}


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="雲へ出た内容の帳簿")
    ap.add_argument("cmd", choices=["report", "list"])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--door", default=None)
    ap.add_argument("--full", action="store_true", help="本文を截らず全部出す")
    a = ap.parse_args()
    if a.cmd == "report":
        s = summarize(a.days)
        if not s["exists"]:
            print(f"帳簿はまだ在りませぬ（雲へ一度も出ておらぬ）: {s['ledger']}")
            return
        print(f"■ 雲の使用 直近{s['days']}日: {s['n']}件 / 送出 {s['prompt_chars_total']:,}文字")
        print("  日別:", s["by_day"] or "なし")
        print("  出口:", s["by_door"] or "なし")
        print("  人  :", s["by_uid"] or "なし")
        print(f"  帳簿: {s['ledger']}")
        return
    recs = read(a.days)
    if a.door:
        recs = [d for d in recs if d.get("door") == a.door]
    if not recs:
        print(f"直近{a.days}日、該当なし（0件。帳簿の不在とは別）")
        return
    for d in recs:
        c = d.get("ctx") or {}
        print(f"\n── {d.get('at')} [{d.get('door')}] {d.get('model')} "
              f"{d.get('outcome')} {d.get('dur_sec')}s uid={c.get('uid', '-')} "
              f"送{d.get('prompt_chars')}字/受{d.get('resp_chars')}字"
              + ("  ★截あり" if d.get("prompt_truncated") or d.get("resp_truncated") else ""))
        if c.get("query"):
            print(f"   発話: {c['query'][:120]}")
        if d.get("image"):
            print(f"   画像: {d['image'].get('path')} ({d['image'].get('bytes')}B)")
        body = d.get("prompt") or ""
        resp = d.get("response") or ""
        if not a.full:
            body, resp = body[:400], resp[:400]
        print(f"   送出> {body}")
        print(f"   受信> {resp}")


if __name__ == "__main__":
    _cli()
