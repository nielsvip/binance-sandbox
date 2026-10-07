#!/usr/bin/env python3
"""csd4_fill_missing — add ONLY the missing config fields (current venue-config/QuickConfig value, never a template promotion) to data/cat_side_defaults_4.json.
For when tools/build_cat_side_defaults_4.py refuses (template default violations) but new config fields appeared. Existing values are never touched. Atomic write + backup."""
import json, shutil, sys, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import build_cat_side_defaults_4 as B
import cat_side_defaults as CSD
data = json.loads(CSD.PATH.read_text())
shutil.copy2(CSD.PATH, ROOT / "backups" / f"before_csd4_fill_missing_{datetime.datetime.now():%Y%m%d%H%M}.json")
for cs in CSD.CAT_SIDES:
    _t, live, quick = B.venue_values(cs.startswith("STOCKS"))
    before = len(data[cs])
    kept = dict(data[cs])
    filled, conflicts, skipped = B.fill_every_field(kept, live, quick)
    data[cs] = kept
    print(cs, "added", len(kept) - before, filled[:6])
tmp = CSD.PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=1, default=str, sort_keys=True)); tmp.replace(CSD.PATH)
