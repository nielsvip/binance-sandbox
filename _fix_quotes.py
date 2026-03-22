#!/usr/bin/env python3
"""Fix missing quotes in L/S ratio code that were stripped by heredoc."""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/niels/binance/ez_positions_quick.py"

with open(path) as f:
    c = f.read()

replacements = [
    # The first line got mangled by the previous attempt, find what it is now
    ('getattr(config, \'LS_RATIO_ENFORCE\', False) and (\'OPEN\' in action or \'AUGMENT\' in action)',
     'getattr(config, "LS_RATIO_ENFORCE", False) and ("OPEN" in action or "AUGMENT" in action)'),
    ('getattr(config, LS_RATIO_ENFORCE, False) and (OPEN in action or AUGMENT in action)',
     'getattr(config, "LS_RATIO_ENFORCE", False) and ("OPEN" in action or "AUGMENT" in action)'),
    ('.endswith(_LONG)', '.endswith("_LONG")'),
    ('.endswith(_SHORT)', '.endswith("_SHORT")'),
    ('getattr(config, LS_RATIO_HARD_MIN, 0.25)', 'getattr(config, "LS_RATIO_HARD_MIN", 0.25)'),
    ('getattr(config, LS_RATIO_HARD_MAX, 4.0)', 'getattr(config, "LS_RATIO_HARD_MAX", 4.0)'),
    ('getattr(config, LS_RATIO_MIN, 0.40)', 'getattr(config, "LS_RATIO_MIN", 0.40)'),
    ('getattr(config, LS_RATIO_MAX, 2.50)', 'getattr(config, "LS_RATIO_MAX", 2.50)'),
    ('getattr(config, LS_RATIO_LOG_INTERVAL, 60)', 'getattr(config, "LS_RATIO_LOG_INTERVAL", 60)'),
]

count = 0
for old, new in replacements:
    if old in c:
        c = c.replace(old, new)
        count += 1
        print(f"Fixed: {old[:50]}...")

with open(path, "w") as f:
    f.write(c)
print(f"Done. {count} replacements applied.")
