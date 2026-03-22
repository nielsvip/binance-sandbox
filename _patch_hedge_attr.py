#!/usr/bin/env python3
"""Fix HedgeEngine missing _unhedged_since attribute."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

old = "        self._hedge_in_flight: set = set()  # Dedup: prevents concurrent hedge attempts on same losing position"
new = "        self._hedge_in_flight: set = set()  # Dedup: prevents concurrent hedge attempts on same losing position\n        self._unhedged_since: Dict[str, float] = {}  # Track when positions became unhedged"

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fix applied: _unhedged_since attribute added to HedgeEngine')
