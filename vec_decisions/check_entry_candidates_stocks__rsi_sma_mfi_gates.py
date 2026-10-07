"""RSI / SMA / SMA200_DIST / MFI entry GATES (stocks) — pure per-bar block predicates.

LIVE SOURCE: tradier_manage.py should_enter_long/short (fallback section).
  RSI_ENTRY (~11962/12243): LONG block when rsi_val > RSI_ENTRY_LONG_TRADIER (42);
                            SHORT block when rsi_val < RSI_ENTRY_SHORT_TRADIER (58).
                            (Only applies when rsi data exists — caller responsibility.)
  SMA_FILTER (~11972/12253): LONG block when cur>0 AND sma>0 AND cur < sma;
                             SHORT block when cur>0 AND sma>0 AND cur > sma.
  SMA200_DIST (~12005, LONG only): block when sma200_4h>0 AND cur>0 AND
                             ((cur-sma200_4h)/sma200_4h*100) < SMA200_DIST_LONG_THRESHOLD_4H (-10).
  MFI_ENTRY (~12052, LONG only): block when mfi_D > MFI_LONG_THRESHOLD_D (80, overbought).

Each predicate returns True == BLOCKED. SHORT has no MFI/SMA200_DIST gate in live code.
2026-05-30 PARITY: shared pure predicates drive scalar+vec.
NPZ fields: rsi_<p>_D / rsi_D, sma_200_D, sma_200_4h, mfi_D, close(=current_price) — all present.
"""
from typing import Tuple


def _rsi_gate_blocks(rsi_val: float, is_long: bool, long_thr: float, short_thr: float) -> bool:
    """PURE: True when RSI gate blocks (LONG: rsi>long_thr; SHORT: rsi<short_thr)."""
    if is_long:
        return rsi_val > long_thr
    return rsi_val < short_thr


def _sma_gate_blocks(current_price: float, sma_val: float, is_long: bool) -> bool:
    """PURE: True when SMA filter blocks. Mirrors live: requires sma>0 and cur>0."""
    if not (sma_val > 0 and current_price > 0):
        return False
    if is_long:
        return current_price < sma_val
    return current_price > sma_val


def _sma200_dist_blocks_long(current_price: float, sma200_4h: float, threshold: float) -> bool:
    """PURE (LONG only): True when (cur-sma)/sma*100 < threshold. Requires sma>0 and cur>0."""
    if not (sma200_4h > 0 and current_price > 0):
        return False
    dist_pct = ((current_price - sma200_4h) / sma200_4h) * 100.0
    return dist_pct < threshold


def _mfi_gate_blocks_long(mfi_D: float, threshold: float) -> bool:
    """PURE (LONG only): True when mfi_D > threshold (overbought)."""
    return mfi_D > threshold


# ── live param helpers ──
def _rsi_params(config):
    return (float(getattr(config, "RSI_ENTRY_LONG_TRADIER", 42.0)),
            float(getattr(config, "RSI_ENTRY_SHORT_TRADIER", 58.0)))


def _sma200_dist_thr(config):
    return float(getattr(config, "SMA200_DIST_LONG_THRESHOLD_4H", -10.0))


def _mfi_thr(config):
    return float(getattr(config, "MFI_LONG_THRESHOLD_D", 80.0))


# ── scalar wrappers ──
def check_rsi_gate(config, rsi_val: float, is_long: bool) -> Tuple[bool, str]:
    long_thr, short_thr = _rsi_params(config)
    if _rsi_gate_blocks(rsi_val, is_long, long_thr, short_thr):
        return True, f"RSI_GATE_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_sma_gate(config, current_price: float, sma_val: float, is_long: bool) -> Tuple[bool, str]:
    if _sma_gate_blocks(current_price, sma_val, is_long):
        return True, f"SMA_GATE_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_sma200_dist_long(config, indicators: dict) -> Tuple[bool, str]:
    cur = float(indicators.get("current_price", 0) or 0)
    sma = float(indicators.get("sma_200_4h", 0) or 0)
    if _sma200_dist_blocks_long(cur, sma, _sma200_dist_thr(config)):
        return True, "SMA200_DIST_BLOCK_LONG"
    return False, ""


def check_mfi_gate_long(config, indicators: dict) -> Tuple[bool, str]:
    mfi = float(indicators.get("mfi_D", 50) or 50)
    if _mfi_gate_blocks_long(mfi, _mfi_thr(config)):
        return True, "MFI_GATE_BLOCK_LONG"
    return False, ""


# ── vec masks (True == blocked) ──
def check_rsi_gate_vec(config, rsi_arr, is_long):
    import numpy as np
    r = np.asarray(rsi_arr, dtype=float)
    long_thr, short_thr = _rsi_params(config)
    if is_long:
        return r > long_thr
    return r < short_thr


def check_sma_gate_vec(config, current_price_arr, sma_arr, is_long):
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    s = np.asarray(sma_arr, dtype=float)
    valid = (s > 0) & (p > 0)
    if is_long:
        return valid & (p < s)
    return valid & (p > s)


def check_sma200_dist_long_vec(config, current_price_arr, sma200_4h_arr):
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    s = np.asarray(sma200_4h_arr, dtype=float)
    thr = _sma200_dist_thr(config)
    valid = (s > 0) & (p > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.where(s > 0, (p - s) / s * 100.0, 0.0)
    return valid & (dist < thr)


def check_mfi_gate_long_vec(config, mfi_D_arr):
    import numpy as np
    m = np.asarray(mfi_D_arr, dtype=float)
    return m > _mfi_thr(config)
