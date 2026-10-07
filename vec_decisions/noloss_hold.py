"""Shared NOLOSS hold + STOP_LOSS fire predicates — ONE logic for the live managers
(ez_manage.process_position, tradier_manage.process_position) mirroring
v12_quick_engine.simulate_one (EXIT_STRUCTURAL).

NOLOSS (vec ~13926-13938): when NOLOSS_ENABLED and the technical exit would close
at a loss, HOLD (skip the exit) unless (a) the WT-5OF5 bypass passes (>=
NOLOSS_BYPASS_WT_5OF5_MIN_TFS of 5m/15m/1h/4h/D WT TFs against the side) or
(b) the DC-recovery escape fires (entry stranded beyond dc_4h AND price back
within DC_RECOVERY_EXIT_TOLERANCE_PCT of entry). Default OFF = inert.

STOP_LOSS (vec ~13710): fixed-% full close when live pnl% <= -STOP_LOSS_PCT.
Evaluated right after PROFIT_TARGET (vec adjacency). Default OFF = inert.

Pure functions: no state, no I/O. Live defaults inert -> zero live behaviour
change until an operator promotes a value.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Tuple

_WT_TFS = ("5m", "15m", "1h", "4h", "D")


def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def count_wt_against(ind: Mapping[str, Any], is_long: bool) -> int:
    """Per-TF count of WT TFs against the side (vec 11664-11673 semantics).

    LONG counts wt1<wt2, SHORT wt1>wt2; TFs with both legs zero (= missing data)
    are skipped, exactly like vec's ~((w1==0)&(w2==0)) mask.
    """
    n = 0
    for tf in _WT_TFS:
        w1 = _f((ind or {}).get(f"wt1_{tf}", 0.0), 0.0)
        w2 = _f((ind or {}).get(f"wt2_{tf}", 0.0), 0.0)
        if w1 == 0 and w2 == 0:
            continue
        if (w1 < w2) if is_long else (w1 > w2):
            n += 1
    return n


def noloss_hold_loss_exit(get: Callable[[str, Any], Any], is_long: bool, gain_pct: float, entry_price: float, px: float, ind: Mapping[str, Any]) -> Tuple[bool, str]:
    """True = HOLD (suppress the technical loss exit). get(name, default) reads config."""
    if not bool(get("NOLOSS_ENABLED", False)):
        return False, ""
    if _f(gain_pct, 0.0) >= 0:
        return False, ""
    if bool(get("NOLOSS_BYPASS_WT_5OF5_ENABLED", False)):
        try:
            min_tfs = int(float(get("NOLOSS_BYPASS_WT_5OF5_MIN_TFS", 5) or 5))
        except (TypeError, ValueError):
            min_tfs = 5
        if count_wt_against(ind, is_long) >= min_tfs:
            return False, "NOLOSS_BYPASS_WT"
    if bool(get("DC_RECOVERY_EXIT_ENABLED", True)):
        tol = _f(get("DC_RECOVERY_EXIT_TOLERANCE_PCT", 0.10), 0.10)
        entry = _f(entry_price, 0.0)
        price = _f(px, 0.0)
        dc4 = _f((ind or {}).get("dc_high_4h" if is_long else "dc_low_4h", 0.0), 0.0)
        stranded = (entry > dc4 and dc4 > 0) if is_long else (entry < dc4 and dc4 > 0)
        near = abs(price - entry) / entry * 100 < tol if entry > 0 else False
        if stranded and near:
            return False, "DC_RECOVERY"
    return True, "NOLOSS_HOLD"


def stop_loss_fires(get: Callable[[str, Any], Any], gain_pct: float) -> Tuple[bool, str]:
    """Fixed-% stop (vec 13710): gain% <= -STOP_LOSS_PCT with the gate enabled."""
    if not bool(get("STOP_LOSS_ENABLED", False)):
        return False, ""
    pct = _f(get("STOP_LOSS_PCT", 2.0), 2.0)
    if _f(gain_pct, 0.0) <= -pct:
        return True, f"STOP_LOSS_g{gain_pct:.2f}"
    return False, ""
