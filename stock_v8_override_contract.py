"""Fail-closed environment contract for explicit stock V8 overrides."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import MutableMapping


def establish_stock_v8_override(
    env: MutableMapping[str, str],
    override_file: str | Path | None,
) -> dict:
    """Bind an explicit Tradier research override above accepted overlays.

    An empty path is the accepted baseline and deliberately leaves per-symbol
    overlays untouched. A non-empty path must be a readable JSON object.
    """
    raw = str(override_file or "").strip()
    if not raw:
        return {
            "kind": "BASELINE_OVERLAY_PRESERVED",
            "override_file": None,
            "override_sha256": None,
            "explicit_keys": 0,
        }
    path = Path(raw)
    if not path.is_file():
        raise RuntimeError(f"Tradier V8 override file does not exist: {path}")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError("Tradier V8 override must be a JSON object")
    env["V8_OVERRIDE_FILE"] = str(path)
    env["V8_SWEEP_MODE"] = "1"
    env["V8_BACKTEST_OVERRIDE_PRECEDENCE"] = "1"
    return {
        "kind": "EXPLICIT_STOCK_SWEEP_OVERRIDE",
        "override_file": str(path),
        "override_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "explicit_keys": len(payload),
        "dual_guard": True,
    }

