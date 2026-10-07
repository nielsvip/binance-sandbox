"""w2-exits staged twin: GAP_MOC INTRADAY sentinel + 30m emergency force + DC-breakdown top.

Live source: tradier_manage.py gap-aware loop.
  INTRADAY sentinel ... tradier_manage.py:10012-10027
      pct = (current - close_1h_prev|close_15m_prev)/prev*100
      LONG fires pct > GAP_MOC_INTRADAY_PCT (0.30); SHORT fires pct < -thr.
      Reason tag INTRADAY (tradier_manage.py:10103,10125).
  30m emergency force . tradier_manage.py:10109-10112
      mins_to_close <= 30 and any sentinel -> bypass small-top, force MOC.
  DC-breakdown top .... tradier_manage.py:9448-9453 (long) / 9459-9462 (short)
      long: cur < dc_low_4h ; short: cur > dc_high_4h. Any single top signal
      suffices (live:9453). Vec _is_top currently models wt1_15m-vs-wt2_15m
      only; the wt_5m/ha/px_5m signals need 5m fields (LG-13 USER ORDER:
      no 5m in vec, never approximated) and stay unmodeled.

NOT modeled (portfolio state, see IMPOSSIBILITY_HEDGE_RATIO.md):
  LS_GATE skip (10028-10062), LS_FORCE_CLOSE (10063-10090).

Every function returns a real NumPy bool mask over the engine bar grid.
Fails closed: missing arrays -> all-False, never a fabricated fire.
"""
from __future__ import annotations

import numpy as np


def _arr(npz, key, n):
    try:
        a = np.asarray(npz.get(key, None), dtype=float)
    except Exception:
        return None
    if a is None or a.size == 0:
        return None
    if a.size < n:
        out = np.zeros(n, dtype=float)
        out[:a.size] = a
        return out
    return a[:n]


def intraday_sentinel_mask(npz, n: int, is_long: bool, cfg) -> np.ndarray:
    """Per-bar INTRADAY same-direction sentinel (live tradier:10016-10025)."""
    out = np.zeros(n, dtype=bool)
    try:
        thr = float(getattr(cfg, "GAP_MOC_INTRADAY_PCT", 0.30) or 0.30)
    except Exception:
        thr = 0.30
    cp = _arr(npz, "close", n)
    prev = _arr(npz, "close_1h_prev", n)
    if prev is None or not np.any(prev > 0):
        prev = _arr(npz, "close_15m_prev", n)  # live fallback chain :10018
    if cp is None or prev is None:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(prev != 0, (cp - prev) / prev * 100.0, 0.0)
    valid = (cp > 0) & (prev != 0)
    if is_long:
        return valid & (pct > thr)
    return valid & (pct < -thr)


def dc_breakdown_top_mask(npz, n: int, is_long: bool) -> np.ndarray:
    """DC-breakdown small-top signal (live tradier:9448-9462, DC part only)."""
    out = np.zeros(n, dtype=bool)
    cp = _arr(npz, "close", n)
    if cp is None:
        return out
    if is_long:
        lvl = _arr(npz, "dc_low_4h", n)
        if lvl is None:
            return out
        return (cp > 0) & (lvl > 0) & (cp < lvl)
    lvl = _arr(npz, "dc_high_4h", n)
    if lvl is None:
        return out
    return (cp > 0) & (lvl > 0) & (cp > lvl)


def emergency_30m_mask(n: int, bars_per_day: int, bar_min: int) -> np.ndarray:
    """Last-30m-of-RTH-day bars (live tradier:10111 mins_to_close<=30 force)."""
    out = np.zeros(n, dtype=bool)
    try:
        bpd = int(bars_per_day)
        bm = max(int(bar_min), 1)
    except Exception:
        return out
    if bpd <= 0:
        return out
    bars_30m = max(1, int(round(30.0 / bm)))
    for i in range(n):
        if (i % bpd) >= bpd - bars_30m:
            out[i] = True
    return out


def gap_moc_intraday_fire(npz, n: int, is_long: bool, cfg, in_window,
                           is_top, gap_should, bars_per_day: int,
                           bar_min: int) -> np.ndarray:
    """Combined staged mask: OR into exit_sig at ONE call site.

    Mirrors live fire order (tradier:10103-10117): INTRADAY sentinel joins the
    sentinel set; small-top required unless 30m emergency force.
    Each sub-piece is independently knob-gated (all default False = inert):
      W2_GAP_INTRADAY_ENABLED / W2_GAP_EMERGENCY_30M / W2_GAP_DC_TOP.
    """
    out = np.zeros(n, dtype=bool)
    try:
        if not bool(getattr(cfg, "GAP_MOC_EXIT_ENABLED", True)):
            return out
        if str(getattr(cfg, "MODE", "crypto")) != "tradier":
            return out  # live loop is stocks-only
        intra_on = bool(getattr(cfg, "W2_GAP_INTRADAY_ENABLED", False))
        emerg_on = bool(getattr(cfg, "W2_GAP_EMERGENCY_30M", False))
        dctop_on = bool(getattr(cfg, "W2_GAP_DC_TOP", False))
        if not (intra_on or emerg_on or dctop_on):
            return out
        iw = np.asarray(in_window, dtype=bool)
        top = np.asarray(is_top, dtype=bool)
        if dctop_on:
            top = top | dc_breakdown_top_mask(npz, n, is_long)
        should = np.zeros(n, dtype=bool)
        if intra_on:
            should = should | intraday_sentinel_mask(npz, n, is_long, cfg)
        if emerg_on:
            e30 = emergency_30m_mask(n, bars_per_day, bar_min)
            any_should = np.asarray(gap_should, dtype=bool) | should
            out = out | (iw & e30 & any_should)  # top bypassed, live :10111
            out = out | (iw & should & top)
        else:
            out = out | (iw & should & top)
        return out
    except Exception:
        return np.zeros(n, dtype=bool)
