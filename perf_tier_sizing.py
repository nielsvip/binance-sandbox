"""perf_tier_sizing — PERF_TIER_SIZING (USER 2026-10-06): risk is carried by SIZE, not by refusal.

data/persym_size_tiers.json (written daily by tools/v15_persym_size_tiers.py from the fresh 30D + 365D results) maps
SYM_SIDE -> {"mult": m, ...}. Negative on 30D and 365D -> minimal multiplier; ranked 30D gainers -> larger multiplier.
Live callers (behind config PERF_TIER_SIZING_ENABLED):
  * ez_manage._psym_sps (crypto START_POSITION_SIZE): apply_usd(base_usd, ...) — floor = exchange minimum, cap = MAX_ORDER_VALUE
  * tradier_manage queue sizing (stocks quantity): apply_qty(qty, ...) — floor = 1 share; MAX_ORDER_VALUE cap stays downstream
  * negbook_blocks(entry, tier_sizing_on): with tier sizing ON only an explicit _NEG_BLOCK tag blocks; acc_gain_pct <= 0 -> minimal size
Missing/unreadable file or sym_side -> multiplier 1.0 (fail-open = unchanged sizing).
"""
import json
import os
import time
from pathlib import Path

PATH = Path(os.environ.get("PERSYM_SIZE_TIERS_PATH") or (Path(__file__).resolve().parent / "data" / "persym_size_tiers.json"))
_CACHE = {"mtime": None, "tiers": {}, "checked": 0.0}


def _tiers():
    now = time.time()
    if now - _CACHE["checked"] < 30.0:
        return _CACHE["tiers"]
    _CACHE["checked"] = now
    try:
        m = PATH.stat().st_mtime
        if m != _CACHE["mtime"]:
            d = json.loads(PATH.read_text())
            _CACHE["tiers"] = d.get("tiers", {}) if isinstance(d, dict) else {}
            _CACHE["mtime"] = m
    except Exception:
        pass
    return _CACHE["tiers"]


def get_mult(symbol: str, side: str) -> float:
    try:
        e = _tiers().get(f"{str(symbol).upper()}_{str(side).upper()}")
        m = float((e or {}).get("mult", 1.0))
        return m if m > 0 else 1.0
    except Exception:
        return 1.0


def apply_usd(base_usd: float, symbol: str, side: str, min_usd: float, max_usd: float) -> float:
    """Crypto START_POSITION_SIZE in USD x tier multiplier; never below the exchange minimum, never above MAX_ORDER_VALUE."""
    m = get_mult(symbol, side)
    if m == 1.0:
        return base_usd
    v = float(base_usd) * m
    if min_usd and v < min_usd:
        v = float(min_usd)
    if max_usd and v > max_usd:
        v = max(float(max_usd), float(min_usd or 0.0))
    return v


def apply_qty(qty: float, symbol: str, side: str) -> float:
    """Stocks quantity x tier multiplier, rounded DOWN to whole shares then floored at 1 (USER 2026-10-11:
    neg/365D-unrescued trade live tiny — supersedes the 2026-10-06 below-one-share = skip rule).
    Zero/negative input stays 0 (never invent an order). The MAX_ORDER_VALUE cap is applied right after by the caller."""
    if qty is None:
        return qty
    try:
        q = float(qty)
    except Exception:
        return qty
    if q <= 0:
        return 0
    m = get_mult(symbol, side)
    if m == 1.0:
        return qty
    return max(1, int(q * m))


def negbook_blocks(entry: dict, tier_sizing_on: bool) -> bool:
    """True = refuse trading. Tier sizing ON: only an explicit _NEG_BLOCK tag; OFF: legacy (acc_gain_pct <= 0 or _NEG_BLOCK)."""
    if not isinstance(entry, dict):
        return False
    if "_NEG_BLOCK" in str(entry.get("winning_tag", "")):
        return True
    if tier_sizing_on:
        return False
    g = entry.get("acc_gain_pct")
    try:
        return g is not None and float(g) <= 0
    except Exception:
        return False
