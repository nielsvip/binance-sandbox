#!/bin/bash
# run_persym_promote_gated.sh — floor-gated pending->live per_sym promotion.
#
# 2026-06-04 P2 cadence: make 7D recency reach live more often than weekly,
# WITHOUT ever letting a sub-floor (<30 trades / <1yr) per-sym key go live.
#
# 2026-06-04: promote_pending_per_sym.py is now UNLOCKED and carries the gate
# itself (sample-floor + USER wsharpe>0 + decent-gain), so this wrapper just
# orchestrates:
#   1) persym_floor_gate.py --prune-pending      (strip sub-floor from staging)
#   2) promote_pending_per_sym.py --promote-eligible --yes
#      (auto-selects + gates: sample-floor + wsharpe>0 + decent-gain, new-or-improver)
#   3) persym_floor_gate.py --clean-live          (belt-and-suspenders, Mac only)
#
# Runs ON S1 (canonical home of pending + live; the Mac */10 rsync carries the
# cleaned live config back to the Mac live procs). Idempotent; no churn when
# there are no floor-clean improvers.
#
# Cron (S1): every 6h
#   0 */6 * * * /bin/bash /home/niels/binance-sandbox/run_persym_promote_gated.sh >> /home/niels/logs/persym_promote_gated.log 2>&1

set -u
if [ -d /home/niels/binance-sandbox ]; then
    ROOT=/home/niels/binance-sandbox
    PY=/home/niels/.conda/envs/binance_env/bin/python
else
    ROOT=/Users/niels/Documents/binance
    PY=/opt/anaconda3/envs/binance_env/bin/python
fi
cd "$ROOT" || exit 1
TS=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
echo "[$TS] persym promote (gated) start  ROOT=$ROOT"

# 1) prune sub-floor from staging (belt-and-suspenders; the gate also blocks them)
"$PY" persym_floor_gate.py --prune-pending

# 2) promote every pending key passing the gate (sample-floor + wsharpe>0 +
#    decent gain) that is new or improves on live. Selection + gating now live
#    inside the UNLOCKED promote_pending_per_sym.py --promote-eligible.
"$PY" promote_pending_per_sym.py --promote-eligible --yes

# 4) belt-and-suspenders re-clean live — ONLY where /history/ is authoritative.
#    The Mac is the live-trading box with the real trade ledger; S1's history dir
#    is a stale 1-file sandbox snapshot, so running --clean-live on S1 would
#    misclassify OPEN positions as closed and wrongly remove their (tag-only)
#    keys. The promote above only ADDS pre-filtered floor-clean improvers (never
#    sub-floor), and step (1) already guarantees pending is floor-clean, so
#    skipping clean-live on S1 is safe. On the Mac, clean-live runs to catch any
#    sub-floor active key. The Mac live config is the autosync target either way.
if [ "$ROOT" = "/Users/niels/Documents/binance" ]; then
    "$PY" persym_floor_gate.py --clean-live
else
    echo "[$TS] skip --clean-live on S1 (stale /history/); promote added only floor-clean improvers"
fi

echo "[$TS] persym promote (gated) done"
