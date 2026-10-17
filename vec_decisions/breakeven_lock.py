"""BREAKEVEN_LOCK + LOSS_PRONE_ENTRY_FILTER (USER 2026-10-17: "what we do need is a stricter enforcement of 'closing before entering
into a loss' and a clear database of what functions ended having to close in a loss - these are to be excluded from newborn
protection and should only be accepted with additional filters in future application (unless backtest deltas say otherwise)").

Evidence (data/loss_close_db.sqlite, boards' own 30-day ledgers): 128 crypto losers had peaked >= +0.5% first (-350.6% summed),
44 on stocks (-122.0%). The live HARD_BREAKEVEN_FLOOR was never implemented in the engine, and the PEAK_GIVEBACK hard-zero was
switched off 2026-04-27 (it closed breakouts at exactly 0% on the first retest) — so no board protected a winner from turning red.

BREAKEVEN_LOCK (decided on CLOSED 15m bars, close basis, so live follows through the VEC_EXACT twin):
  armed  once the position's best close-basis gain since entry >= BREAKEVEN_LOCK_ARM_PCT (0.5 / 1.0 / 1.5)
  fires  when the bar's close-basis gain <= BREAKEVEN_LOCK_FLOOR_PCT (0.10 = costs / 0.0 / 0.25) -> CLOSE "BREAKEVEN_LOCK"
  scope  BREAKEVEN_LOCK_SCOPE: "off" | "loss_prone" (default: only positions opened by a loss-prone function) | "all"
LOSS_PRONE_ENTRY_FILTER (default on; a template row so a board can switch it off when its delta says so):
  an OPEN whose function is flagged in data/loss_prone_entries.json must also pass LOSS_PRONE_ENTRY_MIN_FILTERS (2) of
  {wt1_1h vs wt2_1h with the side, wt1_4h vs wt2_4h with the side, close vs ema_50_1h with the side}. Missing data = not passed.
"""
from __future__ import annotations

import math

REASON = "BREAKEVEN_LOCK"


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


def lock_in_scope(cfg, entry_family: str, flags) -> bool:
    sc = str(_get(cfg, "BREAKEVEN_LOCK_SCOPE", "loss_prone") or "loss_prone").strip().lower()
    if sc == "all":
        return True
    if sc == "loss_prone":
        return str(entry_family or "") in (flags or ())
    return False


def lock_fires(cfg, mfe_close: float, gain_close: float) -> bool:
    arm = _num(_get(cfg, "BREAKEVEN_LOCK_ARM_PCT", 0.5), 0.5)
    floor = _num(_get(cfg, "BREAKEVEN_LOCK_FLOOR_PCT", 0.10), 0.10)
    return _num(mfe_close, 0.0) >= arm and _num(gain_close, 0.0) <= floor


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
