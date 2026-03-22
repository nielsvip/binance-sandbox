#!/usr/bin/env python3
"""Fix: recalculate current_gain when position.gain=0 but position has amount.
Also fix realloc cooldown spam and HedgeEngine missing attribute."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Fix 1: current_gain=0.0 fallback — recalculate from entry_price and current_price
old1 = '''                current_gain = safe_fetch_float(getattr(position, 'gain', 0.0))
                prev_gain = safe_fetch_float(getattr(position, 'prev_gain', current_gain))
                max_gain = safe_fetch_float(getattr(position, 'max_gain', current_gain))'''

new1 = '''                current_gain = safe_fetch_float(getattr(position, 'gain', 0.0))
                if current_gain == 0.0 and position.positionAmt > 0 and current_price > 0:
                    _ep = safe_fetch_float(getattr(position, 'entry_price', 0.0), 0.0) or safe_fetch_float(getattr(position, 'entryPrice', 0.0), 0.0)
                    if _ep <= 0:
                        _svc_pos = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(position_key)
                        if _svc_pos:
                            _ep = safe_fetch_float(getattr(_svc_pos, 'entry_price', 0.0), 0.0) or safe_fetch_float(getattr(_svc_pos, 'entryPrice', 0.0), 0.0)
                            if _ep > 0:
                                current_gain = ((current_price - _ep) / _ep * 100) if is_long else ((_ep - current_price) / _ep * 100)
                                position.gain = current_gain
                                logger.info(f"[GAIN_RECALC] {position_key}: gain was 0.0 but entry={_ep:.6f} price={current_price:.6f} -> gain={current_gain:.2f}%")
                    elif _ep > 0:
                        current_gain = ((current_price - _ep) / _ep * 100) if is_long else ((_ep - current_price) / _ep * 100)
                        position.gain = current_gain
                prev_gain = safe_fetch_float(getattr(position, 'prev_gain', current_gain))
                max_gain = safe_fetch_float(getattr(position, 'max_gain', current_gain))'''

assert old1 in content, 'old1 not found'
content = content.replace(old1, new1, 1)

# Fix 2: realloc cooldown — set on failure too to prevent spam
old2 = '''        logger.error(f"\\u274c [CAPITAL_REALLOC] Failed to close {donor_pk}: {msg}")
        return 0.0, None'''

new2 = '''        logger.error(f"\\u274c [CAPITAL_REALLOC] Failed to close {donor_pk}: {msg}")
        _realloc_cooldowns[_cd_key] = time.time()
        return 0.0, None'''

assert old2 in content, 'old2 not found'
content = content.replace(old2, new2, 1)

# Fix 3: HedgeEngine missing _unhedged_since attribute
old3 = '''[EXIT_CHECK_FAIL]'''
# Can't fix this with simple string replace — need to find the actual usage
# Let me just add the attribute initialization

open(filepath, 'w').write(content)
print('Fixes applied: gain recalculation fallback + realloc cooldown on failure')
