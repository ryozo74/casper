#!/usr/bin/env python3
"""Casper モデル比較ベンチ: qwen3:14b / qwen3.6:27b / Sonnet × chat/teach/play。"""
import json, os, re, subprocess, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIGEST = open(os.path.join(HERE, "casper_context.md"), encoding="utf-8").read()[:3500]
import casper_endpoint as _ep                       # ★2026-08-31: 焼き付き既定を殺す(禁足席)
OLLAMA = _ep.gen_endpoint() + "/api/chat"
SCHED = ("メンバーA スケジュール 2026/6\n6/1 プロジェクトP MTG\n6/2-6/5 プロジェクトP 作業(確定)\n"
         "6/8 プロジェクトQ 打合せ(仮)\n6/10-6/20 プロジェクトQ レイアウト\n6/21以降 未定")

TASKS = {
    "chat": {"sys": DIGEST + "\n\nあなたはCasper。社内データに基づき具体的に答えよ。",
             "user": "ライティングやコンポジットが得意なメンバーを、根拠(スキル/担当PJ)つきで教えて。"},
    "teach": {"sys": DIGEST + "\n\nあなたは社内知識の解像度を上げる逆インタビュアー Casper。",
              "user": "知識の穴を埋める確認質問を1つだけ、選択式で作れ。形式厳守(他に何も書かない):\nQUESTION: <一文>\nCHOICES: 候補1 | 候補2 | 候補3 | その他"},
    "play": {"sys": "あなたは資料を読み取り理解するCasper。",
             "user": f"次の資料を読み、出力形式厳守(他に何も書かない):\nSUMMARY: <2〜4文>\nQUESTIONS: 質問1 | 質問2 | 質問3\n\n資料:\n{SCHED}"},
}


def run_ollama(model, sys_, user):
    body = json.dumps({"model": model, "stream": False, "think": False, "options": {"num_predict": 700},
                       "messages": [{"role": "system", "content": sys_},
                                    {"role": "user", "content": user}]}).encode()
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(OLLAMA, data=body,
                               headers={"Content-Type": "application/json"}), timeout=300)
    txt = json.load(r).get("message", {}).get("content", "")
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip(), round(time.time() - t, 1)


def run_cli(sys_, user):
    t = time.time()
    p = subprocess.run(["claude", "-p", "--model", "sonnet"], input=sys_ + "\n\n" + user,
                       capture_output=True, text=True, timeout=300, cwd="/tmp/casper_cli")
    return (p.stdout or "").strip() or ("(err " + (p.stderr or "")[:80] + ")"), round(time.time() - t, 1)


MODELS = [("qwen3:14b", "ollama"), ("qwen3.6:27b", "ollama"), ("Sonnet", "cli")]
out = {}
for mname, kind in MODELS:
    out[mname] = {}
    for tname, t in TASKS.items():
        try:
            txt, el = (run_ollama(mname, t["sys"], t["user"]) if kind == "ollama"
                       else run_cli(t["sys"], t["user"]))
        except Exception as e:
            txt, el = f"(error: {e})", 0
        out[mname][tname] = {"text": txt, "sec": el}
        print(f"[{mname}/{tname}] {el}s :: {txt[:90].replace(chr(10),' ')}")
json.dump(out, open("/tmp/casper_bench.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("===== BENCH DONE =====")
