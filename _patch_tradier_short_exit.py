#!/usr/bin/env python3
"""Fix tradier should_exit_short: add CYCLE_TP + trailing ATR with stoch confirmation."""

filepath = '/home/niels/binance/tradier_manage.py'
content = open(filepath, 'r').read()

old = """            # Quick Bottom Exit: If in profit, and 1m or 5m stochastic hooks up from bottom
            quick_bottom_hit = gain > 0.5 and ((k_1m < 20 and k_1m > k_1m_prev) or (k_5m < 20 and k_5m > k_5m_prev))

            stop_loss_hit = stop_loss and current_price >= stop_loss"""

new = """            # Quick Bottom Exit: If in profit, and 1m or 5m stochastic hooks up from bottom
            quick_bottom_hit = gain > 0.5 and ((k_1m < 20 and k_1m > k_1m_prev) or (k_5m < 20 and k_5m > k_5m_prev))
            # CYCLE_TP: exit at 15m stoch trough while in profit >= 3% (sweep winner)
            k_15m = float(indicators.get('stoch_k_15m', 50))
            k_15m_prev = float(indicators.get('stoch_k_15m_prev', 50))
            _cycle_tp_pct = getattr(config, 'CYCLE_TP_PCT', 0.03) * 100
            cycle_tp_hit = gain >= _cycle_tp_pct and k_15m < 25 and k_15m > k_15m_prev
            # Trailing ATR: exit when gain > 2 ATR AND retraces 1 ATR AND stoch flips
            _atr = float(indicators.get('atr_1h', indicators.get('atr_5m', 0)))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            _stoch_flip_s = k_5m > k_5m_prev and k_5m < 30
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct * 2 and (_max_gain - gain) > _atr_pct and _stoch_flip_s

            stop_loss_hit = stop_loss and current_price >= stop_loss"""

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fixed: SHORT exit now has CYCLE_TP + trailing ATR with stoch confirmation')
