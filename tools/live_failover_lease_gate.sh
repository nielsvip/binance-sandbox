#!/bin/bash
# LIVE FAILOVER lease gate (2026-10-13 USER: Mac and S1 must never both trade). Sourced by
# run_with_watchdog.sh (Mac + S1 live dir), tools/s1_trading_supervisor.sh and tools/s1_failover_start.sh (S1).
# lf_lease_fenced <script> <workdir> [account] -> exit 0 when THIS machine must NOT spawn <script>:
#   Mac (Darwin): data/live_failover/lease_local.json says holder "s1" (written by
#                 tools/mac_failover_agent.py from S1's authoritative lease.json) AND the script's venue is in
#                 its "venues" (no "venues" field = every venue, the pre-2026-10-14 full-lease behaviour).
#   S1 (Linux)  : data/live_failover/lease.json does not say holder "s1", OR it has a "run_venues" field that does
#                 not contain the script's venue (run_venues is set by tools/live_failover_s1.py only after the Mac
#                 acknowledged the fence for that venue; no field = legacy full lease).
# 2026-10-19 ACCOUNT SCOPE (USER "if any account fails it should be taken over by s1 ... If trb fails
# tradier_ has to take over on s1 immediately"): S1's lease may carry "accounts": {"stocks": ["trb"]}
# meaning S1 holds ONLY those accounts — the Mac keeps trading their siblings. With a scope present,
# an account in scope is fenced on the Mac / runnable on S1; an account outside scope is the reverse.
# No scope (or unreadable scope) = legacy venue-wide behaviour. An UNSPECIFIED account with a scope
# present reads FENCED (fail-safe: never spawn into doubt).
# Venue: tradier_manage.py / tradier_positions.py = stocks, every other gated script = crypto (2026-10-14 per-venue
# not-trading takeover). Only order-placing / authoritative-position procs are gated; data feeds always run.
lf_lease_scope() {
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); a=d.get('accounts') or {}; print(' '.join(sorted(f\"{k}={','.join(v)}\" for k,v in a.items() if v)))" "$1" 2>/dev/null
}
lf_account_held() {
    local scope="$1" venue="$2" account="$3" entry
    [[ -z "$scope" ]] && return 1
    for entry in $scope; do
        if [[ "$entry" == "$venue="* ]]; then
            [[ ",${entry#*=}," == *",$account,"* ]] && return 0
            return 1
        fi
    done
    return 1
}
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
    local script="$1" workdir="$2" account="${3:-}" venue file scope
    lf_is_gated_script "$script" || return 1
    venue="$(lf_script_venue "$script")"
    if [[ "${LF_UNAME:-$(uname)}" == "Darwin" ]]; then
        file="$workdir/data/live_failover/lease_local.json"
        grep -q '"holder": *"s1"' "$file" 2>/dev/null || return 1
        # 2026-10-16 A GHOST LEASE MUST NOT FENCE FOREVER (USER: "trading can only stop because macbook went offline,
        # NEVER is there any possibility that it just stops"). lease_local.json is written by tools/mac_failover_agent.py
        # from S1's lease; if that agent dies while the file says holder=s1, every gated Mac trader stays fenced with
        # nothing left to ever unfence it. A lease older than LF_LEASE_MAX_AGE_S is not a lease, it is a leftover file.
        local _age _now _fetched
        _fetched="$(sed -n 's/.*"fetched_epoch"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$file" 2>/dev/null | head -1)"
        # Honour the project's test clock (tools/live_failover_common.py:62 does the same:
        # float(os.environ.get("LF_FAKE_NOW") or time.time())). Without it the staleness guard below compared a
        # lease stamped at the harness's frozen T_SESSION (2026-10-13 18:00Z) against the REAL wall clock, so by
        # 2026-10-17 every fence read as ~4 days stale and 6 failover tests failed on
        # `gate(world.mac, "ez_manage.py") == 0` -- i.e. the tests that prove the Mac is FENCED when S1 takes a
        # venue, the "seamlessly" half of the 3-minute takeover, could not pass at all. Production is unaffected:
        # LF_FAKE_NOW is never set outside the harness.
        _now="${LF_FAKE_NOW%%.*}"
        [[ -n "$_now" ]] || _now="$(date -u +%s)"
        if [[ -n "$_fetched" ]]; then
            _age=$(( _now - _fetched ))
            if [[ "$_age" -gt "${LF_LEASE_MAX_AGE_S:-600}" ]]; then
                echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [LEASE_STALE] lease_local.json says holder=s1 but is ${_age}s old (> ${LF_LEASE_MAX_AGE_S:-600}s) — the failover agent is not maintaining it; NOT fencing $script" >&2
                return 1
            fi
        fi
        grep -q '"venues":' "$file" 2>/dev/null || return 0
        grep -q "\"venues\": *\"[^\"]*${venue}" "$file" 2>/dev/null || return 1
        # 2026-10-20 NEVER FENCE A VENUE S1 WILL NOT RUN. The lease has two fields: "venues" = what the Mac must
        # fence, "run_venues" = what S1 is actually allowed to run. They can disagree, and when they do this gate
        # used to fence on "venues" alone and nobody traded the venue at all.
        # MEASURED 2026-10-20 14:24Z, market open: lease gen 70 holder=s1 venues="crypto,stocks" run_venues="crypto",
        # S1 running ZERO tradier_manage processes, and all three Mac stock accounts (trb/trc/tra) sitting in
        # [LEASE_FENCED] — stocks fenced here, unrun there. The recovery path was blocked too: tradier_positions.py is
        # itself a gated script, so the stale tra/state.json that triggered the takeover could never refresh and the
        # Mac could never prove health. Same shape as the 2026-10-16 incident ("S1 — which does not run stocks at all
        # — held the lease") and the same principle stated there: only a host that may RUN the venue may fence for it.
        # If run_venues is present and non-empty and does NOT name this venue, S1 has told us it will not run it, so
        # this host keeps trading. An absent/empty run_venues is the pre-ack window and still fences (fail-safe:
        # S1 is about to take it), which preserves the "never both trade" guarantee.
        if grep -q '"run_venues":' "$file" 2>/dev/null; then
            local _rv
            _rv="$(sed -n 's/.*"run_venues"[^"]*"\([^"]*\)".*/\1/p' "$file" 2>/dev/null | head -1)"
            if [[ -n "$_rv" ]] && [[ "$_rv" != *"${venue}"* ]]; then
                echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [LEASE_NOT_FENCED] lease holder=s1 fences '${venue}' but its run_venues='${_rv}' excludes it — S1 will not run this venue, so NOT fencing $script here (nobody would trade it)" >&2
                return 1
            fi
        fi
        scope="$(lf_lease_scope "$file")"
        if [[ -n "$scope" ]]; then
            if [[ -z "$account" ]]; then
                return 0
            fi
            lf_account_held "$scope" "$venue" "$account" && return 0
            return 1
        fi
        return 0
    fi
    file="$workdir/data/live_failover/lease.json"
    grep -q '"holder": *"s1"' "$file" 2>/dev/null || return 0
    grep -q '"run_venues":' "$file" 2>/dev/null || return 1
    grep -q "\"run_venues\": *\"[^\"]*${venue}" "$file" 2>/dev/null || return 0
    scope="$(lf_lease_scope "$file")"
    if [[ -n "$scope" ]]; then
        if [[ -z "$account" ]]; then
            return 0
        fi
        lf_account_held "$scope" "$venue" "$account" && return 1
        return 0
    fi
    return 1
}
