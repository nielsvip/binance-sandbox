#!/bin/bash
# Backtest status report — runs every 12h via cron
# Reports progress of all running backtests on local + server

REPORT_DIR="/Users/niels/Documents/binance/data/backtest_reports"
mkdir -p "$REPORT_DIR"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M")
REPORT="$REPORT_DIR/report_${TIMESTAMP}.txt"

echo "═══════════════════════════════════════════════════════════════" > "$REPORT"
echo "  BACKTEST STATUS REPORT — $(date -u '+%Y-%m-%d %H:%M UTC')" >> "$REPORT"
echo "═══════════════════════════════════════════════════════════════" >> "$REPORT"

# ── SERVER STATUS ──
echo "" >> "$REPORT"
echo "── SERVER (157.180.125.52) ──" >> "$REPORT"
SERVER_UP=$(ssh s1-int "uptime" 2>/dev/null || echo "UNREACHABLE")
echo "Uptime: $SERVER_UP" >> "$REPORT"

# Server processes
echo "" >> "$REPORT"
echo "Running backtests:" >> "$REPORT"
ssh s1-int "ps aux | grep 'python.*backtest' | grep -v grep | awk '{print \"  \", \$11, \$12, \$13, \$14, \$15}'" 2>/dev/null >> "$REPORT" || echo "  (unreachable)" >> "$REPORT"

# Hedge strategies progress
HEDGE_DONE=$(ssh s1-int "python3 -c \"import json; d=json.load(open('/home/niels/binance-sandbox/data/backtest_hedge_strategies/hedge_strategies_results.json')); print(len(d))\"" 2>/dev/null || echo "?")
echo "" >> "$REPORT"
echo "Hedge Strategies: ${HEDGE_DONE}/988 configs completed" >> "$REPORT"

# Hedge latest log
echo "Latest progress:" >> "$REPORT"
ssh s1-int "tail -5 /home/niels/binance-sandbox/data/backtest_hedge_strategies/massive_parallel.log" 2>/dev/null >> "$REPORT"

# Master revalidation check
MR_RUNNING=$(ssh s1-int "pgrep -f backtest_master_revalidation" 2>/dev/null)
if [ -n "$MR_RUNNING" ]; then
    echo "" >> "$REPORT"
    echo "Master Revalidation: RUNNING" >> "$REPORT"
    ssh s1-int "tail -3 /home/niels/logs/master_revalidation.log" 2>/dev/null >> "$REPORT"
fi

# SBA check
SBA_RUNNING=$(ssh s1-int "pgrep -f backtest_sba_mq" 2>/dev/null)
if [ -n "$SBA_RUNNING" ]; then
    echo "" >> "$REPORT"
    echo "SBA v2: RUNNING" >> "$REPORT"
    ssh s1-int "tail -3 /home/niels/logs/sba_v2.log" 2>/dev/null >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "Server CPU:" >> "$REPORT"
ssh s1-int "mpstat 1 1 2>/dev/null | tail -1 || top -bn1 | head -3" 2>/dev/null >> "$REPORT"

# ── LOCAL STATUS ──
echo "" >> "$REPORT"
echo "── LOCAL (MacBook) ──" >> "$REPORT"
echo "Uptime: $(uptime)" >> "$REPORT"
echo "CPU cores: $(sysctl -n hw.ncpu)" >> "$REPORT"

echo "" >> "$REPORT"
echo "Running backtests:" >> "$REPORT"
ps aux | grep 'python.*backtest' | grep -v grep | awk '{print "  ", $11, $12, $13}' >> "$REPORT" 2>/dev/null || echo "  (none)" >> "$REPORT"

# WT Ultimate progress
if [ -f "/Users/niels/Documents/binance/data/backtest_wt_ultimate/checkpoint.json" ]; then
    echo "" >> "$REPORT"
    echo "WT Ultimate checkpoint:" >> "$REPORT"
    python3 -c "
import json
d = json.load(open('/Users/niels/Documents/binance/data/backtest_wt_ultimate/checkpoint.json'))
phases = d.get('completed_phases', [])
print(f'  Completed phases: {phases}')
for p in ['phase_a','phase_b','phase_c','phase_d']:
    data = d.get(p, {})
    if data:
        print(f'  {p}: {len(data)} entries')
" >> "$REPORT" 2>/dev/null
fi

# WT 15m Deep progress
if [ -f "/Users/niels/Documents/binance/data/backtest_wt_15m_deep/checkpoint.json" ]; then
    echo "" >> "$REPORT"
    echo "WT 15m Deep checkpoint:" >> "$REPORT"
    python3 -c "
import json
d = json.load(open('/Users/niels/Documents/binance/data/backtest_wt_15m_deep/checkpoint.json'))
for p in ['phase1','phase2']:
    data = d.get(p, {})
    if data:
        print(f'  {p}: {len(data)} entries')
    else:
        print(f'  {p}: not started')
print(f'  baseline: {\"complete\" if d.get(\"baseline\",{}).get(\"complete\") else \"pending\"}')
" >> "$REPORT" 2>/dev/null
fi

# WT Ultimate log tail
if [ -f "/Users/niels/Documents/binance/data/backtest_wt_ultimate/backtest_wt_ultimate.log" ]; then
    echo "" >> "$REPORT"
    echo "WT Ultimate latest:" >> "$REPORT"
    tail -5 /Users/niels/Documents/binance/data/backtest_wt_ultimate/backtest_wt_ultimate.log >> "$REPORT"
fi

# WT 15m Deep log tail
if [ -f "/Users/niels/Documents/binance/data/backtest_wt_15m_deep/backtest_wt_15m_deep.log" ]; then
    echo "" >> "$REPORT"
    echo "WT 15m Deep latest:" >> "$REPORT"
    tail -5 /Users/niels/Documents/binance/data/backtest_wt_15m_deep/backtest_wt_15m_deep.log >> "$REPORT"
fi

# Trading services count
TRADING_COUNT=$(ps aux | grep 'python.*ez_\|python.*tradier_' | grep -v grep | wc -l)
echo "" >> "$REPORT"
echo "Trading services running: $TRADING_COUNT" >> "$REPORT"

echo "" >> "$REPORT"
echo "═══════════════════════════════════════════════════════════════" >> "$REPORT"
echo "  END OF REPORT" >> "$REPORT"
echo "═══════════════════════════════════════════════════════════════" >> "$REPORT"

# Print to stdout and save
cat "$REPORT"

# Keep last 14 reports (7 days)
ls -t "$REPORT_DIR"/report_*.txt 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null
