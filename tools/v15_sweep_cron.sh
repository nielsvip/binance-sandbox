#!/bin/bash
# USER 2026-09-29: self-sustaining, reboot-proof main 30D full-universe sweep. Idempotent — safe to run
# from cron (*/5) and @reboot. Per host: (1) hold the herd singleton lock so any respawned v15_local_herd
# self-exits (stops the old self-resurrecting rig), (2) ensure the full_sweep_driver is running with the
# right per-venue shard. Keeps the box >85% CPU on the 30D backtests (disabled sym_sides first,
# LONG+SHORT paired, neg=defaults-reset). Never touches download_stock_klines (365D fetch keeps running).
cd ~/binance-sandbox 2>/dev/null || exit 3
# serialize the whole check-and-launch so a manual run and the */5 cron can never both launch a driver
exec 8>/tmp/v15_sweep_cron.wrapper.lock
flock -n 8 || exit 0
HOST=$(hostname)
REC=data/reports/per_sym_recheck_20260929
mkdir -p logs

# 1. hold the herd singleton lock (suppress any respawned herd) — one long-lived flock holder
if ! pgrep -f "flock_hold_herd" >/dev/null 2>&1; then
  setsid nohup bash -c 'exec 9>/tmp/v15_local_herd.lock; flock -x 9 && sleep 2592000  # flock_hold_herd' >/dev/null 2>&1 < /dev/null 8>&- &
fi

# 2. per-host driver args
case "$HOST" in
  *niels*) ORDER=$REC/run_order_crypto.txt; DIS=$REC/priority_negzero_crypto.txt; SHARD=0/1; MP=3; W=3;;  # USER 2026-09-30: s1 = ALL crypto, F+yellow
  *s5*)    ORDER=$REC/run_order_allcells_alt.txt; DIS=$REC/priority_negzero_crypto.txt; SHARD=0/1; MP=3; W=3; EXTRA=--all-cols;;  # USER 2026-09-30: s5 = every-cell sheets, crypto/stock alternating
  *s2*)    ORDER=$REC/run_order_stocks.txt; DIS=$REC/priority_negzero_stocks.txt; SHARD=0/1; MP=6; W=3;;
  *)       echo "unknown host $HOST"; exit 4;;
esac

# 2b. current pass progress dir (controller flips this run1->run2); default run1
PDIR=$(cat ~/v15_current_progress_dir.txt 2>/dev/null || echo "$HOME/v15_run1_20260929/progress")
I=${SHARD%/*}; N=${SHARD#*/}
MARKER="$(dirname "$PDIR")/PASS_COMPLETE_shard${I}of${N}"

# 3. ensure the full-sweep driver is running (unless this pass's shard is already complete — then wait for
#    the pipeline controller to advance; do NOT tight-loop-relaunch a finished pass)
if [ -f "$MARKER" ]; then
  exit 0
fi
if ! pgrep -f "v15_full_sweep_driver.py" >/dev/null 2>&1; then
  TS=$(date +%Y%m%d_%H%M)
  setsid nohup .venv/bin/python -u tools/v15_full_sweep_driver.py \
    --order "$ORDER" --disabled-set "$DIS" --shard "$SHARD" --max-parallel "$MP" --workers "$W" --progress-dir "$PDIR" $EXTRA \
    >> logs/full_sweep_${TS}.log 2>&1 < /dev/null 8>&- &
  echo "$(date -u +%FT%TZ) launched full_sweep_driver on $HOST shard $SHARD" >> logs/v15_sweep_cron.log
fi
