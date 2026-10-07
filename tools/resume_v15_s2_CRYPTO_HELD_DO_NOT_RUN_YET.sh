#!/bin/bash
set -euo pipefail
# 🔴 HELD — DO NOT RUN until stocks done (204 stocks final_gain)
# Crypto USDC/USDT only on s1 2 3 5 AFTER stocks
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s2"
#
# BLOCKED until stocks done — uncomment after verification
# if [ "$(python3 -c 'import json,glob; print(sum(1 for f in glob.glob("data/reports/lifecycle_pilot/*_v14_progress.json") if json.loads(open(f).read()).get("final_gain") is not None and not f.split("/")[-1].split("_")[0].endswith(("USDT","USDC"))))' )" -lt 204 ]; then echo "BLOCKED stocks not done"; exit 2; fi

# echo "=== 1INCHUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side 1INCHUSDT_LONG --window-days 30 || echo "[warn] 1INCHUSDT_LONG exit $?"
# echo "=== ADAUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ADAUSDC_SHORT --window-days 30 || echo "[warn] ADAUSDC_SHORT exit $?"
# echo "=== ARBUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ARBUSDC_SHORT --window-days 30 || echo "[warn] ARBUSDC_SHORT exit $?"
# echo "=== AVAXUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side AVAXUSDC_SHORT --window-days 30 || echo "[warn] AVAXUSDC_SHORT exit $?"
# echo "=== BCHUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BCHUSDC_SHORT --window-days 30 || echo "[warn] BCHUSDC_SHORT exit $?"
# echo "=== BSVUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BSVUSDT_LONG --window-days 30 || echo "[warn] BSVUSDT_LONG exit $?"
# echo "=== COMPUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side COMPUSDT_LONG --window-days 30 || echo "[warn] COMPUSDT_LONG exit $?"
# echo "=== CRVUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CRVUSDC_LONG --window-days 30 || echo "[warn] CRVUSDC_LONG exit $?"
# echo "=== DOGEUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DOGEUSDC_SHORT --window-days 30 || echo "[warn] DOGEUSDC_SHORT exit $?"
# echo "=== EGLDUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EGLDUSDT_SHORT --window-days 30 || echo "[warn] EGLDUSDT_SHORT exit $?"
# echo "=== ENSUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ENSUSDT_SHORT --window-days 30 || echo "[warn] ENSUSDT_SHORT exit $?"
# echo "=== FARTCOINUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side FARTCOINUSDT_LONG --window-days 30 || echo "[warn] FARTCOINUSDT_LONG exit $?"
# echo "=== GALAUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side GALAUSDT_SHORT --window-days 30 || echo "[warn] GALAUSDT_SHORT exit $?"
# echo "=== ICPUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ICPUSDT_SHORT --window-days 30 || echo "[warn] ICPUSDT_SHORT exit $?"
# echo "=== IOTXUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side IOTXUSDT_SHORT --window-days 30 || echo "[warn] IOTXUSDT_SHORT exit $?"
# echo "=== KSMUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side KSMUSDT_LONG --window-days 30 || echo "[warn] KSMUSDT_LONG exit $?"
# echo "=== MAGICUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MAGICUSDT_SHORT --window-days 30 || echo "[warn] MAGICUSDT_SHORT exit $?"
# echo "=== MEMEUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MEMEUSDT_SHORT --window-days 30 || echo "[warn] MEMEUSDT_SHORT exit $?"
# echo "=== MUUUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MUUUSDT_LONG --window-days 30 || echo "[warn] MUUUSDT_LONG exit $?"
# echo "=== ORDIUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ORDIUSDC_SHORT --window-days 30 || echo "[warn] ORDIUSDC_SHORT exit $?"
# echo "=== PTBUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PTBUSDT_LONG --window-days 30 || echo "[warn] PTBUSDT_LONG exit $?"
# echo "=== QTUMUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side QTUMUSDT_SHORT --window-days 30 || echo "[warn] QTUMUSDT_SHORT exit $?"
# echo "=== RSRUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RSRUSDT_SHORT --window-days 30 || echo "[warn] RSRUSDT_SHORT exit $?"
# echo "=== SKYAIUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SKYAIUSDT_SHORT --window-days 30 || echo "[warn] SKYAIUSDT_SHORT exit $?"
# echo "=== SNXUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SNXUSDT_SHORT --window-days 30 || echo "[warn] SNXUSDT_SHORT exit $?"
# echo "=== STORJUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side STORJUSDT_SHORT --window-days 30 || echo "[warn] STORJUSDT_SHORT exit $?"
# echo "=== TIAUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TIAUSDC_LONG --window-days 30 || echo "[warn] TIAUSDC_LONG exit $?"
# echo "=== UNIUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side UNIUSDC_SHORT --window-days 30 || echo "[warn] UNIUSDC_SHORT exit $?"
# echo "=== WIFUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side WIFUSDC_SHORT --window-days 30 || echo "[warn] WIFUSDC_SHORT exit $?"
# echo "=== XLMUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XLMUSDT_SHORT --window-days 30 || echo "[warn] XLMUSDT_SHORT exit $?"
# echo "=== XRPUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XRPUSDC_SHORT --window-days 30 || echo "[warn] XRPUSDC_SHORT exit $?"
# echo "=== ZENUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ZENUSDT_SHORT --window-days 30 || echo "[warn] ZENUSDT_SHORT exit $?"