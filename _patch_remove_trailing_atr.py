#!/usr/bin/env python3
"""Remove trailing ATR exit from ez_positions_quick.py — backtest shows ALL combos negative return.
Keep CYCLE_TP + stoch exits which are proven winners."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

old = """                if not hard_exit_reason and not is_hedge and current_gain > 2.0:
                    _atr_pct = safe_fetch_float(indicators.get('atr_1h', 0), 0) / current_price * 100 if current_price > 0 else 0
                    _stoch_flip = (is_long and k_3m < d_3m and k_3m < k_3m_prev) or (not is_long and k_3m > d_3m and k_3m > k_3m_prev)
                    if _atr_pct > 0 and current_gain > _atr_pct * 2 and (max_gain - current_gain) > _atr_pct and _stoch_flip:
                        hard_exit_reason = f"TRAILING_ATR_STOCH_gain{current_gain:.2f}%_atr1h{_atr_pct:.2f}%_peak{max_gain:.2f}%"
                hard_augment = False"""

new = """                hard_augment = False"""

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Removed trailing ATR exit from crypto — backtest showed ALL combos negative return')
