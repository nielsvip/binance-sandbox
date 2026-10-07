#!/bin/bash
# USER 2026-09-29: kill ALL old sweep runs + their spawners (no agents running). Keep ONLY the stock
# 365D kline fetch (download_stock_klines*). Disable relaunch crons. Leave the fleet idle+clean so the
# controlled disabled-first defaults-reset 30D driver can own it. Reversible: crontab backed up.
cd ~/binance-sandbox 2>/dev/null || exit 3
TS=$(date +%Y%m%d%H%M)
echo "=== backup + disable relaunch crons ==="
crontab -l > ~/crontab_backup_reset_${TS}.txt 2>/dev/null && echo "cron backup ~/crontab_backup_reset_${TS}.txt"
crontab -l 2>/dev/null | sed -E '/mega_supervisor|herd_watchdog|parity_saturator/ s/^([^#])/#\1/' | crontab -
echo "active relaunch crons after (should be none):"; crontab -l 2>/dev/null | grep -vE '^#' | grep -iE 'mega_supervisor|herd_watchdog|parity_saturator'
echo "=== kill spawners -> orchestrators -> pilots (2 passes); NEVER touch download_stock_klines ==="
KILL="cycle_follower my_runs.sh recalc_audit mega_supervisor v15_mega_sweep v15_mega_pilot v15_local_herd v15_finisher iso_dispatch run_one v15_365_cycle v15_365_repair v15_pilot.py parity_check parity_saturator undo_npz"
for pass in 1 2 3; do
  for pat in $KILL; do pkill -9 -f "$pat" 2>/dev/null; done
  rm -f /tmp/v15_local_herd.lock
  sleep 2
done
echo "=== VERIFY ==="
echo "REMAINING old-run procs (want NONE):"
pgrep -af "iso_dispatch|cycle_follower|v15_local_herd|v15_pilot|v15_mega|run_one|365_cycle|365_repair|v15_finisher|parity_check|mega_supervisor" | grep -v pgrep | grep -v download_stock_klines | cut -c1-90
echo "KLINE FETCH still alive (want it to stay):"
pgrep -af "download_stock_klines|massive" | grep -v pgrep | cut -c1-90
echo "reset_done TS=$TS"
