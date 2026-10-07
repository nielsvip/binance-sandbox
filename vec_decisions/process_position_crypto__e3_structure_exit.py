"""SHARED scalar+vectorized predicate for the LIVE decision E_3_STRUCTURE_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41577-41587.

Counts how many of {15m,1h,4h} have wt_structure against the position; fires
when count >= 2. wt_structure_* are STRING enums (LH/LL/HH/HL).

  LONG  against on TF iff wt_structure_<TF> in {LH, LL}
  SHORT against on TF iff wt_structure_<TF> in {HH, HL}
  fires iff count(against over 15m,1h,4h) >= 2

(The E_3 mode 0/1/2 is a config switch: 0=off, 1=shadow/log-only, 2=live exit.
The FIRE predicate (count>=2) is identical regardless of mode — mode only gates
whether the exit executes. This core returns the count>=2 predicate.)

CLASSIFICATION: vectorized. Pure per-bar predicate on three NPZ string fields.
"""
from typing import Tuple
import numpy as np

_TFS = ("15m", "1h", "4h")
_LONG_AGAINST = ("LH", "LL")
_SHORT_AGAINST = ("HH", "HL")


def _e3_against_count(structs: dict, is_long: bool) -> int:
    against = _LONG_AGAINST if is_long else _SHORT_AGAINST
    c = 0
    for tf in _TFS:
        s = (structs.get(tf, "") or "").upper()
        if s in against:
            c += 1
    return c


def _e3_structure_fires(structs: dict, is_long: bool) -> bool:
    return _e3_against_count(structs, is_long) >= 2


def check_e3_structure_exit(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path (mode>0 enable gate applied by caller)."""
    structs = {tf: str((indicators or {}).get(f"wt_structure_{tf}", "") or "") for tf in _TFS}
    cnt = _e3_against_count(structs, is_long)
    if cnt < 2:
        return False, ""
    return True, f"E_3_STRUCTURE_EXIT_{cnt}TF"


def check_e3_structure_exit_vec(config, struct_arrs: dict, is_long):
    """VECTORIZED per-bar fire mask. struct_arrs maps TF -> array of strings.
    SAME count-then-threshold logic."""
    against = _LONG_AGAINST if is_long else _SHORT_AGAINST
    n = len(struct_arrs[_TFS[0]])
    cnt = np.zeros(n, dtype=int)
    for tf in _TFS:
        up = np.array([(str(x) or "").upper() for x in struct_arrs[tf]], dtype=object)
        hit = np.zeros(n, dtype=bool)
        for tok in against:
            hit = hit | (up == tok)
        cnt = cnt + hit.astype(int)
    return cnt >= 2
