#!/bin/bash
# live OFAT progress + health for /focus4 (USER 2026-07-20 emergency-brake view)
ssh -o ConnectTimeout=10 s1-int '/home/niels/.conda/envs/binance_env/bin/python - <<PY
import sqlite3, json
C="stocks_baseline_v2_s4h"
c=sqlite3.connect("/home/niels/binance-sandbox/data/param_results_stocks.db")
cells=c.execute("SELECT COUNT(*) FROM param_cells WHERE campaign=?",(C,)).fetchone()[0]
params=c.execute("SELECT COUNT(DISTINCT param) FROM param_cells WHERE campaign=?",(C,)).fetchone()[0]
zero=c.execute("SELECT COUNT(*) FROM param_cells WHERE campaign=? AND (delta_vs_baseline_gain_mo IS NULL OR delta_vs_baseline_gain_mo=0)",(C,)).fetchone()[0]
notrade=c.execute("SELECT COUNT(*) FROM param_cells WHERE campaign=? AND (trades IS NULL OR trades=0)",(C,)).fetchone()[0]
worse=c.execute("SELECT COUNT(*) FROM param_cells WHERE campaign=? AND delta_vs_baseline_gain_mo<0",(C,)).fetchone()[0]
better=c.execute("SELECT COUNT(*) FROM param_cells WHERE campaign=? AND delta_vs_baseline_gain_mo>0",(C,)).fetchone()[0]
rows=[dict(zip(("symbol","side","param","value","gain_mo","delta","trades","ts"),r)) for r in c.execute(
 "SELECT symbol,side,param,value_json,ROUND(gain_per_mo,4),ROUND(delta_vs_baseline_gain_mo,4),trades,ts FROM param_cells WHERE campaign=? ORDER BY ts DESC LIMIT 25",(C,))]
per={}
for r in c.execute("SELECT symbol,side,COUNT(*),ROUND(AVG(delta_vs_baseline_gain_mo),4),ROUND(MIN(delta_vs_baseline_gain_mo),4),ROUND(MAX(delta_vs_baseline_gain_mo),4) FROM param_cells WHERE campaign=? GROUP BY symbol,side",(C,)):
    per[r[0]+"_"+r[1]]={"cells":r[2],"avg_delta":r[3],"min_delta":r[4],"max_delta":r[5]}
alarms=[]
if cells and zero/cells>0.5: alarms.append("OVER HALF OF CELLS HAVE ZERO/NULL DELTA - overrides may be inert or baseline missing")
if cells and notrade/cells>0.3: alarms.append("MANY CELLS HAVE ZERO TRADES - engine may be blocking entries")
if worse>better*3 and worse>20: alarms.append("DELTAS MOSTLY NEGATIVE - config drifting worse than baseline")
print(json.dumps({"campaign":C,"cells":cells,"params_covered":params,"zero_delta":zero,
 "zero_trades":notrade,"worse":worse,"better":better,"per_key":per,"latest":rows,"alarms":alarms},indent=1))
PY' > /Users/niels/Documents/binance/data/_diagnostic/ofat_live.json 2>/dev/null
