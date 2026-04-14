#!/bin/bash
# TRADIER ABLATION SWEEP — Proper test grid
# Phase 1: Which evaluate_ functions contribute value?
# Phase 2: Threshold sweep on winners
#
# Deploys to server2 (157.180.125.52 has tradier_48h_sweep, use server2=204)
# Uses backtest_v5_full_tradier.py (has --ablation flag, real evaluate functions)
# Also uses backtest_v7_engine.py --mode tradier for threshold sweeps

set -euo pipefail
SERVER="s2-int"
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
START="2024-01-01"
CAPITAL=70000
ACCOUNT="trb"
RESULTS_DIR="$HOME/Documents/binance/data/tradier_ablation"
REMOTE_RESULTS="$BASE/tradier_ablation_results"
MAX_CONCURRENT=4  # server2 has 30GB, each run ~3GB = 10 max; use 4 for safety

mkdir -p "$RESULTS_DIR"

# Sync latest files to server2
echo "Syncing files to server2..."
for f in backtest_v5_full_tradier.py backtest_v5_harness.py tradier_manage.py tradier_api.py \
          tradier_positions.py tradier_indicators.py tradier_rankings.py tradier_watchdog_cron.sh \
          config.py config_tradier.py utils.py wt_composite.py; do
    [ -f "/Users/niels/Documents/binance/$f" ] && \
        scp -o ConnectTimeout=10 "/Users/niels/Documents/binance/$f" "$SERVER:$BASE/" 2>/dev/null && \
        echo "  synced $f" || echo "  SKIP $f"
done
ssh $SERVER "mkdir -p $REMOTE_RESULTS"

echo ""
echo "===== PHASE 1: ABLATION STUDY ====="
echo "Tests each evaluate_ function in isolation."
echo "Which ones add value? Which ones hurt?"
echo ""

# ABLATION CONFIGS — test each function on/off
declare -a ABLATION_MODES=(
    "ALL"         # all functions enabled — baseline
    "NO_STOP"     # never exit — shows cost of holding forever
    "NO_OPEN"     # never enter new — shows augment+reentry only value
    "NO_AUGMENT"  # no augmentation — shows entry+exit only
    "NO_REENTRY"  # no reentry — shows entry+exit only (no compounding)
    "STOP_ONLY"   # only exits, no entries — shows pure exit value
    "OPEN_ONLY"   # only entries+exits, no augment/reentry
    "WT_EXIT"     # WT-cross exits only (no IBS, no struct, no DC)
)

launch_job() {
    local NAME="$1"
    local CMD="$2"
    local LOGFILE="$REMOTE_RESULTS/${NAME}.log"
    local DONEFILE="$REMOTE_RESULTS/${NAME}.done"
    local SCRIPT="/tmp/trl_${NAME}.sh"

    cat > "/tmp/trl_${NAME}.sh" << SCRIPT_EOF
#!/bin/bash
set -e
cd $BASE
$CMD > $LOGFILE 2>&1
echo "DONE" > $DONEFILE
SCRIPT_EOF

    scp -o ConnectTimeout=10 "/tmp/trl_${NAME}.sh" "$SERVER:/tmp/trl_${NAME}.sh" 2>/dev/null
    ssh -o ConnectTimeout=10 "$SERVER" "nohup bash /tmp/trl_${NAME}.sh &" 2>/dev/null
    echo "  LAUNCHED: $NAME"
}

wait_for_slot() {
    # Wait until fewer than MAX_CONCURRENT jobs are running on server2
    while true; do
        RUNNING=$(ssh -o ConnectTimeout=5 "$SERVER" "pgrep -f 'backtest_v5_full_tradier\|backtest_v7_engine' | wc -l" 2>/dev/null || echo "0")
        [ "$RUNNING" -lt "$MAX_CONCURRENT" ] && break
        echo "  [slot wait] $RUNNING/$MAX_CONCURRENT running on server2..."
        sleep 30
    done
}

collect_result() {
    local NAME="$1"
    local DONEFILE="$REMOTE_RESULTS/${NAME}.done"
    local LOGFILE="$REMOTE_RESULTS/${NAME}.log"

    # Wait for completion
    local T0=$(date +%s)
    while true; do
        CHECK=$(ssh -o ConnectTimeout=5 "$SERVER" "cat $DONEFILE 2>/dev/null" 2>/dev/null || echo "")
        [ "$CHECK" = "DONE" ] && break
        ELAPSED=$(( $(date +%s) - T0 ))
        [ $ELAPSED -gt 3600 ] && { echo "  TIMEOUT: $NAME"; return; }
        sleep 30
    done

    # Fetch and parse results
    RESULT=$(ssh -o ConnectTimeout=10 "$SERVER" "cat $LOGFILE 2>/dev/null" 2>/dev/null | \
        /opt/anaconda3/envs/binance_env/bin/python3 - << 'PYEOF'
import sys, re
txt = sys.stdin.read()
def ex(pat, default="0"):
    m = re.search(pat, txt, re.IGNORECASE)
    if not m: return default
    return next((g for g in m.groups() if g), default) if m.groups() else m.group(1)

trades  = ex(r"(?:total_trades|Trades):\s*(\d+)", "0")
pnl     = ex(r"(?:realized_pnl|Realized PnL).*?\$\+?([-\d,.]+)", "0")
wr      = ex(r"Win Rate[:\s]+([\d.]+)%|WR[:\s]+([\d.]+)%", "0")
sharpe  = ex(r"Sharpe[:\s]+([-\d.]+)", "0")
dd      = ex(r"Max Drawdown[:\s]+([\d.]+)%", "0")
print(f"{trades}|{pnl}|{wr}|{sharpe}|{dd}")
PYEOF
    )

    TRADES=$(echo "$RESULT" | cut -d'|' -f1)
    PNL=$(echo "$RESULT"   | cut -d'|' -f2)
    WR=$(echo "$RESULT"    | cut -d'|' -f3)
    SHARPE=$(echo "$RESULT"| cut -d'|' -f4)
    DD=$(echo "$RESULT"    | cut -d'|' -f5)

    echo "  RESULT: $NAME — Trades=$TRADES PnL=\$$PNL WR=${WR}% Sharpe=$SHARPE DD=$DD%"

    # Save to CSV
    echo "${NAME},ablation,${TRADES},${PNL},${WR},${SHARPE},${DD}" >> "$RESULTS_DIR/phase1_ablation.csv"

    # Cleanup remote
    ssh -o ConnectTimeout=5 "$SERVER" "rm -f $DONEFILE /tmp/trl_${NAME}.sh" 2>/dev/null
}

# Initialize results CSV
echo "config_name,type,trades,pnl_usd,win_rate_pct,sharpe,max_dd_pct" > "$RESULTS_DIR/phase1_ablation.csv"

# Launch all ablation configs
for MODE in "${ABLATION_MODES[@]}"; do
    NAME="abl_${MODE}"
    CMD="$PYTHON backtest_v5_full_tradier.py --all --start $START --capital $CAPITAL --account $ACCOUNT --ablation $MODE --noloss 0"
    wait_for_slot
    launch_job "$NAME" "$CMD"
    sleep 2
done

echo ""
echo "All Phase 1 jobs launched. Collecting results..."
for MODE in "${ABLATION_MODES[@]}"; do
    collect_result "abl_${MODE}"
done

echo ""
echo "===== PHASE 2: KEY THRESHOLD SWEEP ====="
echo "Tests critical numeric thresholds in the winning evaluate_ functions."
echo ""

# NOLOSS_MIN_PROFIT_PCT_TRADIER — how much profit needed before WT exit fires
echo "--- NOLOSS_MIN_PROFIT_PCT sweep ---"
for PCT in 0.0 0.1 0.3 0.5 1.0 2.0 3.0; do
    NAME="noloss_${PCT}"
    # Pass via V5_CONFIG_OVERRIDES
    echo "{\"NOLOSS_MIN_PROFIT_PCT_TRADIER\": $PCT, \"TRB_NOLOSS_MIN_PROFIT_PCT\": $PCT}" > "/tmp/trl_cfg_${NAME}.json"
    scp -o ConnectTimeout=10 "/tmp/trl_cfg_${NAME}.json" "$SERVER:/tmp/trl_cfg_${NAME}.json" 2>/dev/null
    CMD="V5_CONFIG_OVERRIDES=/tmp/trl_cfg_${NAME}.json $PYTHON backtest_v5_full_tradier.py --all --start $START --capital $CAPITAL --account $ACCOUNT --ablation ALL --noloss ${PCT}"
    wait_for_slot
    launch_job "$NAME" "$CMD"
    sleep 2
done
for PCT in 0.0 0.1 0.3 0.5 1.0 2.0 3.0; do
    collect_result "noloss_${PCT}"
done

# MIN_HOLD_BARS_TRADIER — grace period before exits fire
echo "--- MIN_HOLD_BARS sweep ---"
for BARS in 0 8 16 24 32 48 64; do
    NAME="hold_${BARS}b"
    MINS=$((BARS * 5))
    echo "{\"MIN_HOLD_BARS_TRADIER\": $BARS, \"MIN_HOLD_MINUTES_TRADIER\": $MINS}" > "/tmp/trl_cfg_${NAME}.json"
    scp -o ConnectTimeout=10 "/tmp/trl_cfg_${NAME}.json" "$SERVER:/tmp/trl_cfg_${NAME}.json" 2>/dev/null
    CMD="V5_CONFIG_OVERRIDES=/tmp/trl_cfg_${NAME}.json $PYTHON backtest_v5_full_tradier.py --all --start $START --capital $CAPITAL --account $ACCOUNT --ablation ALL --noloss 0.3"
    wait_for_slot
    launch_job "$NAME" "$CMD"
    sleep 2
done
for BARS in 0 8 16 24 32 48 64; do
    collect_result "hold_${BARS}b"
done

# WT_EXIT_TFS_TRADIER — which timeframes to use for WT exit
echo "--- WT_EXIT_TFS sweep ---"
for TFS in "1h" "4h" "D" "1h+4h" "4h+D" "1h+D" "1h+4h+D"; do
    NAME="wt_${TFS//+/_}"
    echo "{\"WT_EXIT_TFS_TRADIER\": \"$TFS\"}" > "/tmp/trl_cfg_${NAME}.json"
    scp -o ConnectTimeout=10 "/tmp/trl_cfg_${NAME}.json" "$SERVER:/tmp/trl_cfg_${NAME}.json" 2>/dev/null
    CMD="V5_CONFIG_OVERRIDES=/tmp/trl_cfg_${NAME}.json $PYTHON backtest_v5_full_tradier.py --all --start $START --capital $CAPITAL --account $ACCOUNT --ablation WT_EXIT --noloss 0.3"
    wait_for_slot
    launch_job "$NAME" "$CMD"
    sleep 2
done
for TFS in "1h" "4h" "D" "1h+4h" "4h+D" "1h+D" "1h+4h+D"; do
    collect_result "wt_${TFS//+/_}"
done

echo ""
echo "===== ALL DONE ====="
echo "Results in: $RESULTS_DIR"
echo "Phase 1 (ablation): $RESULTS_DIR/phase1_ablation.csv"

# Rsync results back
rsync -avz "$SERVER:$REMOTE_RESULTS/" "$RESULTS_DIR/server2_logs/" 2>/dev/null || true

# Update 101.xlsx
/opt/anaconda3/envs/binance_env/bin/python3 - << 'PYEOF'
import csv, glob, os
from pathlib import Path

results_dir = Path(os.path.expanduser("~/Documents/binance/data/tradier_ablation"))
all_rows = []

for csvf in results_dir.glob("*.csv"):
    try:
        with open(csvf) as f:
            rows = list(csv.DictReader(f))
            all_rows.extend(rows)
    except Exception:
        pass

if not all_rows:
    print("No results yet")
else:
    print(f"Found {len(all_rows)} results")
    # Try to add to 101.xlsx
    try:
        import openpyxl
        wb_path = Path(os.path.expanduser("~/Documents/binance/101.xlsx"))
        wb = openpyxl.load_workbook(str(wb_path)) if wb_path.exists() else openpyxl.Workbook()
        if "TRADIER_ABLATION" in wb.sheetnames:
            del wb["TRADIER_ABLATION"]
        ws = wb.create_sheet("TRADIER_ABLATION", 0)
        if all_rows:
            ws.append(list(all_rows[0].keys()))
            for r in all_rows:
                ws.append(list(r.values()))
        wb.save(str(wb_path))
        print(f"Updated 101.xlsx with tradier ablation results")
    except Exception as e:
        print(f"XLSX update failed: {e}")
PYEOF
