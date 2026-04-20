#!/bin/bash
# Exit-rule tournament — tries every exit rule on mix_12 sector.
# Per rule: pick=3, 45s max, 15s bail at robust<2. Picks winners fast.
set -u

RULES=(wt15 wt5 dc15 dc5 late_safety delta_slow delta_slow_15m multi_tf_wt wt_state_15m_1h stoch_ob_cross ha_flip combined_primary combined_all fast_wt_dc_5m fast_wt_dc_15m)
SECTOR=${1:-mix_12}
PY=/home/niels/miniconda3/envs/binance_env/bin/python
CD=/home/niels/binance-sandbox
OUT=/home/niels/logs/exit_tournament_${SECTOR}.log
> "$OUT"
echo "=== EXIT TOURNAMENT sector=$SECTOR @ $(date -u +%FT%TZ) ===" | tee -a "$OUT"

for rule in "${RULES[@]}"; do
    db="/tmp/extourn_${SECTOR}_${rule}.sqlite"
    rm -f "$db"
    echo -e "\n--- RULE: $rule ---" | tee -a "$OUT"
    timeout 60 "$PY" -u "$CD/vec_stock_mega.py" \
        --sector "$SECTOR" --bars 40000 --pick 3 --workers 6 \
        --time-budget-s 45 --bail-after-s 15 --bail-min-robust 2.0 \
        --floor-robust 0.5 --elite-robust 2.0 \
        --exit-rule "$rule" --out-db "$db" 2>&1 | tail -30 | tee -a "$OUT"
done

echo -e "\n\n=== TOURNAMENT SUMMARY (top-1 per rule) ===" | tee -a "$OUT"
for rule in "${RULES[@]}"; do
    db="/tmp/extourn_${SECTOR}_${rule}.sqlite"
    if [ -f "$db" ]; then
        result=$("$PY" -c "
import sqlite3
try:
    c = sqlite3.connect('$db')
    r = c.execute('SELECT sharpe_robust, sharpe_avg, sharpe_pool, wr_avg, n_trades_total, syms_included, side, combo FROM results ORDER BY sharpe_robust DESC LIMIT 1').fetchone()
    n = c.execute('SELECT COUNT(*) FROM results').fetchone()[0]
    if r:
        print(f'  {r[0]:.3f}  avg={r[1]:.2f} pool={r[2]:.2f} WR={r[3]:.1f}% n={r[4]} syms={r[5]} {r[6]} {r[7][:55]}  [kept={n}]')
    else:
        print(f'  NONE  [kept={n}]')
    c.close()
except Exception as e:
    print(f'  ERR {e}')
")
        printf "%-25s %s\n" "$rule" "$result" | tee -a "$OUT"
    fi
done
