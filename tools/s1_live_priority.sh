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
set -u

LIVE_RE='ez_indicators\.py|ez_indicators_merger\.py|ez_klines\.py|ez_prices\.py|ez_prices_ws\.py|ez_mark_prices\.py|ez_manage\.py|ez_positions|ez_reentry|tradier_indicators\.py|tradier_klines\.py|tradier_prices\.py|tradier_manage\.py|tradier_positions\.py|live_failover_s1\.py'
TEST_RE='v15_pilot\.py|v15_.*herd|v12_quick_engine|backtest_v8_precompute|backtest_v12_engine|npz_bar_refresh|npz_fresh_sync|v15_xlsx_topup|v17_|tools/v15_'

LIVE_ADJ="${S1_LIVE_OOM_ADJ:--900}"
TEST_ADJ="${S1_TEST_OOM_ADJ:-800}"
LIVE_NICE="${S1_LIVE_NICE:--5}"
TEST_NICE="${S1_TEST_NICE:-15}"

ts() { date -u +%FT%TZ; }

_set_adj() {   # pid, adj  — writing a NEGATIVE adj needs privilege; a positive one does not
    local pid="$1" adj="$2" cur
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
for pid in $LIVE_PIDS; do
    [ "$pid" = "$$" ] && continue
    [ -d "/proc/$pid" ] || continue   # pid may exit between pgrep and here — not an error
    _set_adj "$pid" "$LIVE_ADJ" && live_n=$((live_n + 1))
    _renice "$pid" "$LIVE_NICE"
done
for pid in $(pgrep -f "$TEST_RE" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    [ -d "/proc/$pid" ] || continue
    case "$LIVE_PIDS" in *" $pid "*) continue ;; esac
    _set_adj "$pid" "$TEST_ADJ" && test_n=$((test_n + 1))
    _renice "$pid" "$TEST_NICE"
done

[ "$live_n" -gt 0 ] || [ "$test_n" -gt 0 ] && echo "[$(ts)] reprioritised live=$live_n tests=$test_n (live adj=$LIVE_ADJ nice=$LIVE_NICE, tests adj=$TEST_ADJ nice=$TEST_NICE)"
exit 0
