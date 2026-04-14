#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Sweep Cockpit — Run from MacBook to monitor/control sweep server
#
# Usage:
#   bash sweep_cockpit.sh status          # Show progress + leaderboard
#   bash sweep_cockpit.sh watch           # Live progress (refreshes every 30s)
#   bash sweep_cockpit.sh leaderboard     # Full leaderboard
#   bash sweep_cockpit.sh best            # Show best config found so far
#   bash sweep_cockpit.sh apply-crypto    # Download + apply best crypto config locally
#   bash sweep_cockpit.sh apply-tradier   # Download + apply best tradier config locally
#   bash sweep_cockpit.sh kill            # Stop the sweep
#   bash sweep_cockpit.sh ssh             # SSH into sweep server
# ═══════════════════════════════════════════════════════════════════

# ──── CONFIGURE THIS ────
SWEEP_SERVER="${SWEEP_SERVER:-10.0.0.4}"
SWEEP_USER="niels"
SWEEP_PY="/home/$SWEEP_USER/miniconda3/envs/binance_env/bin/python3"
LOCAL_BASE="/Users/niels/Documents/binance"
# ────────────────────────

SSH="ssh $SWEEP_USER@$SWEEP_SERVER"

case "${1:-status}" in
    status)
        echo "═══ CRYPTO SWEEP ═══"
        $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_crypto/progress.txt 2>/dev/null" || echo "Not started"
        echo ""
        echo "═══ TRADIER SWEEP ═══"
        $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_tradier/progress.txt 2>/dev/null" || echo "Not started"
        echo ""
        echo "═══ SERVER LOAD ═══"
        $SSH "uptime; echo ''; free -h | head -2; echo ''; df -h / | tail -1"
        ;;
    watch)
        echo "Watching sweep progress (Ctrl+C to stop)..."
        while true; do
            clear
            echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
            echo ""
            $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_crypto/progress.txt 2>/dev/null" || echo "Crypto: not started"
            echo ""
            $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_tradier/progress.txt 2>/dev/null" || echo "Tradier: not started"
            echo ""
            $SSH "uptime"
            sleep 30
        done
        ;;
    leaderboard)
        MODE="${2:-crypto}"
        echo "═══ ${MODE^^} LEADERBOARD ═══"
        $SSH "cd ~/binance && $SWEEP_PY backtest_v5_parallel_sweep.py --mode $MODE --status" 2>/dev/null
        ;;
    best)
        echo "═══ BEST CRYPTO CONFIG ═══"
        $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_crypto/best_config.json 2>/dev/null" | python3 -m json.tool || echo "No results yet"
        echo ""
        echo "═══ BEST TRADIER CONFIG ═══"
        $SSH "cat ~/binance-sandbox/backtest_v5/parallel_sweeps_tradier/best_config.json 2>/dev/null" | python3 -m json.tool || echo "No results yet"
        ;;
    apply-crypto)
        echo "Downloading best crypto config..."
        scp $SWEEP_USER@$SWEEP_SERVER:~/binance-sandbox/backtest_v5/parallel_sweeps_crypto/best_config.json /tmp/best_crypto.json
        echo "Best config:"
        python3 -m json.tool /tmp/best_crypto.json
        echo ""
        echo "To apply these overrides to config.py, review and run:"
        echo "  python3 $LOCAL_BASE/sweep_apply_config.py --mode crypto --config /tmp/best_crypto.json"
        ;;
    apply-tradier)
        echo "Downloading best tradier config..."
        scp $SWEEP_USER@$SWEEP_SERVER:~/binance-sandbox/backtest_v5/parallel_sweeps_tradier/best_config.json /tmp/best_tradier.json
        echo "Best config:"
        python3 -m json.tool /tmp/best_tradier.json
        echo ""
        echo "To apply these overrides to config_tradier.py, review and run:"
        echo "  python3 $LOCAL_BASE/sweep_apply_config.py --mode tradier --config /tmp/best_tradier.json"
        ;;
    kill)
        echo "Killing sweep on $SWEEP_SERVER..."
        $SSH "pkill -f backtest_v5_parallel_sweep || true; pkill -f backtest_v5_engine || true"
        echo "Done. Sweep stopped."
        ;;
    ssh)
        $SSH
        ;;
    *)
        echo "Usage: $0 {status|watch|leaderboard [crypto|tradier]|best|apply-crypto|apply-tradier|kill|ssh}"
        ;;
esac
