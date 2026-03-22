#!/usr/bin/env python3
"""Fix _get_ls_ratio_manage position: move outside the class."""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/niels/binance/ez_manage.py"

with open(path) as f:
    content = f.read()

# 1. Find and remove the helper from its current position (between class methods)
helper_start = "\nasync def _get_ls_ratio_manage(tracker_manager, account_key: str) -> tuple:"
helper_end = "    async def execute_now(self, position_key"

start_idx = content.find(helper_start)
if start_idx == -1:
    print("ERROR: helper not found at current position")
    sys.exit(1)

end_idx = content.find(helper_end, start_idx)
if end_idx == -1:
    print("ERROR: execute_now not found after helper")
    sys.exit(1)

# Extract the helper function text (between start and the execute_now line)
helper_text = content[start_idx:end_idx].rstrip() + "\n\n"

# Remove it from the class body
content = content[:start_idx] + "\n" + content[end_idx:]
print(f"Removed helper from line ~{content[:start_idx].count(chr(10))}")

# 2. Insert it before "class MultiAccountTradeManager:"
class_marker = "class MultiAccountTradeManager:"
class_idx = content.find(class_marker)
if class_idx == -1:
    print("ERROR: class not found")
    sys.exit(1)

content = content[:class_idx] + helper_text + "\n" + content[class_idx:]
print(f"Inserted helper before class at position {class_idx}")

with open(path, "w") as f:
    f.write(content)
print("DONE")
