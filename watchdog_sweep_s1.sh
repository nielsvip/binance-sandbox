#!/bin/bash
# watchdog_sweep_s1.sh — every 5min: keep BOTH crypto AND tradier backtest_v8_sweep alive.
# 2026-05-09 v16: count_tradier_sweep() now matches --mode tradier (any tier) instead of
# tradier_param_hunt only. Fix: when tradier_grtf7_hunt runs, watchdog correctly sees it
# as "tradier running" and does not launch crypto concurrently. Also disabled competing
# watchdog_sweep_s1_tradier.sh from S1 crontab (was launching rogue sweeps with wrong syms).
# 2026-05-08 v12: added --mem-throttle-pct 70 to tradier sweep to prevent OOM kills when
# profiler + crypto + tradier all run concurrently (each engine peaks ~10GB on 30GB server).
# 2026-05-09 v13: reduced crypto to 4 syms (was 8). Root cause of rc=-9 OOM kills confirmed:
# concurrent tradier engine (~4-6GB) + 8-sym crypto final-aggregation spike (~11-12GB) exceeds
# 18GB available. 4 syms halves the footprint (~5-6GB peak) and survives alongside tradier.
# 2026-05-09 v14: STRICT SERIALIZATION — 4-sym crypto STILL OOMs at 38min with concurrent
# tradier. Root cause: combined peak exceeds 26GB available. Fix: only one engine runs at a
# time. Tradier is priority. State file /home/niels/logs/sweep_next_mode tracks alternation.
# When both dead: check state file, launch whichever is "next" (default=tradier). The other
# waits. Natural alternation: tradier full grid (~11h) → crypto full grid → tradier → ...
# 2026-05-09 v15: tradier tier changed to tradier_grtf7_hunt (7-indicator GR_HTF gate:
# WT+RSI+MFI+DC+BB+RVOL+stoch_K, 5×6 grid + 3×5 exit grid + 6 ENTRY_THR_0 combos).
# golden_rule_htf.py updated to score 7 indicators per TF.
# Crypto: 4 USDC syms, start=2026-01-01. Tradier: 20 liquid stocks, start=2026-01-01.
LOG=/home/niels/logs/watchdog_sweep_s1.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python
STATE=/home/niels/logs/sweep_next_mode  # contains "crypto" or "tradier" (which runs NEXT)

CORE4_CRYPTO=BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC
CORE20_TRADIER=AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS

count_crypto_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*system_combo" | grep python | wc -l | tr -d '[:space:]'
}
count_tradier_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*--mode tradier" | grep python | wc -l | tr -d '[:space:]'
}
count_crypto_promoter() {
    ps aux | grep "[r]ate_filter_promoter.*--mode crypto" | grep python | wc -l | tr -d '[:space:]'
}

NC=$(count_crypto_sweep)
NT=$(count_tradier_sweep)
NEXT=$(cat "$STATE" 2>/dev/null || echo "tradier")

# -- Part 1: crypto backtest_v8_sweep system_combo (4 USDC syms, 2026-01-01) --
if [ "$NC" -lt 1 ]; then
    if [ "$NT" -gt 0 ]; then
        # Tradier is running — skip crypto (serialize, tradier priority)
        echo "[$TS] crypto dead — tradier running (procs=$NT), deferring crypto to avoid OOM" >> "$LOG"
    elif [ "$NEXT" = "crypto" ]; then
        # Both dead AND it's crypto's turn
        echo "[$TS] crypto system_combo dead — it's crypto's turn, relaunching (4 syms, 2026-01-01, timeout=5400)" >> "$LOG"
        TS2=$(date +%Y%m%d_%H%M)
        cd "$DIR"
        nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
            --mode crypto --account ang \
            --start 2026-01-01 \
            --symbols "$CORE4_CRYPTO" \
            --tier system_combo \
            --workers 1 \
            --timeout 5400 \
            --mem-throttle-pct 85 \
            > ~/logs/bt_sweep_crypto_4sym_${TS2}.log 2>&1 < /dev/null & disown
        sleep 5
        echo "[$TS] post-relaunch crypto procs=$(count_crypto_sweep)" >> "$LOG"
        echo "tradier" > "$STATE"  # tradier goes next after crypto finishes
    else
        # Both dead but it's tradier's turn — skip crypto, tradier section will launch tradier
        echo "[$TS] crypto dead — tradier's turn (NEXT=$NEXT), waiting for tradier to run first" >> "$LOG"
    fi
else
    echo "[$TS] crypto sweep running (procs=$NC) -- ok" >> "$LOG"
fi

# -- Part 2: tradier backtest_v8_sweep tradier_param_hunt (20 stocks, 2026-01-01) --
# Recheck live counts after potential crypto launch above
NT=$(count_tradier_sweep)
NC=$(count_crypto_sweep)
NEXT=$(cat "$STATE" 2>/dev/null || echo "tradier")

if [ "$NT" -lt 1 ]; then
    if [ "$NC" -gt 0 ]; then
        # Crypto is running — skip tradier (serialize)
        echo "[$TS] tradier dead — crypto running (procs=$NC), deferring tradier to avoid OOM" >> "$LOG"
    elif [ "$NEXT" = "tradier" ]; then
        # Both dead AND it's tradier's turn
        echo "[$TS] tradier tradier_param_hunt dead — it's tradier's turn, relaunching (20 stocks, 2026-01-01, timeout=5400)" >> "$LOG"
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
        echo "[$TS] post-relaunch tradier procs=$(count_tradier_sweep)" >> "$LOG"
        echo "crypto" > "$STATE"  # crypto goes next after tradier finishes
    else
        # Both dead but it's crypto's turn — crypto section should have handled it
        echo "[$TS] tradier dead — crypto's turn (NEXT=$NEXT), crypto section handles" >> "$LOG"
    fi
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
