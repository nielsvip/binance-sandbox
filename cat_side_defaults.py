"""cat_side_defaults — every switch/filter has FOUR defaults, one per CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT
(USER 2026-09-30). Source: data/cat_side_defaults_4.json, built from the TEMPLATE_{cat_side}.xlsx bold / is_default rows by
tools/build_cat_side_defaults_4.py (re-run by tools/v15_avg_delta_apply.py after every promotion).

Precedence everywhere (live ez_manage/_psym_get + tradier_manage/_cfg, v12 sweep engine, v15_pilot):
    per-sym override  >  cat_side default (this module)  >  single global value in config.py / config_tradier.py / QuickConfig
Per-sym overrides are unchanged; this module only replaces the DEFAULT a lookup falls back to.
"""
import json
import os
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "cat_side_defaults_4.json"
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
_MISSING = object()
_cache = {"mtime": None, "data": {}}


def cat_side_of(symbol: str, side: str, venue: str = None) -> str:
    """venue 'crypto'/'tradier'/'stocks' wins when given; else crypto by quote suffix."""
    s = str(side or "").upper()
    s = "LONG" if s.startswith("L") else "SHORT"
    if venue:
        cat = "CRYPTO" if str(venue).lower() == "crypto" else "STOCKS"
    else:
        cat = "CRYPTO" if str(symbol or "").upper().endswith(CRYPTO_SUFFIXES) else "STOCKS"
    return f"{cat}_{s}"


def _load() -> dict:
    try:
        m = PATH.stat().st_mtime
    except FileNotFoundError:
        return {}
    if m != _cache["mtime"]:
        try:
            raw = json.loads(PATH.read_text())
            _cache["data"] = {cs: dict(raw.get(cs) or {}) for cs in CAT_SIDES}
            _cache["mtime"] = m
        except Exception:
            pass  # keep the last good copy on a partial write
    return _cache["data"]


def enabled() -> bool:
    return os.environ.get("CAT_SIDE_DEFAULTS_DISABLED", "0") != "1"


def defaults(cat_side: str) -> dict:
    return dict(_load().get(cat_side) or {}) if enabled() else {}


def get(key: str, cat_side: str, default=_MISSING):
    """the cat_side default of key; `default` (or KeyError) when this cat_side has none."""
    if enabled():
        d = _load().get(cat_side) or {}
        if key in d:
            return d[key]
    if default is _MISSING:
        raise KeyError(f"{key} has no {cat_side} default")
    return default


def get_for(key: str, symbol: str, side: str, default=_MISSING, venue: str = None):
    return get(key, cat_side_of(symbol, side, venue), default)
