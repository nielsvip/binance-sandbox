#!/usr/bin/env bash
# usdc_expansion_revert.sh — restores the 8 files from a backup snapshot created by usdc_expansion_apply.sh.
# Usage:  bash data/usdc_expansion_revert.sh <TS>
# where <TS> is the timestamp printed at the end of apply (e.g. 20260426_141530).
# If <TS> omitted, picks the most recent backups/usdc_expansion_* dir.

set -euo pipefail

ROOT="/Users/niels/Documents/binance"

if [[ $# -ge 1 ]]; then
  TS="$1"
  BACKUP_DIR="${ROOT}/backups/usdc_expansion_${TS}"
else
  BACKUP_DIR="$(ls -1dt ${ROOT}/backups/usdc_expansion_* 2>/dev/null | head -1 || true)"
  TS="(latest)"
fi

if [[ -z "${BACKUP_DIR}" || ! -d "${BACKUP_DIR}" ]]; then
  echo "ERROR: backup dir not found: ${BACKUP_DIR}"
  echo "Available backups:"
  ls -1dt ${ROOT}/backups/usdc_expansion_* 2>/dev/null || echo "  (none)"
  exit 1
fi

echo "=========================================="
echo "USDC EXPANSION REVERT — ${TS}"
echo "Backup dir: ${BACKUP_DIR}"
echo "=========================================="

# Confirm files exist in backup
FILES=(tradeable_keys.json symbols.json
       symbols_fin.json symbols_flz.json symbols_men.json
       symbols_ang_long.json symbols_ang_short.json
       symbols_inf_long.json symbols_inf_short.json)

# Safety: snapshot CURRENT (post-apply) state before overwriting, in case revert is itself a mistake.
SAFETY_DIR="${ROOT}/backups/usdc_expansion_pre_revert_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${SAFETY_DIR}"
echo "Pre-revert safety snapshot: ${SAFETY_DIR}"

for f in "${FILES[@]}"; do
  if [[ -f "${BACKUP_DIR}/${f}" && -f "${ROOT}/${f}" ]]; then
    cp "${ROOT}/${f}" "${SAFETY_DIR}/${f}"
    cp "${BACKUP_DIR}/${f}" "${ROOT}/${f}"
    echo "RESTORED: ${f}"
  else
    echo "SKIP: ${f} (not in backup or live)"
  fi
done

echo
echo "=== VALIDATION ==="
"/opt/anaconda3/envs/binance_env/bin/python" -c "
import json
for f in ['tradeable_keys.json','symbols.json','symbols_fin.json','symbols_flz.json',
          'symbols_men.json','symbols_ang_long.json','symbols_ang_short.json',
          'symbols_inf_long.json','symbols_inf_short.json']:
    with open(f'/Users/niels/Documents/binance/{f}') as h:
        d = json.load(h)
    print(f'OK PARSE: {f} ({len(d)} entries)')
"

echo
echo "=========================================="
echo "REVERT DONE. Pre-revert state in: ${SAFETY_DIR}"
echo "=========================================="
