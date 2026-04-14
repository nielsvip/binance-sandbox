#!/bin/bash
# Syncs latest live + backtest scripts to BOTH sandbox servers.
# Non-destructive: never touches live trading dirs, never restarts services.
# Sandbox dirs are backtest-only per CLAUDE.md ("servers = backtests ONLY").
LOCAL="/Users/niels/Documents/binance"
SANDBOXES=("s1-int:/home/niels/binance-sandbox" "s2-int:/home/niels/binance-sandbox")
FILES=(
    # live trading core (used by V8 engine which calls real code)
    ez_manage.py ez_positions_quick.py ez_positions_service.py ez_positions.py
    ez_indicators.py ez_rankings.py ez_klines.py ez_klines_htf.py
    ez_crosses.py ez_double.py ez_gain_protector.py ez_gap_filler.py
    ez_market_data.py ez_news_scanner.py ez_share_ind.py
    ez_prices.py ez_prices_ws.py ez_mark_prices.py
    ez_manipulation_detector.py ez_breakout_agent.py
    trade_quality_auditor.py utils.py
    # tradier
    tradier_manage.py tradier_indicators.py tradier_rankings.py
    tradier_positions.py tradier_prices.py tradier_api.py
    # configs (sweep overrides get applied on top of these)
    config.py config_tradier.py
    # WT + scorer
    wt_composite.py wt_dc_delta.py wt_dc_exit_scorer.py
    # V8 backtest engine + sweep
    backtest_v8_engine.py backtest_v8_sweep.py backtest_v8_harness.py
    backtest_v8_precompute.py
)
for SANDBOX in "${SANDBOXES[@]}"; do
    echo "→ syncing to $SANDBOX"
    for f in "${FILES[@]}"; do
        [ -f "$LOCAL/$f" ] && rsync -a --checksum "$LOCAL/$f" "$SANDBOX/$f" 2>/dev/null
    done
    # Clear stale .pyc on the remote sandbox so fresh code wins
    HOST="${SANDBOX%%:*}"
    DIR="${SANDBOX#*:}"
    ssh "$HOST" "find '$DIR' -name '*.pyc' -delete 2>/dev/null; find '$DIR' -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; echo pycache_cleared_$HOST" 2>/dev/null
done
echo "✅ sandbox sync complete"
