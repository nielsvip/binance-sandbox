#!/bin/bash
# Run tradier ablation in controlled batches on server2
# Each job ~3-4GB RAM, 30-40 min runtime. Max 3 concurrent.
set -euo pipefail

SERVER="s2-int"
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
RDIR="$BASE/tradier_ablation_results"
MAX_CONC=3

ABLATION_MODES="ALL NO_STOP NO_OPEN NO_AUGMENT NO_REENTRY STOP_ONLY OPEN_ONLY WT_EXIT"
LOCAL_RESULTS="$HOME/Documents/binance/data/tradier_ablation"
mkdir -p "$LOCAL_RESULTS"

log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

launch() {
    local MODE="$1"
    local LOGF="$RDIR/abl_${MODE}.log"
    local DONEF="$RDIR/abl_${MODE}.done"

    # Create launch script (using && so DONE only writes on success, + trap for SIGTERM)
    local SCRIPT=$(cat << BASH_EOF
#!/bin/bash
cd $BASE
$PYTHON backtest_v5_full_tradier.py --all --start 2024-01-01 --capital 70000 --account trb --ablation ${MODE} --noloss 0 > $LOGF 2>&1
echo "SUCCESS" > $DONEF
BASH_EOF
)
    echo "$SCRIPT" | ssh -o ConnectTimeout=10 "$SERVER" "cat > /tmp/trl_${MODE}.sh && chmod +x /tmp/trl_${MODE}.sh && nohup bash /tmp/trl_${MODE}.sh &"
    log "Launched $MODE"
}

wait_for_slot() {
    while true; do
        RUNNING=$(ssh -o ConnectTimeout=5 "$SERVER" "pgrep -f 'backtest_v5_full_tradier' | wc -l" 2>/dev/null || echo "0")
        [ "$RUNNING" -lt "$MAX_CONC" ] && return
        log "  $RUNNING/$MAX_CONC running on server2, waiting 60s..."
        sleep 60
    done
}

wait_for_job() {
    local MODE="$1"
    local DONEF="$RDIR/abl_${MODE}.done"
    local T0=$(date +%s)
    while true; do
        STATUS=$(ssh -o ConnectTimeout=5 "$SERVER" "cat $DONEF 2>/dev/null" 2>/dev/null || echo "")
        if [ "$STATUS" = "SUCCESS" ]; then
            ELAPSED=$(( $(date +%s) - T0 ))
            log "  ✓ $MODE completed in ${ELAPSED}s"
            return 0
        fi
        # Show progress
        PROGRESS=$(ssh -o ConnectTimeout=5 "$SERVER" "tail -1 $RDIR/abl_${MODE}.log 2>/dev/null" 2>/dev/null || echo "")
        log "  $MODE: $PROGRESS"
        sleep 60
        ELAPSED=$(( $(date +%s) - T0 ))
        [ $ELAPSED -gt 5400 ] && { log "  TIMEOUT $MODE after ${ELAPSED}s"; return 1; }
    done
}

collect_result() {
    local MODE="$1"
    local LOGF="$RDIR/abl_${MODE}.log"

    # Fetch log
    ssh -o ConnectTimeout=10 "$SERVER" "cat $LOGF 2>/dev/null" > "/tmp/abl_${MODE}_result.log" 2>/dev/null

    # Parse results
    /opt/anaconda3/envs/binance_env/bin/python3 - << PYEOF
import re
with open("/tmp/abl_${MODE}_result.log") as f:
    txt = f.read()

def ex(pat, default="0"):
    m = re.search(pat, txt, re.IGNORECASE | re.MULTILINE)
    if not m: return default
    return next((g for g in m.groups() if g is not None), default) if m.groups() else m.group(1)

trades  = ex(r"Total trades[:\s=]+(\d+)|Trades[:\s]+(\d+)", "0")
pnl     = ex(r"Total PnL[:\s]+\+?([-\d.]+)%", "0")
real_pnl= ex(r"Realized PnL[:\s]+\\\$\+?([-\d,.]+)", "0").replace(",","")
wr      = ex(r"Win Rate[:\s]+([\d.]+)%|WR[:\s]+([\d.]+)%", "0")
sharpe  = ex(r"Sharpe[:\s]+([-\d.]+)", "0")
dd      = ex(r"Max Drawdown[:\s]+([-\d.]+)%", "0")
wins    = ex(r"wins[:\s=]+(\d+)|Wins[:\s]+(\d+)", "0")
losses  = ex(r"losses[:\s=]+(\d+)|Losses[:\s]+(\d+)", "0")

print(f"  {MODE:<12}: trades={trades:>5} PnL={pnl:>7}% realPnL=\${real_pnl:>8} WR={wr:>5}% Sharpe={sharpe:>6} DD={dd:>5}%")
# Append to CSV
import csv, os
csvf = "$LOCAL_RESULTS/phase1_ablation.csv"
write_header = not os.path.exists(csvf)
with open(csvf, "a") as cf:
    w = csv.writer(cf)
    if write_header:
        w.writerow(["mode","trades","pnl_pct","realized_pnl_usd","win_rate_pct","sharpe","max_dd_pct","wins","losses"])
    w.writerow(["${MODE}", trades, pnl, real_pnl, wr, sharpe, dd, wins, losses])
PYEOF
}

# Run all 8 ablation modes in controlled batches
log "Starting tradier ablation sweep on server2 (max $MAX_CONC concurrent)"
log "8 ablation modes × ~35min each. Expect first results in ~35min."
log ""

ALL_MODES=($ABLATION_MODES)
RUNNING_MODES=()

for MODE in "${ALL_MODES[@]}"; do
    wait_for_slot
    launch "$MODE"
    RUNNING_MODES+=("$MODE")
    sleep 5
done

log "All jobs launched. Collecting results..."
for MODE in "${ALL_MODES[@]}"; do
    wait_for_job "$MODE"
    collect_result "$MODE"
done

log ""
log "===== PHASE 1 ABLATION COMPLETE ====="
cat "$LOCAL_RESULTS/phase1_ablation.csv" 2>/dev/null

# Update 101.xlsx
/opt/anaconda3/envs/binance_env/bin/python3 - << PYEOF
import csv, openpyxl
from pathlib import Path
from openpyxl.styles import Font, PatternFill

wb_path = Path("101.xlsx")
wb = openpyxl.load_workbook(str(wb_path)) if wb_path.exists() else openpyxl.Workbook()
sheet = "TRADIER_ABLATION"
if sheet in wb.sheetnames: del wb[sheet]
ws = wb.create_sheet(sheet, 0)

with open("$LOCAL_RESULTS/phase1_ablation.csv") as f:
    rows = list(csv.DictReader(f))

if rows:
    ws.append(list(rows[0].keys()))
    ws[1][0].font = Font(bold=True)
    for row in rows:
        ws.append(list(row.values()))

wb.save(str(wb_path))
print(f"Updated 101.xlsx: TRADIER_ABLATION sheet with {len(rows)} results")
PYEOF

log "Results in: $LOCAL_RESULTS/phase1_ablation.csv"
log "101.xlsx updated with TRADIER_ABLATION sheet"
