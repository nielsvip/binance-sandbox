#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SWEEP SCHEDULER — 5-phase autonomous sweep pipeline
# Runs 24/7 across S1+S2, auto-chains phases, never idles.
#
# Usage: nohup bash sweep_scheduler.sh [s1|s2] > /tmp/sweep_scheduler.log 2>&1 &
#
# Phase 1: Fast discovery (12 sym, 3072 cfgs) → top 50
# Phase 2: Full-symbol validation (top 50 × all sym) → survivors
# Phase 3: Exit tuning (survivors × exit grid) → candidates
# Phase 4: Safety + scalp (candidates × safety switches)
# Phase 5: Full combinatorial (100K+ cfgs, continuous)
# Phase 6: Winner optimization (modify losers, cross-validate)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

MACHINE="${1:-auto}"
LOGDIR="/tmp/sweep_schedule_logs"
mkdir -p "$LOGDIR"

# Auto-detect machine
if [[ "$MACHINE" == "auto" ]]; then
    CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
    if [[ $CORES -ge 12 ]]; then
        MACHINE="s1"
    else
        MACHINE="s2"
    fi
fi

# Machine-specific config
if [[ "$MACHINE" == "s1" ]]; then
    PY="/home/niels/.conda/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    WORKERS_CRYPTO=8
    WORKERS_TRADIER=4
    TOTAL_CORES=16
elif [[ "$MACHINE" == "s2" ]]; then
    PY="/home/niels/miniconda3/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    WORKERS_CRYPTO=0  # S2 = stocks only per server roles
    WORKERS_TRADIER=8
    TOTAL_CORES=8
else
    PY="/opt/anaconda3/envs/binance_env/bin/python"
    BASE="/Users/niels/Documents/binance"
    WORKERS_CRYPTO=3
    WORKERS_TRADIER=3
    TOTAL_CORES=10
fi

SWEEP_DIR="$BASE/data/sweep_results"
mkdir -p "$SWEEP_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] [$MACHINE] $*" | tee -a "$LOGDIR/scheduler.log"; }
phase_marker() { echo "$1" > "$SWEEP_DIR/.current_phase_${MACHINE}"; }

# ═══ HELPER: extract top N configs from CSV by Sharpe ═══
extract_top_configs() {
    local csv="$1" n="$2" out="$3"
    $PY -c "
import csv, json, sys
rows = []
with open('$csv') as f:
    reader = csv.DictReader(f)
    for r in reader:
        try:
            s = float(r.get('sharpe', 0))
            t = int(r.get('trades', 0))
            if t > 10 and s > 0:
                rows.append(r)
        except: pass
rows.sort(key=lambda x: -float(x.get('sharpe', 0)))
top = rows[:$n]
configs = []
for r in top:
    cfg = {}
    for k, v in r.items():
        if k.startswith('cfg_'):
            key = k[4:]
            try: cfg[key] = json.loads(v)
            except: cfg[key] = v
    configs.append(cfg)
with open('$out', 'w') as f:
    json.dump(configs, f, indent=2)
print(f'Extracted {len(configs)} top configs (of {len(rows)} positive) to $out')
" 2>&1 | tee -a "$LOGDIR/scheduler.log"
}

# ═══ HELPER: run a quick sweep and wait for completion ═══
run_sweep() {
    local mode="$1" tier="$2" symbols="$3" workers="$4" start="$5" label="$6"
    local extra_args="${7:-}"
    local logfile="$LOGDIR/${label}_$(date -u +%Y%m%d_%H%M).log"

    log "STARTING: $label | mode=$mode tier=$tier symbols=$symbols workers=$workers start=$start"

    $PY -u "$BASE/v8_quick_sweep.py" \
        --mode "$mode" \
        --symbols "$symbols" \
        --start "$start" \
        --tier "$tier" \
        --workers "$workers" \
        $extra_args \
        > "$logfile" 2>&1

    local rc=$?
    log "FINISHED: $label | exit=$rc | log=$logfile"
    return $rc
}

# ═══ HELPER: run top-N configs on full symbols ═══
run_top_configs_full() {
    local mode="$1" configs_json="$2" workers="$3" start="$4" label="$5" symbols="$6"
    local logfile="$LOGDIR/${label}_$(date -u +%Y%m%d_%H%M).log"

    log "STARTING VALIDATION: $label | configs=$(cat $configs_json | $PY -c 'import json,sys;print(len(json.load(sys.stdin)))') | symbols=$symbols"

    # Run each config through quick engine
    $PY -c "
import json, subprocess, os, sys, time, csv
from pathlib import Path

BASE = Path('$BASE')
configs = json.load(open('$configs_json'))
results = []
for i, cfg in enumerate(configs):
    override = BASE / 'data' / 'sweep_results' / f'override_val_{i}.json'
    with open(override, 'w') as f: json.dump(cfg, f)
    env = os.environ.copy()
    env['V8_OVERRIDE_FILE'] = str(override)
    cmd = ['$PY', str(BASE / 'v8_quick_engine.py'), '--mode', '$mode', '--symbols', '$symbols', '--start', '$start']
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600, cwd=str(BASE))
        elapsed = time.time() - t0
        # Parse V8_RESULT line
        for line in proc.stdout.splitlines():
            if 'V8_RESULT:' in line:
                toks = dict(t.split('=',1) for t in line.split() if '=' in t)
                results.append({
                    'config_idx': i, 'sharpe': float(toks.get('sharpe',0)), 'pnl': float(toks.get('pnl',0)),
                    'trades': int(toks.get('trades',0)), 'elapsed': round(elapsed,1), **{f'cfg_{k}':v for k,v in cfg.items()}
                })
                break
    except Exception as e:
        results.append({'config_idx': i, 'sharpe': 0, 'pnl': 0, 'trades': 0, 'error': str(e)})
    override.unlink(missing_ok=True)
    print(f'  [{i+1}/{len(configs)}] sharpe={results[-1].get(\"sharpe\",0):.3f} trades={results[-1].get(\"trades\",0)}', flush=True)

# Write CSV
csv_path = BASE / 'data' / 'sweep_results' / f'$label.csv'
if results:
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys(), extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(results, key=lambda x: -x.get('sharpe',0)))
    print(f'Saved {len(results)} results to {csv_path}')
" > "$logfile" 2>&1

    log "FINISHED VALIDATION: $label | log=$logfile"
}

# ═══════════════════════════════════════════════════════════════
# PHASE 1: Fast discovery
# ═══════════════════════════════════════════════════════════════
phase1() {
    phase_marker "PHASE1_FAST_DISCOVERY"
    log "═══ PHASE 1: Fast discovery (12 sym × 3072 cfgs) ═══"

    if [[ $WORKERS_CRYPTO -gt 0 ]]; then
        run_sweep crypto entry_gates fast $WORKERS_CRYPTO 2022-01-01 "p1_crypto_entry" "--resume" || true
    fi
    run_sweep tradier entry_gates fast $WORKERS_TRADIER 2024-01-01 "p1_tradier_entry" "--resume" || true

    # Find latest CSV results
    local crypto_csv=$(ls -t "$SWEEP_DIR"/v8_quick_crypto_entry_gates_*.csv 2>/dev/null | head -1)
    local tradier_csv=$(ls -t "$SWEEP_DIR"/v8_quick_tradier_entry_gates_*.csv 2>/dev/null | head -1)

    if [[ -n "$crypto_csv" ]]; then
        extract_top_configs "$crypto_csv" 50 "$SWEEP_DIR/top50_crypto_entry.json"
    fi
    if [[ -n "$tradier_csv" ]]; then
        extract_top_configs "$tradier_csv" 50 "$SWEEP_DIR/top50_tradier_entry.json"
    fi
}

# ═══════════════════════════════════════════════════════════════
# PHASE 2: Full-symbol validation
# ═══════════════════════════════════════════════════════════════
phase2() {
    phase_marker "PHASE2_FULL_SYMBOL_VALIDATION"
    log "═══ PHASE 2: Full-symbol validation (top 50 × all symbols) ═══"

    if [[ $WORKERS_CRYPTO -gt 0 ]] && [[ -f "$SWEEP_DIR/top50_crypto_entry.json" ]]; then
        # All crypto symbols
        local crypto_syms=$(ls "$BASE/backtest_v8/indicators/"*USDT.npz "$BASE/backtest_v8/indicators/"*USDC.npz 2>/dev/null | xargs -I{} basename {} .npz | paste -sd, -)
        run_top_configs_full crypto "$SWEEP_DIR/top50_crypto_entry.json" $WORKERS_CRYPTO 2022-01-01 "p2_crypto_fullsym" "$crypto_syms"
    fi

    if [[ -f "$SWEEP_DIR/top50_tradier_entry.json" ]]; then
        # All stock symbols
        local stock_syms=$(ls "$BASE/backtest_v8/indicators/"*.npz 2>/dev/null | xargs -I{} basename {} .npz | grep -v "USDT\|USDC" | paste -sd, -)
        run_top_configs_full tradier "$SWEEP_DIR/top50_tradier_entry.json" $WORKERS_TRADIER 2024-01-01 "p2_tradier_fullsym" "$stock_syms"
    fi

    # Extract survivors (>30% of fast Sharpe retained)
    for mode in crypto tradier; do
        local fast_csv="$SWEEP_DIR/top50_${mode}_entry.json"
        local full_csv="$SWEEP_DIR/p2_${mode}_fullsym.csv"
        if [[ -f "$full_csv" ]]; then
            extract_top_configs "$full_csv" 20 "$SWEEP_DIR/top20_${mode}_validated.json"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════
# PHASE 3: Exit tuning
# ═══════════════════════════════════════════════════════════════
phase3() {
    phase_marker "PHASE3_EXIT_TUNING"
    log "═══ PHASE 3: Exit tuning (top 20 × exit grid × all symbols) ═══"

    if [[ $WORKERS_CRYPTO -gt 0 ]]; then
        run_sweep crypto exit_tuning fast $WORKERS_CRYPTO 2022-01-01 "p3_crypto_exit" "--resume" || true
    fi
    run_sweep tradier exit_tuning fast $WORKERS_TRADIER 2024-01-01 "p3_tradier_exit" "--resume" || true

    # Extract exit winners
    for mode in crypto tradier; do
        local csv=$(ls -t "$SWEEP_DIR"/v8_quick_${mode}_exit_tuning_*.csv 2>/dev/null | head -1)
        if [[ -n "$csv" ]]; then
            extract_top_configs "$csv" 10 "$SWEEP_DIR/top10_${mode}_exit.json"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════
# PHASE 4: Safety + Scalp
# ═══════════════════════════════════════════════════════════════
phase4() {
    phase_marker "PHASE4_SAFETY_SCALP"
    log "═══ PHASE 4: Safety switches + Scalp V2 ═══"

    # Safety switch sweep uses scalar V8 (tier 27) — quick engine doesn't have safety hooks yet
    if [[ $WORKERS_TRADIER -gt 0 ]]; then
        $PY -u "$BASE/backtest_v8_sweep.py" --mode tradier --account trb --start 2024-06-01 \
            --workers $WORKERS_TRADIER --tier 27 --symbols fast --resume \
            > "$LOGDIR/p4_tradier_safety_$(date -u +%Y%m%d_%H%M).log" 2>&1 || true
        log "Phase 4 safety sweep complete"
    fi
}

# ═══════════════════════════════════════════════════════════════
# PHASE 5: Full combinatorial (continuous, never ends)
# ═══════════════════════════════════════════════════════════════
phase5() {
    phase_marker "PHASE5_FULL_COMBINATORIAL"
    log "═══ PHASE 5: Full combinatorial (100K+ cfgs, continuous) ═══"

    # Crypto full grid
    if [[ $WORKERS_CRYPTO -gt 0 ]]; then
        run_sweep crypto full fast $WORKERS_CRYPTO 2022-01-01 "p5_crypto_full" "--resume" || true

        # Full symbols on any configs that haven't been validated
        local crypto_syms=$(ls "$BASE/backtest_v8/indicators/"*USDT.npz "$BASE/backtest_v8/indicators/"*USDC.npz 2>/dev/null | xargs -I{} basename {} .npz | paste -sd, -)
        run_sweep crypto full "$crypto_syms" $WORKERS_CRYPTO 2022-01-01 "p5_crypto_full_allsym" "--resume" || true
    fi

    # Tradier full grid
    local stock_syms=$(ls "$BASE/backtest_v8/indicators/"*.npz 2>/dev/null | xargs -I{} basename {} .npz | grep -v "USDT\|USDC" | paste -sd, -)
    run_sweep tradier full fast $WORKERS_TRADIER 2024-01-01 "p5_tradier_full" "--resume" || true
    run_sweep tradier full "$stock_syms" $WORKERS_TRADIER 2024-01-01 "p5_tradier_full_allsym" "--resume" || true
}

# ═══════════════════════════════════════════════════════════════
# PHASE 6: Winner optimization (modify losers, cross-validate)
# ═══════════════════════════════════════════════════════════════
phase6() {
    phase_marker "PHASE6_WINNER_OPTIMIZATION"
    log "═══ PHASE 6: Winner optimization — mutate losers, cross-validate winners ═══"

    # Generate mutated configs from Phase 5 losers
    $PY -c "
import csv, json, random, sys
from pathlib import Path

SWEEP_DIR = Path('$SWEEP_DIR')
# Load best and worst from Phase 5
for mode in ['crypto', 'tradier']:
    csv_path = list(SWEEP_DIR.glob(f'v8_quick_{mode}_full_*.csv'))
    if not csv_path: continue
    csv_path = sorted(csv_path)[-1]
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            try: rows.append(r)
            except: pass
    rows.sort(key=lambda x: -float(x.get('sharpe',0)))
    winners = rows[:20]
    losers = [r for r in rows if float(r.get('sharpe',0)) < 0 and int(r.get('trades',0)) > 50][:50]

    # Strategy: take loser config, swap ONE param with winner's value
    mutants = []
    cfg_keys = [k for k in rows[0] if k.startswith('cfg_')]
    for loser in losers[:20]:
        for winner in winners[:5]:
            for k in cfg_keys:
                if loser.get(k) != winner.get(k):
                    mutant = {kk[4:]: loser[kk] for kk in cfg_keys}
                    mutant[k[4:]] = winner[k]
                    mutants.append(mutant)
                    break  # one mutation per winner-loser pair

    out = SWEEP_DIR / f'mutant_configs_{mode}.json'
    with open(out, 'w') as f: json.dump(mutants[:100], f, indent=2)
    print(f'{mode}: generated {len(mutants[:100])} mutant configs from {len(losers)} losers × {len(winners)} winners')
" 2>&1 | tee -a "$LOGDIR/scheduler.log"

    # Run mutants
    for mode in crypto tradier; do
        local mutant_file="$SWEEP_DIR/mutant_configs_${mode}.json"
        if [[ -f "$mutant_file" ]]; then
            local workers=$([[ "$mode" == "crypto" ]] && echo $WORKERS_CRYPTO || echo $WORKERS_TRADIER)
            local start=$([[ "$mode" == "crypto" ]] && echo "2022-01-01" || echo "2024-01-01")
            [[ $workers -eq 0 ]] && continue
            run_top_configs_full "$mode" "$mutant_file" "$workers" "$start" "p6_${mode}_mutants" "fast"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP — phases chain automatically, restarts from Phase 5
# ═══════════════════════════════════════════════════════════════
main() {
    log "═══════════════════════════════════════════════════════════"
    log "SWEEP SCHEDULER STARTED on $MACHINE ($(nproc 2>/dev/null || echo '?') cores)"
    log "═══════════════════════════════════════════════════════════"

    # Check if Phase 1 results already exist (resume support)
    local has_p1_crypto=$(ls "$SWEEP_DIR"/v8_quick_crypto_entry_gates_*.csv 2>/dev/null | wc -l)
    local has_p1_tradier=$(ls "$SWEEP_DIR"/v8_quick_tradier_entry_gates_*.csv 2>/dev/null | wc -l)

    if [[ $has_p1_crypto -eq 0 ]] || [[ $has_p1_tradier -eq 0 ]]; then
        phase1
    else
        log "Phase 1 CSVs exist — extracting tops and skipping to Phase 2"
        local crypto_csv=$(ls -t "$SWEEP_DIR"/v8_quick_crypto_entry_gates_*.csv 2>/dev/null | head -1)
        local tradier_csv=$(ls -t "$SWEEP_DIR"/v8_quick_tradier_entry_gates_*.csv 2>/dev/null | head -1)
        [[ -n "$crypto_csv" ]] && extract_top_configs "$crypto_csv" 50 "$SWEEP_DIR/top50_crypto_entry.json"
        [[ -n "$tradier_csv" ]] && extract_top_configs "$tradier_csv" 50 "$SWEEP_DIR/top50_tradier_entry.json"
    fi

    phase2
    phase3
    phase4

    # Continuous loop: Phase 5 → Phase 6 → repeat
    local cycle=1
    while true; do
        log "═══ CONTINUOUS CYCLE $cycle ═══"
        phase5
        phase6
        cycle=$((cycle + 1))
        log "Cycle $cycle complete. Restarting continuous loop..."
    done
}

main
