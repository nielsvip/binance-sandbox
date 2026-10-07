#!/bin/bash
D=/Users/niels/Documents/binance/data
mkdir -p $D/_diagnostic/focus4_coverage $D/reports
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/_diagnostic/focus4_scoreboard.json $D/_diagnostic/ 2>/dev/null
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/_diagnostic/focus4_coverage/ $D/_diagnostic/focus4_coverage/ 2>/dev/null
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/reports/stocks_bh_capture.json $D/reports/ 2>/dev/null
ssh -o ConnectTimeout=10 s1-int 'cd /home/niels/binance-sandbox && /home/niels/.conda/envs/binance_env/bin/python -c "
import sqlite3
c=sqlite3.connect(\"data/param_results_stocks.db\")
print(\"param_results_stocks.db: baselines=%d param_cells=%d distinct_params=%d\" % (
  c.execute(\"SELECT COUNT(*) FROM key_baseline\").fetchone()[0],
  c.execute(\"SELECT COUNT(*) FROM param_cells\").fetchone()[0],
  c.execute(\"SELECT COUNT(DISTINCT param) FROM param_cells\").fetchone()[0]))
for r in c.execute(\"SELECT symbol,side,param,value_json,ROUND(gain_per_mo,3),trades FROM param_cells ORDER BY ts DESC LIMIT 10\"):
  print(\"  \", r)
"; echo "--- ofat log:"; tail -4 /home/niels/logs/ofat_focus4.log 2>/dev/null; echo "--- ladder log:"; tail -4 /home/niels/logs/psc_winner_band_v2.log 2>/dev/null' > /Users/niels/Documents/binance/data/_diagnostic/ofat_progress.txt 2>/dev/null
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/_diagnostic/switch_ladder/ /Users/niels/Documents/binance/data/_diagnostic/switch_ladder/ 2>/dev/null
/opt/anaconda3/envs/binance_env/bin/python - <<'PY' 2>/dev/null
import json, glob, os
d = {}
for f in glob.glob("/Users/niels/Documents/binance/data/_diagnostic/switch_ladder/*_valued.json"):
    sym = os.path.basename(f).replace("_valued.json", "")
    try:
        d[sym] = json.load(open(f))
    except Exception:
        pass
json.dump(d, open("/Users/niels/Documents/binance/data/_diagnostic/switch_ladder/_all.json", "w"))
PY
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/reports/LAB_SWITCH_MATRIX.csv /Users/niels/Documents/binance/data/reports/ 2>/dev/null
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/reports/SWITCH_MATRIX_TRB.csv.gz /Users/niels/Documents/binance/data/reports/ 2>/dev/null
/usr/bin/rsync -az --timeout=60 s1-int:/home/niels/binance-sandbox/data/reports/SWITCH_MATRIX_TRB.xlsx /Users/niels/Documents/binance/data/reports/ 2>/dev/null
ssh -o ConnectTimeout=10 s1-int 'cd /home/niels/binance-sandbox && /home/niels/.conda/envs/binance_env/bin/python tools/export_switch_matrix_xls.py --campaign stocks_baseline_v2_s4h' >/dev/null 2>&1
bash /Users/niels/Documents/binance/scripts/ofat_progress.sh >/dev/null 2>&1
