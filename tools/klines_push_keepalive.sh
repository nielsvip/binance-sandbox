#!/bin/bash
# klines_push_keepalive — keep the Mac->S1 market-data/klines pusher alive, and SAY SO when it was not.
#
# WHY. rsync_market_data_to_server_continuous.sh is a `while true` loop that nothing supervised: no launchd job
# (com.binance.klines-sync runs rsync_klines_from_server.sh, a PULL), no cron, no keepalive. It was killed at
# 2026-10-19 22:53:36Z — its log ends `rsync error: received SIGINT, SIGTERM, or SIGHUP (code 20)` — and stayed
# dead for 16 hours. Everything downstream of it stopped with it, measured 2026-10-20 14:5xZ:
#
#   Mac  klines_cache/tradier/SAP_15m.json        last bar 10-20 14:45Z   (current)
#   S1   klines_cache_macbook/tradier/SAP_15m.json last bar 10-19 20:00Z  (the dead pusher's last pass)
#   S1   klines_cache/tradier/SAP_15m.json         last bar 10-19 20:00Z  (klines_append had nothing to append)
#   S1   backtest_v8/indicators/SAP.npz            74 bars / 18.5 h behind
#
# and from there: the JIT tail refresh reads S1's LOCAL cache for stocks (fetch_fresh is REST for crypto only), so
# no stock NPZ could advance past yesterday's close; npz_bar_ingest refused 99 of 207 staged bars with
# HOLE_LEFT_TO_REFRESHER, leaving the gap to the one component that had no source for it; 128 of 221 stock NPZs sat
# at yesterday's close; and the dispatch gate then correctly refused every one of them. One unsupervised loop dying
# quietly took the whole stock backtest pipeline off fresh data for a day.
#
# This restarts it and records the outage in data/reports/klines_push_keepalive.json so the gap is never silent.
set -u
ROOT=/Users/niels/Documents/binance
SCRIPT=$ROOT/rsync_market_data_to_server_continuous.sh
LOG=/Users/niels/logs/rsync_market_data_to_server.log
STATE=$ROOT/data/reports/klines_push_keepalive.json
NOW=$(date -u +%FT%TZ)
cd "$ROOT" || exit 1
RUNNING=$(pgrep -f "rsync_market_data_to_server_continuous" | wc -l | tr -d ' ')
# More than one instance pushes the same 850 MB twice; keep the oldest and stop the rest.
if [ "$RUNNING" -gt 1 ]; then
    KEEP=$(ps -eo pid,etime,command | grep "rsync_market_data_to_server_continuous" | grep -v grep | sort -k2 -r | awk '{print $1; exit}')
    for p in $(pgrep -f "rsync_market_data_to_server_continuous"); do
        [ "$p" != "$KEEP" ] && kill "$p" 2>/dev/null
    done
    echo "[$NOW] [klines-push-keepalive] $RUNNING instances — kept $KEEP, stopped the rest"
    RUNNING=1
fi
ACTION=none
if [ "$RUNNING" -lt 1 ]; then
    ACTION=restarted
    echo "[$NOW] [klines-push-keepalive] NOT RUNNING — restarting (stock NPZs cannot advance without it)"
    nohup /bin/bash "$SCRIPT" >> "$LOG" 2>&1 < /dev/null &
    disown 2>/dev/null || true
    sleep 5
    RUNNING=$(pgrep -f "rsync_market_data_to_server_continuous" | wc -l | tr -d ' ')
fi
# The staged tail is the honest health signal: the loop can be alive and still not be delivering.
STAGED=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=15 -o ControlPath=none "${KLINES_PUSH_HOST:-s1-pub}" \
    'f=~/binance-sandbox/klines_cache_macbook/tradier/SAP_15m.json; [ -f "$f" ] && stat -c %Y "$f" || echo 0' 2>/dev/null || echo 0)
AGE=$(( $(date -u +%s) - ${STAGED:-0} ))
mkdir -p "$(dirname "$STATE")"
printf '{"generated_utc":"%s","running":%s,"action":"%s","staged_sample":"klines_cache_macbook/tradier/SAP_15m.json","staged_age_s":%s}\n' \
    "$NOW" "${RUNNING:-0}" "$ACTION" "$AGE" > "$STATE"
if [ "${STAGED:-0}" = "0" ] || [ "$AGE" -gt "${KLINES_PUSH_MAX_AGE_S:-3600}" ]; then
    echo "[$NOW] [klines-push-keepalive] ALERT staged stock klines on S1 are ${AGE}s old (> ${KLINES_PUSH_MAX_AGE_S:-3600}s) — stock NPZs will stay behind"
fi
