#!/bin/bash
# LIVE FAILOVER S1 stocks producers (ExecStart of live-failover-stocks-producers.service, on demand,
# started by tools/live_failover_s1.py when the Mac's Tradier klines/indicators are >180 s stale in
# the NYSE session). Live-mirror copies in ~/binance (NOT the disabled sandbox service), conda env,
# S1 egress IP. tradier_klines.py -> ~/binance/klines_cache/tradier, tradier_indicators.py ->
# ~/binance/data/tradier/tradier_indicators_latest.json. Both die with the unit (cgroup).
set -u
cd /home/niels/binance || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
export EZ_LOG_DIR=/home/niels/binance/logs

# 2026-10-16: `systemctl stop` always timed out and left the unit `failed (Result: timeout)`, because these
# respawn loops have no signal handling: TERM killed the python, the loop immediately started another, and
# systemd gave up after TimeoutStopSec. A unit stuck in `failed` reads as "the standby cannot produce stock
# data" to every status check. Stop now means stop: the trap clears the run flag, kills the children, and both
# loops exit on the next turn.
# The trap is AUTHORITATIVE and exits the script itself. Relying on the loops to notice a flag does not work:
# tradier_indicators.py does not die promptly on SIGTERM, so `wait` never returns and the loop never gets to look.
RUN=1
_shutdown() {
    RUN=0
    echo "[$(date -u +%FT%TZ)] shutdown signal — stopping stock producers" >> /home/niels/logs/lf_tradier_indicators.log
    for p in "${SUB_PID:-}" "${KLINES_PID:-}" "${IND_PID:-}"; do [ -n "$p" ] && kill -TERM "$p" 2>/dev/null; done
    sleep 5
    for p in "${SUB_PID:-}" "${KLINES_PID:-}" "${IND_PID:-}"; do [ -n "$p" ] && kill -KILL "$p" 2>/dev/null; done
    exit 0
}
trap _shutdown TERM INT

( while [ "$RUN" = "1" ]; do "$PY" -u tradier_klines.py >> /home/niels/logs/lf_tradier_klines.log 2>&1 & KLINES_PID=$!; wait "$KLINES_PID"; [ "$RUN" = "1" ] || break; sleep 30; done ) &
SUB_PID=$!
while [ "$RUN" = "1" ]; do
    "$PY" -u tradier_indicators.py >> /home/niels/logs/lf_tradier_indicators.log 2>&1 &
    IND_PID=$!
    wait "$IND_PID"
    echo "[$(date -u +%FT%TZ)] tradier_indicators exited rc=$? — restart in 10s" >> /home/niels/logs/lf_tradier_indicators.log
    [ "$RUN" = "1" ] || break
    sleep 10
done
kill -TERM "$SUB_PID" 2>/dev/null
exit 0
