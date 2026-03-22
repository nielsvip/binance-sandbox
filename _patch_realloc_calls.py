#!/usr/bin/env python3
"""Patch to add reallocate_capital_for_winner calls at augmentation points."""

import sys

filepath = sys.argv[1] if len(sys.argv) > 1 else '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Fix 1: Hard augment path — add realloc before the augment execute
old1 = '''                if hard_augment:
                    augment_qty = max(config.START_POSITION_SIZE / current_price, position.positionAmt * 0.1)
                    augment_reason = f"MAX_GAIN_3PCT_AUGMENT_{current_gain:.2f}%"
                    await tracker_manager.set_processing(position_key)
                    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)'''

new1 = '''                if hard_augment:
                    if current_gain >= 5.0:
                        await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, current_gain, data_manager=data_manager)
                    augment_qty = max(config.START_POSITION_SIZE / current_price, position.positionAmt * 0.1)
                    augment_reason = f"MAX_GAIN_3PCT_AUGMENT_{current_gain:.2f}%"
                    await tracker_manager.set_processing(position_key)
                    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)'''

assert old1 in content, f'old1 not found'
content = content.replace(old1, new1, 1)

# Fix 2: Reactive augment path — find the augment_qty calculation area and add realloc before execute
# The reactive path has: augment_reason = f"AUGMENT_WINNING_g{position.gain:.2f}%
old2 = '''                                if 'WAIT' in augment_reason:
                                    return f'{position_key} WAIT MEANS WAIT7301\''''

new2 = '''                                if 'WAIT' in augment_reason:
                                    return f'{position_key} WAIT MEANS WAIT7301'
                                if position.gain >= 5.0:
                                    await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.gain, data_manager=data_manager)'''

assert old2 in content, f'old2 not found'
content = content.replace(old2, new2, 1)

open(filepath, 'w').write(content)
print('Reallocation calls added at both augment paths')
