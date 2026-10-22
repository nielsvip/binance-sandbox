#!/bin/bash
# run_with_watchdog.sh - ULTRA ROBUST WATCHDOG FOR PYTHON SCRIPTS

# Configuration
WORKDIR="/Users/niels/Documents/binance"
# USER 2026-10-07: per-OS interpreter — S1 has no /opt/anaconda3 (was exit-127 loop since Oct06; men unmanaged 00:01-01:14Z).
if [[ "$(uname)" == "Darwin" ]]; then
    PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
else
    # 2026-10-17 S1 COULD NOT HAVE TAKEN OVER ANYTHING. `.venv` is the BACKTEST environment and is incomplete:
    # measured on S1, 9 of 10 live scripts fail to even import under it — ez_manage / tradier_manage /
    # tradier_positions / ez_indicators / ez_positions_watchdog / tradier_indicators die on `aiofiles`,
    # ez_klines / ez_prices on `aiohttp`, tradier_klines on `pandas`. Because this preference picked `.venv`
    # whenever it merely EXISTS, every gated live script S1 started crash-looped on import: ez_indicators had been
    # restarting every 32 s since 2026-10-14, which is why S1's newest crypto market snapshot was 2.1 DAYS old
    # while the lease, the arm file and every readiness signal said the standby was ready.
    # binance_env_mac is the designated live env ("exact copy of the Mac live env", py 3.11.14) and is exactly what
    # tools/s1_trading_supervisor.sh already uses to start the traders — this only aligns the watchdog with the
    # supervisor, so both halves of a takeover run the same interpreter as the Mac. All 10 import cleanly under it.
    # `.venv` is KEPT as a fallback (it is a working interpreter for anything whose deps it does have), just no
    # longer preferred for live scripts. Darwin branch deliberately untouched.
    PYTHON="/home/niels/.conda/envs/binance_env_mac/bin/python"
    [ -x "$PYTHON" ] || PYTHON="/home/niels/.conda/envs/binance_env_mac/bin/python"
    [ -x "$PYTHON" ] || PYTHON="/home/niels/.conda/envs/binance_env_mac/bin/python"
    [ -x "$PYTHON" ] || PYTHON="/usr/bin/python3"
    [ -x "$PYTHON" ] || PYTHON="python3"
fi

# 2026-07-28: raise the file-descriptor ceiling. launchd hands children a soft
# RLIMIT_NOFILE of 256, which tradier_manage (117 positions + Tradier HTTP +
# yfinance cache handles) exhausts after ~4 days uptime, producing
# "[Errno 24] Too many open files" in market_data_sync_loop / symbol sync /
# heartbeat. Mitigation only — the underlying leak is the un-blacklisted
# yfinance retry path for delisted tickers (BK, CTRA).
ulimit -n 8192 2>/dev/null || ulimit -n 4096 2>/dev/null || true

# Use local logs if /Users/niels/logs is not writable
LOGDIR="/Users/niels/logs"
if [ ! -w "$LOGDIR" ]; then
    LOGDIR="$WORKDIR/logs"
    mkdir -p "$LOGDIR"
fi
# 2026-10-06 USER: test hooks (no live impact) — pytest dry-runs point these at /tmp.
if [[ -n "${WATCHDOG_TEST_LOGDIR:-}" ]]; then LOGDIR="$WATCHDOG_TEST_LOGDIR"; mkdir -p "$LOGDIR"; fi
if [[ -n "${WATCHDOG_TEST_WORKDIR:-}" ]]; then WORKDIR="$WATCHDOG_TEST_WORKDIR"; fi

# Timeouts and limits
TIMEOUT=0                 # DISABLED — never kill healthy processes. NO_OUTPUT_TIMEOUT handles stuck ones.
NO_OUTPUT_TIMEOUT=300     # 5 minutes default — overridden per-script below
# NO-OUTPUT CPU GATE (2026-10-20). A silent log only triggers a restart when the process ALSO made no CPU
# progress. CPU_MIN_S of CPU observed over CPU_WINDOW_S means "working, leave it alone"; HARD_MULT bounds how
# long a silent-but-busy process can be shielded, so a spinning process is still recycled eventually.
NO_OUTPUT_CPU_MIN_S=${NO_OUTPUT_CPU_MIN_S:-2}
NO_OUTPUT_CPU_WINDOW_S=${NO_OUTPUT_CPU_WINDOW_S:-120}
NO_OUTPUT_HARD_MULT=${NO_OUTPUT_HARD_MULT:-6}

# Cumulative CPU seconds of a pid. `ps -o time=` is the one format both macOS and Linux emit:
# `[DD-]HH:MM:SS` or `MM:SS.ss`. Returns 0 when the pid is gone or unparseable (never a false "working").
_cpu_secs() {
    # A dead pid makes `ps` print NOTHING, so awk's main block never runs; without the END guard this
    # returned the empty string and `$((cpu - base))` would abort the watchdog's monitor loop.
    ps -o time= -p "$1" 2>/dev/null | awk '
        { gsub(/^[ \t]+|[ \t]+$/, "", $0); if ($0 == "") { print 0; ok = 1; exit }
          d = 0; t = $0
          if (t ~ /-/) { split(t, a, "-"); d = a[1] + 0; t = a[2] }
          n = split(t, p, ":")
          s = 0
          for (i = 1; i <= n; i++) s = s * 60 + (p[i] + 0)
          printf "%d\n", d * 86400 + s; ok = 1; exit }
        END { if (!ok) print 0 }' 2>/dev/null || echo 0
}
MIN_RESTART_INTERVAL=5  # EMERGENCY 2026-08-14 tradier_indicators must be 60s; 60s gap caused STALE_HOLD sink  # 2026-04-28 user: bumped 3→60s. Workers OOM-killing every ~60s with 3s respawn = no recovery time for memory pages. 60s lets the OS reclaim before next allocation spike.
MAX_RAPID_RESTARTS=15      # Max restarts within rapid window
RAPID_WINDOW=300          # 5 minutes for rapid restart detection

# Parse arguments
if [ -z "$1" ]; then
    echo "Error: No script name provided"
    echo "Usage: $0 script_name.py [args...]"
    exit 1
fi

# SELF-REEXEC SUPPORT (2026-10-20). A bash script is parsed once at startup, so a long-lived wrapper keeps the
# `case` timeouts, the fence logic and every other value it was BORN with. Measured 2026-10-20 17:23Z: the
# ez_market_data (pid 5216) and ez_indicators (pid 4708) wrappers had been running since Sun Oct 18 02:36 against a
# file last modified two and a half days later, so EVERY timeout fix and the 2026-10-20 account-scope fence fix were
# unloaded on exactly the two worst churners. Proof it mattered: ez_rankings (started Oct 19 23:04) was still
# killing at the OLD limit — "No output for 903s (limit: 900s)" — while the file on disk had said 1800 for hours.
# Keep the original argv so the loop can re-exec itself cleanly between runs (never mid-run).
WD_ARGV=("$0" "$@")
if [[ "$(uname)" == "Darwin" ]]; then
    WD_SELF_MTIME=$(stat -f "%m" "$0" 2>/dev/null || echo 0)
else
    WD_SELF_MTIME=$(stat -c "%Y" "$0" 2>/dev/null || echo 0)
fi

SCRIPT="$1"
shift
ARGS=("$@")

# ═══ TRADIER MARKET-HOURS GATE ═══
# tradier_* scripts are not allowed to run outside Mon-Fri 13:30-20:00 UTC.
# Otherwise this wrapper will respawn them after we manually kill them on
# weekends/after close, and they consume CPU + spam stale-log alerts.
# Refuse to launch; exit cleanly so launchd/cron don't treat it as failure.
tradier_market_open_utc() {
    local dow hour minute mins
    dow=$(date -u +%u)
    hour=$(date -u +%H)
    minute=$(date -u +%M)
    mins=$((10#$hour * 60 + 10#$minute))
    [ "$dow" -le 5 ] && [ "$mins" -ge 810 ] && [ "$mins" -lt 1200 ]
}

# ez_news_scanner: starts 30min before open (13:00 UTC) for premarket agent work
ez_news_market_open_utc() {
    local dow hour minute mins
    dow=$(date -u +%u)
    hour=$(date -u +%H)
    minute=$(date -u +%M)
    mins=$((10#$hour * 60 + 10#$minute))
    [ "$dow" -le 5 ] && [ "$mins" -ge 780 ] && [ "$mins" -lt 1200 ]
}

case "$SCRIPT" in
    tradier_prices.py)
        # 2026-08-19 FIX: pre-warm 30 min before open (13:00 UTC) so marks are fresh by 09:35 ET email (13:35 UTC).
        # Previously gated at 13:30 — 5-min race + watchdog 5-min tick = stale banner every morning.
        if ! ez_news_market_open_utc; then
            mkdir -p "$LOGDIR"
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [TRADIER_MARKET_GATE] refusing to launch $SCRIPT ${ARGS[*]} — outside Mon-Fri 13:00-20:00 UTC (pre-warm for marks)" >> "$LOGDIR/tradier_market_gate.log"
            exit 0
        fi
        ;;
    tradier_manage.py|tradier_positions.py|tradier_indicators.py|tradier_rankings.py|tradier_premarket_scanner.py|tradier_options_csp_monitor.py|tradier_options_analyzer.py|tradier_options_agent.py|tradier_hourly_reconfig.py|tradier_webhook_bridge.py)
        if ! tradier_market_open_utc; then
            mkdir -p "$LOGDIR"
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [TRADIER_MARKET_GATE] refusing to launch $SCRIPT ${ARGS[*]} — outside Mon-Fri 13:30-20:00 UTC" >> "$LOGDIR/tradier_market_gate.log"
            exit 0
        fi
        ;;
    ez_news_scanner.py)
        if ! ez_news_market_open_utc; then
            mkdir -p "$LOGDIR"
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [EZ_NEWS_MARKET_GATE] refusing to launch $SCRIPT ${ARGS[*]} — outside Mon-Fri 13:00-20:00 UTC (30min pre-open)" >> "$LOGDIR/tradier_market_gate.log"
            exit 0
        fi
        ;;
esac

# ═══ SINGLETON GUARD: function called before every launch ═══
FULL_CMD="$SCRIPT ${ARGS[*]}"
MY_PID=$$
MY_PPID=$PPID
kill_duplicate_python() {
    local EXISTING_PIDS=""
    local _dup_pat="python.*-u $SCRIPT ${ARGS[*]}"
    if [ ${#ARGS[@]} -eq 0 ]; then
        # 2026-07-02: with empty ARGS the pattern "python.*-u SCRIPT " (trailing space) matched
        # EVERY instance of SCRIPT regardless of its args, so a no-arg watchdog SIGKILLed a
        # correctly-args'd sibling's child every 60s loop (tradier_positions.py --accounts
        # tra trb trc restart churn 2026-07-02: rogue arg-less watchdog murdered the real child).
        # Anchor to end-of-line so an empty-ARGS watchdog only matches a genuinely no-arg process.
        _dup_pat="python.*-u $SCRIPT[[:space:]]*\$"
    fi
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        [ "$pid" = "$MY_PID" ] && continue
        [ "$pid" = "$MY_PPID" ] && continue
        EXISTING_PIDS="$EXISTING_PIDS $pid"
    done < <(pgrep -f "$_dup_pat" 2>/dev/null || true)
    if [ -n "$EXISTING_PIDS" ]; then
        log "[SINGLETON] Killing duplicate python instances of '$FULL_CMD':$EXISTING_PIDS"
        for pid in $EXISTING_PIDS; do
            kill -TERM "$pid" 2>/dev/null || true
        done
        sleep 3
        for pid in $EXISTING_PIDS; do
            kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
        done
    fi
}
# Setup logging
mkdir -p "$LOGDIR"
SCRIPT_BASE="${SCRIPT%.py}"
# Extract --account value from ARGS for per-account log file names
ACCT_SUFFIX=""
for i in "${!ARGS[@]}"; do
    next=$((i+1))
    if [[ "${ARGS[$i]}" == "--account" || "${ARGS[$i]}" == "--accounts" ]] && [[ $next -lt ${#ARGS[@]} ]] && [[ -n "${ARGS[$next]}" ]]; then
        ACCT_SUFFIX="_${ARGS[$next]}"
        break
    fi
done

# ═══ WATCHDOG SINGLETON LOCK ═══
# Without this, two concurrent watchdogs for the same script+account fight
# each other: each runs kill_duplicate_python → SIGKILLs the other's child →
# 15 rapid restarts → both watchdogs give up. Caused men loop 2026-04-26 02:02-02:04.
WATCHDOG_LOCK="$LOGDIR/.watchdog_${SCRIPT_BASE}${ACCT_SUFFIX}.pid"
if [ -f "$WATCHDOG_LOCK" ]; then
    OLD_WD_PID=$(cat "$WATCHDOG_LOCK" 2>/dev/null)
    # 2026-10-22: NEVER mistake OURSELVES for the incumbent. [WD_SELF_REEXEC] (:752) re-execs this script with `exec`
    # to pick up a changed copy, and `exec` PRESERVES the pid — which is exactly why the re-exec was considered safe
    # for the lock. But this check then read our own pid back out of the lock file, `kill -0 $$` succeeded, and the
    # fresh image exited as a "duplicate" of itself. The EXIT trap below then removed the lock, so the account was
    # left with NO watchdog and NO child at all: measured on ez_indicators 2026-10-22 05:02:18-05:03:55Z
    # ("[WD_SELF_REEXEC] … pid 46034 preserved" immediately followed by "[WATCHDOG_SINGLETON] Another watchdog is
    # alive (pid=46034) — refusing to start"), i.e. the one supervisor the crypto indicator producer has.
    if [ -n "$OLD_WD_PID" ] && [ "$OLD_WD_PID" != "$$" ] && kill -0 "$OLD_WD_PID" 2>/dev/null; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [WATCHDOG_SINGLETON] Another watchdog is alive (pid=$OLD_WD_PID) for ${SCRIPT_BASE}${ACCT_SUFFIX} — refusing to start to avoid SIGKILL fight" | tee -a "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_watchdog.log"
        exit 0
    fi
fi
echo "$$" > "$WATCHDOG_LOCK"
# 2026-10-22: `kill -TERM ${CHILD_PID:-0}` degenerates to `kill -TERM 0` whenever CHILD_PID is still unset, and
# pid 0 means "every process in MY process group". crypto_watchdog_cron.sh starts its wrappers with `nohup … &`
# from a non-interactive shell, so a whole cron run shares one group — one early exit there would TERM every
# sibling wrapper and its child. Unreachable today (this trap is replaced by cleanup/handle_sigterm at :905-:906
# before `main` runs, and nothing exits at top level in between), but "signal my entire process group" can never
# be the intent of "kill my child", so it is spelled out rather than left to ${VAR:-0}.
trap 'rm -f "$WATCHDOG_LOCK"; [ -n "${CHILD_PID:-}" ] && kill -TERM "$CHILD_PID" 2>/dev/null; exit' EXIT INT TERM

# Only the watchdog that owns the lock may clean up a stale Python child.
# Previously this ran before the lock check, so every duplicate watchdog
# killed the healthy incumbent child and then exited.
kill_duplicate_python

APP_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_app.log"  # legacy, no longer written to
WD_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_watchdog.log"

# Per-script NO_OUTPUT_TIMEOUT and log file selection
# Each script has different output frequency — tight timeouts catch failures fast
case "$SCRIPT_BASE" in
    # 2026-10-22: 120 -> 900. The 120 s bar is right for steady state (it writes every ~1s) but it is UNSURVIVABLE
    # during warm-up, and warm-up is where this process dies: the [WARMUP] loop emits one line per 25 symbols, and
    # measured today those lines were 79-118 s apart against a 120 s limit. The NO-OUTPUT CPU GATE cannot shield it
    # either — it needs NO_OUTPUT_CPU_MIN_S=2 s of CPU per 120 s window, while the warm-up heartbeats measured
    # `cpu +0.14s in 10.0s` (~1.7 s/120 s) because the pass is I/O-bound, not CPU-bound. So a healthy warming
    # process looks silent AND idle and gets killed, which restarts warm-up from zero — the trap the 2026-10-19
    # comment in ez_indicators.py describes (38 kills in six hours, "cannot be escaped once entered"). Measured
    # consequence today: data/latest_market_data.json — the snapshot EVERY engine reads for entry AND exit
    # decisions — went unwritten from 00:59 to past 04:20 while every account traded on 3-hour-old indicators.
    # A genuinely dead process is still caught: tools/ez_indicators_warmup_guard.py only emits [WARMUP_HEARTBEAT]
    # while the pid is alive and making progress, so the log goes truly silent when it wedges.
    ez_indicators)       NO_OUTPUT_TIMEOUT=900  ;;  # ~1s in steady state; 900 so a warm-up pass cannot be killed
    ez_indicators_merger) NO_OUTPUT_TIMEOUT=120  ;;  # writes every ~3s
    ez_market_data)      NO_OUTPUT_TIMEOUT=180  ;;  # writes every ~30s
    ez_crosses)          NO_OUTPUT_TIMEOUT=300  ;;  # writes every ~3min (signal save cycle)
    ez_rankings)         NO_OUTPUT_TIMEOUT=1800 ;;  # 2026-10-19: 300 -> 900 -> 1800. 900 was still marginal: the
    # 23:04:54Z start was SIGKILLed at 23:19:58Z after 954s of quiet init (exit=137, "after 954s"), i.e. the quiet
    # bootstrap phase is LONGER than 900s on a loaded box. The next attempt (23:20:04Z) got through and has been
    # self-sustaining for 40+ min, because once ranking_loop starts it logs every few seconds — so the steady-state
    # bar is unaffected either way and only the one-off init needs the headroom. 1800s still catches a genuinely
    # dead process within half an hour. The proper fix is a progress heartbeat in the bootstrap path, same as the
    # [WARMUP] line now in ez_indicators._process_timeframe; until that exists this is the honest number.
    # Earlier note (the 300 -> 900 step): "init can be slow" is measured as slower than 300 s on a loaded box: the 22:40:12 start logged its last init line at 22:40:57 and was SIGKILLed at 22:57:05 for "No output for 305s" having never reached the task set, so the market-movement monitor (the ONLY writer of data/market_mode.json, i.e. of LIGHT/NORMAL/EXTREME) never ran. Same shape and same remedy as tradier_rankings below: a quiet synchronous phase needs headroom, not a tighter trigger. 900 s still catches a genuinely dead process, and once init completes the loop logs every ~4 s so the real steady-state bar is unchanged.
    ez_prices|ez_prices_ws) NO_OUTPUT_TIMEOUT=600 ;;  # writes every ~3min, bursts
    ez_klines)           NO_OUTPUT_TIMEOUT=600  ;;  # scheduled every 5min
    ez_manage)           NO_OUTPUT_TIMEOUT=600  ;;  # quick_general writes every ~30-300s
    ez_share_ind)        NO_OUTPUT_TIMEOUT=600  ;;  # heartbeat every few minutes
    ez_mark_prices)      NO_OUTPUT_TIMEOUT=600  ;;  # periodic
    ez_orderbook)        NO_OUTPUT_TIMEOUT=0    ;;  # 2026-04-27: disabled — real heartbeat is Redis (orderbook:* keys), not the log file. The 600s log-mtime check was killing healthy processes every ~10 min in a loop.
    tradier_rankings)    NO_OUTPUT_TIMEOUT=900  ;;  # 2026-07-21: the per-symbol scoring block (tradier_rankings.py ~2126-2216) is one synchronous pandas loop over 224 symbols that logged NOTHING. Measured 553s on 2026-06-13 with 219 symbols; at 224 it crossed the 600s default, so the watchdog SIGKILLed it 67x on 2026-07-21 alone and tradier_rankings.json has not been written since 2026-06-13 03:30. A progress heartbeat was added to the loop; 900s is belt-and-braces headroom, still catches a genuinely dead process.
    tradier_indicators)  NO_OUTPUT_TIMEOUT=600  ;;  # 2026-08-20: 163 syms * 1m/5m/15m/1h/4h/D with clipping takes 180-300s, give 600s headroom (was default 600 but tradier_manage 0 masked it)
    tradier_manage)      NO_OUTPUT_TIMEOUT=0    ;;  # 2026-06-03: disabled — same failure mode as ez_orderbook. The per-account tradier_manage_{acct}.log is hijack-unreliable (multi-proc RotatingFileHandler race freezes trb/tra's own log while the worker is alive + trading + writing shared logs), so the 600s log-mtime check kill-looped healthy trb/tra every ~10min. Liveness covered by binance_supervisor (proc-aware) + shared tradier_positions/actions logs.
    *)                   NO_OUTPUT_TIMEOUT=600  ;;  # safe default
esac

# Find the ACTUAL log file — check both locations (logs are in /Users/niels/logs/ on macbook)
PYTHON_LOG=""
for _candidate in "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log" "$WORKDIR/logs/${SCRIPT_BASE}${ACCT_SUFFIX}.log"; do
    if [[ -f "$_candidate" ]]; then
        PYTHON_LOG="$_candidate"
        break
    fi
done
# 2026-10-20 REMOVED: the ez_manage quick_general heartbeat override.
# It pointed the no-output check at ez_positions_quick_general_<acct>.log, which ez_manage DOES NOT WRITE — that
# file belongs to ez_positions_quick.py (:368/:406), a different process, and since the single-writer change
# ez_manage never touches positions at all. `lsof ~/logs/ez_manage_<acct>.log` shows exactly one writer: the child
# itself. So the engine was being judged on another process's silence, and 9 of the 16 no-output kills in the
# 24 h to 2026-10-20 17:07Z were provably wrong — in 5 of them the child had written its OWN log in the very
# second it was SIGKILLed, e.g. "No output for 4389s" charged to a 123-second-old process (2026-10-20 13:51:48,
# ang). Each kill then surfaced as exit 137 and was relabelled `[SIGKILL_EXTERNAL] … external kill, NOT memory
# pressure`, so the watchdog blamed an outside agent for a child it had killed itself two seconds earlier.
# The engine's own log is its only correct, exclusive heartbeat.
# If no log found yet, set expected path so watchdog can detect when it appears
if [[ -z "$PYTHON_LOG" ]]; then
    PYTHON_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log"
fi

RESTART_TRACKER="$LOGDIR/.${SCRIPT_BASE}${ACCT_SUFFIX}_restarts"

# Read macOS pages-free as KB. On non-Darwin returns a large value (always healthy).
# 2026-05-01: used by SIGKILL_BACKOFF to decide whether the host has actually recovered
# enough memory to respawn an ez_manage worker without triggering another jetsam kill.
mac_pages_free_kb() {
    if [[ "$(uname)" != "Darwin" ]]; then echo 999999; return; fi
    local pages
    pages=$(vm_stat 2>/dev/null | awk '/Pages free/ {gsub(/\./, "", $3); print $3; exit}')
    [[ -z "$pages" || ! "$pages" =~ ^[0-9]+$ ]] && { echo 999999; return; }
    echo $((pages * 16))
}

# Read macOS jetsam pressure level. Returns: 1=normal, 2=warning, 4=critical, 8=sustained-critical.
# 2026-05-01: pages_free is a misleading metric on Mac — it stays at 60-80MB
# constantly because macOS uses RAM as cache. The true "is jetsam about to fire"
# signal is kern.memorystatus_vm_pressure_level. Workers should respawn when this is
# ≤ 2 (normal or warning); avoid spawning when it is ≥ 4 (critical).
mac_pressure_level() {
    if [[ "$(uname)" != "Darwin" ]]; then echo 1; return; fi
    local lvl
    lvl=$(/usr/sbin/sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null)
    [[ -z "$lvl" || ! "$lvl" =~ ^[0-9]+$ ]] && { echo 1; return; }
    echo "$lvl"
}

# Logging function (with 5MB rotation)
WD_LOG_MAX=$((5 * 1024 * 1024))
log() {
    if [[ -f "$WD_LOG" ]]; then
        local sz
        sz=$(wc -c < "$WD_LOG" 2>/dev/null || echo 0)
        if (( sz > WD_LOG_MAX )); then
            mv -f "$WD_LOG" "${WD_LOG}.1" 2>/dev/null || true
        fi
    fi
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$WD_LOG"
}

# Function to check rapid restarts
check_rapid_restarts() {
    local now=$(date +%s)
    local count=0
    
    # Create restart tracker if it doesn't exist
    touch "$RESTART_TRACKER"
    
    # Remove old entries (older than rapid window)
    local cutoff=$((now - RAPID_WINDOW))
    awk -v cutoff="$cutoff" '$1 >= cutoff' "$RESTART_TRACKER" > "${RESTART_TRACKER}.tmp"
    mv "${RESTART_TRACKER}.tmp" "$RESTART_TRACKER"
    
    # Count recent restarts
    count=$(wc -l < "$RESTART_TRACKER" | tr -d ' ')
    
    # Add current restart
    echo "$now" >> "$RESTART_TRACKER"
    
    if [ "$count" -ge "$MAX_RAPID_RESTARTS" ]; then
        # 2026-10-22 (USER: "can you ABSOLUTELY GUARANTEE no account can stop trading EVER when there is any open
        # position?"): this exit is the single most destructive refusal in the supervisor, because `exit 1` fires
        # the EXIT trap -> cleanup() -> `pkill -P $$`, so the watchdog does not merely stop replacing the engine,
        # it KILLS the running one and leaves the account with nothing. Measured: ang and inf both logged
        # "Watchdog shutting down..." at 06:42:34Z today and their engines died with them (recovered ~30 s later
        # only by the keepalive's separate "NO ez_manage watchdog -> start" path), and inf has taken this exit 15
        # times, once at 29 restarts in 300 s. Giving up is defensible for a flat account — a true crash loop
        # should stop and be fixed by a human. It is never defensible while the account holds exposure: a crash
        # loop still gets the engine up for part of every cycle, and zero engine gets nothing. So an account with
        # an open position keeps its supervisor, the counter is reset so the next window is judged fresh, and the
        # loop is slowed to SIGKILL_BACKOFF-like spacing instead of hammering. Flat accounts behave exactly as
        # before. account_holds_risk exits 0 on holds-risk and on ANY unreadable/missing positions file.
        local _hr_py="${WD_HOLDS_RISK_PY:-/opt/anaconda3/envs/binance_env/bin/python}"
        local _acct_bare="${ACCT_SUFFIX#_}"
        if [[ -n "$_acct_bare" && -f "$WORKDIR/tools/account_holds_risk.py" ]] \
           && "$_hr_py" "$WORKDIR/tools/account_holds_risk.py" --account "$_acct_bare" --quiet 2>/dev/null; then
            log "⚠️ [RAPID_RESTART_OVERRIDE] $count restarts in ${RAPID_WINDOW}s would normally end this watchdog, but ${_acct_bare} HOLDS AN OPEN POSITION ($("$_hr_py" "$WORKDIR/tools/account_holds_risk.py" --account "$_acct_bare" 2>/dev/null | sed 's/^[a-z]*: //')) — a crash loop trades part of the time, no supervisor trades none of the time. Keeping the supervisor, resetting the window, backing off ${WD_RAPID_OVERRIDE_SLEEP_S:-60}s."
            : > "$RESTART_TRACKER"
            echo "$now" >> "$RESTART_TRACKER"
            sleep "${WD_RAPID_OVERRIDE_SLEEP_S:-60}"
            return 0
        fi
        log "ERROR: Too many rapid restarts ($count in ${RAPID_WINDOW}s). Exiting to prevent crash loop."
        exit 1
    fi
    # 2026-10-06 USER: churn alert marker (10 silent waves/90min went unnoticed) — alert BEFORE the 15x kill-switch trips.
    if [ "$count" -ge 6 ]; then
        echo "$(date +'%Y-%m-%d %H:%M:%S') $count restarts in ${RAPID_WINDOW}s" > "$LOGDIR/.restart_alert_${SCRIPT_BASE}${ACCT_SUFFIX}"
        log "ALERT: restart churn ($count in ${RAPID_WINDOW}s) — marker $LOGDIR/.restart_alert_${SCRIPT_BASE}${ACCT_SUFFIX}"
    fi
    
    # Check minimum interval since last restart
    local last_restart=$(tail -n 2 "$RESTART_TRACKER" | head -n 1)
    if [ -n "$last_restart" ]; then
        local interval=$((now - last_restart))
        if [ "$interval" -lt "$MIN_RESTART_INTERVAL" ]; then
            log "Waiting $((MIN_RESTART_INTERVAL - interval))s before restart (min interval: ${MIN_RESTART_INTERVAL}s)"
            sleep $((MIN_RESTART_INTERVAL - interval))
        fi
    fi
}

# FENCE AWARENESS (USER 2026-10-15 live takeover test). When S1 holds the lease it runs these accounts and this host
# must not. Nothing told the watchdogs that: during the 19:27-21:47Z S1 lease these respawned ez_manage continuously
# while mac_failover_agent SIGTERMed it on every 15 s tick ("FENCE: SIGTERM 6 gated trader pids" at 20:19, 20:20,
# 20:25, 20:32...). Besides being a pointless fight, each respawn gave ez_manage a live window to place an order on an
# account S1 was already trading. A fenced host simply waits.
# 2026-10-20 ACCOUNT SCOPE REACHES THIS GATE TOO. There were TWO fence checks: this one (venue-only) and the
# account-aware lf_lease_fenced() at ~line 584. The 2026-10-19 account-scope change ("if any account fails it should
# be taken over by s1 ... the Mac keeps trading their siblings") updated only lf_lease_fenced; this copy kept reading
# the "venues" field alone. run_script() calls THIS one first, so the blind copy decided everything.
# MEASURED 2026-10-20 03:39-03:47Z: S1 took the lease for ONE dead account — lease.json
# accounts={"crypto":["flz"]}, reason "account_dead 1/5 (flz) ... local repair failed for 48s" — and all FIVE Mac
# wrappers then logged "[FENCED] S1 holds the lease for ez_manage.py" every 15 s. S1 ran flz, the Mac ran nothing, so
# fin/men/inf/ang traded on NEITHER host. A one-account takeover silently became a five-account outage.
# Delegating to lf_lease_fenced (tools/live_failover_lease_gate.sh) keeps ONE implementation of the lease semantics —
# scope, ghost-lease staleness and the LF_FAKE_NOW test clock included — instead of a second copy that drifts.
# Fail-safe preserved: if the gate cannot be sourced, fall back to the venue-only read (never spawn into doubt).
_lease_fenced() {
    case "$SCRIPT" in
        ez_manage.py|tradier_manage.py|ez_positions_watchdog.py|ez_positions.py|ez_copilot.py|ez_reentry_daemon.py|tradier_positions.py) ;;
        *) return 1 ;;
    esac
    if [[ -f "$WORKDIR/tools/live_failover_lease_gate.sh" ]]; then
        source "$WORKDIR/tools/live_failover_lease_gate.sh"
        lf_lease_fenced "$SCRIPT" "$WORKDIR" "${ACCT_SUFFIX#_}"
        return $?
    fi
    local lease="$WORKDIR/data/live_failover/lease_local.json"
    [ -f "$lease" ] || return 1
    case "$SCRIPT" in
        tradier_*) local venue="stocks" ;;
        *) local venue="crypto" ;;
    esac
    /usr/bin/python3 - "$lease" "$venue" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
venues = str(d.get("venues") or "")
sys.exit(0 if (d.get("holder") == "s1" and (not venues or sys.argv[2] in venues)) else 1)
PYEOF
}

# Function to run the script
run_script() {
    local start_time=$(date +%s)
    while _lease_fenced; do
        log "[FENCED] S1 holds the lease for $SCRIPT — not starting on this host; re-checking in 15s"
        sleep 15
    done
    log "Starting $SCRIPT ${ARGS[*]}"

    # Scripts using Python logging write to their own log file — stdout to /dev/null
    # Exception: ez_klines uses print() so redirect stdout to its monitored log file
    # 2026-07-21: stderr was going to /dev/null, so every "Script exited with code: 1"
    # was undiagnosable (tradier_indicators exited 1 twice at 19:18/19:20 with zero trace).
    # Capture stderr to a SEPARATE file (never PYTHON_LOG — that would feed the
    # no-output heartbeat check and mask a dead process).
    local stderr_log="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_stderr.log"
    if [[ -f "$stderr_log" ]] && (( $(wc -c < "$stderr_log" 2>/dev/null || echo 0) > 20971520 )); then
        mv -f "$stderr_log" "${stderr_log}.1" 2>/dev/null || true
    fi
    if [[ "$SCRIPT_BASE" == "ez_klines" ]]; then
        "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" >> "$PYTHON_LOG" 2>> "$stderr_log" &
    else
        "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" > /dev/null 2>> "$stderr_log" &
    fi
    local script_pid=$!
    CHILD_PID=$script_pid
    local rss_over_count=0
    # NO-OUTPUT CPU GATE state (see the gate below). Empty baseline = the log is not currently over its limit.
    local _sil_cpu0="" _sil_base_t=0

    # Monitor the script
    while kill -0 "$script_pid" 2>/dev/null; do
        sleep 5
        
        local now=$(date +%s)
        local runtime=$((now - start_time))
        
        # Check timeout
        if [ "$TIMEOUT" -gt 0 ] && [ "$runtime" -gt "$TIMEOUT" ]; then
            log "Timeout reached (${TIMEOUT}s). Restarting..."
            kill -TERM "$script_pid" 2>/dev/null || true
            sleep 2
            kill -KILL "$script_pid" 2>/dev/null || true
            break
        fi
        
        # Check for output timeout — re-resolve log file each cycle in case it appears later
        local _check_log="$PYTHON_LOG"
        if [[ ! -f "$_check_log" ]]; then
            # Try alternate locations
            for _try in "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log" "$WORKDIR/logs/${SCRIPT_BASE}${ACCT_SUFFIX}.log"; do
                if [[ -f "$_try" ]]; then _check_log="$_try"; break; fi
            done
        fi
        # ARMING GUARD was 120 s, which is SHORTER THAN STARTUP. Measured over 36 ez_manage launches: `Starting` ->
        # first trading-loop line is 10-255 s, median ~140 s (outliers 797/859/887 s). Six kills fired at run_age
        # 108/115/116/120/121/123 s — on the FIRST check after arming, every time. Silence must only ever be
        # measured over a window the child actually owned, so arm at NO_OUTPUT_TIMEOUT instead.
        local _arm_after="$NO_OUTPUT_TIMEOUT"
        [ "$_arm_after" -lt 120 ] && _arm_after=120
        if [[ "$SCRIPT" != "ez_vpn.py" && "$SCRIPT" != "ez_double.py" && -f "$_check_log" && "$runtime" -gt "$_arm_after" ]]; then
            local last_mod
            if [[ "$(uname)" == "Darwin" ]]; then
                last_mod=$(stat -f "%m" "$_check_log" 2>/dev/null || echo 0)
            else
                last_mod=$(stat -c "%Y" "$_check_log" 2>/dev/null || echo 0)
            fi
            # CLAMP THE AGE TO THIS RUN. The raw mtime age is inherited from BEFORE the child was born, so the first
            # launch after any quiet period is killed for someone else's silence: tradier_rankings 2026-10-20
            # 13:36:07 "No output for 63302s (limit: 900s). Restarting..." — 63302 s IS the overnight market-closed
            # gap, charged to a 2-minute-old process. A child can only be held responsible for the time it has run.
            [ "$last_mod" -lt "$start_time" ] && last_mod="$start_time"
            local no_output_time=$((now - last_mod))
            if [ "$NO_OUTPUT_TIMEOUT" -gt 0 ] && [ "$no_output_time" -gt "$NO_OUTPUT_TIMEOUT" ]; then
                # USER 2026-10-20: "make sure the watchdogs do not intervene innecessarily ... every restart
                # destabilizes the system". Log mtime is NOT liveness. This single check has kill-looped healthy
                # processes at least three times already, each time "fixed" by widening or disabling the limit for
                # one script: ez_orderbook -> 0 (killed every ~10 min), tradier_manage -> 0 (RotatingFileHandler
                # race froze trb/tra's own log while they were alive and trading), tradier_rankings 600 -> 900
                # (SIGKILLed 67x in one day inside a silent pandas loop, so tradier_rankings.json went unwritten
                # for 5 WEEKS). Every one of those was the same bug: a process doing work while writing nothing.
                # Per-script constants cannot fix that, because the safe limit depends on the workload of the hour.
                # So ask the process instead: has it burned CPU since the log went quiet? CPU advancing proves it
                # is NOT wedged, whatever its log says, and a wedged process (deadlock, dead socket wait, spin in
                # uninterruptible sleep) shows no progress and is still killed exactly as before. A genuinely busy
                # but silent process is now left alone up to a hard ceiling, so nothing can be shielded forever.
                local _cpu_now; _cpu_now=$(_cpu_secs "$script_pid")
                local _hard=$((NO_OUTPUT_TIMEOUT * NO_OUTPUT_HARD_MULT))
                if [ -z "$_sil_cpu0" ]; then _sil_cpu0="$_cpu_now"; _sil_base_t="$now"; fi
                local _gain=$((_cpu_now - _sil_cpu0))
                local _waited=$((now - _sil_base_t))
                if [ "$no_output_time" -ge "$_hard" ]; then
                    log "⚠️ No output for ${no_output_time}s (limit: ${NO_OUTPUT_TIMEOUT}s, HARD ceiling ${_hard}s) on ${_check_log} — CPU gain ${_gain}s is not enough to keep shielding it. Restarting..."
                elif [ "$_gain" -ge "$NO_OUTPUT_CPU_MIN_S" ]; then
                    log "[NO_OUTPUT_BUT_WORKING] ${_check_log} silent ${no_output_time}s (limit ${NO_OUTPUT_TIMEOUT}s) but the process burned ${_gain}s CPU in the last ${_waited}s — NOT restarting (hard ceiling ${_hard}s)"
                    _sil_cpu0="$_cpu_now"; _sil_base_t="$now"
                    continue
                elif [ "$_waited" -lt "$NO_OUTPUT_CPU_WINDOW_S" ]; then
                    continue    # still inside the observation window — decide once we have really watched it
                else
                    log "⚠️ No output for ${no_output_time}s (limit: ${NO_OUTPUT_TIMEOUT}s) on ${_check_log} AND only ${_gain}s CPU in ${_waited}s — wedged. Restarting..."
                fi
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 2
                kill -KILL "$script_pid" 2>/dev/null || true
                break
            fi
            _sil_cpu0=""    # log is fresh again — drop the baseline
        fi
        # 2026-04-26: Preemptive RSS recycling for ez_manage workers — beat jetsam to the punch
        # with a graceful TERM so positions/locks save cleanly. Mac jetsam SIGKILLs at ~1GB+ when
        # whole-system memory pressure is high, losing in-flight state. Recycle at 900MB → 1.5GB
        # 2026-04-27: bumped 900MB → 1500MB. Live audit: inf was being recycled every 3-5 min
        # (RSS hit 900MB fast), causing constant cutouts user complained about. System has 36GB
        # RAM, 5 ez_manage workers ≈ 3.5GB total; 1.5GB ceiling per worker is plenty of headroom.
        # Apply to ez_orderbook too (long-running OB ingest, similar pattern).
        # 2026-04-30: per-account ceiling — inf grew to 91 open positions; natural working set
        # ~1.75GB (200MB base + 91 × ~17MB klines+indicators+WT state per position). Was hitting
        # 1.5GB → SIGTERM→SIGKILL → exit 137 every 3 min (9× in 30 min). This is workload
        # scaling, NOT a leak: men=50pos@880MB, fin=24pos@685MB, ang=12pos@474MB all stable
        # under 1.5GB; only inf's 91-position load exceeds. 36GB RAM means even 5×2.2GB=11GB is
        # comfortable. Default unchanged at 1.5GB; inf gets 2.2GB.
        if [[ ( "$SCRIPT" == "ez_manage.py" || "$SCRIPT" == "ez_orderbook.py" ) && "$runtime" -gt 60 ]]; then
            local rss_kb
            if [[ "$(uname)" == "Darwin" ]]; then
                rss_kb=$(ps -p "$script_pid" -o rss= 2>/dev/null | tr -d ' ' || echo 0)
            else
                rss_kb=$(awk '/VmRSS/{print $2}' "/proc/$script_pid/status" 2>/dev/null || echo 0)
            fi
            local MAX_RSS_KB=1572864  # 1.5GB default (was 900MB)
            if [[ "$SCRIPT" == "ez_manage.py" ]]; then
                # 2026-05-19 OOM_CYCLE: men cycling 137 every 7-37min, ang/fin trending toward
                # ceiling. Observed RSS peaks at preemptive recycle: men 1.6-2.18GB, ang 1.8GB,
                # fin 1.7-1.77GB. The 2026-04-30 baseline (men=50pos@880MB) no longer holds —
                # working set has grown (new R1/R2/R3 state, per_sym overlay, HH/HL/LL framework,
                # STDEV_MACRO). flz with 1 open position remains stable at 469-643MB so this
                # is genuinely workload-scaled, not a leak: same evidence pattern that justified
                # inf's bump to 2.2GB. Apply same ceiling to men/ang/fin.
                case "${ARGS[*]}" in
                    *"--account inf"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-04-30 original)
                    *"--account men"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                    *"--account ang"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                    *"--account fin"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                esac
                # 2026-05-19: file-based override so future ceiling tweaks don't require
                # restarting the bash watchdog. Lets ops bump a single account without
                # rebooting all 5 ez_manage tty sessions. Empty/missing file = use case
                # default above. Path: data/.watchdog_max_rss_kb_<acct>
                local _override_acct=""
                for _arg in "${ARGS[@]}"; do
                    if [[ "$_arg" =~ ^(ang|inf|flz|men|fin)$ ]]; then
                        _override_acct="$_arg"
                        break
                    fi
                done
                if [[ -n "$_override_acct" ]]; then
                    local _override_file="$WORKDIR/data/.watchdog_max_rss_kb_${_override_acct}"
                    if [[ -f "$_override_file" ]]; then
                        local _override_val
                        _override_val=$(cat "$_override_file" 2>/dev/null | tr -d ' \n\r' || echo "")
                        if [[ "$_override_val" =~ ^[0-9]+$ ]] && [[ "$_override_val" -ge 524288 ]]; then
                            MAX_RSS_KB="$_override_val"
                        fi
                    fi
                fi
            fi
            # 2026-05-21 OOM_CYCLE: 4-sample (20s) confirmation lets RSS spike from
            # ceiling (2.2GB) → 3.0GB before SIGTERM fires (men 20:29:04 hit 3102992KB).
            # When RSS is already ≥130% of ceiling, that IS sustained — confirmation
            # adds risk, not signal. Recycle IMMEDIATELY at >130% so the 30s graceful
            # SIGTERM window starts before jetsam picks us off (jetsam picks the
            # largest RSS regardless of our ceiling; losing the race = exit 137 and
            # in-flight state loss). Confirmation tier still applies for the
            # 100–130% band (slow drift, may be transient).
            local _hard_ceiling=$((MAX_RSS_KB * 130 / 100))
            if [[ -n "$rss_kb" && "$rss_kb" -gt "$_hard_ceiling" ]]; then
                log "🚨 RSS hard overflow: ${rss_kb}KB > 130% of ${MAX_RSS_KB}KB (${_hard_ceiling}KB). Immediate graceful recycle — skipping confirmation window to beat jetsam."
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 30
                kill -KILL "$script_pid" 2>/dev/null || true
                break
            fi
            if [[ -n "$rss_kb" && "$rss_kb" -gt "$MAX_RSS_KB" ]]; then
                rss_over_count=$((rss_over_count + 1))
                if [[ "$rss_over_count" -ge 4 ]]; then
                    log "🧹 RSS preemptive recycle: ${rss_kb}KB > ${MAX_RSS_KB}KB sustained for ${rss_over_count} samples (~$((rss_over_count * 5))s). Graceful restart before jetsam fires."
                    # 2026-05-14: SIGTERM grace bumped 5s→30s. Live evidence: every "exit 137"
                    # in the OOM_CYCLE detector was preceded by THIS log line ~5s earlier — i.e.
                    # the watchdog's own SIGKILL was firing because ez_manage's shutdown handler
                    # (signal_handler at ez_manage.py:46004 → shutdown_event.set() → background
                    # tasks unwind, position state flushes to JSON/Redis) takes longer than 5s.
                    # 30s lets the graceful path actually complete → exit 143 (clean) instead of
                    # 137 (kill). The ang log on 06:06:43 showed "SHUTDOWN & CLEANUP SEQUENCE
                    # INITIATED" with positions still being processed 3s later — SIGKILL came
                    # mid-flush. Workers fin (4h42m, 258MB) + flz (4h41m, 516MB) are long-term
                    # stable, so this isn't a runaway leak — it's workload-scaled RSS pressure
                    # on the larger accounts (inf 91pos, men 50pos, ang ~70pos) hitting the
                    # 1.5/2.2GB ceiling and the watchdog killing too aggressively.
                    kill -TERM "$script_pid" 2>/dev/null || true
                    sleep 30
                    kill -KILL "$script_pid" 2>/dev/null || true
                    break
                fi
            else
                rss_over_count=0
            fi
            # 2026-05-09: Pressure-aware evacuation. Per-process RSS ceilings only catch
            # leaks/scaling. Whole-system jetsam waves (e.g. 5 workers SIGKILLed simultaneously
            # 2026-05-09 06:07) hit workers BELOW their per-process ceiling because
            # jetsam picks the largest RSS regardless. Reading kern.memorystatus_vm_pressure_level
            # (≥4 = critical) and self-TERMing the largest workers BEFORE jetsam fires lets
            # them save state cleanly. Threshold 70% of MAX_RSS_KB so only well-loaded workers
            # self-evict — small workers stay alive, system pressure drops, jetsam stands down.
            local _pl=$(mac_pressure_level)
            if [[ -n "$rss_kb" && "$_pl" -ge 4 && "$rss_kb" -gt $((MAX_RSS_KB * 7 / 10)) ]]; then
                log "🚨 PRESSURE_EVAC: pressure_level=${_pl} (≥4 critical) rss=${rss_kb}KB > 70% of ${MAX_RSS_KB}KB. Graceful evac before jetsam SIGKILLs us."
                # 2026-05-14: 5s→30s grace for the same reason as preemptive recycle above.
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 30
                kill -KILL "$script_pid" 2>/dev/null || true
                break
            fi
        fi
    done
    
    # Wait for process to exit
    wait "$script_pid" 2>/dev/null
    local exit_code=$?
    local _sig=""
    if [[ "$exit_code" -gt 128 ]]; then _sig=" (signal $((exit_code - 128)): $(kill -l $((exit_code - 128)) 2>/dev/null || echo ?))"; fi
    log "Script exited with code: $exit_code$_sig"
    # 2026-10-06 USER: capture WHY — tail of the child's stderr into the watchdog log (was undiagnosable).
    local _et
    _et=$(tail -n 15 "$stderr_log" 2>/dev/null | tr '\n' '|' | cut -c1-900)
    if [[ -n "$_et" ]]; then log "[EXIT_REASON] ${_et}"; fi
    return $exit_code
}

# Main watchdog loop
main() {
    log "=== WATCHDOG STARTED ==="
    log "Script: $SCRIPT"
    log "Args: ${ARGS[*]}"
    log "Timeout: ${TIMEOUT}s (0=disabled)"
    log "No-output timeout: ${NO_OUTPUT_TIMEOUT}s"
    log "Monitoring log: ${PYTHON_LOG}"
    log "Parent: pid=$PPID cmd=$(ps -o command= -p $PPID 2>/dev/null | cut -c1-120)"
    
    # Verify script exists
    if [ ! -f "$WORKDIR/$SCRIPT" ]; then
        log "ERROR: Script not found: $WORKDIR/$SCRIPT"
        exit 1
    fi
    
    # Main restart loop
    # 2026-05-01: SIGKILL-aware backoff. When the script exits with code 137 (SIGKILL,
    # typically Mac jetsam killing it under whole-system memory pressure), respawning
    # in 60s allocates the working set again and triggers another kill — a thrash loop
    # that just consumes CPU + adds memory pressure without recovering. Each consecutive
    # 137 doubles the wait (60→120→240→480→600 cap). Any clean / non-137 exit resets the
    # counter. Applied only to ez_manage workers (where this pattern was observed).
    # 2026-05-19 OOM_CYCLE: also reset when the last run lasted ≥300s. A 5-min stable run
    # before a single 137 = intermittent jetsam wave, NOT a thrash loop. Without this, men
    # got stuck at 600s backoff for hours because every restart eventually OOMed even after
    # 30+ min uptime, and the counter never reset. Distinguishes "ceiling exceeded after
    # legitimate work" from "thrash loop where every restart OOMs in <60s".
    local consecutive_sigkill=0
    local consecutive_fail=0
    local _run_started_at=0
    while true; do
        # 2026-10-20 check_rapid_restarts MOVED to just before run_script (was here, at the top of the loop).
        # Every gate below — the lease fence, FAILOVER_IDLE, PRESSURE_GATE — `continue`s the loop WITHOUT launching
        # anything, and each of those iterations was writing a restart into $RESTART_TRACKER. A fenced wrapper
        # re-checks every 30 s, so a host that spawned nothing at all still accumulated restarts at 10 per 300 s:
        # measured 1557 "ALERT: restart churn" lines against 152 real launches in the 24 h to 2026-10-20 17:07Z.
        # That made the churn marker meaningless, and worse, it is one cadence change away from reaching
        # MAX_RAPID_RESTARTS=15, whose branch EXITS the wrapper entirely ("Too many rapid restarts ... Exiting to
        # prevent crash loop") — i.e. a long fence could permanently retire the supervisor of a live account.
        # Counting only actual launches is the whole fix.

        # ═══ LIVE FAILOVER LEASE GATE (2026-10-13 USER, Mac only, applies even with TRADIER_LOCAL_ONLY=1) ═══
        # S1 holds the live-trading lease -> the Mac never spawns a gated trader (tools/live_failover_lease_gate.sh).
        # 2026-10-14: also on S1's live dir (has data/live_failover/): ez_copilot's wrapper kept spawning tradier_positions.py
        # on S1 WITHOUT the lease, so S1's own stock files (not the Mac's seed) decided opened_at at takeover.
        # 2026-10-16 FENCED WATCHDOG MUST NOT KILL (proven, trb 2026-10-16): kill_duplicate_python ran BEFORE this gate,
        # so a fenced wrapper SIGTERMed every live child it refused to replace — once per 30 s loop. tradier_positions.py
        # (the only writer of stock position truth) therefore ran 13:30-13:50, 14:03-14:12, 14:37-14:52 and then NOT AT ALL
        # until 19:51 (665 LEASE_FENCED iterations, 9 restarts/300 s churn, "[SINGLETON] Killing duplicate ... tradier_positions.py"
        # at 14:02/15:32/16:12/16:26/18:18/19:18), while S1 — which does not run stocks at all — held the lease. A fenced
        # wrapper now does NOTHING: no duplicate reaping, no spawn. Only a wrapper that is allowed to RUN the venue may kill for it.
        if [[ -f "$WORKDIR/tools/live_failover_lease_gate.sh" ]] && [[ "$(uname)" == "Darwin" || -d "$WORKDIR/data/live_failover" ]]; then
            source "$WORKDIR/tools/live_failover_lease_gate.sh"
            # 2026-10-19 ACCOUNT SCOPE: pass this wrapper's account so a sibling fenced on the other
            # machine does not fence this one (ACCT_SUFFIX parsed at line ~151 from --account/--accounts).
            if lf_lease_fenced "$SCRIPT" "$WORKDIR" "${ACCT_SUFFIX#_}"; then log "[LEASE_FENCED] the other machine holds the live-trading lease for this venue (data/live_failover/lease_local.json on the Mac, lease.json run_venues on S1) — NOT spawning $SCRIPT on $(uname), and NOT killing any running instance. Retrying in 30s..."; sleep 30; continue; fi
        fi
        # Kill any duplicate python instances before launching (only reached when this wrapper may actually run the venue)
        kill_duplicate_python
        # ═══ S1 FAILOVER GATE (MacBook only) ═══
        # Local-only launches must never SSH to S1.  start_everything_3.command
        # and local cron recovery export TRADIER_LOCAL_ONLY=1; the legacy
        # START_EVERYTHING_3_LOCAL_ONLY flag is kept for compatibility.
        if [[ "$(uname)" == "Darwin" && "${TRADIER_LOCAL_ONLY:-0}" != "1" && "${START_EVERYTHING_3_LOCAL_ONLY:-0}" != "1" ]]; then
            local is_gated=0
            # 2026-07-02: ONLY gate order-placing / authoritative-position-state procs across machines.
            # Read-only DATA feeds (ez_prices/klines/mark_prices/indicators/market_data/orderbook/rankings/
            # tradier_prices/indicators/rankings) each write their OWN machine-local Redis, so gating them on
            # the Mac (live-trading box) while S1 also runs them STARVED the Mac's local feeds → stale klines.
            # They must run per-machine; double-run is harmless (separate Redis, separate IP rate-limit pools).
            case "$SCRIPT" in
                ez_manage.py|tradier_manage.py|ez_positions_watchdog.py|ez_positions.py|ez_copilot.py|ez_reentry_daemon.py|tradier_positions.py) is_gated=1 ;;
            esac
            if [[ "$is_gated" -eq 1 ]]; then
                # USER 2026-10-07: anchor to the python WORKER (was: matched the S1 watchdog wrapper itself -> false FAILOVER_IDLE, men unmanaged 00:01-01:14Z). Bracket-trick (bash-3.2-safe ${arg#${arg%?}}) so the ssh probe never self-matches. No -u requirement: s1_failover_start.sh launches without it.
                local s1_pattern="python.*$SCRIPT"
                for arg in "${ARGS[@]}"; do
                    if [[ "$arg" =~ ^(ang|inf|flz|men|fin|tra|trb|trc)$ ]]; then local a_last="${arg#${arg%?}}"; s1_pattern="python.*$SCRIPT.*${arg%?}[$a_last]"; break; fi
                done
                if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no s1-int "pgrep -f \"$s1_pattern\"" >/dev/null 2>&1; then
                    log "[FAILOVER_IDLE] S1 is running $SCRIPT (pattern: $s1_pattern) — MacBook will NOT spawn. Retrying in 60s..."
                    sleep 60
                    continue
                fi
            fi
        fi

        # Change to working directory
        cd "$WORKDIR" || {
            log "ERROR: Cannot change to $WORKDIR"
            exit 1
        }

        # 2026-10-06 USER: never launch into critical memory pressure (jetsam would SIGKILL
        # the spawn immediately). Bounded 300s wait, then launch anyway — never deadlock trading.
        local _pg_wait=0
        while [[ "$(mac_pressure_level)" -ge 4 && "$_pg_wait" -lt 300 ]]; do
            if [[ "$((_pg_wait % 60))" -eq 0 ]]; then log "[PRESSURE_GATE] level≥4 critical — delaying launch 60s (${_pg_wait}s/300s)"; fi
            sleep 60
            _pg_wait=$((_pg_wait + 60))
        done
        # Pick up our own fixes before launching. Between runs the child is dead, so an exec here is free: the pid
        # is preserved, which keeps $WATCHDOG_LOCK and the [WATCHDOG_SINGLETON] guard valid, and no live process is
        # touched. Without this, a watchdog edit only reaches a wrapper when something else happens to restart it.
        local _now_mtime
        if [[ "$(uname)" == "Darwin" ]]; then
            _now_mtime=$(stat -f "%m" "${WD_ARGV[0]}" 2>/dev/null || echo 0)
        else
            _now_mtime=$(stat -c "%Y" "${WD_ARGV[0]}" 2>/dev/null || echo 0)
        fi
        if [[ "$_now_mtime" != "0" && "$_now_mtime" != "$WD_SELF_MTIME" ]] && bash -n "${WD_ARGV[0]}" 2>/dev/null; then
            log "[WD_SELF_REEXEC] $(basename "${WD_ARGV[0]}") changed on disk (mtime $WD_SELF_MTIME -> $_now_mtime) and parses clean — re-exec'ing to load it (pid $$ preserved, no child running)"
            exec /bin/bash "${WD_ARGV[@]}"
        fi

        # Only NOW does a launch actually happen, so only now may it be counted (see the note at the loop head).
        check_rapid_restarts

        # Run the script
        _run_started_at=$(date +%s)
        run_script
        local _last_exit=$?
        local _run_duration=$(( $(date +%s) - _run_started_at ))

        if [[ "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 && "$_run_duration" -ge 300 ]]; then
            if [[ "$consecutive_sigkill" -gt 0 ]]; then
                log "[SIGKILL_BACKOFF] last run lasted ${_run_duration}s (≥300s stable) — resetting counter (was $consecutive_sigkill)"
            fi
            consecutive_sigkill=0
        fi

        # 2026-10-16 USER ("NO account can be inactive for >3min ... NEVER IS THERE ANY POSSIBILITY THAT it just stops
        # trading"): exit 137 is NOT proof of jetsam. An operator `kill -9`, a rolling restart, or any external SIGKILL
        # lands in the branch below and was converted into a DELIBERATE 60-660s halt that nothing could fill — the
        # watchdog holds the singleton lock while it sleeps, so crypto_watchdog_cron.sh (wrapper alive = healthy) and
        # the keepalive (log age < 300s) both stand down. PROVEN 2026-10-16: ang/inf/flz/fin SIGKILLed 04:08:11-13Z,
        # each logged "consecutive 137 #1 — sleeping 60s", relaunched only 04:09:18-19Z (~65-68s down), and the very
        # next line recorded pressure_level=1 free=56-62MB — i.e. there was never any memory pressure to back off from.
        # The jetsam signal is the PRESSURE LEVEL, not the exit code. When the host is not tight the kill came from
        # outside and the only correct action is to relaunch at once. The OOM thrash protection below is untouched and
        # still applies in full whenever the host really is under pressure.
        # Darwin-only: mac_pressure_level() returns a constant 1 off Darwin, which would turn EVERY 137 into an
        # immediate relaunch on S1/Linux. S1's behaviour is left exactly as it was.
        _lvl_137=9
        if [[ "$(uname)" == "Darwin" && "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 ]]; then _lvl_137=$(mac_pressure_level); fi
        if [[ "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 && "$_lvl_137" -le 2 ]]; then
            log "[SIGKILL_EXTERNAL] exit 137 after ${_run_duration}s with pressure_level=${_lvl_137} (≤2 = not jetsam) free=$(mac_pages_free_kb)KB — external kill, NOT memory pressure: relaunching immediately, no backoff"
            consecutive_sigkill=0
            consecutive_fail=0
            sleep 1
            continue
        fi

        if [[ "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 ]]; then
            consecutive_sigkill=$((consecutive_sigkill + 1))
            local _backoff=$((60 * (1 << (consecutive_sigkill - 1))))
            if [[ "$_backoff" -gt 600 ]]; then _backoff=600; fi
            log "[SIGKILL_BACKOFF] consecutive 137 #$consecutive_sigkill — sleeping ${_backoff}s before next launch (system memory pressure)"
            sleep "$_backoff"
            # 2026-05-01: pressure-aware extension. Fixed sleep doesn't guarantee memory
            # is actually free. A fresh ez_manage spawn peaks ~600MB during state-load —
            # if the host is in jetsam-critical pressure, that allocation re-trips jetsam
            # and the kill loop continues.
            # Updated 2026-05-01 22:41: switched from pages_free (Mac always reports
            # 60-80MB free because of cache), to kern.memorystatus_vm_pressure_level
            # which is the actual jetsam signal. We launch when pressure ≤ 2 (warning),
            # block when pressure ≥ 4 (critical). pages_free retained as a fallback floor.
            local _wait_start=$(date +%s)
            local _patience=600
            while true; do
                local _level=$(mac_pressure_level)
                local _free_kb=$(mac_pages_free_kb)
                # Pressure level OK → launch immediately
                if [[ "$_level" -le 2 ]]; then
                    log "[SIGKILL_BACKOFF] pressure_level=${_level} (≤2 OK) free=${_free_kb}KB — launching"
                    break
                fi
                # Catastrophic pages_free floor: even with pressure ≥ 4, if the system
                # has clawed back ≥ 200MB free we're past the worst — launch.
                if [[ "$_free_kb" -ge 204800 ]]; then
                    log "[SIGKILL_BACKOFF] pages_free=${_free_kb}KB ≥ 200MB despite pressure_level=${_level} — launching"
                    break
                fi
                local _waited=$(( $(date +%s) - _wait_start ))
                if [[ "$_waited" -ge "$_patience" ]]; then
                    log "[SIGKILL_BACKOFF] pressure_level=${_level} free=${_free_kb}KB still tight, patience exhausted (${_patience}s) — launching anyway"
                    break
                fi
                log "[SIGKILL_BACKOFF] pressure_level=${_level} (≥4 critical) free=${_free_kb}KB — host still tight, waiting 60s more (${_waited}s/${_patience}s)"
                sleep 60
            done
        else
            if [[ "$consecutive_sigkill" -gt 0 ]]; then
                log "[SIGKILL_BACKOFF] resetting counter (last exit=$_last_exit, was $consecutive_sigkill)"
            fi
            consecutive_sigkill=0
            # 2026-10-06 USER: backoff on consecutive failures (exit 1 crash loops relaunched every 5s).
            # 0/143 (clean / graceful TERM recycle) reset the counter and relaunch fast as before.
            # 2026-10-14 USER: an INTENTIONAL restart relaunches immediately, never a 300 s gap. Intentional =
            # SIGTERM sent to this wrapper (handle_sigterm), a request file
            # data/restart_requests/<script><_acct>.request (consumed), or any exit after a STABLE run
            # (>= WD_STABLE_RUN_S, default 120 s: a rolling restart TERMs a long-running child -> exit 1).
            # The failure counter only grows for crash loops (exits within WD_STABLE_RUN_S of launch).
            local _req="$WORKDIR/data/restart_requests/${SCRIPT_BASE}${ACCT_SUFFIX}.request"
            # 2026-10-22 (USER "UNLOCK make sure no unnecessary restarts take place"): only an exit this box can
            # ACCOUNT FOR may be called INTENTIONAL. A bare `_run_duration >= WD_STABLE_RUN_S` meant any kill of a
            # child that had merely been up >120 s was logged "[INTENTIONAL_RESTART] … sigterm=0 request=0", which
            # is a lie — nobody intended it — and it was never counted, so an outside killer could recycle a
            # producer indefinitely and every line said the restart was deliberate. Measured on ez_indicators
            # 2026-10-22: three exit=143 at 05:02:18/05:11:42/05:17:44 (after 7425/462/356 s) all wore that label
            # while this watchdog had logged NO kill of its own — no [SINGLETON], no RSS recycle, no no-output kill
            # — and the sender was never identified because nothing recorded it. Behaviour is UNCHANGED (immediate
            # relaunch, counter reset, no backoff: a dead producer is never left dead to chase an attribution);
            # only the label is now truthful and the event is recorded so repeated unnecessary restarts surface.
            if [[ "${WD_INTENTIONAL:-0}" == "1" || -f "$_req" || "$_run_duration" -ge "${WD_STABLE_RUN_S:-120}" ]]; then
                local _why="sigterm" _tag="INTENTIONAL_RESTART"
                if [[ "${WD_INTENTIONAL:-0}" != "1" ]]; then
                    if [[ -f "$_req" ]]; then _why="request"; else _why="unexplained"; _tag="EXTERNAL_TERM"; fi
                fi
                log "[$_tag] exit=$_last_exit after ${_run_duration}s (cause=$_why sigterm=${WD_INTENTIONAL:-0} request=$([[ -f "$_req" ]] && echo 1 || echo 0)) — failure counter reset (was $consecutive_fail), relaunching in 1s"
                if [[ "$_why" == "unexplained" ]]; then record_unexplained_term "$_last_exit" "$_run_duration"; fi
                rm -f "$_req"
                WD_INTENTIONAL=0
                consecutive_fail=0
                sleep 1
                continue
            fi
            # 2026-10-20 EXIT CODES THAT ARE NOT CRASHES. An externally-signalled or deliberately-chosen exit must
            # never be charged to the crash-loop backoff (5/10/20/…/300 s), because that compounds someone else's
            # kill into minutes of not trading. Measured in the 24 h to 2026-10-20 17:07Z:
            #   130 — tradier_manage.py:60904 `raise KeyboardInterrupt` from its SIGTERM handler; CPython exits 130.
            #         55 of 83 stock exits. Classified as a crash, it reached "[RESTART_BACKOFF] consecutive
            #         failure #6 (code 130) — sleeping 160s" on trb at 14:46:06 — 160 s of RTH, for a clean stop.
            #   42  — ez_positions_service.py:6796-6799 `os._exit(42)` after 3 consecutive process_account_update
            #         hangs. 6 crypto exits. This is the engine deliberately recycling itself; the hang is the bug,
            #         the exit is the designed response.
            # 143 (SIGTERM before a handler exists) and 0 were already exempt.
            if [[ "$_last_exit" -ne 0 && "$_last_exit" -ne 143 && "$_last_exit" -ne 130 && "$_last_exit" -ne 42 ]]; then
                consecutive_fail=$((consecutive_fail + 1))
                local _fbo=$((5 * (1 << (consecutive_fail - 1))))
                if [[ "$_fbo" -gt 300 ]]; then _fbo=300; fi
                log "[RESTART_BACKOFF] consecutive failure #${consecutive_fail} (code $_last_exit) — sleeping ${_fbo}s"
                sleep "$_fbo"
            else
                consecutive_fail=0
                log "Script stopped. Restarting in 5 seconds..."
                sleep 5
            fi
        fi
    done
}

# 2026-10-22: record an exit this watchdog cannot account for, so an outside killer is attributable instead of
# being silently relabelled "intentional". Pure bookkeeping — it never signals, never delays, never fails the
# caller. Repeated entries are the signal: a producer recycled by nobody-knows-what is an unnecessary restart.
record_unexplained_term() {
    local _exit="$1" _dur="$2" _sink="$WORKDIR/data/reports/watchdog_unexplained_restarts.jsonl"
    local _now _suspects _n24
    _now=$(date +%s)
    mkdir -p "$(dirname "$_sink")" 2>/dev/null || true
    _suspects=$(ps -eo pid,command 2>/dev/null | grep -iE "guardian|keepalive|crypto_watchdog|pkill" | grep -vE "grep|TCPKeepAlive|ServerAlive" | sed -E 's#^ *([0-9]+) .*/([^/ ]+).*#\1:\2#' | tr '\n' ' ' | cut -c1-240)
    printf '{"ts":%s,"script":"%s","acct":"%s","exit":%s,"run_s":%s,"wd_pid":%s,"live_suspects":"%s"}\n' \
        "$_now" "$SCRIPT_BASE" "${ACCT_SUFFIX#_}" "$_exit" "$_dur" "$$" "$_suspects" >> "$_sink" 2>/dev/null || true
    _n24=$(awk -v c=$((_now - 86400)) -v s="\"script\":\"$SCRIPT_BASE\"" 'index($0,s) && match($0,/"ts":[0-9]+/) && substr($0,RSTART+5,RLENGTH-5)+0 >= c {n++} END {print n+0}' "$_sink" 2>/dev/null || echo 0)
    log "[EXTERNAL_TERM] recorded: ${_n24} unexplained restart(s) of ${SCRIPT_BASE}${ACCT_SUFFIX} in the last 24h — see data/reports/watchdog_unexplained_restarts.jsonl"
    if [[ "${_n24:-0}" -ge "${WD_UNEXPLAINED_ALERT_24H:-4}" ]]; then
        log "⚠️ [EXTERNAL_TERM_STORM] ${_n24} unexplained external SIGTERMs of ${SCRIPT_BASE}${ACCT_SUFFIX} in 24h (threshold ${WD_UNEXPLAINED_ALERT_24H:-4}) — an outside process is recycling this producer; live suspects: ${_suspects:-none}"
    fi
}

# Cleanup on exit (only on true exit, not SIGTERM of child)
cleanup() {
    log "Watchdog shutting down..."
    # Kill any remaining child processes
    pkill -P $$ 2>/dev/null || true
}

# SIGTERM to watchdog itself: kill child, let main loop restart it
CHILD_PID=""
handle_sigterm() {
    WD_INTENTIONAL=1
    log "Watchdog received SIGTERM — killing child, will restart"
    if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null || true
        sleep 2
        kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    # Do NOT exit — let the main loop handle restart
}

trap handle_sigterm TERM
trap cleanup EXIT

# Start the watchdog
main
