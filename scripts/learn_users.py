#!/usr/bin/env python3
"""会話ログ → ユーザー理解の自動蓄積。
conversation_log.jsonl をユーザー(ukey)別に集計し、Opus(claude CLI 迂回)で
『関心・よく聞くこと・重視点・傾向』を蒸留 → vault/20_people/profile_<ukey>.md に追記更新。
会話するほど Casper のユーザー理解が深まるループ。

実行: python3 learn_users.py            (全ユーザー)
      python3 learn_users.py <ukey>     (指定ユーザーのみ)
冪等: 既存プロファイルがあれば『前回の理解』として渡し、新会話分で更新。
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONVO = os.path.join(HERE, "conversation_log.jsonl")
PEOPLE = os.path.join(HERE, "..", "vault", "20_people")
CLAUDE_BIN = os.environ.get("CASPER_CLAUDE_BIN", "claude")
MODEL = os.environ.get("CASPER_LEARN_MODEL", "opus")
MIN_TURNS = 4          # これ未満の会話しかないユーザーはスキップ


def load_by_user():
    rows = []
    if not os.path.exists(CONVO):
        return {}
    for ln in open(CONVO, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    by = collections.defaultdict(list)
    for r in rows:
        key = r.get("ukey") or (("u_" + r["uid"]) if r.get("uid") else None)
        if key and r.get("role") and r.get("content"):
            by[key].append(r)
    return by


def distill(ukey, rows, prev=""):
    convo = "\n".join(("殿: " if r["role"] == "user" else "Casper: ") + str(r["content"])[:400]
                      for r in rows[-120:])
    prompt = (
        "あなたはユーザー理解を深める分析役。以下はある社員と Casper(社内AI)の会話ログ。"
        "この人物の『①関心・よく尋ねる話題 ②仕事の重視点・価値観 ③コミュニケーションの癖・好み"
        "④Casperへの期待・使い方』を、会話から読み取れる範囲で簡潔に箇条書きで。"
        "憶測しすぎず、会話に根拠がある事項のみ。各項目1〜3点。\n"
        + (("\n## 前回までの理解(これを踏まえ更新):\n" + prev + "\n") if prev else "")
        + "\n## 会話ログ:\n" + convo
        + "\n\n出力は markdown 箇条書きのみ(前後の説明文不要)。")
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                           capture_output=True, text=True, timeout=300, cwd="/tmp")
        return (r.stdout or "").strip()
    except Exception as e:
        return f"(蒸留失敗: {e})"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    by = load_by_user()
    os.makedirs(PEOPLE, exist_ok=True)
    n = 0
    for ukey, rows in by.items():
        if only and ukey != only:
            continue
        users_turns = [r for r in rows if r.get("role") == "user"]
        if len(users_turns) < MIN_TURNS:
            print(f"skip {ukey} (発言{len(users_turns)}件・{MIN_TURNS}未満)")
            continue
        path = os.path.join(PEOPLE, f"profile_{ukey}.md")
        prev = ""
        if os.path.exists(path):
            t = open(path, encoding="utf-8").read()
            if "## Casper の理解" in t:
                prev = t.split("## Casper の理解", 1)[1].strip()
        understanding = distill(ukey, rows, prev)
        ident = rows[-1].get("email") or rows[-1].get("uid") or ukey
        note = (f"---\nname: ユーザープロファイル {ident}\ntype: user_profile\nukey: {ukey}\n"
                f"tags: [casper, user_profile]\n---\n\n# ユーザープロファイル — {ident}\n\n"
                f"会話から Casper が蓄積したユーザー理解(会話{len(users_turns)}発言時点)。"
                f"キーワード: ユーザー 好み 関心 傾向 {ident}\n\n"
                f"## Casper の理解\n{understanding}\n")
        open(path, "w", encoding="utf-8").write(note)
        print(f"OK {ukey} -> profile_{ukey}.md ({len(users_turns)}発言から蒸留)")
        n += 1
    print(f"=== 完了: {n} ユーザー更新 ===")


if __name__ == "__main__":
    main()
