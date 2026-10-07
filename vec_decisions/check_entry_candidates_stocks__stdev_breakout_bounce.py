"""STDEV_BREAKOUT + STDEV_BOUNCE (stocks entry) — stateless BB %B entries (per HTF).

LIVE SOURCE: tradier_manage.py should_enter_long ~11910/11921, should_enter_short ~12192/12203.
  BREAKOUT LONG  fires when bb_pct_b_<htf> >= STDEV_BREAKOUT_PCTB_LONG  AND rvol >= rvol_min
  BREAKOUT SHORT fires when bb_pct_b_<htf> <= STDEV_BREAKOUT_PCTB_SHORT AND rvol >= rvol_min
  BOUNCE   LONG  fires when bb_pct_b_<htf> <= STDEV_BOUNCE_PCTB_LONG    AND rvol >= rvol_min
  BOUNCE   SHORT fires when bb_pct_b_<htf> >= STDEV_BOUNCE_PCTB_SHORT   AND rvol >= rvol_min
  rvol = relative_volume_<htf> (default 1.0); bb_pct_b default 0.5.
  Live iterates an HTF list and returns True on FIRST htf that fires; per-HTF predicate below.

Defaults (faithful): BREAKOUT pctb_long=1.0/pctb_short=0.0/rvol_min=1.2;
BOUNCE pctb_long=0.05/pctb_short=0.95/rvol_min=1.2.

2026-05-30 PARITY: shared pure per-(bar,htf) predicate drives scalar+vec.
NPZ fields: bb_pct_b_<htf>, relative_volume_<htf> (both present in NPZ).
"""
from typing import Tuple


def _stdev_breakout_fires(pctb: float, rvol: float, is_long: bool,
                          pctb_long: float, pctb_short: float, rvol_min: float) -> bool:
    """PURE per-(bar,htf) BREAKOUT fire predicate."""
    if is_long:
        return pctb >= pctb_long and rvol >= rvol_min
    return pctb <= pctb_short and rvol >= rvol_min


def _stdev_bounce_fires(pctb: float, rvol: float, is_long: bool,
                        pctb_long: float, pctb_short: float, rvol_min: float) -> bool:
    """PURE per-(bar,htf) BOUNCE fire predicate."""
    if is_long:
        return pctb <= pctb_long and rvol >= rvol_min
    return pctb >= pctb_short and rvol >= rvol_min


def _breakout_params(config):
    return (float(getattr(config, "STDEV_BREAKOUT_PCTB_LONG", 1.0)),
            float(getattr(config, "STDEV_BREAKOUT_PCTB_SHORT", 0.0)),
            float(getattr(config, "STDEV_BREAKOUT_RVOL_MIN", 1.2)))


def _bounce_params(config):
    return (float(getattr(config, "STDEV_BOUNCE_PCTB_LONG", 0.05)),
            float(getattr(config, "STDEV_BOUNCE_PCTB_SHORT", 0.95)),
            float(getattr(config, "STDEV_BOUNCE_RVOL_MIN", 1.2)))


def check_stdev_breakout(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar BREAKOUT path — iterates HTF list, fires on first match."""
    if not getattr(config, "STDEV_BREAKOUT_ENABLED", False):
        return False, ""
    htf_list = list(getattr(config, "STDEV_BREAKOUT_HTF_LIST", None) or ["D", "4h"])
    pl, ps, rmin = _breakout_params(config)
    for htf in htf_list:
        pctb = float(indicators.get(f"bb_pct_b_{htf}", 0.5) or 0.5)
        rvol = float(indicators.get(f"relative_volume_{htf}", 1.0) or 1.0)
        if _stdev_breakout_fires(pctb, rvol, is_long, pl, ps, rmin):
            return True, f"STDEV_BREAKOUT_{'LONG' if is_long else 'SHORT'}_{htf}"
    return False, ""


def check_stdev_bounce(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar BOUNCE path — iterates HTF list, fires on first match."""
    if not getattr(config, "STDEV_BOUNCE_ENABLED", False):
        return False, ""
    htf_list = list(getattr(config, "STDEV_BOUNCE_HTF_LIST", None) or ["D", "4h"])
    pl, ps, rmin = _bounce_params(config)
    for htf in htf_list:
        pctb = float(indicators.get(f"bb_pct_b_{htf}", 0.5) or 0.5)
        rvol = float(indicators.get(f"relative_volume_{htf}", 1.0) or 1.0)
        if _stdev_bounce_fires(pctb, rvol, is_long, pl, ps, rmin):
            return True, f"STDEV_BOUNCE_{'LONG' if is_long else 'SHORT'}_{htf}"
    return False, ""


def check_stdev_breakout_vec(config, pctb_arr, rvol_arr, is_long):
    """VECTORIZED single-HTF BREAKOUT mask (caller ORs across HTFs). SAME predicate."""
    import numpy as np
    pb = np.asarray(pctb_arr, dtype=float)
    rv = np.asarray(rvol_arr, dtype=float)
    pl, ps, rmin = _breakout_params(config)
    if is_long:
        return (pb >= pl) & (rv >= rmin)
    return (pb <= ps) & (rv >= rmin)


def check_stdev_bounce_vec(config, pctb_arr, rvol_arr, is_long):
    """VECTORIZED single-HTF BOUNCE mask (caller ORs across HTFs). SAME predicate."""
    import numpy as np
    pb = np.asarray(pctb_arr, dtype=float)
    rv = np.asarray(rvol_arr, dtype=float)
    pl, ps, rmin = _bounce_params(config)
    if is_long:
        return (pb <= pl) & (rv >= rmin)
    return (pb >= ps) & (rv >= rmin)
