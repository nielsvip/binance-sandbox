#!/bin/bash
set -euo pipefail
LOG="/tmp/mac_v15_pull_new.log"
exec >> "$LOG" 2>&1
echo "=== mac_v15_pull_new $(date -u) ==="
TARGETS=(MU_LONG SNDK_LONG NVDA_LONG GOOGL_LONG META_LONG MSFT_LONG AAPL_LONG ASML_LONG TSLA_LONG MRVL_LONG RBLX_LONG INTC_LONG MSTR_LONG VLO_LONG GLD_LONG AMZN_SHORT MSTR_SHORT MU_SHORT NVDA_SHORT QQQ_LONG)
MAC_OUT="/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL"
for sym in "${TARGETS[@]}"; do
  for host in s2 s3-pub s5-pub; do
    remote_xlsx="/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/${sym}_30d_matrix.xlsx"
    remote_prog="/home/niels/binance-sandbox/data/reports/lifecycle_pilot/${sym}_v14_progress.json"
    local_xlsx="${MAC_OUT}/${sym}_30d_matrix.xlsx"
    if ssh -o ConnectTimeout=5 $host "test -f $remote_xlsx" 2>/dev/null; then
      rsync -auz -e "ssh -o ConnectTimeout=10" "$host:$remote_xlsx" "$local_xlsx" 2>&1 | head -1
      rsync -auz -e "ssh -o ConnectTimeout=10" "$host:$remote_prog" "/Users/niels/Documents/binance/data/reports/lifecycle_pilot/${sym}_v14_progress.json" 2>&1 | head -1
      break
    fi
  done
done
/opt/anaconda3/envs/binance_env/bin/python << 'PY' >> "$LOG" 2>&1
import openpyxl, pathlib, json
from pathlib import Path
MAC_OUT=Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL")
TARGETS=["MU_LONG","SNDK_LONG","NVDA_LONG","GOOGL_LONG","META_LONG","MSFT_LONG","AAPL_LONG","ASML_LONG","TSLA_LONG","MRVL_LONG","RBLX_LONG","INTC_LONG","MSTR_LONG","VLO_LONG","GLD_LONG","AMZN_SHORT","MSTR_SHORT","MU_SHORT","NVDA_SHORT","QQQ_LONG"]
for sym in TARGETS:
    p=MAC_OUT/f"{sym}_30d_matrix.xlsx"
    prog=Path(f"/Users/niels/Documents/binance/data/reports/lifecycle_pilot/{sym}_v14_progress.json")
    if not p.exists():
        print(f"{sym}: MISSING")
        continue
    try:
        wb=openpyxl.load_workbook(str(p), data_only=False)
        sheets=len([s for s in wb.sheetnames if s not in ["LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2","Results_Deltas","Results_30d_Deltas"]])
        ws=wb["ENTRY_REVERSAL_BOUNCE"]
        f=sum(1 for r in range(3,9) if isinstance(ws.cell(r,6).value,float))
        g=sum(1 for r in range(3,9) if isinstance(ws.cell(r,7).value,float))
        v=any(isinstance(ws.cell(r,7).value,str) and "VLOOKUP" in str(ws.cell(r,7).value) for r in range(3,9))
        wb.close()
        prog_msg=""
        if prog.exists():
            j=json.loads(prog.read_text())
            done=len(j.get("done",{})); sheets_p=len(set(k.split("!")[0] for k in j.get("done",{}))); cum=j.get("cumulative_gain",0); base=j.get("baseline_gain",0)
            prog_msg=f"prog {done} {sheets_p}/13 cum{cum:.1f} d{cum-base:.1f}"
        print(f"{sym}: sheets {sheets}/13 F{f} G{g} vlook{v} {p.stat().st_size/1e3:.0f}K {prog_msg}")
    except Exception as e:
        print(f"{sym}: err {e}")
PY
echo "done immediate"
/usr/bin/python3 /Users/niels/Documents/binance/tools/prune_v15_v16_cell_by_cell.py >> /tmp/prune_cell_mac.log 2>&1
echo "prune after pull: $(date -u) kept latest per sym_side"
