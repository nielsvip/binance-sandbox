"""QUICK_REDUCE_STRONG (HLR_TOP_EXIT family) — shared scalar+vectorized predicate.

2026-05-30 PARITY EXTRACTION. Mirrors the proven pattern in strategy_enhancements.py
(_pyramid_fires / check_pyramid_signal / check_pyramid_signal_vec): ONE pure per-bar
core predicate, a scalar wrapper, and a numpy-vectorized version that share the core so
the two paths CANNOT drift.

LIVE SOURCE: ez_positions_quick.py:3451-3494 (the HLR_TOP_EXIT block inside the exit
evaluator). Live reason string family: "HLR_TOP_EXIT_<tfs>_g=<gain>%_REENTER_<mult>x",
action STRONG_REDUCE (-10). The orchestrator hint labels this decision
"QUICK_REDUCE_STRONG_REDUCE (1250)".

The live block builds a per-TF confirmation list across 1h / 4h / D / W and fires when
len(tfs) >= HLR_TOP_MIN_TFS AND at least one confirming TF is 4h-or-higher. Each TF's
confirmation is a boolean test on indicator fields + config velocity thresholds. The
registry write + logger.warning + reentry-multiplier are SIDE EFFECTS; the fire DECISION
is pure and is what this module extracts.

────────────────────────────────────────────────────────────────────────────
FAITHFULNESS CAVEATS (read before trusting vec results)
────────────────────────────────────────────────────────────────────────────
1. wt_momentum_state semantics DIFFER between live and NPZ. Live (ez_indicators.py:1638)
   encodes momentum_state from (velocity sign, acceleration sign):
       EXHAUST_UP   = velocity > 0 and acceleration <= 0
       EXHAUST_DOWN = velocity <= 0 and acceleration >= 0  (the else branch)
   The NPZ int8 wt_momentum_state_{tf} (backtest_v8_precompute.py:718-721) encodes a
   DIFFERENT thing (score-sign + velocity-sign: 2/1/-1/-2). It is NOT the live EXHAUST
   string. So to stay faithful to LIVE, this predicate RECONSTRUCTS the EXHAUST condition
   from wt_velocity_{tf} + wt_acceleration_{tf} (both present in NPZ), NOT from the NPZ
   momentum_state code. The scalar path, given the live string, compares to 'EXHAUST_*'.
2. Live divergence string 'BEAR'/'BULL' <-> NPZ wt_divergence_{tf} int8 -1 / +1.
3. Live peak-structure string 'LH'/'HH' <-> NPZ wt_peak_structure_{tf} int8 -1 / +1.
   The scalar wrapper accepts EITHER the live strings OR the int8 codes and normalizes.
4. _noloss_min in live is per-symbol/regime (config.get_symbol_setting / regime params).
   The vec path cannot reproduce per-symbol/regime lookups per-bar cheaply, so it uses the
   single config NOLOSS_MIN_PROFIT_PCT (and clamps to >= 0.3 exactly as the live FIX
   2026-04-07 does). Caller may pass an explicit noloss_min array if a richer per-bar floor
   is available.

NPZ / indicator fields read (per TF): wt_velocity_{1h,4h,D,W}, wt_acceleration_{1h,4h,D,W}
(for the live EXHAUST reconstruction), wt_divergence_{4h,D}, wt_peak_structure_{4h}.
All are emitted by backtest_v8_precompute.compute_tf_arrays for tfs in
["3m"/"5m","15m","1h","4h","D","W","M"], so all are present in the NPZ.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

# Tag constants — keep identical to live strings so scalar callers pass them straight through.
_EXHAUST_UP = "EXHAUST_UP"
_EXHAUST_DOWN = "EXHAUST_DOWN"
_DIV_BEAR = "BEAR"
_DIV_BULL = "BULL"
_PEAK_LH = "LH"
_PEAK_HH = "HH"


def _quick_reduce_strong_thresholds(config) -> Tuple[float, float, float, float, float, int]:
    """Pull every config threshold the live HLR_TOP_EXIT block reads. Defaults match the
    getattr defaults at ez_positions_quick.py:3454,3470-3472,3488."""
    min_gain = float(getattr(config, "HLR_TOP_MIN_GAIN_PCT", 1.5))
    v1h_thr = float(getattr(config, "HLR_TOP_VEL_1H_THRESH", -1.0))
    v4h_thr = float(getattr(config, "HLR_TOP_VEL_4H_THRESH", 0.0))
    vD_thr = float(getattr(config, "HLR_TOP_VEL_D_THRESH", 0.0))
    noloss_min = float(getattr(config, "NOLOSS_MIN_PROFIT_PCT", 0.30))
    min_tfs = int(getattr(config, "HLR_TOP_MIN_TFS", 2))
    return min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs


def _quick_reduce_strong_fires(
    gain_pct: float,
    is_long: bool,
    vel_1h: float,
    vel_4h: float,
    vel_D: float,
    vel_W: float,
    exhaust_4h: bool,
    exhaust_D: bool,
    exhaust_W: bool,
    div_against_4h: bool,
    div_against_D: bool,
    peak_bad_4h: bool,
    min_gain: float,
    v1h_thr: float,
    v4h_thr: float,
    vD_thr: float,
    noloss_min: float,
    min_tfs: int,
) -> bool:
    """PURE per-bar fire predicate for QUICK_REDUCE_STRONG / HLR_TOP_EXIT.

    Faithful replica of ez_positions_quick.py:3454-3490. `exhaust_*` / `div_against_*` /
    `peak_bad_*` are the side-resolved booleans (caller decides EXHAUST_UP-vs-DOWN,
    BEAR-vs-BULL, LH-vs-HH based on is_long). No state, no config, no side effects.

    The four per-TF confirmations (mirrors the live `_hte_tfs.append` sites):
      1h:  is_long ? vel_1h <  v1h_thr        : vel_1h >  abs(v1h_thr)
      4h:  (is_long ? vel_4h < v4h_thr : vel_4h > 0) OR exhaust_4h OR div_against_4h OR peak_bad_4h
      D :  (is_long ? vel_D  < vD_thr  : vel_D  > 0) OR exhaust_D  OR div_against_D
      W :  (is_long ? vel_W  < 0       : vel_W  > 0) OR exhaust_W
    Fire iff (#confirming TFs >= min_tfs) AND (at least one of 4h/D/W confirmed).
    """
    if gain_pct < noloss_min:
        return False
    if gain_pct < min_gain:
        return False
    tf_1h = (vel_1h < v1h_thr) if is_long else (vel_1h > abs(v1h_thr))
    if is_long:
        tf_4h = (vel_4h < v4h_thr) or exhaust_4h or div_against_4h or peak_bad_4h
        tf_D = (vel_D < vD_thr) or exhaust_D or div_against_D
        tf_W = (vel_W < 0.0) or exhaust_W
    else:
        tf_4h = (vel_4h > 0.0) or exhaust_4h or div_against_4h or peak_bad_4h
        tf_D = (vel_D > 0.0) or exhaust_D or div_against_D
        tf_W = (vel_W > 0.0) or exhaust_W
    count = int(tf_1h) + int(tf_4h) + int(tf_D) + int(tf_W)
    htf_ok = tf_4h or tf_D or tf_W
    return count >= min_tfs and htf_ok


def _is_exhaust(mom_state, is_long: bool) -> bool:
    """Normalize a live momentum_state string to the side-correct EXHAUST boolean."""
    want = _EXHAUST_UP if is_long else _EXHAUST_DOWN
    return mom_state == want


def _div_against(div_value, is_long: bool) -> bool:
    """Live: div=='BEAR' (long) / 'BULL' (short). NPZ int8: -1 BEAR / +1 BULL."""
    if isinstance(div_value, str):
        return div_value == (_DIV_BEAR if is_long else _DIV_BULL)
    try:
        v = int(div_value)
    except (TypeError, ValueError):
        return False
    return (v == -1) if is_long else (v == 1)


def _peak_bad(peak_value, is_long: bool) -> bool:
    """Live: peak=='LH' (long) / 'HH' (short). NPZ int8: -1 LH / +1 HH."""
    if isinstance(peak_value, str):
        return peak_value == (_PEAK_LH if is_long else _PEAK_HH)
    try:
        v = int(peak_value)
    except (TypeError, ValueError):
        return False
    return (v == -1) if is_long else (v == 1)


def check_quick_reduce_strong(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar wrapper. Returns (should_reduce, reason). Fire decision comes from the
    shared _quick_reduce_strong_fires() predicate so it cannot drift from the vec path.

    `indicators` keys read (live names): wt_velocity_1h, wt_velocity_4h, wt_velocity_D,
    wt_velocity_W, wt_acceleration_1h..W, wt_momentum_state_4h/D/W, wt_divergence_4h/D,
    wt_peak_structure_4h. Missing velocity/accel -> 0.0; missing state/div/peak -> '' (no
    confirm). Live momentum strings are honored if present; otherwise EXHAUST is
    reconstructed from velocity+acceleration exactly as ez_indicators.py:1638 defines it."""
    if not getattr(config, "HLR_TOP_EXIT_ENABLED", True):
        return False, ""
    min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs = _quick_reduce_strong_thresholds(config)
    noloss_min = max(noloss_min, 0.3)
    vel_1h = float(indicators.get("wt_velocity_1h") or 0.0)
    vel_4h = float(indicators.get("wt_velocity_4h") or 0.0)
    vel_D = float(indicators.get("wt_velocity_D") or 0.0)
    vel_W = float(indicators.get("wt_velocity_W") or 0.0)
    mom_4h = indicators.get("wt_momentum_state_4h", "")
    mom_D = indicators.get("wt_momentum_state_D", "")
    mom_W = indicators.get("wt_momentum_state_W", "")
    if mom_4h in ("", None):
        mom_4h = _reconstruct_mom(float(indicators.get("wt_acceleration_4h") or 0.0), vel_4h)
    if mom_D in ("", None):
        mom_D = _reconstruct_mom(float(indicators.get("wt_acceleration_D") or 0.0), vel_D)
    if mom_W in ("", None):
        mom_W = _reconstruct_mom(float(indicators.get("wt_acceleration_W") or 0.0), vel_W)
    exhaust_4h = _is_exhaust(mom_4h, is_long)
    exhaust_D = _is_exhaust(mom_D, is_long)
    exhaust_W = _is_exhaust(mom_W, is_long)
    div_4h = _div_against(indicators.get("wt_divergence_4h", ""), is_long)
    div_D = _div_against(indicators.get("wt_divergence_D", ""), is_long)
    peak_4h = _peak_bad(indicators.get("wt_peak_structure_4h", ""), is_long)
    fired = _quick_reduce_strong_fires(gain_pct, is_long, vel_1h, vel_4h, vel_D, vel_W,
                                       exhaust_4h, exhaust_D, exhaust_W, div_4h, div_D, peak_4h,
                                       min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs)
    if not fired:
        return False, ""
    side = "L" if is_long else "S"
    return True, f"HLR_TOP_EXIT_{side}_g={gain_pct:.2f}%"


def _reconstruct_mom(acceleration: float, velocity: float) -> str:
    """Replicate ez_indicators.py:1638-1645 momentum_state from (velocity, acceleration)."""
    wt_rising = velocity > 0
    if wt_rising and acceleration > 0:
        return "IMPULSE_UP"
    if wt_rising and acceleration <= 0:
        return _EXHAUST_UP
    if (not wt_rising) and acceleration < 0:
        return "IMPULSE_DOWN"
    return _EXHAUST_DOWN


def check_quick_reduce_strong_vec(
    config,
    gain_arr,
    is_long: bool,
    vel_1h_arr,
    vel_4h_arr,
    vel_D_arr,
    vel_W_arr,
    accel_4h_arr,
    accel_D_arr,
    accel_W_arr,
    div_4h_arr,
    div_D_arr,
    peak_4h_arr,
):
    """VECTORIZED per-bar QUICK_REDUCE_STRONG fire mask (backtest path). SAME thresholds +
    SAME predicate as the live scalar check_quick_reduce_strong, vectorized via numpy.
    Returns a bool ndarray; caller AND-gates it with position-open state and takes it as a
    per-bar STRONG_REDUCE candidate.

    EXHAUST is reconstructed from velocity+acceleration arrays (NPZ wt_velocity_{tf} /
    wt_acceleration_{tf}) — NOT from the NPZ wt_momentum_state int8 (whose semantics differ
    from the live EXHAUST string). div / peak arrays are NPZ int8 (-1/+1). gain_arr is the
    per-bar gain% of the open position."""
    g = np.asarray(gain_arr, dtype=float)
    n = len(g)
    if not getattr(config, "HLR_TOP_EXIT_ENABLED", True):
        return np.zeros(n, dtype=bool)
    min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs = _quick_reduce_strong_thresholds(config)
    noloss_min = max(noloss_min, 0.3)
    v1h = np.nan_to_num(np.asarray(vel_1h_arr, dtype=float))
    v4h = np.nan_to_num(np.asarray(vel_4h_arr, dtype=float))
    vD = np.nan_to_num(np.asarray(vel_D_arr, dtype=float))
    vW = np.nan_to_num(np.asarray(vel_W_arr, dtype=float))
    a4h = np.nan_to_num(np.asarray(accel_4h_arr, dtype=float))
    aD = np.nan_to_num(np.asarray(accel_D_arr, dtype=float))
    aW = np.nan_to_num(np.asarray(accel_W_arr, dtype=float))
    d4h = np.asarray(div_4h_arr).astype(int)
    dD = np.asarray(div_D_arr).astype(int)
    p4h = np.asarray(peak_4h_arr).astype(int)
    # EXHAUST reconstruction (mirror _reconstruct_mom + _is_exhaust).
    if is_long:
        exh4 = (v4h > 0) & (a4h <= 0)
        exhD = (vD > 0) & (aD <= 0)
        exhW = (vW > 0) & (aW <= 0)
        div4 = d4h == -1
        divD = dD == -1
        peak4 = p4h == -1
        tf_1h = v1h < v1h_thr
        tf_4h = (v4h < v4h_thr) | exh4 | div4 | peak4
        tf_D = (vD < vD_thr) | exhD | divD
        tf_W = (vW < 0.0) | exhW
    else:
        # EXHAUST_DOWN = (not rising) and accel>=0  → here vel<=0 & accel>=0 (else branch).
        exh4 = (~(v4h > 0)) & (a4h >= 0)
        exhD = (~(vD > 0)) & (aD >= 0)
        exhW = (~(vW > 0)) & (aW >= 0)
        div4 = d4h == 1
        divD = dD == 1
        peak4 = p4h == 1
        tf_1h = v1h > abs(v1h_thr)
        tf_4h = (v4h > 0.0) | exh4 | div4 | peak4
        tf_D = (vD > 0.0) | exhD | divD
        tf_W = (vW > 0.0) | exhW
    count = tf_1h.astype(int) + tf_4h.astype(int) + tf_D.astype(int) + tf_W.astype(int)
    htf_ok = tf_4h | tf_D | tf_W
    gate = (g >= noloss_min) & (g >= min_gain)
    return gate & (count >= min_tfs) & htf_ok


def quick_reduce_gain_ok(config, gain_pct: float) -> bool:
    """Per-bar GAIN gate for the vectorized mask, applied in the position loop with the
    SIMULATED position's real gain (2026-09-28 fix: npz['gain_pct'] does not exist in
    indicator NPZs, so the precomputed mask is indicator-only and this gate supplies the
    live gain conditions from ez_positions_quick.py:3454/3488: gain >= NOLOSS_MIN_PROFIT_PCT
    and gain >= HLR_TOP_MIN_GAIN_PCT)."""
    min_gain, _v1, _v4, _vD, noloss_min, _mt = _quick_reduce_strong_thresholds(config)
    return gain_pct >= noloss_min and gain_pct >= min_gain
