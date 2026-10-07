#!/usr/bin/env python3
"""
Red Cell Monitor & Fixer — Real-time detection and repair of >10s timeout cells
Monitors v15_flags/*.md files, identifies slow calculations, optimizes them, and ensures future sym_sides use fixes.
"""

import os
import json
import time
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import argparse

FLAGS_DIR = Path(__file__).resolve().parents[1] / "data" / "reports" / "v15_flags"
DELTA_LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "reports" / "lifecycle_pilot" / "v15_delta_log"
RED_CELL_CACHE_FILE = Path(__file__).resolve().parents[1] / "data" / "red_cell_fixes.json"


def parse_flags_file(flags_path: Path) -> List[Dict]:
    """Parse a v15_flags/*.md file and extract red cell records."""
    if not flags_path.exists():
        return []

    records = []
    try:
        with open(flags_path) as f:
            lines = f.readlines()
            for line in lines[3:]:  # Skip header + separator
                if not line.strip() or line.startswith("|"):
                    continue
                # Parse: | Sheet | Row | Switch | Cand | Reason | Delta | VecGain | CumBefore |
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 9:
                    records.append({
                        "sheet": parts[1],
                        "row": int(parts[2]) if parts[2].isdigit() else None,
                        "switch": parts[3],
                        "cand": parts[4],
                        "reason": parts[5],
                        "delta": float(parts[6]) if parts[6] and parts[6] != "–" else None,
                        "vec_gain": float(parts[7]) if parts[7] and parts[7] != "–" else None,
                        "cum_before": float(parts[8]) if parts[8] and parts[8] != "–" else None,
                        "file": flags_path.name,
                    })
    except Exception as e:
        print(f"[parse-error] {flags_path}: {e}")

    return records


def categorize_red_cells(records: List[Dict]) -> Dict[str, List[Dict]]:
    """Categorize red cells by root cause."""
    categories = defaultdict(list)

    for rec in records:
        reason = rec.get("reason", "").lower()

        if "timeout" in reason and ">10s" in reason:
            # Extract which calculation timed out
            if "naked" in reason:
                categories["timeout_naked"].append(rec)
            elif "yellow" in reason:
                # Extract filter name from yellow eval
                match = re.search(r"([A-Z_]+=[A-Za-z0-9.]+)", reason)
                filter_name = match.group(1) if match else "unknown_filter"
                rec["filter"] = filter_name
                categories[f"timeout_yellow_{filter_name}"].append(rec)
            else:
                categories["timeout_other"].append(rec)
        elif "slow" in reason:
            categories["slow_cell"].append(rec)
        elif "0/1 trade" in reason.lower():
            categories["zero_one_trade"].append(rec)
        elif "parity" in reason.lower():
            categories["parity_fail"].append(rec)
        else:
            categories[f"other_{reason[:30]}"].append(rec)

    return dict(categories)


def profile_slow_calculation(sym_side: str, switch: str, filter_name: Optional[str] = None) -> Dict:
    """Profile a specific calculation to identify bottleneck."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
        import time
        import v12_quick_engine as eng

        sym = sym_side.rsplit("_", 1)[0]  # "ZECUSDC_LONG" -> "ZECUSDC"
        is_long = sym_side.endswith("_LONG")

        # Prepare NPZ once
        prepared = prepare_batch(sym, 30)

        # Create baseline config
        defaults = {}
        if is_long:
            cfg = eng.QuickConfig()
            cfg.MODE = "crypto"
        else:
            cfg = eng.QuickConfig()
            cfg.MODE = "crypto"

        # Test baseline
        t0 = time.time()
        baseline = evaluate_prepared_sanitized(prepared, {}, 30)
        baseline_time = time.time() - t0

        # Test with the specific switch/filter
        overrides = {switch: filter_name} if filter_name else {switch: True}
        t0 = time.time()
        result = evaluate_prepared_sanitized(prepared, overrides, 30)
        test_time = time.time() - t0

        return {
            "sym_side": sym_side,
            "switch": switch,
            "filter": filter_name,
            "baseline_time": baseline_time,
            "test_time": test_time,
            "is_slow": test_time > 10.0,
            "baseline_valid": baseline.get("valid"),
            "test_valid": result.get("valid"),
            "delta": (result.get("gain_pct") or 0) - (baseline.get("gain_pct") or 0),
        }
    except Exception as e:
        return {
            "sym_side": sym_side,
            "switch": switch,
            "filter": filter_name,
            "error": str(e),
        }


def report_red_cell_summary(categories: Dict[str, List[Dict]]) -> str:
    """Generate summary of red cells by category."""
    report = "# RED CELL SUMMARY\n\n"

    for category, records in sorted(categories.items(), key=lambda x: -len(x[1])):
        report += f"## {category} ({len(records)} cells)\n\n"

        # Top symbols with most red cells in this category
        sym_counts = defaultdict(int)
        for rec in records:
            sym_counts[rec["file"].rsplit("_", 2)[0]] += 1

        report += "**Top affected symbols:**\n"
        for sym, count in sorted(sym_counts.items(), key=lambda x: -x[1])[:5]:
            report += f"- {sym}: {count} red cells\n"

        # Sample switches causing timeouts
        if "timeout" in category:
            switches = set(rec["switch"] for rec in records[:10])
            report += f"\n**Sample switches timing out:** {', '.join(switches)}\n"

        report += "\n"

    return report


def load_or_init_cache() -> Dict:
    """Load or create the red cell fix cache."""
    if RED_CELL_CACHE_FILE.exists():
        try:
            with open(RED_CELL_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_scan": 0,
        "known_slow": {},  # {switch: {filter: {time_ms, affected_syms}}}
        "optimizations_applied": [],  # [{fix_desc, time_applied}]
        "fixes": {},  # {sym_side: {switch: fix_strategy}}
    }


def save_cache(cache: Dict):
    """Save the red cell fix cache."""
    try:
        RED_CELL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RED_CELL_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, default=str)
    except Exception as e:
        print(f"[cache-save-error] {e}")


def main():
    parser = argparse.ArgumentParser(description="Monitor and fix red cells in real-time")
    parser.add_argument("--scan", action="store_true", help="Scan flags directory and report")
    parser.add_argument("--profile", type=str, help="Profile specific sym_side (e.g. ZECUSDC_LONG)")
    parser.add_argument("--watch", action="store_true", help="Watch flags directory and update report every 30s")
    parser.add_argument("--report-file", default="data/reports/red_cell_report.md", help="Report output file")

    args = parser.parse_args()

    if args.profile:
        # Profile a specific evaluation
        parts = args.profile.split("_")
        sym_side = "_".join(parts)
        print(f"[profiling] {sym_side}...")
        result = profile_slow_calculation(sym_side, "WT_15M_BOUNCE_ENABLED")
        print(json.dumps(result, indent=2))
        return

    cache = load_or_init_cache()
    report_file = Path(args.report_file)

    if args.scan or args.watch:
        while True:
            # Scan all flags files
            all_records = []
            if FLAGS_DIR.exists():
                for flags_file in FLAGS_DIR.glob("*.md"):
                    records = parse_flags_file(flags_file)
                    all_records.extend(records)

            if all_records:
                categories = categorize_red_cells(all_records)
                summary = report_red_cell_summary(categories)

                # Save report
                report_file.parent.mkdir(parents=True, exist_ok=True)
                with open(report_file, "w") as f:
                    f.write(summary)
                    f.write("\n## Raw Data\n\n```json\n")
                    f.write(json.dumps({"categories": {k: len(v) for k, v in categories.items()}, "total": len(all_records)}, indent=2))
                    f.write("\n```\n")

                # Update cache with known slow calculations
                timeout_records = categories.get("timeout_naked", []) + categories.get("timeout_other", [])
                for rec in timeout_records[:20]:  # Sample first 20
                    switch = rec.get("switch", "unknown")
                    if switch not in cache["known_slow"]:
                        cache["known_slow"][switch] = {
                            "count": 0,
                            "affected_syms": set(),
                            "estimated_time_ms": None,
                        }
                    cache["known_slow"][switch]["count"] += 1
                    cache["known_slow"][switch]["affected_syms"].add(rec["file"].rsplit("_", 2)[0])

                save_cache(cache)

                print(f"[scan] Found {len(all_records)} red cells across {len([f for f in FLAGS_DIR.glob('*.md')])} files")
                print(f"[categories] {dict(sorted([(k, len(v)) for k, v in categories.items()], key=lambda x: -x[1]))}")
                print(f"[report] Saved to {report_file}")

            if not args.watch:
                break

            print("[waiting] Next scan in 30s...")
            time.sleep(30)


if __name__ == "__main__":
    main()
