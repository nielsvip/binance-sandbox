#!/usr/bin/env python3
"""Refresh the live Mac category sets used only when a symbol has no per-sym row."""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import per_sym_settings

DEST = ROOT / "data" / "vec_live" / "cat_side_full_defaults.json"


def main():
    categories = {name: per_sym_settings.defaults(name) for name in ("CRYPTO_LONG", "CRYPTO_SHORT")}
    if any(len(value) < 3000 for value in categories.values()):
        raise RuntimeError("live category defaults incomplete")
    payload = {"_meta": {"generated_ts": time.time(), "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}, **categories}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".cat_side_full_defaults.", suffix=".json", dir=DEST.parent, delete=False) as stream:
        json.dump(payload, stream, sort_keys=True)
        temp = pathlib.Path(stream.name)
    os.replace(temp, DEST)
    for host in ("s1-pub", "s2"):
        subprocess.run(["rsync", "-az", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=8", str(DEST), f"{host}:~/binance-sandbox/data/vec_live/cat_side_full_defaults.json"], check=True, timeout=90)
    print(f"cat_defaults_pushed {payload['_meta']['generated_utc']} long={len(categories['CRYPTO_LONG'])} short={len(categories['CRYPTO_SHORT'])}", flush=True)


if __name__ == "__main__":
    main()
