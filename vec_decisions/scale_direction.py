"""SCALE_DIRECTION law — which way a POSITION-SCALING act may point (USER 2026-10-22).

USER: "the reduce mechanism is broken ... reducing when it should be augmenting (price rising)
but not reducing when the price is falling (vv shorts)".

MEASURED before writing a line (391 published board charts, data/reports/charts_1M, per-producer
census in tools/scale_direction_census.py):

    act      producer                                  n      in profit   on a favourable 4-bar move
    REDUCE   WT_CROSS_EXIT (1h WT turns against)    6320         100.0%         63.3%
    REDUCE   SATOSHIT_EXIT_PARTIAL                  2482          51.4%         51.5%
    REDUCE   PARTIAL_PROFIT_LOCK ladder             2145         100.0%         95.4%
    REDUCE   REGIME / QUICK / HLR reduce block      1439          52.3%         64.7%
    REDUCE   STDEV_REJECT_EXIT                       344          99.4%         88.7%
    REDUCE   giveback / breakeven protection         123          88.6%         74.8%
    AUGMENT  HAIKU winner augment                     460         100.0%         99.1%
    AUGMENT  UAG gain ladder                           94         100.0%        100.0%
    AUGMENT  AUGMENT_AT_LOSS                           86           0.0%         65.1%
    TOTAL    12,936 REDUCE vs 747 AUGMENT — 17 scale-OUT acts for every scale-IN act.

So the board scales out of a rally (PARTIAL_PROFIT_LOCK alone: 2,145 cuts, 95.4% of them while price
was rising in the position's favour), the adverse-side family is 123 of 12,936 reduces (0.95%), and the
only producer that scales IN on an adverse move is AUGMENT_AT_LOSS — every polarity inverted.

THE LAW (this module is the predicate; v12_quick_engine holds the call sites):
  * price moving in the position's favour  -> the scaling act is AUGMENT; a partial REDUCE is refused.
  * price moving against the position      -> the scaling act is REDUCE; a partial AUGMENT is refused.
  * direction is the recent price MOVE (close[i] vs close[i-lookback]), not the sign of the P&L: a
    position still in profit that is giving back from its peak IS falling, and must be allowed to cut.
  * it governs PARTIAL scaling only. An act that takes the position to flat is an EXIT — stops, channel
    exits, WT-cross-against, REDUCE_TO_FLAT, HLR sell-the-top — and is NEVER blocked here
    (BACKTEST_BIBLE §15, memory exits_never_blocked_20261014).

Default OFF (`SCALE_DIRECTION_LAW_ENABLED = False`) so the engine is byte-identical until a board
measures it: USER 2026-10-17 "NEVER SHOULD BE ENABLED OR DISABLED IT IS A SWITCH THAT FLIPS ACCORDING
TO v15_pilot.py TEST RESULTS" — the mechanism is built and wired here, the pilot decides per sym_side.
"""
from __future__ import annotations


def params(config):
    """(enabled, lookback_bars, band_pct, block_reduce, block_augment) with the law's own defaults."""
    enabled = bool(getattr(config, "SCALE_DIRECTION_LAW_ENABLED", False))
    try:
        lookback = int(float(getattr(config, "SCALE_DIRECTION_LOOKBACK_BARS", 4) or 4))
    except (TypeError, ValueError):
        lookback = 4
    try:
        band = float(getattr(config, "SCALE_DIRECTION_BAND_PCT", 0.0) or 0.0)
    except (TypeError, ValueError):
        band = 0.0
    block_reduce = bool(getattr(config, "SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE", True))
    block_augment = bool(getattr(config, "SCALE_DIRECTION_BLOCK_AUGMENT_ON_ADVERSE", True))
    return enabled, max(1, lookback), abs(band), block_reduce, block_augment


def move_pct(is_long: bool, px: float, ref_px: float) -> float:
    """Signed move over the lookback, in the position's favour: > 0 favourable, < 0 adverse."""
    if not (px > 0 and ref_px > 0):
        return 0.0
    raw = (px - ref_px) / ref_px * 100.0
    return raw if is_long else -raw


def scale_allowed(config, kind: str, is_long: bool, px: float, ref_px: float, to_flat: bool = False):
    """(allowed, reason) for one PARTIAL scaling act. `kind` in {'REDUCE', 'AUGMENT'}.

    `ref_px` = close `SCALE_DIRECTION_LOOKBACK_BARS` bars back (the caller owns the array).
    `to_flat=True` marks an act that closes the position — an exit, always allowed.
    Fails OPEN: anything unresolvable returns (True, '') so the law can only ever refuse a scaling
    act it positively identified as pointing the wrong way.
    """
    enabled, _lookback, band, block_reduce, block_augment = params(config)
    if not enabled or to_flat:
        return True, ""
    mv = move_pct(is_long, px, ref_px)
    k = str(kind or "").upper()
    if k == "REDUCE":
        if block_reduce and mv > band:
            return False, f"SCALE_DIRECTION: price +{mv:.2f}% in favour — augment territory, partial reduce refused"
        return True, ""
    if k == "AUGMENT":
        if block_augment and mv < -band:
            return False, f"SCALE_DIRECTION: price {mv:.2f}% against — reduce territory, partial augment refused"
        return True, ""
    return True, ""


def ref_index(i: int, lookback: int) -> int:
    """Bar index the move is measured from, clamped to the start of the series."""
    return max(0, int(i) - max(1, int(lookback)))
