#!/usr/bin/env python3
"""Add sanity guard: don't trigger gain protection when current_gain is suspiciously 0."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

# Add sanity guard before gain protection block
old = '''                hard_augment = False
                if not hard_exit_reason:
                    last_aug_age = minutes_since(position.last_augmentation_time)
                    if current_gain >= 3.0:
                        hard_augment = True
                    elif max_gain >= 3.0 and current_gain < 2.5:
                        hard_exit_reason = f"GAIN_PROTECTION_2.5PCT_REDUCE_{current_gain:.2f}%"
                    elif max_gain >= 2.5 and current_gain <= 2.0:'''

new = '''                hard_augment = False
                if not hard_exit_reason:
                    last_aug_age = minutes_since(position.last_augmentation_time)
                    if current_gain >= 3.0:
                        hard_augment = True
                    elif current_gain <= 0.0 and max_gain > 2.0 and position.positionAmt > 0:
                        logger.warning(f"[GAIN_SANITY] {position_key}: current_gain={current_gain:.2f}% but max_gain={max_gain:.2f}% — gain likely broken, skipping protection rules")
                    elif max_gain >= 3.0 and current_gain < 2.5:
                        hard_exit_reason = f"GAIN_PROTECTION_2.5PCT_REDUCE_{current_gain:.2f}%"
                    elif max_gain >= 2.5 and current_gain <= 2.0:'''

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Gain sanity guard added')
