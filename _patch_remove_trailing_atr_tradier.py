#!/usr/bin/env python3
"""Remove trailing ATR exit from tradier_manage.py — backtest shows ALL combos negative return.
Keep CYCLE_TP which is the proven winner."""

filepath = '/home/niels/binance/tradier_manage.py'
content = open(filepath, 'r').read()

# Remove from LONG exit — set trailing_atr_hit = False
content = content.replace(
    "trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct * 2 and (_max_gain - gain) > _atr_pct and _stoch_flip_l",
    "trailing_atr_hit = False  # DISABLED: backtest sweep showed ALL ATR trail combos negative return"
)

content = content.replace(
    "trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct * 2 and (_max_gain - gain) > _atr_pct and _stoch_flip_s",
    "trailing_atr_hit = False  # DISABLED: backtest sweep showed ALL ATR trail combos negative return"
)

# Also disable the old version if it exists
content = content.replace(
    "trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct and (_max_gain - gain) > _atr_pct * 0.5",
    "trailing_atr_hit = False  # DISABLED: backtest sweep showed ALL ATR trail combos negative return"
)

open(filepath, 'w').write(content)
print('Disabled trailing ATR exit in tradier — backtest confirmed negative returns')
