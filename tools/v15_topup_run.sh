#!/bin/bash
# v15_topup_run — drive tools/v15_xlsx_topup.py across the tradeable universe, in chunks, with a JIT NPZ refresh
# in front of every chunk. Runs ON THE MAC because only the Mac can reach the exchanges:
#   crypto: S1's REST is 418-banned, so its NPZ can only append from the canonical store the Mac pushes
#           (tools/klines_canonical.py tail -> tools/klines_canonical_push.sh). Measured 2026-10-16: a crypto NPZ
#           goes stale again every 15 min, so a long run MUST re-refresh per chunk or later sym_sides refuse.
#   stocks: only s2 reaches Tradier (S1 answers EXCHANGE_BAR_NOT_READY for every stock), and the market is shut, so
#           one refresh per symbol holds for the whole run.
# The measurement and the publish both happen on the worker; the Mac only sequences and never authors per_sym.
#
# USAGE
#   bash tools/v15_topup_run.sh crypto 6 "--yellow-mode best"            # dry run, chunks of 6
#   V15_TOPUP_APPLY=1 bash tools/v15_topup_run.sh crypto 6 "--yellow-mode best"
#   V15_TOPUP_APPLY=1 bash tools/v15_topup_run.sh stocks 8 "--yellow-mode best --wt"
# Pause dispatch first (06 §10) and restore after:
#   python3 tools/v15_suspect_row_rerun.py --pause s1,s2   /   --resume
set -u
VENUE="${1:-crypto}"
CHUNK="${2:-6}"
EXTRA="${3:---yellow-mode best}"
APPLY=""
[ "${V15_TOPUP_APPLY:-0}" = "1" ] && APPLY="--apply"
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
TAG="${V15_TOPUP_TAG:-TOPUP_$(date -u +%Y%m%dT%H%M%SZ)}"
HOST_CRYPTO=s1-pub
HOST_STOCKS=s2
SSH="ssh -S none -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
PY=$ROOT/.venv/bin/python
LOG=/tmp/v15_topup_run_${TAG}.log
say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

if [ "$VENUE" = "crypto" ]; then HOST=$HOST_CRYPTO; MODE=crypto; else HOST=$HOST_STOCKS; MODE=stocks; fi
say "=== topup run tag=$TAG venue=$VENUE host=$HOST chunk=$CHUNK apply=${APPLY:-DRY} extra='$EXTRA'"

# --- the target list comes from the tool's own tradeable+triage selection, so one rule decides everywhere ---
# V15_TOPUP_SYMSIDES=a,b,c runs exactly that list (used to finish a paused run sequentially without redoing
# the sym_sides it already published).
if [ -n "${V15_TOPUP_SYMSIDES:-}" ]; then
  TARGETS="$V15_TOPUP_SYMSIDES"
else
TARGETS=$($SSH "$HOST" "cd ~/binance-sandbox && .venv/bin/python - <<'PY'
import sys, json
sys.path.insert(0, '.')
import tools.v15_suspect_row_rerun as RR
class A: sym_sides=''; venue='$VENUE'; tiers=''; limit=0; allow_untradeable=False
print(','.join(RR.select_sym_sides(A())))
PY" 2>/dev/null | tail -1)
fi
[ -z "$TARGETS" ] && { say "no targets — refusing"; exit 1; }
IFS=',' read -ra ALLSS <<< "$TARGETS"
# V15_TOPUP_SHARD=i/n runs shard i of n concurrently on the same host (both hosts are 16-core; one topup process at
# 5-8 threads leaves the box idle). Modulo sharding keeps the triage tier order inside every shard.
SHARD="${V15_TOPUP_SHARD:-1/1}"
SI=${SHARD%%/*}; SN=${SHARD##*/}
SS=()
for idx in "${!ALLSS[@]}"; do
  [ $(( idx % SN )) -eq $(( SI - 1 )) ] && SS+=("${ALLSS[$idx]}")
done
say "targets: ${#SS[@]} of ${#ALLSS[@]} sym_sides (shard $SI/$SN)"

# --- the published sets live only on the orchestrator (s2 holds 0 publish rows) -> export once, push once ---
$SSH "$HOST_CRYPTO" "cd ~/binance-sandbox && .venv/bin/python -u tools/v15_export_start_sets.py --published --venue $VENUE --out data/reports/v15_published_sets_${VENUE}.json" | tee -a "$LOG"
if [ "$HOST" != "$HOST_CRYPTO" ]; then
  rsync -az --timeout=60 -e "$SSH" "$HOST_CRYPTO:binance-sandbox/data/reports/v15_published_sets_${VENUE}.json" "/tmp/v15_published_sets_${VENUE}.json" \
    && rsync -az --timeout=60 -e "$SSH" "/tmp/v15_published_sets_${VENUE}.json" "$HOST:binance-sandbox/data/reports/v15_published_sets_${VENUE}.json" \
    && say "published sets relayed $HOST_CRYPTO -> $HOST"
fi

i=0
while [ $i -lt ${#SS[@]} ]; do
  BATCH=("${SS[@]:$i:$CHUNK}")
  i=$((i + CHUNK))
  # BSD sed (macOS) has no \| alternation in a basic regex, so one expression silently stripped NOTHING and the
  # refresh was handed SYM_SIDES: "npz 1INCHUSDT_SHORT ... tail=MISSING" for every symbol (2026-10-16).
  SYMS=$(printf '%s\n' "${BATCH[@]}" | sed -e 's/_LONG$//' -e 's/_SHORT$//' | sort -u | paste -sd, -)
  LIST=$(printf '%s,' "${BATCH[@]}"); LIST=${LIST%,}
  say "--- chunk $((i / CHUNK)): ${#BATCH[@]} sym_sides ($SYMS)"
  # USER 2026-10-16: "make sure ONLY tradeable_keys are considered (or tradeable stock)". The worker filters with
  # its OWN copy of the universe, and that copy drifts: s2 was 4 days stale at 01:50Z (78 stock targets read as 47)
  # and had drifted again by 04:15Z (4 sym_sides it would have processed are not tradeable on the Mac, 3 tradeable
  # ones it would have skipped). tradeable_keys.json is rewritten continuously, so it is pushed before EVERY chunk,
  # not once per run. Tiny files; the push is cheaper than one wasted board.
  rsync -az --timeout=30 -e "$SSH" tradeable_keys.json symbols_trb_long.json symbols_trb_short.json "$HOST:binance-sandbox/" >> "$LOG" 2>&1 \
    || say "WARN: universe push to $HOST failed — the worker may filter on a stale universe"
  # USER 2026-10-16 ("I see no new results"): the FINAL_0657 round MEASURED 66 crypto + 11 stock sym_sides and
  # published none of them — "source board xlsx is on neither this host nor the peer". The Mac holds 2632 boards
  # (s1 510, s2 939) and no worker can reach it (NAT), so the source workbook the new board is written into must
  # be pushed from here BEFORE the chunk runs. Cheap (1-2 MB per board) next to a thrown-away measurement.
  "$PY" -u tools/v15_topup_push_boards.py --host "$HOST" --symsides "$LIST" >> "$LOG" 2>&1 \
    || say "WARN: source-board push to $HOST failed — measurements may not be publishable"
  if [ "$MODE" = "crypto" ]; then
    # JIT: exchange tail on the Mac -> push just those files -> worker appends. NEVER relax the staleness guard.
    timeout 300 "$PY" -u tools/klines_canonical.py tail --symbols "$SYMS" --rps 4 >> "$LOG" 2>&1
    FILES=""; for s in $(echo "$SYMS" | tr ',' ' '); do [ -f "klines_canonical/${s}_15m.json" ] && FILES="$FILES klines_canonical/${s}_15m.json"; done
    # shellcheck disable=SC2086
    [ -n "$FILES" ] && rsync -az --delay-updates --timeout=120 -e "$SSH" $FILES "$HOST:binance-sandbox/klines_canonical/" >> "$LOG" 2>&1
  fi
  $SSH "$HOST" "cd ~/binance-sandbox && bash tools/npz_refresh_batch.sh '$SYMS' $MODE /tmp/npz_topup_s${SI}.lock" | tee -a "$LOG"
  $SSH "$HOST" "cd ~/binance-sandbox && .venv/bin/python -u tools/v15_xlsx_topup.py --sym-sides '$LIST' --published-sets data/reports/v15_published_sets_${VENUE}.json --workers 8 $EXTRA $APPLY --tag ${TAG}_c$((i / CHUNK))" | tee -a "$LOG"
done
say "=== done — reports on $HOST: data/reports/v15_xlsx_topup_${TAG}_c*.json"
say "REMINDER: restore dispatch with  python3 tools/v15_suspect_row_rerun.py --resume"
