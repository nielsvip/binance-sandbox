"""REENTRY_PATHWAYS — vector twin of the live reentry loop pathways that need only 15m+ arrays (Agent C2, staged b3).

LIVE SOURCE: ez_manage.py reentry_enforcement_loop (ez_manage.py:36302-36826), `should_reenter` pathways, evaluated every 15s for each pending
reentry (a key that exited): P0 3m-rescue (3m arrays), PATHWAY F FAVORABLE-MOVE (36594-36624): price moved >= REENTRY_FAVORABLE_MOVE_PCT (0.5%)
in our favour since the exit AND htf_count>=REENTRY_FAVORABLE_HTF_MIN(2) of (wt1_1h>wt2_1h, wt1_4h>wt2_4h, wt1_D>wt2_D) (long; mirror short) -> reenter,
bypassing the safety gates; AGGR/P1 (3m/1m arrays), CROSSED/NOCROSS_FULLSTACK (3m delta + full stack: 3m arrays). HARDCODED_RALLY (36351-36375) =
close>exit (+wt rising when REQUIRE_WT) -> force reentry, already in simulate_one.
The vector engine had `if REENTRY_MANDATORY: fire = True` after every close = reenter on EVERY bar, which live never does (REENTRY_MANDATORY only
enables the loop; a pathway must fire). This module supplies the pathway that IS vectorizable (F); the 3m/1m pathways are UNWIRABLE_NO_3M and the
blanket fire is made switchable (REENTRY_BLANKET_FIRE_ENABLED, default True = unchanged baseline) so live-like behaviour (False) is testable.
"""
from __future__ import annotations

import numpy as np


def favorable_move_mask(npz, n, is_long, cfg, safe):
    """bool[n]: HTF alignment count >= REENTRY_FAVORABLE_HTF_MIN (the price-move leg needs the last exit price, applied per bar in simulate_one)."""
    cnt = np.zeros(n, dtype=np.int8)
    for tf in ('1h', '4h', 'D'):
        w1, w2 = safe(npz, f'wt1_{tf}', n, 0.0), safe(npz, f'wt2_{tf}', n, 0.0)
        a = (w1 > w2) if is_long else (w1 < w2)
        cnt = cnt + a.astype(np.int8)
    return cnt >= int(getattr(cfg, 'REENTRY_FAVORABLE_HTF_MIN', 2) or 2)


def favorable_price_ok(is_long: bool, px: float, last_exit_px: float, pct: float) -> bool:
    if not (last_exit_px and last_exit_px > 0 and px and px > 0):
        return False
    f = float(pct) / 100.0
    return px >= last_exit_px * (1.0 + f) if is_long else px <= last_exit_px * (1.0 - f)
