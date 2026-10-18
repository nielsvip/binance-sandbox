"""FIXED_STOP core — non-negotiable frozen-at-entry stop (USER 2026-10-12).

The stop is a simple fact, not a switch: NO config field, NO QuickConfig
field, NO template row, NO venue/mode gate. Every consumer (v12 vec,
ez_manage.process_position, ez_positions_service monitor+fetch, tradier
process_position, backtest_v12 via the real process_position) imports THIS
module so the math cannot drift between paths.

Levels:
  software (maker-first regular exit): LONG entry-bar dc_low_15m * 0.9975,
    SHORT entry-bar dc_high_15m * 1.0025. Frozen at first entry, never moved.
  exchange (STOP_MARKET taker backstop): beyond software, so the regular exit
    is tried first and taker fees are the last resort. Binance sits at
    CRYPTO_EXCHANGE_TOTAL_PCT (0.40%) from the frozen channel (USER
    2026-10-18, was 0.2999%); Tradier keeps EXCHANGE_EXTRA_PCT (0.05%) past
    software, i.e. 0.2999% from the channel.
Fallback when the entry-bar channel is missing/zero: entry price based.
Pure math only — no config/env imports (import-cycle safe).
"""
from __future__ import annotations
import math

REASON = "FIXED_STOP"
FIXED_STOP_BUFFER_PCT = 0.25
EXCHANGE_EXTRA_PCT = 0.05
CRYPTO_EXCHANGE_TOTAL_PCT = 0.40  # USER 2026-10-18: the Binance STOP_MARKET backstop sits 0.40% beyond the frozen entry-bar dc_15m (was software -0.25% then -0.05% = 0.2999%)
STOP_ORDER_PREFIX = "EZMSTOP_FX_"


def freeze_software_level(is_long: bool, dc_low_15m: float, dc_high_15m: float, entry_price: float) -> float:
    try:
        ch = float(dc_low_15m) if is_long else float(dc_high_15m)
    except (TypeError, ValueError):
        ch = 0.0
    try:
        ep = float(entry_price or 0.0)
    except (TypeError, ValueError):
        ep = 0.0
    base = ch if ch > 0 else (ep if ep > 0 else 0.0)
    if base <= 0:
        return 0.0
    return base * 0.9975 if is_long else base * 1.0025


def exchange_level(is_long: bool, soft_level: float) -> float:
    try:
        s = float(soft_level or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    return s * 0.9995 if is_long else s * 1.0005


def crypto_exchange_level(is_long: bool, soft_level: float) -> float:
    """Binance STOP_MARKET level: CRYPTO_EXCHANGE_TOTAL_PCT beyond the frozen entry-bar dc_15m (USER 2026-10-18).

    Derived from the software level rather than from the channel so the ONE frozen number still drives both legs:
    software is the channel * (1 -+ 0.25%), so dividing that out and applying the total gives exactly
    dc_low_15m * 0.9960 for a LONG and dc_high_15m * 1.0040 for a SHORT. Tradier keeps exchange_level().
    """
    try:
        s = float(soft_level or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    if is_long:
        return s * (1.0 - CRYPTO_EXCHANGE_TOTAL_PCT / 100.0) / (1.0 - FIXED_STOP_BUFFER_PCT / 100.0)
    return s * (1.0 + CRYPTO_EXCHANGE_TOTAL_PCT / 100.0) / (1.0 + FIXED_STOP_BUFFER_PCT / 100.0)


def needs_freeze(stored_level: float, stored_anchor: float, opened_at_epoch: float) -> bool:
    """True when the stored level does not belong to THIS lifecycle and must be frozen again.

    USER 2026-10-15 (COTI incident): the level was frozen once per position KEY and never cleared when the
    position closed, so a re-opened sym_side inherited the previous lifecycle's channel. ang/fin/men COTIUSDT_SHORT
    opened 13:09-13:59Z on 10-15 were still carrying the 10-13/10-14 dc_high_15m (0.01256-0.01272) while the real
    entry-bar channel was 0.011277 -> stop 0.011305. 38 of 41 crypto positions were stale, 27 of them already past
    their true stop and still open. A distance tolerance is NOT the fix (USER 2026-10-13: "NO 5% OR ANY% EXISTS") —
    the level is the entry-bar channel, so it is bound to the lifecycle that entered: a different opened_at is a
    different trade and gets its own freeze. Anchor 0 with a level set = frozen before anchors existed -> re-freeze once.
    """
    try:
        lv = float(stored_level or 0.0)
    except (TypeError, ValueError):
        return True
    if lv <= 0:
        return True
    try:
        oa = float(opened_at_epoch or 0.0)
        an = float(stored_anchor or 0.0)
    except (TypeError, ValueError):
        return True
    if oa <= 0:
        return False  # no lifecycle stamp to compare against: keep what is frozen, never drop protection
    return abs(an - oa) > 1.0


def breached(is_long: bool, price: float, soft_level: float) -> bool:
    try:
        px = float(price or 0.0)
        lv = float(soft_level or 0.0)
    except (TypeError, ValueError):
        return False
    if px <= 0 or lv <= 0:
        return False
    return px <= lv if is_long else px >= lv


def loosens(is_long: bool, old_level: float, new_level: float) -> bool:
    """True = new_level sits FURTHER from entry than old_level, i.e. the stop would give back protection.

    USER 2026-10-17 (men:ENJUSDT_LONG): "it is changing in the wrong direction during a trade ... can not move
    further away from entry price ever". Within ONE lifecycle the level is supposed to be frozen, but the
    self-healing re-freeze in ez_positions_service accepted whatever it recomputed, in either direction. When the
    entry-bar klines call timed out it substituted the CURRENT channel over the already-proven level, and the next
    successful rebuild snapped back to the entry bar — so the live stop wandered for as long as the endpoint was
    flaky. Measured in the 10-15..10-17 logs: 17 same-lifecycle re-freezes moved the stop further from entry
    (men:COTIUSDT_LONG -4.12%, men:GALAUSDT_SHORT +3.90%, men:ENJUSDT_LONG -3.85%, men:RVNUSDT_SHORT +3.63%),
    every large one the away-leg of such a flap pair. Tightening toward entry stays allowed; loosening does not.
    A genuine new lifecycle carries a different opened_at and is exempt — it is a different trade, not a drift.
    """
    try:
        o = float(old_level or 0.0)
        n = float(new_level or 0.0)
    except (TypeError, ValueError):
        return False
    if o <= 0 or n <= 0:
        return False
    return n < o if is_long else n > o


def armed_at_freeze(is_long: bool, price: float, soft_level: float) -> bool:
    """A stop arms only while price is still on the protected side of it (USER 2026-10-15).

    A stop is a CROSS, not a state. Firing merely because price is already beyond the level the first time we look at
    it turns every late or corrected freeze into an instant market exit:
      * longs, 2026-10-15 11:00-13:00Z: stale levels sat ABOVE price, so FIXED_STOP "breached" the moment it was
        evaluated — ang:RAYSOLUSDT px1.7776 lvl2.0262, men:ENSUSDT px5.4865 lvl5.8763, fin:ETHUSDC px2390 lvl2485 —
        a wave of instant stop-outs at g~0.00 that no cross ever justified;
      * shorts, same day: when the levels were corrected at 18:3xZ price had ALREADY run past them during the 4.5 h
        writer outage, so the corrected stop wanted to dump 13 positions at -3.5% to -6.9% for crosses that happened
        while nothing was watching. USER: "you cant just close these now without absolute reentry guarantee".
    So: armed while unbreached at freeze; a position frozen already-beyond its level waits, and arms the moment price
    returns to the protected side. Protection for every NEW cross is unchanged — at entry price is always on the
    protected side, so a real trade arms immediately.
    """
    return not breached(is_long, price, soft_level)


def round_to_tick(is_long: bool, level: float, tick: float) -> float:
    try:
        lv = float(level or 0.0)
        tk = float(tick or 0.0)
    except (TypeError, ValueError):
        return float(level or 0.0)
    if lv <= 0 or tk <= 0:
        return lv
    q = lv / tk
    steps = math.floor(q + 1e-9) if is_long else math.ceil(q - 1e-9)
    out = steps * tk
    return float(f"{out:.10f}")


def build_stop_params(is_long: bool, soft_level: float, tick: float) -> dict:
    lvl = crypto_exchange_level(is_long, soft_level)
    return {
        "side": "SELL" if is_long else "BUY",
        "stopPrice": round_to_tick(is_long, lvl, tick),
        "closePosition": True,
        "workingType": "MARK_PRICE",
    }
