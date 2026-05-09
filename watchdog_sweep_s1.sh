#!/bin/bash
# watchdog_sweep_s1.sh — every 5min: keep BOTH crypto AND tradier backtest_v8_sweep alive.
# 2026-05-08 v12: added --mem-throttle-pct 70 to tradier sweep to prevent OOM kills when
# profiler + crypto + tradier all run concurrently (each engine peaks ~10GB on 30GB server).
# Crypto: 12 USDC syms, start=2026-01-01. Tradier: 20 liquid stocks, start=2026-01-01.
LOG=/home/niels/logs/watchdog_sweep_s1.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python

CORE8_CRYPTO=BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,LINKUSDC,LTCUSDC,UNIUSDC,DOGEUSDC,CRVUSDC
CORE20_TRADIER=AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS

count_crypto_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*system_combo" | grep python | wc -l | tr -d '[:space:]'
}
count_tradier_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*tradier_param_hunt" | grep python | wc -l | tr -d '[:space:]'
}
count_crypto_promoter() {
    ps aux | grep "[r]ate_filter_promoter.*--mode crypto" | grep python | wc -l | tr -d '[:space:]'
}

# -- Part 1: crypto backtest_v8_sweep system_combo (12 USDC syms, 2026-01-01) --
NC=$(count_crypto_sweep)
if [ "$NC" -lt 1 ]; then
    echo "[$TS] crypto system_combo dead -- relaunching (8 syms, 2026-01-01, timeout=3600)" >> "$LOG"
    TS2=$(date +%Y%m%d_%H%M)
    cd "$DIR"
    nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
        --mode crypto --account ang \
        --start 2026-04-09 \
        --symbols "$CORE8_CRYPTO" \
        --tier system_combo \
        --workers 1 \
        --timeout 3600 \
        --mem-throttle-pct 85 \
        > ~/logs/bt_sweep_crypto_12sym_${TS2}.log 2>&1 < /dev/null & disown
    sleep 5
    echo "[$TS] post-relaunch crypto procs=$(count_crypto_sweep)" >> "$LOG"
else
    echo "[$TS] crypto sweep running (procs=$NC) -- ok" >> "$LOG"
fi

# -- Part 2: tradier backtest_v8_sweep tradier_param_hunt (20 stocks, 2026-01-01) --
# 2026-05-08: changed from 2024-01-01 to 2026-01-01 so each variant completes in ~3-7 min
# (vs 13-29 min with 2-yr range which gets OOM-killed before V8_RESULT prints).
NT=$(count_tradier_sweep)
if [ "$NT" -lt 1 ]; then
    echo "[$TS] tradier tradier_param_hunt dead -- relaunching (20 stocks, 2026-01-01, timeout=3600)" >> "$LOG"
    TS2=$(date +%Y%m%d_%H%M)
    cd "$DIR"
    nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
        --mode tradier --account trb \
        --start 2026-04-09 \
        --symbols "$CORE20_TRADIER" \
        --tier tradier_param_hunt \
        --workers 1 \
        --timeout 3600 \
        --mem-throttle-pct 70 \
        > ~/logs/bt_sweep_tradier_20sym_${TS2}.log 2>&1 < /dev/null & disown
    sleep 5
    echo "[$TS] post-relaunch tradier procs=$(count_tradier_sweep)" >> "$LOG"
else
    echo "[$TS] tradier sweep running (procs=$NT) -- ok" >> "$LOG"
fi

# -- Part 3: kill competing autonomous_search/canonical processes --
kill -9 $(ps aux | grep "[a]utonomous_search" | grep python | awk '{print $2}') 2>/dev/null
kill -9 $(ps aux | grep "[c]anonical_crypto_50sym\|[c]anonical_tradier_100sym" | grep -E "bash|sh" | awk '{print $2}') 2>/dev/null

# -- Part 4: rate_filter_promoter daemon (crypto mode) --
NP=$(count_crypto_promoter)
if [ "$NP" -lt 1 ]; then
    echo "[$TS] rate_filter_promoter crypto dead -- relaunching" >> "$LOG"
    cd "$DIR"
    nohup "$PYTHON" -u rate_filter_promoter.py --mode crypto --poll-s 30 \
        > ~/logs/rate_filter_promoter_crypto.log 2>&1 < /dev/null & disown
    sleep 2
    echo "[$TS] post-relaunch rate_filter_promoter procs=$(count_crypto_promoter)" >> "$LOG"
fi
