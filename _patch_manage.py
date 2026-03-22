#!/usr/bin/env python3
"""Add L/S ratio enforcement to ez_manage.py execute_now."""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/niels/binance/ez_manage.py"

with open(path) as f:
    content = f.read()

# Add get_ls_ratio_manage helper near execute_now
helper = '''
async def _get_ls_ratio_manage(tracker_manager, account_key: str) -> tuple:
    """Returns (ratio, long_val, short_val) for account."""
    long_val = 0.0
    short_val = 0.0
    if not tracker_manager:
        return 1.0, 0.0, 0.0
    async with tracker_manager._exit_candidates_lock:
        for k, v in tracker_manager.exit_candidates.items():
            if v.get("status") != "active": continue
            ak, sym, ps = parse_position_key(k)
            if ak != account_key: continue
            amt = safe_fetch_float(v.get("positionAmt", 0))
            price = safe_fetch_float(v.get("mark_price", 0))
            val = abs(amt * price)
            if ps == "LONG" and amt > 0:
                long_val += val
            elif ps == "SHORT" and amt > 0:
                short_val += val
    ratio = long_val / short_val if short_val > 0 else (999.0 if long_val > 0 else 1.0)
    return ratio, long_val, short_val

'''

# Insert helper right before "    async def execute_now"
marker = "    async def execute_now(self, position_key"
idx = content.find(marker)
if idx == -1:
    print("ERROR: execute_now not found")
    sys.exit(1)

content = content[:idx] + helper + content[idx:]
print(f"Helper inserted before execute_now at position {idx}")

# Now find the right place inside execute_now to insert the gate
# Insert after the STRICT_NO_LOSS_BLOCK section, before the HEDGE_MODE_BLOCK section
gate_marker = '        if is_hedge_account(config, account_key) and is_reduce and \'HEDGE\' not in reason_upper'
gate_idx = content.find(gate_marker, idx)
if gate_idx == -1:
    print("ERROR: hedge_account gate not found in execute_now")
    sys.exit(1)

gate_code = '''        # === L/S RATIO ENFORCEMENT (execute_now) ===
        if getattr(config, "LS_RATIO_ENFORCE", False) and is_augment and not is_hedge and "HEDGE" not in reason_upper:
            if self.tracker_manager:
                ratio, lv, sv = await _get_ls_ratio_manage(self.tracker_manager, account_key)
                total_val = lv + sv
                if total_val >= 50.0:
                    is_opening_long = position_side == "LONG"
                    is_opening_short = position_side == "SHORT"
                    open_val = quantity * old_price if old_price > 0 else 0
                    new_lv = lv + (open_val if is_opening_long else 0)
                    new_sv = sv + (open_val if is_opening_short else 0)
                    new_ratio = new_lv / new_sv if new_sv > 0 else 999.0
                    hard_min = getattr(config, "LS_RATIO_HARD_MIN", 0.25)
                    hard_max = getattr(config, "LS_RATIO_HARD_MAX", 4.0)
                    soft_min = getattr(config, "LS_RATIO_MIN", 0.40)
                    soft_max = getattr(config, "LS_RATIO_MAX", 2.50)
                    if is_opening_short and new_ratio < hard_min:
                        logger.critical(f"\\U0001f6d1 [LS_RATIO_HARD_BLOCK] {position_key}: Blocking SHORT. Ratio would be {new_ratio:.3f} < {hard_min}. L=${lv:.0f} S=${sv:.0f}")
                        return "BLOCKED_LS_RATIO"
                    elif is_opening_long and new_ratio > hard_max:
                        logger.critical(f"\\U0001f6d1 [LS_RATIO_HARD_BLOCK] {position_key}: Blocking LONG. Ratio would be {new_ratio:.3f} > {hard_max}. L=${lv:.0f} S=${sv:.0f}")
                        return "BLOCKED_LS_RATIO"
                    elif is_opening_short and new_ratio < soft_min and ratio < soft_min:
                        logger.warning(f"\\U0001f6a7 [LS_RATIO_SOFT_BLOCK] {position_key}: Blocking SHORT. Ratio {ratio:.3f} < {soft_min}. L=${lv:.0f} S=${sv:.0f}")
                        return "BLOCKED_LS_RATIO"
                    elif is_opening_long and new_ratio > soft_max and ratio > soft_max:
                        logger.warning(f"\\U0001f6a7 [LS_RATIO_SOFT_BLOCK] {position_key}: Blocking LONG. Ratio {ratio:.3f} > {soft_max}. L=${lv:.0f} S=${sv:.0f}")
                        return "BLOCKED_LS_RATIO"
'''

content = content[:gate_idx] + gate_code + content[gate_idx:]
print(f"Gate inserted before hedge_account check")

with open(path, "w") as f:
    f.write(content)

print("DONE")
