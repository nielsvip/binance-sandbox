#!/bin/bash
# merge_intel_sweep.sh — run on server2 after intel sweep completes
# Merges worker chunks, produces sorted CSV, then rsync results locally
# Then the local update_101_intel.py updates 101.xlsx

PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
LOCAL="niels@192.168.1.100"  # placeholder — use push.py instead
log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

merge_results() {
    local DIR="$1"
    local MODE="$2"
    local MERGED="$DIR/intel_merged.csv"
    local HDR=""

    log "Merging $MODE results from $DIR..."
    for f in "$DIR"/chunk_*.csv; do
        [ -f "$f" ] || continue
        if [ -z "$HDR" ]; then
            HDR=$(head -1 "$f")
            echo "$HDR" > "$MERGED"
        fi
        tail -n +2 "$f" >> "$MERGED" 2>/dev/null
    done

    [ -f "$MERGED" ] || { log "No results in $DIR"; return; }

    # Sort by sharpe descending
    TMP="$DIR/sorted_tmp.csv"
    echo "$HDR" > "$TMP"
    tail -n +2 "$MERGED" | sort -t',' -k7 -rn >> "$TMP"
    mv "$TMP" "$MERGED"

    TOTAL=$(wc -l < "$MERGED")
    log "$MODE: $TOTAL result rows"

    log "TOP 30 LONG ($MODE):"
    grep ",LONG," "$MERGED" | head -30 | awk -F',' '{printf "  Sharpe=%s | %s -> %s\n", $7, $1, $2}'
    log ""
    log "TOP 30 SHORT ($MODE):"
    grep ",SHORT," "$MERGED" | head -30 | awk -F',' '{printf "  Sharpe=%s | %s -> %s\n", $7, $1, $2}'
}

# Wait for all tradier workers
log "=== Waiting for TRADIER intel sweep workers ==="
while true; do
    RUNNING=$(ps aux | grep backtest_wt_intel_sweep | grep -v grep | grep -v "bash -c" | wc -l)
    T_DONE=$(ls "$BASE/intel_sweep_tradier/w"*.done 2>/dev/null | wc -l)
    C_DONE=$(ls "$BASE/intel_sweep_crypto/w"*.done 2>/dev/null | wc -l)
    [ "$T_DONE" -ge "8" ] && [ "$C_DONE" -ge "4" ] && break
    log "  Tradier: $T_DONE/8 done | Crypto: $C_DONE/4 done | $RUNNING running"
    sleep 60
done

log "All workers done!"

merge_results "$BASE/intel_sweep_tradier" "TRADIER"
merge_results "$BASE/intel_sweep_crypto" "CRYPTO"

log "=== COMPLETE ==="
log "Results:"
log "  Tradier: $BASE/intel_sweep_tradier/intel_merged.csv"
log "  Crypto:  $BASE/intel_sweep_crypto/intel_merged.csv"
