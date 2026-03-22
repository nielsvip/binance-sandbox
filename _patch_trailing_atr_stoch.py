#!/usr/bin/env python3
"""Fix trailing ATR exit: require stoch confirmation + higher thresholds.
Only exit when:
1. Gain > 2 ATR (not 1 ATR) — let winners run
2. Retrace > 1 ATR from peak (not 0.5)
3. AND stoch confirms momentum loss (k_3m crossing against direction)
"""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Replace the hasty trailing ATR exit with stoch-confirmed version
old = """                if not hard_exit_reason and not is_hedge and current_gain > 0.5:
                    _atr_pct = safe_fetch_float(indicators.get('atr_3m', 0), 0) / current_price * 100 if current_price > 0 else 0
                    if _atr_pct > 0 and current_gain > _atr_pct and (max_gain - current_gain) > _atr_pct * 0.5:
                        hard_exit_reason = f"TRAILING_ATR_EXIT_gain{current_gain:.2f}%_atr{_atr_pct:.2f}%_peak{max_gain:.2f}%\""""

new = """                if not hard_exit_reason and not is_hedge and current_gain > 2.0:
                    _atr_pct = safe_fetch_float(indicators.get('atr_1h', 0), 0) / current_price * 100 if current_price > 0 else 0
                    _stoch_flip = (is_long and k_3m < d_3m and k_3m < k_3m_prev) or (not is_long and k_3m > d_3m and k_3m > k_3m_prev)
                    if _atr_pct > 0 and current_gain > _atr_pct * 2 and (max_gain - current_gain) > _atr_pct and _stoch_flip:
                        hard_exit_reason = f"TRAILING_ATR_STOCH_gain{current_gain:.2f}%_atr1h{_atr_pct:.2f}%_peak{max_gain:.2f}%\""""

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fixed: trailing ATR now requires 2x ATR gain, 1x ATR retrace, stoch flip confirmation, using 1h ATR')
