"""perf_tier_sizing — PERF_TIER_SIZING (USER 2026-10-06): risk is carried by SIZE, not by refusal.

data/persym_size_tiers.json (written daily by tools/v15_persym_size_tiers.py from the fresh 30D + 365D results) maps
SYM_SIDE -> {"mult": m, ...}. Negative on 30D and 365D -> minimal multiplier; ranked 30D gainers -> larger multiplier.
Live callers (behind config PERF_TIER_SIZING_ENABLED):
  * ez_manage._psym_sps (crypto START_POSITION_SIZE): apply_usd(base_usd, ...) — floor = exchange minimum, cap = MAX_ORDER_VALUE
  * tradier_manage queue sizing (stocks quantity): apply_qty(qty, ...) — round DOWN, 0 = skip below one share; MAX_ORDER_VALUE cap stays downstream
  * negbook_blocks(entry, tier_sizing_on): with tier sizing ON nothing blocks (no _NEG_BLOCK, USER 2026-10-13); acc_gain_pct <= 0 -> minimal size
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


_TRIG = {"checked": 0.0, "sig": None, "set": frozenset()}
_ROOT = Path(os.environ.get("PERF_TIER_ROOT") or Path(__file__).resolve().parent)


def _trigger_files():
    d = _ROOT / "data"
    return [d / "stale_set_halt" / "halts_ez.json", d / "stale_set_halt" / "halts_tradier.json", d / "vigilance_blocks_ez.json", d / "vigilance_blocks_tradier.json", d / "v15_365_priority.json"]


def _triggered():
    """V17 CONVICTION (USER 2026-10-14): sym_sides under a live drawdown guard RIGHT NOW (stale-set 2-loss halt, vigilance block,
    2-loss 365D escalation). Re-read on mtime change, checked every 30 s. Any IO error -> previous set (never raises)."""
    now = time.time()
    if now - _TRIG["checked"] < 30.0:
        return _TRIG["set"]
    _TRIG["checked"] = now
    files = _trigger_files()
    sig = []
    for p in files:
        try:
            sig.append(p.stat().st_mtime)
        except Exception:
            sig.append(None)
    if sig == _TRIG["sig"]:
        return _TRIG["set"]
    out = set()
    for p, key in zip(files, ("halts", "halts", "blocks", "blocks", "sym_sides")):
        try:
            out.update(str(k).upper() for k in (json.loads(p.read_text()).get(key) or {}))
        except Exception:
            pass
    _TRIG["sig"], _TRIG["set"] = sig, frozenset(out)
    return _TRIG["set"]


def _conviction_disabled() -> bool:
    return os.environ.get("V17_CONVICTION_DISABLED") == "1" or (_ROOT / "data" / "conviction" / "DISABLED").exists()


def get_mult(symbol: str, side: str) -> float:
    """Tier multiplier (data/persym_size_tiers.json "mult" = tier x v17 conviction, tools/v17_conviction.combine_entry).
    Kill switch data/conviction/DISABLED -> the tier rule's own "tier_mult". Live drawdown guard active -> capped at 1.0 immediately."""
    try:
        ss = f"{str(symbol).upper()}_{str(side).upper()}"
        e = _tiers().get(ss) or {}
        m = float(e.get("mult", 1.0))
        if "tier_mult" in e and _conviction_disabled():
            m = float(e.get("tier_mult") or 1.0)
        if m <= 0:
            return 1.0
        if m > 1.0 and ss in _triggered():
            return 1.0
        return m
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
    """Stocks quantity x tier multiplier, rounded DOWN to whole shares (USER 2026-10-11: only trade when the
    sized amount buys at least one full share — below one share returns 0 = skip, never round up to 1).
    Zero/negative input stays 0 (never invent an order). The MAX_ORDER_VALUE cap is applied right after by the caller.
    """
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
    return int(q * m)


def negbook_blocks(entry: dict, tier_sizing_on: bool) -> bool:
    """True = refuse trading. Tier sizing ON: never (risk carried by size; USER 2026-10-13 "NEG_BLOCK does NOT exist" — the tag
    blocks nothing). OFF: legacy acc_gain_pct <= 0 kill switch."""
    if not isinstance(entry, dict):
        return False
    if tier_sizing_on:
        return False
    g = entry.get("acc_gain_pct")
    try:
        return g is not None and float(g) <= 0
    except Exception:
        return False
