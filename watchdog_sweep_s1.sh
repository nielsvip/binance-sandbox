#!/bin/bash
# watchdog_sweep_s1.sh — every 5min: keep BOTH crypto AND tradier backtest_v8_sweep alive.
# 2026-05-10 v17: MEMORY GUARD — refuse to launch if available RAM < MEM_GUARD_MB (18000).
# Root cause of cascading OOM (02:10 UTC): count_crypto_sweep() returned 0 between engine
# variants (race window when one variant finishes and next hasn't spawned yet), causing Part 2
# to see NC=0 and launch tradier alongside active crypto engine. Fix: (1) memory guard at top
# exits if available < 18GB; (2) Part 2 uses snapshot NC/NT from top of script (no recheck).
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
MEM_GUARD_MB=18000  # refuse to launch if available RAM < this (each engine peaks ~10GB)

CORE4_CRYPTO=BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC
CORE20_TRADIER=AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS

# -- Memory guard: exit early if not enough RAM to safely launch another engine --
AVAIL_MB=$(free -m | awk '/^Mem:/{print $7}')
if [ "$AVAIL_MB" -lt "$MEM_GUARD_MB" ]; then
    echo "[$TS] MEM_GUARD: available=${AVAIL_MB}MB < ${MEM_GUARD_MB}MB — skipping all launches" >> "$LOG"
    exit 0
fi

count_crypto_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*system_combo" | grep python | wc -l | tr -d '[:space:]'
}
count_tradier_sweep() {
    ps aux | grep "[b]acktest_v8_sweep.*--mode tradier" | grep python | wc -l | tr -d '[:space:]'
}
count_crypto_promoter() {
    ps aux | grep "[r]ate_filter_promoter.*--mode crypto" | grep python | wc -l | tr -d '[:space:]'
}

count_coordinator() {
    ps aux | grep "[s]weep_coordinator" | grep python | wc -l | tr -d '[:space:]'
}
count_coordinator_tradier() {
    ps aux | grep "[s]weep_coordinator.*--mode tradier" | grep python | wc -l | tr -d '[:space:]'
}

NC=$(count_crypto_sweep)
NT=$(count_tradier_sweep)
NCOORD=$(count_coordinator)
NEXT=$(cat "$STATE" 2>/dev/null || echo "tradier")

# -- Parts 1+2: ablation sweeps skipped if coordinator is running (coordinator owns backtest_v8_engine) --
if [ "$NCOORD" -gt 0 ]; then
    echo "[$TS] sweep_coordinator running (procs=$NCOORD) — skipping ablation Parts 1+2" >> "$LOG"
else
    # -- Part 1: crypto backtest_v8_sweep system_combo (4 USDC syms, 2026-01-01) --
    if [ "$NC" -lt 1 ]; then
        if [ "$NT" -gt 0 ]; then
            echo "[$TS] crypto dead — tradier running (procs=$NT), deferring crypto to avoid OOM" >> "$LOG"
        elif [ "$NEXT" = "crypto" ]; then
            echo "[$TS] crypto system_combo dead — it's crypto's turn, relaunching (4 syms, 2026-01-01, timeout=5400)" >> "$LOG"
            TS2=$(date +%Y%m%d_%H%M)
            cd "$DIR"
            nohup env V8_RATE_GUARD_DISABLED=1 V8_DISABLE_RELAXED_SRS=1 "$PYTHON" backtest_v8_sweep.py \
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
            echo "tradier" > "$STATE"
        else
            echo "[$TS] crypto dead — tradier's turn (NEXT=$NEXT), waiting for tradier to run first" >> "$LOG"
        fi
    else
        echo "[$TS] crypto sweep running (procs=$NC) -- ok" >> "$LOG"
    fi

    # -- Part 2: tradier backtest_v8_sweep tradier_param_hunt (20 stocks, 2026-01-01) --
    NT=$(count_tradier_sweep)
    NEXT=$(cat "$STATE" 2>/dev/null || echo "tradier")
    if [ "$NT" -lt 1 ]; then
        if [ "$NC" -gt 0 ]; then
            echo "[$TS] tradier dead — crypto running (procs=$NC), deferring tradier to avoid OOM" >> "$LOG"
        elif [ "$NEXT" = "tradier" ]; then
            echo "[$TS] tradier tradier_grtf7_hunt_resume dead — relaunching (20 stocks, 2026-01-01, mem-throttle 70)" >> "$LOG"
            TS2=$(date +%Y%m%d_%H%M)
            cd "$DIR"
            nohup env V8_RATE_GUARD_DISABLED=1 V8_DISABLE_RELAXED_SRS=1 "$PYTHON" backtest_v8_sweep.py \
                --mode tradier --account trb \
                --start 2026-01-01 \
                --symbols "$CORE20_TRADIER" \
                --tier tradier_grtf7_hunt_resume \
                --workers 1 \
                --timeout 5400 \
                --mem-throttle-pct 85 \
                > ~/logs/bt_sweep_tradier_grtf7_resume_${TS2}.log 2>&1 < /dev/null & disown
            sleep 5
            echo "[$TS] post-relaunch tradier procs=$(count_tradier_sweep)" >> "$LOG"
            echo "crypto" > "$STATE"
        else
            echo "[$TS] tradier dead — crypto's turn (NEXT=$NEXT), crypto section handles" >> "$LOG"
        fi
    else
        echo "[$TS] tradier sweep running (procs=$NT) -- ok" >> "$LOG"
    fi
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

# -- Part 5: sweep_coordinator — 24/7 prioritized queue runner (vectorized mode) --
# Coordinator runs backtest_v8_engine directly (not backtest_v8_sweep).
# When coordinator is running, Parts 1+2 ablation sweeps are skipped.
# Two coordinators run: crypto (start=2022-01-01 bear+bull) + tradier (start=2024-01-01).
# Each runs one engine at a time; combined peak ~15GB — safe on 31GB server.
NCOORD=$(count_coordinator)
NCOORD_T=$(count_coordinator_tradier)
if [ "$NCOORD" -lt 1 ] && [ "$NC" -lt 1 ] && [ "$NT" -lt 1 ]; then
    echo "[$TS] sweep_coordinator dead and ablation sweeps idle — relaunching crypto coordinator" >> "$LOG"
    TS2=$(date +%Y%m%d_%H%M)
    cd "$DIR"
    nohup "$PYTHON" sweep_coordinator.py \
        --mode crypto --account ang \
        --start 2022-01-01 \
        --symbols "$CORE4_CRYPTO" \
        --hang-timeout 120000 \
        --mem-throttle 80 \
        > ~/logs/sweep_coordinator_crypto_${TS2}.log 2>&1 < /dev/null & disown
    sleep 5
    NCOORD=$(count_coordinator)
    echo "[$TS] post-relaunch coordinator procs=$NCOORD" >> "$LOG"
elif [ "$NCOORD" -gt 0 ]; then
    echo "[$TS] sweep_coordinator running (procs=$NCOORD) -- ok" >> "$LOG"
else
    echo "[$TS] sweep_coordinator idle (ablation sweeps busy: crypto=$NC tradier=$NT)" >> "$LOG"
fi

# -- Part 6: tradier sweep_coordinator (runs alongside crypto coordinator) --
CORE7_TRADIER=AAPL,MSFT,NVDA,TSLA,META,AMD,AMZN
if [ "$NCOORD_T" -lt 1 ] && [ "$NCOORD" -gt 0 ]; then
    AVAIL_MB2=$(free -m | awk '/^Mem:/{print $7}')
    if [ "$AVAIL_MB2" -gt 12000 ]; then
        echo "[$TS] tradier coordinator dead (crypto running, mem ok ${AVAIL_MB2}MB) — relaunching tradier coordinator" >> "$LOG"
        TS2=$(date +%Y%m%d_%H%M)
        cd "$DIR"
        nohup "$PYTHON" sweep_coordinator.py \
            --mode tradier --account trb \
            --start 2024-01-01 \
            --symbols "$CORE7_TRADIER" \
            --hang-timeout 120000 \
            --mem-throttle 80 \
            > ~/logs/sweep_coordinator_tradier_${TS2}.log 2>&1 < /dev/null & disown
        sleep 5
        echo "[$TS] post-relaunch tradier coordinator procs=$(count_coordinator_tradier)" >> "$LOG"
    else
        echo "[$TS] tradier coordinator skipped — low mem ${AVAIL_MB2}MB" >> "$LOG"
    fi
elif [ "$NCOORD_T" -gt 0 ]; then
    echo "[$TS] tradier coordinator running (procs=$NCOORD_T) -- ok" >> "$LOG"
fi
