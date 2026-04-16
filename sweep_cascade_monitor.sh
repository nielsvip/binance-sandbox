#!/bin/bash
# Cascade monitor — reads sweep CSVs every 15min, logs best Sharpe per phase,
# alerts when target 1.8 hit, triggers deeper sweeps on winners.
set -uo pipefail

BASE="${1:-/Users/niels/Documents/binance}"
PY="${2:-/opt/anaconda3/envs/binance_env/bin/python}"
cd "$BASE"

LOG="/tmp/sweep_cascade_monitor.log"
DASHBOARD="$BASE/data/sweep_results/DASHBOARD.md"

log() { echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "$LOG"; }

write_dashboard() {
    cat > "$DASHBOARD" <<EOF
# Live Sweep Dashboard
_Updated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')_

## Current Best Sharpe

| Sweep | Mode | Best Sharpe | Best Sharpe_pt | Trades | WR% | Config |
|-------|------|-------------|----------------|--------|-----|--------|
EOF

    for csv in $BASE/data/sweep_results/*.csv; do
        [[ -f "$csv" ]] || continue
        local name=$(basename "$csv" .csv)
        $PY -c "
import csv, sys, os
try:
    rows = list(csv.DictReader(open('$csv')))
    if not rows: sys.exit(0)
    # Find sharpe col (could be 'sharpe' or 'sharpe_ann')
    sharpe_col = 'sharpe' if 'sharpe' in rows[0] else ('sharpe_ann' if 'sharpe_ann' in rows[0] else None)
    if not sharpe_col: sys.exit(0)
    rows.sort(key=lambda r: -float(r.get(sharpe_col, 0) or 0))
    r = rows[0]
    sharpe_pt = r.get('sharpe_pt', '?')
    mode = 'tradier' if 'tradier' in '$name' else ('crypto' if 'crypto' in '$name' else '?')
    trades = r.get('trades', '?')
    wr_val = r.get('wr')
    if not wr_val:
        try: wr_val = f\"{100*int(r.get('wins',0))/max(1,int(r.get('trades',1))):.1f}\"
        except: wr_val = '?'
    cfg_keys = [k for k in r if k.startswith('cfg_') or k.startswith('REENTRY_') or k in ('ENTRY_SCORE_THRESHOLD','K3M_FLOOR')]
    cfg_summary = ' '.join(f'{k[4:] if k.startswith(\"cfg_\") else k}={r[k]}' for k in cfg_keys[:6])[:80]
    print(f'| {\"$name\"[:40]} | {mode} | {float(r.get(sharpe_col,0)):.3f} | {sharpe_pt} | {trades} | {wr_val}% | {cfg_summary} |')
except Exception as e:
    pass
" >> "$DASHBOARD"
    done

    echo "" >> "$DASHBOARD"
    echo "## Active Processes" >> "$DASHBOARD"
    echo "\`\`\`" >> "$DASHBOARD"
    ps aux | grep -E "sweep_|v8_quick" | grep -v grep | awk '{print $2, $11, $12, $13, $14}' | head -10 >> "$DASHBOARD"
    echo "\`\`\`" >> "$DASHBOARD"
}

log "Cascade monitor started"

while true; do
    write_dashboard

    # Detect new winners (Sharpe > 0.5 means breakthrough from 0.3 plateau)
    for csv in "$BASE"/data/sweep_results/quality_sniper_*.csv "$BASE"/data/sweep_results/reentry_blocks_*.csv; do
        [[ -f "$csv" ]] || continue
        BEST=$($PY -c "
import csv
rows = list(csv.DictReader(open('$csv')))
if not rows: print(0); exit()
sharpe_col = 'sharpe' if 'sharpe' in rows[0] else 'sharpe_ann'
rows.sort(key=lambda r: -float(r.get(sharpe_col,0) or 0))
print(f'{float(rows[0].get(sharpe_col,0)):.3f}')
" 2>/dev/null || echo 0)
        log "$(basename $csv) best=$BEST"
        if (( $(echo "$BEST > 1.8" | bc -l 2>/dev/null) )); then
            log "🎯🎯🎯 TARGET HIT: $(basename $csv) Sharpe=$BEST"
        fi
    done

    sleep 900  # 15 minutes
done
