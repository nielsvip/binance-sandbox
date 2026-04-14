#!/bin/bash
# Phase 2: Tradier Threshold Sweep — runs on server2
# Tests ALL numeric thresholds in evaluate_stop/augment/reentry
# Run after ablation (Phase 1) completes.
# Each job ~8GB RAM, MAX_CONC=2 on 30GB server.

SERVER="s2-int"
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
RDIR="$BASE/tradier_ablation_results"
LOCAL_RESULTS="$HOME/Documents/binance/data/tradier_ablation"
MAX_CONC=2

mkdir -p "$LOCAL_RESULTS"
log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

count_running() {
    ssh -o ConnectTimeout=5 "$SERVER" "ps aux | grep backtest_v5_full_tradier | grep -v 'bash -c' | grep -v grep | wc -l" 2>/dev/null || echo "0"
}

wait_for_slot() {
    while true; do
        RUNNING=$(count_running)
        [ "$RUNNING" -lt "$MAX_CONC" ] && return
        log "  $RUNNING/$MAX_CONC running, waiting 60s..."
        sleep 60
    done
}

launch_config() {
    local NAME="$1"
    local OVERRIDE_JSON="$2"
    local ABLATION="${3:-ALL}"
    local NOLOSS="${4:-0}"
    local DONEF="$RDIR/${NAME}.done"
    local LOGF="$RDIR/${NAME}.log"
    [ -f "/tmp/${NAME}.done" ] && scp -o ConnectTimeout=5 "$SERVER:$DONEF" "/tmp/${NAME}.done" 2>/dev/null
    [ -f "/tmp/${NAME}.done" ] && { log "SKIP $NAME (already done)"; return; }
    wait_for_slot
    log "Launching $NAME..."
    # Write config override to server
    ssh -o ConnectTimeout=10 "$SERVER" "cat > /tmp/${NAME}_cfg.json" << JEOF
$OVERRIDE_JSON
JEOF
    # Create and run the script on server
    ssh -o ConnectTimeout=10 "$SERVER" "cat > /tmp/${NAME}.sh << 'SEOF'
#!/bin/bash
cd $BASE
V5_CONFIG_OVERRIDES=/tmp/${NAME}_cfg.json $PYTHON backtest_v5_full_tradier.py --all --start 2024-01-01 --capital 70000 --account trb --ablation $ABLATION --noloss $NOLOSS > $LOGF 2>&1 && echo SUCCESS > $DONEF || echo FAILED > $DONEF
SEOF
chmod +x /tmp/${NAME}.sh"
    ssh -o ConnectTimeout=10 "$SERVER" "setsid bash /tmp/${NAME}.sh & disown; echo launched"
    sleep 5
    log "  $NAME launched"
}

collect_result() {
    local NAME="$1"
    local DONEF="$RDIR/${NAME}.done"
    local LOGF="$RDIR/${NAME}.log"
    local T0=$(date +%s)
    while true; do
        STATUS=$(ssh -o ConnectTimeout=5 "$SERVER" "cat $DONEF 2>/dev/null" 2>/dev/null || echo "")
        [ -n "$STATUS" ] && { log "  $NAME: $STATUS"; break; }
        ELAPSED=$(( $(date +%s) - T0 ))
        [ $ELAPSED -gt 7200 ] && { log "  TIMEOUT $NAME"; break; }
        PROGRESS=$(ssh -o ConnectTimeout=5 "$SERVER" "tail -1 $LOGF 2>/dev/null" 2>/dev/null || echo "")
        log "  $NAME: $PROGRESS"
        sleep 60
    done
    # Fetch log and parse
    ssh -o ConnectTimeout=10 "$SERVER" "cat $LOGF 2>/dev/null" > "/tmp/${NAME}.log" 2>/dev/null
    /opt/anaconda3/envs/binance_env/bin/python3 - "$NAME" << 'PYEOF'
import sys, re, csv, os
name = sys.argv[1]
try:
    with open(f"/tmp/{name}.log") as f:
        txt = f.read()
except:
    txt = ""
def ex(pat, default="0"):
    m = re.search(pat, txt, re.IGNORECASE | re.MULTILINE)
    if not m: return default
    return next((g for g in m.groups() if g is not None), default) if m.groups() else m.group(1)
trades  = ex(r"Total trades[:\s=]+(\d+)|Trades[:\s]+(\d+)", "0")
pnl     = ex(r"Total PnL[:\s]+\+?([-\d.]+)%", "0")
real_pnl= ex(r"Realized PnL[:\s]+\$\+?([-\d,.]+)", "0").replace(",","")
wr      = ex(r"Win Rate[:\s]+([\d.]+)%|WR[:\s]+([\d.]+)%", "0")
sharpe  = ex(r"Sharpe[:\s]+([-\d.]+)", "0")
dd      = ex(r"Max Drawdown[:\s]+([-\d.]+)%", "0")
print(f"  {name:<30}: trades={trades:>5} PnL={pnl:>7}% Sharpe={sharpe:>6} WR={wr:>5}% DD={dd:>5}%")
csvf = os.path.expanduser("~/Documents/binance/data/tradier_ablation/phase2_sweep.csv")
write_hdr = not os.path.exists(csvf)
with open(csvf, "a") as cf:
    w = csv.writer(cf)
    if write_hdr:
        w.writerow(["config","trades","pnl_pct","realized_pnl_usd","win_rate_pct","sharpe","max_dd_pct"])
    w.writerow([name, trades, pnl, real_pnl, wr, sharpe, dd])
PYEOF
}

log "===== PHASE 2: TRADIER THRESHOLD SWEEP ====="
log ""

# --- NOLOSS_MIN_PROFIT_PCT sweep (most important: controls when WT exit fires) ---
log "=== NOLOSS_MIN_PROFIT_PCT_TRADIER sweep ==="
for PCT in 0.0 0.1 0.2 0.3 0.5 0.75 1.0 1.5 2.0 3.0; do
    NAME="noloss_${PCT//./_}"
    launch_config "$NAME" "{\"NOLOSS_MIN_PROFIT_PCT_TRADIER\": $PCT}" "ALL" "0"
done
for PCT in 0.0 0.1 0.2 0.3 0.5 0.75 1.0 1.5 2.0 3.0; do
    collect_result "noloss_${PCT//./_}"
done

# --- MIN_HOLD_BARS_TRADIER sweep (grace period before any exit fires) ---
log "=== MIN_HOLD_BARS_TRADIER sweep ==="
for BARS in 0 8 16 24 32 40 48 64 96; do
    NAME="hold_${BARS}b"
    MINS=$((BARS * 5))
    launch_config "$NAME" "{\"MIN_HOLD_BARS_TRADIER\": $BARS, \"MIN_HOLD_MINUTES_TRADIER\": $MINS}" "ALL" "0"
done
for BARS in 0 8 16 24 32 40 48 64 96; do
    collect_result "hold_${BARS}b"
done

# --- WT_EXIT_MIN_TFS_TRADIER sweep (1 vs 2 TFs required for WT exit) ---
log "=== WT_EXIT_MIN_TFS_TRADIER sweep ==="
for MTFS in 1 2; do
    NAME="wt_min_tfs_${MTFS}"
    launch_config "$NAME" "{\"WT_EXIT_MIN_TFS_TRADIER\": $MTFS}" "ALL" "0"
done
for MTFS in 1 2; do
    collect_result "wt_min_tfs_${MTFS}"
done

# --- MIN_EXIT_TF_AGAINST_TRADIER sweep (multi-TF gate for technical exits) ---
log "=== MIN_EXIT_TF_AGAINST_TRADIER sweep ==="
for MTFA in 1 2 3; do
    NAME="min_exit_tf_${MTFA}"
    launch_config "$NAME" "{\"MIN_EXIT_TF_AGAINST_TRADIER\": $MTFA}" "ALL" "0"
done
for MTFA in 1 2 3; do
    collect_result "min_exit_tf_${MTFA}"
done

# --- MIN_GAIN_TO_BUY_AGGRESSIVELY sweep (augment gate) ---
log "=== MIN_GAIN_TO_BUY_AGGRESSIVELY (augment gate) sweep ==="
for PCT in 0.5 1.0 1.5 2.0 2.5 3.0 4.0 5.0; do
    NAME="aug_gate_${PCT//./_}"
    launch_config "$NAME" "{\"MIN_GAIN_TO_BUY_AGGRESSIVELY\": $PCT}" "ALL" "0"
done
for PCT in 0.5 1.0 1.5 2.0 2.5 3.0 4.0 5.0; do
    collect_result "aug_gate_${PCT//./_}"
done

# --- REENTRY_TIER2_PRICE_PCT_TRADIER sweep ---
log "=== REENTRY_TIER2_PRICE_PCT sweep ==="
for PCT in 0.001 0.002 0.003 0.005 0.01; do
    NAME="re_pct_${PCT//./_}"
    launch_config "$NAME" "{\"REENTRY_TIER2_PRICE_PCT_TRADIER\": $PCT}" "ALL" "0"
done
for PCT in 0.001 0.002 0.003 0.005 0.01; do
    collect_result "re_pct_${PCT//./_}"
done

# --- WT_EXIT_TFS_TRADIER sweep (redundant with tradier_48h but confirm here) ---
log "=== WT_EXIT_TFS_TRADIER sweep ==="
for TFS in "1h" "4h" "D" "1h+4h" "4h+D" "1h+D" "1h+4h+D"; do
    NAME="wt_tfs_${TFS//+/_}"
    launch_config "$NAME" "{\"WT_EXIT_TFS_TRADIER\": \"$TFS\"}" "WT_EXIT" "0"
done
for TFS in "1h" "4h" "D" "1h+4h" "4h+D" "1h+D" "1h+4h+D"; do
    collect_result "wt_tfs_${TFS//+/_}"
done

log ""
log "===== PHASE 2 SWEEP COMPLETE ====="
log "Results: $LOCAL_RESULTS/phase2_sweep.csv"

# Update 101.xlsx
/opt/anaconda3/envs/binance_env/bin/python3 - << 'PYEOF'
import csv, os
from pathlib import Path

results_dir = Path(os.path.expanduser("~/Documents/binance/data/tradier_ablation"))
try:
    import openpyxl
    wb_path = Path(os.path.expanduser("~/Documents/binance/101.xlsx"))
    wb = openpyxl.load_workbook(str(wb_path)) if wb_path.exists() else openpyxl.Workbook()
    for sheet_name, csv_name in [("TRADIER_ABLATION","phase1_ablation.csv"),("TRADIER_P2_SWEEP","phase2_sweep.csv")]:
        csvf = results_dir / csv_name
        if not csvf.exists(): continue
        with open(csvf) as f:
            rows = list(csv.DictReader(f))
        if sheet_name in wb.sheetnames: del wb[sheet_name]
        ws = wb.create_sheet(sheet_name, 0)
        if rows:
            ws.append(list(rows[0].keys()))
            for r in rows: ws.append(list(r.values()))
    wb.save(str(wb_path))
    print("Updated 101.xlsx with ablation + P2 sweep results")
except Exception as e:
    print(f"XLSX update failed: {e}")
PYEOF
