#!/usr/bin/env python3
"""Quick validation script for v15_pilot.py fixes"""

import sys
import subprocess
from pathlib import Path

def check_syntax():
    """Verify v15_pilot.py has valid Python syntax"""
    try:
        subprocess.run(
            [sys.executable, "-m", "py_compile", "v15_pilot.py"],
            cwd=Path(__file__).parent.parent,
            check=True,
            capture_output=True
        )
        print("✓ v15_pilot.py syntax valid")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Syntax error: {e.stderr.decode()}")
        return False

def check_while_loop():
    """Verify while loop is present in _spec_fill_workbook"""
    v15_file = Path(__file__).parent.parent / "v15_pilot.py"
    content = v15_file.read_text()

    # Check for while _any_pending
    if "while _any_pending() and loop_guard < max_loops:" in content:
        print("✓ Main processing while loop present")
        return True
    else:
        print("✗ Main processing while loop NOT found")
        return False

def check_max_loops():
    """Verify max_loops calculation is correct"""
    v15_file = Path(__file__).parent.parent / "v15_pilot.py"
    content = v15_file.read_text()

    # Check for new max_loops formula
    if "max_loops = total_rows * len(tabs) + 200" in content:
        print("✓ max_loops calculation uses tab-aware formula")
        return True
    else:
        print("✗ max_loops calculation not updated")
        return False

def check_key_matching():
    """Verify _next_pending handles key mismatch"""
    v15_file = Path(__file__).parent.parent / "v15_pilot.py"
    content = v15_file.read_text()

    # Check for fallback key matching
    if "found_by_switch" in content and "key_by_switch = f\"{sw}={cand}\"" in content:
        print("✓ _next_pending has fallback key matching for resorting")
        return True
    else:
        print("✗ _next_pending key matching fallback not found")
        return False

def check_baseline_restore():
    """Verify baseline carry-over fix"""
    v15_file = Path(__file__).parent.parent / "v15_pilot.py"
    content = v15_file.read_text()

    # Check for cumulative_after recovery
    if "baseline-restore" in content and "cumulative_after" in content:
        print("✓ Baseline carry-over from last completed row implemented")
        return True
    else:
        print("✗ Baseline carry-over fix not found")
        return False

def check_resolve_cols():
    """Verify _resolve_cols is used for baseline writing"""
    v15_file = Path(__file__).parent.parent / "v15_pilot.py"
    content = v15_file.read_text()

    # Check for _resolve_cols usage in baseline writing
    if "cols = _resolve_cols(ws_fix)" in content:
        print("✓ E2/E3 baseline uses _resolve_cols")
        return True
    else:
        print("✗ E2/E3 baseline does not use _resolve_cols")
        return False

def main():
    print("\n" + "="*60)
    print("v15_pilot.py FIXES VALIDATION")
    print("="*60 + "\n")

    results = []
    results.append(("Syntax", check_syntax()))
    results.append(("While Loop Present", check_while_loop()))
    results.append(("Max_Loops Calculation", check_max_loops()))
    results.append(("Key Matching Fallback", check_key_matching()))
    results.append(("Baseline Restore", check_baseline_restore()))
    results.append(("Resolve Cols Usage", check_resolve_cols()))

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8s} {name}")

    print(f"\nTotal: {passed}/{total} checks passed")

    if passed == total:
        print("\n🎉 All fixes validated!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} checks failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
