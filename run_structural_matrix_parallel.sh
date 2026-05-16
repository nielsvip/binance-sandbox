#!/usr/bin/env bash
# run_structural_matrix_parallel.sh — Parallel A/B sector matrix for structural gates.
# 8 variants × 9 sectors = 72 cells, run 4 in parallel.

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
RUN_TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="$WORKDIR/data/sweep_results/structural_matrix_${RUN_TS}"
mkdir -p "$OUT_DIR"
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
MASTER_LOG="$LOG_DIR/structural_matrix_${RUN_TS}.log"
PARALLEL=${PARALLEL:-4}
START_DATE=${START_DATE:-2023-01-01}
TIMEOUT_S=${TIMEOUT_S:-2400}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Parallel matrix start TS=$RUN_TS p=$PARALLEL start=$START_DATE to=${TIMEOUT_S}s ===" | tee -a "$MASTER_LOG"
echo "Out: $OUT_DIR" | tee -a "$MASTER_LOG"

STOCK_SECTORS=(tech_ai_chips energy_oil_gas precious_metals base_metals_mining uranium_nuclear agriculture_fertilizer defense_aerospace consumer_media commodities_crypto_etf)
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

# Build tasks.tsv: variant_name<TAB>overrides<TAB>sector<TAB>symbols
TASK_FILE="$OUT_DIR/tasks.tsv"
: > "$TASK_FILE"
for variant_def in "${VARIANTS[@]}"; do
    vname="${variant_def%%|*}"; ovr="${variant_def#*|}"
    for sector in "${STOCK_SECTORS[@]}"; do
        syms=$("$PYTHON" -c "
import json
with open('$WORKDIR/sectors_tradier.json') as f: d=json.load(f)
print(','.join(d.get('$sector',[])), end='')")
        [[ -z "$syms" ]] && continue
        printf '%s\t%s\t%s\t%s\n' "$vname" "$ovr" "$sector" "$syms" >> "$TASK_FILE"
    done
done

N_TASKS=$(wc -l < "$TASK_FILE")
echo "Tasks: $N_TASKS" | tee -a "$MASTER_LOG"

# Cell runner
RUNNER="$OUT_DIR/_run_cell.sh"
cat > "$RUNNER" <<'CELLEOF'
#!/usr/bin/env bash
PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
OUT_DIR="$1"; VNAME="$2"; OVR="$3"; SECTOR="$4"; SYMS="$5"; START="$6"; TIMEOUT_S="$7"
override_args=""
if [[ -n "$OVR" ]]; then
    IFS=',' read -ra K <<< "$OVR"
    for kv in "${K[@]}"; do override_args="$override_args --override $kv"; done
fi
cell_log="$OUT_DIR/${VNAME}__${SECTOR}.log"
cell_json="$OUT_DIR/${VNAME}__${SECTOR}.summary.json"
t0=$(date +%s)
cd "$WORKDIR"
timeout "$TIMEOUT_S" "$PYTHON" v8_vec_sweep.py \
    --mode tradier --account trb \
    --symbols "$SYMS" \
    --start "$START" \
    --no-history \
    $override_args > "$cell_log" 2>&1
rc=$?
t1=$(date +%s)
elapsed=$((t1 - t0))
canon=$(grep -E '^pool_sharpe=' "$cell_log" | tail -1)
"$PYTHON" -c "
import json, re
canon = '''$canon'''
out = {'variant':'$VNAME','sector':'$SECTOR','rc':$rc,'elapsed_s':$elapsed,'canonical_line':canon}
for pat,k,c in [(r'pool_sharpe=([+\-\d.]+)','pool_sharpe',float),(r'sym_sharpe=([+\-\d.]+)','sym_sharpe',float),(r'avg_gain_trade=([+\-\d.]+)','avg_gain_trade',float),(r'gain_per_yr=([+\-\d.]+)','gain_per_yr',float),(r'gain_sym_yr=([+\-\d.]+)','gain_sym_yr',float),(r'trades=([\d,]+)','trades',lambda s:int(s.replace(',',''))),(r'dd=([+\-\d.]+)','max_dd_pct',float),(r'n_syms=(\d+)','n_syms',int),(r'years=([+\-\d.]+)','years',float)]:
    m=re.search(pat,canon)
    if m:
        try: out[k]=c(m.group(1))
        except: out[k]=None
with open('$cell_json','w') as f: json.dump(out,f,indent=2)
print(f'DONE {out[\"variant\"]}/{out[\"sector\"]} rc={out[\"rc\"]} ps={out.get(\"pool_sharpe\")} tr={out.get(\"trades\")} t={out[\"elapsed_s\"]}s')
"
CELLEOF
chmod +x "$RUNNER"

# Dispatch in parallel
while IFS=$'\t' read -r VNAME OVR SECTOR SYMS; do
    printf '%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' "$OUT_DIR" "$VNAME" "$OVR" "$SECTOR" "$SYMS" "$START_DATE" "$TIMEOUT_S" "$RUNNER"
done < "$TASK_FILE" | \
xargs -0 -n 8 -P "$PARALLEL" bash -c 'bash "$7" "$0" "$1" "$2" "$3" "$4" "$5" "$6"' 2>&1 | \
tee -a "$MASTER_LOG"

# Aggregate
"$PYTHON" - <<PYEOF
import json, glob, csv
out_dir = "$OUT_DIR"
rows = []
for f in sorted(glob.glob(f"{out_dir}/*.summary.json")):
    with open(f) as fh: rows.append(json.load(fh))
csv_path = f"{out_dir}/MATRIX_RESULTS.csv"
cols = ["variant","sector","n_syms","trades","years","pool_sharpe","sym_sharpe","avg_gain_trade","gain_per_yr","gain_sym_yr","max_dd_pct","elapsed_s","rc","canonical_line"]
with open(csv_path,"w",newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    [w.writerow(r) for r in rows]
print(f"WROTE {csv_path}  rows={len(rows)}")
PYEOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Matrix complete. CSV: $OUT_DIR/MATRIX_RESULTS.csv ===" | tee -a "$MASTER_LOG"
