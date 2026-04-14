# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Switch-Wiring Verifier — anti-revert alarm.

Session-start check (per CLAUDE.md DEATH PENALTY rule): every canonical switch
in data/sweep_alerts/canonical_switches.json MUST appear in its declared files.
If any are missing or moved to DISABLED_BY_DESIGN without note, raise a red alert.

Usage:
  python verify_switches.py              # print report, exit 0 if all OK, 1 if any dead
  python verify_switches.py --json       # machine-readable output
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CANON = BASE / "data" / "sweep_alerts" / "canonical_switches.json"


def _grep(pattern, file_path):
    try:
        r = subprocess.run(["grep", "-c", pattern, str(BASE / file_path)], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip() or "0")
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not CANON.exists():
        print(f"FATAL: {CANON} missing")
        sys.exit(2)
    data = json.loads(CANON.read_text())
    report = {"ok": [], "missing": [], "disabled_by_design": [], "partial": []}
    for name, meta in data["switches"].items():
        status = meta.get("implemented", "MISSING")
        hits = {f: _grep(name, f) for f in meta["files"]}
        entry = {"switch": name, "category": meta["category"], "files": hits, "status": status}
        if status == "MISSING":
            report["missing"].append(entry)
        elif status == "DISABLED_BY_DESIGN":
            report["disabled_by_design"].append(entry)
        elif status == "partial":
            report["partial"].append(entry)
        else:
            if all(c > 0 for c in hits.values()):
                report["ok"].append(entry)
            else:
                entry["status"] = "WIRING_LOST"
                report["missing"].append(entry)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n=== Canonical Switch Verification ===")
        print(f"  OK ...........: {len(report['ok'])}")
        print(f"  Partial ......: {len(report['partial'])}")
        print(f"  Disabled-by-design: {len(report['disabled_by_design'])}")
        print(f"  MISSING (DEATH PENALTY if a revert): {len(report['missing'])}")
        if report["missing"]:
            print("\n  MISSING SWITCHES:")
            for e in report["missing"]:
                print(f"    ❌ {e['switch']} [{e['category']}] — files: {e['files']}")
        if report["partial"]:
            print("\n  PARTIAL (has some refs but may not gate trades):")
            for e in report["partial"]:
                print(f"    ⚠ {e['switch']} — {e['files']}")
    sys.exit(1 if report["missing"] else 0)


if __name__ == "__main__":
    main()
