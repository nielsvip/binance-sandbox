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
HB_STUCK_PASSES="${MAC_POS_HB_STUCK_PASSES:-10}"   # consecutive 1-min passes with ZERO heartbeat progress before a kill
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

# USER 2026-10-22: a restart this script declines to make must be ALERTED IMMEDIATELY, not silently skipped — an
# engine that is genuinely wedged past its 24 h budget needs a human or a code fix, and silence is how men spent a
# day not trading. Writes the keepalive log, a machine-readable row, and a per-account sentinel file. Rate-limited
# to one alert per account per ALERT_EVERY_S so the cron minute does not spam, but the FIRST one is immediate.
ALERT_EVERY_S="${MAC_MGR_ALERT_EVERY_S:-600}"
engine_alert() {
  local A="$1" MSG="$2" st="$HOME/logs/.ez_manage_${A}_alert_last" last=0 age=999999 nowt
  nowt=$(date +%s)
  [ -f "$st" ] && last=$(cat "$st" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ "$last" -gt 0 ] && age=$(( nowt - last ))
  if [ "$age" -ge "$ALERT_EVERY_S" ]; then
    echo "$nowt" > "$st" 2>/dev/null || true
    klog "🚨 ALERT $A: $MSG"
    local ad="$BASE/data/reports"
    mkdir -p "$ad" 2>/dev/null || true
    printf '{"ts":%s,"account":"%s","kind":"engine_restart_suppressed","msg":"%s"}\n' "$nowt" "$A" "$MSG" >> "$ad/engine_restart_alerts.jsonl" 2>/dev/null || true
    printf '%s\n' "$MSG" > "$BASE/data/ENGINE_ALERT_${A}" 2>/dev/null || true
  fi
}

# ═══ HARD RUN BOUND (2026-10-16 USER 3-MINUTE INVARIANT) ═══
# Spawning a writer or realtime worker BLOCKS this script — measured: cron pass 63180 sat 73 s inside a spawn
# subshell (04:30:00 -> 04:31:13Z) while holding the single-flight lock below, so every following */1 pass exited with
# "another run holds the lock" (63 of the last 200 log lines). The layer that is supposed to guarantee the 3-minute
# bound had therefore stopped checking, silently. A pass may now never outlive its own schedule: the EXIT/TERM trap
# releases the lock, so the next cron minute always gets a clean run.
RUN_MAX_S="${MAC_POS_RUN_MAX_S:-45}"
if [ -z "${MAC_POS_TIMED:-}" ] && [ -x /opt/homebrew/bin/timeout ]; then
  export MAC_POS_TIMED=1
  # 2026-10-22 --foreground: without it GNU timeout signals the whole process GROUP on expiry, so a pass that ran
  # past RUN_MAX_S after spawning run_with_watchdog.sh TERM'd the watchdog it had just started — measured as
  # "Watchdog received SIGTERM — killing child" 8-23 s after every one of fin's 5 relaunches, and 28-52 times per
  # account. --foreground signals only this script, which is the only process the bound is meant to cover.
  exec /opt/homebrew/bin/timeout --foreground -s TERM "$RUN_MAX_S" /bin/bash "$0" "$@"
fi

LOCK="/tmp/.mac_pos_writer_keepalive.lock"
# 2026-10-16: shlock only breaks a lock whose holder is DEAD. A holder that is merely SLOW or WEDGED keeps it
# forever, and because this script is the layer that guarantees the 3-minute bound, a stuck holder silently turns the
# guarantee off — measured 2026-10-16: 63 of the last 200 log lines were "another run holds the lock". Break the lock
# by AGE as well (the same pattern crypto_watchdog_cron.sh already uses), and kill the stuck holder so it cannot run
# on in parallel with us. A healthy pass takes well under a second after the python removals above.
# USER 2026-10-22: 75 s was BELOW the script's own measured worst case — the comment at the spawn site records a pass
# that sat 73 s inside a single spawn, so under Mac load (239 at 03:30Z) a healthy slow pass was declared stale and
# kill -9'd mid-spawn every 75 s. Each replacement pass then saw "no writer" and spawned again: 13 live subshells
# carrying this script's argv and a steady stream of "reap duplicate" lines. 170 s keeps the lock inside the 3-minute
# bound this layer guarantees while sitting well above the 73 s spawn block, so only a genuinely hung holder is broken.
LOCK_STALE_S="${MAC_POS_LOCK_STALE_S:-170}"  # < the 3-min guarantee, > the measured 73 s spawn block
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

# THE LEASE NEVER BLOCKS THE MAC (USER 2026-10-22: "s1 is an EMERGENCY system that runs as long as macbook is DEAD
# but it can NEVER block any mac action it needs to STAND DOWN as soon as mac trades").
#
# This script used to `exit 0` whenever S1 held the crypto lease, on the two-hosts-one-position argument. That had the
# authority backwards and it cost real trading: MEASURED 2026-10-22 04:32:04-04:35:03Z, four consecutive passes logged
# "crypto lease held by S1 — Mac writers stay DOWN (fenced); not starting anything" while S1 ran **zero** ez_manage
# processes (pgrep -cf 'ez_manage.py --account' = 0 on s1-int). So nobody was trading those accounts: the Mac was
# forbidden and the standby was empty. fin's engine stayed down through the whole window for exactly this reason.
#
# The dedup that the fence was protecting is S1's job, not the Mac's: the Mac is always authoritative, and S1 stands
# down the moment the Mac trades. So this layer now always keeps the Mac's writers and engines up and merely records
# who holds the lease, leaving the yield to live_failover_s1 / mac_failover_agent.
LEASE="$BASE/data/live_failover/lease_local.json"
if [ -f "$LEASE" ]; then
  holder=$(/usr/bin/sed -n 's/.*"holder"[[:space:]]*:[[:space:]]*"\([a-z0-9_]*\)".*/\1/p' "$LEASE" 2>/dev/null | head -1)
  [ "$holder" = "s1" ] && klog "crypto lease held by S1 — starting Mac writers ANYWAY (S1 is emergency-only and must stand down once the Mac trades); S1 yield is live_failover's job"
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
  # USER 2026-10-22: "dont restart again by watchdogs or anything unless there is a code change or code error that
  # needs fixing and gets alerted immediately — so no more than 1 'flap' per 24 hours." An ez_manage start costs
  # minutes of no trading (measured 10 s to 13 min depending on the account), so a restart is never cheap and this
  # script spent the day proving it: men 30 launches, flz 19, ang 9. One automatic restart per account per 24 h is
  # the budget; past it this script ALERTS and leaves the engine alone. Clearing the stamp is the deliberate
  # "I changed code, a restart is expected" action:  rm ~/logs/.ez_manage_<acct>_restart_budget
  local rb_stamp="$HOME/logs/.ez_manage_${A}_restart_budget"
  local rb_window="${MAC_MGR_RESTART_WINDOW_S:-86400}"
  local rb_last=0 rb_age=999999
  [ -f "$rb_stamp" ] && rb_last=$(cat "$rb_stamp" 2>/dev/null || echo 0)
  case "$rb_last" in ''|*[!0-9]*) rb_last=0 ;; esac
  [ "$rb_last" -gt 0 ] && rb_age=$(( now - rb_last ))
  MGR="run_with_watchdog.sh ez_manage.py --account $A"
  # 2026-10-22 USER ("a 300 s silence kill is FINE if you monitor the correct logs that produce output >once per
  # second"): liveness is the FRESHEST of the engine's two logs, not ez_manage_$A.log alone. Measured on inf in
  # steady state: ez_positions_quick_general_$A.log writes 1,555-2,183 lines/MINUTE (26-36/s) and is written by this
  # very process (the quick loops run embedded inside ez_manage), while ez_manage_$A.log writes only 36-169/min with
  # routine 300-560 s gaps around PERF_REPORT and the Redis flush. Judging the engine by the slow log is what killed
  # men 30x, flz 19x and ang 9x on 2026-10-22 while each start costs minutes. The quick log is silent during startup
  # (QUICK_EMBED starts last), so the slow log still covers boot — hence freshest-of-both plus the boot grace.
  mgr_log="$HOME/logs/ez_manage_$A.log"
  local _qlog="$HOME/logs/ez_positions_quick_general_$A.log"
  local _qmtime=0 _qage=999999 _mage=999999
  [ -f "$_qlog" ] && _qmtime=$(stat -f %m "$_qlog" 2>/dev/null || echo 0)
  [ "$_qmtime" -gt 0 ] && _qage=$(( now - _qmtime ))
  if [ -f "$mgr_log" ]; then
    mgr_mtime=$(stat -f %m "$mgr_log" 2>/dev/null || echo 0)
    [ "$mgr_mtime" -gt 0 ] && _mage=$(( now - mgr_mtime ))
  fi
  mgr_age="$_mage"
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
    # 2026-10-22: NEITHER the restart budget NOR the lease may leave an account with no engine. A missing child is not
    # a "flap" — there is no running engine to preserve, so starting one costs nothing and not starting one means the
    # account is simply unattended. MEASURED: fin sat with watchdog 49999 alive and child NONE while this branch
    # alerted "NO ENGINE CHILD ... budget is spent — NOT restarting". The budget guards against storms (below), never
    # against existing; and $gated (LEASE_FENCED/FAILOVER_IDLE) is no longer consulted, because S1 holding the lease
    # must never stop the Mac from running its own engine (USER 2026-10-22).
    if [ "$gone" -ge "$MAX_MGR_NOCHILD" ] && [ "$plvl" -le 2 ] && [ "$mgr_since" -gt 90 ]; then
      klog "$A: ez_manage watchdog ALIVE BUT NO ENGINE CHILD for ${gone}s (pressure_level=$plvl) -> replace watchdog $mgr_pids"
      [ "$rb_age" -lt "$rb_window" ] && engine_alert "$A" "NO ENGINE CHILD for ${gone}s and the 24h budget was already spent (last ${rb_age}s ago) — starting anyway: an account is never left without an engine"
      echo "$now" > "$rb_stamp"
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
  # 2026-10-22, revised the same day per USER: with liveness read from the >1/s quick log above, a short limit is
  # correct again and detects a real wedge far faster than the old 300 s ever did on the slow log. 120 s is ~3,000
  # missing quick-log lines in steady state, which is unambiguous. (The earlier 900 s here was compensating for
  # watching the WRONG log; keeping it would have delayed real wedge detection by 15 minutes.) The boot grace below
  # still covers startup, when only the slow log is writing.
  # Which log is authoritative: the >1/s quick log the moment it has produced output for THIS process generation
  # (quick mtime newer than the watchdog start), otherwise the slow log, which is all that writes during boot.
  local max_age
  if [ "$_qmtime" -gt 0 ] && [ "$mgr_started" -gt 0 ] && [ "$_qmtime" -gt "$mgr_started" ]; then
    mgr_age="$_qage"
    max_age="${MAC_MGR_MAX_LOG_AGE_ALL:-120}"
  else
    max_age="${MAC_MGR_MAX_BOOT_LOG_AGE:-900}"
  fi
  # 2026-10-22: the ENGINE gets its own boot grace. The writer's 240 s is far too short for an ez_manage start
  # (measured 8-13 min before the STEP 6 repair, and only the slow log writes until QUICK_EMBED comes up at the very
  # end), so a 120 s silence limit would otherwise kill every engine mid-startup — the exact loop that cost men 30
  # launches. run_with_watchdog.sh still covers this window with its own 600 s no-output + CPU-progress check.
  local mgr_boot_grace="${MAC_MGR_BOOT_GRACE:-600}"
  if [ -z "$mgr_pids" ]; then
    klog "$A: NO ez_manage watchdog -> start (decision engine was down)"
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ] && [ "$rb_age" -lt "$rb_window" ]; then
    engine_alert "$A" "ez_manage SILENT (${mgr_age}s > ${max_age}s) but the 24h restart budget is spent (last restart ${rb_age}s ago) — NOT restarting; a genuinely wedged engine now needs a human/code fix"
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ]; then
    klog "$A: ez_manage SILENT (freshest of quick+manage logs ${mgr_age}s > ${max_age}s) -> kill watchdog $mgr_pids + child and restart (24h budget consumed)"
    echo "$now" > "$rb_stamp"
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
  # USER 2026-10-22: a STALE heartbeat is not the same as a STUCK one. Measured 03:18-03:31Z at Mac load 239: every
  # writer was SIGKILLed ~300s after its own start ("heartbeat 945s > 180s, up 302s" — a heartbeat OLDER than the
  # process) and the ages FELL between passes (945s -> 484s), so the writers were saving, just slower than
  # MAX_HB_AGE under load; the restart added load and bought another kill. 32 WEDGED/reap events in 30 min, no
  # convergence. This pass runs every minute, so demand NO FORWARD PROGRESS for HB_STUCK_PASSES consecutive passes
  # before killing: any advance of the heartbeat mtime resets the counter, a genuinely frozen writer still dies
  # within ~HB_STUCK_PASSES minutes, and a slow-but-alive writer is left alone to finish its save.
  hb_mtime=$(stat -f %m "$BASE/$A/.positions_service_heartbeat.json" 2>/dev/null || echo 0)
  hb_state="/tmp/.mac_pos_hb_state_$A"
  hb_prev=$(awk '{print $1}' "$hb_state" 2>/dev/null || echo "")
  hb_stall=$(awk '{print $2+0}' "$hb_state" 2>/dev/null || echo 0)
  if [ "$hb_mtime" = "$hb_prev" ]; then hb_stall=$((hb_stall + 1)); else hb_stall=0; fi
  echo "$hb_mtime $hb_stall" > "$hb_state"
  stamp_file="/tmp/.mac_pos_started_$A"
  started=0
  [ -f "$stamp_file" ] && started=$(cat "$stamp_file" 2>/dev/null || echo 0)
  if [ "$started" -gt 0 ]; then since_start=$(( now - started )); else since_start=999999; fi  # no stamp = not started by us = no boot grace
  if [ -z "$pids" ]; then
    klog "$A: no writer -> start"
  elif [ "$hb_age" -gt "$MAX_HB_AGE" ] && [ "$since_start" -gt "$BOOT_GRACE" ] && [ "$hb_stall" -ge "$HB_STUCK_PASSES" ]; then
    klog "$A: writer WEDGED (heartbeat ${hb_age}s > ${MAX_HB_AGE}s, up ${since_start}s, no progress for ${hb_stall} passes) -> kill $pids and restart"
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
