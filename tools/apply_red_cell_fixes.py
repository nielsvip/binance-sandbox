#!/usr/bin/env python3
"""
Apply Red Cell Fixes — Active repair tool
When a red cell timeout is detected, identify the cause and apply optimization.
Must ensure fixes match live trading logic exactly (compare with ez_manage/tradier_manage).
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def create_fix_for_switch(switch_name: str, is_crypto: bool = True) -> dict:
    """
    Create a fix strategy for a specific switch that's timing out.

    Returns:
      - fix_type: "numpy_optimize", "npz_fallback_fix", "remove_disk_io", etc.
      - location: file and line number
      - optimization: what to change
      - validation: how to verify
    """

    # Map common timeout switches to their likely issues
    TIMEOUT_SWITCHES = {
        "BB_SQUEEZE_WIDTH_PERCENTILE": {
            "fix_type": "numpy_optimize",
            "file": "v12_quick_engine.py",
            "search": "BB_SQUEEZE",
            "issue": "Bollinger band calculation may be doing per-bar computation",
            "fix": "Ensure BB calculation is fully vectorized; use pre-computed BB arrays from NPZ",
        },
        "ATR_LONG_WINDOW": {
            "fix_type": "npz_key_check",
            "file": "v12_quick_engine.py",
            "search": "ATR_LONG_WINDOW",
            "issue": "ATR calculation may fall back to computing from close when NPZ key missing",
            "fix": "Check if 'atr_*' keys exist in NPZ; if not, pre-compute or use fallback mask",
        },
        "WT_DC_HTF_GATE": {
            "fix_type": "remove_python_loop",
            "file": "v12_quick_engine.py",
            "search": "WT_DC_HTF_GATE",
            "issue": "Wave Trend HTF gate may be doing per-bar Python loop",
            "fix": "Vectorize HTF gate check using numpy.where() instead of bar-by-bar iteration",
        },
        "DC_BREAK_WAIT_WT15_CLOSE_ENABLED": {
            "fix_type": "numpy_optimize",
            "file": "v12_quick_engine.py",
            "search": "DC_BREAK_WAIT_WT15",
            "issue": "DC break detection may have per-bar Python logic",
            "fix": "Vectorize DC break mask computation; ensure WT_15M data is pre-loaded",
        },
        "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": {
            "fix_type": "remove_disk_io",
            "file": "vec_decisions/reduce_profit_lock.py",
            "search": "NOLOSS_BYPASS_WT_5OF5",
            "issue": "May be doing per-row file loads or network calls",
            "fix": "Ensure all WT arrays are pre-loaded; avoid disk I/O in bar loop",
        },
    }

    return TIMEOUT_SWITCHES.get(switch_name, {
        "fix_type": "unknown",
        "file": "unknown",
        "issue": f"Unknown timeout switch: {switch_name}",
        "fix": "Profile the switch to identify bottleneck",
    })


def check_switch_implementation(switch_name: str) -> dict:
    """
    Check where a switch is implemented and if it's optimized.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]

    # Search for switch in key files
    grep_results = {}
    for pattern in [switch_name, switch_name.lower()]:
        try:
            result = subprocess.run(
                ["grep", "-r", f"{pattern}", "--include=*.py", str(repo_root)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout:
                files = set()
                for line in result.stdout.split("\n"):
                    if ":" in line:
                        files.add(line.split(":")[0])
                grep_results[pattern] = list(files)
        except Exception:
            pass

    return {
        "switch": switch_name,
        "found_in": grep_results,
        "needs_analysis": True,
    }


def compare_with_live(switch_name: str, vec_result: float, live_result: float) -> dict:
    """
    Compare vector engine result with live trading result.
    Delta must be <1e-9 to ensure no logic divergence.
    """
    delta = abs(vec_result - live_result)
    return {
        "switch": switch_name,
        "vec_gain": vec_result,
        "live_gain": live_result,
        "delta": delta,
        "parity_ok": delta < 1e-9,
        "mismatch_pct": abs(delta / (live_result or 0.0001)) * 100,
    }


def generate_fix_report(sym_side: str, timeouts: list) -> str:
    """Generate a fix report for a sym_side with multiple timeouts."""

    report = f"""# Fix Report for {sym_side} — {datetime.utcnow().isoformat()}Z

## Summary
- Timeouts detected: {len(timeouts)}
- First timeout at: {timeouts[0] if timeouts else 'N/A'}
- Pattern: All cells at same cumulative baseline (system stall after baseline)

## Root Cause Hypothesis
The baseline evaluation succeeds (~0.07s), but then the ThreadPool/evaluation cache
becomes unable to process subsequent variants. This suggests:

1. **Per-row disk reload** — a filter is calling file I/O in the variant loop
2. **Missing NPZ key with fallback** — stdev_edge_* or similar not cached; fallback recomputes
3. **Slow vec_decisions function** — a mask calculation takes >10s on this symbol
4. **ThreadPool deadlock** — the Executor's wait() call hangs on unfinished futures

## Investigation Steps (in order)

### Step 1: Verify NPZ preload
Check that NPZ keys are loaded and no disk I/O happens during evaluation.

### Step 2: Profile the first timeout switch
Profile which specific calculation is timing out.


### Step 3: Check if it's a disk reload
```bash
grep -n "\.seek\\|np\\.load\\|npz\\[" v12_quick_engine.py | head -20
```

## Fixes to Apply

"""

    for timeout_switch in timeouts[:5]:  # Top 5 timeouts
        fix = create_fix_for_switch(timeout_switch)
        report += f"""### {timeout_switch}
- **Type:** {fix.get('fix_type', 'unknown')}
- **File:** {fix.get('file', 'unknown')}
- **Issue:** {fix.get('issue', 'Unknown')}
- **Fix:** {fix.get('fix', 'Manual investigation needed')}

"""

    report += """## Validation Checklist

- [ ] Identified bottleneck function
- [ ] Confirmed it's not per-row disk I/O
- [ ] Optimized to numpy-only vectorization
- [ ] Verified delta unchanged from original (<1e-9)
- [ ] Tested on ≥3 symbols (crypto + stocks)
- [ ] Compared vector result with live trading result
- [ ] Deployed to S1/S2/S5
- [ ] Restarted herd and monitored for regressions

## Next Steps

1. Run diagnosis script: `python3 tools/diagnose_baseline_stall.py`
2. Identify slow function from output
3. Optimize that function to <100ms
4. Validate delta unchanged
5. Deploy: `rsync -az <file> s1-int:~/binance-sandbox/`
6. Restart: `ssh s1-int "pkill -f v15_pilot.py"`
7. Monitor flags directory for improvement
"""

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Apply fixes for red cell timeouts")
    parser.add_argument("--sym-side", type=str, help="Generate fix report for sym_side")
    parser.add_argument("--check", type=str, help="Check implementation of a switch")
    parser.add_argument("--apply-fix", type=str, help="Apply a specific fix (not yet automated)")

    args = parser.parse_args()

    if args.sym_side:
        # Extract timeouts from flags file
        flags_dir = Path(__file__).resolve().parents[1] / "data" / "reports" / "v15_flags"
        flags_file = flags_dir / f"{args.sym_side}_30d_flags.md"

        timeouts = []
        if flags_file.exists():
            with open(flags_file) as f:
                for line in f:
                    if "stuck >10s" in line or "TIMEOUT" in line:
                        # Extract switch name
                        parts = line.split("|")
                        if len(parts) >= 4:
                            switch = parts[3].strip()
                            if switch and switch not in timeouts:
                                timeouts.append(switch)

        report = generate_fix_report(args.sym_side, timeouts)
        print(report)

        # Save report
        report_file = Path(__file__).resolve().parents[1] / "data" / "reports" / f"fix_plan_{args.sym_side}.md"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with open(report_file, "w") as f:
            f.write(report)
        print(f"\n[saved] Report to {report_file}")

    elif args.check:
        result = check_switch_implementation(args.check)
        print(json.dumps(result, indent=2, default=str))

    else:
        print("Usage: python3 apply_red_cell_fixes.py --sym-side <SYM>_<SIDE> [--check <SWITCH>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
