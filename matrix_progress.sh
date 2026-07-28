#!/bin/bash
# Live matrix-fill progress. Run this any time: ./matrix_progress.sh
# Reads the LIVE store (param_cells on S1), not the periodic CSV export, so it
# shows work the CSV has not caught up with yet.
cd /Users/niels/Documents/binance || exit 1

echo "════════════════════════════════════════════════════════════════"
echo " MATRIX FILL PROGRESS   $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "════════════════════════════════════════════════════════════════"

ssh s1-int 'cd /home/niels/binance-sandbox && /home/niels/.conda/envs/binance_env/bin/python - <<"PYEOF"
import sqlite3
c = sqlite3.connect("data/param_results_stocks.db")
q = c.execute
print("LIVE STORE (param_cells, ENGINE tier)")
print("  total ENGINE rows : %s" % q("select count(*) from param_cells where tier=\"ENGINE\"").fetchone()[0])
print("  newest write      : %s" % q("select max(ts) from param_cells where tier=\"ENGINE\"").fetchone()[0])
for label, window in (("last 15 min", "-15 minutes"), ("last hour", "-1 hours"), ("last 6 hours", "-6 hours")):
    n = q("select count(*) from param_cells where tier=\"ENGINE\" and ts > datetime(\"now\",?)", (window,)).fetchone()[0]
    print("  cells written %-12s : %s" % (label, n))
print()
print("PER PILOT (cells written in the last 6h)")
rows = q("""select symbol||"_"||side k, count(*) n, max(ts)
            from param_cells where tier="ENGINE" and ts > datetime("now","-6 hours")
            group by 1 order by n desc""").fetchall()
if not rows:
    print("  NONE — nothing written in 6h. Something is stuck.")
for k, n, last in rows:
    print("  %-12s %6d   last=%s" % (k, n, last))
PYEOF'

echo
echo "RUNNING DAEMONS"
ssh s1-int 'ps -eo cmd | grep "[p]aram_matrix_daemon" | grep -oE "only [A-Z]+ --side [A-Z]+" | sort | uniq -c' \
  || echo "  NONE RUNNING — the fill has stopped."

echo
echo "STUCK / FAILING (last line of each daemon log)"
ssh s1-int 'for f in /home/niels/logs/matrix_r*.log; do
    printf "  %-22s " "$(basename "$f")"
    tail -1 "$f" 2>/dev/null | head -c 100
    echo
  done'

echo
echo "CSV EXPORT (lags the live store; this is the scoreboard)"
python3 tools/matrix_guard.py 2>/dev/null | grep -E "FILLED|EMPTY|TOTAL GAP"
echo "════════════════════════════════════════════════════════════════"
