#!/usr/bin/env python3
"""Backfill per_sym_store.db from existing JSON files.

For each sym_side in per_sym_active_config.json (+ stocks + trb/trc):
  - load overrides
  - build defaults snapshot for that cat_side at THIS moment (cat_side_defaults)
  - full_config = defaults + overrides
  - upsert into SQLite (and re-touch JSON backup with full snapshot)

Idempotent: re-running does not duplicate history, just updates the snapshot
to the current defaults (so after a TEMPLATE change, re-backfill refreshes).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cat_side_defaults as csd
import per_sym_store as pss

JSON_FILES = [
    ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json",
    ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json",
    ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json",
    ROOT / "data" / "hourly_reconfig" / "trc" / "active_config.json",
]

def backfill():
    total = 0
    for jf in JSON_FILES:
        if not jf.exists():
            print(f"[skip] {jf} not found")
            continue
        try:
            raw = json.loads(jf.read_text())
        except Exception as e:
            print(f"[warn] {jf} load {e}")
            continue
        n = 0
        for sym_side, entry in raw.items():
            if sym_side == "_meta" or not isinstance(entry, dict):
                continue
            overrides = entry.get("overrides") or {}
            if not overrides:
                continue
            # cat_side
            parts = sym_side.rsplit("_", 1)
            sym = parts[0] if len(parts) == 2 else sym_side
            side = parts[1] if len(parts) == 2 else "LONG"
            cat = csd.cat_side_of(sym, side)
            defaults = csd.defaults(cat)
            full = dict(defaults)
            full.update(overrides)
            # meta from entry
            meta = {k: v for k, v in entry.items() if k not in ("overrides", "defaults_snapshot", "full_config")}
            # template md5
            meta.setdefault("winning_tag", entry.get("winning_tag", "backfill"))
            pss.upsert(sym_side, overrides, defaults, full, meta, json_path=jf)
            n += 1
        print(f"[backfill] {jf.name}: {n} sym_sides -> {pss.DB_PATH}")
        total += n
    health = pss.health()
    print(f"[health] {health}")
    print(f"[total] {total} entries backfilled")


if __name__ == "__main__":
    backfill()
