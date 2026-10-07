#!/bin/bash
set -euo pipefail
# 🔴 ONLY NEW — LONG+SHORT TOGETHER while NPZ in memory (V12_NPZ_CACHE=32)
# V15_FULL_354.txt → 204 stocks, 156 already calculated ERASED, 48 MISSING (40 bases, 8 pairs co-located)
# No repeat ever, no crypto until stocks done
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s5"
mkdir -p data/reports/lifecycle_pilot
# NPZ hot: LONG+SHORT of same base run back-to-back, ALL_PREPARED stays in RAM

echo "=== APA_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side APA_LONG --window-days 30 || echo "[warn] APA_LONG exit $?"
echo "=== CIBR_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CIBR_LONG --window-days 30 || echo "[warn] CIBR_LONG exit $?"
echo "=== EOG_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EOG_LONG --window-days 30 || echo "[warn] EOG_LONG exit $?"
echo "=== FANG_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side FANG_LONG --window-days 30 || echo "[warn] FANG_LONG exit $?"
echo "=== MA_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side MA_LONG --window-days 30 || echo "[warn] MA_LONG exit $?"
echo "=== OXY_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side OXY_LONG --window-days 30 || echo "[warn] OXY_LONG exit $?"
echo "=== RIO_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side RIO_LONG --window-days 30 || echo "[warn] RIO_LONG exit $?"
echo "=== SAP_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side SAP_SHORT --window-days 30 || echo "[warn] SAP_SHORT exit $?"
echo "=== UEC_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side UEC_LONG --window-days 30 || echo "[warn] UEC_LONG exit $?"
echo "=== UEC_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side UEC_SHORT --window-days 30 || echo "[warn] UEC_SHORT exit $?"
echo "=== XOP_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XOP_LONG --window-days 30 || echo "[warn] XOP_LONG exit $?"
echo "[DONE s5] $(date -u +%FT%TZ) — 11 NEW sym_sides, pairs co-located"