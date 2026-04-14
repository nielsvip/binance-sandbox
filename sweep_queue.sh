#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# STRUCTURAL SWEEP — Tests architecture, not knobs
#
# The live system opened 15,000 hedge positions in 2 days.
# We need to know: which COMPONENTS make money, which destroy it.
#
# Each config enables/disables a major subsystem.
# Run ONE at a time, wait for RAM.
# ═══════════════════════════════════════════════════════════════════

PY="$HOME/miniconda3/envs/binance_env/bin/python3"
[ -f "$PY" ] || PY="$HOME/.conda/envs/binance_env/bin/python3"
BASE="$HOME/binance"
SANDBOX="$HOME/binance-sandbox"
RESULTS_DIR="$SANDBOX/backtest_v5/sweep_structural"
LOG="$HOME/sweep_structural.log"
MIN_FREE_MB=3000
MAX_SYMBOLS=30
START_DATE="2025-10-01"
CAPITAL=10000.0
MODE="crypto"

mkdir -p "$RESULTS_DIR/logs" "$RESULTS_DIR/overrides"

# ═══════════════════════════════════════════════════════════════════
# STRUCTURAL CONFIGS — Does the component HELP or HURT?
# ═══════════════════════════════════════════════════════════════════
configs=(
    # ── BASELINE: current production config ──
    "S00_BASELINE={}"

    # ── HEDGE SYSTEM: the #1 disaster source ──
    "S01_NO_HEDGE={\"HEDGE_MODE\":false,\"HEDGE_SAME_SYMBOL_ENABLED\":false,\"LOSS_EXIT_REQUIRES_HEDGE\":false,\"OBLIGATORY_HEDGE_PCT\":0.0}"
    "S02_HEDGE_BUT_NO_PROTECT={\"HEDGE_MODE\":true,\"HEDGE_SAME_SYMBOL_ENABLED\":false,\"OBLIGATORY_HEDGE_PCT\":0.0,\"HEDGE_TRIGGER_LOSS_PCT\":-99.0}"
    "S03_HEDGE_TIGHT={\"HEDGE_MODE\":true,\"HEDGE_OVERSIZE_RATIO\":0.5,\"HEDGE_MAX_RATIO\":0.5,\"HEDGE_TRIGGER_LOSS_PCT\":-5.0}"

    # ── REENTRY SYSTEM: 2000+ reentries/day ──
    "S04_NO_REENTRY={\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false}"
    "S05_REENTRY_SLOW={\"REENTRY_MANDATORY\":true,\"REENTRY_TIER2_MAX_MINUTES\":480.0,\"REENTRY_TIER2_MIN_MINUTES\":60.0}"
    "S06_REENTRY_NO_TIER2={\"REENTRY_MANDATORY\":true,\"REENTRY_TIER2_SIZE_MULT\":0.0}"

    # ── QUICK REDUCE: 7000-12000 reduces/day ──
    "S07_NO_QUICK_REDUCE={\"CYCLE_TP_TIERED_ENABLED\":false,\"STOCH_CROSS_3M_EXIT_ENABLED\":false,\"BOUNCE_TOP_EXIT_ENABLED\":false}"
    "S08_SLOW_REDUCE={\"REDUCTION_COOLDOWN_SECONDS\":300.0,\"MIN_HOLD_BARS_BEFORE_EXIT\":96}"

    # ── AUGMENTATION: are augments helping? ──
    "S09_NO_AUGMENT={\"AUGMENT_ONLY_WHEN_PROFITABLE\":true,\"MIN_GAIN_TO_BUY_AGGRESSIVELY\":99.0,\"MAX_AUGMENTS_PER_POSITION\":0}"
    "S10_AUGMENT_WINNERS_ONLY={\"AUGMENT_ONLY_WHEN_PROFITABLE\":true,\"MIN_GAIN_TO_BUY_AGGRESSIVELY\":5.0,\"MAX_AUGMENTS_PER_POSITION\":1}"

    # ── RATIO SYSTEM: does L/S ratio enforcement help? ──
    "S11_NO_RATIO={\"LS_RATIO_ENFORCE\":false,\"RATIO_MULTIPLIER\":1.0}"
    "S12_RATIO_LOOSE={\"LS_RATIO_ENFORCE\":true,\"RATIO_MULTIPLIER\":1.0,\"LS_RATIO_MIN\":0.05,\"LS_RATIO_MAX\":20.0}"

    # ── ENTRY GATES: are we filtering well or blocking good trades? ──
    "S13_WIDE_OPEN_ENTRY={\"MTS_GATE_ENABLED\":false,\"K_ZONE_ENTRY_ENABLED\":false,\"ENTRY_VOL_MIN_RATIO\":0.0,\"ENTRY_ATR_PCT_MIN\":0.0}"
    "S14_STRICT_ENTRY={\"MTS_GATE_ENABLED\":true,\"MTS_BOTTOM_MIN\":25.0,\"K_ZONE_ENTRY_ENABLED\":true,\"ENTRY_VOL_MIN_RATIO\":2.0,\"ENTRY_ATR_PCT_MIN\":0.02}"

    # ── EXIT VELOCITY: let winners run vs cut early ──
    "S15_PATIENT_EXIT={\"WT_EXIT_VEL_THRESHOLD\":-10.0,\"MIN_HOLD_BARS_BEFORE_EXIT\":64,\"WT_REDUCE_FRAC_LOW\":0.05,\"WT_REDUCE_FRAC_MED\":0.10,\"WT_REDUCE_FRAC_HIGH\":0.25}"
    "S16_FAST_EXIT={\"WT_EXIT_VEL_THRESHOLD\":-3.0,\"MIN_HOLD_BARS_BEFORE_EXIT\":8,\"WT_REDUCE_FRAC_LOW\":0.30,\"WT_REDUCE_FRAC_MED\":0.50,\"WT_REDUCE_FRAC_HIGH\":0.80}"

    # ── NOLOSS: the big question — does strict no-loss work? ──
    "S17_NOLOSS_OFF={\"NOLOSS_MIN_PROFIT_PCT\":0.0}"
    "S18_NOLOSS_TIGHT={\"NOLOSS_MIN_PROFIT_PCT\":0.5}"
    "S19_NOLOSS_LOOSE={\"NOLOSS_MIN_PROFIT_PCT\":5.0}"

    # ── STRIPPED SYSTEMS: find the minimal profitable core ──
    "S20_BARE_MINIMUM={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false,\"SBA_ENABLED\":false,\"COMPRESSION_BREAKOUT_ENABLED\":false,\"BB_SQUEEZE_ENABLED\":false,\"VOL_SPIKE_ENABLED\":false,\"CYCLE_TP_TIERED_ENABLED\":false,\"MAX_AUGMENTS_PER_POSITION\":0,\"LS_RATIO_ENFORCE\":false,\"K_ZONE_ENTRY_ENABLED\":false,\"MTS_GATE_ENABLED\":false}"
    "S21_BARE_PLUS_RATIO={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false,\"SBA_ENABLED\":false,\"COMPRESSION_BREAKOUT_ENABLED\":false,\"BB_SQUEEZE_ENABLED\":false,\"VOL_SPIKE_ENABLED\":false,\"CYCLE_TP_TIERED_ENABLED\":false,\"MAX_AUGMENTS_PER_POSITION\":0,\"LS_RATIO_ENFORCE\":true,\"RATIO_MULTIPLIER\":3.0}"
    "S22_BARE_PLUS_REENTRY={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":true,\"FAST_REENTRY_ENABLED\":false,\"SBA_ENABLED\":false,\"COMPRESSION_BREAKOUT_ENABLED\":false,\"BB_SQUEEZE_ENABLED\":false,\"VOL_SPIKE_ENABLED\":false,\"CYCLE_TP_TIERED_ENABLED\":false,\"MAX_AUGMENTS_PER_POSITION\":0,\"LS_RATIO_ENFORCE\":false}"
    "S23_BARE_PLUS_AUGMENT={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false,\"SBA_ENABLED\":false,\"COMPRESSION_BREAKOUT_ENABLED\":false,\"BB_SQUEEZE_ENABLED\":false,\"VOL_SPIKE_ENABLED\":false,\"CYCLE_TP_TIERED_ENABLED\":false,\"MAX_AUGMENTS_PER_POSITION\":2,\"MIN_GAIN_TO_BUY_AGGRESSIVELY\":3.0,\"LS_RATIO_ENFORCE\":false}"

    # ── COMBINED: best structural hypothesis ──
    "S24_NO_HEDGE_NO_REENTRY={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false,\"LOSS_EXIT_REQUIRES_HEDGE\":false}"
    "S25_NO_HEDGE_PATIENT={\"HEDGE_MODE\":false,\"LOSS_EXIT_REQUIRES_HEDGE\":false,\"WT_EXIT_VEL_THRESHOLD\":-10.0,\"MIN_HOLD_BARS_BEFORE_EXIT\":64}"
    "S26_NO_HEDGE_STRICT_ENTRY={\"HEDGE_MODE\":false,\"LOSS_EXIT_REQUIRES_HEDGE\":false,\"MTS_GATE_ENABLED\":true,\"MTS_BOTTOM_MIN\":25.0,\"ENTRY_VOL_MIN_RATIO\":2.0}"
    "S27_FULL_STRIP_PLUS_RATIO_PATIENT={\"HEDGE_MODE\":false,\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false,\"LOSS_EXIT_REQUIRES_HEDGE\":false,\"MAX_AUGMENTS_PER_POSITION\":0,\"LS_RATIO_ENFORCE\":true,\"RATIO_MULTIPLIER\":3.0,\"WT_EXIT_VEL_THRESHOLD\":-10.0,\"MIN_HOLD_BARS_BEFORE_EXIT\":64}"
)

TOTAL=${#configs[@]}
DONE=0
START_TS=$(date +%s)

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" | tee -a "$LOG"; }
get_free_mb() { free -m | awk '/^Mem:/ {print $7}'; }

log "═══ STRUCTURAL SWEEP — $TOTAL configs, $MAX_SYMBOLS symbols ═══"
log "Testing: hedge on/off, reentry on/off, reduce freq, augment, ratio, entry gates, noloss, stripped systems"

for entry in "${configs[@]}"; do
    NAME="${entry%%=*}"
    OVERRIDES="${entry#*=}"

    [ -f "$RESULTS_DIR/logs/${NAME}.done" ] && { DONE=$((DONE+1)); log "  [$DONE/$TOTAL] $NAME — SKIP (done)"; continue; }

    # Wait for memory
    WAIT_COUNT=0
    while true; do
        FREE_MB=$(get_free_mb)
        [ "$FREE_MB" -ge "$MIN_FREE_MB" ] && break
        [ $((WAIT_COUNT % 6)) -eq 0 ] && log "  Waiting for ${MIN_FREE_MB}MB free... currently ${FREE_MB}MB"
        WAIT_COUNT=$((WAIT_COUNT + 1))
        sleep 10
    done

    OVERRIDE_FILE="$RESULTS_DIR/overrides/${NAME}.json"
    echo "$OVERRIDES" > "$OVERRIDE_FILE"

    DONE=$((DONE + 1))
    log "  [$DONE/$TOTAL] $NAME — STARTING (${FREE_MB}MB free)"

    T0=$(date +%s)
    V5_CONFIG_OVERRIDES="$OVERRIDE_FILE" \
        $PY "$BASE/backtest_v5_engine.py" \
        --mode "$MODE" --all --start "$START_DATE" \
        --capital "$CAPITAL" --max-symbols "$MAX_SYMBOLS" \
        > "$RESULTS_DIR/logs/${NAME}.log" 2>&1
    EXIT_CODE=$?
    T1=$(date +%s)
    ELAPSED=$((T1 - T0))

    SHARPE=$(grep -o '"sharpe": [0-9.-]*' "$RESULTS_DIR/logs/${NAME}.log" | tail -1 | awk '{print $2}')
    PNL=$(grep -o '"total_pnl_pct": [0-9.-]*' "$RESULTS_DIR/logs/${NAME}.log" | tail -1 | awk '{print $2}')
    TRADES=$(grep -o '"total_trades": [0-9]*' "$RESULTS_DIR/logs/${NAME}.log" | tail -1 | awk '{print $2}')
    DD=$(grep -o '"max_drawdown_pct": [0-9.-]*' "$RESULTS_DIR/logs/${NAME}.log" | tail -1 | awk '{print $2}')
    WR=$(grep -o '"win_rate": [0-9.-]*' "$RESULTS_DIR/logs/${NAME}.log" | tail -1 | awk '{print $2}')

    [ -z "$SHARPE" ] && SHARPE="ERR"
    [ -z "$PNL" ] && PNL="ERR"
    [ -z "$TRADES" ] && TRADES="0"
    [ -z "$DD" ] && DD="ERR"
    [ -z "$WR" ] && WR="ERR"

    RESULT_LINE="$NAME | Sharpe=$SHARPE | PnL=$PNL% | DD=$DD% | WR=$WR% | Trades=$TRADES | ${ELAPSED}s | exit=$EXIT_CODE"
    log "  [$DONE/$TOTAL] $RESULT_LINE"
    echo "$RESULT_LINE" >> "$RESULTS_DIR/results.txt"
    touch "$RESULTS_DIR/logs/${NAME}.done"

    cat > "$RESULTS_DIR/progress.txt" << PROG
╔══════════════════════════════════════════════════════════════╗
║  STRUCTURAL SWEEP — Which components make/lose money?
║  Done: $DONE / $TOTAL  ($(( DONE * 100 / TOTAL ))%)
║  Last: $NAME → Sharpe=$SHARPE PnL=$PNL%
║  Updated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
╚══════════════════════════════════════════════════════════════╝

RESULTS SO FAR:
$(cat "$RESULTS_DIR/results.txt" 2>/dev/null | sort -t'=' -k2 -rn)
PROG
done

log "═══ STRUCTURAL SWEEP COMPLETE ═══"
log "LEADERBOARD:"
sort -t'=' -k2 -rn "$RESULTS_DIR/results.txt" | tee -a "$LOG"
