#!/bin/bash
# v15_preopen_verify.sh — S1 cron 12:35 UTC daily. Read-only gate: chain OK,
# s6 canonical, loops alive, scheduler ticking, deploys intact. Loud on failure.
# USER 2026-10-08: everything live again before market open, nothing undone.
FAIL=0
say() { [ "$2" = "FAIL" ] && FAIL=1; echo "[$2] $1"; }
cd ~/binance-sandbox || { echo "[FAIL] cd sandbox"; exit 1; }
D=$(date -u +%Y%m%d)
[ -f "data/daily_chain/$D.json" ] && ST=$(python3 -c "import json;print(json.load(open('data/daily_chain/$D.json')).get('status'))" 2>/dev/null) || ST="MISSING"
[ "$ST" = "OK" ] || [ "$ST" = "ok" ] && say "chain $D $ST" OK || say "chain $D $ST" FAIL
C1=$(md5sum config.py | cut -c1-8); C6=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=10 niels@10.0.0.6 "md5sum ~/binance-sandbox/config.py" 2>/dev/null | cut -c1-8)
KV1=$(.venv/bin/python -c "import per_sym_store as pss,json,hashlib;print(hashlib.md5(json.dumps(pss.kv_get(pss.KV_CAT_SIDE_DEFAULTS_4),sort_keys=True).encode()).hexdigest()[:8])" 2>/dev/null)
[ "$C1" = "$C6" ] && [ -n "$C6" ] && say "s6 config==S1 $C1" OK || say "s6 config $C6 vs S1 $C1" FAIL
G1=$(md5sum data/encyclopedia_v2/graph.json | cut -c1-8)
for H in niels@10.0.0.4 niels@10.0.0.5 niels@10.0.0.6; do
  GH=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=10 $H "md5sum ~/binance-sandbox/data/encyclopedia_v2/graph.json" 2>/dev/null | cut -c1-8)
  [ "$GH" = "$G1" ] && say "graph $H $GH" OK || say "graph $H $GH vs $G1" FAIL
done
for F in tools/v15_graph_search.py tools/v15_filter_flip.py tools/v15_filter_pair.py tools/v15_lever_inventory.py tools/v15_knowledge_graph.py tools/v15_encyclopedia_cycle.sh; do
  [ -f "$F" ] && say "$F present" OK || say "$F MISSING" FAIL
done
S6P=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=10 niels@10.0.0.6 "pgrep -c -f 'filter_flip|filter_pair'; grep -h 'QUEUE DONE' /tmp/s6_flip_A.log /tmp/s6_flip_B.log /tmp/s6_pair_A.log /tmp/s6_pair_B.log 2>/dev/null | wc -l" 2>/dev/null | tr '\n' ' ')
case "$S6P" in *[1-9]*) say "s6 flips/pairs $S6P" OK;; *) say "s6 flips/pairs idle-none-done" FAIL;; esac
LAST=$(tail -1 /tmp/v15_fleet_sched.log 2>/dev/null | grep -o '"now": "[^"]*"' | head -1)
[ -n "$LAST" ] && say "sched tick $LAST" OK || say "sched log empty" FAIL
grep -q V15_GS_FLEET=1 <(crontab -l 2>/dev/null) && say "GS fleet flag" OK || say "GS fleet flag OFF" FAIL
grep -q V15_ENCY_CYCLE <(crontab -l 2>/dev/null) && say "ency cycle cron" OK || say "ency cycle cron GONE" FAIL
[ $FAIL -eq 0 ] && echo "PREOPEN S1 ALL GREEN $(date -u +%FT%TZ)" || echo "PREOPEN S1 FAILURES $(date -u +%FT%TZ)"
exit $FAIL
