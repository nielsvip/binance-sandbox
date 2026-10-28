"""stale_set_halt_twin — the STALE-SET 2-LOSS HALT decision, ONE definition for live and the vector.

USER 2026-10-27 (operator ruling, verbatim): "board opens : vectorized should have CIRCUIT_BREAKER/STALE_SET_HALT
and trade on it if not add the functions and switches".

Live (stale_set_halt.evaluate, BIBLE §102): a sym_side whose active set was last recalculated by a board more than
STALE_SET_HALT_DAYS ago AND whose last STALE_SET_HALT_LOSSES closes since the set was applied were ALL net losses
refuses every new exposure until a new board is applied. Before this module the vector never applied it, so a board
open on a halted sym_side was a live bypass (VEC_LAST_OVERRIDE gate STALE_SET_HALT).

Both sides call `halt_fires`:
  * live  — stale_set_halt.evaluate with the REAL recorded closes;
  * board — v12_quick_engine.simulate_one with the board's own ledger closes after the apply epoch. The two epochs
    reach the engine as QuickConfig fields STALE_SET_RECALC_EPOCH / STALE_SET_APPLY_EPOCH, which only the
    forward-state builder sets (tools/v15_forward_state.py). They default to 0.0, so every backtest/board run is
    inert (a 30D board is by definition a fresh recalc) and only the forward-rolled live state can halt.
Pure: no IO, no config import.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

DAY_S = 86400.0


def last_n_all_losses(net_pcts: List[float], n: int) -> bool:
    """The last n values exist and are ALL < 0 (a net loss). n <= 0 never hits."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False
    if n <= 0 or len(net_pcts) < n:
        return False
    try:
        return all(float(v) < 0 for v in net_pcts[-n:])
    except (TypeError, ValueError):
        return False


def age_days(now: float, recalc_epoch: float) -> float:
    try:
        return max(0.0, (float(now) - float(recalc_epoch or 0.0)) / DAY_S)
    except (TypeError, ValueError):
        return 0.0


def is_stale(now: float, recalc_epoch: float, days: float) -> bool:
    """recalc_epoch <= 0 means no anchor (backtest / board run): never stale."""
    try:
        if float(recalc_epoch or 0.0) <= 0.0:
            return False
        return age_days(now, recalc_epoch) > float(days)
    except (TypeError, ValueError):
        return False


def halt_decision(age_d: float, days: float, trailing_hit: bool) -> bool:
    """The live HALT predicate exactly: vector older than `days` AND the trailing-loss window hit."""
    try:
        return float(age_d) > float(days) and bool(trailing_hit)
    except (TypeError, ValueError):
        return False


def halt_fires(now: float, recalc_epoch: float, apply_epoch: float, closes: Iterable[Tuple[float, float]], days: float, losses: int) -> Tuple[bool, str]:
    """(halt?, why). closes = (close_ts, net_pct) pairs; only closes strictly after apply_epoch count."""
    try:
        if float(recalc_epoch or 0.0) <= 0.0:
            return False, "NO_ANCHOR"
    except (TypeError, ValueError):
        return False, "NO_ANCHOR"
    a = age_days(now, recalc_epoch)
    if a <= float(days):
        return False, "FRESH"
    since = float(apply_epoch or 0.0)
    rows = sorted([(float(t), float(p)) for t, p in closes if float(t) > since], key=lambda r: r[0])
    nets = [p for _, p in rows]
    hit = last_n_all_losses(nets, losses)
    if halt_decision(a, days, hit):
        return True, f"STALE_SET_HALT vector_age={a:.1f}d>{float(days):g}d AND last {int(losses)} closes net " + ",".join(f"{p:+.2f}%" for p in nets[-int(losses):])
    return False, "NO_TRAILING_LOSSES"
