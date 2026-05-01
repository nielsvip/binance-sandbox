#!/bin/bash
# watchdog_sweep_s1.sh — every 5min check that S1 crypto sweep is alive; relaunch via canonical launcher if dead.
# Replaces _NOLIES_HOLD_20260430/disarmed_launchers/watchdog_v8_quick_sweep.sh which referenced the
# pre-cockroach-fix tier mega_crypto_v8_a1234 — this version uses the start_crypto_sweeps.sh launcher
# (which routes through the cockroach-smothered metrics_guard chokepoint).
LOG=/home/niels/logs/watchdog_sweep_s1.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
# pgrep -afc returns "0" + exit-code-1 when nothing matches; the old `|| echo 0` produced "0\n0"
# which made `[ -lt 1 ]` fail with "integer expression expected", silently breaking the watchdog.
N=$(pgrep -afc "v8_quick_sweep.*--mode crypto" 2>/dev/null | head -1)
N=${N:-0}
if [ "$N" -lt 1 ]; then
    echo "[$TS] $N v8_quick_sweep crypto procs — relaunching via start_crypto_sweeps.sh wt_dc_full" >> "$LOG"
    bash /home/niels/binance-sandbox/start_crypto_sweeps.sh wt_dc_full >> "$LOG" 2>&1
    sleep 5
    echo "[$TS] post-relaunch procs=$(pgrep -afc 'v8_quick_sweep.*--mode crypto')" >> "$LOG"
fi
