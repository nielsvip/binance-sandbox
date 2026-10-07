import sys

with open("ez_indicators.py", "r") as f:
    lines = f.readlines()

with open("_helpers.py", "r") as f:
    helpers = f.read()

for i, line in enumerate(lines):
    if line.startswith("# ---------------------------------------------------------"):
        lines.insert(i, helpers + "\n")
        break

with open("ez_indicators.py", "w") as f:
    f.writelines(lines)
