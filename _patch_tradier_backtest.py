#!/usr/bin/env python3
"""Apply backtest findings to tradier_manage.py:
1. Add CYCLE_TP exit (15m stoch peak/trough while in profit >= 3%)
2. Add trailing ATR exit
"""

filepath = '/home/niels/binance/tradier_manage.py'
content = open(filepath, 'r').read()

# Fix 1: Add CYCLE_TP + trailing ATR to should_exit_long
old_long = "            # Quick Top Exit: If in profit, and 1m or 5m stochastic hooks down from top\n            quick_top_hit = gain > 0.5 and ((k_1m > 80 and k_1m < k_1m_prev) or (k_5m > 80 and k_5m < k_5m_prev))"

new_long = """            # Quick Top Exit: If in profit, and 1m or 5m stochastic hooks down from top
            quick_top_hit = gain > 0.5 and ((k_1m > 80 and k_1m < k_1m_prev) or (k_5m > 80 and k_5m < k_5m_prev))
            # CYCLE_TP: exit at 15m stoch peak while in profit >= 3% (sweep winner: +102 avg Sharpe delta)
            k_15m = float(indicators.get('stoch_k_15m', 50))
            k_15m_prev = float(indicators.get('stoch_k_15m_prev', 50))
            _cycle_tp_pct = getattr(config, 'CYCLE_TP_PCT', 0.03) * 100
            cycle_tp_hit = gain >= _cycle_tp_pct and k_15m > 75 and k_15m < k_15m_prev
            # Trailing ATR: exit when gain > 1 ATR and retraces 0.5 ATR from peak
            _atr = float(indicators.get('atr_5m', 0))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct and (_max_gain - gain) > _atr_pct * 0.5"""

assert old_long in content, 'old_long not found'
content = content.replace(old_long, new_long, 1)

# Update the final_result for long exit to include new conditions
old_result_long = "            final_result = stop_loss_hit or take_profit_hit or crossunder_hit or quick_top_hit"
new_result_long = "            final_result = stop_loss_hit or take_profit_hit or crossunder_hit or quick_top_hit or cycle_tp_hit or trailing_atr_hit"
# Only replace the first occurrence (in should_exit_long)
idx = content.find(old_result_long)
if idx >= 0:
    content = content[:idx] + new_result_long + content[idx+len(old_result_long):]

# Fix 2: Add CYCLE_TP + trailing ATR to should_exit_short
old_short_search = "            # Quick Bottom Exit"
short_idx = content.find(old_short_search)
if short_idx < 0:
    # Find the short exit function another way
    old_short = "quick_bottom_hit = gain > 0.5 and ((k_1m < 20 and k_1m > k_1m_prev) or (k_5m < 20 and k_5m > k_5m_prev))"
    new_short = """quick_bottom_hit = gain > 0.5 and ((k_1m < 20 and k_1m > k_1m_prev) or (k_5m < 20 and k_5m > k_5m_prev))
            # CYCLE_TP: exit at 15m stoch trough while in profit >= 3% (sweep winner)
            k_15m = float(indicators.get('stoch_k_15m', 50))
            k_15m_prev = float(indicators.get('stoch_k_15m_prev', 50))
            _cycle_tp_pct = getattr(config, 'CYCLE_TP_PCT', 0.03) * 100
            cycle_tp_hit = gain >= _cycle_tp_pct and k_15m < 25 and k_15m > k_15m_prev
            # Trailing ATR: exit when gain > 1 ATR and retraces 0.5 ATR from peak
            _atr = float(indicators.get('atr_5m', 0))
            _atr_pct = (_atr / current_price * 100) if current_price > 0 and _atr > 0 else 0
            _max_gain = float(getattr(position, 'max_gain', gain))
            trailing_atr_hit = _atr_pct > 0 and gain > _atr_pct and (_max_gain - gain) > _atr_pct * 0.5"""
    assert old_short in content, 'old_short not found'
    content = content.replace(old_short, new_short, 1)

# Update final_result for short exit
remaining = content[content.find('should_exit_short'):]
old_result_short = "            final_result = stop_loss_hit or take_profit_hit or crossover_hit or quick_bottom_hit"
if old_result_short in content:
    # Find it after should_exit_short
    short_func_start = content.find('should_exit_short')
    rest = content[short_func_start:]
    local_idx = rest.find(old_result_short)
    if local_idx >= 0:
        abs_idx = short_func_start + local_idx
        content = content[:abs_idx] + "            final_result = stop_loss_hit or take_profit_hit or crossover_hit or quick_bottom_hit or cycle_tp_hit or trailing_atr_hit" + content[abs_idx+len(old_result_short):]

open(filepath, 'w').write(content)
print('Applied: CYCLE_TP + trailing ATR exits to tradier should_exit_long/short')
