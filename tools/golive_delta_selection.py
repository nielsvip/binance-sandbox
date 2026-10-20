#!/usr/bin/env python3
"""golive_delta_selection — the subset of data/published_selection.json that is NOT live yet.

WHY. tools/v15_persym_golive.py applies the WHOLE selection every run and has no resume, no ordering and
no limit, so the hourly wrapper's `timeout 5400` turned it into a partial apply that restarted from the
same place every hour. Measured 2026-10-20 14:1xZ against the real live reader: of 474 published boards
41 resolved exactly, 271 differed only by the applier's own documented strippings, **159 differed
materially** and 3 had no live row at all — and six consecutive runs that day were killed before writing
their report, so nothing noticed.

The applier reads its work list from the file named by --published. So instead of patching the applier
(gate-scoped, and it has no resume to patch), this writes a SMALLER selection file holding only the
sym_sides whose live row does not match their published board, newest publish first. A delta apply
finishes inside the pre-open window; the hourly full apply keeps running unchanged as the backstop.

Safety: a subset can only ADD or CORRECT rows. v15_persym_golive --published iterates exactly the rows in
the file it is given (golive -> published_collected -> golive_rows) and never prunes a sym_side that is
absent from it, so a key left out of the delta keeps whatever it already has live.

The "is it live" verdict and the benign/material classification come from
tools/preopen_readiness_gate.scan_selection, which derives them from v15_persym_golive.ARTEFACT_KEYS /
migrate() and per_sym_live_set.CRYPTO_MIN_SIZE_CAPS — never from a list repeated here.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import preopen_readiness_gate as gate  # noqa: E402


def build(selection_path, out_path, limit=None, include_missing=True):
    items, exact, benign_only, offenders, missing_rows = gate.scan_selection(selection_path)
    source = json.loads(Path(selection_path).read_text())
    wanted = [o["sym_side"] for o in offenders]
    if include_missing:
        wanted += [k for k in missing_rows if k not in wanted]
    order = {k: (source["sym_sides"].get(k, {}).get("published_at") or "") for k in wanted}
    wanted.sort(key=lambda k: order[k], reverse=True)
    if limit:
        wanted = wanted[:limit]
    payload = {
        "_meta": {
            "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "by": "tools/golive_delta_selection.py",
            "source": str(selection_path),
            "source_meta": source.get("_meta"),
            "n": len(wanted),
            "scanned": len(items),
            "exact": exact,
            "benign_divergence_only": benign_only,
            "material_divergence": len(offenders),
            "no_live_row": len(missing_rows),
            "order": "published_at desc",
        },
        "sym_sides": {k: source["sym_sides"][k] for k in wanted},
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(out)
    return payload["_meta"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", default=str(gate.SELECTION))
    parser.add_argument("--out", default=str(ROOT / "data" / "published_selection_delta.json"))
    parser.add_argument("--limit", type=int, default=None, help="cap the delta (newest publishes kept)")
    parser.add_argument("--skip-missing", action="store_true", help="leave out sym_sides that have no live row at all")
    args = parser.parse_args()
    meta = build(args.selection, args.out, limit=args.limit, include_missing=not args.skip_missing)
    print(f"[golive-delta] {meta['n']} of {meta['scanned']} sym_sides need applying "
          f"(exact={meta['exact']} benign_only={meta['benign_divergence_only']} material={meta['material_divergence']} no_row={meta['no_live_row']}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
