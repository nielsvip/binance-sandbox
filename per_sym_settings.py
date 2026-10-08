"""per_sym_settings — every switch/filter has FOUR defaults, one per CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT
(USER 2026-09-30; renamed from cat_side_defaults 2026-10-08 — this is where the settings are parked).
Source: data/per_sym_settings.json (JSON backup; SQL kv_json row "per_sym_settings" is PRIMARY — both always identical),
built from the TEMPLATE_{cat_side}.xlsx bold / is_default rows by tools/build_cat_side_defaults_4.py.

Precedence everywhere (live ez_manage/_psym_get + tradier_manage/_cfg, v12 sweep engine, v15_pilot):
    per-sym override  >  cat_side default (this module)  >  single global value in config.py / config_tradier.py / QuickConfig
Per-sym overrides are unchanged; this module only replaces the DEFAULT a lookup falls back to.
"""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("PER_SYM_SETTINGS_PATH") or os.environ.get("CAT_SIDE_DEFAULTS_PATH") or (Path(__file__).resolve().parent / "data" / "per_sym_settings.json"))  # sweep-only override (autopilot); unset in live = unchanged
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
    # SQL-primary (kv_json) → JSON fallback keeps herds alive if file vanishes.
    # Never fall back to {} silently when SQL has 172 promotions — that would sweep on globals.
    if os.environ.get("CAT_SIDE_DEFAULTS_SQLITE_DISABLED") != "1":
        try:
            import per_sym_store as _pss
            raw_sql = _pss.kv_get(_pss.KV_CAT_SIDE_DEFAULTS_4)
            if isinstance(raw_sql, dict) and any(raw_sql.get(cs) for cs in CAT_SIDES):
                # cache SQL result by epoch so we don't hit DB every call
                try:
                    # use kv epoch as mtime-equivalent
                    import sqlite3
                    con = _pss._connect()
                    try:
                        row = con.execute("SELECT epoch FROM kv_json WHERE key=?", (_pss.KV_CAT_SIDE_DEFAULTS_4,)).fetchone()
                        m_sql = float(row["epoch"]) if row and row["epoch"] is not None else -1
                    finally:
                        con.close()
                except Exception:
                    m_sql = -1
                if m_sql != _cache.get("mtime_sql"):
                    _cache["data"] = {cs: dict(raw_sql.get(cs) or {}) for cs in CAT_SIDES}
                    _cache["mtime_sql"] = m_sql
                    _cache["mtime"] = _cache.get("mtime")  # keep file mtime untouched
                return _cache["data"]
        except Exception:
            pass
    try:
        m = PATH.stat().st_mtime
    except FileNotFoundError:
        # file missing but SQL disabled or empty → fail-soft {} is intentional caller fallback to global config
        return _cache["data"] if _cache["data"] else {}
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
