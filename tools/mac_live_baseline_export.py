"""Export the exact Mac live per-symbol sets for isolated fleet baseline inputs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import per_sym_live_set
import per_sym_store

DEST = ROOT / "data" / "live_baseline_snapshots"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    # One consistent SQLite read transaction: calling full_set() once per symbol
    # can re-prime the entire live cache on every concurrent WAL change.
    with sqlite3.connect(f"file:{per_sym_store.DB_PATH}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT sym_side, full_config_json FROM per_sym_active ORDER BY sym_side"
        ).fetchall()
    hashes: dict[str, str] = {}
    for name, encoded in rows:
        if not encoded:
            continue
        try:
            raw = json.loads(per_sym_store._maybe_decompress(encoded))
            full = per_sym_live_set._apply_caps(name, per_sym_store._mandate_sanitize(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(full, dict) or not full:
            continue
        data = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
        sha = hashlib.sha256(data).hexdigest()
        hashes[name] = sha
        dest = DEST / f"{name}.json"
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
            continue
        temp = dest.with_suffix(".json.tmp")
        temp.write_bytes(data)
        os.replace(temp, dest)
    manifest = {"source": "mac_per_sym_live_set.full_set", "generated_at": time.time(), "sets": hashes}
    temp = DEST / "manifest.json.tmp"
    temp.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    os.replace(temp, DEST / "manifest.json")
    print("EXPORTED", len(hashes), "sets", "to", DEST)


if __name__ == "__main__":
    main()
