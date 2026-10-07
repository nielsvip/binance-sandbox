"""Per-{CAT}_{SIDE} best-so-far default merge (Job 2, 2026-09-28).

Reads data/cat_side_best_defaults.json and merges the winners for a sym_side's
category+side into a defaults dict, so an untested sym_side's baseline (row 3)
starts from the best proven settings. Per-sym overrides always win over these
(callers apply sym overrides AFTER calling this). Empty file => no-op.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_PATH = ROOT / "data" / "cat_side_best_defaults.json"
_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_PATH.read_text())
        except Exception:
            _CACHE = {}
    return _CACHE


def map_key_for_symside(symside: str) -> str:
    s = symside.upper()
    base = s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s
    is_crypto = base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    is_long = s.endswith("_LONG")
    return f"{'CRYPTO' if is_crypto else 'STOCKS'}_{'LONG' if is_long else 'SHORT'}"


def cat_side_defaults(symside: str) -> dict:
    data = _load()
    key = map_key_for_symside(symside)
    d = data.get(key) or {}
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def apply_cat_side_defaults(defaults: dict, symside: str) -> dict:
    """Return defaults with per-cat_side winners merged in (winners override base defaults).
    Caller must still apply per-sym overrides afterwards so they win over these."""
    winners = cat_side_defaults(symside)
    if not winners:
        return defaults
    out = dict(defaults)
    for k, v in winners.items():
        out[k] = v
    return out
