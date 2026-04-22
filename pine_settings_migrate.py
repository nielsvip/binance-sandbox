#!/usr/bin/env python3
"""
pine_settings_migrate.py — Copy settings between Nielsbot Pine Script versions.

TradingView stores strategy inputs by POSITION (in_0, in_1, ...).
This script reads input values from one strategy on the chart and applies
them to another, mapping by variable name so moved/reordered inputs are
handled correctly.

Usage:
  # List all inputs in a Pine source file:
  python3 pine_settings_migrate.py --list /tmp/v150.pine

  # Compare inputs between two versions:
  python3 pine_settings_migrate.py --compare /tmp/v150.pine /tmp/v164.pine

  # Read current values from v1.50 on chart, save to file:
  python3 pine_settings_migrate.py --read "Nielsbot(v1.50)" --out /tmp/v150_settings.json

  # Apply saved values to v1.64 on chart:
  python3 pine_settings_migrate.py --apply /tmp/v150_settings.json /tmp/v164.pine "Nielsbot(v1.64)"

  # One-shot: read from source strategy and apply to target (both on chart):
  python3 pine_settings_migrate.py --copy "Nielsbot(v1.50)" /tmp/v150.pine "Nielsbot(v1.64)" /tmp/v164.pine
"""

import re, sys, json, argparse, urllib.request, urllib.error
from pathlib import Path

# ── CDP plumbing ──────────────────────────────────────────────────────────────

CDP_HOST = "localhost"
CDP_PORT = 9222

def cdp_targets():
    with urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json/list", timeout=5) as r:
        return json.loads(r.read())

def find_chart_target():
    targets = cdp_targets()
    return (
        next((t for t in targets if t["type"] == "page" and re.search(r"tradingview\.com/chart", t.get("url",""), re.I)), None)
        or next((t for t in targets if t["type"] == "page" and re.search(r"tradingview", t.get("url",""), re.I)), None)
    )

def cdp_eval(ws_url: str, js: str):
    """Send a Runtime.evaluate to a CDP target via websocket."""
    import websocket, uuid
    ws = websocket.create_connection(ws_url, timeout=10)
    msg_id = 1
    ws.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                        "params": {"expression": js, "returnByValue": True, "awaitPromise": False}}))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            break
    ws.close()
    if "exceptionDetails" in resp.get("result", {}):
        raise RuntimeError(f"JS error: {resp['result']['exceptionDetails']}")
    return resp["result"]["result"].get("value")

def get_ws_url():
    target = find_chart_target()
    if not target:
        raise RuntimeError("No TradingView chart target found. Is TradingView open?")
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        # Build it manually
        ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target['id']}"
    return ws_url

# ── Pine source parsing ───────────────────────────────────────────────────────

def parse_inputs(pine_source: str) -> list[dict]:
    """Return all input declarations in order with position, var_name, type, default, label."""
    inputs = []
    for lineno, line in enumerate(pine_source.split("\n"), start=1):
        stripped = line.strip()
        # Skip lines that are pure comments
        if stripped.startswith("//"):
            continue
        # Match: varname = input.type(...)
        m = re.match(r"^(\w+)\s*=\s*input\.(bool|int|float|string|timeframe|source|color)\s*\((.+)", stripped)
        if not m:
            continue
        var_name, inp_type, rest = m.group(1), m.group(2), m.group(3)
        # Strip inline comment from rest
        rest = re.sub(r"\s*//.*$", "", rest)
        # First argument = default value
        default_m = re.match(r"\s*([^,)]+)", rest)
        default = default_m.group(1).strip() if default_m else "?"
        # Look for the label string (first quoted string)
        label_m = re.search(r"['\"]([^'\"]+)['\"]", rest)
        label = label_m.group(1) if label_m else var_name
        pos = len(inputs)  # 0-indexed, matches in_0 / in_1 / ...
        inputs.append({"pos": pos, "id": f"in_{pos}", "var_name": var_name,
                       "type": inp_type, "default": default, "label": label, "line": lineno})
    return inputs

def inputs_by_name(pine_source: str) -> dict[str, dict]:
    return {i["var_name"]: i for i in parse_inputs(pine_source)}

# ── TV interaction ────────────────────────────────────────────────────────────

CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()"

def tv_read_inputs(strategy_name: str) -> list[dict]:
    """Return [{id, value}, ...] for all inputs of a named strategy on chart."""
    ws = get_ws_url()
    escaped = strategy_name.replace("'", "\\'")
    js = f"""
    (function() {{
        var chart = {CHART_API};
        var studies = chart.getAllStudies();
        var target = studies.find(function(s) {{
            return s.name && s.name.toLowerCase().indexOf('{escaped.lower()}') !== -1;
        }});
        if (!target) return {{ error: 'Strategy not found: {escaped}' }};
        var study = chart.getStudyById(target.id);
        if (!study) return {{ error: 'Cannot get study by id: ' + target.id }};
        return {{ id: target.id, name: target.name, inputs: study.getInputValues() }};
    }})()
    """
    result = cdp_eval(ws, js)
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(result["error"])
    if not isinstance(result, dict) or "inputs" not in result:
        raise RuntimeError(f"Unexpected result from TV: {result}")
    return result

def tv_set_inputs(strategy_name: str, inputs_dict: dict[str, object]):
    """Set input values on a named strategy by input ID (in_0, in_1, ...)."""
    ws = get_ws_url()
    escaped = strategy_name.replace("'", "\\'")
    inputs_json = json.dumps(inputs_dict)
    js = f"""
    (function() {{
        var chart = {CHART_API};
        var studies = chart.getAllStudies();
        var target = studies.find(function(s) {{
            return s.name && s.name.toLowerCase().indexOf('{escaped.lower()}') !== -1;
        }});
        if (!target) return {{ error: 'Strategy not found: {escaped}' }};
        var study = chart.getStudyById(target.id);
        if (!study) return {{ error: 'Cannot get study by id: ' + target.id }};
        var current = study.getInputValues();
        var overrides = {inputs_json};
        var updated = [];
        for (var i = 0; i < current.length; i++) {{
            if (overrides.hasOwnProperty(current[i].id)) {{
                current[i].value = overrides[current[i].id];
                updated.push(current[i].id);
            }}
        }}
        study.setInputValues(current);
        return {{ updated: updated, total: current.length }};
    }})()
    """
    result = cdp_eval(ws, js)
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(result["error"])
    return result

# ── Mapping logic ─────────────────────────────────────────────────────────────

def build_migration_map(src_inputs: list[dict], dst_inputs: list[dict],
                        src_values: list[dict]) -> dict[str, object]:
    """
    Build {in_N: value} dict for dst, mapping src values by var_name.
    src_values is the [{id, value}] list from tv_read_inputs().
    Inputs in dst that have no match in src keep their defaults (not included in result).
    """
    # src var_name → value
    src_by_pos = {v["id"]: v["value"] for v in src_values}
    src_by_name = {inp["var_name"]: src_by_pos.get(inp["id"]) for inp in src_inputs}

    result = {}
    skipped = []
    for inp in dst_inputs:
        name = inp["var_name"]
        if name in src_by_name and src_by_name[name] is not None:
            result[inp["id"]] = src_by_name[name]
        else:
            skipped.append(name)

    return result, skipped

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(pine_file: str):
    src = Path(pine_file).read_text()
    inputs = parse_inputs(src)
    print(f"\n{len(inputs)} inputs in {pine_file}:")
    for inp in inputs:
        print(f"  {inp['id']:<7}  line {inp['line']:<5}  {inp['var_name']:<22}  [{inp['type']:<9}]  default={inp['default']:<15}  \"{inp['label']}\"")

def cmd_compare(from_file: str, to_file: str):
    from_src = Path(from_file).read_text()
    to_src   = Path(to_file).read_text()
    from_inp = parse_inputs(from_src)
    to_inp   = parse_inputs(to_src)
    from_map = {i["var_name"]: i for i in from_inp}
    to_map   = {i["var_name"]: i for i in to_inp}

    added   = [i for i in to_inp   if i["var_name"] not in from_map]
    removed = [i for i in from_inp if i["var_name"] not in to_map]
    moved   = [i for i in to_inp   if i["var_name"] in from_map
               and from_map[i["var_name"]]["pos"] != i["pos"]]
    compat  = [i for i in to_inp   if i["var_name"] in from_map
               and from_map[i["var_name"]]["pos"] == i["pos"]]

    print(f"\n=== INPUT COMPATIBILITY: {Path(from_file).name} → {Path(to_file).name} ===")
    print(f"From: {len(from_inp)} inputs   To: {len(to_inp)} inputs")
    print(f"\n  Compatible by position : {len(compat)}")
    print(f"  MOVED (pos changed)    : {len(moved)}")
    print(f"  NEW in to-version      : {len(added)}")
    print(f"  REMOVED from to-version: {len(removed)}")

    if moved:
        print(f"\nMOVED inputs (values will be mapped by name, not position):")
        for i in moved:
            old_pos = from_map[i["var_name"]]["pos"]
            print(f"  {i['var_name']:<22}  in_{old_pos} → in_{i['pos']}")

    if added:
        print(f"\nNEW inputs (will use defaults):")
        for i in added:
            print(f"  in_{i['pos']:<4}  {i['var_name']:<22}  default={i['default']}")

    if removed:
        print(f"\nREMOVED inputs (from-values ignored):")
        for i in removed:
            print(f"  in_{i['pos']:<4}  {i['var_name']}")

    if not moved and not removed:
        print("\n✓ All from-inputs map cleanly to to-version by position.")
        print("  Direct position copy is safe. Only the new inputs need defaults.")

def cmd_read(strategy_name: str, out_file: str):
    print(f"Reading inputs from '{strategy_name}' on chart...")
    result = tv_read_inputs(strategy_name)
    print(f"  Found: {result['name']}  (id={result['id']})")
    print(f"  {len(result['inputs'])} inputs read.")
    data = {"strategy_name": result["name"], "entity_id": result["id"],
            "inputs": result["inputs"]}
    Path(out_file).write_text(json.dumps(data, indent=2))
    print(f"  Saved to {out_file}")

def cmd_apply(settings_file: str, to_pine: str, to_strategy_name: str):
    data = json.loads(Path(settings_file).read_text())
    to_src = Path(to_pine).read_text()
    to_inputs = parse_inputs(to_src)

    # Build from_inputs from the settings file if Pine source isn't provided
    # (just use the id list from the saved values directly)
    src_values = data["inputs"]  # [{id, value}, ...]
    src_pine_file = data.get("pine_file")

    if src_pine_file and Path(src_pine_file).exists():
        src_inputs = parse_inputs(Path(src_pine_file).read_text())
    else:
        # Synthesise from saved ids — positions only, no var_name info
        src_inputs = [{"var_name": f"__pos_{v['id']}", "id": v["id"], "pos": int(v["id"].split("_")[1])}
                      for v in src_values]
        # When no Pine source for "from", just map by position ID directly
        overrides = {v["id"]: v["value"] for v in src_values
                     if v["id"] in {i["id"] for i in to_inputs}}
        skipped = [i["var_name"] for i in to_inputs if i["id"] not in overrides]
        print(f"\nApplying {len(overrides)} values to '{to_strategy_name}' (by position, no name mapping)...")
        result = tv_set_inputs(to_strategy_name, overrides)
        print(f"  Updated {len(result.get('updated', []))} inputs. Skipped (new): {skipped}")
        return

    overrides, skipped = build_migration_map(src_inputs, to_inputs, src_values)
    print(f"\nApplying {len(overrides)} values to '{to_strategy_name}' (by name mapping)...")
    if skipped:
        print(f"  Skipped (new/unmatched): {skipped}")
    result = tv_set_inputs(to_strategy_name, overrides)
    print(f"  Updated {len(result.get('updated', []))} inputs.")

def cmd_copy(from_name: str, from_pine: str, to_name: str, to_pine: str):
    print(f"Reading inputs from '{from_name}' on chart...")
    from_result = tv_read_inputs(from_name)
    print(f"  Found: {from_result['name']}  ({len(from_result['inputs'])} inputs)")

    from_src = Path(from_pine).read_text()
    to_src   = Path(to_pine).read_text()
    from_inputs = parse_inputs(from_src)
    to_inputs   = parse_inputs(to_src)

    overrides, skipped = build_migration_map(from_inputs, to_inputs, from_result["inputs"])

    print(f"\nMapping: {len(overrides)} inputs matched by name, {len(skipped)} new/unmatched will keep defaults.")
    if skipped:
        print(f"  Using defaults for: {skipped}")

    print(f"Applying to '{to_name}'...")
    result = tv_set_inputs(to_name, overrides)
    print(f"  Done. Updated {len(result.get('updated', []))} inputs.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Migrate Nielsbot Pine Script settings between versions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="List all inputs in a Pine source file")
    p.add_argument("pine_file")

    p = sub.add_parser("compare", help="Compare inputs between two Pine source files")
    p.add_argument("from_file")
    p.add_argument("to_file")

    p = sub.add_parser("read", help="Read current input values from a strategy on chart, save to JSON")
    p.add_argument("strategy_name", help="Partial name of the strategy on chart (e.g. 'v1.50')")
    p.add_argument("out_file", help="Output JSON file (e.g. /tmp/v150_settings.json)")

    p = sub.add_parser("apply", help="Apply saved JSON settings to a strategy on chart")
    p.add_argument("settings_file", help="Settings JSON saved by 'read' command")
    p.add_argument("to_pine", help="Pine source file of the target version")
    p.add_argument("to_strategy_name", help="Name of target strategy on chart")

    p = sub.add_parser("copy", help="One-shot: copy settings from one chart strategy to another")
    p.add_argument("from_name", help="Source strategy name (partial match, e.g. 'v1.50')")
    p.add_argument("from_pine", help="Pine source file for source version")
    p.add_argument("to_name",   help="Target strategy name (partial match, e.g. 'v1.64')")
    p.add_argument("to_pine",   help="Pine source file for target version")

    # Legacy flat-arg compat (old --from-file / --compare style)
    ap.add_argument("--list", metavar="FILE", help="[legacy] list inputs")
    ap.add_argument("--compare", nargs=2, metavar=("FROM", "TO"), help="[legacy] compare two files")
    ap.add_argument("--from-file", metavar="FILE")
    ap.add_argument("--to-file",   metavar="FILE")

    args = ap.parse_args()

    # Legacy compat
    if args.list:
        cmd_list(args.list); return
    if args.compare:
        cmd_compare(args.compare[0], args.compare[1]); return
    if not args.cmd and args.from_file:
        cmd_list(args.from_file); return

    if args.cmd == "list":       cmd_list(args.pine_file)
    elif args.cmd == "compare":  cmd_compare(args.from_file, args.to_file)
    elif args.cmd == "read":     cmd_read(args.strategy_name, args.out_file)
    elif args.cmd == "apply":    cmd_apply(args.settings_file, args.to_pine, args.to_strategy_name)
    elif args.cmd == "copy":     cmd_copy(args.from_name, args.from_pine, args.to_name, args.to_pine)
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
