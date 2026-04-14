#!/bin/bash
SANDBOX="s1-int:/home/niels/binance-sandbox"
LOCAL="/Users/niels/Documents/binance"
for f in ez_manage.py ez_positions_quick.py config.py tradier_manage.py ez_indicators.py ez_rankings.py tradier_rankings.py tradier_indicators.py ez_positions.py ez_klines.py ez_crosses.py ez_double.py ez_gain_protector.py ez_gap_filler.py ez_market_data.py ez_news_scanner.py ez_positions_service.py utils.py config_tradier.py ez_breakout_agent.py ez_klines_htf.py ez_share_ind.py ez_prices.py ez_prices_ws.py ez_mark_prices.py ez_manipulation_detector.py trade_quality_auditor.py; do
    [ -f "$LOCAL/$f" ] && rsync -a --checksum "$LOCAL/$f" "$SANDBOX/$f" 2>/dev/null
done
