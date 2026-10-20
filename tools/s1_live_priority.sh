#!/bin/bash
# s1_live_priority.sh — USER 2026-10-16: "These scripts take prevalence over any running tests on s1."
#
# WHY (proved on S1, 2026-10-16 20:05Z): live-failover-stocks-producers.service started on demand, produced stock
# indicators for 3 minutes, and then:
#     s1_producers_stocks.sh: line 16: 2430158 Killed   "$PY" -u tradier_indicators.py
#     × live-failover-stocks-producers.service - Active: failed (Result: timeout)
# The host had 25 GB of 30 GB used with NO SWAP, and the largest RSS on the box was the backtest herd
# (v15_pilot 2.78 GB / 2.53 GB / 1.84 GB, npz_bar_refresh 1.73 GB x2, all at oom_score_adj=0, oom_score 666-685).
# The kernel picked the live stock data producer because nothing told it the producer matters more than a sweep.
# So the standby could not produce stock indicators AT ALL — exactly the capability the Mac falls back to.
#
# This guard states the priority the operator wants, in the only terms the OOM killer understands:
#   LIVE  (data producers, traders, position writers, the failover agent itself) -> oom_score_adj -900, nice -5
#   TESTS (pilots, sweeps, herds, NPZ builders, precompute)                      -> oom_score_adj +800, nice +15
# A backtest worker is therefore ALWAYS a more attractive victim than any live process, whatever their sizes.
# It only ever reprioritises; it never kills anything. Idempotent — safe to run every minute from cron.
#
#   */1 * * * * /bin/bash /home/niels/binance/tools/s1_live_priority.sh >> /home/niels/logs/s1_live_priority.log 2>&1
#
# 2026-10-20 NOW RUNS ON THE MAC TOO — IT PREVIOUSLY COULD NOT. USER: "MACBOOK TRADES WHILE IT IS UP NOTHING ELSE
# TRADES UNTIL MACBOOK IS OFFLINE" and "s1 takes over every time I take macbook offline" — i.e. S1 must NEVER take
# over while the Mac is up. The measured 2026-10-20 16:38-16:56Z outage was exactly that: S1 wrote the lease with
# reason "Mac down: hb_age=..s pushes_stopped" and grew it to crypto+stocks, the Mac was then fenced out of both,
# and for 18 minutes NOTHING traded on the Mac. The Mac was not down — it was starved. At 17:08Z load average was
# 306 and EVERY live process (all five ez_manage, all three tradier_manage, the five ez_positions_service_main
# writers, ez_klines, ez_indicators AND tools/mac_failover_agent.py — the heartbeat writer whose silence IS the
# takeover trigger) was at nice=0, the same priority as v18_linknet training, v18_experience ingest and
# v18_precision_build. Nothing told the scheduler the traders matter more than a sweep.
# This script was the guard for that, and on the Mac it was a NO-OP: `[ -d "/proc/$pid" ] || continue` skipped
# EVERY pid because macOS has no /proc, so not one process was ever reprioritised there. Two portability fixes:
#   - liveness test is `kill -0` (portable), not `/proc/$pid`
#   - oom_score_adj is Linux-only and is now skipped cleanly off Linux WITHOUT skipping the renice
# On macOS a negative nice needs root, so `renice -n -5` on the live tier will usually fail and live stays at 0;
# demoting the research tier to +15 does NOT need root and delivers the same priority GAP. That is the fix that
# matters, and it is why the research patterns below now also cover the v18_* brain/experience/precision tools.
set -u

# LIVE = anything that trades, produces the data trading reads, writes positions, keeps the failover lease honest,
# or contains live risk (day-loss, per-symbol halt, loss streak). These are promoted, never demoted.
LIVE_RE='ez_indicators\.py|ez_indicators_merger\.py|ez_klines\.py|ez_prices\.py|ez_prices_ws\.py|ez_mark_prices\.py|ez_manage\.py|ez_positions|ez_reentry|ez_share_ind\.py|ez_market_data\.py|ez_orderbook\.py|ez_crosses\.py|ez_copilot\.py|tradier_indicators\.py|tradier_klines\.py|tradier_prices\.py|tradier_manage\.py|tradier_positions\.py|live_failover_s1\.py|mac_failover_agent\.py|never_untraded_guard\.py|v15_day_loss_guard\.py|v15_sym_halt\.py|v15_loss_streak_watch\.py|v15_open_positions_push\.py|v15_warn_daemon\.py|v15_s1_tunnel_keeper\.sh'
# TESTS = an EXPLICIT research list. 2026-10-20: this was `tools/v15_|v17_|v18_` — a blanket prefix match, which on
# the Mac demoted live risk guards that merely share the v15_ prefix: v15_day_loss_guard.py (same-day containment on
# all five Binance accounts), v15_sym_halt.py (per-pair halt after N losers), v15_loss_streak_watch.py,
# v15_open_positions_push.py (the push that makes S1's failover view of positions non-blind) and, worst,
# tools/v15_lock_run.sh — the generic lock wrapper that EXECS its child, including never_untraded_guard.py, so
# demoting the wrapper demoted the local-repair path itself. A priority guard must fail SAFE: anything not named
# here is left completely alone, because on macOS a demotion CANNOT be undone without root (`sudo renice` prompts
# for a password), so a wrong demotion persists for the life of the process.
TEST_RE='v15_pilot\.py|v15_local_herd|v15_overnight_herd|v15_cpu80_watchdog|v15_parity_saturator\.py|v15_mac_harvest_loop\.sh|v15_trade_parity\.py|v15_xlsx_topup|v15_refill_from_json\.py|v15_assure|v15_vector_delta|v15_switch_add\.py|v15_morning_report\.py|v12_quick_engine|backtest_v8_precompute|backtest_v12_engine|npz_bar_refresh|npz_fresh_sync|v16_|v17_|v18_linknet|v18_experience|v18_precision_build|v18_brain|dc_simple_8_sweep'

# NEVER touched, whatever else matches. `pgrep -f` reads the WHOLE command line, so a process is demoted for merely
# MENTIONING a research name: an agent shell sourcing ~/.claude/shell-snapshots/..., a `sh -c pgrep -f v15_...`
# liveness probe, or an ssh/rsync transport whose argv names an NPZ path. Demotions are irreversible without root
# here, and an rsync can just as easily be the live klines push as a research pull, so ambiguity means hands off.
NEVER_RE='shell-snapshots|\.claude/|/claude |claude-code|^/bin/(ba|z)?sh -c pgrep|sh -c pgrep -f|(^| )(ssh|rsync|scp|sftp) |Code Helper|node '

LIVE_ADJ="${S1_LIVE_OOM_ADJ:--900}"
TEST_ADJ="${S1_TEST_OOM_ADJ:-800}"
LIVE_NICE="${S1_LIVE_NICE:--5}"
TEST_NICE="${S1_TEST_NICE:-15}"

ts() { date -u +%FT%TZ; }

_alive() { kill -0 "$1" 2>/dev/null; }   # portable liveness; macOS has no /proc

_set_adj() {   # pid, adj  — writing a NEGATIVE adj needs privilege; a positive one does not. Linux-only.
    local pid="$1" adj="$2" cur
    [ -d /proc ] || return 1
    cur=$(cat "/proc/$pid/oom_score_adj" 2>/dev/null) || return 1
    [ "$cur" = "$adj" ] && return 1
    if ! echo "$adj" > "/proc/$pid/oom_score_adj" 2>/dev/null; then
        sudo -n sh -c "echo $adj > /proc/$pid/oom_score_adj" 2>/dev/null || return 1
    fi
    return 0
}

_renice() {
    local pid="$1" want="$2" cur
    cur=$(ps -o ni= -p "$pid" 2>/dev/null | tr -d ' ') || return 1
    [ -z "$cur" ] && return 1
    [ "$cur" = "$want" ] && return 1
    renice -n "$want" -p "$pid" >/dev/null 2>&1 || sudo -n renice -n "$want" -p "$pid" >/dev/null 2>&1 || return 1
    return 0
}

# Resolve the LIVE set FIRST and hold it: the two patterns can match the same pid (a wrapper, a shell whose command
# line quotes both names), and a demoted live process is the exact outcome this guard exists to prevent.
LIVE_PIDS=" $(pgrep -f "$LIVE_RE" 2>/dev/null | tr '\n' ' ')"
live_n=0; test_n=0
live_ren=0; test_ren=0
for pid in $LIVE_PIDS; do
    [ "$pid" = "$$" ] && continue
    _alive "$pid" || continue   # pid may exit between pgrep and here — not an error
    _set_adj "$pid" "$LIVE_ADJ" && live_n=$((live_n + 1))
    _renice "$pid" "$LIVE_NICE" && live_ren=$((live_ren + 1))
done
for pid in $(pgrep -f "$TEST_RE" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    _alive "$pid" || continue
    case "$LIVE_PIDS" in *" $pid "*) continue ;; esac
    _args=$(ps -o args= -p "$pid" 2>/dev/null) || continue
    printf '%s' "$_args" | grep -Eq "$NEVER_RE" && continue
    printf '%s' "$_args" | grep -Eq "$LIVE_RE" && continue
    _set_adj "$pid" "$TEST_ADJ" && test_n=$((test_n + 1))
    _renice "$pid" "$TEST_NICE" && test_ren=$((test_ren + 1))
done

if [ "$live_n" -gt 0 ] || [ "$test_n" -gt 0 ] || [ "$live_ren" -gt 0 ] || [ "$test_ren" -gt 0 ]; then
    echo "[$(ts)] reprioritised oom(live=$live_n tests=$test_n) nice(live=$live_ren tests=$test_ren) (live adj=$LIVE_ADJ nice=$LIVE_NICE, tests adj=$TEST_ADJ nice=$TEST_NICE)"
fi
exit 0
