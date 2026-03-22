#!/usr/bin/env python3
"""Move capital reallocation to be reactive: only after augment fails, then retry."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Remove proactive call from hard_augment path (line ~7235)
old1 = '''                if hard_augment:
                    if current_gain >= 5.0:
                        await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, current_gain, data_manager=data_manager)
                    augment_qty = max(config.START_POSITION_SIZE / current_price, position.positionAmt * 0.1)
                    augment_reason = f"MAX_GAIN_3PCT_AUGMENT_{current_gain:.2f}%"
                    await tracker_manager.set_processing(position_key)
                    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)
                    if success:
                        await record_trade_event(tracker_manager, account_key, position_key, symbol, 'AUGMENT', position_side, current_price, augment_qty, augment_reason, False, False)
                        await tracker_manager.save_tracker(account_key, force=True)
                    await tracker_manager.clear_processing(position_key)
                    return "PROACTIVE_AUGMENTED"'''

new1 = '''                if hard_augment:
                    augment_qty = max(config.START_POSITION_SIZE / current_price, position.positionAmt * 0.1)
                    augment_reason = f"MAX_GAIN_3PCT_AUGMENT_{current_gain:.2f}%"
                    await tracker_manager.set_processing(position_key)
                    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)
                    if not success and current_gain >= 5.0:
                        freed, donor = await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, current_gain, data_manager=data_manager)
                        if freed > 0:
                            await asyncio.sleep(1)
                            success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason + f"_REALLOC_FROM_{donor}", already_locked=True, data_manager=data_manager)
                    if success:
                        await record_trade_event(tracker_manager, account_key, position_key, symbol, 'AUGMENT', position_side, current_price, augment_qty, augment_reason, False, False)
                        await tracker_manager.save_tracker(account_key, force=True)
                    await tracker_manager.clear_processing(position_key)
                    return "PROACTIVE_AUGMENTED"'''

assert old1 in content, 'old1 not found'
content = content.replace(old1, new1, 1)

# Remove proactive call from reactive augment path and make it reactive
old2 = '''                                if 'WAIT' in augment_reason:
                                    return f'{position_key} WAIT MEANS WAIT7301'
                                if position.gain >= 5.0:
                                    await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.gain, data_manager=data_manager)
                                await tracker_manager.set_processing(position_key)
                                success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)'''

new2 = '''                                if 'WAIT' in augment_reason:
                                    return f'{position_key} WAIT MEANS WAIT7301'
                                await tracker_manager.set_processing(position_key)
                                success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)
                                if not success and position.gain >= 5.0:
                                    freed, donor = await reallocate_capital_for_winner(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.gain, data_manager=data_manager)
                                    if freed > 0:
                                        await asyncio.sleep(1)
                                        success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)'''

assert old2 in content, 'old2 not found'
content = content.replace(old2, new2, 1)

open(filepath, 'w').write(content)
print('Reallocation is now reactive: only triggers after augment fails, then retries')
