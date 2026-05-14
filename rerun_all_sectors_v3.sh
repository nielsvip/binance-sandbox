#!/usr/bin/env bash
# rerun_all_sectors_v3.sh
# Re-runs ALL sectors that still need valid results.
# Crypto sectors are split to ≤5 syms per run to stay under 31 GB RAM ceiling.
# (7-sym run peaked at ~26.7 GB; spikes killed it with 4.6 GB headroom.)
#
# Sectors included:
#   Stock (need rerun): tech_ai_chips, energy_oil_gas (if v2 failed)
#   Crypto (all, split): mega_l1a/b, defi_oracle_a/b/c, gaming_nft_a/b,
#                        alt_l1_a/b/c, ecosystems, infrastructure_a/b,
#                        misc_smallcap_a/b
#
# Usage (on S1):
#   nohup bash rerun_all_sectors_v3.sh > ~/logs/sector_rerun_v3_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
#
# Created: 2026-05-14

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ---------------------------------------------------------------------------
# Kill any competing engines/vec runners that consume RAM
# ---------------------------------------------------------------------------
kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|rerun_all_sectors" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
    local vec_pids
    vec_pids=$(pgrep -af "vec_matrix_runner" 2>/dev/null \
               | grep -v "$$\|grep\|bash -c\|rerun_all_sectors" \
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
                  | grep -v "$$\|grep\|bash -c\|rerun_all_sectors" | wc -l)
        [[ "$running" -eq 0 ]] && break
        [[ $waited -eq 0 ]] && log "  Waiting for $running competing engine(s)..."
        sleep 30
        waited=$((waited + 30))
    done
    [[ $waited -gt 0 ]] && log "  Engines cleared after ${waited}s."
}

# ---------------------------------------------------------------------------
# Run one sector by explicit symbol list (not sectors_*.json lookup)
# ---------------------------------------------------------------------------
run_sector_syms() {
    local mode="$1"
    local label="$2"     # human label for log (e.g. "mega_l1a")
    local tier="$3"
    local syms="$4"      # comma-separated symbol list
    local start_date="$5"
    local timeout_s="${6:-3600}"

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS
    TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/${mode}_sector_sweep_${label}_${TS}.log"

    kill_competing_engines
    wait_for_clear_engines

    log ">>> START $mode/$label ($count syms, start=$start_date)"
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
    log "<<< DONE $mode/$label rc=$rc log=$logfile"
    sleep 30
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log "=== Sector Sweep v3 (split crypto ≤5 syms) starting ==="

# ── STOCK SECTORS (only run if rc=-9 in prior CSV) ──
# tech_ai_chips: 29 syms — tradier, 3yr
run_sector_syms tradier tech_ai_chips tradier_sector_baseline \
    "NVDA,AMD,AVGO,MU,INTC,LRCX,TXN,ARM,PLTR,CRWD,CIBR,IBM,SAP,ACN,MA,PYPL,TTD,WDAY,FIVN,AXON,OLED,SNDK,RBLX,ASTS,CRWV,MSTR,GOOGL,ROBO,BOTZ" \
    2023-05-13 3600

# energy_oil_gas: 24 tradeable syms (BKR/SLB/XLE removed — not in symbols_trb/trc)
run_sector_syms tradier energy_oil_gas tradier_sector_baseline \
    "COP,EOG,DVN,FANG,CRK,EQT,AR,RRC,CHRD,PR,CTRA,LNG,OKE,TRGP,EPD,MPC,VLO,PSX,PBF,DINO,XOP,USO,BNO,COPX" \
    2023-05-13 3600

# precious_metals: 15 tradeable syms (GDXJ removed — not in symbols_trb/trc)
run_sector_syms tradier precious_metals tradier_sector_baseline \
    "NEM,GDX,WPM,AEM,AU,AGI,EGO,KGC,FNV,RGLD,PAAS,HL,AG,GLD,CDE" \
    2023-05-13 3600

# base_metals_mining: 14 syms — all tradeable
run_sector_syms tradier base_metals_mining tradier_sector_baseline \
    "CLF,NUE,STLD,CMC,RS,VALE,BHP,RIO,TECK,SCCO,FCX,MP,LAC,ALB" \
    2023-05-13 3600

# ── CRYPTO SECTORS — split to ≤4 syms each (safe under 31 GB / no swap) ──
# Removed non-tradeable: KNCUSDT LRCUSDT ENJUSDT ANKRUSDT KAVAUSDT MTLUSDT NKNUSDT BELUSDT GTCUSDT

# mega_l1: 7 syms (BTC/ETH/SOL/BNB/AVAX/ADA/XRP) → split 4+3
run_sector_syms crypto mega_l1a crypto_sector_baseline \
    "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC" \
    2022-01-01 3600

run_sector_syms crypto mega_l1b crypto_sector_baseline \
    "AVAXUSDC,ADAUSDC,XRPUSDC" \
    2022-01-01 3600

# defi_oracle: 9 syms → split 5+4
run_sector_syms crypto defi_oracle_a crypto_sector_baseline \
    "UNIUSDC,LINKUSDC,1INCHUSDT,SUSHIUSDT,COMPUSDT" \
    2022-01-01 3600

run_sector_syms crypto defi_oracle_b crypto_sector_baseline \
    "SNXUSDT,YFIUSDT,BANDUSDT,GRTUSDT" \
    2022-01-01 3600

# gaming_nft: 6 syms → split 3+3
run_sector_syms crypto gaming_nft_a crypto_sector_baseline \
    "SANDUSDT,MANAUSDT,AXSUSDT" \
    2022-01-01 3600

run_sector_syms crypto gaming_nft_b crypto_sector_baseline \
    "C98USDT,CHRUSDT,SKLUSDT" \
    2022-01-01 3600

# alt_l1: 12 syms → split 4+4+4
run_sector_syms crypto alt_l1a crypto_sector_baseline \
    "DOTUSDT,ATOMUSDT,LTCUSDC,ETCUSDT" \
    2022-01-01 3600

run_sector_syms crypto alt_l1b crypto_sector_baseline \
    "XLMUSDT,DASHUSDT,XMRUSDT,XTZUSDT" \
    2022-01-01 3600

run_sector_syms crypto alt_l1c crypto_sector_baseline \
    "ALGOUSDT,EGLDUSDT,QTUMUSDT,TRXUSDT" \
    2022-01-01 3600

# ecosystems: 4 tradeable syms → single run (~14 GB, safe)
run_sector_syms crypto ecosystems crypto_sector_baseline \
    "KSMUSDT,VETUSDT,THETAUSDT,CELRUSDT" \
    2022-01-01 3600

# infrastructure: 6 syms → split 3+3
run_sector_syms crypto infrastructure_a crypto_sector_baseline \
    "STORJUSDT,HOTUSDT,IOTXUSDT" \
    2022-01-01 3600

run_sector_syms crypto infrastructure_b crypto_sector_baseline \
    "IOSTUSDT,IOTAUSDT,RLCUSDT" \
    2022-01-01 3600

# misc_smallcap: 6 syms → split 3+3
run_sector_syms crypto misc_smallcap_a crypto_sector_baseline \
    "BATUSDT,COTIUSDT,RSRUSDT" \
    2022-01-01 3600

run_sector_syms crypto misc_smallcap_b crypto_sector_baseline \
    "RVNUSDT,ZENUSDT,SXPUSDT" \
    2022-01-01 3600

log "=== v3 complete: 5 stock + 14 crypto sub-sector runs (all tradeable-only symbols) ==="
log "Results in: $WORKDIR/data/sweep_results/"
