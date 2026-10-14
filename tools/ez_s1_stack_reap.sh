#!/bin/bash
# ez_s1_stack_reap.sh — USER 2026-10-14: "indicators need to be calculated on s1 as soon as mac shows a delay — other than that we
# need all compute on s1 for backtesting while macbook is in charge of live trading only".
# The EZ_S1_STACK cron (always-on) was commented out (#EZ_S1_STACK_ONDEMAND_20261014_USER#); the stack now runs ONLY inside
# live-failover-crypto-producers.service, which tools/live_failover_s1.py starts when the Mac's crypto data is >180 s stale and
# stops on NORMAL (systemd then kills its cgroup). This guard (S1 cron */2) removes stack processes living OUTSIDE that unit
# (left over from the old cron) — only when crypto is NORMAL and the unit is inactive. Never touches the unit's own processes,
# never runs while failover is ACTIVE. Kill switch: touch ~/binance/ez_s1_stack/KEEP_ALWAYS_ON.
set -u
STACK=/home/niels/binance/ez_s1_stack
STATE=/home/niels/binance/data/live_failover/state.json
UNIT=live-failover-crypto-producers.service
LOG=/home/niels/logs/ez_s1_stack_reap.log
ts(){ date -u +%FT%TZ; }
[ -f "$STACK/KEEP_ALWAYS_ON" ] && exit 0
systemctl is-active --quiet "$UNIT" && exit 0
mode=$(python3 -c "import json;print((json.load(open('$STATE')).get('venues') or {}).get('crypto',{}).get('mode',''))" 2>/dev/null)
[ "$mode" = "NORMAL" ] || exit 0
victims=""
for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    [ "$pid" = "$$" ] && continue
    cwd=$(readlink "$p/cwd" 2>/dev/null) || continue
    [ "$cwd" = "$STACK" ] || continue
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
    case "$cmd" in
        *run_with_watchdog.sh*|*ez_klines.py*|*ez_prices.py*|*ez_mark_prices.py*|*ez_prices_ws.py*|*ez_indicators.py*|*ez_indicators_merger.py*) victims="$victims $pid";;
    esac
done
[ -n "$victims" ] || exit 0
echo "[$(ts)] crypto NORMAL + $UNIT inactive -> stopping out-of-unit ez_s1_stack pids:$victims" >> "$LOG"
kill -TERM $victims 2>/dev/null
sleep 10
for pid in $victims; do [ -d "/proc/$pid" ] && kill -KILL "$pid" 2>/dev/null; done
exit 0
