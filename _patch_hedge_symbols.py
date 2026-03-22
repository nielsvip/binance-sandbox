#!/usr/bin/env python3
"""Fix: get_hottest_hedge should use all active symbols for hedges, not just account-specific ones.
Hedges are protective — they need the widest pool of candidates possible."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# In get_hottest_hedge, expand allowed_symbols to include symbols_active for hedges
old = '''    def get_hottest_hedge(self, account_key, target_side, exclude_symbols=None):
        """Finds best hedge candidates (Top 10 + High Score + Allowed + Not Crowded + No Open Positions)"""
        candidates = self.top_longs if target_side == "LONG" else self.top_shorts
        if not candidates: return []
        allowed_symbols = self._get_account_symbols(account_key)'''

new = '''    def get_hottest_hedge(self, account_key, target_side, exclude_symbols=None):
        """Finds best hedge candidates (Top 10 + High Score + Allowed + Not Crowded + No Open Positions)"""
        candidates = self.top_longs if target_side == "LONG" else self.top_shorts
        if not candidates: return []
        allowed_symbols = self._get_account_symbols(account_key) | set(getattr(self.trade_manager, 'symbols_active', []) or [])'''

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fix applied: hedge candidates use full symbols_active pool')
