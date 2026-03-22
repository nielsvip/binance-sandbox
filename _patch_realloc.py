#!/usr/bin/env python3
"""Patch ez_positions_quick.py to add capital reallocation for winners."""

import sys

filepath = sys.argv[1] if len(sys.argv) > 1 else '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Find the insertion point: right before execute_trade_wrapper
marker = 'async def execute_trade_wrapper(trade_manager, tracker_manager: TrackerManager, hedge_engine: HedgeEngine, account_key: str, position_key: str, positionAmt:float, action: str, current_price: float, qty: float, reason: str, already_locked: bool = False, is_hedge: bool = False, hedge_for: Optional[str] = None, override_qty: Optional[float] = None, verify_via_websocket: bool = True, data_manager: Optional[FastDataManager] = None) -> tuple[bool, str]:'

assert marker in content, 'execute_trade_wrapper not found'

new_func = '''_realloc_cooldowns = {}


async def reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key: str, winner_key: str, winner_gain: float, data_manager=None):
    """Close smallest profitable position to free capital for a big winner. Returns (freed_usd, donor_key) or (0, None)."""
    global _realloc_cooldowns
    _cd_key = f"realloc_{account_key}"
    if time.time() - _realloc_cooldowns.get(_cd_key, 0) < 300:
        return 0.0, None
    if winner_gain < 5.0:
        return 0.0, None
    acc_positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
    if not acc_positions:
        return 0.0, None
    hedged_keys = set()
    if hasattr(tracker_manager, 'active_hedges'):
        async with tracker_manager._hedges_lock:
            for h in tracker_manager.active_hedges:
                hedged_keys.add(h.get('position_key', ''))
                hedged_keys.add(h.get('losing_position_key', ''))
    candidates = []
    for pk, pos in acc_positions.items():
        if pk == winner_key:
            continue
        if pk in hedged_keys:
            continue
        p_amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
        if p_amt <= 0:
            continue
        p_gain = safe_fetch_float(getattr(pos, 'gain', 0.0), 0.0)
        p_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
        p_val = p_amt * p_price
        if p_gain < 0.10 or p_gain > 3.0:
            continue
        if p_val < 5.0:
            continue
        candidates.append((pk, pos, p_gain, p_val))
    if not candidates:
        logger.info(f"[CAPITAL_REALLOC] {account_key}: No eligible donor for {winner_key} (gain={winner_gain:.1f}%)")
        return 0.0, None
    candidates.sort(key=lambda x: x[2])
    donor_pk, donor_pos, donor_gain, donor_val = candidates[0]
    donor_amt = abs(safe_fetch_float(getattr(donor_pos, 'positionAmt', 0.0), 0.0))
    is_donor_long = donor_pk.endswith('_LONG')
    donor_side = 'SELL' if is_donor_long else 'BUY'
    donor_sym = parse_position_key(donor_pk)[1]
    donor_price = safe_fetch_float(getattr(donor_pos, 'mark_price', 0.0), 0.0)
    winner_sym = winner_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
    logger.warning(f"\\U0001f4b0 [CAPITAL_REALLOC] {account_key}: Closing {donor_pk} (gain={donor_gain:.2f}% val=${donor_val:.1f}) to fund {winner_key} (gain={winner_gain:.1f}%)")
    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, donor_pk, donor_pos.positionAmt, 'CLOSE', donor_price, donor_amt, f"REALLOC_FOR_{winner_sym}_{winner_gain:.0f}pct", data_manager=data_manager)
    if success:
        _realloc_cooldowns[_cd_key] = time.time()
        logger.warning(f"\\u2705 [CAPITAL_REALLOC] Freed ~${donor_val:.1f} from {donor_pk} for {winner_key}")
        return donor_val, donor_pk
    else:
        logger.error(f"\\u274c [CAPITAL_REALLOC] Failed to close {donor_pk}: {msg}")
        return 0.0, None


'''

content = content.replace(marker, new_func + marker, 1)
open(filepath, 'w').write(content)
print('Capital reallocation function added successfully')
