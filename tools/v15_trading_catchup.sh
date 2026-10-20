#!/bin/bash
# v15_trading_catchup — USER 2026-10-19: get EVERY trading sym_side recounted before the open. The fleet scheduler
# spends most of its per-tick launch budget on universe symbols its own _drop_untradeable then refuses ("dispatch
# SKIPPED for VICR/BA/WDAY/IBIT/A/RRC: no tradeable side left", 3-4 per tick), so the boards that actually trade were
# starved. This dispatches them directly through the project's own tools/v15_push_dispatch.sh (NPZ prep + md5-verified
# push + launch, and it skips a side already running), up to MAXPER per host per run, only while load is under MAXCPU.
# Sources, in priority order: ~/v15_priority_front.txt (the stale-since-Thursday trading symbols), then
# ~/v15_trading_symsides.txt (all tra+trb+trc long/short sym_sides).
set -u
ROOT="$HOME/binance-sandbox"
PDIR="$(ls -dt $HOME/v15_run3*/progress 2>/dev/null | head -1)"
CELL="$HOME/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"
[ -n "$PDIR" ] || exit 0
LOG="$HOME/logs/v15_trading_catchup.log"
mkdir -p "$(dirname "$LOG")"
MAXCPU="${CATCHUP_MAX_CPU:-80}"
MAXPER="${CATCHUP_MAX_PER_HOST:-3}"
# USER 2026-10-20 (fleet concurrency 9.3 -> 1.0): 1,137 dispatches for 20 sym_sides in 24 h on S2, every run ending on the same
# final_gain because the content gate refuses the board. A side is NOT re-dispatched when (1) it was dispatched in the last
# CATCHUP_MIN_GAP_H (6) hours, (2) its last 2 KPI runs ended on the same final_gain (stalled) within 24 h, or (3) its sweep log
# (24 h) ends in an ESSENTIAL-GATE / RUN-FAILED-KEPT-BEST refusal. CATCHUP_LOOP_GUARD=0 restores the old behaviour.
GAP_S=$(( ${CATCHUP_MIN_GAP_H:-6} * 3600 ))
STATE="$HOME/logs/v15_catchup_state.txt"
touch "$STATE"
NOW=$(date +%s)
STALLED=" $(CATCHUP_LOOP_GUARD="${CATCHUP_LOOP_GUARD:-1}" python3 - <<'PYEOF'
import os, sqlite3, time
if os.environ.get("CATCHUP_LOOP_GUARD", "1") != "1":
    raise SystemExit
try:
    c = sqlite3.connect(os.path.expanduser("~/binance-sandbox/data/kpi/v17_kpi.sqlite"), timeout=5)
    rows = c.execute("select sym_side, final_gain, done_ts from boards where done_ts > ? order by done_ts desc", (time.time() - 14 * 86400,)).fetchall()
except Exception:
    raise SystemExit
seen = {}
for sym_side, gain, done_ts in rows:
    seen.setdefault(sym_side, []).append((gain, done_ts))
out = []
for sym_side, lst in seen.items():
    if len(lst) >= 2 and lst[0][0] is not None and round(float(lst[0][0]), 6) == round(float(lst[1][0] if lst[1][0] is not None else 1e18), 6) and time.time() - lst[0][1] < 86400:
        out.append(sym_side)
print(" ".join(out))
PYEOF
) "
cd "$ROOT" || exit 0
FRONT=""
[ -f "$HOME/v15_priority_front.txt" ] && FRONT=$(tr ',' '\n' < "$HOME/v15_priority_front.txt" | sed 's/[[:space:]]//g' | grep . | while read -r s; do echo "${s}_LONG"; echo "${s}_SHORT"; done)
ALL=""
[ -f "$HOME/v15_trading_symsides.txt" ] && ALL=$(grep . "$HOME/v15_trading_symsides.txt")
WANT=$(printf '%s\n%s\n' "$FRONT" "$ALL" | grep . | awk '!seen[$0]++')
for H in 127.0.0.1 10.0.0.4; do
    if [ "$H" = "127.0.0.1" ]; then
        CPU=$(awk -v c="$(nproc)" '{print int($1*100/c)}' /proc/loadavg)
    else
        CPU=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "niels@$H" 'awk -v c=$(nproc) "{print int(\$1*100/c)}" /proc/loadavg' 2>/dev/null || echo 99)
    fi
    [ "${CPU:-99}" -ge "$MAXCPU" ] && { echo "$(date -u +%FT%TZ) $H cpu=${CPU}% hold" >> "$LOG"; continue; }
    if [ "${CATCHUP_LOOP_GUARD:-1}" = "1" ]; then
        if [ "$H" = "127.0.0.1" ]; then MEMA=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo); else MEMA=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "niels@$H" "awk '/MemAvailable/ {print int(\$2/1024)}' /proc/meminfo" 2>/dev/null || echo 0); fi
        [ "${MEMA:-0}" -lt "${CATCHUP_MIN_MEM_MB:-9000}" ] && { echo "$(date -u +%FT%TZ) $H mem_avail=${MEMA}MB hold" >> "$LOG"; continue; }
    fi
    N=0
    for SS in $WANT; do
        [ "$N" -ge "$MAXPER" ] && break
        # USER 2026-10-19 ("no results visible"): a board that merely STARTED is not a result. 65 boards were refused
        # at publish by the content gate (F_filled < 25% of the done board) and kept their old standing board; progress
        # accumulates across runs, and boards given a second slice do pass (AMZN_LONG 2081, ADP_SHORT 2136 vs 749-852
        # on a first slice). So eligibility is now "has no PUBLISHED board", not "has no progress file" — otherwise the
        # refused boards never get the slice that would let them publish.
        ls $CELL/${SS}_*gain*_30d_matrix.xlsx >/dev/null 2>&1 && continue
        if [ "${CATCHUP_LOOP_GUARD:-1}" = "1" ]; then
            LASTD=$(awk -v k="$SS" '$2==k {t=$1} END {print t+0}' "$STATE")
            [ $((NOW - LASTD)) -lt "$GAP_S" ] && continue
            case "$STALLED" in *" $SS "*) echo "$(date -u +%FT%TZ) $H skip $SS stalled (last 2 runs same final_gain)" >> "$LOG"; continue;; esac
            REF_CMD="f=/tmp/sweep_${SS}_30D.log; [ -n \"\$(find \$f -newermt '-24 hours' 2>/dev/null)\" ] && tail -n 4000 \$f | grep -q -E 'ESSENTIAL-GATE|RUN-FAILED-KEPT-BEST'"
            if [ "$H" = "127.0.0.1" ]; then bash -c "$REF_CMD" && REFUSED=1 || REFUSED=0; else ssh -o BatchMode=yes -o ConnectTimeout=8 "niels@$H" "$REF_CMD" >/dev/null 2>&1 && REFUSED=1 || REFUSED=0; fi
            [ "$REFUSED" = "1" ] && { echo "$(date -u +%FT%TZ) $H skip $SS content-gate refusal in sweep log" >> "$LOG"; continue; }
        fi
        [ -n "$(find /tmp/sweep_${SS}_30D.log -newermt '-75 minutes' 2>/dev/null)" ] && continue
        pgrep -f "v15_pilot.py --sym-side ${SS}" >/dev/null 2>&1 && continue
        ssh -o BatchMode=yes -o ConnectTimeout=8 "niels@$H" "pgrep -f 'v15_pilot.py --sym-side ${SS}'" >/dev/null 2>&1 && continue
        echo "$(date -u +%FT%TZ) $H cpu=${CPU}% -> dispatch $SS" >> "$LOG"
        echo "$NOW $SS $H" >> "$STATE"
        timeout 300 bash tools/v15_push_dispatch.sh "$H" 6 "$SS" >> "$LOG" 2>&1
        N=$((N+1))
    done
    [ "$N" -eq 0 ] && echo "$(date -u +%FT%TZ) $H nothing eligible" >> "$LOG"
done
