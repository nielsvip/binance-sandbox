#!/bin/bash
set -euo pipefail
# 🔴 ONLY NEW — LONG+SHORT TOGETHER while NPZ in memory (V12_NPZ_CACHE=32)
# V15_FULL_354.txt → 204 stocks, 156 already calculated ERASED, 48 MISSING (40 bases, 8 pairs co-located)
# No repeat ever, no crypto until stocks done
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s1"
mkdir -p data/reports/lifecycle_pilot
# NPZ hot: LONG+SHORT of same base run back-to-back, ALL_PREPARED stays in RAM

echo "=== A_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side A_LONG --window-days 30 || echo "[warn] A_LONG exit $?"
echo "=== A_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side A_SHORT --window-days 30 || echo "[warn] A_SHORT exit $?"
echo "=== APO_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side APO_SHORT --window-days 30 || echo "[warn] APO_SHORT exit $?"
echo "=== CLF_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CLF_LONG --window-days 30 || echo "[warn] CLF_LONG exit $?"
echo "=== CLF_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CLF_SHORT --window-days 30 || echo "[warn] CLF_SHORT exit $?"
echo "=== EPD_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EPD_LONG --window-days 30 || echo "[warn] EPD_LONG exit $?"
echo "=== GM_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side GM_LONG --window-days 30 || echo "[warn] GM_LONG exit $?"
echo "=== NLR_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side NLR_SHORT --window-days 30 || echo "[warn] NLR_SHORT exit $?"
echo "=== PAAS_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PAAS_LONG --window-days 30 || echo "[warn] PAAS_LONG exit $?"
echo "=== ROBO_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ROBO_LONG --window-days 30 || echo "[warn] ROBO_LONG exit $?"
echo "=== SCCO_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SCCO_LONG --window-days 30 || echo "[warn] SCCO_LONG exit $?"
echo "=== SCCO_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SCCO_SHORT --window-days 30 || echo "[warn] SCCO_SHORT exit $?"
echo "=== VALE_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side VALE_LONG --window-days 30 || echo "[warn] VALE_LONG exit $?"
echo "[DONE s1] $(date -u +%FT%TZ) — 13 NEW sym_sides, pairs co-located"