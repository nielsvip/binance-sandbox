#!/bin/bash
# Launch 12 crypto workers on S1: 12 diverse symbols × 15 months
# Uses 0.7574 baseline as starting point; mix of flip rates for exploration

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
NPZ=$SANDBOX/backtest_v8/indicators
BASELINE=$SANDBOX/data/baselines/crypto_0p7574.json
SYMS="BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,AVAXUSDC,LINKUSDC,ADAUSDC,DOTUSDT,LTCUSDC,TRXUSDT,ATOMUSDT"
OUT_BASE=$SANDBOX/data/autonomous/crypto_12sym_1yr_v2
START="2025-01-01"
BH=200          # approx 12 diverse crypto 15mo BH (mixed gainers/losers)
TARGET=2000     # 10x BH exploratory target
SHARPE=1.0      # exploratory tier-1 gate
NMAX=500000
LOG_DIR=/home/niels/logs

mkdir -p "$OUT_BASE" "$LOG_DIR"
touch /home/niels/SWEEP_RUNNING

# Workers 1-4: low flip (stay near 0.7574 baseline)
for i in 1 2 3 4; do
  SEED=$((100 + i))
  mkdir -p "$OUT_BASE/w${i}"
  nohup $PYTHON -u $SANDBOX/autonomous_search.py \
    --mode crypto --symbol-list "$SYMS" --start "$START" \
    --npz-dir "$NPZ" \
    --bh-accumulated-gain-pct $BH --target-gain-abs $TARGET \
    --target-sharpe-min $SHARPE \
    --min-trades-per-sym 30 \
    --out-dir "$OUT_BASE/w${i}" \
    --n-max $NMAX \
    --bool-flip-prob 0.10 --numeric-perturb-prob 0.08 \
    --base-overrides-json "$BASELINE" \
    --seed $SEED \
    > "$LOG_DIR/crypto12_w${i}.log" 2>&1 &
  echo "Started w${i} (low-flip) PID=$!"
done

# Workers 5-8: medium flip (explore wider)
for i in 5 6 7 8; do
  SEED=$((200 + i))
  mkdir -p "$OUT_BASE/w${i}"
  nohup $PYTHON -u $SANDBOX/autonomous_search.py \
    --mode crypto --symbol-list "$SYMS" --start "$START" \
    --npz-dir "$NPZ" \
    --bh-accumulated-gain-pct $BH --target-gain-abs $TARGET \
    --target-sharpe-min $SHARPE \
    --min-trades-per-sym 30 \
    --out-dir "$OUT_BASE/w${i}" \
    --n-max $NMAX \
    --bool-flip-prob 0.20 --numeric-perturb-prob 0.15 \
    --base-overrides-json "$BASELINE" \
    --seed $SEED \
    > "$LOG_DIR/crypto12_w${i}.log" 2>&1 &
  echo "Started w${i} (med-flip) PID=$!"
done

# Workers 9-12: high flip (random exploration, no baseline)
for i in 9 10 11 12; do
  SEED=$((300 + i))
  mkdir -p "$OUT_BASE/w${i}"
  nohup $PYTHON -u $SANDBOX/autonomous_search.py \
    --mode crypto --symbol-list "$SYMS" --start "$START" \
    --npz-dir "$NPZ" \
    --bh-accumulated-gain-pct $BH --target-gain-abs $TARGET \
    --target-sharpe-min $SHARPE \
    --min-trades-per-sym 30 \
    --out-dir "$OUT_BASE/w${i}" \
    --n-max $NMAX \
    --bool-flip-prob 0.35 --numeric-perturb-prob 0.25 \
    --seed $SEED \
    > "$LOG_DIR/crypto12_w${i}.log" 2>&1 &
  echo "Started w${i} (high-flip) PID=$!"
done

echo "All 12 workers launched"
