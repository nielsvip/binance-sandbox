"""twin_vec_special.py — numpy twins for 7 vec-special switches (2026-10-04).

Each twin mirrors its live implementation EXACTLY: same predicate, same
defaults, same order of checks. Live citations (base af74536):
  DC_BREAKOUT_TF_EXPANDED ....... ez_positions_quick.py:4555-4566 (AdvancedSignalRater.rate bc133)
  DELTA_PYRAMID_PRICE_TOL ........ wt_dc_delta.py:924-937 (pyramid price_ok; cfg via ez_positions_quick.py:8179 / tradier_manage.py:22636)
  LIVE_VEC_EMERGENCY_BRAKE_ENABLED  ez_manage.py:32115-32243 / tradier_manage.py:26360-26425 (execute_now brake)
  R3_HTF_FLIP_EXIT_ENABLED ....... ez_manage.py:47962-48000 (crypto) / tradier_manage.py:11162-11217 (stocks)
  HTF_WT_CHURN_REENTRY_ENABLED ... ez_manage.py:42701-42725 (process_single_reentry_evaluation)
  RSI_ENTRY_LONG/SHORT_TRADIER ... tradier_manage.py:17394-17395 (MFI score) + 27835-27860/28185-28205 (T55 gate)
BIBLE 19: real masks only, no getattr scaffolding. BIBLE 39: None when inert.
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "DC_BREAKOUT_TF_EXPANDED",
    "DELTA_PYRAMID_PRICE_TOL",
    "LIVE_VEC_EMERGENCY_BRAKE_ENABLED",
    "R3_HTF_FLIP_EXIT_ENABLED",
    "HTF_WT_CHURN_REENTRY_ENABLED",
    "RSI_ENTRY_LONG_TRADIER",
    "RSI_ENTRY_SHORT_TRADIER",
)


def _safe(npz: Mapping[str, Any], key: str, n: int, default: float = 0.0) -> np.ndarray:
    if key in npz:
        try:
            a = np.asarray(npz[key], dtype=float).reshape(-1)
        except Exception:
            return np.full(n, default, dtype=float)
        if a.size < n:
            tmp = np.full(n, default, dtype=float)
            tmp[: a.size] = a
            a = tmp
        else:
            a = a[:n]
        return np.where(np.isfinite(a), a, default)
    return np.full(n, default, dtype=float)


def _f(cfg: Any, name: str, default: float) -> float:
    try:
        v = float(getattr(cfg, name, default))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _b(cfg: Any, name: str, default: bool) -> bool:
    try:
        return bool(getattr(cfg, name, default))
    except Exception:
        return default


def _s(cfg: Any, name: str, default: str) -> str:
    try:
        v = getattr(cfg, name, default)
        return str(v) if v is not None else default
    except Exception:
        return default


# === 1. DC_BREAKOUT_TF_EXPANDED (live ez_positions_quick.py:4555-4566) ===
def dc_breakout_fires(px: float, hi: float, lo: float, adx: float, is_long: bool) -> bool:
    if not (hi > 0 and lo > 0 and adx > 25):
        return False
    return (px > hi) if is_long else (px < lo)


def dc_breakout_tf(cfg: Any) -> str:
    return str(getattr(cfg, "DC_BREAKOUT_TF_EXPANDED", getattr(cfg, "DC_BREAKOUT_TF", "1h")))


def dc_breakout_entry_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, close: Any = None) -> np.ndarray | None:
    if not _b(cfg, "DC_BREAKOUT_ENTRY_ENABLED", False):
        return None
    tf = dc_breakout_tf(cfg)
    hi = _safe(npz, f"dc_high_{tf}", n, 0.0)
    lo = _safe(npz, f"dc_low_{tf}", n, 0.0)
    adx = _safe(npz, f"adx_{tf}", n, 0.0)
    px = _safe(npz, "close", n, 0.0) if close is None else np.asarray(close, dtype=float).reshape(-1)[:n]
    if px.size < n:
        tmp = np.zeros(n)
        tmp[: px.size] = px
        px = tmp
    gate = (hi > 0) & (lo > 0) & (adx > 25)
    return gate & ((px > hi) if is_long else (px < lo))


# === 2. DELTA_PYRAMID_PRICE_TOL (live wt_dc_delta.py:924-937) ===
def pyramid_price_ok(cur_px: float, last_px: float, is_long: bool, tol: float) -> bool:
    if not last_px or not cur_px or cur_px <= 0:
        return False
    return (cur_px <= last_px * (1 + tol)) if is_long else (cur_px >= last_px * (1 - tol))


def delta_pyramid_price_mask(cur_px: Any, last_px: Any, is_long: bool, tol: float) -> np.ndarray:
    cur = np.asarray(cur_px, dtype=float)
    last = np.asarray(last_px, dtype=float)
    valid = (last != 0) & (cur != 0) & (cur > 0) & np.isfinite(cur) & np.isfinite(last)
    ok = (cur <= last * (1 + tol)) if is_long else (cur >= last * (1 - tol))
    return valid & ok


def delta_pyramid_mask_vec(n: int, is_long: bool, cfg: Any, cur_px: Any = None, last_px: Any = None) -> np.ndarray | None:
    if not _b(cfg, "DELTA_ENGINE_ENABLED", True):
        return None
    if cur_px is None or last_px is None:
        return None
    return delta_pyramid_price_mask(cur_px, last_px, is_long, _f(cfg, "DELTA_PYRAMID_PRICE_TOL", 0.02))


def delta_pyramid_adds_allowed(cfg: Any, adds_so_far: int) -> bool:
    """DELTA_PYRAMID_MAX cap (live: DeltaTracker pyramid_max, ez_positions_quick:8181 / tradier __init__). Default 8 = config/tradier/QuickConfig unanimous."""
    try:
        n = int(adds_so_far or 0)
    except (TypeError, ValueError):
        n = 0
    return n < int(_f(cfg, "DELTA_PYRAMID_MAX", 8))


# === 3. LIVE_VEC_EMERGENCY_BRAKE (live ez_manage.py:32199-32243 / tradier_manage.py:26387-26425) ===
EB_REASONS = ("EMERGENCY_BRAKE_10_PER_MIN_CHURN", "EMERGENCY_BRAKE_MAX_ENTRIES", "EMERGENCY_BRAKE_MAX_TRADES", "EMERGENCY_BRAKE_SYMBOL_CHURN")


def emergency_brake_blocked(min_total: int, entries: int, total: int, sym_count: int, is_profitable_close: bool, max_per_min: int = 10, include_min_churn: bool = True) -> tuple:
    if is_profitable_close:
        return False, "OK"
    if include_min_churn and max_per_min > 0 and min_total >= max_per_min:
        return True, "EMERGENCY_BRAKE_10_PER_MIN_CHURN"
    if entries > 500:
        return True, "EMERGENCY_BRAKE_MAX_ENTRIES"
    if total > 1000:
        return True, "EMERGENCY_BRAKE_MAX_TRADES"
    if sym_count > 50:
        return True, "EMERGENCY_BRAKE_SYMBOL_CHURN"
    return False, "OK"


def emergency_brake_mask(min_total: Any, entries: Any, total: Any, sym_count: Any, is_profitable_close: Any = False, max_per_min: int = 10, include_min_churn: bool = True) -> np.ndarray:
    mt = np.asarray(min_total, dtype=float)
    en = np.asarray(entries, dtype=float)
    to = np.asarray(total, dtype=float)
    sy = np.asarray(sym_count, dtype=float)
    pc = np.asarray(is_profitable_close, dtype=bool)
    blocked = np.zeros(mt.shape, dtype=bool)
    if include_min_churn and max_per_min > 0:
        blocked |= mt >= max_per_min
    blocked |= en > 500
    blocked |= to > 1000
    blocked |= sy > 50
    return blocked & (~pc)


def evaluate_emergency_brake_core(acct: str, dt_now: Any, position_key: str = "", action: str = "", is_profitable_close: bool = False, config: Any = None, decisions_dir_path: Any = None) -> tuple:
    """Adapter matching live's missing vec_paths.emergency_brake.evaluate_emergency_brake_core call
    (ez_manage.py:32224 / tradier_manage.py:26406). Re-reads the same decisions JSONL with the same
    parse + cutoffs + thresholds as live (ez_manage.py:32115-32210). Crypto includes the 10/min
    churn tier; stocks does not (tradier_manage.py:26387-26400 has no min tier)."""
    import json as _j
    from datetime import timedelta as _td
    from pathlib import Path as _P
    cname = type(config).__name__ if config is not None else ""
    stocks = cname == "TradierConfig" or str(getattr(config, "MODE", "") or "") == "tradier"
    try:
        max_per_min = int(getattr(config, "EMERGENCY_BRAKE_MAX_TRADES_PER_MIN", 10) or 10)
    except Exception:
        max_per_min = 10
    base = str(decisions_dir_path or "")
    if not base and config is not None:
        try:
            base = str(_P(str(getattr(config, "BASE_PATH", "."))) / "data" / "decisions")
        except Exception:
            base = ""
    hour_entries = hour_total = min_total = 0
    sym_counts: dict = {}
    try:
        cutoff = dt_now - _td(hours=1)
        cutoff_min = dt_now - _td(seconds=60)
        dfile = str(_P(base) / f"decisions_{acct}_{dt_now.strftime('%Y%m%d')}.jsonl")
        with open(dfile, errors="replace") as fh:
            fh.seek(0, 2)
            fsize = fh.tell()
            fh.seek(max(0, fsize - 500000))
            if fsize > 500000:
                fh.readline()
            for line in fh:
                try:
                    dd = _j.loads(line)
                    dt = dd.get("timestamp", "")
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(str(dt).replace("Z", "+00:00"))
                    if dt < cutoff:
                        continue
                    hour_total += 1
                    if dt >= cutoff_min:
                        min_total += 1
                    if dd.get("action", "") in ("OPEN", "AUGMENT", "REENTRY", "QUICK_OPEN", "QUICK_AUGMENT"):
                        hour_entries += 1
                    pk = dd.get("position_key", "")
                    sym_counts[pk] = sym_counts.get(pk, 0) + 1
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    sym_key = position_key or "unknown"
    return emergency_brake_blocked(min_total, hour_entries, hour_total, sym_counts.get(sym_key, 0), bool(is_profitable_close), max_per_min, include_min_churn=not stocks)


# === 4. R3_HTF_FLIP (live ez_manage.py:47962-48000 crypto / tradier_manage.py:11162-11217 stocks) ===
def r3_htf_flip_fires(px: float, dc_basis_D: float, w1D: float, w2D: float, w1W: float, w2W: float, ema_4h: float, atr_4h: float, w1_4h: float, w2_4h: float, is_long: bool, tier4h_enabled: bool = False, require_wt: bool = False, age_min: Any = None, newborn_min: float = 15.0, r1_stop: Any = None) -> bool:
    fire = False
    if dc_basis_D > 0:
        dc_break = (is_long and px < dc_basis_D) or ((not is_long) and px > dc_basis_D)
        wt_flip = (is_long and w1D < w2D and w1W < w2W) or ((not is_long) and w1D > w2D and w1W > w2W)
        if ((dc_break and wt_flip) if require_wt else (dc_break or wt_flip)):
            fire = True
    if (not fire) and tier4h_enabled:
        if ema_4h > 0 and atr_4h > 0:
            b4l = is_long and px < (ema_4h - atr_4h) and w1_4h < w2_4h
            b4s = (not is_long) and px > (ema_4h + atr_4h) and w1_4h > w2_4h
            if b4l or b4s:
                fire = True
    if fire and age_min is not None and newborn_min > 0:
        try:
            if float(age_min) < newborn_min:
                fire = False
        except (TypeError, ValueError):
            pass
    if fire and r1_stop is not None:
        try:
            rs = float(r1_stop)
            if rs > 0 and ((is_long and px > rs) or ((not is_long) and px < rs)):
                fire = False
        except (TypeError, ValueError):
            pass
    return fire


def r3_htf_flip_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, close: Any = None, stocks: Any = None, age_min_arr: Any = None, r1_stop_arr: Any = None) -> np.ndarray | None:
    if not _b(cfg, "R3_HTF_FLIP_EXIT_ENABLED", False):
        return None
    if stocks is None:
        stocks = str(getattr(cfg, "MODE", "crypto") or "crypto") == "tradier"
    require_wt = bool(getattr(cfg, "R3_HTF_FLIP_REQUIRE_WT", True)) if stocks else False
    tier4h = _b(cfg, "R3_HTF_FLIP_4H_TIER_ENABLED", False)
    px = _safe(npz, "close", n, 0.0) if close is None else np.asarray(close, dtype=float).reshape(-1)[:n]
    if px.size < n:
        tmp = np.zeros(n)
        tmp[: px.size] = px
        px = tmp
    dcB = _safe(npz, "dc_basis_D", n, 0.0)
    w1D = _safe(npz, "wt1_D", n, 0.0)
    w2D = _safe(npz, "wt2_D", n, 0.0)
    w1W = _safe(npz, "wt1_W", n, 0.0)
    w2W = _safe(npz, "wt2_W", n, 0.0)
    ema = _safe(npz, "ema_20_4h", n, 0.0)
    atr = _safe(npz, "atr_4h", n, 0.0)
    w14 = _safe(npz, "wt1_4h", n, 0.0)
    w24 = _safe(npz, "wt2_4h", n, 0.0)
    if is_long:
        dc_break = (dcB > 0) & (px < dcB)
        wt_flip = (w1D < w2D) & (w1W < w2W)
        b4 = (ema > 0) & (atr > 0) & (px < (ema - atr)) & (w14 < w24)
    else:
        dc_break = (dcB > 0) & (px > dcB)
        wt_flip = (w1D > w2D) & (w1W > w2W)
        b4 = (ema > 0) & (atr > 0) & (px > (ema + atr)) & (w14 > w24)
    fire = (dc_break & wt_flip) if require_wt else (dc_break | wt_flip)
    if tier4h:
        fire = fire | ((~fire) & b4)
    if stocks:
        nb = _f(cfg, "R3_HTF_FLIP_NEWBORN_WINDOW_MIN", 15.0)
        if age_min_arr is not None and nb > 0:
            age = np.asarray(age_min_arr, dtype=float).reshape(-1)[:n]
            fire = fire & ~(age < nb)
        if r1_stop_arr is not None:
            rs = np.asarray(r1_stop_arr, dtype=float).reshape(-1)[:n]
            guard = (rs > 0) & (((px > rs) if is_long else (px < rs)))
            fire = fire & (~guard)
    return fire


# === 5. HTF_WT_CHURN_REENTRY (live ez_manage.py:42701-42725) ===
def htf_wt_churn_fires(w1_1h: float, w2_1h: float, w1_15m: float, w2_15m: float, w1_4h: float, w2_4h: float, is_long: bool, age_min: float, max_age: float, notional: float, sps: float) -> bool:
    if age_min > max_age:
        return False
    with_ = (w1_1h > w2_1h or w1_15m > w2_15m or w1_4h > w2_4h) if is_long else (w1_1h < w2_1h or w1_15m < w2_15m or w1_4h < w2_4h)
    if not with_:
        return False
    return notional < sps * 3


def htf_wt_churn_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, age_min_arr: Any = None, notional_arr: Any = None) -> np.ndarray | None:
    if not _b(cfg, "HTF_WT_CHURN_REENTRY_ENABLED", True):
        return None
    max_age = _f(cfg, "HTF_WT_CHURN_REENTRY_MAX_AGE_MIN", 120.0)
    sps = _f(cfg, "START_POSITION_SIZE", 28.0)
    w1h = _safe(npz, "wt1_1h", n, 0.0)
    w2h = _safe(npz, "wt2_1h", n, 0.0)
    w115 = _safe(npz, "wt1_15m", n, 0.0)
    w215 = _safe(npz, "wt2_15m", n, 0.0)
    w14 = _safe(npz, "wt1_4h", n, 0.0)
    w24 = _safe(npz, "wt2_4h", n, 0.0)
    with_ = ((w1h > w2h) | (w115 > w215) | (w14 > w24)) if is_long else ((w1h < w2h) | (w115 < w215) | (w14 < w24))
    fire = with_
    if age_min_arr is not None:
        fire = fire & (np.asarray(age_min_arr, dtype=float).reshape(-1)[:n] <= max_age)
    if notional_arr is not None:
        fire = fire & (np.asarray(notional_arr, dtype=float).reshape(-1)[:n] < sps * 3)
    return fire


# === 6/7. RSI_ENTRY_LONG/SHORT_TRADIER (live tradier_manage.py:17394-17395 + 27835-27860/28185-28205) ===
def rsi_t55_key(cfg: Any) -> str:
    try:
        period = int(float(getattr(cfg, "RSI_ENTRY_PERIOD_TRADIER", 10)))
    except (TypeError, ValueError):
        period = 10
    return f"rsi_{period}_D"


def rsi_t55_series(npz: Mapping[str, Any], n: int, cfg: Any) -> tuple:
    for key in (rsi_t55_key(cfg), "rsi_D", "rsi_1h"):
        if key in npz:
            try:
                a = np.asarray(npz[key], dtype=float).reshape(-1)
            except Exception:
                continue
            if a.size < n:
                tmp = np.full(n, 50.0)
                tmp[: a.size] = a
                a = tmp
            else:
                a = a[:n]
            a = np.where(np.isfinite(a), a, 50.0)
            return np.where(a == 0, 50.0, a), True
    return np.full(n, 50.0), False


def rsi_entry_gate_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    vals, present = rsi_t55_series(npz, n, cfg)
    if not present:
        return np.ones(n, dtype=bool)
    if is_long:
        return vals <= _f(cfg, "RSI_ENTRY_LONG_TRADIER", 42.0)
    return vals >= _f(cfg, "RSI_ENTRY_SHORT_TRADIER", 58.0)


def rsi_mfi_score_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    tf = _s(cfg, "ENTRY_PRIMARY_TF", "4h")
    flow = _safe(npz, f"mfi_{tf}", n, 50.0)
    if is_long:
        return flow < _f(cfg, "RSI_ENTRY_LONG_TRADIER", 42.0)
    return flow > _f(cfg, "RSI_ENTRY_SHORT_TRADIER", 58.0)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "DC_BREAKOUT_TF_EXPANDED":
        return dc_breakout_entry_mask(npz, n, is_long, cfg, close=kw.get("close"))
    if switch == "DELTA_PYRAMID_PRICE_TOL":
        return delta_pyramid_mask_vec(n, is_long, cfg, cur_px=kw.get("cur_px"), last_px=kw.get("last_px"))
    if switch == "LIVE_VEC_EMERGENCY_BRAKE_ENABLED":
        if not _b(cfg, "LIVE_VEC_EMERGENCY_BRAKE_ENABLED", False):
            return None
        if kw.get("min_total") is None or kw.get("entries") is None or kw.get("total") is None or kw.get("sym_count") is None:
            return None
        cname = type(cfg).__name__
        stocks = cname == "TradierConfig" or str(getattr(cfg, "MODE", "") or "") == "tradier"
        try:
            mpm = int(getattr(cfg, "EMERGENCY_BRAKE_MAX_TRADES_PER_MIN", 10) or 10)
        except Exception:
            mpm = 10
        return emergency_brake_mask(kw.get("min_total"), kw.get("entries"), kw.get("total"), kw.get("sym_count"), kw.get("is_profitable_close", False), mpm, include_min_churn=not stocks)
    if switch == "R3_HTF_FLIP_EXIT_ENABLED":
        return r3_htf_flip_mask(npz, n, is_long, cfg, close=kw.get("close"), age_min_arr=kw.get("age_min_arr"), r1_stop_arr=kw.get("r1_stop_arr"))
    if switch == "HTF_WT_CHURN_REENTRY_ENABLED":
        return htf_wt_churn_mask(npz, n, is_long, cfg, age_min_arr=kw.get("age_min_arr"), notional_arr=kw.get("notional_arr"))
    if switch == "RSI_ENTRY_LONG_TRADIER":
        return rsi_entry_gate_mask(npz, n, True, cfg)
    if switch == "RSI_ENTRY_SHORT_TRADIER":
        return rsi_entry_gate_mask(npz, n, False, cfg)
    raise KeyError(f"unknown vec-special switch: {switch!r}")
