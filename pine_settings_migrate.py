#!/usr/bin/env python3
"""
pine_settings_migrate.py - Migrate strategy settings between Nielsbot versions.

Usage:
  python3 pine_settings_migrate.py --from v1.63 --to v1.64

How it works:
  TradingView stores strategy inputs by POSITION in the source code.
  When you copy settings from v1.50→v1.64, mismatched positions give wrong values.
  This script:
    1. Parses input declarations from both source files
    2. Builds a name→value map from the "from" version's saved settings JSON
    3. Emits a position-ordered values list for the "to" version
    4. Can optionally apply them via TradingView MCP pine_set_source (injecting defaults)

Usage modes:
  --list-inputs <file>     : List all inputs with their positions and defaults
  --compare <from> <to>    : Show which inputs are new/removed/moved between versions
  --migrate <from_json> <to_pine>  : Generate migrated settings JSON
"""

import re, sys, json, argparse
from pathlib import Path

INPUT_PATTERN = re.compile(
    r'^(\w+)\s*=\s*input\.(bool|int|float|string|timeframe|source|color|price)'
    r'\s*\(([^,)]+)',
    re.MULTILINE
)

def parse_inputs(pine_source: str) -> list[dict]:
    """Extract all input declarations with position, name, type, and default."""
    inputs = []
    for i, line in enumerate(pine_source.split("\n")):
        m = re.match(r"^(\w+)\s*=\s*input\.(bool|int|float|string|timeframe)(.*)", line.strip())
        if m:
            var_name = m.group(1)
            input_type = m.group(2)
            rest = m.group(3)
            # Extract default value (first argument)
            default_match = re.match(r"\(\s*([^,)]+)", rest)
            default = default_match.group(1).strip() if default_match else "?"
            # Extract label if present
            label_match = re.search(r"['\"]([^\'\"]+)['\"]", rest)
            label = label_match.group(1) if label_match else var_name
            inputs.append({
                "position": len(inputs) + 1,
                "line": i + 1,
                "var_name": var_name,
                "type": input_type,
                "default": default,
                "label": label
            })
    return inputs

def compare_versions(src_from: str, src_to: str):
    """Show diff of inputs between two versions."""
    inputs_from = {x["var_name"]: x for x in parse_inputs(src_from)}
    inputs_to = {x["var_name"]: x for x in parse_inputs(src_to)}
    
    added = set(inputs_to) - set(inputs_from)
    removed = set(inputs_from) - set(inputs_to)
    moved = {k for k in (set(inputs_from) & set(inputs_to)) 
             if inputs_from[k]["position"] != inputs_to[k]["position"]}
    
    print(f"\n=== INPUTS COMPARISON ===")
    print(f"From: {len(inputs_from)} inputs  →  To: {len(inputs_to)} inputs")
    if added:
        print(f"\nNEW in 'to' version ({len(added)}):")
        for k in sorted(added): 
            print(f"  + pos={inputs_to[k]['position']} {k} = {inputs_to[k]['default']} ({inputs_to[k]['label']})")
    if removed:
        print(f"\nREMOVED ({len(removed)}):")
        for k in sorted(removed): 
            print(f"  - pos={inputs_from[k]['position']} {k}")
    if moved:
        print(f"\nMOVED ({len(moved)}):")
        for k in sorted(moved): 
            print(f"  ~ {k}: pos {inputs_from[k]['position']} → {inputs_to[k]['position']}")
    if not added and not removed and not moved:
        print("  ✓ No differences — settings are fully compatible by position")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", help="Source version Pine file")
    ap.add_argument("--to-file", help="Target version Pine file")
    ap.add_argument("--list", action="store_true", help="List inputs of from-file")
    ap.add_argument("--compare", action="store_true", help="Compare inputs between versions")
    args = ap.parse_args()
    
    if args.from_file:
        src_from = Path(args.from_file).read_text()
    if args.to_file:
        src_to = Path(args.to_file).read_text()
    
    if args.list and args.from_file:
        inputs = parse_inputs(src_from)
        print(f"\n{len(inputs)} inputs in {args.from_file}:")
        for inp in inputs:
            print(f"  {inp['position']:3d}. {inp['var_name']:<20s} = {inp['default']:<12s} ({inp['label']})")
    
    if args.compare and args.from_file and args.to_file:
        compare_versions(src_from, src_to)

if __name__ == "__main__":
    main()
