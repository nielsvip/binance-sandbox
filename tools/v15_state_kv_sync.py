#!/usr/bin/env python3
"""v15_state_kv_sync — make the per_sym_store kv_json rows equal the chain's JSON state files (daily chain, 2026-10-06).

cat_side_defaults._load and v15_daily_template_update read SQL first (kv_json) and the JSON only as fallback, so a pushed/pulled
JSON alone does NOT change what they read when a stale kv row exists. Every host that receives the chain's JSONs runs this after
the copy. Keys: cat_side_defaults_4, cat_side_promotions, avg_delta_round_ledger.

  python tools/v15_state_kv_sync.py            # report kv==json per key (exit 0)
  python tools/v15_state_kv_sync.py --apply    # kv_put every key whose JSON exists and differs, then re-read verify (exit 1 on mismatch)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import per_sym_store as pss  # noqa: E402

PAIRS = ((pss.KV_CAT_SIDE_DEFAULTS_4, ROOT / "data" / "per_sym_settings.json"), (pss.KV_CAT_SIDE_PROMOTIONS, ROOT / "data" / "cat_side_promotions.json"), (pss.KV_AVG_DELTA_ROUND_LEDGER, ROOT / "data" / "avg_delta_round_ledger.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    bad = 0
    for key, path in PAIRS:
        if not path.exists():
            print(f"[kv-sync] {key}: json missing ({path}) - skipped")
            continue
        js = json.loads(path.read_text())
        same = pss.kv_get(key) == js
        if a.apply and not same:
            pss.kv_put(key, js)
            same = pss.kv_get(key) == js
            print(f"[kv-sync] {key}: kv_put from {path.name} -> verify {'OK' if same else 'FAILED'}")
            bad += 0 if same else 1
        else:
            print(f"[kv-sync] {key}: kv==json {same}")
    print(f"[kv-sync] db {pss.DB_PATH}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
