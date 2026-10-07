#!/usr/bin/env python3
"""
Fix red cells by correcting baseline configuration.
Instead of fixing individual calculations, fix the baseline to have >=30 trades.
Then re-run red cells with new baseline - they'll get real deltas.
"""

import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def get_baseline_fixes() -> dict:
    """Define baseline fixes per symbol that has <30 trade issues."""
    return {
        "ZECUSDC": {
            "LONG": {
                "overrides": {
                    "WT_15M_BOUNCE_OPEN_ENABLED": True,
                    "WT_LOWER_CROSS_EXIT_TF": "15m",
                },
                "reason": "Baseline <30 trades, add WT_15M cross to get 300+ trades",
            },
            "SHORT": {
                "overrides": {
                    "WT_15M_BOUNCE_OPEN_ENABLED": True,
                    "WT_LOWER_CROSS_EXIT_TF": "15m",
                },
                "reason": "Baseline <30 trades, add WT_15M cross to get 300+ trades",
            },
        },
    }


def test_baseline_fix(sym_side: str, overrides: dict) -> dict:
    """Test if proposed baseline fix produces >=30 trades."""
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch

        sym = sym_side.rsplit("_", 1)[0]
        prepared = prepare_batch(sym, 30)

        # Current baseline
        current = evaluate_prepared_sanitized(prepared, {}, 30)

        # Fixed baseline
        fixed = evaluate_prepared_sanitized(prepared, overrides, 30)

        current_trades = current.get("trades", 0)
        fixed_trades = fixed.get("trades", 0)

        return {
            "sym_side": sym_side,
            "current_trades": current_trades,
            "current_gain": current.get("gain_pct", 0),
            "fixed_trades": fixed_trades,
            "fixed_gain": fixed.get("gain_pct", 0),
            "is_valid": fixed_trades >= 30,
            "delta_trades": fixed_trades - current_trades,
            "delta_gain": fixed.get("gain_pct", 0) - current.get("gain_pct", 0),
        }
    except Exception as e:
        return {
            "sym_side": sym_side,
            "error": str(e),
        }


def find_red_cell_symbols() -> set:
    """Find which symbols have red cells flagged."""
    flags_dir = Path(__file__).resolve().parents[1] / "data" / "reports" / "v15_flags"

    red_symbols = set()
    for flags_file in flags_dir.glob("*_flags.md"):
        try:
            with open(flags_file) as f:
                content = f.read()
                if "INVALID trades" in content or "stuck >10s" in content or "TIMEOUT" in content:
                    sym_side = flags_file.stem.replace("_flags", "")
                    red_symbols.add(sym_side)
        except Exception:
            pass

    return red_symbols


def generate_fix_report(fixes: dict) -> str:
    """Generate report of baseline fixes to apply."""
    report = "# Red Cell Baseline Fixes — Automated\n\n"

    for sym, sides in fixes.items():
        for side, fix_info in sides.items():
            sym_side = f"{sym}_{side}"
            report += f"## {sym_side}\n\n"

            # Test the fix
            test_result = test_baseline_fix(sym_side, fix_info["overrides"])

            if "error" in test_result:
                report += f"❌ ERROR: {test_result['error']}\n\n"
            else:
                current = test_result.get("current_trades", 0)
                fixed = test_result.get("fixed_trades", 0)
                is_valid = test_result.get("is_valid", False)

                status = "✅" if is_valid else "⚠️"
                report += f"{status} **Current baseline:** {current} trades, {test_result.get('current_gain', 0):.2f}% gain\n\n"
                report += f"{status} **Fixed baseline:** {fixed} trades, {test_result.get('fixed_gain', 0):.2f}% gain\n\n"

                if is_valid:
                    report += f"**✅ VALID** — {fixed} trades >= 30\n\n"
                else:
                    report += f"**⚠️ WEAK** — {fixed} trades < 30\n\n"

                report += f"**Overrides to apply:**\n```python\n"
                for key, val in fix_info["overrides"].items():
                    report += f"{key} = {repr(val)}\n"
                report += f"```\n\n"

                report += f"**Reason:** {fix_info['reason']}\n\n"

    report += "## Instructions\n\n"
    report += "1. For each symbol with ✅ VALID baseline:\n"
    report += "   - Add overrides to `config.py` or `config_tradier.py` as **defaults**\n"
    report += "   - OR add to template column C (override) for that symbol\n"
    report += "2. Re-run `v15_pilot.py` for affected sym_sides\n"
    report += "3. Red cells will re-evaluate with new baseline, get real deltas\n"
    report += "4. Verify no red cells remain: `python3 tools/red_cell_monitor_and_fixer.py --scan`\n"

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fix red cells by correcting baseline")
    parser.add_argument("--test", action="store_true", help="Test baseline fixes")
    parser.add_argument("--report", type=str, help="Generate report file")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to config (NOT YET IMPLEMENTED)")

    args = parser.parse_args()

    fixes = get_baseline_fixes()

    if args.test:
        print("Testing baseline fixes...\n")
        for sym, sides in fixes.items():
            for side, fix_info in sides.items():
                sym_side = f"{sym}_{side}"
                result = test_baseline_fix(sym_side, fix_info["overrides"])
                if "error" not in result:
                    print(f"{sym_side}:")
                    print(f"  Current: {result['current_trades']} trades")
                    print(f"  Fixed: {result['fixed_trades']} trades")
                    print(f"  Valid: {result['is_valid']}")
                else:
                    print(f"{sym_side}: ERROR {result['error']}")

    if args.report:
        report = generate_fix_report(fixes)
        report_file = Path(args.report)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with open(report_file, "w") as f:
            f.write(report)
        print(f"✅ Report saved to {report_file}")

    red_symbols = find_red_cell_symbols()
    if red_symbols:
        print(f"\nSymbols with red cells: {red_symbols}")
        print(f"Baseline fixes available for: {set(fixes.keys())}")

    if args.apply:
        print("⚠️ --apply not yet implemented")
        print("   Manually update config.py or add overrides to template column C")


if __name__ == "__main__":
    main()
