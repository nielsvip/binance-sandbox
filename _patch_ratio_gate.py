#!/usr/bin/env python3
"""Fix: RATIO_GATE should not block AUGMENT on winning positions (gain >= 2%)."""

filepath = '/home/niels/binance/ez_manage.py'
content = open(filepath, 'r').read()

# Skip RATIO_GATE for augments on winners
old = "        is_entry_action = action in ['OPEN', 'AUGMENT', 'REENTRY', 'REVERSE', 'REVERSE_AUGMENT','QUICK_OPEN', 'HEDGE_OPEN', 'QUICK_AUGMENT']\n        if is_entry_action and 'HEDGE' not in reason.upper() and self.positions_service:"

new = "        is_entry_action = action in ['OPEN', 'AUGMENT', 'REENTRY', 'REVERSE', 'REVERSE_AUGMENT','QUICK_OPEN', 'HEDGE_OPEN', 'QUICK_AUGMENT']\n        _is_winner_augment = action in ['AUGMENT', 'QUICK_AUGMENT'] and safe_fetch_float(getattr(position, 'gain', 0.0), 0.0) >= 2.0\n        if is_entry_action and 'HEDGE' not in reason.upper() and not _is_winner_augment and self.positions_service:"

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fix applied: RATIO_GATE bypassed for AUGMENT on winners >= 2% gain')
