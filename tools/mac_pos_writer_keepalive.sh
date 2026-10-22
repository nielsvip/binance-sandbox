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
# ═══ 2026-10-22 PASS BUDGET SPLIT — NEITHER PASS MAY STARVE THE OTHER ═══
# The whole script runs under `timeout -s TERM $RUN_MAX_S` (45s). PASS 1 (engines) and PASS 2 (position writers)
# shared that one budget with no per-pass bound, so whichever ran first could consume all of it. On 2026-10-16 the
# writers were first and starved the engine check (that is why PASS 1 was moved ahead of them). MEASURED today at
# load 209-290 on 12 cores, the mirror image: engine_keepalive took 20-40s PER ACCOUNT, so a 45s pass covered 1-2 of
# 5 accounts and PASS 2 — the position-writer pass this script is NAMED for — was never reached at all. fin sat with
# NO WRITER for 200s and nothing started it; all five writers had to be launched by hand. Ordering alone cannot fix
# this, it only chooses the victim. PASS 1 now gets PASS1_MAX_S of the budget and stops at that deadline, leaving the
# remainder to PASS 2, which is cheap (stat + pgrep per account). Coverage stays gapless because the rotation pointer
# advances by the number of accounts PASS 1 actually processed, not by one.
PASS1_MAX_S="${MAC_POS_PASS1_MAX_S:-22}"   # engines keep the larger half of RUN_MAX_S; writers are guaranteed the rest
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
  # 2026-10-22 -k: TERM ALONE DOES NOT BOUND THIS SCRIPT. A bash trap cannot run while the shell is blocked in a
  # foreground child, so a pass stuck in a `pgrep`/`stat`/spawn under extreme load never reaches `trap ... exit`;
  # `timeout` sends its one TERM, exits, and the pass is reparented to pid 1 and runs on UNBOUNDED AND LOCKLESS —
  # precisely the state the trap fix at :122 was meant to end. MEASURED now, with that fix in place: 4 orphaned
  # copies (PPID 1) aged 1h43m-1h45m, still executing the kill -9 paths against ez_manage watchdogs and children.
  # -k escalates to SIGKILL after the grace period, which no child can defer. The EXIT trap is then skipped, so the
  # lock file outlives the holder — already handled: shlock breaks a lock whose pid is dead, and LOCK_STALE_S breaks
  # it by age. Worst-case bound is RUN_MAX_S + grace = 60s, still inside the 3-minute invariant.
  exec /opt/homebrew/bin/timeout --foreground -k 15 -s TERM "$RUN_MAX_S" /bin/bash "$0" "$@"
fi

# ═══ ORPHAN REAPER — a pass that outlived its own bound is not a supervisor, it is a loose kill -9 loop ═══
# Belt-and-braces behind `timeout -k`: anything carrying this script's argv, reparented to init, and older than
# ORPHAN_MAX_S cannot be a legitimate pass (the bound above is RUN_MAX_S + 15s). Never touches this pid or a pass
# that still has a live parent.
# NOTE: macOS `ps` has NO `etimes` (seconds) keyword — that is GNU-only, and asking for it makes ps fail outright
# ("ps: etimes: keyword not found"), which would have made this reaper silently match nothing. Parse BSD `etime`
# ([[dd-]hh:]mm:ss) instead.
ORPHAN_MAX_S="${MAC_POS_ORPHAN_MAX_S:-120}"
for _op in $(/bin/ps -eo pid=,ppid=,etime=,command= 2>/dev/null \
  | /usr/bin/awk -v me="$$" -v cap="$ORPHAN_MAX_S" '
      function secs(e){ d=0; t=e; if (split(e,a,"-")==2) { d=a[1]+0; t=a[2] }
                        m=split(t,b,":");
                        if (m==3) return d*86400+b[1]*3600+b[2]*60+b[3];
                        if (m==2) return d*86400+b[1]*60+b[2];
                        return d*86400+b[1]+0 }
      $2==1 && $1!=me && secs($3)>cap && /mac_pos_writer_keepalive/ {print $1}'); do
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [keepalive] ORPHAN REAPED: pid=$_op (PPID 1, older than ${ORPHAN_MAX_S}s) — a pass that outlived its timeout bound" >&2
  kill -9 "$_op" 2>/dev/null || true
done

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
# 2026-10-22 (USER "guarantee all accounts keep trading whether on mac or s1 including fin"): THE HANDLER MUST
# EXIT. A bash trap on a SIGNAL runs the handler and then RESUMES at the interrupted command — it does not end the
# script. So when the `timeout --foreground -s TERM "$RUN_MAX_S"` at :75 fired at 45 s, this handler released the
# single-flight lock and the pass KEPT RUNNING, now unbounded AND lockless, reparented to pid 1 because timeout had
# already reaped. The next */1 cron minute then saw no lock and started another. Measured 2026-10-22 06:3xZ: 12
# concurrent copies of this script, 9 of them orphaned (PPID 1), ages 6 min to 1 h 27 m — every one still executing
# the kill -9 paths at :157/:234/:271-272/:320-321/:381 against ez_manage watchdogs and children. That is the engine
# churn: 216 ez_manage watchdog starts and 123 [WATCHDOG_SINGLETON] refusals in 24 h (men alone 60/30), men's
# "[SIGKILL_EXTERNAL] exit 137 after 22s" at 06:17:22, and the 0<->1 engine flapping that reset
# never_untraded_guard's untraded_s to 0 on every 30 s sample so it logged WITHIN_GRACE forever and never enforced
# the 3-minute rule while fin had gone 48 min without an execution. `exit` is what run_with_watchdog.sh:222 already
# does for the same reason.
trap 'rm -f "$LOCK"; exit' EXIT INT TERM

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
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ] && [ "$rb_age" -lt "$rb_window" ] \
       && ! "$PY" "$BASE/tools/account_holds_risk.py" --account "$A" --quiet 2>/dev/null; then
    # 2026-10-22 (USER: "can you ABSOLUTELY GUARANTEE no account can stop trading EVER when there is any open
    # position?"): the budget may only refuse a restart for an account that is PROVABLY FLAT. The refusal below is
    # correct for an idle account — a wedged engine with nothing at stake should wait for a human rather than be
    # restarted forever — and it is indefensible for an account holding real exposure, which is what it did at
    # 06:42:06Z today ("ALERT ang: ez_manage SILENT (158s > 120s) ... NOT restarting") while ang held 6 open
    # positions. account_holds_risk exits 0 on holds-risk AND on any unreadable/missing file, so only a positions
    # file that parses and is genuinely empty reaches this arm. 61 positions were open across 6 accounts when
    # this was written.
    engine_alert "$A" "ez_manage SILENT (${mgr_age}s > ${max_age}s) but the 24h restart budget is spent (last restart ${rb_age}s ago) and the account is PROVABLY FLAT — NOT restarting; a genuinely wedged engine now needs a human/code fix"
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ] && [ "${_MGR_OVERLOADED:-0}" = "1" ]; then
    # 2026-10-22 LOAD GATE (USER positions-reliability): a starved engine looks exactly like a
    # wedged one (no log output), and killing it buys an 8-13 min bootstrap storm that raises the
    # load that caused the silence — measured all 2026-10-22, a kill every ~10 min/account at Mac
    # load 200+. Defer the kill (the NOCHILD branch above still replaces a truly dead engine, and
    # writers are unaffected); retried every pass, alerted rate-limited like all engine alerts.
    engine_alert "$A" "ez_manage SILENT (${mgr_age}s > ${max_age}s) but host overloaded (load ${_MGR_LOAD1:-?} on ${_MGR_NCPU:-?} cores) — kill DEFERRED to break the kill→bootstrap→load loop"
    klog "$A: ez_manage SILENT (${mgr_age}s) but host overloaded (load ${_MGR_LOAD1:-?}) -> kill DEFERRED, retry next pass"
  elif [ "$mgr_age" -gt "$max_age" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ]; then
    _risk_note="budget available"
    if [ "$rb_age" -lt "$rb_window" ]; then
      _risk_note="BUDGET OVERRIDDEN: $("$PY" "$BASE/tools/account_holds_risk.py" --account "$A" 2>/dev/null | sed 's/^[a-z]*: //')"
    fi
    klog "$A: ez_manage SILENT (freshest of quick+manage logs ${mgr_age}s > ${max_age}s) -> kill watchdog $mgr_pids + child and restart (${_risk_note})"
    echo "$now" > "$rb_stamp"
    for p in $mgr_pids; do kill -9 "$p" 2>/dev/null; done
    for p in $(pgrep -f "bin/python -u ez_manage.py --account $A"); do kill -9 "$p" 2>/dev/null; done
    mgr_pids=""
  fi
  # ── NOT-TRADING IS A FAULT, NOT A STATE (USER 2026-10-22: "NO ACCOUNT CAN EVER NOT TRADE", "you need to make sure
  # NO account can EVER stop trading"). Log freshness cannot see this: fin and men logged happily while executing
  # NOTHING. MEASURED 2026-10-22 05:5xZ from data/vec_live/live_exec_<acct>.jsonl, which gets a row for EVERY vec
  # decision outcome — inf 153 outcomes/h (15 SUCCESS) and flz 141 (23 SUCCESS), against men 5 and fin 4, all of
  # those "already handled"/"already flat". Both engines were up, both logs were fresh, neither traded.
  # The test is execution, and it is conditioned on WORK BEING AVAILABLE so it cannot false-positive between bars:
  # dictates pending in the spool AND the ledger not advancing => this account is not trading => repair it locally.
  # Local repair first; mac_failover_agent still raises not_trading_account (30 s) for the S1 path when repair fails.
  local nt_ledger="$BASE/data/vec_live/live_exec_${A}.jsonl"
  # ONE BAR of the lane's current cadence, derived — not a constant. USER 2026-10-22: "we trade 15m bars for now
  # (copy trading from vector trades until parity is reached) but the system trades every ms", so this bound has to
  # shrink on its own when the cadence does. VEC_LANE_BAR_SECONDS is the single knob (live_twins/vec_driven.py
  # BAR_SECONDS reads the same one); at the 15m copy-trading cadence this is 900 s, at a 60 s cadence it is 60 s, and
  # the operator's 30 s target becomes reachable for free once the lane itself ticks faster than that.
  # Why one bar and not 30 s today: this ledger only gains rows when dictates are PROCESSED, and dictates arrive per
  # bar, so a healthy account is legitimately quiet for minutes — flz booked 23 SUCCESS in the hour its ledger sat
  # 208 s idle, and a 120 s limit flagged four of five healthy accounts and replaced two that were trading.
  local nt_bar="${VEC_LANE_BAR_SECONDS:-900}"
  case "$nt_bar" in ''|*[!0-9]*) nt_bar=900 ;; esac
  local nt_limit="${MAC_NOT_TRADING_S:-$nt_bar}"
  local nt_age nt_pending nt_mt
  # NOT age_of(): that helper greps the FIRST "ts" field out of the file, which is correct for a single-object
  # heartbeat but on an append-only JSONL ledger returns the OLDEST row. 2026-10-22 05:54Z it therefore reported
  # 31,224s / 37,875s / 341,946s for ledgers that were seconds old and this rule killed ALL FIVE engines, including
  # inf and flz which were actively trading (15 and 23 SUCCESS in the preceding hour). Use the file's mtime, and when
  # the ledger cannot be read treat it as fresh — an unreadable signal must never cost a running engine.
  nt_mt=0
  [ -f "$nt_ledger" ] && nt_mt=$(/usr/bin/stat -f %m "$nt_ledger" 2>/dev/null || echo 0)
  case "$nt_mt" in ''|*[!0-9]*) nt_mt=0 ;; esac
  if [ "$nt_mt" -gt 0 ]; then nt_age=$(( now - nt_mt )); else nt_age=0; fi
  nt_pending=$(/bin/ls "$BASE/data/vec_live/intents/" 2>/dev/null | /usr/bin/grep -c "^PB-${A}-" 2>/dev/null || echo 0)
  case "$nt_pending" in ''|*[!0-9]*) nt_pending=0 ;; esac
  # DISABLED BY DEFAULT 2026-10-22 06:2xZ — THIS RULE WAS THE RESTART LOOP. I added it to guarantee "no account can
  # ever stop trading" and it did the exact opposite: it killed fin at 05:54:27, 06:05:23 and 06:16:31Z, men at
  # 05:54:32, 06:05:29 and 06:16:40Z, flz at 06:07:12Z — every ~11 minutes. A freshly restarted engine HAS a stale
  # ledger and pending dictates by definition, so the kill reset it before it could trade, which kept the ledger
  # stale, which fired the rule again. The boot grace could not save it either: $mgr_since comes from
  # /tmp/.mac_mgr_started_<acct>, which is stamped only when THIS script spawns the watchdog, not when the watchdog
  # respawns the child — measured ang/inf stamps 1,837 s old while their engines were seconds old.
  # Re-enable only with a grace measured from the ENGINE CHILD's own start and a limit proven not to fire on a
  # healthy account: MAC_NOT_TRADING_ENFORCE=1.
  if [ "${MAC_NOT_TRADING_ENFORCE:-0}" = "1" ] && [ -n "$mgr_pids" ] && [ "$nt_pending" -gt 0 ] && [ "$nt_age" -gt "$nt_limit" ] && [ "$mgr_since" -gt "$mgr_boot_grace" ]; then
    engine_alert "$A" "NOT TRADING: ${nt_pending} dictate(s) pending and live_exec_${A}.jsonl has not advanced in ${nt_age}s (> ${nt_limit}s) while the engine is up — replacing the engine"
    klog "$A: NOT TRADING (${nt_pending} pending dictates, ledger ${nt_age}s stale) -> replacing engine $mgr_pids"
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

# ═══ 2026-10-22 ROTATION (USER positions-reliability): one pass never outlives RUN_MAX_S
# (45 s), and under Mac load 200+ the engine section alone can eat it — so a fixed account order
# starves the tail: measured, fin's writer-stall counter froze for an hour (state mtime 16:29Z
# while passes ran) because passes kept dying before reaching the 4th account. Rotate the starting
# account each pass; coverage is then eventually-consistent instead of never. Order only — no
# account is skipped and none is preferred.
_mr_seq_file="/tmp/.mac_pos_keepalive_seq"
_mr_rot=$(cat "$_mr_seq_file" 2>/dev/null || echo 0)
case "$_mr_rot" in ''|*[!0-9]*) _mr_rot=0 ;; esac
_mr_n=$(echo $ACCOUNTS | wc -w | tr -d ' ')
case "$_mr_n" in ''|*[!0-9]*) _mr_n=0 ;; esac
if [ "$_mr_n" -gt 0 ]; then
  _mr_rot=$(( _mr_rot % _mr_n ))
  echo $(( (_mr_rot + 1) % _mr_n )) > "$_mr_seq_file" 2>/dev/null || true
  _mr_i=0; _mr_head=""; _mr_tail=""
  for _mr_a in $ACCOUNTS; do
    if [ "$_mr_i" -lt "$_mr_rot" ]; then _mr_head="$_mr_head $_mr_a"; else _mr_tail="$_mr_tail $_mr_a"; fi
    _mr_i=$(( _mr_i + 1 ))
  done
  ACCOUNTS_ROTATED="$_mr_tail $_mr_head"
else
  ACCOUNTS_ROTATED="$ACCOUNTS"
fi
# ═══ 2026-10-22 LOAD SNAPSHOT (one sysctl per pass, shared by every account below). Fail-open:
# unreadable load reads as not-overloaded and nothing changes.
_MGR_LOAD1=$(/usr/sbin/sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | awk '{print $1}')
_MGR_NCPU=$(/usr/sbin/sysctl -n hw.ncpu 2>/dev/null || echo 0)
case "$_MGR_NCPU" in ''|*[!0-9]*) _MGR_NCPU=0 ;; esac
_MGR_OVERLOADED=0
if [ "$_MGR_NCPU" -gt 0 ]; then
  _MGR_OVERLOADED=$(awk -v l="$_MGR_LOAD1" -v n="$_MGR_NCPU" 'BEGIN{print ((l+0) > 4*n) ? 1 : 0}')
fi
case "$_MGR_OVERLOADED" in 1|0) ;; *) _MGR_OVERLOADED=0 ;; esac

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# PASS 1 — THE DECISION ENGINE, FIRST AND ALONE.
# 2026-10-16 USER ("NO account can be inactive for >3min"): engine supervision used to live at the BOTTOM of a loop
# that first handled writers and realtime workers. Starting any of those blocks this script (measured: cron run 63180
# sat 73 s inside a spawn subshell at 04:30:00-04:31:13Z while holding the single-flight lock), so on a bad pass the
# engine check was reached late or not at all, and every run behind it aborted with "another run holds the lock".
# The 3-minute invariant is about ENGINES, so engines are now checked before anything else can stall the pass.
_p1_start=$(date +%s)
_p1_done=0
for A in $ACCOUNTS_ROTATED; do
  _p1_elapsed=$(( $(date +%s) - _p1_start ))
  if [ "$_p1_done" -gt 0 ] && [ "$_p1_elapsed" -ge "$PASS1_MAX_S" ]; then
    klog "PASS1 budget spent (${_p1_elapsed}s >= ${PASS1_MAX_S}s) after $_p1_done/$(echo $ACCOUNTS | wc -w | tr -d ' ') accounts — yielding to the writer pass; rotation resumes at $A"
    break
  fi
  engine_keepalive "$A"
  _p1_done=$(( _p1_done + 1 ))
done
# Gapless round-robin: resume at the first account PASS 1 did NOT reach, instead of advancing by one. Without this a
# budget-truncated pass would re-check the same leading accounts next minute and the tail could go unchecked for
# 5+ passes, which is the >3min inactivity this layer exists to prevent.
if [ "$_mr_n" -gt 0 ] && [ "$_p1_done" -gt 0 ]; then
  echo $(( (_mr_rot + _p1_done) % _mr_n )) > "$_mr_seq_file" 2>/dev/null || true
fi
klog "PASS1 done: $_p1_done account(s) in $(( $(date +%s) - _p1_start ))s"

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# PASS 2 — position writers and realtime failover workers (unchanged logic, now strictly after the engines).
# It is reached on EVERY pass now: PASS 1 can no longer outspend the shared budget (see PASS1_MAX_S above).
_p2_start=$(date +%s)
for A in $ACCOUNTS_ROTATED; do
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
klog "PASS2 done: $(echo $ACCOUNTS | wc -w | tr -d ' ') writer(s) checked in $(( $(date +%s) - _p2_start ))s (total pass $(( $(date +%s) - _p1_start ))s)"
