#!/bin/bash
# LIVE FAILOVER lease gate (2026-10-13 USER: Mac and S1 must never both trade). Sourced by
# run_with_watchdog.sh (Mac + S1 live dir), tools/s1_trading_supervisor.sh and tools/s1_failover_start.sh (S1).
# lf_lease_fenced <script> <workdir> -> exit 0 when THIS machine must NOT spawn <script>:
#   Mac (Darwin): data/live_failover/lease_local.json says holder "s1" (written by
#                 tools/mac_failover_agent.py from S1's authoritative lease.json) AND the script's venue is in
#                 its "venues" (no "venues" field = every venue, the pre-2026-10-14 full-lease behaviour).
#   S1 (Linux)  : data/live_failover/lease.json does not say holder "s1", OR it has a "run_venues" field that does
#                 not contain the script's venue (run_venues is set by tools/live_failover_s1.py only after the Mac
#                 acknowledged the fence for that venue; no field = legacy full lease).
# Venue: tradier_manage.py / tradier_positions.py = stocks, every other gated script = crypto (2026-10-14 per-venue
# not-trading takeover). Only order-placing / authoritative-position procs are gated; data feeds always run.
lf_is_gated_script() {
    case "$1" in
        ez_manage.py|tradier_manage.py|ez_positions_watchdog.py|ez_positions.py|ez_copilot.py|ez_reentry_daemon.py|tradier_positions.py) return 0 ;;
    esac
    return 1
}
lf_script_venue() {
    case "$1" in
        tradier_manage.py|tradier_positions.py) echo stocks ;;
        *) echo crypto ;;
    esac
}
lf_lease_fenced() {
    local script="$1" workdir="$2" venue file
    lf_is_gated_script "$script" || return 1
    venue="$(lf_script_venue "$script")"
    if [[ "$(uname)" == "Darwin" ]]; then
        file="$workdir/data/live_failover/lease_local.json"
        grep -q '"holder": *"s1"' "$file" 2>/dev/null || return 1
        # 2026-10-16 A GHOST LEASE MUST NOT FENCE FOREVER (USER: "trading can only stop because macbook went offline,
        # NEVER is there any possibility that it just stops"). lease_local.json is written by tools/mac_failover_agent.py
        # from S1's lease; if that agent dies while the file says holder=s1, every gated Mac trader stays fenced with
        # nothing left to ever unfence it. A lease older than LF_LEASE_MAX_AGE_S is not a lease, it is a leftover file.
        local _age _now _fetched
        _fetched="$(sed -n 's/.*"fetched_epoch"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$file" 2>/dev/null | head -1)"
        _now="$(date -u +%s)"
        if [[ -n "$_fetched" ]]; then
            _age=$(( _now - _fetched ))
            if [[ "$_age" -gt "${LF_LEASE_MAX_AGE_S:-600}" ]]; then
                echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [LEASE_STALE] lease_local.json says holder=s1 but is ${_age}s old (> ${LF_LEASE_MAX_AGE_S:-600}s) — the failover agent is not maintaining it; NOT fencing $script" >&2
                return 1
            fi
        fi
        grep -q '"venues":' "$file" 2>/dev/null || return 0
        grep -q "\"venues\": *\"[^\"]*${venue}" "$file" 2>/dev/null && return 0
        return 1
    fi
    file="$workdir/data/live_failover/lease.json"
    grep -q '"holder": *"s1"' "$file" 2>/dev/null || return 0
    grep -q '"run_venues":' "$file" 2>/dev/null || return 1
    grep -q "\"run_venues\": *\"[^\"]*${venue}" "$file" 2>/dev/null && return 1
    return 0
}
