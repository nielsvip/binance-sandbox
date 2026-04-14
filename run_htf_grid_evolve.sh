#!/bin/bash
# HTF Exit Grid — EVOLVING: runs tests, finds winners, combines them, repeats
# Self-learning loop: each generation combines the best from the previous
PY="/opt/anaconda3/envs/binance_env/bin/python"
SCRIPT="backtest_v5_full_tradier.py"
LOG="/Users/niels/logs"
cd /Users/niels/Documents/binance

# Stop at 12:30 UTC for market prep
STOP_HOUR=12
STOP_MIN=30

should_stop() {
    local h=$(date -u +%H)
    local m=$(date -u +%M)
    if [ "$h" -gt "$STOP_HOUR" ] || ([ "$h" -eq "$STOP_HOUR" ] && [ "$m" -ge "$STOP_MIN" ]); then
        return 0
    fi
    return 1
}

run_test() {
    local name=$1
    shift
    local logfile="$LOG/htf_${name}.log"
    echo "$(date -u): Running $name..."
    $PY -u $SCRIPT --all --start 2024-06-01 "$@" > "$logfile" 2>&1
    # Extract realized PnL
    local pnl=$(grep "PnL:" "$logfile" 2>/dev/null | tail -1 | grep -oP '\$[\d.-]+' | head -1 | tr -d '$')
    local trades=$(grep "Realized:" "$logfile" 2>/dev/null | grep -oP '\d+ trades' | head -1 | grep -oP '\d+')
    local eq=$(grep "eq:" "$logfile" 2>/dev/null | tail -1 | grep -oP 'eq: \$[\d.]+' | grep -oP '[\d.]+')
    echo "$name|$pnl|$trades|$eq" >> "$LOG/htf_results.csv"
    echo "$(date -u): $name → PnL=\$$pnl trades=$trades eq=\$$eq"
}

echo "$(date -u): === EVOLVING HTF GRID STARTED ==="
echo "name|realized_pnl|trades|final_equity" > "$LOG/htf_results.csv"

# ============================================================
# GENERATION 1: Core TF comparisons (which TF works best?)
# ============================================================
echo "$(date -u): === GENERATION 1: Single TF exits ==="
should_stop && exit 0
run_test "G1_hodl"      --noloss 0 --wt-exit-tfs off
should_stop && exit 0
run_test "G1_1h"        --noloss 0 --wt-exit-tfs "1h"
should_stop && exit 0
run_test "G1_4h"        --noloss 0 --wt-exit-tfs "4h"
should_stop && exit 0
run_test "G1_D"         --noloss 0 --wt-exit-tfs "D"
should_stop && exit 0
run_test "G1_4h_vel"    --noloss 0 --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -2
should_stop && exit 0
run_test "G1_D_vel"     --noloss 0 --wt-exit-tfs "D" --wt-exit-mode velocity --wt-vel-threshold -1

# ============================================================
# GENERATION 2: Multi-TF combos
# ============================================================
echo "$(date -u): === GENERATION 2: Multi-TF combos ==="
should_stop && exit 0
run_test "G2_1h4h"      --noloss 0 --wt-exit-tfs "1h,4h"
should_stop && exit 0
run_test "G2_4hD"       --noloss 0 --wt-exit-tfs "4h,D"
should_stop && exit 0
run_test "G2_1h4hD"     --noloss 0 --wt-exit-tfs "1h,4h,D"
should_stop && exit 0
run_test "G2_4hD_both"  --noloss 0 --wt-exit-tfs "4h,D" --wt-exit-mode both

# ============================================================
# GENERATION 3: Add Daily entry gate to best TF combos
# ============================================================
echo "$(date -u): === GENERATION 3: Daily entry gate ==="
should_stop && exit 0
run_test "G3_4h_Dgate"      --noloss 0 --wt-exit-tfs "4h" --entry-d-gate
should_stop && exit 0
run_test "G3_4hD_Dgate"     --noloss 0 --wt-exit-tfs "4h,D" --entry-d-gate
should_stop && exit 0
run_test "G3_D_Dgate"       --noloss 0 --wt-exit-tfs "D" --entry-d-gate
should_stop && exit 0
run_test "G3_4h_vel_Dgate"  --noloss 0 --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -2 --entry-d-gate

# ============================================================
# GENERATION 4: NOLOSS sweep on best combos
# ============================================================
echo "$(date -u): === GENERATION 4: NOLOSS sweep ==="
should_stop && exit 0
run_test "G4_4hD_Dgate_nl0"   --noloss 0   --wt-exit-tfs "4h,D" --entry-d-gate
should_stop && exit 0
run_test "G4_4hD_Dgate_nl05"  --noloss 0.5 --wt-exit-tfs "4h,D" --entry-d-gate
should_stop && exit 0
run_test "G4_4hD_Dgate_nl1"   --noloss 1.0 --wt-exit-tfs "4h,D" --entry-d-gate

# ============================================================
# GENERATION 5: Velocity threshold sweep
# ============================================================
echo "$(date -u): === GENERATION 5: Velocity sweep ==="
should_stop && exit 0
run_test "G5_4h_vel1"   --noloss 0 --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -1
should_stop && exit 0
run_test "G5_4h_vel3"   --noloss 0 --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -3
should_stop && exit 0
run_test "G5_4h_vel5"   --noloss 0 --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -5
should_stop && exit 0
run_test "G5_D_vel05"   --noloss 0 --wt-exit-tfs "D" --wt-exit-mode velocity --wt-vel-threshold -0.5
should_stop && exit 0
run_test "G5_D_vel2"    --noloss 0 --wt-exit-tfs "D" --wt-exit-mode velocity --wt-vel-threshold -2

# ============================================================
# FINAL: Print sorted leaderboard
# ============================================================
echo ""
echo "$(date -u): =========================================="
echo "  LEADERBOARD (sorted by realized PnL)"
echo "=========================================="
sort -t'|' -k2 -rn "$LOG/htf_results.csv" | while IFS='|' read name pnl trades eq; do
    printf "  %-25s PnL: \$%-12s Trades: %-6s Equity: \$%s\n" "$name" "$pnl" "$trades" "$eq"
done
echo "=========================================="
echo "$(date -u): GRID COMPLETE"
