#!/usr/bin/env bash
# crypto_sector_w_confirm_v5d.sh
# Reruns ALL 7 crypto sectors with the W WT confirmation gate.
# Fixes v5c timeout: server load (gateway_data_broadcaster 52% CPU) slows engine 4×.
# Calibration: 1 sym × 3.5m (2026-02-01) = 1117s (18.6 min). Rate = 3935s/sym/yr.
# start=2026-02-01 → 2 syms × 0.284yr × 3935 = 2234s ≈ 37 min → safe under 3000s.
# 25 total runs × ~37 min ≈ 15 hours.
#
# Sectors: mega_l1(4→2+2+2+1), defi_oracle(4→2+2+2+2), gaming_nft(3→2+2+2),
#          alt_l1(6→2+2+2+2+2+2), ecosystems(2→2+2), infrastructure(3→2+2+2),
#          misc_smallcap(3→2+2+2)  = 25 runs total
#
# Usage (on S1):
#   nohup bash crypto_sector_w_confirm_v5d.sh > ~/logs/crypto_w_confirm_v5d_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
#
# Created: 2026-05-15 (v5d)

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|crypto_sector_w_confirm" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
    local vec_pids
    vec_pids=$(pgrep -af "vec_matrix_runner" 2>/dev/null \
               | grep -v "$$\|grep\|bash -c\|crypto_sector_w_confirm" \
               | awk '{print $1}')
    if [[ -n "$vec_pids" ]]; then
        log "  Killing vec_matrix_runner: $vec_pids"
        echo "$vec_pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
}

wait_for_clear_engines() {
    local waited=0
    while true; do
        local running
        running=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute" 2>/dev/null \
                  | grep -v "$$\|grep\|bash -c\|crypto_sector_w_confirm" | wc -l)
        [[ "$running" -eq 0 ]] && break
        [[ $waited -eq 0 ]] && log "  Waiting for $running competing engine(s)..."
        sleep 30
        waited=$((waited + 30))
    done
    [[ $waited -gt 0 ]] && log "  Engines cleared after ${waited}s."
}

run_sector_syms() {
    local mode="$1"
    local label="$2"
    local tier="$3"
    local syms="$4"
    local start_date="$5"
    local timeout_s="${6:-3300}"

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS
    TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/${mode}_sector_${tier}_${label}_${TS}.log"

    kill_competing_engines
    wait_for_clear_engines

    log ">>> START $mode/$label tier=$tier ($count syms, start=$start_date)"
    cd "$WORKDIR" || { log "ERROR: cannot cd $WORKDIR"; return; }

    V8_USE_VEC_ALL=1 V8_SWEEP_MODE=1 V8_RATE_GUARD_DISABLED=1 \
    "$PYTHON" backtest_v8_sweep.py \
        --mode "$mode" \
        --tier "$tier" \
        --symbols "$syms" \
        --start "$start_date" \
        --workers 1 \
        --timeout "$timeout_s" \
        > "$logfile" 2>&1

    local rc=$?
    log "<<< DONE $mode/$label tier=$tier rc=$rc log=$logfile"
    sleep 30
}

log "=== crypto v5d W-confirm: 2 syms/group, start=2026-02-01, ~37 min/run (calibrated) ==="

# mega_l1: 7 syms → 2+2+2+1
run_sector_syms crypto mega_l1_1 crypto_sector_w_confirm \
    "BTCUSDC,ETHUSDC" 2026-02-01 3300

run_sector_syms crypto mega_l1_2 crypto_sector_w_confirm \
    "SOLUSDC,BNBUSDC" 2026-02-01 3300

run_sector_syms crypto mega_l1_3 crypto_sector_w_confirm \
    "AVAXUSDC,ADAUSDC" 2026-02-01 3300

run_sector_syms crypto mega_l1_4 crypto_sector_w_confirm \
    "XRPUSDC" 2026-02-01 3300

# defi_oracle: 8 syms → 2+2+2+2 (skip GRTUSDT)
run_sector_syms crypto defi_oracle_1 crypto_sector_w_confirm \
    "UNIUSDC,LINKUSDC" 2026-02-01 3300

run_sector_syms crypto defi_oracle_2 crypto_sector_w_confirm \
    "1INCHUSDT,SUSHIUSDT" 2026-02-01 3300

run_sector_syms crypto defi_oracle_3 crypto_sector_w_confirm \
    "COMPUSDT,SNXUSDT" 2026-02-01 3300

run_sector_syms crypto defi_oracle_4 crypto_sector_w_confirm \
    "YFIUSDT,BANDUSDT" 2026-02-01 3300

# gaming_nft: 6 syms → 2+2+2
run_sector_syms crypto gaming_nft_1 crypto_sector_w_confirm \
    "SANDUSDT,MANAUSDT" 2026-02-01 3300

run_sector_syms crypto gaming_nft_2 crypto_sector_w_confirm \
    "AXSUSDT,C98USDT" 2026-02-01 3300

run_sector_syms crypto gaming_nft_3 crypto_sector_w_confirm \
    "CHRUSDT,SKLUSDT" 2026-02-01 3300

# alt_l1: 12 syms → 2+2+2+2+2+2
run_sector_syms crypto alt_l1_1 crypto_sector_w_confirm \
    "DOTUSDT,ATOMUSDT" 2026-02-01 3300

run_sector_syms crypto alt_l1_2 crypto_sector_w_confirm \
    "LTCUSDC,ETCUSDT" 2026-02-01 3300

run_sector_syms crypto alt_l1_3 crypto_sector_w_confirm \
    "XLMUSDT,DASHUSDT" 2026-02-01 3300

run_sector_syms crypto alt_l1_4 crypto_sector_w_confirm \
    "XMRUSDT,XTZUSDT" 2026-02-01 3300

run_sector_syms crypto alt_l1_5 crypto_sector_w_confirm \
    "ALGOUSDT,EGLDUSDT" 2026-02-01 3300

run_sector_syms crypto alt_l1_6 crypto_sector_w_confirm \
    "QTUMUSDT,TRXUSDT" 2026-02-01 3300

# ecosystems: 4 syms → 2+2
run_sector_syms crypto ecosystems_1 crypto_sector_w_confirm \
    "KSMUSDT,VETUSDT" 2026-02-01 3300

run_sector_syms crypto ecosystems_2 crypto_sector_w_confirm \
    "THETAUSDT,CELRUSDT" 2026-02-01 3300

# infrastructure: 6 syms → 2+2+2
run_sector_syms crypto infrastructure_1 crypto_sector_w_confirm \
    "STORJUSDT,HOTUSDT" 2026-02-01 3300

run_sector_syms crypto infrastructure_2 crypto_sector_w_confirm \
    "IOTXUSDT,IOSTUSDT" 2026-02-01 3300

run_sector_syms crypto infrastructure_3 crypto_sector_w_confirm \
    "IOTAUSDT,RLCUSDT" 2026-02-01 3300

# misc_smallcap: 6 syms → 2+2+2
run_sector_syms crypto misc_smallcap_1 crypto_sector_w_confirm \
    "BATUSDT,COTIUSDT" 2026-02-01 3300

run_sector_syms crypto misc_smallcap_2 crypto_sector_w_confirm \
    "RSRUSDT,RVNUSDT" 2026-02-01 3300

run_sector_syms crypto misc_smallcap_3 crypto_sector_w_confirm \
    "ZENUSDT,SXPUSDT" 2026-02-01 3300

log "=== crypto v5c complete: 25 runs (2 syms/group, start=2026-02-01) ==="
log "Results in: $WORKDIR/data/sweep_results/"
