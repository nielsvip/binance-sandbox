#!/bin/bash
# run_function_grid.sh — Run function call grid analysis with auto-compare
# Usage: ./scripts/run_function_grid.sh
# Saves timestamped XLS and compares against previous run if available

cd /Users/niels/Documents/binance
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
TS=$(date -u +"%Y%m%d_%H%M_UTC")
OUTDIR="function_grids"
mkdir -p "$OUTDIR"

echo "=== Function Call Grid Analysis — $TS ==="

# Step 1: Run the grid analysis
echo "[1/3] Building function call grid..."
$PYTHON scripts/function_call_grid.py 2>&1
cp function_call_grid.xlsx "$OUTDIR/function_call_grid_${TS}.xlsx"

# Step 2: Run dead code analysis (adds DEAD_CODE sheet)
echo "[2/3] Running dead code analysis..."
$PYTHON scripts/dead_code_analysis.py 2>&1

# Step 3: Copy final result
cp function_call_grid.xlsx "$OUTDIR/function_call_grid_${TS}.xlsx"
echo ""
echo "Saved: $OUTDIR/function_call_grid_${TS}.xlsx"

# Step 4: Find previous run for comparison
PREV=$(ls -t "$OUTDIR"/function_call_grid_*.xlsx 2>/dev/null | grep -v "$TS" | head -1)
if [ -n "$PREV" ]; then
    echo ""
    echo "Previous run found: $PREV"
    echo "To compare, start Claude Code and say:"
    echo "  Compare function_grids/$(basename $PREV) vs function_grids/function_call_grid_${TS}.xlsx"
else
    echo ""
    echo "No previous run found — this is the baseline."
fi

echo ""
echo "Done."
