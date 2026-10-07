"""STDEV_REJECT_EXIT — pre-breakout %B-rejection exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6616-6628.
  Gated by STDEV_REJECT_EXIT_ENABLED (default False).
  tf   = STDEV_REJECT_EXIT_TF     (default 'D')
  zone = STDEV_REJECT_EXIT_ZONE   (default 0.80)
  ret  = STDEV_REJECT_EXIT_RETURN (default 0.65)
  pctb_now  = bb_pct_b_<tf>       (default 0.5)
  pctb_prev = bb_pct_b_<tf>_prev  (default pctb_now)
  vel       = wt_velocity_1h       (default 0)
  LONG  fires when pctb_prev >= zone AND pctb_now < ret AND vel < 0
  SHORT fires when pctb_prev <= (1.0-zone) AND pctb_now > (1.0-ret) AND vel > 0

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_stdev_reject_exit) AND vec (check_stdev_reject_exit_vec) share the SAME pure
per-bar predicate _stdev_reject_exit_fires(), so the two paths CANNOT drift. Pure core:
the thresholds (zone/ret) are passed in, so the core has no config and no state.

Indicator-only EXIT — no per-position state (runs inside the scorer-hold block).

NPZ / indicator fields read by the core:
  bb_pct_b_<tf>, bb_pct_b_<tf>_prev, wt_velocity_1h.
"""
from typing import Tuple


def _stdev_reject_exit_fires(pctb_now: float, pctb_prev: float, vel: float, zone: float,
                             ret: float, is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6623 / 6626."""
    if is_long:
        return pctb_prev >= zone and pctb_now < ret and vel < 0
    return pctb_prev <= (1.0 - zone) and pctb_now > (1.0 - ret) and vel > 0


def _stdev_reject_thresholds(config):
    return (str(getattr(config, "STDEV_REJECT_EXIT_TF", "D")),
            float(getattr(config, "STDEV_REJECT_EXIT_ZONE", 0.80)),
            float(getattr(config, "STDEV_REJECT_EXIT_RETURN", 0.65)))


def check_stdev_reject_exit(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Fire from the shared predicate.
    Live .get defaults mirror tradier_manage.py lines 6620-6622."""
    if not bool(getattr(config, "STDEV_REJECT_EXIT_ENABLED", False)):
        return False, ""
    tf, zone, ret = _stdev_reject_thresholds(config)
    pctb_now = float(indicators.get(f"bb_pct_b_{tf}", 0.5) or 0.5)
    pctb_prev = float(indicators.get(f"bb_pct_b_{tf}_prev", pctb_now) or pctb_now)
    vel = float(indicators.get("wt_velocity_1h", 0) or 0)
    if not _stdev_reject_exit_fires(pctb_now, pctb_prev, vel, zone, ret, is_long):
        return False, ""
    return True, f"STDEV_REJECT_EXIT_{tf}_pctb={pctb_now:.3f}_g={gain_pct:.2f}%"


def check_stdev_reject_exit_vec(config, pctb_now_arr, pctb_prev_arr, wt_velocity_1h_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate + SAME thresholds as the
    live scalar. Returns a bool ndarray. Arrays per-bar (NPZ): bb_pct_b_<tf>, bb_pct_b_<tf>_prev,
    wt_velocity_1h."""
    import numpy as np
    now = np.asarray(pctb_now_arr, dtype=float)
    if not bool(getattr(config, "STDEV_REJECT_EXIT_ENABLED", False)):
        return np.zeros(len(now), dtype=bool)
    _tf, zone, ret = _stdev_reject_thresholds(config)
    prev = np.asarray(pctb_prev_arr, dtype=float)
    vel = np.asarray(wt_velocity_1h_arr, dtype=float)
    if is_long:
        return (prev >= zone) & (now < ret) & (vel < 0)
    return (prev <= (1.0 - zone)) & (now > (1.0 - ret)) & (vel > 0)
