#!/bin/bash
# Server backtest queue — runs ALL pending tests sequentially, maximizing CPU
# Chains: hedge_strategies(running) → master_reval → SBA v2 → WT 15m deep → V4 sweep → V4 tradier sweep
# Usage: nohup bash backtest_queue_server.sh > /home/niels/logs/backtest_queue.log 2>&1 &
# DEADLINE: Must complete by 2026-03-30 00:00 UTC

set -e
PYTHON="/home/niels/.conda/envs/binance_env/bin/python"
SANDBOX="/home/niels/binance-sandbox"
MAIN="/home/niels/binance"
WORKERS=15

echo "$(date -u) === BACKTEST QUEUE STARTED (6 tests, deadline 2026-03-30) ==="

# 1. Wait for hedge_strategies to finish (est ~11h from 03:15 UTC → ~14:15 UTC)
echo "$(date -u) [1/6] Waiting for hedge_strategies to complete (988 configs, 652 remaining)..."
while pgrep -f "backtest_hedge_strategies" > /dev/null 2>&1; do
    sleep 60
done
echo "$(date -u) [1/6] hedge_strategies DONE ✓"

# 2. Master Revalidation (SKIPPED — wrong args, re-queued as test 7)
echo "$(date -u) [2/6] SKIPPED — wrong CLI args, re-queued as test 7"

# 3. SBA v2 (SKIPPED — wrong args, re-queued as test 8)
echo "$(date -u) [3/6] SKIPPED — wrong CLI args, re-queued as test 8"

# 4. WT 15m Deep Sweep (baseline done, phase1+2 empty, ~160 combos)
echo "$(date -u) [4/6] Starting WT 15m Deep Sweep (160 combos, ~1.5h est)..."
cd "$MAIN"
$PYTHON backtest_wt_15m_deep.py --workers $WORKERS --resume 2>&1 | tee /home/niels/logs/wt_15m_deep.log || echo "$(date -u) WARNING: wt_15m_deep exited with error"
echo "$(date -u) [4/6] WT 15m Deep Sweep DONE ✓"

# 5. V4 Fix Sweep — Crypto (24 configs × 48 symbols)
echo "$(date -u) [5/6] Starting V4 Fix Sweep Crypto (~30min est)..."
cd "$SANDBOX"
$PYTHON backtest_v4_fix_sweep.py 2>&1 | tee /home/niels/logs/v4_fix_sweep.log || echo "$(date -u) WARNING: v4_fix_sweep exited with error"
echo "$(date -u) [5/6] V4 Fix Sweep Crypto DONE ✓"

# 6. V4 Sweep Tradier — Stocks
echo "$(date -u) [6/6] Starting V4 Sweep Tradier (~30min est)..."
cd "$SANDBOX"
$PYTHON backtest_v4_sweep_tradier.py 2>&1 | tee /home/niels/logs/v4_sweep_tradier.log || echo "$(date -u) WARNING: v4_sweep_tradier exited with error"
echo "$(date -u) [6/6] V4 Sweep Tradier DONE ✓"

# 7. Master Revalidation (re-run with correct args)
echo "$(date -u) [7/8] Starting Master Revalidation (correct args: --max-symbols 60)..."
cd "$MAIN"
$PYTHON backtest_master_revalidation.py --max-symbols 60 2>&1 | tee /home/niels/logs/master_revalidation.log || echo "$(date -u) WARNING: master_revalidation exited with error"
echo "$(date -u) [7/8] Master Revalidation DONE"

# 8. SBA v2 (re-run with correct args)
echo "$(date -u) [8/8] Starting SBA v2 (correct args: --workers $WORKERS)..."
cd "$MAIN"
$PYTHON backtest_sba_mq.py --workers $WORKERS 2>&1 | tee /home/niels/logs/sba_v2.log || echo "$(date -u) WARNING: sba_mq exited with error"
echo "$(date -u) [8/8] SBA v2 DONE"

echo "$(date -u) ═══════════════════════════════════════"
echo "$(date -u) === ALL 8 QUEUED BACKTESTS COMPLETE ==="
echo "$(date -u) ═══════════════════════════════════════"
