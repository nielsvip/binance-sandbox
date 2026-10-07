"""STDEV_BB_RZ_EXIT — failed-breakout %B-rejection exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6603-6614.
  Gated by STDEV_BB_RZ_EXIT_ENABLED (default False), tf = STDEV_BB_RZ_EXIT_TF (default 'D').
  pctb_now  = bb_pct_b_<tf>          (default 0.5)
  pctb_prev = bb_pct_b_<tf>_prev     (default pctb_now)
  vel       = wt_velocity_1h          (default 0)
  LONG  fires when pctb_prev >= 1.0 AND pctb_now < 1.0 AND vel < 0  (rejected at upper band)
  SHORT fires when pctb_prev <= 0.0 AND pctb_now > 0.0 AND vel > 0  (rejected at lower band)

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_stdev_bb_rz_exit) AND vec (check_stdev_bb_rz_exit_vec) share the SAME pure per-bar
predicate _stdev_bb_rz_exit_fires(), so the two paths CANNOT drift. Pure core: no config.

This is an indicator-only EXIT — no per-position state at all (no gain/age gate; it runs
inside the scorer-hold block). Fully vectorizable.

NPZ / indicator fields read by the core:
  bb_pct_b_<tf>, bb_pct_b_<tf>_prev, wt_velocity_1h.
"""
from typing import Tuple


def _stdev_bb_rz_exit_fires(pctb_now: float, pctb_prev: float, vel: float, is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6609 / 6612."""
    if is_long:
        return pctb_prev >= 1.0 and pctb_now < 1.0 and vel < 0
    return pctb_prev <= 0.0 and pctb_now > 0.0 and vel > 0


def check_stdev_bb_rz_exit(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Fire from the shared predicate.
    Live .get defaults mirror tradier_manage.py lines 6606-6608."""
    if not bool(getattr(config, "STDEV_BB_RZ_EXIT_ENABLED", False)):
        return False, ""
    tf = str(getattr(config, "STDEV_BB_RZ_EXIT_TF", "D"))
    pctb_now = float(indicators.get(f"bb_pct_b_{tf}", 0.5) or 0.5)
    pctb_prev = float(indicators.get(f"bb_pct_b_{tf}_prev", pctb_now) or pctb_now)
    vel = float(indicators.get("wt_velocity_1h", 0) or 0)
    if not _stdev_bb_rz_exit_fires(pctb_now, pctb_prev, vel, is_long):
        return False, ""
    return True, f"STDEV_BB_RZ_EXIT_{tf}_pctb={pctb_now:.3f}_g={gain_pct:.2f}%"


def check_stdev_bb_rz_exit_vec(config, pctb_now_arr, pctb_prev_arr, wt_velocity_1h_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar.
    Returns a bool ndarray. Arrays per-bar (NPZ): bb_pct_b_<tf>, bb_pct_b_<tf>_prev, wt_velocity_1h."""
    import numpy as np
    now = np.asarray(pctb_now_arr, dtype=float)
    if not bool(getattr(config, "STDEV_BB_RZ_EXIT_ENABLED", False)):
        return np.zeros(len(now), dtype=bool)
    prev = np.asarray(pctb_prev_arr, dtype=float)
    vel = np.asarray(wt_velocity_1h_arr, dtype=float)
    if is_long:
        return (prev >= 1.0) & (now < 1.0) & (vel < 0)
    return (prev <= 0.0) & (now > 0.0) & (vel > 0)
