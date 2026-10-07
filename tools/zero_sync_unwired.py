#!/usr/bin/env python3
"""zero_sync_unwired — keep data/vec_unwired.json consistent with Agent C's wiring status (data/wiring/status.json).
status.json (tolerated shapes): {"<KEY>": "<state>"} or {"<KEY>": {"state": "<state>"}} or {"switches": {...}}; states: staged | verified | deployed | failed.
  * STAGED copy  (data/zero_audit/staged/vec_unwired.staged.json): keys whose state in (staged, verified, deployed) are REMOVED (they will be evaluated in the staged round).
  * DEPLOYED copy (data/vec_unwired.json, pushed to hosts): only keys with state == deployed are removed, and only with --apply-deployed.
Never adds keys (v15_zero_audit does that); never touches switches_manual."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
ST = ROOT / "data" / "wiring" / "status.json"
UN = ROOT / "data" / "vec_unwired.json"
OUT = ROOT / "data" / "zero_audit" / "staged" / "vec_unwired.staged.json"
def states():
    if not ST.exists():
        return {}
    d = json.loads(ST.read_text())
    if isinstance(d.get("switches"), dict):
        d = {**d, **d["switches"]}
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = v.get("state") or v.get("status")
        if isinstance(v, str):
            out[k] = v.lower()
    return out
def main():
    s = states(); un = json.loads(UN.read_text())
    staged = {k for k, v in s.items() if v in ("staged", "verified", "deployed")}
    dep = {k for k, v in s.items() if v == "deployed"}
    st = dict(un); st["switches"] = sorted(set(un.get("switches", [])) - staged); st["filters"] = sorted(set(un.get("filters", [])) - staged)
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(st, indent=1))
    print(f"staged copy: {len(un.get('switches', []))}->{len(st['switches'])} switches, {len(un.get('filters', []))}->{len(st['filters'])} filters ({len(staged)} keys staged+)")
    if "--apply-deployed" in sys.argv:
        dd = dict(un); dd["switches"] = sorted(set(un.get("switches", [])) - dep); dd["filters"] = sorted(set(un.get("filters", [])) - dep)
        UN.write_text(json.dumps(dd, indent=1)); print(f"deployed copy updated: removed {len(dep)}")
if __name__ == "__main__":
    main()
