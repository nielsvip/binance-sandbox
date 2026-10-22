#!/bin/bash
# v15_round_watchdog — USER 2026-10-08 ("you let the s1 coordinator sit there and die"): if the fleet scheduler reports
# every REACHABLE, NON-DRAINED host idle with "no eligible ready symbol" for 3 consecutive ticks (round exhausted)
# and no pilot runs anywhere, roll the round through the autopilot's own stage (tools/v15_autopilot.py --force-stage restart).
# USER 2026-10-15 (06.1, round-concept decision: KEEP rounds, ignore dead weight): UNREACHABLE hosts and drained hosts
# (data/fleet_drain_hosts.json + $V15_SCHED_DRAIN_HOSTS) are IGNORED by the check — s5/s6 "UNREACHABLE" used to veto
# the all() forever (round stuck on v15_run32_20261011). Ticks with zero judgable hosts are never a strike.
# USER 2026-10-22 (QUORUM rule, measured deadlock): the old "a host blocked for a REAL reason (disk/mem guard, slots
# full) still vetoes, rolling fixes nothing" assumption is FALSE when the blocked host is a minority. Measured 2026-10-22
# 02:58Z: s2/s5/s6 reported "no eligible ready symbol" at slots 0/6 while s1 sat at slots 1/8 "mem guard" holding ONE
# symbol (FLUXUSDT) — s1 vetoed the all() for 17h (last roll 2026-10-21T09:30Z), 3 of 4 billed hosts idle the whole
# time, published_24h=0, coverage 0.000. The forced roll run34->run35 reset 31 attempt keys and work became eligible
# again, so the round WAS exhausted and rolling DID fix it. Two changes, both fail-safe:
#   RULE A (strict, unchanged): ALL judgable hosts "no eligible" at 0/ for >=3 ticks and no pilot anywhere -> roll.
#   RULE B (quorum, new): >=half (and >=2) of judgable hosts "no eligible" at 0/ for >=$V15_ROUND_WD_QUORUM_TICKS
#   consecutive ticks with no launches -> roll even if another host is busy or resource-guarded. A busy host can no
#   longer starve the rest of the fleet, and RULE B deliberately does NOT require a fleet-wide zero pilot count: that
#   second gate is what made the deadlock permanent, because s1 almost always runs something.
# A $V15_ROUND_WD_COOLDOWN_S cooldown stops attempt-counter thrash, and every decision is logged + published to
# $V15_ROUND_WD_STATE so an idle fleet is never silent again. Idempotent, logged.
cd /home/niels/binance-sandbox || exit 1
L=${V15_SCHED_LOG:-/tmp/v15_fleet_sched.log}; W=/tmp/v15_round_watchdog.log
S=${V15_ROUND_WD_STATE:-/tmp/v15_round_wd_state.json}; STAMP=${V15_ROUND_WD_STAMP:-/tmp/v15_round_wd_last_roll}
QTICKS=${V15_ROUND_WD_QUORUM_TICKS:-8}; COOL=${V15_ROUND_WD_COOLDOWN_S:-900}
log(){ echo "[$(date -u +%FT%TZ)] [round-wd] $*" >> $W; }
VERDICT=$(grep '^{"now"' $L | tail -$QTICKS | QTICKS=$QTICKS S=$S python3 -c '
# PY-CHECK-START
import sys, json, os, time, math
drain = {x.strip() for x in os.environ.get("V15_SCHED_DRAIN_HOSTS", "").split(",") if x.strip()}
try:
    drain |= {str(x) for x in json.load(open("data/fleet_drain_hosts.json")).get("hosts", [])}
except Exception:
    pass
ticks = []
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    hs = d.get("hosts", {})
    elig = {k: v for k, v in hs.items() if isinstance(v, dict) and k not in drain and not v.get("draining")}
    if not elig:
        continue
    starved = {k for k, v in elig.items() if str(v.get("idle_reason", "")).startswith("no eligible") and str(v.get("slots", "")).startswith("0/")}
    ticks.append({"now": d.get("now"), "n_elig": len(elig), "starved": sorted(starved), "launched": bool(d.get("launched")), "hosts": {k: {"slots": v.get("slots"), "idle": v.get("idle_reason")} for k, v in elig.items()}})
strict = quorum = 0
for t in reversed(ticks):
    need = max(2, math.ceil(t["n_elig"] / 2))
    if not (len(t["starved"]) >= need and not t["launched"]):
        break
    quorum += 1
for t in reversed(ticks):
    if not (len(t["starved"]) == t["n_elig"] and not t["launched"]):
        break
    strict += 1
last = ticks[-1] if ticks else {}
state = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ticks_seen": len(ticks), "strict_streak": strict, "quorum_streak": quorum, "quorum_ticks_required": int(os.environ.get("QTICKS", 8)), "last_tick": last}
try:
    json.dump(state, open(os.environ.get("S", "/tmp/v15_round_wd_state.json"), "w"), indent=1)
except Exception:
    pass
print(strict, quorum, len(ticks), ",".join(last.get("starved", [])) or "-")
# PY-CHECK-END
')
STRICT=$(echo "$VERDICT" | awk '{print $1+0}'); QUORUM=$(echo "$VERDICT" | awk '{print $2+0}')
SEEN=$(echo "$VERDICT" | awk '{print $3+0}'); STARVED=$(echo "$VERDICT" | awk '{print $4}')
RUNNING=$(pgrep -f "v15_pilot.py --sym-side" | wc -l)
NOW=$(date -u +%s); LAST=$(cat $STAMP 2>/dev/null || echo 0); AGE=$((NOW-LAST))
REASON=""
if [ "$STRICT" -ge 3 ] && [ "$RUNNING" -eq 0 ]; then
  REASON="strict: all $SEEN-tick hosts no-eligible, 0 pilots"
elif [ "$QUORUM" -ge "$QTICKS" ]; then
  REASON="quorum: [$STARVED] starved $QUORUM/$QTICKS consecutive ticks (other hosts busy/guarded, $RUNNING pilots here)"
fi
if [ -n "$REASON" ]; then
  if [ "$AGE" -lt "$COOL" ]; then
    log "round exhausted ($REASON) but cooldown ${AGE}s < ${COOL}s -> not rolling"
  else
    log "round exhausted ($REASON) -> rolling round"
    echo "$NOW" > $STAMP
    timeout 300 python3 tools/v15_autopilot.py --once --force-stage restart >> $W 2>&1 && log "restart stage ok" || log "restart stage FAILED rc=$?"
  fi
else
  log "ok strict=$STRICT quorum=$QUORUM/$QTICKS ticks=$SEEN starved=[$STARVED] pilots=$RUNNING"
fi
