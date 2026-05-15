#!/usr/bin/env bash
# sector_sweep_w_confirm_v5.sh
# Runs ALL sectors (stock + crypto) with the W WT confirmation gate.
# Gate: wt1_W > wt2_W for longs, wt1_W < wt2_W for shorts — entries counter to W trend blocked.
# Goal: determine if requiring Weekly WaveTrend direction alignment lifts sector Sharpe 10×.
#
# Comparison:
#   tradier_sector_baseline (no W gate) = run first to establish floor
#   tradier_sector_w_confirm (W gate)   = same symbols, compare pool_sharpe
#
# Stock sectors (≤10 syms/group — empirical max for 3600s timeout):
#   tech_ai_chips   (29 syms) → a(10)+b(10)+c(9)
#   energy_oil_gas  (24 syms) → a(8)+b(8)+c(8)
#   precious_metals (15 syms) → a(8)+b(7)
#   base_metals_mining (14 syms) → single run
#   uranium_nuclear    (5 syms)  → single run
#   agriculture_fertilizer (8 syms) → single run
#   defense_aerospace  (4 syms)  → single run
#   consumer_media     (5 syms)  → single run
#   commodities_crypto_etf (6 syms) → single run
#
# Crypto sectors (≤4 syms/group — safe under 31 GB, no swap):
#   mega_l1(4+3), defi_oracle(5+4), gaming_nft(3+3), alt_l1(4+4+4),
#   ecosystems(4), infrastructure(3+3), misc_smallcap(3+3)
#
# Usage (on S1, after syncing):
#   nohup bash sector_sweep_w_confirm_v5.sh > ~/logs/sector_w_confirm_v5_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
#
# Created: 2026-05-15

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|sector_sweep_w_confirm" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
    local vec_pids
    vec_pids=$(pgrep -af "vec_matrix_runner" 2>/dev/null \
               | grep -v "$$\|grep\|bash -c\|sector_sweep_w_confirm" \
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
                  | grep -v "$$\|grep\|bash -c\|sector_sweep_w_confirm" | wc -l)
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
    local timeout_s="${6:-3600}"

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

log "=== v5 W-confirm sector sweep starting — testing Weekly WT gate on all sectors ==="

# ─────────────────────────────────────────────────────────────────────────────
# STOCK SECTORS — tradier_sector_w_confirm tier
# Start date 2023-05-13 (3yr to stay within 3600s timeout per group)
# ─────────────────────────────────────────────────────────────────────────────

# tech_ai_chips: 29 syms → 10+10+9
run_sector_syms tradier tech_ai_chips_a tradier_sector_w_confirm \
    "NVDA,AMD,AVGO,MU,INTC,LRCX,TXN,ARM,PLTR,CRWD" \
    2023-05-13 3600

run_sector_syms tradier tech_ai_chips_b tradier_sector_w_confirm \
    "CIBR,IBM,SAP,ACN,MA,PYPL,TTD,WDAY,FIVN,AXON" \
    2023-05-13 3600

run_sector_syms tradier tech_ai_chips_c tradier_sector_w_confirm \
    "OLED,SNDK,RBLX,ASTS,CRWV,MSTR,GOOGL,ROBO,BOTZ" \
    2023-05-13 3600

# energy_oil_gas: 24 syms → 8+8+8
run_sector_syms tradier energy_oil_gas_a tradier_sector_w_confirm \
    "COP,EOG,DVN,FANG,CRK,EQT,AR,RRC" \
    2023-05-13 3600

run_sector_syms tradier energy_oil_gas_b tradier_sector_w_confirm \
    "CHRD,PR,CTRA,LNG,OKE,TRGP,EPD,MPC" \
    2023-05-13 3600

run_sector_syms tradier energy_oil_gas_c tradier_sector_w_confirm \
    "VLO,PSX,PBF,DINO,XOP,USO,BNO,COPX" \
    2023-05-13 3600

# precious_metals: 15 syms → 8+7
run_sector_syms tradier precious_metals_a tradier_sector_w_confirm \
    "NEM,GDX,WPM,AEM,AU,AGI,EGO,KGC" \
    2023-05-13 3600

run_sector_syms tradier precious_metals_b tradier_sector_w_confirm \
    "FNV,RGLD,PAAS,HL,AG,GLD,CDE" \
    2023-05-13 3600

# base_metals_mining: 14 syms — single run (53 min = OK)
run_sector_syms tradier base_metals_mining tradier_sector_w_confirm \
    "CLF,NUE,STLD,CMC,RS,VALE,BHP,RIO,TECK,SCCO,FCX,MP,LAC,ALB" \
    2023-05-13 3600

# uranium_nuclear: 5 syms
run_sector_syms tradier uranium_nuclear tradier_sector_w_confirm \
    "CCJ,UEC,NXE,DNN,URA" \
    2023-05-13 3600

# agriculture_fertilizer: 8 syms
run_sector_syms tradier agriculture_fertilizer tradier_sector_w_confirm \
    "MOS,NTR,CF,ICL,CTVA,DE,ADM,BG" \
    2023-05-13 3600

# defense_aerospace: 4 syms
run_sector_syms tradier defense_aerospace tradier_sector_w_confirm \
    "RTX,LMT,NOC,GD" \
    2023-05-13 3600

# consumer_media: 5 syms
run_sector_syms tradier consumer_media tradier_sector_w_confirm \
    "NFLX,DIS,AMZN,PARA,WBD" \
    2023-05-13 3600

# commodities_crypto_etf: 6 syms
run_sector_syms tradier commodities_crypto_etf tradier_sector_w_confirm \
    "GLD,SLV,USO,PDBC,IBIT,FBTC" \
    2023-05-13 3600

log "=== Stock sectors W-confirm complete (14 runs) — starting crypto sectors ==="

# ─────────────────────────────────────────────────────────────────────────────
# CRYPTO SECTORS — crypto_sector_w_confirm tier
# Start date 2022-01-01 (bull + bear market context)
# ≤4 syms/group — safe under 31 GB RAM
# ─────────────────────────────────────────────────────────────────────────────

# mega_l1: 7 syms → 4+3
run_sector_syms crypto mega_l1a crypto_sector_w_confirm \
    "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC" \
    2022-01-01 3600

run_sector_syms crypto mega_l1b crypto_sector_w_confirm \
    "AVAXUSDC,ADAUSDC,XRPUSDC" \
    2022-01-01 3600

# defi_oracle: 9 syms → 5+4
run_sector_syms crypto defi_oracle_a crypto_sector_w_confirm \
    "UNIUSDC,LINKUSDC,1INCHUSDT,SUSHIUSDT,COMPUSDT" \
    2022-01-01 3600

run_sector_syms crypto defi_oracle_b crypto_sector_w_confirm \
    "SNXUSDT,YFIUSDT,BANDUSDT,GRTUSDT" \
    2022-01-01 3600

# gaming_nft: 6 syms → 3+3
run_sector_syms crypto gaming_nft_a crypto_sector_w_confirm \
    "SANDUSDT,MANAUSDT,AXSUSDT" \
    2022-01-01 3600

run_sector_syms crypto gaming_nft_b crypto_sector_w_confirm \
    "C98USDT,CHRUSDT,SKLUSDT" \
    2022-01-01 3600

# alt_l1: 12 syms → 4+4+4
run_sector_syms crypto alt_l1a crypto_sector_w_confirm \
    "DOTUSDT,ATOMUSDT,LTCUSDC,ETCUSDT" \
    2022-01-01 3600

run_sector_syms crypto alt_l1b crypto_sector_w_confirm \
    "XLMUSDT,DASHUSDT,XMRUSDT,XTZUSDT" \
    2022-01-01 3600

run_sector_syms crypto alt_l1c crypto_sector_w_confirm \
    "ALGOUSDT,EGLDUSDT,QTUMUSDT,TRXUSDT" \
    2022-01-01 3600

# ecosystems: 4 syms — single run
run_sector_syms crypto ecosystems crypto_sector_w_confirm \
    "KSMUSDT,VETUSDT,THETAUSDT,CELRUSDT" \
    2022-01-01 3600

# infrastructure: 6 syms → 3+3
run_sector_syms crypto infrastructure_a crypto_sector_w_confirm \
    "STORJUSDT,HOTUSDT,IOTXUSDT" \
    2022-01-01 3600

run_sector_syms crypto infrastructure_b crypto_sector_w_confirm \
    "IOSTUSDT,IOTAUSDT,RLCUSDT" \
    2022-01-01 3600

# misc_smallcap: 6 syms → 3+3
run_sector_syms crypto misc_smallcap_a crypto_sector_w_confirm \
    "BATUSDT,COTIUSDT,RSRUSDT" \
    2022-01-01 3600

run_sector_syms crypto misc_smallcap_b crypto_sector_w_confirm \
    "RVNUSDT,ZENUSDT,SXPUSDT" \
    2022-01-01 3600

log "=== v5 W-confirm complete: 14 stock + 14 crypto runs (all sectors, WT_W_REQUIRED gate) ==="
log "Results in: $WORKDIR/data/sweep_results/"
log "Compare w_confirm CSVs vs baseline CSVs to measure Weekly WT gate effect on pool_sharpe."
