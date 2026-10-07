#!/bin/bash
# Grey-switch rewire scalar proof: run backtest_v12_engine (REAL ez_manage / tradier_manage
# .process_position) with a probe on vec_decisions.dc_channel_exits, optionally forcing config keys.
#
# usage (repo root): tools/grey_switch_scalar_ab.sh MODE SYMBOL START TAG [OVERRIDE_JSON]
#   MODE=tradier|crypto, START=YYYY-MM-DD, OVERRIDE_JSON e.g. {"DAYTRADE_DC_TARGET_TF":"15m,1h"}
# Outputs: $OUT/probe_TAG.json (hook call/fire counters), $OUT/reasons_TAG.txt (ledger reason histogram)
# Env: BT_ENGINE (default backtest_v12_engine.py), NPZ_DIR (default backtest_v8/indicators), OUT (default /tmp)
# Diagnostic only — single-symbol replay, never a promotion metric.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
MODE=$1; SYM=$2; START=$3; TAG=$4; PCFG=${5:-}
OUT=${OUT:-/tmp}
export PYTHONHASHSEED=0 V8_HASHSEED_LOCKED=1 V8_FORCE_REAL=1 V8_DISABLE_PER_SYM=1
export BT_ENGINE=${BT_ENGINE:-backtest_v12_engine.py} PROBE_OUT=$OUT/probe_$TAG.json
if [ -n "$PCFG" ]; then
  export PROBE_CFG=$PCFG
  if [ "$MODE" = "tradier" ]; then export V8_SWEEP_MODE=1 V8_BACKTEST_OVERRIDE_PRECEDENCE=1 V8_OVERRIDE_FILE=$PCFG; fi
fi
ACC=""
if [ "$MODE" = "tradier" ]; then ACC="--account trb"; fi
timeout 2400 python3 tools/grey_switch_scalar_probe.py --mode "$MODE" $ACC --symbols "$SYM" --start "$START" --npz-dir "${NPZ_DIR:-$ROOT/backtest_v8/indicators}" > "$OUT/run_$TAG.log" 2>&1
L=$(grep -o "/[^ ]*\.jsonl" "$OUT/run_$TAG.log" | tail -1)
echo "== $TAG $(grep -o 'V8_RESULT: pool_sharpe=[^ ]* [^ ]* [^ ]* [^ ]* [^ ]*' "$OUT/run_$TAG.log" | tail -1)"
cat "$OUT/probe_$TAG.json" 2>/dev/null | tr -d '\n '; echo
grep -o '"reason": *"[A-Z_]*' "$L" | sort | uniq -c | sort -rn | head -12 | tee "$OUT/reasons_$TAG.txt"
