"""mandatory_reentry_wt_vec — STAGED vec twin of the live MANDATORY_REENTRY_WT_FILTER gate.

LIVE SOURCE: tradier_reentry_wt_contract.mandatory_reentry_wt_gate, called from
tradier_manage.process_position BRANCH B (~12163) on the WITHIN_BAND leg of the
mandatory PRICE_CROSS_BACK reentry. The CROSSED_BACK (favorable) leg bypasses
the gate live (GUARANTEED) and bypasses it here too.

Knobs (all six, TradierConfig-effective defaults):
  MANDATORY_REENTRY_WT_FILTER_ENABLED=True, _TF_MODE='15m_only', _MIN_TFS=1,
  _REQUIRE_FLIP=False, _VELOCITY_RATIO=0.90, _MIN_VELOCITY=0.0.

FIDELITY NOTES (honest limitations, see staged README):
  1. NPZ carries WT only for 15m/1h/4h/D(/W/M) — no 5m WT keys. The 15m_only
     mode (live effective default) is EXACT. For 5m_or_15m/5m_only the 5m leg
     is inert-by-data; `required` is computed over evaluable TFs and the
     detail string names the missing leg. A min_tfs=2 + 5m_or_15m combo can
     never pass in vec (only one TF evaluable) — documented, not fudged.
  2. Live `_prev` indicator keys are vectorized as causal shift-by-1 of the
     same NPZ array (rows ARE 15m bars, so previous-row == previous 15m bar).
     Live's explicit-flip flags (wt_cross_bull/bear_15m) ARE in the NPZ and
     are read directly, exactly like live.
  3. Live's missing-prev fallbacks (w1_prev default=w2, velocity_prev
     default=velocity) are mirrored for bar 0.
  4. NOT covered (separate ownership): the DC-break conjunction
     (REENTRY_LIVE_MONITOR_DC_BREAK_*, LIVE_ONLY, 5m-unwirable), the
     _reentry_opposition veto, and the BRANCH-A (evaluate_reentry) ungated
     bypass which governs reduce-states live. Scalar parity via
     backtest_v12_engine is REQUIRED before promotion (§39 step 6, §43).

Call site (ONE, staged patch patches/mandatory_reentry_wt_callsite.patch):
  simulate_one stocks_reentry_sources block — gate the PRICE_CROSS_BACK
  within-band leg after full CLOSE rows only (BRANCH-B states).
"""
from __future__ import annotations

import numpy as np


def _as_f64(a, n):
    a = np.asarray(a, dtype=np.float64)
    if a.shape[0] != n:
        z = np.zeros(n, dtype=np.float64)
        m = min(a.shape[0], n)
        z[:m] = a[:m]
        return z
    return a


def precompute(npz, n, safe):
    """Precompute the 15m WT arrays once. Returns dict or None when 15m WT absent."""
    if "wt1_15m" not in npz or "wt2_15m" not in npz:
        return None
    pre = {
        "w1": _as_f64(safe(npz, "wt1_15m", n, 0.0), n),
        "w2": _as_f64(safe(npz, "wt2_15m", n, 0.0), n),
        "vel": _as_f64(safe(npz, "wt_velocity_15m", n, 0.0), n),
    }
    if "wt_cross_bull_15m" in npz:
        pre["bull"] = _as_f64(safe(npz, "wt_cross_bull_15m", n, 0.0), n) > 0.5
    else:
        pre["bull"] = np.zeros(n, dtype=bool)
    if "wt_cross_bear_15m" in npz:
        pre["bear"] = _as_f64(safe(npz, "wt_cross_bear_15m", n, 0.0), n) > 0.5
    else:
        pre["bear"] = np.zeros(n, dtype=bool)
    return pre


def _tf_ok(is_long, w1, w2, w1p, w2p, explicit_flip, vel, velp, require_flip,
           ratio, min_v):
    favorable = (w1 > w2) if is_long else (w1 < w2)
    was_fav = (w1p > w2p) if is_long else (w1p < w2p)
    flipped = bool(explicit_flip) or (bool(favorable) and not bool(was_fav))
    fav_vel = (vel >= min_v) if is_long else (vel <= -min_v)
    not_slowing = abs(vel) >= max(min_v, abs(velp) * ratio)
    ok = bool(favorable) and (bool(flipped) if require_flip else True) \
        and bool(fav_vel) and bool(not_slowing)
    return ok


def wt_gate_ok(cfg, is_long, i, pre):
    """Scalar per-bar mirror of mandatory_reentry_wt_gate. -> bool."""
    if not bool(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_ENABLED", True)):
        return True
    mode = str(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_TF_MODE", "15m_only")
               or "15m_only").lower().replace(" ", "")
    if mode in ("5m", "5m_only"):
        tfs = ("5m",)
    elif mode in ("15m", "15m_only"):
        tfs = ("15m",)
    else:
        tfs = ("5m", "15m")
    try:
        min_tfs = int(float(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_TFS", 1)))
    except (TypeError, ValueError):
        min_tfs = 1
    require_flip = bool(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP", False))
    try:
        ratio = max(0.0, float(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO", 0.90)))
    except (TypeError, ValueError):
        ratio = 0.90
    try:
        min_v = max(0.0, float(getattr(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY", 0.0)))
    except (TypeError, ValueError):
        min_v = 0.0
    if pre is None:
        return False
    n = pre["w1"].shape[0]
    if i < 0 or i >= n:
        return False
    w1 = float(pre["w1"][i])
    w2 = float(pre["w2"][i])
    vel = float(pre["vel"][i])
    if i > 0:
        w1p = float(pre["w1"][i - 1])
        w2p = float(pre["w2"][i - 1])
        velp = float(pre["vel"][i - 1])
    else:  # live missing-prev fallbacks: w1_prev/w2_prev default=w2, vel_prev default=vel
        w1p, w2p, velp = w2, w2, vel
    qualifying = 0
    evaluable = 0
    for tf in tfs:
        if tf != "15m":
            continue  # no 5m WT in NPZ: inert-by-data (see module docstring)
        evaluable += 1
        explicit = bool(pre["bull"][i]) if is_long else bool(pre["bear"][i])
        if _tf_ok(is_long, w1, w2, w1p, w2p, explicit, vel, velp,
                  require_flip, ratio, min_v):
            qualifying += 1
    if evaluable == 0:
        return False
    required = max(1, min(min_tfs, evaluable))
    return qualifying >= required


def leg_ok(cfg, is_long, px, exit_px, i, pre):
    """WITHIN_BAND-leg decision: favorable (CROSSED_BACK) passes unconditionally
    (live GUARANTEED bypass); within-band legs must pass the WT gate."""
    if not (exit_px and exit_px > 0 and px and px > 0):
        return False
    fav = (px >= exit_px) if is_long else (px <= exit_px)
    if fav:
        return True
    return wt_gate_ok(cfg, is_long, i, pre)
