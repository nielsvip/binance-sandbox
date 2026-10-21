#!/usr/bin/env python3
"""vector_violations — manual restore console for the vector-copy-trading law.

A disabled family can NEVER act again until restored here, by a human, on purpose.
Every new kill also files a retrace work order (BIBLE §126.1 standing agent job):
an agent vectorizes it (switch + shared scalar/vec core, both venues) or notes
non-vectorizable (stays killed, switch default OFF).
Usage:
  python3 tools/vector_violations.py list [--n 20]
  python3 tools/vector_violations.py kills
  python3 tools/vector_violations.py restore FAMILY [--why TEXT]   # manual restore, logged
  python3 tools/vector_violations.py disable FAMILY --why TEXT     # manual disable, logged
  python3 tools/vector_violations.py queue                          # pending retrace work orders
  python3 tools/vector_violations.py resolve FAMILY --note TEXT --verdict vectorized|non_vectorizable
  python3 tools/vector_violations.py resolutions                   # recorded verdicts
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
    if cmd == "queue":
        try:
            lines = Path(ve.RETRACE_QUEUE).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        try:
            res_lines = Path(ve.RESOLUTIONS).read_text(encoding="utf-8").splitlines()
        except OSError:
            res_lines = []
        resolved_after = {}
        for line in res_lines:
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if r.get("family"):
                resolved_after[r["family"]] = max(
                    resolved_after.get(r["family"], 0.0), float(r.get("ts", 0.0))
                )
        pending = 0
        for line in lines:
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if d.get("status") != "TODO":
                continue
            if resolved_after.get(d.get("family"), 0.0) >= float(d.get("ts", 0.0)):
                continue
            pending += 1
            print(
                f"{time.strftime('%m-%d %H:%M', time.gmtime(d['ts']))} {d['family']} "
                f"{d.get('example_action', '')} {d.get('example_reason', '')[:90]}"
            )
        if not pending:
            print("queue empty — every killed family has a retrace resolution")
            print("verdicts: python3 tools/vector_violations.py resolutions")
        return 0
    if cmd == "resolutions":
        try:
            lines = Path(ve.RESOLUTIONS).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        if not lines:
            print("no resolutions recorded")
            return 0
        for line in lines[-20:]:
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            print(
                f"{time.strftime('%m-%d %H:%M', time.gmtime(r['ts']))} {r['family']} "
                f"{r['verdict']} {r.get('note', '')[:100]}"
            )
        return 0
    if cmd == "resolve":
        fam = argv[2].upper() if len(argv) > 2 else ""
        note = argv[argv.index("--note") + 1] if "--note" in argv else ""
        verdict = argv[argv.index("--verdict") + 1] if "--verdict" in argv else ""
        if not fam or not note or verdict not in ("vectorized", "non_vectorizable"):
            print(
                "usage: resolve FAMILY --note TEXT --verdict vectorized|non_vectorizable"
            )
            return 2
        try:
            with open(ve.RESOLUTIONS, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "family": fam,
                            "verdict": verdict,
                            "note": note[:500],
                        }
                    )
                    + "\n"
                )
        except OSError as e:
            print(f"resolve failed: {e}")
            return 1
        print(
            f"RESOLVED {fam} as {verdict} ({note[:80]}) — "
            + (
                "restore it + list in VEC_SWITCH_EXIT_FAMILIES (done separately, never re-kill)"
                if verdict == "vectorized"
                else "stays killed, switch default OFF"
            )
        )
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
