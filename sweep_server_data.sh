#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Sweep Data Sync + Launch — Run on new Hetzner box AFTER setup
# Syncs data from existing server, installs deps, launches sweep
#
# Usage:
#   ssh niels@<NEW_IP> bash ~/binance/sweep_server_data.sh
# ═══════════════════════════════════════════════════════════════════

set -e

EXISTING="10.0.0.2"
CONDA_PY="$HOME/miniconda3/envs/binance_env/bin/python3"
# Fallback to existing conda path
[ -f "$CONDA_PY" ] || CONDA_PY="$HOME/.conda/envs/binance_env/bin/python3"

echo "═══ STEP 1: Create directory structure ═══"
mkdir -p ~/binance
mkdir -p ~/binance-sandbox/backtest_v4/indicators
mkdir -p ~/binance-sandbox/backtest_v4_tradier/indicators
mkdir -p ~/binance-sandbox/backtest_v5/indicators_3m
mkdir -p ~/binance-sandbox/backtest_v5/indicators_5m_tradier
mkdir -p ~/binance-sandbox/backtest_v5/logs
mkdir -p ~/binance-sandbox/klines_cache

echo "═══ STEP 2: Sync code from existing server ═══"
rsync -avz --progress $EXISTING:/home/niels/binance/*.py ~/binance/
rsync -avz --progress $EXISTING:/home/niels/binance/config*.py ~/binance/
rsync -avz --progress $EXISTING:/home/niels/binance/utils.py ~/binance/

echo "═══ STEP 3: Sync precomputed NPZ data (the big one) ═══"
echo "Syncing crypto 3m indicators (~2.4GB)..."
rsync -avz --progress $EXISTING:/home/niels/binance-sandbox/backtest_v5/indicators_3m/ ~/binance-sandbox/backtest_v5/indicators_3m/

echo "Syncing crypto 15m indicators (~1.8GB)..."
rsync -avz --progress $EXISTING:/home/niels/binance-sandbox/backtest_v4/indicators/ ~/binance-sandbox/backtest_v4/indicators/

echo "Syncing tradier 5m indicators..."
rsync -avz --progress $EXISTING:/home/niels/binance-sandbox/backtest_v5/indicators_5m_tradier/ ~/binance-sandbox/backtest_v5/indicators_5m_tradier/ 2>/dev/null || \
    echo "No 5m tradier data yet — will use 15m fallback"

echo "Syncing tradier 15m indicators (~730MB)..."
rsync -avz --progress $EXISTING:/home/niels/binance-sandbox/backtest_v4_tradier/indicators/ ~/binance-sandbox/backtest_v4_tradier/indicators/

echo "═══ STEP 4: Install Python dependencies ═══"
$CONDA_PY -m pip install -q numpy pandas aiohttp websockets redis requests ta 2>/dev/null || true

echo "═══ STEP 5: Verify engine can import ═══"
cd ~/binance
$CONDA_PY -c "import py_compile; py_compile.compile('backtest_v5_engine.py', doraise=True)" && echo "Engine compiles OK" || echo "ENGINE COMPILE FAILED"
$CONDA_PY -c "import py_compile; py_compile.compile('backtest_v5_parallel_sweep.py', doraise=True)" && echo "Sweep compiles OK" || echo "SWEEP COMPILE FAILED"

echo "═══ STEP 6: Quick smoke test (1 symbol, baseline) ═══"
timeout 120 $CONDA_PY backtest_v5_engine.py --mode crypto --max-symbols 1 --start 2025-01-01 2>&1 | tail -5 || echo "Smoke test had issues — check manually"

CORES=$(nproc)
WORKERS=$((CORES - 2))
[ $WORKERS -lt 1 ] && WORKERS=1

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  READY TO SWEEP"
echo "  Cores: $CORES | Workers: $WORKERS"
echo "  Data synced from $EXISTING"
echo ""
echo "  Launch full sweep (both systems, max 7 days):"
echo "    screen -S sweep $CONDA_PY backtest_v5_parallel_sweep.py --mode both --workers $WORKERS --max-hours 168"
echo ""
echo "  Launch crypto only (faster):"
echo "    screen -S sweep $CONDA_PY backtest_v5_parallel_sweep.py --mode crypto --workers $WORKERS --max-hours 84"
echo ""
echo "  Monitor from MacBook:"
echo "    ssh niels@<THIS_IP> cat ~/binance-sandbox/backtest_v5/parallel_sweeps_crypto/progress.txt"
echo "    ssh niels@<THIS_IP> cat ~/binance-sandbox/backtest_v5/parallel_sweeps_tradier/progress.txt"
echo ""
echo "  View leaderboard:"
echo "    ssh niels@<THIS_IP> '$CONDA_PY ~/binance/backtest_v5_parallel_sweep.py --status'"
echo "════════════════════════════════════════════════════════════════"
