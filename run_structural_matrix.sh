#!/usr/bin/env bash
# run_structural_matrix.sh — Sector × variant A/B matrix for structural-pattern gates.
# Runs v8_vec_sweep.py for 8 variants × 9 stock sectors × start=2022-01-01 (~3.4 yr bull+bear).
# Writes one JSONL summary per variant×sector under data/sweep_results/structural_matrix_<TS>/.
# Created 2026-05-16.

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
RUN_TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="$WORKDIR/data/sweep_results/structural_matrix_${RUN_TS}"
mkdir -p "$OUT_DIR"
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
MASTER_LOG="$LOG_DIR/structural_matrix_${RUN_TS}.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$MASTER_LOG"; }

STOCK_SECTORS=(tech_ai_chips energy_oil_gas precious_metals base_metals_mining uranium_nuclear agriculture_fertilizer defense_aerospace consumer_media commodities_crypto_etf)

# variant_name|override list (comma-separated KEY=VAL)
VARIANTS=(
"V0_baseline|"
"V1_spy|SPY_REGIME_GATE_ENABLED=true"
"V2_atr|SIZING_MODE=ATR_PARITY"
"V3_dailytf|DECISION_TF_MODE=DAILY"
"V4_connors|CONNORS_RSI2_OVERLAY_ENABLED=true"
"V5_spy_dailytf|SPY_REGIME_GATE_ENABLED=true,DECISION_TF_MODE=DAILY"
"V6_spy_atr_dailytf|SPY_REGIME_GATE_ENABLED=true,SIZING_MODE=ATR_PARITY,DECISION_TF_MODE=DAILY"
"V7_all_on|SPY_REGIME_GATE_ENABLED=true,SIZING_MODE=ATR_PARITY,DECISION_TF_MODE=DAILY,CONNORS_RSI2_OVERLAY_ENABLED=true"
)

run_cell() {
    local sector="$1"
    local variant_name="$2"
    local overrides="$3"

    local syms
    syms=$("$PYTHON" -c "
import json
with open('$WORKDIR/sectors_tradier.json') as f: d = json.load(f)
print(','.join(d.get('$sector', [])), end='')
")
    if [[ -z "$syms" ]]; then
        log "  SKIP $variant_name/$sector: no symbols"
        return
    fi

    local n_syms
    n_syms=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local cell_log="$OUT_DIR/${variant_name}__${sector}.log"
    local cell_json="$OUT_DIR/${variant_name}__${sector}.summary.json"

    # Build --override args
    local override_args=""
    if [[ -n "$overrides" ]]; then
        IFS=',' read -ra OVR <<< "$overrides"
        for kv in "${OVR[@]}"; do override_args="$override_args --override $kv"; done
    fi

    log ">>> START $variant_name / $sector ($n_syms syms)"
    local t0
    t0=$(date +%s)

    cd "$WORKDIR" || return
    timeout 1800 "$PYTHON" v8_vec_sweep.py \
        --mode tradier --account trb \
        --symbols "$syms" \
        --start 2022-01-01 \
        --no-history \
        $override_args \
        > "$cell_log" 2>&1
    local rc=$?
    local t1
    t1=$(date +%s)
    local elapsed=$((t1 - t0))

    # Parse the canonical line from log
    local canon
    canon=$(grep -E "^pool_sharpe=" "$cell_log" | tail -1)

    # Emit JSON summary
    "$PYTHON" -c "
import json, os, re, sys
canon = '''$canon'''
out = {
    'variant': '$variant_name',
    'sector': '$sector',
    'n_syms_in_sector': $n_syms,
    'rc': $rc,
    'elapsed_s': $elapsed,
    'canonical_line': canon,
}
# Parse canonical
for pat, key, cast in [
    (r'pool_sharpe=([+\-\d.]+)', 'pool_sharpe', float),
    (r'sym_sharpe=([+\-\d.]+)', 'sym_sharpe', float),
    (r'avg_gain_trade=([+\-\d.]+)', 'avg_gain_trade', float),
    (r'gain_per_yr=([+\-\d.]+)', 'gain_per_yr', float),
    (r'gain_sym_yr=([+\-\d.]+)', 'gain_sym_yr', float),
    (r'trades=([\d,]+)', 'trades', lambda s: int(s.replace(',',''))),
    (r'dd=([+\-\d.]+)', 'max_dd_pct', float),
    (r'n_syms=(\d+)', 'n_syms', int),
    (r'years=([+\-\d.]+)', 'years', float),
]:
    m = re.search(pat, canon)
    if m:
        try: out[key] = cast(m.group(1))
        except: out[key] = None
with open('$cell_json','w') as f: json.dump(out, f, indent=2)
print(json.dumps({k:out.get(k) for k in ('variant','sector','pool_sharpe','trades','n_syms','years','max_dd_pct','rc','elapsed_s')}))
" >> "$MASTER_LOG"

    log "<<< DONE  $variant_name / $sector  rc=$rc  elapsed=${elapsed}s  $canon"
}

log "=== Structural matrix starting (${#VARIANTS[@]} variants × ${#STOCK_SECTORS[@]} sectors) ==="
log "Run TS: $RUN_TS  Out: $OUT_DIR"

# Outer loop: variants (so each variant completes across sectors before next)
for variant_def in "${VARIANTS[@]}"; do
    variant_name="${variant_def%%|*}"
    overrides="${variant_def#*|}"
    log "--- Variant: $variant_name  overrides: ${overrides:-(none)} ---"
    for sector in "${STOCK_SECTORS[@]}"; do
        run_cell "$sector" "$variant_name" "$overrides"
    done
done

log "=== Matrix complete. Aggregating... ==="

"$PYTHON" - <<PYEOF
import json, glob, csv
out_dir = "$OUT_DIR"
rows = []
for f in sorted(glob.glob(f"{out_dir}/*.summary.json")):
    with open(f) as fh:
        d = json.load(fh)
    rows.append(d)
# Write CSV
csv_path = f"{out_dir}/MATRIX_RESULTS.csv"
cols = ["variant","sector","n_syms","trades","years","pool_sharpe","sym_sharpe","avg_gain_trade","gain_per_yr","gain_sym_yr","max_dd_pct","elapsed_s","rc","canonical_line"]
with open(csv_path,"w",newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows: w.writerow(r)
print(f"WROTE {csv_path}  rows={len(rows)}")
PYEOF

log "=== Done. CSV: $OUT_DIR/MATRIX_RESULTS.csv ==="
