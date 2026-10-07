#!/bin/bash
set -euo pipefail
# 🔴 ONLY NEW — LONG+SHORT TOGETHER while NPZ in memory (V12_NPZ_CACHE=32)
# V15_FULL_354.txt → 204 stocks, 156 already calculated ERASED, 48 MISSING (40 bases, 8 pairs co-located)
# No repeat ever, no crypto until stocks done
ROOT="$(cd $(dirname $0)/.. && pwd)"
cd "$ROOT"
echo "[$(date -u +%FT%TZ)] $0 server s2"
mkdir -p data/reports/lifecycle_pilot
# NPZ hot: LONG+SHORT of same base run back-to-back, ALL_PREPARED stays in RAM

echo "=== ADBE_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ADBE_SHORT --window-days 30 || echo "[warn] ADBE_SHORT exit $?"
echo "=== BG_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side BG_LONG --window-days 30 || echo "[warn] BG_LONG exit $?"
echo "=== CRK_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side CRK_LONG --window-days 30 || echo "[warn] CRK_LONG exit $?"
echo "=== EQT_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EQT_LONG --window-days 30 || echo "[warn] EQT_LONG exit $?"
echo "=== EQT_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side EQT_SHORT --window-days 30 || echo "[warn] EQT_SHORT exit $?"
echo "=== LDOS_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side LDOS_SHORT --window-days 30 || echo "[warn] LDOS_SHORT exit $?"
echo "=== NUKZ_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side NUKZ_LONG --window-days 30 || echo "[warn] NUKZ_LONG exit $?"
echo "=== PR_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PR_LONG --window-days 30 || echo "[warn] PR_LONG exit $?"
echo "=== PR_SHORT ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side PR_SHORT --window-days 30 || echo "[warn] PR_SHORT exit $?"
echo "=== ROKU_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side ROKU_LONG --window-days 30 || echo "[warn] ROKU_LONG exit $?"
echo "=== TECK_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side TECK_LONG --window-days 30 || echo "[warn] TECK_LONG exit $?"
echo "=== XLE_LONG ==="; python3 tools/v15_pilot_sheet_runner.py --sym-side XLE_LONG --window-days 30 || echo "[warn] XLE_LONG exit $?"
echo "[DONE s2] $(date -u +%FT%TZ) — 12 NEW sym_sides, pairs co-located"