#!/bin/bash
# runs ON S1. Keep exactly one live_failover_s1 agent alive. Nothing supervised it before: killing it left the
# standby blind with no alarm (USER 2026-10-15 "ARM s1 SO IT CAN ALWAYS ENTER INTO ACTION AT ANY INSTANT").
#
# 2026-10-17 (USER "s1 takes over ... after 3min ... and stop immediately when macbook comes back"): the takeover and
# failback thresholds live in a systemd drop-in on live-failover-watchdog.service
# (/etc/systemd/system/live-failover-watchdog.service.d/takeover-timings.conf). This script used to respawn the agent
# with `setsid` and NO environment, so a keepalive-started agent silently ran the SOURCE defaults — failback 600 s
# instead of 60 s and traders_dead 600 s instead of 120 s — i.e. exactly the behaviour the operator asked to change,
# restored by the thing meant to keep it alive. Now: prefer `systemctl start`, which carries the drop-in; only if the
# unit is unavailable fall back to setsid, and then export the same values explicitly so both paths behave alike.
# The fallback values are duplicated deliberately — a divergence here is invisible until a failover goes wrong.
set -u
BASE=/home/niels/binance
PY=/home/niels/.conda/envs/binance_env/bin/python
LOG=/home/niels/logs/live_failover_s1.log
UNIT=live-failover-watchdog.service
# 2026-10-17 PRE-EXISTING BUG, caught live: the pattern was `live_failover_s1.py --loop`, which `pgrep -f` matches
# against the WHOLE command line of every process — so any shell, ssh command, grep or monitoring one-liner that
# merely MENTIONS the agent matched, and the branch below kill -9'd it. It killed an operator SSH session during
# this very deployment. Anchor to a real python interpreter running the script, and exclude this script's own pid
# and its parent, so the keepalive can only ever reap an actual second agent.
PAT='[p]ython[^ ]* -u [^ ]*live_failover_s1\.py --loop'
pids=$(pgrep -f "$PAT" | grep -vE "^($$|$PPID)$" | tr '\n' ' ')
n=$(echo $pids | wc -w)
if [ "$n" -gt 1 ]; then
  # keep the unit's own MainPID when there is one — reaping systemd's copy just makes it restart and fight us
  keep=$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null)
  case " $pids " in *" $keep "*) : ;; *) keep=$(echo $pids | awk '{print $1}') ;; esac
  for p in $pids; do [ "$p" = "$keep" ] || { echo "[s1-ka] reap duplicate failover agent $p (keeping $keep)"; kill -9 "$p"; }; done
elif [ "$n" -eq 0 ]; then
  echo "[s1-ka] failover agent DOWN -> starting"
  if systemctl list-unit-files "$UNIT" >/dev/null 2>&1 && sudo -n systemctl start "$UNIT" 2>/dev/null; then
    echo "[s1-ka] started via $UNIT (carries the takeover-timings drop-in)"
  else
    echo "[s1-ka] $UNIT unavailable -> setsid fallback WITH the same timings"
    export LF_NT_ORDER_S=120 LF_TRADING_DEAD_S=120 LF_FAILBACK_S=60 LF_NT_BACKOFF_MAX=1
    cd "$BASE" && setsid -f "$PY" -u tools/live_failover_s1.py --loop </dev/null >> "$LOG" 2>&1
  fi
fi
