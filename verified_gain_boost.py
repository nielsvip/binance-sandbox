import json
import os
import time

BOOST_PER_GAIN = 1.0
BOOST_CAP = 25.0
BOOST_MIN_TRADES = 10
BOOST_TIERS = ("RANKED", "BOOK_POS_HOLD")
BOOST_CACHE_TTL_S = 300.0

_cache = {"mtime": 0.0, "checked": 0.0, "boosts": {}}


def _tiers_path(base_path):
    try:
        return os.path.join(str(base_path), "data", "persym_size_tiers.json")
    except Exception:
        return ""


def _build_boosts(path):
    out = {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception:
        return out
    try:
        tiers = data.get("tiers") or {}
    except Exception:
        return out
    if not isinstance(tiers, dict):
        return out
    for sym_side, row in tiers.items():
        try:
            if not isinstance(row, dict):
                continue
            if row.get("tier") not in BOOST_TIERS:
                continue
            g30 = float(row.get("g30") or 0.0)
            if g30 <= 0.0:
                continue
            trades = float(row.get("trades_30d") or 0.0)
            if trades < BOOST_MIN_TRADES:
                continue
            out[str(sym_side)] = min(BOOST_CAP, BOOST_PER_GAIN * g30)
        except Exception:
            continue
    return out


def refresh(base_path):
    global _cache
    path = _tiers_path(base_path)
    now = time.time()
    try:
        mtime = os.path.getmtime(path) if path else 0.0
    except Exception:
        mtime = 0.0
    if mtime and mtime == _cache["mtime"] and (now - _cache["checked"]) < BOOST_CACHE_TTL_S:
        return _cache["boosts"]
    boosts = _build_boosts(path) if mtime else {}
    _cache = {"mtime": mtime, "checked": now, "boosts": boosts}
    return boosts


def boost_for(symbol, side, base_path):
    try:
        key = "%s_%s" % (str(symbol).strip().upper(), str(side).strip().upper())
    except Exception:
        return 0.0
    try:
        return float(refresh(base_path).get(key, 0.0))
    except Exception:
        return 0.0


def _fresh_tiers():
    global _cache
    _cache = {"mtime": 0.0, "checked": 0.0, "boosts": {}}
