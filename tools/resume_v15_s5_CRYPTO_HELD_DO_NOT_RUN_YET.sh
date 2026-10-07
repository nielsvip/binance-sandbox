#!/bin/bash
set -euo pipefail
# 🔴 HELD — DO NOT RUN until stocks done (204 stocks final_gain)
# Crypto USDC/USDT only on s1 2 3 5 AFTER stocks
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s5"
#
# BLOCKED until stocks done — uncomment after verification
# if [ "$(python3 -c 'import json,glob; print(sum(1 for f in glob.glob("data/reports/lifecycle_pilot/*_v14_progress.json") if json.loads(open(f).read()).get("final_gain") is not None and not f.split("/")[-1].split("_")[0].endswith(("USDT","USDC"))))' )" -lt 204 ]; then echo "BLOCKED stocks not done"; exit 2; fi

# echo "=== ACEUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ACEUSDT_LONG --window-days 30 || echo "[warn] ACEUSDT_LONG exit $?"
# echo "=== ALGOUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ALGOUSDT_SHORT --window-days 30 || echo "[warn] ALGOUSDT_SHORT exit $?"
# echo "=== ATOMUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ATOMUSDT_LONG --window-days 30 || echo "[warn] ATOMUSDT_LONG exit $?"
# echo "=== AXSUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side AXSUSDT_SHORT --window-days 30 || echo "[warn] AXSUSDT_SHORT exit $?"
# echo "=== BIOUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BIOUSDC_SHORT --window-days 30 || echo "[warn] BIOUSDC_SHORT exit $?"
# echo "=== CHRUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CHRUSDT_LONG --window-days 30 || echo "[warn] CHRUSDT_LONG exit $?"
# echo "=== COTIUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side COTIUSDT_LONG --window-days 30 || echo "[warn] COTIUSDT_LONG exit $?"
# echo "=== DASHUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DASHUSDT_SHORT --window-days 30 || echo "[warn] DASHUSDT_SHORT exit $?"
# echo "=== DOTUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DOTUSDT_SHORT --window-days 30 || echo "[warn] DOTUSDT_SHORT exit $?"
# echo "=== ENJUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ENJUSDT_LONG --window-days 30 || echo "[warn] ENJUSDT_LONG exit $?"
# echo "=== ETCUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ETCUSDT_SHORT --window-days 30 || echo "[warn] ETCUSDT_SHORT exit $?"
# echo "=== FILUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side FILUSDC_SHORT --window-days 30 || echo "[warn] FILUSDC_SHORT exit $?"
# echo "=== HYPEUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side HYPEUSDT_LONG --window-days 30 || echo "[warn] HYPEUSDT_LONG exit $?"
# echo "=== INJUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side INJUSDT_LONG --window-days 30 || echo "[warn] INJUSDT_LONG exit $?"
# echo "=== KASUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side KASUSDT_SHORT --window-days 30 || echo "[warn] KASUSDT_SHORT exit $?"
# echo "=== LPTUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side LPTUSDT_SHORT --window-days 30 || echo "[warn] LPTUSDT_SHORT exit $?"
# echo "=== MANAUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MANAUSDT_SHORT --window-days 30 || echo "[warn] MANAUSDT_SHORT exit $?"
# echo "=== MORPHOUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MORPHOUSDT_LONG --window-days 30 || echo "[warn] MORPHOUSDT_LONG exit $?"
# echo "=== NOTUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side NOTUSDT_SHORT --window-days 30 || echo "[warn] NOTUSDT_SHORT exit $?"
# echo "=== PENGUUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PENGUUSDC_SHORT --window-days 30 || echo "[warn] PENGUUSDC_SHORT exit $?"
# echo "=== PUMPUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PUMPUSDT_LONG --window-days 30 || echo "[warn] PUMPUSDT_LONG exit $?"
# echo "=== RLCUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RLCUSDT_LONG --window-days 30 || echo "[warn] RLCUSDT_LONG exit $?"
# echo "=== SANDUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SANDUSDT_LONG --window-days 30 || echo "[warn] SANDUSDT_LONG exit $?"
# echo "=== SKYUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SKYUSDT_SHORT --window-days 30 || echo "[warn] SKYUSDT_SHORT exit $?"
# echo "=== SOXLUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SOXLUSDT_SHORT --window-days 30 || echo "[warn] SOXLUSDT_SHORT exit $?"
# echo "=== THETAUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side THETAUSDT_LONG --window-days 30 || echo "[warn] THETAUSDT_LONG exit $?"
# echo "=== TRUMPUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TRUMPUSDC_SHORT --window-days 30 || echo "[warn] TRUMPUSDC_SHORT exit $?"
# echo "=== VETUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side VETUSDT_SHORT --window-days 30 || echo "[warn] VETUSDT_SHORT exit $?"
# echo "=== WLDUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side WLDUSDC_SHORT --window-days 30 || echo "[warn] WLDUSDC_SHORT exit $?"
# echo "=== XMRUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XMRUSDT_SHORT --window-days 30 || echo "[warn] XMRUSDT_SHORT exit $?"
# echo "=== XTZUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XTZUSDT_SHORT --window-days 30 || echo "[warn] XTZUSDT_SHORT exit $?"
# echo "=== ZROUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ZROUSDT_SHORT --window-days 30 || echo "[warn] ZROUSDT_SHORT exit $?"