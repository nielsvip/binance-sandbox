"""PEAK_GIVEBACK scope + LOSS_PRONE_ENTRY_FILTER (USER 2026-10-17: "what we do need is a stricter enforcement of 'closing before
entering into a loss' and a clear database of what functions ended having to close in a loss - these are to be excluded from newborn
protection and should only be accepted with additional filters in future application (unless backtest deltas say otherwise)";
"hard-zero breakeven protection ... NEVER SHOULD BE ENABLED OR DISABLED IT IS A SWITCH THAT FLIPS ACCORDING TO v15_pilot.py test results").

Evidence (data/loss_close_db.sqlite, boards' own 30-day ledgers): 128 crypto losers had peaked >= +0.5% first (-350.6% summed),
44 on stocks (-122.0%). The close-before-a-loss rule already exists as the PEAK_GIVEBACK family (live ez_positions_quick
hard-zero / drop branch, pure core vec_decisions/check_exit_candidates_crypto__peak_giveback.py) but the engine never called it,
so every pilot test of PEAK_GIVEBACK_HARD_ZERO_ENABLED measured delta 0. v12_quick_engine now runs that SAME core per closed bar
(close basis, reason PEAK_GIVEBACK_GAIN_EROSION_STOP) — the pilot's measured delta flips it; nobody sets it by hand.
  PEAK_GIVEBACK_SCOPE  "all" (the existing behaviour) | "loss_prone" (only positions opened by a function flagged in
                       data/loss_prone_entries.json) — a template option the pilot tests like any other.
LOSS_PRONE_ENTRY_FILTER (template switch; boards switch it off when their delta says so):
  an OPEN whose function is flagged in data/loss_prone_entries.json must also pass LOSS_PRONE_ENTRY_MIN_FILTERS (2) of
  {wt1_1h vs wt2_1h with the side, wt1_4h vs wt2_4h with the side, close vs ema_50_1h with the side}. Missing data = not passed.
"""
from __future__ import annotations

import math



def _num(v, d=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _get(cfg, k, d):
    v = cfg(k, d) if callable(cfg) else getattr(cfg, k, d)
    return d if v is None else v


def _on(cfg, k, d):
    v = _get(cfg, k, d)
    return (str(v).strip().lower() in ("1", "true", "yes")) if isinstance(v, str) else bool(v)


def scope_covers(cfg, entry_family: str, flags) -> bool:
    sc = str(_get(cfg, "PEAK_GIVEBACK_SCOPE", "all") or "all").strip().lower()
    if sc == "loss_prone":
        return str(entry_family or "") in (flags or ())
    return True


def entry_filters_pass(cfg, is_long: bool, close: float, wt1_1h: float, wt2_1h: float, wt1_4h: float, wt2_4h: float, ema50_1h: float) -> bool:
    need = int(_num(_get(cfg, "LOSS_PRONE_ENTRY_MIN_FILTERS", 2), 2))
    c = _num(close, 0.0)
    a1, b1, a4, b4, e = (_num(x, float("nan")) for x in (wt1_1h, wt2_1h, wt1_4h, wt2_4h, ema50_1h))
    ok = 0
    if math.isfinite(a1) and math.isfinite(b1) and (a1 != 0 or b1 != 0):
        ok += (a1 > b1) if is_long else (a1 < b1)
    if math.isfinite(a4) and math.isfinite(b4) and (a4 != 0 or b4 != 0):
        ok += (a4 > b4) if is_long else (a4 < b4)
    if math.isfinite(e) and e > 0 and c > 0:
        ok += (c > e) if is_long else (c < e)
    return ok >= need
