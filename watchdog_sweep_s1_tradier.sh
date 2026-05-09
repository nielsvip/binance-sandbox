#!/bin/bash
# watchdog_sweep_s1_tradier.sh — TRADIER-ONLY watchdog for S1.
# 2026-05-09: Per USER mandate, S2 destroyed; S1 alone runs both crypto & tradier
# sweeps. This watchdog is the tradier half of the pair. Cooperates with
# watchdog_sweep_s1.sh (which now manages crypto only when run with WATCHDOG_MODE=crypto)
# via shared state file /home/niels/logs/sweep_next_mode.
#
# Strict serialization (RAM constraint: ~10.8GB/worker peak NPZ on 31GB box):
#   - If crypto sweep is running: skip (defer to crypto first)
#   - If both dead AND state==tradier: launch
#   - If both dead AND state==crypto: skip (crypto watchdog handles its turn)
#
# After tradier launches: state flipped to "crypto" (so crypto goes next).
# When tradier finishes naturally, this watchdog detects it dead next cron tick.
#
# Cron suggestion: */7 * * * * /home/niels/binance-sandbox/watchdog_sweep_s1_tradier.sh
# (offset +2 from crypto watchdog's */5 to avoid race on same minute).

LOG=/home/niels/logs/watchdog_sweep_s1_tradier.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python
STATE=/home/niels/logs/sweep_next_mode

CORE20_TRADIER=AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS

count_crypto_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*system_combo" | grep python | wc -l | tr -d '[:space:]'
}
count_tradier_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*tradier" | grep python | wc -l | tr -d '[:space:]'
}

NC=$(count_crypto_sweep)
NT=$(count_tradier_sweep)
NEXT=$(cat "$STATE" 2>/dev/null || echo "tradier")

if [ "$NT" -ge 1 ]; then
    echo "[$TS] tradier sweep running (procs=$NT) -- ok" >> "$LOG"
    exit 0
fi

if [ "$NC" -gt 0 ]; then
    echo "[$TS] tradier dead -- crypto running (procs=$NC), defer (RAM serialization)" >> "$LOG"
    exit 0
fi

if [ "$NEXT" != "tradier" ]; then
    echo "[$TS] tradier dead -- crypto's turn (NEXT=$NEXT), let crypto watchdog handle" >> "$LOG"
    exit 0
fi

echo "[$TS] tradier sweep dead, both clear, tradier's turn -- launching tradier_grtf7_hunt" >> "$LOG"
TS2=$(date +%Y%m%d_%H%M)
cd "$DIR"
nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
    --mode tradier --account trb \
    --start 2026-01-01 \
    --symbols "$CORE20_TRADIER" \
    --tier tradier_grtf7_hunt \
    --workers 1 \
    --timeout 5400 \
    --mem-throttle-pct 70 \
    > ~/logs/bt_sweep_tradier_20sym_${TS2}.log 2>&1 < /dev/null & disown
sleep 5
NEW_NT=$(count_tradier_sweep)
echo "[$TS] post-launch tradier procs=$NEW_NT" >> "$LOG"
echo "crypto" > "$STATE"
