"""Read a hash-checked snapshot of the settings currently consumed on the Mac.

Backtest hosts receive these files before launching the next symbol. The
snapshot is separate from their writable per_sym_store so a completed backtest
can publish its result without overwriting the Mac input of another symbol.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class LiveSnapshotError(RuntimeError):
    pass


def load_live_entry(root: Path, sym_side: str, *, max_age_seconds: int = 900):
    directory = Path(root) / "data" / "live_baseline_snapshots"
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise LiveSnapshotError(f"Mac live baseline manifest missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("source") != "mac_per_sym_live_set.full_set":
            raise ValueError("wrong source")
        age = time.time() - float(manifest["generated_at"])
        if age < -60 or age > max_age_seconds:
            raise ValueError(f"snapshot age {age:.0f}s exceeds {max_age_seconds}s")
        expected = manifest["sets"].get(sym_side)
        if expected is None:
            return None  # New symbol: cat_side is its only baseline.
        payload = (directory / f"{sym_side}.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("set hash mismatch")
        full = json.loads(payload)
        if not isinstance(full, dict) or len(full) < 100:
            raise ValueError("incomplete full set")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LiveSnapshotError(f"Mac live baseline invalid for {sym_side}: {exc}") from exc
    return {"full_config": full, "overrides": full, "source": "Mac live full_set snapshot"}
