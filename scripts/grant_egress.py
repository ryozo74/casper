#!/usr/bin/env python3
"""殿の egress 許可を .claude/settings.local.json へ冪等に追記するヘルパー。
殿御自身が `!python3 projects/casper/scripts/grant_egress.py` で実行する想定。
(agent による権限自己付与は分類器が禁止するため、実行は殿の手で。)
"""
import json, os

P = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".claude", "settings.local.json")
RULES = [
    "Bash(python3 projects/casper/scripts/test_llm_db_understanding.py --full --endpoint http://192.168.44.119:11434)",
    "Bash(python3 projects/casper/scripts/test_llm_db_understanding.py --endpoint http://192.168.44.119:11434 --full)",
    "Bash(curl http://192.168.44.119:11434/*)",
    "Bash(curl -s http://192.168.44.119:11434/*)",
]

d = json.load(open(P, encoding="utf-8"))
allow = d.setdefault("permissions", {}).setdefault("allow", [])
added = [r for r in RULES if r not in allow]
allow.extend(added)
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"added {len(added)} new rule(s); total allow rules now {len(allow)}")
for r in added:
    print("  +", r)
