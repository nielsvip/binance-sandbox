"""
vec_decisions/reentry_breakout.py — SHARED scalar+vectorized REENTRY_BREAKOUT predicate.

Decision: OPEN — REENTRY_BREAKOUT (the DC-break fast-path reentry).

LIVE SOURCE OF TRUTH:
  ez_positions_quick.py:16588-16602 — the `_dc_reentry_breakout` computation inside
  process_single_reentry_evaluation_epq(). This is the PURE per-bar fire condition
  (a boolean test on DC channel breakout + optional K/WT filters on a filter-TF),
  evaluated BEFORE the live-only state gates (cooldown, position notional cap,
  delta-tolerance, LEGACY_DC_BREAKOUT_REENTRY, dispatch). Those state/async gates are
  intentionally NOT part of this predicate — they are not per-bar boolean tests and
  cannot be vectorized faithfully here (see notes in the structured report).

PARITY PATTERN (mirrors strategy_enhancements.py _pyramid_fires / check_pyramid_signal /
check_pyramid_signal_vec): one pure per-bar core predicate, a scalar wrapper, and a
numpy-vectorized version that share the EXACT same core logic so the two paths cannot drift.

Live logic replicated verbatim (LONG; SHORT is the mirror):
  _buf = 0.001
  _dc_3m_long_re   = dc_high_3m > 0 and price > dc_high_3m * (1 + _buf)
  _dc_1h15_long_re = (dc_high_1h > 0 and price > dc_high_1h * (1 + _buf))
                     or (allow_15m and dc_high_15m > 0 and price > dc_high_15m * (1 + _buf))
  fires = _dc_3m_long_re
          or (_dc_1h15_long_re
              and ((fk > fd) if (req_k and kdata) else True)
              and ((fw1 > fw2) if (req_wt and wtdata) else True))
where kdata = abs(fk) > 1e-9 or abs(fd) > 1e-9 ; wtdata = abs(fw1) > 1e-9 or abs(fw2) > 1e-9.

INDICATOR / NPZ FIELDS READ (filter-TF default '3m'):
  close (current_price), dc_high_3m, dc_low_3m, dc_high_1h, dc_low_1h,
  dc_high_15m, dc_low_15m, stoch_k_<ftf>, stoch_d_<ftf>, wt1_<ftf>, wt2_<ftf>
CONFIG THRESHOLDS:
  REENTRY2_DC_BREAK_ALLOW_15M (default True), REENTRY2_DC_BREAK_REQUIRE_K_FILTER (True),
  REENTRY2_DC_BREAK_REQUIRE_WT_FILTER (False), REENTRY2_DC_BREAK_FILTER_TF (default '3m').
"""
from __future__ import annotations

from typing import Tuple

_BUF = 0.001
_EPS = 1e-9


def _reentry_breakout_fires(
    price: float,
    dc_high_3m: float,
    dc_low_3m: float,
    dc_high_1h: float,
    dc_low_1h: float,
    dc_high_15m: float,
    dc_low_15m: float,
    fk: float,
    fd: float,
    fw1: float,
    fw2: float,
    is_long: bool,
    allow_15m: bool,
    require_k: bool,
    require_wt: bool,
) -> bool:
    """PURE per-bar REENTRY_BREAKOUT fire predicate (single source of truth).

    Verbatim port of ez_positions_quick.py:16588-16602. No config, no state, no I/O.
    `fk/fd/fw1/fw2` are the stoch K/D and WT1/WT2 on the configured filter-TF.
    """
    kdata = abs(fk) > _EPS or abs(fd) > _EPS
    wtdata = abs(fw1) > _EPS or abs(fw2) > _EPS
    if is_long:
        dc_3m_re = dc_high_3m > 0 and price > dc_high_3m * (1 + _BUF)
        dc_1h15_re = (dc_high_1h > 0 and price > dc_high_1h * (1 + _BUF)) or (
            allow_15m and dc_high_15m > 0 and price > dc_high_15m * (1 + _BUF)
        )
        k_ok = (fk > fd) if (require_k and kdata) else True
        wt_ok = (fw1 > fw2) if (require_wt and wtdata) else True
    else:
        dc_3m_re = dc_low_3m > 0 and price < dc_low_3m * (1 - _BUF)
        dc_1h15_re = (dc_low_1h > 0 and price < dc_low_1h * (1 - _BUF)) or (
            allow_15m and dc_low_15m > 0 and price < dc_low_15m * (1 - _BUF)
        )
        k_ok = (fk < fd) if (require_k and kdata) else True
        wt_ok = (fw1 < fw2) if (require_wt and wtdata) else True
    return bool(dc_3m_re or (dc_1h15_re and k_ok and wt_ok))


def _thresholds(config) -> Tuple[bool, bool, bool, str]:
    return (
        bool(getattr(config, "REENTRY2_DC_BREAK_ALLOW_15M", True)),
        bool(getattr(config, "REENTRY2_DC_BREAK_REQUIRE_K_FILTER", True)),
        bool(getattr(config, "REENTRY2_DC_BREAK_REQUIRE_WT_FILTER", False)),
        str(getattr(config, "REENTRY2_DC_BREAK_FILTER_TF", "3m")),
    )


def check_reentry_breakout(config, indicators: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path — fire decision comes from the shared _reentry_breakout_fires().

    Returns (fires, tf_tag). tf_tag mirrors the live `_dc_re_tf` ('3M'/'1H'/'15M').
    NOTE: this is ONLY the per-bar fire predicate. The live caller still applies the
    state gates (cooldown 900s, position-notional cap, delta tolerance, LEGACY flag)
    around it — those are deliberately out of scope of the shared predicate.
    """
    allow_15m, require_k, require_wt, ftf = _thresholds(config)

    def _sf(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    dc_high_3m = _sf(indicators.get("dc_high_3m", 0), 0.0)
    dc_low_3m = _sf(indicators.get("dc_low_3m", 0), 0.0)
    dc_high_1h = _sf(indicators.get("dc_high_1h", 0), 0.0)
    dc_low_1h = _sf(indicators.get("dc_low_1h", 0), 0.0)
    dc_high_15m = _sf(indicators.get("dc_high_15m", 0), 0.0)
    dc_low_15m = _sf(indicators.get("dc_low_15m", 0), 0.0)
    fk = _sf(indicators.get(f"stoch_k_{ftf}", 0), 0.0)
    fd = _sf(indicators.get(f"stoch_d_{ftf}", 0), 0.0)
    fw1 = _sf(indicators.get(f"wt1_{ftf}", 0), 0.0)
    fw2 = _sf(indicators.get(f"wt2_{ftf}", 0), 0.0)
    fires = _reentry_breakout_fires(
        current_price, dc_high_3m, dc_low_3m, dc_high_1h, dc_low_1h, dc_high_15m, dc_low_15m,
        fk, fd, fw1, fw2, is_long, allow_15m, require_k, require_wt,
    )
    if not fires:
        return False, ""
    if is_long:
        if dc_high_3m > 0 and current_price > dc_high_3m * (1 + _BUF):
            tf = "3M"
        elif dc_high_1h > 0 and current_price > dc_high_1h * (1 + _BUF):
            tf = "1H"
        else:
            tf = "15M"
    else:
        if dc_low_3m > 0 and current_price < dc_low_3m * (1 - _BUF):
            tf = "3M"
        elif dc_low_1h > 0 and current_price < dc_low_1h * (1 - _BUF):
            tf = "1H"
        else:
            tf = "15M"
    return True, tf


def check_reentry_breakout_vec(
    config,
    price_arr,
    dc_high_3m_arr,
    dc_low_3m_arr,
    dc_high_1h_arr,
    dc_low_1h_arr,
    dc_high_15m_arr,
    dc_low_15m_arr,
    fk_arr,
    fd_arr,
    fw1_arr,
    fw2_arr,
    is_long: bool,
):
    """VECTORIZED per-bar REENTRY_BREAKOUT fire mask — backtest path.

    SAME thresholds + SAME predicate as the live scalar check_reentry_breakout
    (vectorized via numpy). Returns a bool ndarray. Arrays are per-bar (NPZ in
    backtest); filter-TF arrays (fk/fd/fw1/fw2) correspond to REENTRY2_DC_BREAK_FILTER_TF.
    """
    import numpy as np

    allow_15m, require_k, require_wt, _ftf = _thresholds(config)
    p = np.asarray(price_arr, dtype=float)
    dh3 = np.asarray(dc_high_3m_arr, dtype=float)
    dl3 = np.asarray(dc_low_3m_arr, dtype=float)
    dh1h = np.asarray(dc_high_1h_arr, dtype=float)
    dl1h = np.asarray(dc_low_1h_arr, dtype=float)
    dh15 = np.asarray(dc_high_15m_arr, dtype=float)
    dl15 = np.asarray(dc_low_15m_arr, dtype=float)
    fk = np.asarray(fk_arr, dtype=float)
    fd = np.asarray(fd_arr, dtype=float)
    fw1 = np.asarray(fw1_arr, dtype=float)
    fw2 = np.asarray(fw2_arr, dtype=float)
    kdata = (np.abs(fk) > _EPS) | (np.abs(fd) > _EPS)
    wtdata = (np.abs(fw1) > _EPS) | (np.abs(fw2) > _EPS)
    if is_long:
        dc_3m_re = (dh3 > 0) & (p > dh3 * (1 + _BUF))
        dc_1h15_re = ((dh1h > 0) & (p > dh1h * (1 + _BUF)))
        if allow_15m:
            dc_1h15_re = dc_1h15_re | ((dh15 > 0) & (p > dh15 * (1 + _BUF)))
        if require_k:
            k_ok = np.where(kdata, fk > fd, True)
        else:
            k_ok = np.ones_like(p, dtype=bool)
        if require_wt:
            wt_ok = np.where(wtdata, fw1 > fw2, True)
        else:
            wt_ok = np.ones_like(p, dtype=bool)
    else:
        dc_3m_re = (dl3 > 0) & (p < dl3 * (1 - _BUF))
        dc_1h15_re = ((dl1h > 0) & (p < dl1h * (1 - _BUF)))
        if allow_15m:
            dc_1h15_re = dc_1h15_re | ((dl15 > 0) & (p < dl15 * (1 - _BUF)))
        if require_k:
            k_ok = np.where(kdata, fk < fd, True)
        else:
            k_ok = np.ones_like(p, dtype=bool)
        if require_wt:
            wt_ok = np.where(wtdata, fw1 < fw2, True)
        else:
            wt_ok = np.ones_like(p, dtype=bool)
    return dc_3m_re | (dc_1h15_re & k_ok & wt_ok)
