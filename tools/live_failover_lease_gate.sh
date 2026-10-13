#!/bin/bash
# LIVE FAILOVER lease gate (2026-10-13 USER: Mac and S1 must never both trade). Sourced by
# run_with_watchdog.sh (Mac) and tools/s1_trading_supervisor.sh (S1).
# lf_lease_fenced <script> <workdir> -> exit 0 when THIS machine must NOT spawn <script>:
#   Mac (Darwin): data/live_failover/lease_local.json says holder "s1" (written by
#                 tools/mac_failover_agent.py from S1's authoritative lease.json).
#   S1 (Linux)  : data/live_failover/lease.json does not say holder "s1".
# Only order-placing / authoritative-position procs are gated; data feeds always run.
lf_is_gated_script() {
    case "$1" in
        ez_manage.py|tradier_manage.py|ez_positions_watchdog.py|ez_positions.py|ez_copilot.py|ez_reentry_daemon.py|tradier_positions.py) return 0 ;;
    esac
    return 1
}
lf_lease_fenced() {
    local script="$1" workdir="$2"
    lf_is_gated_script "$script" || return 1
    if [[ "$(uname)" == "Darwin" ]]; then
        grep -q '"holder": *"s1"' "$workdir/data/live_failover/lease_local.json" 2>/dev/null && return 0
        return 1
    fi
    grep -q '"holder": *"s1"' "$workdir/data/live_failover/lease.json" 2>/dev/null && return 1
    return 0
}
