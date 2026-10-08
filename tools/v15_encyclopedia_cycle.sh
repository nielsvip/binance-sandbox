#!/bin/bash
# v15_encyclopedia_cycle — S1 cron every 30min. Close the learning loop: every
# new calculation (pilot cells via avg_delta, GS verdicts, flip ledgers, lever
# inventory) is mined into data/encyclopedia_v2/graph.json fault priors, which
# the next GS jobs read at startup. No compute is dumb twice.
# USER 2026-10-08: agents must use every result to learn switch interrelations.
set -u
cd ~/binance-sandbox || exit 1
LOG=/tmp/ency_cycle.log
RUNS=data/encyclopedia_v2/runs
ABL=data/encyclopedia_v2/ablation
mkdir -p "$RUNS" "$ABL"
echo "[$(date -u +%FT%TZ)] ency cycle start" >> "$LOG"
# 1. fresh lever inventory over s6 results (thoughts + flips) — feeds fault_index
.venv/bin/python tools/v15_lever_inventory.py data/s6_365 >> "$LOG" 2>&1
# 2. gather fresh GS evidence (current NPZ generation wins at >=2 syms)
find ~/chain/gs_a* data/s6_365 -name '*.gs.json' -mmin -180 -exec cp -u {} "$RUNS/" \; 2>/dev/null
find ~/chain/gs_a*/ablation data/s6_365 -name '*.json' -mmin -180 -exec cp -u {} "$ABL/" \; 2>/dev/null
# 3. rebuild (atomic swap inside the builder)
if ! .venv/bin/python tools/v15_knowledge_graph.py --runs-dir "$RUNS" --ablation-dir "$ABL" >> "$LOG" 2>&1; then
  echo "[$(date -u +%FT%TZ)] BUILD FAILED, graph untouched" >> "$LOG"
  exit 1
fi
# 4. validate before fleet push (fail-closed: never push a degenerate graph)
OK=$(.venv/bin/python -c "
import json; g=json.load(open('data/encyclopedia_v2/graph.json'))
n=len(g.get('nodes',{})); fi=sum(len(v) for c in g.get('fault_index',{}).values() for v in c.values())
print('OK' if n>3000 and fi>50 else 'DEGENERATE')" 2>/dev/null)
if [ "$OK" != "OK" ]; then
  echo "[$(date -u +%FT%TZ)] VALIDATE FAILED, graph not pushed" >> "$LOG"
  exit 1
fi
# 5. fleet push (GS reads graph at job start; readers never block)
for H in niels@10.0.0.4 niels@10.0.0.5 niels@10.0.0.6; do  # IPs, never aliases (S1 alias s5 once pointed at .6)
  rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15" data/encyclopedia_v2/graph.json "$H:~/binance-sandbox/data/encyclopedia_v2/graph.json" >> "$LOG" 2>&1 || echo "push $H FAILED" >> "$LOG"
done
echo "[$(date -u +%FT%TZ)] ency cycle done $(md5sum data/encyclopedia_v2/graph.json | cut -c1-8)" >> "$LOG"
