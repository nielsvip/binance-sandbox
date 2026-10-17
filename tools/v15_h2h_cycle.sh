#!/bin/bash
# v15_h2h_cycle — the standing "measured, never claimed" go-live gate.
#
# USER 2026-10-16: "make sure no more exaggeration of technicals and gains can ever take place".
# Measured that day: 107 of 144 published crypto claims were overstated on today's NPZ (median 15.1 pp, max
# 77.4 pp — ZECUSDC_LONG claimed +84.11 and measured +6.74; RLCUSDT_SHORT claimed +41.42 and measured -23.19).
# A claim is honest in the window it was measured in and stops being honest as that window slides, so nothing
# here ever compares stored numbers: the incumbent and the latest published candidate are re-measured in ONE
# process on ONE NPZ and the winner must win by more than the 0.48 board-nondeterminism tolerance.
#
# Layout matters (see memory worker-db-is-not-live-20261016): publish rows exist only on S1, live sets only on
# the Mac, fresh stock NPZs only on s2. So the Mac exports its live sets, the worker measures, the Mac applies.
#
#   bash tools/v15_h2h_cycle.sh crypto          # dry run (measures, writes nothing)
#   V15_H2H_APPLY=1 bash tools/v15_h2h_cycle.sh stocks
set -u
VENUE="${1:-crypto}"
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
PY=$(bash "$ROOT/tools/pyinterp.sh")
SSH="ssh -S none -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
if [ "$VENUE" = "crypto" ]; then HOST=s1-pub; else HOST=s2; fi
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG=/tmp/v15_h2h_cycle_${VENUE}.log
say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

say "=== h2h cycle venue=$VENUE host=$HOST apply=${V15_H2H_APPLY:-0}"
LIST=/tmp/h2h_sides_${VENUE}.txt
"$PY" - "$VENUE" "$LIST" <<'PYEOF' >> "$LOG" 2>&1 || { say "universe build failed"; exit 1; }
import json, sys
venue, out = sys.argv[1], sys.argv[2]
sides = set()
if venue == "crypto":
    for k in json.load(open("tradeable_keys.json")):
        if isinstance(k, str):
            k = k.split(":", 1)[-1]
            if k.endswith(("_LONG", "_SHORT")) and k.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1")):
                sides.add(k)
else:
    for f, side in (("symbols_trb_long.json", "LONG"), ("symbols_trb_short.json", "SHORT")):
        d = json.load(open(f))
        for s in (d if isinstance(d, list) else d.get("symbols") or []):
            if isinstance(s, str):
                sides.add(f"{s}_{side}")
open(out, "w").write("\n".join(sorted(sides)) + "\n")
print(f"[h2h-cycle] {venue}: {len(sides)} tradeable sym_sides")
PYEOF
[ -s "$LIST" ] || { say "empty universe — refusing"; exit 1; }

LIVE=/tmp/h2h_live_${VENUE}.json
"$PY" tools/v15_golive_headtohead.py export-live --sym-sides-file "$LIST" --out "$LIVE" | tee -a "$LOG"
[ -s "$LIVE" ] || { say "live export empty — refusing (never measure against a worker's own db)"; exit 1; }

rsync -az --timeout=300 -e "$SSH" tools/v15_golive_headtohead.py "$HOST:binance-sandbox/tools/" >> "$LOG" 2>&1
rsync -az --timeout=600 -e "$SSH" "$LIVE" "$HOST:$LIVE" >> "$LOG" 2>&1 || { say "live-set push failed"; exit 1; }

OUT=/tmp/h2h_${VENUE}_${TS}.json
$SSH "$HOST" "cd ~/binance-sandbox && .venv/bin/python -u tools/v15_golive_headtohead.py measure --live-sets $LIVE --out $OUT" 2>&1 | tail -4 | tee -a "$LOG"
rsync -az --timeout=300 -e "$SSH" "$HOST:$OUT" "$OUT" >> "$LOG" 2>&1 || { say "decision pull failed"; exit 1; }

if [ "${V15_H2H_APPLY:-0}" = "1" ]; then
  cp -p data/hourly_reconfig/per_sym_store.db "backups/before_h2h_${VENUE}_${TS}.db" 2>/dev/null
  "$PY" tools/v15_golive_headtohead.py apply --in "$OUT" --apply 2>&1 | tail -30 | tee -a "$LOG"
else
  "$PY" tools/v15_golive_headtohead.py apply --in "$OUT" 2>&1 | tail -30 | tee -a "$LOG"
fi
say "=== done — decisions $OUT"
