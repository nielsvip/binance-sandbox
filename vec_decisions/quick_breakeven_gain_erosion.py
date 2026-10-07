# ═══════════════════════════════════════════════════════════════════════════
# QUICK_BREAKEVEN_GAIN_EROSION — shared scalar + vectorized predicate
# ═══════════════════════════════════════════════════════════════════════════
# 2026-05-30 PARITY (mirrors strategy_enhancements.py _pyramid_fires pattern):
# the LIVE scalar (check_quick_breakeven_gain_erosion) AND the vectorized backtest
# (check_quick_breakeven_gain_erosion_vec) BOTH derive their fire decision from the
# single pure core predicate _quick_breakeven_gain_erosion_fires() below, so the two
# paths CANNOT drift. The core is pure: no config, no async, no state lookups.
#
# LIVE SOURCE: ez_positions_quick.py:14130-14156 (the
#   `BREAKEVEN_GAIN_EROSION_STOP_age{N}m_gain{g}%` exit, prefixed QUICK_ downstream).
# Extracted boolean test (faithful to live, lines 14130-14154):
#   GATE  : not hard_exit_reason, not hedge, not grace_period, ENABLED, not trend_veto
#   FIRE  : age >= BREAKEVEN_GRACE_MINUTES
#       AND gain in window:
#             require_profit: min_gain <= gain < max(min_gain+0.5, 0.02)
#             else          : gain < 0.02
#       AND HARD_BREAKEVEN peak gate:
#             hbf_enabled and max_gain < hbf_min_peak  -> BLOCK (waiting for R2/PPL)
#             else                                     -> may fire
#       AND HTF-veto override semantics (live lines 14146-14152):
#             hbf_override = hbf_enabled and (max_gain >= hbf_min_peak)
#             if htf_veto_active and not hbf_override  -> BLOCK
#             else (hbf_override OR not htf_veto_active) -> FIRE
#
# The two runtime-only gates (trend_regime veto, HTF multi-TF veto) are passed in
# as plain booleans so the predicate stays pure. In live they are computed from
# multi-TF WT alignment / 1h-trend-regime (ez_positions_quick.py:14116-14129); in
# vec backtest they are normally False (NPZ has the WT fields to compute htf_veto
# but the historical vec model in vec_paths/live_only_signals_batch5.py omitted it).
# ═══════════════════════════════════════════════════════════════════════════
from typing import Tuple
import numpy as np


def _quick_breakeven_gain_erosion_fires(
    gain_pct: float,
    age_min: float,
    max_gain_pct: float,
    htf_veto_active: bool,
    trend_veto_active: bool,
    grace_min: float,
    require_profit: bool,
    min_gain: float,
    hbf_enabled: bool,
    hbf_min_peak: float,
) -> bool:
    """Pure per-bar fire decision for QUICK_BREAKEVEN_GAIN_EROSION_STOP.
    Single source of truth shared by scalar + vec. No config, no state, no I/O."""
    if trend_veto_active:
        return False
    if age_min < grace_min:
        return False
    upper = max(min_gain + 0.5, 0.02)
    if require_profit:
        in_window = (min_gain <= gain_pct) and (gain_pct < upper)
    else:
        in_window = gain_pct < 0.02
    if not in_window:
        return False
    hbf_override = hbf_enabled and (max_gain_pct >= hbf_min_peak)
    if hbf_enabled and (max_gain_pct < hbf_min_peak):
        return False
    if htf_veto_active and not hbf_override:
        return False
    return True


def _be_erosion_thresholds(config):
    return (
        float(getattr(config, "BREAKEVEN_GRACE_MINUTES", 5.0)),
        bool(getattr(config, "BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT", True)),
        float(getattr(config, "BREAKEVEN_GAIN_EROSION_MIN_GAIN",
                      float(getattr(config, "COMMISSION_BUFFER_PCT", 0.10)))),
        bool(getattr(config, "HARD_BREAKEVEN_FLOOR_ENABLED", True)),
        float(getattr(config, "HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5)),
    )


def check_quick_breakeven_gain_erosion(
    config,
    indicators: dict,
    gain_pct: float,
    age_min: float,
    max_gain_pct: float,
    htf_veto_active: bool = False,
    trend_veto_active: bool = False,
) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_close, reason). Fire decision comes from the
    shared _quick_breakeven_gain_erosion_fires() predicate. `indicators` is accepted
    for signature symmetry with the live call site (it reads no indicator fields —
    the live BE block is driven purely by gain/age/max_gain/veto state)."""
    if not getattr(config, "BREAKEVEN_GAIN_EROSION_ENABLED", False):
        return False, ""
    grace_min, require_profit, min_gain, hbf_enabled, hbf_min_peak = _be_erosion_thresholds(config)
    if not _quick_breakeven_gain_erosion_fires(
        gain_pct, age_min, max_gain_pct, htf_veto_active, trend_veto_active,
        grace_min, require_profit, min_gain, hbf_enabled, hbf_min_peak,
    ):
        return False, ""
    return True, f"BREAKEVEN_GAIN_EROSION_STOP_age{age_min:.0f}m_gain{gain_pct:.2f}%"


def check_quick_breakeven_gain_erosion_vec(
    config,
    gain_arr,
    age_min_arr,
    max_gain_arr,
    htf_veto_arr=None,
    trend_veto_arr=None,
):
    """VECTORIZED per-bar fire mask — backtest path. SAME thresholds + SAME predicate
    as the live scalar check_quick_breakeven_gain_erosion (vectorized via numpy).
    Returns a bool ndarray; caller takes the FIRST True per position (close once).
    Arrays are per-bar (NPZ in backtest): gain%, position age (minutes), peak gain%.
    htf_veto_arr / trend_veto_arr default to all-False (historical vec model)."""
    g = np.asarray(gain_arr, dtype=float)
    n = len(g)
    if not getattr(config, "BREAKEVEN_GAIN_EROSION_ENABLED", False):
        return np.zeros(n, dtype=bool)
    grace_min, require_profit, min_gain, hbf_enabled, hbf_min_peak = _be_erosion_thresholds(config)
    age = np.asarray(age_min_arr, dtype=float)
    mg = np.asarray(max_gain_arr, dtype=float)
    if htf_veto_arr is None:
        htf = np.zeros(n, dtype=bool)
    else:
        htf = np.asarray(htf_veto_arr, dtype=bool)
    if trend_veto_arr is None:
        trend = np.zeros(n, dtype=bool)
    else:
        trend = np.asarray(trend_veto_arr, dtype=bool)
    upper = max(min_gain + 0.5, 0.02)
    if require_profit:
        in_window = (g >= min_gain) & (g < upper)
    else:
        in_window = g < 0.02
    m = (~trend) & (age >= grace_min) & in_window
    hbf_override = hbf_enabled & (mg >= hbf_min_peak)
    if hbf_enabled:
        m &= (mg >= hbf_min_peak)
    m &= ~(htf & ~hbf_override)
    return m
