#!/usr/bin/env python3
"""Fix tradier trailing ATR: stoch confirmation + higher thresholds."""

filepath = '/home/niels/binance/tradier_manage.py'
content = open(filepath, 'r').read()

# Fix LONG trailing ATR
old_long = """            # Trailing ATR: exit when gain > 1 ATR and retraces 0.5 ATR from peak
            _atr = float(indicators.get('atr_5m', 0))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct and (_max_gain - gain) > _atr_pct * 0.5"""

new_long = """            # Trailing ATR: exit when gain > 2 ATR AND retraces 1 ATR from peak AND stoch flips
            _atr = float(indicators.get('atr_1h', indicators.get('atr_5m', 0)))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            _stoch_flip_l = k_5m < k_5m_prev and k_5m > 70
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct * 2 and (_max_gain - gain) > _atr_pct and _stoch_flip_l"""

assert old_long in content, 'old_long not found'
content = content.replace(old_long, new_long, 1)

# Fix SHORT trailing ATR
old_short = """            # Trailing ATR: exit when gain > 1 ATR and retraces 0.5 ATR from peak
            _atr = float(indicators.get('atr_5m', 0))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct and (_max_gain - gain) > _atr_pct * 0.5"""

new_short = """            # Trailing ATR: exit when gain > 2 ATR AND retraces 1 ATR from peak AND stoch flips
            _atr = float(indicators.get('atr_1h', indicators.get('atr_5m', 0)))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            _stoch_flip_s = k_5m > k_5m_prev and k_5m < 30
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct * 2 and (_max_gain - gain) > _atr_pct and _stoch_flip_s"""

assert old_short in content, 'old_short not found'
content = content.replace(old_short, new_short, 1)

open(filepath, 'w').write(content)
print('Fixed: tradier trailing ATR now requires 2x ATR gain, 1x retrace, stoch flip')
