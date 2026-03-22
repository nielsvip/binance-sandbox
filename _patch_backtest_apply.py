#!/usr/bin/env python3
"""Apply remaining backtest findings to ez_positions_quick.py:
1. RelVol threshold 1.5 -> 1.2 for TIER2
2. Remove ha_3m from mom_exhausted (line 7208)
3. Add RelVol >= 1.2 entry gate (low vol penalty already exists, add hard gate)
4. Trailing ATR exit for positions in profit
"""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Fix 1: RelVol TIER2 threshold 1.5 -> 1.2
old1 = "        elif htf_align_count >= 2 and (rel_vol_3m >= 1.5 or rel_vol_15m >= 1.5):"
new1 = "        elif htf_align_count >= 2 and (rel_vol_3m >= 1.2 or rel_vol_15m >= 1.2):"
assert old1 in content, 'old1 not found'
content = content.replace(old1, new1, 1)

# Fix 2: Remove ha_3m from mom_exhausted (keep k_1m condition only)
old2 = "                    mom_exhausted = (is_long and (k_1m < d_1m or ha_3m == 'red')) or (not is_long and (k_1m > d_1m or ha_3m == 'green'))"
new2 = "                    mom_exhausted = (is_long and k_1m < d_1m) or (not is_long and k_1m > d_1m)  # ha_3m removed: sweep -5.3 delta Sharpe"
assert old2 in content, 'old2 not found'
content = content.replace(old2, new2, 1)

# Fix 3: vol_strong threshold 2.0 -> 1.5 (user says 2.0 is too high = price doubling)
old3 = "        vol_strong = rel_vol_3m >= 2.0 or rel_vol_15m >= 2.0  # true surge (RV>=2.0 = Sharpe 208, MaxDD -63%)"
new3 = "        vol_strong = rel_vol_3m >= 1.5 or rel_vol_15m >= 1.5  # surge (user: RV>=2.0 too extreme, 1.5 realistic)"
assert old3 in content, 'old3 not found'
content = content.replace(old3, new3, 1)

# Fix 4: Add trailing ATR exit in the hard_exit block (after PROFIT_EROSION, before hard_augment)
# When gain > 1 ATR and price retraces 0.5 ATR from peak, exit
old4 = "                hard_augment = False"
new4 = """                if not hard_exit_reason and not is_hedge and current_gain > 0.5:
                    _atr_pct = safe_fetch_float(indicators.get('atr_3m', 0), 0) / current_price * 100 if current_price > 0 else 0
                    if _atr_pct > 0 and current_gain > _atr_pct and (max_gain - current_gain) > _atr_pct * 0.5:
                        hard_exit_reason = f"TRAILING_ATR_EXIT_gain{current_gain:.2f}%_atr{_atr_pct:.2f}%_peak{max_gain:.2f}%"
                hard_augment = False"""
assert old4 in content, 'old4 not found'
content = content.replace(old4, new4, 1)

open(filepath, 'w').write(content)
print('Applied: RelVol 1.2, ha_3m removed, vol_strong 1.5, trailing ATR exit')
