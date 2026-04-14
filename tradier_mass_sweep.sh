#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# TRADIER MASS SWEEP — MILLIONS OF RESULTS
#
# Runs backtest_v5_full_tradier.py with every config combo sequentially.
# Outputs CSV summary after EACH run for live spreadsheet tracking.
# ═══════════════════════════════════════════════════════════════════

PY="$HOME/.conda/envs/binance_env/bin/python3"
[ -f "$PY" ] || PY="$HOME/miniconda3/envs/binance_env/bin/python3"
BASE="$HOME/binance"
RESULTS_DIR="$HOME/tradier_sweep_results"
CSV="$RESULTS_DIR/tradier_sweep_master.csv"
LOG="$RESULTS_DIR/sweep.log"

mkdir -p "$RESULTS_DIR/logs" "$RESULTS_DIR/trades"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" | tee -a "$LOG"; }

# Initialize CSV header if doesn't exist
if [ ! -f "$CSV" ]; then
    echo "config_name,noloss,wt_exit_tfs,wt_exit_mode,wt_vel_threshold,entry_d_gate,ablation,extra_config,sharpe,total_pnl_pct,max_drawdown_pct,win_rate,total_trades,opens,closes,winning_closes,losing_closes,final_equity,runtime_sec,timestamp" > "$CSV"
fi

# ═══════════════════════════════════════════════════════════════════
# CONFIG GRID — every meaningful combination
# ═══════════════════════════════════════════════════════════════════

# NOLOSS values to test
NOLOSS_VALUES="0 0.1 0.3 0.5 1.0 2.0 5.0"

# WT exit TF combinations
WT_EXIT_TFS_VALUES="off 1h 4h D 1h,4h 4h,D 1h,4h,D"

# WT exit modes
WT_EXIT_MODES="cross velocity both"

# WT velocity thresholds (only used with velocity/both mode)
WT_VEL_THRESHOLDS="-2.0 -3.0 -5.0 -8.0"

# Entry D gate on/off
ENTRY_D_GATES="0 1"

# Extra config overrides (JSON)
EXTRA_CONFIGS=(
    "NONE={}"
    "TIGHT_ENTRY={\"MTS_BOTTOM_MIN\":25.0,\"K_ZONE_ENTRY_ENABLED\":true}"
    "LOOSE_ENTRY={\"MTS_BOTTOM_MIN\":5.0,\"K_ZONE_ENTRY_ENABLED\":false}"
    "HIGH_SCORE={\"ENTRY_SCORE_THRESHOLD\":24}"
    "LOW_SCORE={\"ENTRY_SCORE_THRESHOLD\":14}"
    "BIG_POS={\"START_POSITION_SIZE\":2000}"
    "SMALL_POS={\"START_POSITION_SIZE\":800}"
    "FAST_REENTRY={\"REENTRY_MANDATORY\":true,\"FAST_REENTRY_ENABLED\":true}"
    "NO_REENTRY={\"REENTRY_MANDATORY\":false,\"FAST_REENTRY_ENABLED\":false}"
    "NO_AUGMENT={\"MAX_AUGMENTS_PER_POSITION\":0}"
    "AUGMENT_LOOSE={\"MIN_GAIN_TO_BUY_AGGRESSIVELY\":1.5,\"MAX_AUGMENTS_PER_POSITION\":3}"
    "MFI_SIZING={\"SIZING_INDICATOR\":\"mfi\"}"
    "NO_RATIO={\"LS_RATIO_ENFORCE\":false}"
    "STRICT_RATIO={\"RATIO_MULTIPLIER\":5.0}"
    "PATIENT_EXIT={\"MIN_HOLD_BARS_BEFORE_EXIT\":64}"
    "FAST_EXIT={\"MIN_HOLD_BARS_BEFORE_EXIT\":4}"
)

extract_metric() {
    local file="$1" key="$2"
    python3 -c "import json; d=json.load(open('$file')); print(d.get('$key', 'ERR'))" 2>/dev/null || echo "ERR"
}

TOTAL=0
DONE=0
START_TS=$(date +%s)

# Count total configs
for noloss in $NOLOSS_VALUES; do
    for wt_tfs in $WT_EXIT_TFS_VALUES; do
        if [ "$wt_tfs" = "off" ]; then
            TOTAL=$((TOTAL + 2 * ${#EXTRA_CONFIGS[@]}))  # 2 entry_d_gate values
        else
            for wt_mode in $WT_EXIT_MODES; do
                if [ "$wt_mode" = "cross" ]; then
                    TOTAL=$((TOTAL + 2 * ${#EXTRA_CONFIGS[@]}))
                else
                    for vel in $WT_VEL_THRESHOLDS; do
                        TOTAL=$((TOTAL + 2 * ${#EXTRA_CONFIGS[@]}))
                    done
                fi
            done
        fi
    done
done

log "═══ TRADIER MASS SWEEP — $TOTAL configs ═══"
log "Grid: ${#NOLOSS_VALUES[@]} noloss × WT TF combos × modes × ${#EXTRA_CONFIGS[@]} extra configs"

for noloss in $NOLOSS_VALUES; do
    for wt_tfs in $WT_EXIT_TFS_VALUES; do
        # Build WT exit mode list based on TFs
        if [ "$wt_tfs" = "off" ]; then
            modes_list="cross"
            vel_list="0"
        else
            modes_list="$WT_EXIT_MODES"
        fi

        for wt_mode in $modes_list; do
            if [ "$wt_mode" = "cross" ] || [ "$wt_tfs" = "off" ]; then
                vel_list="-2.0"
            else
                vel_list="$WT_VEL_THRESHOLDS"
            fi

            for vel in $vel_list; do
                for dgate in $ENTRY_D_GATES; do
                    for entry in "${EXTRA_CONFIGS[@]}"; do
                        ENAME="${entry%%=*}"
                        EJSON="${entry#*=}"

                        NAME="NL${noloss}_WT${wt_tfs}_${wt_mode}_V${vel}_DG${dgate}_${ENAME}"
                        NAME=$(echo "$NAME" | tr ',' '-')

                        # Skip if already done
                        if grep -q "^${NAME}," "$CSV" 2>/dev/null; then
                            DONE=$((DONE + 1))
                            continue
                        fi

                        DONE=$((DONE + 1))
                        T0=$(date +%s)
                        LOGFILE="$RESULTS_DIR/logs/${NAME}.log"
                        SUMMARY="$RESULTS_DIR/logs/${NAME}_summary.json"

                        # Build command
                        CMD="$PY $BASE/backtest_v5_full_tradier.py --all --start 2024-06-01 --noloss $noloss"
                        [ "$wt_tfs" != "off" ] && CMD="$CMD --wt-exit-tfs $wt_tfs --wt-exit-mode $wt_mode --wt-vel-threshold $vel"
                        [ "$dgate" = "1" ] && CMD="$CMD --entry-d-gate"
                        [ "$EJSON" != "{}" ] && CMD="$CMD --config '$EJSON'"

                        log "  [$DONE/$TOTAL] $NAME — STARTING"

                        # Run engine, capture output
                        eval $CMD > "$LOGFILE" 2>&1
                        EXIT_CODE=$?
                        T1=$(date +%s)
                        ELAPSED=$((T1 - T0))

                        # Extract summary from last line of log
                        SUMMARY_LINE=$(grep -o '{.*}' "$LOGFILE" | tail -1)
                        if [ -n "$SUMMARY_LINE" ]; then
                            echo "$SUMMARY_LINE" > "$SUMMARY"
                        fi

                        # Parse metrics
                        if [ -f "$SUMMARY" ]; then
                            SHARPE=$(extract_metric "$SUMMARY" "sharpe")
                            PNL=$(extract_metric "$SUMMARY" "total_pnl_pct")
                            DD=$(extract_metric "$SUMMARY" "max_drawdown_pct")
                            WR=$(extract_metric "$SUMMARY" "win_rate")
                            TRADES=$(extract_metric "$SUMMARY" "total_trades")
                            OPENS=$(extract_metric "$SUMMARY" "opens")
                            CLOSES=$(extract_metric "$SUMMARY" "closes")
                            WCLOSE=$(extract_metric "$SUMMARY" "winning_closes")
                            LCLOSE=$(extract_metric "$SUMMARY" "losing_closes")
                            EQUITY=$(extract_metric "$SUMMARY" "final_equity")
                        else
                            SHARPE="ERR"; PNL="ERR"; DD="ERR"; WR="ERR"; TRADES="0"
                            OPENS="0"; CLOSES="0"; WCLOSE="0"; LCLOSE="0"; EQUITY="ERR"
                        fi

                        # Append to CSV
                        echo "${NAME},${noloss},${wt_tfs},${wt_mode},${vel},${dgate},ALL,${ENAME},${SHARPE},${PNL},${DD},${WR},${TRADES},${OPENS},${CLOSES},${WCLOSE},${LCLOSE},${EQUITY},${ELAPSED},$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$CSV"

                        # Log result
                        log "  [$DONE/$TOTAL] $NAME: Sharpe=$SHARPE PnL=$PNL% DD=$DD% WR=$WR% Trades=$TRADES (${ELAPSED}s) exit=$EXIT_CODE"

                        # Update progress file
                        NOW_TS=$(date +%s)
                        ELAPSED_TOTAL=$((NOW_TS - START_TS))
                        if [ $DONE -gt 0 ]; then
                            AVG=$((ELAPSED_TOTAL / DONE))
                            ETA=$(( (TOTAL - DONE) * AVG ))
                            ETA_H=$((ETA / 3600))
                            ETA_M=$(( (ETA % 3600) / 60 ))
                        else
                            ETA_H="?"
                            ETA_M="?"
                        fi

                        cat > "$RESULTS_DIR/PROGRESS.txt" << PROG
╔══════════════════════════════════════════════════════════════╗
║  TRADIER MASS SWEEP — $(hostname)
║  Done: $DONE / $TOTAL  ($(( DONE * 100 / TOTAL ))%)
║  ETA: ${ETA_H}h ${ETA_M}m
║  Last: $NAME → Sharpe=$SHARPE PnL=$PNL%
║  CSV: $CSV ($(wc -l < "$CSV") rows)
║  Updated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
╚══════════════════════════════════════════════════════════════╝

TOP 10 BY SHARPE:
$(tail -n +2 "$CSV" | sort -t',' -k9 -rn | head -10 | awk -F',' '{printf "  %-50s Sharpe=%s PnL=%s%% WR=%s%% Trades=%s\n", $1, $9, $10, $12, $13}')
PROG
                    done
                done
            done
        done
    done
done

log "═══ TRADIER MASS SWEEP COMPLETE — $DONE configs ═══"
log "Results: $CSV"
