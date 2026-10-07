"""check_exit_candidates_crypto__htf_wt_vetoes — TREND_REGIME_VETO + HTF_EXIT_VETO
WT-alignment veto predicates (shared scalar+vectorized).

These are not exits — they are pure per-bar WT-alignment booleans that GATE several
exit branches above (BREAKEVEN, DC4 struct, K1M reverse). Extracting them as shared
scalar+vec cores lets the vectorized backtest reproduce the live veto masks exactly,
which is required for faithful parity of the exits that consume them.

LIVE SOURCE 1 — TREND_REGIME_VETO (ez_positions_quick.py:13697-13706):
    if config.TREND_REGIME_VETO_ENABLED (default True):
        LONG  aligned = (wt1_1h>wt2_1h) and (wt1_4h>wt2_4h) and (wt1_D>wt2_D)
        SHORT aligned = (wt1_1h<wt2_1h) and (wt1_4h<wt2_4h) and (wt1_D<wt2_D)
        if aligned: _trend_veto_active = True
  -> ALL THREE of {1h,4h,D} WT must align WITH the position.

LIVE SOURCE 2 — HTF_EXIT_VETO (ez_positions_quick.py:14116-14127):
    if config.HTF_EXIT_VETO_ENABLED (default True) and not is_hedge:
        if abs(current_gain) <= HTF_EXIT_VETO_MAX_LOSS_PCT (default 2.0):
            LONG  aligned = int(wt1_1h>wt2_1h)+int(wt1_4h>wt2_4h)+int(wt1_D>wt2_D)
            SHORT aligned = int(wt1_1h<wt2_1h)+int(wt1_4h<wt2_4h)+int(wt1_D<wt2_D)
            _htf_veto_active = aligned >= HTF_EXIT_VETO_MIN_ALIGNED (default 2)
  -> at least MIN_ALIGNED of {1h,4h,D} align WITH the position, AND |gain| within band.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure cores _trend_regime_veto_active() / _htf_exit_veto_active() so they
CANNOT drift.

NPZ / indicator fields read:
  - wt1_1h / wt2_1h, wt1_4h / wt2_4h, wt1_D / wt2_D   — all present in NPZ
  - current_gain (state seam — HTF veto only)
"""
import numpy as np


def _aligned_count(w1_1h, w2_1h, w1_4h, w2_4h, w1_D, w2_D, is_long: bool) -> int:
    """Count of {1h,4h,D} WT TFs aligned WITH the position."""
    if is_long:
        return int(w1_1h > w2_1h) + int(w1_4h > w2_4h) + int(w1_D > w2_D)
    return int(w1_1h < w2_1h) + int(w1_4h < w2_4h) + int(w1_D < w2_D)


def _trend_regime_veto_active(w1_1h, w2_1h, w1_4h, w2_4h, w1_D, w2_D, is_long: bool) -> bool:
    """PURE: TREND_REGIME_VETO — ALL THREE TFs aligned. Mirrors live 13702/13704."""
    return _aligned_count(w1_1h, w2_1h, w1_4h, w2_4h, w1_D, w2_D, is_long) == 3


def _htf_exit_veto_active(w1_1h, w2_1h, w1_4h, w2_4h, w1_D, w2_D, is_long: bool,
                          current_gain: float, max_loss_pct: float, min_aligned: int) -> bool:
    """PURE: HTF_EXIT_VETO — >= min_aligned TFs aligned AND |gain| within band.
    Mirrors live 14119/14123-14127."""
    if abs(current_gain) > max_loss_pct:
        return False
    return _aligned_count(w1_1h, w2_1h, w1_4h, w2_4h, w1_D, w2_D, is_long) >= min_aligned


def _wt6(indicators):
    g = lambda k: float(indicators.get(k, 0) or 0)
    return (g("wt1_1h"), g("wt2_1h"), g("wt1_4h"), g("wt2_4h"), g("wt1_D"), g("wt2_D"))


def check_trend_regime_veto(config, indicators: dict, is_long: bool) -> bool:
    """LIVE/scalar TREND_REGIME_VETO active flag."""
    if not bool(getattr(config, "TREND_REGIME_VETO_ENABLED", True)):
        return False
    return _trend_regime_veto_active(*_wt6(indicators), is_long)


def check_htf_exit_veto(config, indicators: dict, is_long: bool, current_gain: float) -> bool:
    """LIVE/scalar HTF_EXIT_VETO active flag (is_hedge gate applied by caller)."""
    if not bool(getattr(config, "HTF_EXIT_VETO_ENABLED", True)):
        return False
    max_loss = float(getattr(config, "HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0))
    min_aligned = int(getattr(config, "HTF_EXIT_VETO_MIN_ALIGNED", 2))
    return _htf_exit_veto_active(*_wt6(indicators), is_long, current_gain, max_loss, min_aligned)


def check_trend_regime_veto_vec(config, w1_1h_a, w2_1h_a, w1_4h_a, w2_4h_a, w1_D_a, w2_D_a,
                                is_long) -> np.ndarray:
    """VECTORIZED TREND_REGIME_VETO active mask. SAME predicate as scalar."""
    a1 = np.asarray(w1_1h_a, dtype=float); b1 = np.asarray(w2_1h_a, dtype=float)
    a4 = np.asarray(w1_4h_a, dtype=float); b4 = np.asarray(w2_4h_a, dtype=float)
    aD = np.asarray(w1_D_a, dtype=float); bD = np.asarray(w2_D_a, dtype=float)
    n = len(a1)
    if not bool(getattr(config, "TREND_REGIME_VETO_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    if is_long:
        return (a1 > b1) & (a4 > b4) & (aD > bD)
    return (a1 < b1) & (a4 < b4) & (aD < bD)


def check_htf_exit_veto_vec(config, w1_1h_a, w2_1h_a, w1_4h_a, w2_4h_a, w1_D_a, w2_D_a,
                            is_long, gain_arr) -> np.ndarray:
    """VECTORIZED HTF_EXIT_VETO active mask. SAME predicate as scalar."""
    a1 = np.asarray(w1_1h_a, dtype=float); b1 = np.asarray(w2_1h_a, dtype=float)
    a4 = np.asarray(w1_4h_a, dtype=float); b4 = np.asarray(w2_4h_a, dtype=float)
    aD = np.asarray(w1_D_a, dtype=float); bD = np.asarray(w2_D_a, dtype=float)
    g = np.asarray(gain_arr, dtype=float)
    n = len(a1)
    if not bool(getattr(config, "HTF_EXIT_VETO_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    max_loss = float(getattr(config, "HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0))
    min_aligned = int(getattr(config, "HTF_EXIT_VETO_MIN_ALIGNED", 2))
    if is_long:
        cnt = (a1 > b1).astype(int) + (a4 > b4).astype(int) + (aD > bD).astype(int)
    else:
        cnt = (a1 < b1).astype(int) + (a4 < b4).astype(int) + (aD < bD).astype(int)
    return (np.abs(g) <= max_loss) & (cnt >= min_aligned)
