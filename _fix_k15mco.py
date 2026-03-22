#!/usr/bin/env python3
"""Add k_15m_prev, k_15mco, k_15mcu to process_single_exit."""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/niels/binance/ez_positions_quick.py"

with open(path) as f:
    lines = f.readlines()

# Find line with "ha_3m=safe_fetch_float(indicators.get('ha_3m'" followed by "hard_exit_reason = None"
# in the process_single_exit function (around line 6468-6469)
target_found = False
for i in range(len(lines)):
    if "ha_3m=safe_fetch_float(indicators.get('ha_3m'" in lines[i] and i + 1 < len(lines) and "hard_exit_reason = None" in lines[i+1]:
        # Check we're in process_single_exit (not rate()) by looking at surrounding context
        # The one in process_single_exit has dc_low_D on the line before
        if i > 0 and "dc_low" in lines[i-1]:
            # Insert k_15m_prev, k_15mco, k_15mcu between ha_3m line and hard_exit_reason
            insert = [
                "                k_15m_prev = safe_fetch_float(indicators.get('stoch_k_15m_prev', 50.0))\n",
                "                k_15mco = k_15m > d_15m and k_15m_prev <= d_15m and k_15m < 30\n",
                "                k_15mcu = k_15m < d_15m and k_15m_prev >= d_15m and k_15m > 70\n",
            ]
            lines = lines[:i+1] + insert + lines[i+1:]
            target_found = True
            print(f"Inserted k_15m_prev/k_15mco/k_15mcu after line {i+1}")
            break

if not target_found:
    print("ERROR: Could not find insertion point")
    sys.exit(1)

with open(path, "w") as f:
    f.writelines(lines)
print("DONE")
