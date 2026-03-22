#!/usr/bin/env python3
"""Fix: STRICT_NO_LOSS gate reads 'entryPrice' but Position class uses 'entry_price'."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Fix the STRICT_NO_LOSS entry price lookup (line 6630)
old = "            _entry_px = safe_fetch_float(getattr(fresh_pos, 'entryPrice', 0), 0)"
new = "            _entry_px = safe_fetch_float(getattr(fresh_pos, 'entry_price', 0), 0) or safe_fetch_float(getattr(fresh_pos, 'entryPrice', 0), 0)"

assert old in content, f'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fix applied: STRICT_NO_LOSS now reads entry_price (underscore) with entryPrice fallback')
