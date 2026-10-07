#!/bin/bash
set -euo pipefail
# 🔴 HELD — DO NOT RUN until stocks done (204 stocks final_gain)
# Crypto USDC/USDT only on s1 2 3 5 AFTER stocks
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s3"
#
# BLOCKED until stocks done — uncomment after verification
# if [ "$(python3 -c 'import json,glob; print(sum(1 for f in glob.glob("data/reports/lifecycle_pilot/*_v14_progress.json") if json.loads(open(f).read()).get("final_gain") is not None and not f.split("/")[-1].split("_")[0].endswith(("USDT","USDC"))))' )" -lt 204 ]; then echo "BLOCKED stocks not done"; exit 2; fi

# echo "=== 1INCHUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side 1INCHUSDT_SHORT --window-days 30 || echo "[warn] 1INCHUSDT_SHORT exit $?"
# echo "=== ALGOUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ALGOUSDT_LONG --window-days 30 || echo "[warn] ALGOUSDT_LONG exit $?"
# echo "=== ARMUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ARMUSDT_SHORT --window-days 30 || echo "[warn] ARMUSDT_SHORT exit $?"
# echo "=== AXSUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side AXSUSDT_LONG --window-days 30 || echo "[warn] AXSUSDT_LONG exit $?"
# echo "=== BEATUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BEATUSDT_SHORT --window-days 30 || echo "[warn] BEATUSDT_SHORT exit $?"
# echo "=== BTCUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BTCUSDC_SHORT --window-days 30 || echo "[warn] BTCUSDC_SHORT exit $?"
# echo "=== COMPUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side COMPUSDT_SHORT --window-days 30 || echo "[warn] COMPUSDT_SHORT exit $?"
# echo "=== CRVUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CRVUSDC_SHORT --window-days 30 || echo "[warn] CRVUSDC_SHORT exit $?"
# echo "=== DOTUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DOTUSDT_LONG --window-days 30 || echo "[warn] DOTUSDT_LONG exit $?"
# echo "=== ENAUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ENAUSDC_SHORT --window-days 30 || echo "[warn] ENAUSDC_SHORT exit $?"
# echo "=== ETCUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ETCUSDT_LONG --window-days 30 || echo "[warn] ETCUSDT_LONG exit $?"
# echo "=== FARTCOINUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side FARTCOINUSDT_SHORT --window-days 30 || echo "[warn] FARTCOINUSDT_SHORT exit $?"
# echo "=== GOOGLUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side GOOGLUSDT_SHORT --window-days 30 || echo "[warn] GOOGLUSDT_SHORT exit $?"
# echo "=== ICXUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ICXUSDT_SHORT --window-days 30 || echo "[warn] ICXUSDT_SHORT exit $?"
# echo "=== IPUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side IPUSDC_SHORT --window-days 30 || echo "[warn] IPUSDC_SHORT exit $?"
# echo "=== KSMUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side KSMUSDT_SHORT --window-days 30 || echo "[warn] KSMUSDT_SHORT exit $?"
# echo "=== MANAUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MANAUSDT_LONG --window-days 30 || echo "[warn] MANAUSDT_LONG exit $?"
# echo "=== MINAUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MINAUSDT_LONG --window-days 30 || echo "[warn] MINAUSDT_LONG exit $?"
# echo "=== NEARUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side NEARUSDC_LONG --window-days 30 || echo "[warn] NEARUSDC_LONG exit $?"
# echo "=== PARTIUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PARTIUSDT_SHORT --window-days 30 || echo "[warn] PARTIUSDT_SHORT exit $?"
# echo "=== PTBUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PTBUSDT_SHORT --window-days 30 || echo "[warn] PTBUSDT_SHORT exit $?"
# echo "=== RENDERUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RENDERUSDT_SHORT --window-days 30 || echo "[warn] RENDERUSDT_SHORT exit $?"
# echo "=== RUNEUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RUNEUSDT_SHORT --window-days 30 || echo "[warn] RUNEUSDT_SHORT exit $?"
# echo "=== SKYUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SKYUSDT_LONG --window-days 30 || echo "[warn] SKYUSDT_LONG exit $?"
# echo "=== SOLUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SOLUSDC_SHORT --window-days 30 || echo "[warn] SOLUSDC_SHORT exit $?"
# echo "=== SUIUSDC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SUIUSDC_SHORT --window-days 30 || echo "[warn] SUIUSDC_SHORT exit $?"
# echo "=== TRBUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TRBUSDT_LONG --window-days 30 || echo "[warn] TRBUSDT_LONG exit $?"
# echo "=== USUALUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side USUALUSDT_LONG --window-days 30 || echo "[warn] USUALUSDT_LONG exit $?"
# echo "=== WLDUSDC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side WLDUSDC_LONG --window-days 30 || echo "[warn] WLDUSDC_LONG exit $?"
# echo "=== XMRUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XMRUSDT_LONG --window-days 30 || echo "[warn] XMRUSDT_LONG exit $?"
# echo "=== XTZUSDT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XTZUSDT_LONG --window-days 30 || echo "[warn] XTZUSDT_LONG exit $?"
# echo "=== ZILUSDT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ZILUSDT_SHORT --window-days 30 || echo "[warn] ZILUSDT_SHORT exit $?"