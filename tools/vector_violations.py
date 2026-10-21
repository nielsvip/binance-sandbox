#!/usr/bin/env python3
"""vector_violations — manual restore console for the vector-copy-trading law.

A disabled family can NEVER act again until restored here, by a human, on purpose.
Usage:
  python3 tools/vector_violations.py list [--n 20]
  python3 tools/vector_violations.py kills
  python3 tools/vector_violations.py restore FAMILY [--why TEXT]   # manual restore, logged
  python3 tools/vector_violations.py disable FAMILY --why TEXT     # manual disable, logged
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live_twins import vector_enforce as ve


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 20
        try:
            lines = Path(ve.VIOLATIONS).read_text().splitlines()[-n:]
        except OSError:
            lines = []
        for line in lines:
            try:
                d = json.loads(line)
                print(
                    f"{time.strftime('%H:%M', time.gmtime(d['ts']))} {d['verdict']} {d['position_key']} {d['reason'][:80]}"
                )
            except Exception:
                pass
        return 0
    if cmd == "kills":
        print(json.dumps(ve._load_kills(), indent=1))
        return 0
    if cmd in ("restore", "disable"):
        fam = argv[2].upper() if len(argv) > 2 else ""
        why = argv[argv.index("--why") + 1] if "--why" in argv else "manual"
        if not fam:
            print("need FAMILY")
            return 2
        kills = ve._load_kills()
        if cmd == "restore":
            kills.pop(fam, None)
            print(f"RESTORED {fam} ({why}) — it may act again")
        else:
            kills[fam] = {"disabled_at": time.time(), "why": f"manual: {why}"}
            print(f"DISABLED {fam} ({why})")
        ve.KILLS.write_text(json.dumps(kills, indent=1))
        ve._KILLS["loaded"] = time.time()
        try:
            with open(ve.VIOLATIONS, "a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "action": "MANUAL",
                            "position_key": fam,
                            "reason": why,
                            "family": fam,
                            "verdict": cmd.upper(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
