#!/usr/bin/env python3
"""workbook_to_manifest.py — push hand-edits in data/param_workbook.csv back into the
per-mode sweep manifests the OFAT runners consume, so editing the spreadsheet changes
what gets swept next.

Reads data/param_workbook.csv. For each (system, param) row it overwrites, in
data/param_sweep_manifest_<mode>.json:
  * sweepable    <- (sweep_enabled == 'Y')   AND a non-empty test_values list
  * test_values  <- parsed from the 'test_values' cell (pipe-separated, type-coerced)
Everything else in the manifest is preserved.

If 'user_set_value' is filled, it is collected into data/param_user_overrides_<mode>.json
— a baseline override the screen runner can merge in as the starting config (so you can
pin a param to a value while sweeping others).

NO-LIES: this only edits the SEARCH SPACE + baseline. It never writes a metric and never
promotes anything live. Sweeps still route results through metrics_guard at floor.
"""
import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _coerce(tok, typ):
    tok = tok.strip()
    if tok in ("True", "False"):
        return tok == "True"
    if typ == "bool":
        return tok.lower() in ("true", "1", "y", "yes")
    try:
        if typ == "int" or ("." not in tok and "e" not in tok.lower()):
            return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/param_workbook.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rows = list(csv.DictReader((BASE / args.csv).open()))
    by_mode = {}
    for r in rows:
        by_mode.setdefault(r["system"], []).append(r)
    for mode, mrows in by_mode.items():
        mp = BASE / f"data/param_sweep_manifest_{mode}.json"
        if not mp.exists():
            print(f"skip {mode}: no manifest")
            continue
        man = json.loads(mp.read_text())
        params = man["params"]
        changed = 0
        overrides = {}
        for r in mrows:
            name = r["param"]
            if name not in params:
                continue
            typ = params[name].get("type", "")
            tv_cell = (r.get("test_values") or "").strip()
            tv = [_coerce(t, typ) for t in tv_cell.split("|") if t.strip()] if tv_cell else []
            enabled = (r.get("sweep_enabled", "").strip().upper() == "Y") and bool(tv)
            old_sw, old_tv = params[name].get("sweepable"), params[name].get("test_values")
            if old_sw != enabled or old_tv != (tv or None):
                changed += 1
            params[name]["sweepable"] = enabled
            params[name]["test_values"] = tv or None
            usv = (r.get("user_set_value") or "").strip()
            if usv:
                overrides[name] = _coerce(usv, typ)
        man["tier_counts_sweepable_after_edit"] = sum(1 for v in params.values() if v.get("sweepable"))
        if not args.dry_run:
            mp.write_text(json.dumps(man, indent=2))
            if overrides:
                op = BASE / f"data/param_user_overrides_{mode}.json"
                op.write_text(json.dumps(overrides, indent=2))
        print(f"{mode}: {changed} params changed, sweepable_now={man['tier_counts_sweepable_after_edit']}, "
              f"user_overrides={len(overrides)}" + ("  [DRY-RUN]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
