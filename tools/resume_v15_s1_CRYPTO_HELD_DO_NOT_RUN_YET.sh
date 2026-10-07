#!/bin/bash
set -euo pipefail
# 🔴 HELD — DO NOT RUN until stocks done (204 stocks final_gain)
# Crypto USDC/USDT only on s1 2 3 5 AFTER stocks
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s1"
#
# BLOCKED until stocks done — uncomment after verification
# if [ "$(python3 -c 'import json,glob; print(sum(1 for f in glob.glob("data/reports/lifecycle_pilot/*_v14_progress.json") if json.loads(open(f).read()).get("final_gain") is not None and not f.split("/")[-1].split("_")[0].endswith(("USDT","USDC"))))' )" -lt 204 ]; then echo "BLOCKED stocks not done"; exit 2; fi

# echo "=== 1000FLOKIUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side 1000FLOKIUSDT_LONG --window-days 30 || echo "[warn] 1000FLOKIUSDT_LONG exit $?"
# echo "=== ADAUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ADAUSDC_LONG --window-days 30 || echo "[warn] ADAUSDC_LONG exit $?"
# echo "=== APPUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side APPUSDT_SHORT --window-days 30 || echo "[warn] APPUSDT_SHORT exit $?"
# echo "=== ATOMUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ATOMUSDT_SHORT --window-days 30 || echo "[warn] ATOMUSDT_SHORT exit $?"
# echo "=== BBUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BBUSDT_SHORT --window-days 30 || echo "[warn] BBUSDT_SHORT exit $?"
# echo "=== BMNRUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BMNRUSDT_LONG --window-days 30 || echo "[warn] BMNRUSDT_LONG exit $?"
# echo "=== CHRUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CHRUSDT_SHORT --window-days 30 || echo "[warn] CHRUSDT_SHORT exit $?"
# echo "=== COTIUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side COTIUSDT_SHORT --window-days 30 || echo "[warn] COTIUSDT_SHORT exit $?"
# echo "=== DOGEUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DOGEUSDC_LONG --window-days 30 || echo "[warn] DOGEUSDC_LONG exit $?"
# echo "=== EGLDUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EGLDUSDT_LONG --window-days 30 || echo "[warn] EGLDUSDT_LONG exit $?"
# echo "=== ENJUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ENJUSDT_SHORT --window-days 30 || echo "[warn] ENJUSDT_SHORT exit $?"
# echo "=== ETHFIUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ETHFIUSDC_LONG --window-days 30 || echo "[warn] ETHFIUSDC_LONG exit $?"
# echo "=== FLNCUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side FLNCUSDT_SHORT --window-days 30 || echo "[warn] FLNCUSDT_SHORT exit $?"
# echo "=== HYPEUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side HYPEUSDT_SHORT --window-days 30 || echo "[warn] HYPEUSDT_SHORT exit $?"
# echo "=== IOTXUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side IOTXUSDT_LONG --window-days 30 || echo "[warn] IOTXUSDT_LONG exit $?"
# echo "=== KORUUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side KORUUSDT_SHORT --window-days 30 || echo "[warn] KORUUSDT_SHORT exit $?"
# echo "=== LUNA2USDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side LUNA2USDT_SHORT --window-days 30 || echo "[warn] LUNA2USDT_SHORT exit $?"
# echo "=== MASKUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MASKUSDT_SHORT --window-days 30 || echo "[warn] MASKUSDT_SHORT exit $?"
# echo "=== MOVRUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MOVRUSDT_SHORT --window-days 30 || echo "[warn] MOVRUSDT_SHORT exit $?"
# echo "=== OPUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side OPUSDT_SHORT --window-days 30 || echo "[warn] OPUSDT_SHORT exit $?"
# echo "=== POLUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side POLUSDT_LONG --window-days 30 || echo "[warn] POLUSDT_LONG exit $?"
# echo "=== QTUMUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side QTUMUSDT_LONG --window-days 30 || echo "[warn] QTUMUSDT_LONG exit $?"
# echo "=== RLCUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RLCUSDT_SHORT --window-days 30 || echo "[warn] RLCUSDT_SHORT exit $?"
# echo "=== SANDUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SANDUSDT_SHORT --window-days 30 || echo "[warn] SANDUSDT_SHORT exit $?"
# echo "=== SNXUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SNXUSDT_LONG --window-days 30 || echo "[warn] SNXUSDT_LONG exit $?"
# echo "=== SPXUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SPXUSDT_LONG --window-days 30 || echo "[warn] SPXUSDT_LONG exit $?"
# echo "=== THETAUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side THETAUSDT_SHORT --window-days 30 || echo "[warn] THETAUSDT_SHORT exit $?"
# echo "=== UNIUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side UNIUSDC_LONG --window-days 30 || echo "[warn] UNIUSDC_LONG exit $?"
# echo "=== VIRTUALUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side VIRTUALUSDT_SHORT --window-days 30 || echo "[warn] VIRTUALUSDT_SHORT exit $?"
# echo "=== XLMUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XLMUSDT_LONG --window-days 30 || echo "[warn] XLMUSDT_LONG exit $?"
# echo "=== XRPUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XRPUSDC_LONG --window-days 30 || echo "[warn] XRPUSDC_LONG exit $?"
# echo "=== ZENUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ZENUSDT_LONG --window-days 30 || echo "[warn] ZENUSDT_LONG exit $?"
# echo "=== ZRXUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ZRXUSDT_SHORT --window-days 30 || echo "[warn] ZRXUSDT_SHORT exit $?"