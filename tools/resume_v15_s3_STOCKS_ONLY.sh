#!/bin/bash
set -euo pipefail
# 🔴 ONLY NEW — LONG+SHORT TOGETHER while NPZ in memory (V12_NPZ_CACHE=32)
# V15_FULL_354.txt → 204 stocks, 156 already calculated ERASED, 48 MISSING (40 bases, 8 pairs co-located)
# No repeat ever, no crypto until stocks done
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s3"
mkdir -p data/reports/lifecycle_pilot
# NPZ hot: LONG+SHORT of same base run back-to-back, ALL_PREPARED stays in RAM

echo "=== ADP_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ADP_LONG --window-days 30 || echo "[warn] ADP_LONG exit $?"
echo "=== CAT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CAT_LONG --window-days 30 || echo "[warn] CAT_LONG exit $?"
echo "=== DIS_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side DIS_SHORT --window-days 30 || echo "[warn] DIS_SHORT exit $?"
echo "=== ETH_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ETH_SHORT --window-days 30 || echo "[warn] ETH_SHORT exit $?"
echo "=== LNG_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side LNG_LONG --window-days 30 || echo "[warn] LNG_LONG exit $?"
echo "=== OKE_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side OKE_LONG --window-days 30 || echo "[warn] OKE_LONG exit $?"
echo "=== RGLD_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RGLD_LONG --window-days 30 || echo "[warn] RGLD_LONG exit $?"
echo "=== RS_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RS_LONG --window-days 30 || echo "[warn] RS_LONG exit $?"
echo "=== RS_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RS_SHORT --window-days 30 || echo "[warn] RS_SHORT exit $?"
echo "=== TXN_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TXN_LONG --window-days 30 || echo "[warn] TXN_LONG exit $?"
echo "=== TXN_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TXN_SHORT --window-days 30 || echo "[warn] TXN_SHORT exit $?"
echo "=== XOM_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XOM_LONG --window-days 30 || echo "[warn] XOM_LONG exit $?"
echo "[DONE s3] $(date -u +%FT%TZ) — 12 NEW sym_sides, pairs co-located"