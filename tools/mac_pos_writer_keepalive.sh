#!/bin/bash
# mac_pos_writer_keepalive.sh — keep the single position writer and its failover ALIVE AND WORKING.
#
# USER 2026-10-15: "s1 SHOULD HAVE KICKED IN AFTER 15 SECONDS WHAT THE FUCK."
# The old crontab keepalive tested liveness with `pgrep -f "ez_positions_service_main.py $A"` only. A process that is
# alive but WEDGED still matches, so men + inf sat hung at "Lazy loading MultiAccountTradeManager" from 13:58Z to
# 18:2xZ (4.5 h) with 19 positions unmanaged, and nothing ever replaced them. Duplicates also piled up (20+ realtime
# workers) because each crashed worker respawned without the dead ones being reaped.
#
# Liveness here is the HEARTBEAT, not the pid: <acct>/.positions_service_heartbeat.json is written by the service
# after every successful save, so "stale heartbeat" == "not doing its job" whatever the process table says.
#   missing process          -> start
#   heartbeat older than MAX -> kill -9 and start (grace period after a start, so boot is not mistaken for a hang)
#   more than one process    -> keep the newest, kill the rest
# The failover (ez_positions_realtime.py) is kept to exactly one process per account; it decides for itself whether to
# write, via pwg.failover_gate().
set -u
BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
ACCOUNTS="${MAC_POS_ACCOUNTS:-ang inf flz fin men}"
MAX_HB_AGE="${MAC_POS_MAX_HB_AGE:-180}"   # seconds without a successful save before the writer is replaced
BOOT_GRACE="${MAC_POS_BOOT_GRACE:-240}"   # a freshly started writer gets this long to produce its first heartbeat
MAX_MGR_LOG_AGE="${MAC_MGR_MAX_LOG_AGE:-300}"  # ez_manage silent (no log write) this long = replace it.
# 2026-10-16: MEASURED, DO NOT LOWER. Log age is a WEAK liveness signal for ez_manage — a healthy engine with few or
# no positions legitimately writes nothing for minutes. Inter-line gaps in the last 3 MB of ez_manage_{inf,ang}.log:
# 92/45 gaps of 45-150 s, gaps of 239 s and 260 s, and 389/456/481 s. inf was last logging at 04:17:26Z when it was
# killed at 04:22:24Z — a 300 s quiet period on a perfectly healthy engine. Lowering this bar to 150 s would have
# SIGKILLed healthy engines about every 2.5 min. The fast, exact signal is the engine CHILD PROCESS below.
MAX_MGR_NOCHILD="${MAC_MGR_MAX_NOCHILD:-45}"  # watchdog alive but NO engine child this long (and host not tight,
# venue not gated) = the wrapper is sleeping in a backoff nothing else can fill -> replace it. See the block below.
cd "$BASE" || exit 1

# SINGLE FLIGHT (macOS has no flock): two overlapping runs each see "no process", each spawn, and the account ends up
# with two writers — which breaks the single-writer law outright. shlock holds the lock to a pid and clears it if dead.
# 2026-10-16: every line is stamped. This script is the proof that an account cannot be inactive for >3 min, and an
# unstamped log cannot prove anything about timing — the 2026-10-16 04:08Z and 04:27Z incidents had to be reconstructed
# from the watchdog logs because this one carried no times at all.
klog() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [keepalive] $*"; }

# ═══ HARD RUN BOUND (2026-10-16 USER 3-MINUTE INVARIANT) ═══
# Spawning a writer or realtime worker BLOCKS this script — measured: cron pass 63180 sat 73 s inside a spawn
# subshell (04:30:00 -> 04:31:13Z) while holding the single-flight lock below, so every following */1 pass exited with
# "another run holds the lock" (63 of the last 200 log lines). The layer that is supposed to guarantee the 3-minute
# bound had therefore stopped checking, silently. A pass may now never outlive its own schedule: the EXIT/TERM trap
# releases the lock, so the next cron minute always gets a clean run.
RUN_MAX_S="${MAC_POS_RUN_MAX_S:-45}"
if [ -z "${MAC_POS_TIMED:-}" ] && [ -x /opt/homebrew/bin/timeout ]; then
  export MAC_POS_TIMED=1
  exec /opt/homebrew/bin/timeout -s TERM "$RUN_MAX_S" /bin/bash "$0" "$@"
fi

LOCK="/tmp/.mac_pos_writer_keepalive.lock"
# 2026-10-16: shlock only breaks a lock whose holder is DEAD. A holder that is merely SLOW or WEDGED keeps it
# forever, and because this script is the layer that guarantees the 3-minute bound, a stuck holder silently turns the
# guarantee off — measured 2026-10-16: 63 of the last 200 log lines were "another run holds the lock". Break the lock
# by AGE as well (the same pattern crypto_watchdog_cron.sh already uses), and kill the stuck holder so it cannot run
# on in parallel with us. A healthy pass takes well under a second after the python removals above.
LOCK_STALE_S="${MAC_POS_LOCK_STALE_S:-75}"  # > RUN_MAX_S (45 s) + margin: a lock older than this cannot be a live run
if [ -f "$LOCK" ]; then
  _lh=$(cat "$LOCK" 2>/dev/null | tr -d ' \000')
  _lm=$(stat -f %m "$LOCK" 2>/dev/null || echo 0)
  _la=$(( $(date +%s) - _lm ))
  case "$_lh" in ''|*[!0-9]*) _lh="" ;; esac
  if [ "$_la" -gt "$LOCK_STALE_S" ]; then
    klog "STALE LOCK: holder pid=${_lh:-none} held $LOCK for ${_la}s (> ${LOCK_STALE_S}s) — breaking it"
    if [ -n "$_lh" ] && kill -0 "$_lh" 2>/dev/null && ps -o command= -p "$_lh" 2>/dev/null | grep -q mac_pos_writer_keepalive; then
      kill -9 "$_lh" 2>/dev/null
    fi
    rm -f "$LOCK"
  fi
fi
if ! /usr/bin/shlock -f "$LOCK" -p $$; then
  klog "another run holds $LOCK — exiting"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT INT TERM

now=$(date +%s)

# RESPECT THE FENCE (USER 2026-10-15 live takeover test). While S1 holds the crypto lease, S1 runs these accounts and
# the Mac must not: the writer is an order path (its FIXED_STOP leg calls execute_now), so restarting it here would
# re-open the two-hosts-one-position window the fence exists to close — and this keepalive runs every minute, so it
# would win against the fence every time. lease_local.json is written by mac_failover_agent BEFORE it fences.
LEASE="$BASE/data/live_failover/lease_local.json"
if [ -f "$LEASE" ]; then
  holder=$(/usr/bin/python3 -c "
import json,sys
try:
    d=json.load(open('$LEASE'))
    v=str(d.get('venues') or '')
    print(d.get('holder','') if (not v or 'crypto' in v) else '')
except Exception:
    print('')
" 2>/dev/null)
  if [ "$holder" = "s1" ]; then
    klog "crypto lease held by S1 — Mac writers stay DOWN (fenced); not starting anything"
    exit 0
  fi
fi

age_of() {  # heartbeat age in seconds, or 999999 when missing/unreadable
  # 2026-10-16: pure shell, NO python. A cold /usr/bin/python3 costs 0.3-3 s under this host's load and this helper
  # plus reap_extras ran ~15 python interpreters per pass, so ONE pass outlived its own */1 schedule: lock holder
  # 44662 was still inside the account loop after 2 min (04:24:13 -> 04:26:11Z) and every run behind it aborted with
  # "another run holds the lock". The keepalive is the layer that guarantees the 3-minute bound, so it must finish in
  # well under a minute or the bound is fiction.
  local f="$1" ts
  [ -f "$f" ] || { echo 999999; return; }
  ts=$(/usr/bin/sed -n 's/.*"ts"[[:space:]]*:[[:space:]]*"\{0,1\}\([0-9][0-9]*\).*/\1/p' "$f" 2>/dev/null | head -1)
  case "$ts" in ''|*[!0-9]*) echo 999999; return ;; esac
  echo $(( $(date +%s) - ts ))
}

procs_for() { pgrep -f "$1" 2>/dev/null | tr '\n' ' '; }

reap_extras() {  # exactly one process per pattern: keep the OLDEST (it is furthest through bootstrap), kill the rest.
  # A wedged old process is not kept by this — the heartbeat check below replaces it. Keeping the NEWEST instead
  # churns, because the newest is always the one this script just spawned.
  local pattern="$1" pids keep p
  pids=$(procs_for "$pattern")
  [ -z "$pids" ] && return 0
  # 2026-10-16: `pgrep -o` IS "oldest matching process" — it replaces a python that shelled out to `ps` + `date`
  # once PER PID. Same selection rule (keep the oldest), one syscall-cheap call instead of 2N interpreters.
  keep=$(pgrep -o -f "$pattern" 2>/dev/null)
  [ -z "$keep" ] && return 0
  for p in $pids; do
    [ "$p" = "$keep" ] && continue
    klog "reap duplicate $p (keeping $keep) ($pattern)"
    kill -9 "$p" 2>/dev/null
  done
}

# ── ez_manage: THE DECISION ENGINE (USER 2026-10-15 "make sure the entire ez_ system starts running again not
# just the stops"). It had run_with_watchdog.sh and NOTHING ABOVE THAT: when the watchdogs died or went into
# SIGKILL_BACKOFF (60s doubling per consecutive 137) all five accounts sat with no engine at all and nothing
# noticed. Nobody supervised the supervisor. 2026-10-16: lifted into a function so PASS 1 can run it for every
# account before writers/realtime workers can stall the pass.
engine_keepalive() {
  local A="$1" MGR mgr_log mgr_age mgr_mtime mgr_pids mgr_stamp mgr_started mgr_since
  local child_pids nochild_stamp gated first_gone gone plvl p
  MGR="run_with_watchdog.sh ez_manage.py --account $A"
  mgr_log="$HOME/logs/ez_manage_$A.log"
  mgr_age=999999
  if [ -f "$mgr_log" ]; then
    mgr_mtime=$(stat -f %m "$mgr_log" 2>/dev/null || echo 0)
    [ "$mgr_mtime" -gt 0 ] && mgr_age=$(( now - mgr_mtime ))
  fi
  mgr_pids=$(procs_for "$MGR")
  mgr_stamp="/tmp/.mac_mgr_started_$A"
  mgr_started=0
  [ -f "$mgr_stamp" ] && mgr_started=$(cat "$mgr_stamp" 2>/dev/null || echo 0)
  if [ "$mgr_started" -gt 0 ]; then mgr_since=$(( now - mgr_started )); else mgr_since=999999; fi

  # ── 2026-10-16 USER 3-MINUTE INVARIANT: a watchdog can be ALIVE and DELIBERATELY IDLE while its engine child is
  # dead — SIGKILL_BACKOFF sleep (60-660 s), PRESSURE_GATE (300 s), RESTART_BACKOFF (up to 300 s). The log-age test
  # below cannot see that until MAX_MGR_LOG_AGE, and nothing else can: the sleeping watchdog still holds
  # .watchdog_ez_manage_<acct>.pid, so every replacement exits with [WATCHDOG_SINGLETON] and crypto_watchdog_cron.sh
  # counts the account healthy because the wrapper exists. PROVEN 2026-10-16 04:08:11-04:09:19Z (ang/inf/flz/fin).
  # A missing child while the host is NOT under memory pressure is never legitimate for more than one relaunch, so it
  # is timed on its own and acted on FIRST. Under real pressure (level >= 4) the wait IS correct and this stays out of
  # the way, and while the venue is gated (LEASE_FENCED / FAILOVER_IDLE) a childless watchdog is the intended state.
  child_pids=$(pgrep -f "bin/python -u ez_manage.py --account $A" 2>/dev/null | tr '\n' ' ')
  nochild_stamp="/tmp/.mac_mgr_nochild_$A"
  if [ -n "$mgr_pids" ] && [ -z "$child_pids" ]; then
    gated=0
    tail -4 "$HOME/logs/ez_manage_${A}_watchdog.log" 2>/dev/null | grep -qE "LEASE_FENCED|FAILOVER_IDLE" && gated=1
    first_gone=0
    [ -f "$nochild_stamp" ] && first_gone=$(cat "$nochild_stamp" 2>/dev/null || echo 0)
    if [ "$first_gone" -le 0 ]; then first_gone=$now; echo "$now" > "$nochild_stamp"; fi
    gone=$(( now - first_gone ))
    plvl=$(/usr/sbin/sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)
    case "$plvl" in ''|*[!0-9]*) plvl=1 ;; esac
    if [ "$gated" = 0 ] && [ "$gone" -ge "$MAX_MGR_NOCHILD" ] && [ "$plvl" -le 2 ] && [ "$mgr_since" -gt 90 ]; then
      klog "$A: ez_manage watchdog ALIVE BUT NO ENGINE CHILD for ${gone}s (pressure_level=$plvl, not gated) -> replace watchdog $mgr_pids"
      for p in $mgr_pids; do kill -9 "$p" 2>/dev/null; done
      mgr_pids=""
      rm -f "$nochild_stamp"
    fi
  else
    rm -f "$nochild_stamp" 2>/dev/null
  fi

  # 2026-10-22 fin silence limit: fin's engine is legitimately quiet 210-343 s every PERF_REPORT cycle and during
  # STEP 2+3 Redis flushes, so the 300 s limit killed it on every cycle. fin alone gets 900 s; a true wedge is still
  # caught by run_with_watchdog.sh (600 s no-output with a CPU-progress gate).
  local max_age="$MAX_MGR_LOG_AGE"
  [ "$A" = "fin" ] && max_age="${MAC_MGR_MAX_LOG_AGE_FIN:-900}"
  if [ -z "$mgr_pids" ]; then
    klog "$A: NO ez_manage watchdog -> start (decision engine was down)"
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$BOOT_GRACE" ]; then
    klog "$A: ez_manage SILENT (log ${mgr_age}s > ${max_age}s) -> kill watchdog $mgr_pids + child and restart"
    for p in $mgr_pids; do kill -9 "$p" 2>/dev/null; done
    for p in $(pgrep -f "bin/python -u ez_manage.py --account $A"); do kill -9 "$p" 2>/dev/null; done
    mgr_pids=""
  fi
  if [ -z "$mgr_pids" ]; then
    # 2026-10-16: re-check immediately before spawning. `procs_for` was observed to false-negative transiently under
    # load (04:38:00Z and 04:40:14Z logged "NO ez_manage watchdog -> start" for inf while wrapper 77006 was alive and
    # answered pgrep a moment later), and two concurrent passes do the same thing. The redundant wrapper was always
    # refused by run_with_watchdog.sh's [WATCHDOG_SINGLETON] so no account ever ran two engines, but it is pointless
    # churn. Re-checking shrinks the window to milliseconds; the spawn stays unconditional when nothing is there, so
    # this can never turn into a refusal to start.
    if [ -n "$(procs_for "$MGR")" ]; then
      klog "$A: ez_manage watchdog reappeared before spawn (transient pgrep miss) — not starting a duplicate"
      return 0
    fi
    echo "$now" > "$mgr_stamp"
    ( cd "$BASE" && nohup /bin/bash run_with_watchdog.sh ez_manage.py --account "$A" < /dev/null >> "$HOME/logs/ez_manage_${A}_cron.log" 2>&1 & )
  fi
}

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# PASS 1 — THE DECISION ENGINE, FIRST AND ALONE.
# 2026-10-16 USER ("NO account can be inactive for >3min"): engine supervision used to live at the BOTTOM of a loop
# that first handled writers and realtime workers. Starting any of those blocks this script (measured: cron run 63180
# sat 73 s inside a spawn subshell at 04:30:00-04:31:13Z while holding the single-flight lock), so on a bad pass the
# engine check was reached late or not at all, and every run behind it aborted with "another run holds the lock".
# The 3-minute invariant is about ENGINES, so engines are now checked before anything else can stall the pass.
for A in $ACCOUNTS; do
  engine_keepalive "$A"
done

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# PASS 2 — position writers and realtime failover workers (unchanged logic, now strictly after the engines).
for A in $ACCOUNTS; do
  SVC="ez_positions_service_main.py $A"
  RT="ez_positions_realtime.py $A"
  reap_extras "$SVC"
  reap_extras "$RT"
  pids=$(procs_for "$SVC")
  hb_age=$(age_of "$BASE/$A/.positions_service_heartbeat.json")
  stamp_file="/tmp/.mac_pos_started_$A"
  started=0
  [ -f "$stamp_file" ] && started=$(cat "$stamp_file" 2>/dev/null || echo 0)
  if [ "$started" -gt 0 ]; then since_start=$(( now - started )); else since_start=999999; fi  # no stamp = not started by us = no boot grace
  if [ -z "$pids" ]; then
    klog "$A: no writer -> start"
  elif [ "$hb_age" -gt "$MAX_HB_AGE" ] && [ "$since_start" -gt "$BOOT_GRACE" ]; then
    klog "$A: writer WEDGED (heartbeat ${hb_age}s > ${MAX_HB_AGE}s, up ${since_start}s) -> kill $pids and restart"
    for p in $pids; do kill -9 "$p" 2>/dev/null; done
    pids=""
  fi
  if [ -z "$pids" ]; then
    echo "$now" > "$stamp_file"
    ( cd "$BASE" && env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin nohup "$PY" -u ez_positions_service_main.py "$A" < /dev/null >> "/tmp/mac_svc_$A.log" 2>&1 & )
  fi
  if [ -z "$(procs_for "$RT")" ]; then
    klog "$A: no failover worker -> start"
    ( cd "$BASE" && env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin nohup "$PY" -u ez_positions_realtime.py "$A" < /dev/null >> "/tmp/mac_rt_$A.log" 2>&1 & )
  fi

done
