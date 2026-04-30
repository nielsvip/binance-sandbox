#!/usr/bin/env bash
# start_vec_sweep.sh — launcher for vec_sweep.py on S1 (crypto) or S2 (tradier).
# Per CLAUDE.md SWEEP-LIVENESS MANDATE: refuses wrong-host, nohup with disown,
# verifies process after T+5s and T+30s, log to ~/logs (NOT /tmp).
#
# Usage:
#   bash start_vec_sweep.sh pooled_48      # full 48-sym crypto pooled sweep (S1)
#   bash start_vec_sweep.sh validate_top   # neighborhood-search the per-sym + 20-sym leaderboard
#   bash start_vec_sweep.sh pooled_tradier # 100-sym stocks pooled sweep (S2)
#   bash start_vec_sweep.sh status         # report process + DB row counts
#   bash start_vec_sweep.sh stop           # kill all vec_sweep workers

set -u
TIER="${1:-status}"
HOST="$(hostname -s)"
REPO="${REPO:-$HOME/binance-sandbox}"
[ -d "$REPO" ] || REPO="$HOME/binance"   # fall back to live tree on Mac (status only)
PY="${V8_PYTHON:-python3}"
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d_%H%M%S)"

cd "$REPO" || { echo "ERR: $REPO not found"; exit 1; }
[ -f vec_sweep.py ] || { echo "ERR: $REPO/vec_sweep.py missing — rsync it first"; exit 1; }
[ -f metrics_guard.py ] || { echo "ERR: metrics_guard.py missing"; exit 1; }

# Server detection: hostname OR public IP. S1=157.180.125.52, S2=204.168.181.211.
_my_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }
is_s1() { [[ "$HOST" == s1* ]] || [[ "$HOSTNAME" == *s1* ]] \
          || [[ "$(_my_ip)" == 157.180.* ]] \
          || { [[ -d /home/niels/binance-sandbox ]] && [[ ! -d /home/niels/miniconda3 ]]; }; }
is_s2() { [[ "$HOST" == s2* ]] || [[ "$HOSTNAME" == *s2* ]] \
          || [[ "$(_my_ip)" == 204.168.* ]] \
          || { [[ -d /home/niels/binance-sandbox ]] && [[ -d /home/niels/miniconda3 ]]; }; }

launch() {
    local name="$1"; shift
    local cmd="$*"
    local log="$LOG_DIR/vec_${name}_${TS}.log"
    echo "[start_vec_sweep] launching $name → $log"
    nohup $PY -u $cmd > "$log" 2>&1 < /dev/null & disown
    local pid=$!
    sleep 5
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "FAIL: process $pid died within 5s"
        tail -20 "$log"
        return 1
    fi
    echo "T+5s: PID=$pid alive, log advancing"
    head -3 "$log" 2>/dev/null
    sleep 25
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "FAIL: process died between 5s and 30s"
        tail -30 "$log"; return 1
    fi
    if grep -E "Traceback|Error|MODE_CONFIG_MISMATCH_SKIP|Killed" "$log" > /dev/null 2>&1; then
        echo "WARN: error pattern in log"
        grep -E "Traceback|Error|MODE_CONFIG_MISMATCH_SKIP" "$log" | head -5
    fi
    echo "T+30s: PID=$pid still alive. log=$log"
    return 0
}

case "$TIER" in
    pooled_48|pooled_32|pooled_24)
        is_s1 || { echo "ERR: pooled_* is CRYPTO — run on S1 (this is $HOST $(_my_ip))"; exit 2; }
        # 48-sym = PUBLISHABLE floor; 32/24 = DIAGNOSTIC fallback if RAM-bound.
        # S1 has 30GB → 48 NPZs OOM, 32 fits.
        # 10 USDC-perp majors named USDC per USDC-OVER-USDT policy + legacy USDT.
        TARGET="${TIER#pooled_}"
        BASE_USDC="BTCUSDC ETHUSDC SOLUSDC ADAUSDC BNBUSDC AVAXUSDC XRPUSDC LINKUSDC LTCUSDC UNIUSDC"
        BASE_USDT="1INCHUSDT ALGOUSDT ANKRUSDT ATOMUSDT AXSUSDT BANDUSDT BATUSDT BELUSDT BTCDOMUSDT C98USDT CELRUSDT CHRUSDT COMPUSDT COTIUSDT DASHUSDT DOTUSDT EGLDUSDT ENJUSDT ETCUSDT GRTUSDT GTCUSDT HOTUSDT IOSTUSDT IOTAUSDT IOTXUSDT KAVAUSDT KNCUSDT KSMUSDT LRCUSDT MANAUSDT MTLUSDT NKNUSDT QTUMUSDT RLCUSDT RSRUSDT RVNUSDT SANDUSDT SKLUSDT"
        SYMS=""; COUNT=0
        for S in $BASE_USDC $BASE_USDT; do
            if [ -f "$REPO/backtest_v8/indicators/$S.npz" ]; then
                SYMS="${SYMS}${S},"; COUNT=$((COUNT+1))
                [ "$COUNT" -ge "$TARGET" ] && break
            fi
        done
        SYMS=${SYMS%,}
        N=$(echo "$SYMS" | tr ',' '\n' | wc -l)
        echo "[$TIER] basket: $N NPZs (target $TARGET)"
        [ "$N" -ge "$TARGET" ] || { echo "ERR: only $N NPZs, need $TARGET"; exit 2; }
        launch "$TIER" vec_sweep.py pooled --basket "crypto$TARGET" --syms "$SYMS" --max-configs 5000 --seed 31
        ;;
    validate_top)
        is_s1 || { echo "ERR: validate_top is CRYPTO — run on S1"; exit 2; }
        BASE_USDC="BTCUSDC ETHUSDC SOLUSDC ADAUSDC BNBUSDC AVAXUSDC XRPUSDC LINKUSDC LTCUSDC UNIUSDC"
        BASE_USDT="1INCHUSDT ALGOUSDT ANKRUSDT ATOMUSDT AXSUSDT BANDUSDT BATUSDT BELUSDT BTCDOMUSDT C98USDT CELRUSDT CHRUSDT COMPUSDT COTIUSDT DASHUSDT DOTUSDT EGLDUSDT ENJUSDT ETCUSDT GRTUSDT GTCUSDT HOTUSDT IOSTUSDT IOTAUSDT IOTXUSDT KAVAUSDT KNCUSDT KSMUSDT LRCUSDT MANAUSDT MTLUSDT NKNUSDT QTUMUSDT RLCUSDT RSRUSDT RVNUSDT SANDUSDT SKLUSDT"
        SYMS=""
        for S in $BASE_USDC $BASE_USDT; do
            [ -f "$REPO/backtest_v8/indicators/$S.npz" ] && SYMS="${SYMS}${S},"
        done
        SYMS=${SYMS%,}
        N=$(echo "$SYMS" | tr ',' '\n' | wc -l)
        echo "[validate_top] discovered $N NPZs in basket"
        launch "validate_48" vec_sweep.py validate --seed-mode "validate:per_symbol" \
            --top-n 1000 --basket crypto48 --syms "$SYMS" \
            --target-pool-sharpe 4.0 --max-configs 50000 --seed 37
        ;;
    pooled_tradier)
        is_s2 || { echo "ERR: pooled_tradier is STOCKS — run on S2"; exit 2; }
        # Stocks 100-sym basket from local NPZ
        SYMS=$(ls "$REPO"/backtest_v8/indicators/*.npz 2>/dev/null | xargs -n1 basename | sed 's/.npz$//' | grep -vE 'USDT|USDC' | head -100 | tr '\n' ',' | sed 's/,$//')
        N=$(echo "$SYMS" | tr ',' '\n' | wc -l | tr -d ' ')
        echo "n_stock_syms=$N"
        [ "$N" -ge 100 ] || { echo "ERR: only $N stocks found, need >=100"; exit 2; }
        launch "pooled_stocks" vec_sweep.py pooled --basket tradier100 --syms "$SYMS" --max-configs 5000 --seed 41
        ;;
    status)
        echo "vec_sweep procs:"; pgrep -af "python.*vec_sweep" || echo "  (none)"
        echo
        echo "logs:"; ls -lt "$LOG_DIR"/vec_*.log 2>/dev/null | head -5
        echo
        DB="$REPO/data/vec_sweep.db"
        if [ -f "$DB" ]; then
            echo "DB: $DB ($(du -h "$DB" | cut -f1))"
            $PY -c "
import sqlite3
con=sqlite3.connect('$DB')
print('  modes:', con.execute('SELECT mode, COUNT(*) FROM results GROUP BY mode').fetchall())
rows=con.execute('SELECT mode, COUNT(*), ROUND(MAX(pool_sharpe),4) FROM results WHERE trades>=30*n_syms AND ABS(pool_sharpe)<=5 GROUP BY mode').fetchall()
print('  honest top per mode:'); [print(f'    {r[0]}: rows={r[1]} best={r[2]}') for r in rows]
" 2>&1
        else
            echo "DB: not yet created at $DB"
        fi
        ;;
    stop)
        echo "stopping vec_sweep workers..."
        pkill -f "python.*vec_sweep" 2>/dev/null
        sleep 2
        pgrep -af "python.*vec_sweep" && echo "WARN: some still alive" || echo "stopped"
        ;;
    *)
        echo "Usage: $0 {pooled_48|validate_top|pooled_tradier|status|stop}"
        exit 1
        ;;
esac
