#!/usr/bin/env bash
# Casper: nibu ch を直接ポーリングし、Elvis(他者)の新着回答を捕捉したら exit。
# listener は CMD ch / Lord発 のみ捕捉するため、bot間 nibu ch 用の自前監視。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
set -a; source "$SCRIPT_DIR/.env"; set +a

CH="1509117294515847289"          # nibu ch
AFTER="1517010007755980830"       # z8a GPU/LLM 照会 以降
ELVIS="1508761826790477946"       # Elvis_Shogun_Bot
ME="1503612248466264084"          # 1st shogun (我宛メンション判定用)
OUT="$SCRIPT_DIR/projects/casper/docs/elvis_llm_reply.txt"
INTERVAL=30
MAX_LOOPS=240                      # 30s*240 = 120分

for i in $(seq 1 "$MAX_LOOPS"); do
  RESP=$(curl -s -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
    "https://discord.com/api/v10/channels/${CH}/messages?after=${AFTER}&limit=50" || echo "[]")
  HIT=$(printf '%s' "$RESP" | python3 -c "
import sys,json,re
try: d=json.load(sys.stdin)
except Exception: d=[]
elvis='${ELVIS}'; me='${ME}'
def relevant(m):
    if m.get('author',{}).get('id')!=elvis: return False
    c=m.get('content','')
    return any(u.get('id')==me for u in m.get('mentions',[])) or me in c
new=[m for m in d if relevant(m)]
new.sort(key=lambda m: int(m['id']))
if new:
    for m in new:
        print('---', m['author'].get('username'), m['author']['id'], m['id'])
        print(m.get('content',''))
" )
  if [ -n "$HIT" ]; then
    { echo "captured_at_loop: $i"; echo "$HIT"; } > "$OUT"
    echo "ELVIS_REPLY_CAPTURED"
    echo "$HIT"
    exit 0
  fi
  sleep "$INTERVAL"
done
echo "POLL_TIMEOUT_NO_REPLY"
exit 2
