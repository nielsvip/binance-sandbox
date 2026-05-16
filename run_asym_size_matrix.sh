#!/usr/bin/env bash
# run_asym_size_matrix.sh — Test side-asymmetric sizing on tech_ai_chips.
# 7 (LONG_MULT, SHORT_MULT) combinations × tech_ai_chips × 2.13 yr realized.

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
RUN_TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="$WORKDIR/data/sweep_results/asym_size_matrix_${RUN_TS}"
mkdir -p "$OUT_DIR"
MASTER_LOG="$HOME/logs/asym_size_${RUN_TS}.log"
PARALLEL=${PARALLEL:-3}
START_DATE=${START_DATE:-2023-01-01}

TECH_SYMS="NVDA,AMD,AVGO,MU,INTC,LRCX,TXN,ARM,PLTR,CRWD,CIBR,IBM,SAP,ACN,MA,PYPL,TTD,WDAY,FIVN,AXON,OLED,SNDK,RBLX,ASTS,CRWV,MSTR,GOOGL,ROBO,BOTZ"

# (variant_name | LONG_MULT | SHORT_MULT)
VARIANTS=(
"S0_baseline_1.0_1.0|1.0|1.0"
"S1_long15_short05|1.5|0.5"
"S2_long15_short025|1.5|0.25"
"S3_long15_short01|1.5|0.10"
"S4_long20_short025|2.0|0.25"
"S5_long10_short025|1.0|0.25"
"S6_long10_short05|1.0|0.5"
)

echo "[$(date '+%H:%M:%S')] === Asym-size matrix start (${#VARIANTS[@]} variants × tech_ai_chips, P=$PARALLEL, start=$START_DATE) ===" | tee -a "$MASTER_LOG"

run_cell() {
    local vname="$1" long_m="$2" short_m="$3"
    local cell_log="$OUT_DIR/${vname}.log"
    local cell_json="$OUT_DIR/${vname}.summary.json"
    local t0=$(date +%s)
    cd "$WORKDIR"
    timeout 1500 "$PYTHON" v8_vec_sweep.py \
        --mode tradier --account trb \
        --symbols "$TECH_SYMS" \
        --start "$START_DATE" \
        --no-history \
        --override "LONG_SIZE_MULT=$long_m" \
        --override "SHORT_SIZE_MULT=$short_m" \
        > "$cell_log" 2>&1
    local rc=$? t1=$(date +%s)
    local elapsed=$((t1-t0))
    local canon=$(grep -E '^pool_sharpe=' "$cell_log" | tail -1)
    "$PYTHON" -c "
import json, re
canon='''$canon'''
out={'variant':'$vname','long_mult':$long_m,'short_mult':$short_m,'rc':$rc,'elapsed_s':$elapsed,'canonical_line':canon}
for pat,k,c in [(r'pool_sharpe=([+\-\d.]+)','pool_sharpe',float),(r'sym_sharpe=([+\-\d.]+)','sym_sharpe',float),(r'gain_per_yr=([+\-\d.]+)','gain_per_yr',float),(r'gain_sym_yr=([+\-\d.]+)','gain_sym_yr',float),(r'avg_gain_trade=([+\-\d.]+)','avg_gain_trade',float),(r'trades=([\d,]+)','trades',lambda s:int(s.replace(',',''))),(r'dd=([+\-\d.]+)','max_dd_pct',float),(r'n_syms=(\d+)','n_syms',int),(r'years=([+\-\d.]+)','years',float)]:
    m=re.search(pat,canon)
    if m:
        try: out[k]=c(m.group(1))
        except: out[k]=None
with open('$cell_json','w') as f: json.dump(out,f,indent=2)
print(f'DONE {out[\"variant\"]} L={out[\"long_mult\"]} S={out[\"short_mult\"]} sharpe={out.get(\"pool_sharpe\")} sym={out.get(\"sym_sharpe\")} gain={out.get(\"gain_per_yr\")} tr={out.get(\"trades\")} dd={out.get(\"max_dd_pct\")} t={out[\"elapsed_s\"]}s')
"
}
export -f run_cell
export TECH_SYMS START_DATE OUT_DIR PYTHON WORKDIR

# Dispatch in parallel
printf '%s\n' "${VARIANTS[@]}" | xargs -I {} -P "$PARALLEL" bash -c '
v="{}"
vname="${v%%|*}"; rest="${v#*|}"
lm="${rest%%|*}"; sm="${rest#*|}"
run_cell "$vname" "$lm" "$sm"
' 2>&1 | tee -a "$MASTER_LOG"

echo "[$(date '+%H:%M:%S')] === Matrix complete. Aggregating... ===" | tee -a "$MASTER_LOG"

"$PYTHON" - <<PYEOF
import json, glob, csv
out_dir = "$OUT_DIR"
rows = []
for f in sorted(glob.glob(out_dir + "/*.summary.json")):
    with open(f) as fh: rows.append(json.load(fh))
csv_path = out_dir + "/ASYM_SIZE_RESULTS.csv"
cols = ["variant","long_mult","short_mult","trades","n_syms","years","pool_sharpe","sym_sharpe","avg_gain_trade","gain_per_yr","gain_sym_yr","max_dd_pct","elapsed_s","rc","canonical_line"]
with open(csv_path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    [w.writerow(r) for r in rows]
print(f"WROTE {csv_path}  rows={len(rows)}")
PYEOF

echo "[$(date '+%H:%M:%S')] === Done. CSV: $OUT_DIR/ASYM_SIZE_RESULTS.csv ===" | tee -a "$MASTER_LOG"
