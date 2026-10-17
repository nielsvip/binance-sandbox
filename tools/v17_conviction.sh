#!/bin/bash
# v17_conviction — DAILY CONVICTION multipliers (USER 2026-10-14 "bolder entry/reentry quantities on proven winners").
#   launchd com.binance.v17-conviction 14:30Z (after the 13:00Z Mac apply + 13:40Z daily learn); Mac clock = UTC.
#   Install: cp launchd/com.binance.v17-conviction.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.binance.v17-conviction.plist
# Read-only on every input; writes data/conviction/* + the conviction columns of data/persym_size_tiers.json (backup first).
# Checks: inputs present, backup written, outputs parse, every tier mult <= OPEN_CEIL_SPS_MAX_MULT; any failure restores the backup.
# Kill switch: touch data/conviction/DISABLED (tier-only multipliers at the next read, no restart).
set -u
set -o pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
if [ "${V17_CONV_LOCKED:-0}" != "1" ]; then
  export V17_CONV_LOCKED=1
  exec /usr/bin/lockf -s -t 0 /tmp/v17_conviction.lock /bin/bash "$0" "$@"
fi
PY=$(bash "$ROOT/tools/pyinterp.sh")
D=$(date -u +%Y%m%d)
TS=$(date -u +%Y%m%d%H%M)
T=$ROOT/data/persym_size_tiers.json
log() { echo "$(date -u +%FT%TZ) [v17_conviction] $*"; }
for f in "$T" "$ROOT/perf_tier_sizing.py" "$ROOT/stale_set_halt.py" "$ROOT/data/hourly_reconfig/per_sym_store.db"; do
  [ -s "$f" ] || { log "MISSING input $f — abort (live keeps previous tiers)"; exit 2; }
done
grep -q "_triggered()" "$ROOT/perf_tier_sizing.py" || { log "perf_tier_sizing.py lacks the live drawdown clamp — abort"; exit 2; }
mkdir -p "$ROOT/backups" "$ROOT/data/conviction"
BK=$ROOT/backups/before_v17_conviction_run_${TS}_persym_size_tiers.json
cp -p "$T" "$BK" && [ -s "$BK" ] && cmp -s "$T" "$BK" || { log "backup of tiers failed — abort"; exit 3; }
log "backup $BK"
if ! nice -n 10 "$PY" -u tools/v17_conviction.py --date "$D" > "$ROOT/data/conviction/run_$D.log" 2>&1; then
  log "tool failed rc=$? — restoring tiers backup"; cp -p "$BK" "$T"; tail -20 "$ROOT/data/conviction/run_$D.log"; exit 4
fi
if ! "$PY" - "$T" <<'EOF'
import json, sys
sys.path.insert(0, "tools")
import v17_conviction as C
d = json.load(open(sys.argv[1]))
cap = C.conv_cap()
bad = [k for k, v in (d.get("tiers") or {}).items() if not (0 < float(v.get("mult", 1.0)) <= cap + 1e-9)]
json.load(open("data/conviction/conviction_latest.json"))
sys.exit(1 if bad else 0)
EOF
then
  log "post-check failed (mult outside (0, cap] or unreadable output) — restoring tiers backup"; cp -p "$BK" "$T"; exit 5
fi
tail -4 "$ROOT/data/conviction/run_$D.log"
push() { rsync -az -e "ssh -S none -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new" "$ROOT/data/conviction/conviction_latest.json" "$ROOT/data/conviction/proof_$D.md" "$1:binance-sandbox/data/conviction/" 2>/dev/null; }
# sandboxes get the data for visibility; their backtest code never sizes from it (board gain% is scale-free)
{ push s1-int || push s1-pub; } && log "pushed -> S1" || log "push -> S1 failed (non-fatal)"
push s2 && log "pushed -> s2" || log "push -> s2 failed (non-fatal)"
log "done"
exit 0
