"""FIXED_STOP core — non-negotiable frozen-at-entry stop (USER 2026-10-12).

The stop is a simple fact, not an on/off switch: it can never be disabled and
has NO venue/mode gate. USER 2026-10-20 lifted the "no config field" part for
the REFERENCE only ("fixed stop can get switches ... if the numbers prove
otherwise I go with the numbers"): FIXED_STOP_REF_FIELD / _REF_TF / _BUFFER_PCT
/ _TIGHTEN_MODE / _TIGHTEN_TRIGGER_PCT choose WHERE it is frozen (see the E7
block at the bottom); their defaults reproduce the level below exactly, and
every value still freezes a stop at entry. Every consumer (v12 vec,
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


# ── E7 2026-10-20 FIXED_STOP switch family (USER 2026-10-20: "fixed stop can get switches, the dc_basis/bb_basis/low are
# supposed to be earlier stops not later ones, but if the numbers prove otherwise I go with the numbers"; doc 17 §3.7).
# The 2026-10-12 "no config field" rule above is lifted for the REFERENCE only: the stop can never be switched off — every
# value of every field below still freezes a stop at entry. Defaults = today's level, byte for byte (freeze_software_level).
SPEC_FIELDS = ("FIXED_STOP_REF_FIELD", "FIXED_STOP_REF_TF", "FIXED_STOP_BUFFER_PCT", "FIXED_STOP_TIGHTEN_MODE", "FIXED_STOP_TIGHTEN_TRIGGER_PCT")
REF_FIELDS = ("dc_low", "dc_basis", "bb_low", "bb_basis")
REF_ALIASES = {"dc_high": "dc_low", "bb_high": "bb_low", "bb_lower": "bb_low", "bb_upper": "bb_low", "dc_mid": "dc_basis", "dc_middle": "dc_basis", "bb_mid": "bb_basis", "bb_middle": "bb_basis"}
REF_TFS = ("15m", "1h", "4h")
TIGHTEN_MODES = ("off", "breakeven_after_pct", "trail_ref")
DEFAULT_REF_FIELD = "dc_low"
DEFAULT_REF_TF = "15m"
DEFAULT_TIGHTEN_MODE = "off"
DEFAULT_TIGHTEN_TRIGGER_PCT = 1.0
DEFAULT_SPEC = (DEFAULT_REF_FIELD, DEFAULT_REF_TF, FIXED_STOP_BUFFER_PCT, DEFAULT_TIGHTEN_MODE, DEFAULT_TIGHTEN_TRIGGER_PCT)
_BUFFER_MIN, _BUFFER_MAX = 0.0, 5.0


def normalize_spec(ref_field=None, ref_tf=None, buffer_pct=None, tighten_mode=None, trigger_pct=None) -> tuple:
    """(field, tf, buffer_pct, mode, trigger_pct) with every unknown/invalid component replaced by today's default.

    Shorts are mirrored by the level math, so the vocabulary is side-neutral: dc_low means "the Donchian edge on the
    protected side" (dc_high_TF for a SHORT); the short spellings dc_high / bb_high / bb_upper are accepted aliases."""
    f = str(ref_field if ref_field is not None else DEFAULT_REF_FIELD).strip().lower()
    f = REF_ALIASES.get(f, f)
    if f not in REF_FIELDS:
        f = DEFAULT_REF_FIELD
    t = str(ref_tf if ref_tf is not None else DEFAULT_REF_TF).strip()
    t = {"15M": "15m", "1H": "1h", "4H": "4h", "60m": "1h", "240m": "4h"}.get(t, t)
    if t not in REF_TFS:
        t = DEFAULT_REF_TF
    try:
        b = float(buffer_pct if buffer_pct is not None else FIXED_STOP_BUFFER_PCT)
    except (TypeError, ValueError):
        b = FIXED_STOP_BUFFER_PCT
    if not (math.isfinite(b) and _BUFFER_MIN <= b <= _BUFFER_MAX):
        b = FIXED_STOP_BUFFER_PCT
    m = str(tighten_mode if tighten_mode is not None else DEFAULT_TIGHTEN_MODE).strip().lower()
    if m not in TIGHTEN_MODES:
        m = DEFAULT_TIGHTEN_MODE
    try:
        tr = float(trigger_pct if trigger_pct is not None else DEFAULT_TIGHTEN_TRIGGER_PCT)
    except (TypeError, ValueError):
        tr = DEFAULT_TIGHTEN_TRIGGER_PCT
    if not (math.isfinite(tr) and tr >= 0.0):
        tr = DEFAULT_TIGHTEN_TRIGGER_PCT
    return (f, t, b, m, tr)


def spec_from(get) -> tuple:
    """Resolve the spec through any getter(name, default) — QuickConfig getattr, a per-sym set, a config proxy."""
    vals = []
    for k, d in zip(SPEC_FIELDS, DEFAULT_SPEC):
        try:
            vals.append(get(k, d))
        except Exception:
            vals.append(d)
    return normalize_spec(*vals)


def is_default_reference(spec) -> bool:
    """True = the frozen level is today's dc_15m channel ∓0.25% (the tighten mode is judged separately)."""
    try:
        return spec[0] == DEFAULT_REF_FIELD and spec[1] == DEFAULT_REF_TF and float(spec[2]) == FIXED_STOP_BUFFER_PCT
    except Exception:
        return True


def is_default_spec(spec) -> bool:
    try:
        return is_default_reference(spec) and spec[3] == DEFAULT_TIGHTEN_MODE
    except Exception:
        return True


def ref_keys(is_long: bool, ref_field: str, ref_tf: str) -> tuple:
    """Indicator keys holding the reference, in lookup order (NPZ and live snapshots spell the BB basis differently:
    NPZ bb_middle_TF, crypto live bb_mid_TF, stocks live has neither -> (bb_upper_TF + bb_lower_TF) / 2, see ref_value)."""
    f = REF_ALIASES.get(str(ref_field), str(ref_field))
    if f == "dc_low":
        return (f"dc_low_{ref_tf}",) if is_long else (f"dc_high_{ref_tf}",)
    if f == "dc_basis":
        return (f"dc_basis_{ref_tf}",)
    if f == "bb_low":
        return (f"bb_lower_{ref_tf}",) if is_long else (f"bb_upper_{ref_tf}",)
    if f == "bb_basis":
        return (f"bb_middle_{ref_tf}", f"bb_mid_{ref_tf}", f"bb_basis_{ref_tf}")
    return ()


def _pos(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if (math.isfinite(x) and x > 0) else 0.0


def ref_value(is_long: bool, ref_field: str, ref_tf: str, get) -> tuple:
    """(value, key) of the reference from getter(key) -> number|None; (0.0, "") when the snapshot lacks it.
    dc_basis falls back to (dc_high + dc_low) / 2 and bb_basis to (bb_upper + bb_lower) / 2 (identical by construction)."""
    f = REF_ALIASES.get(str(ref_field), str(ref_field))
    for k in ref_keys(is_long, f, ref_tf):
        try:
            v = _pos(get(k))
        except Exception:
            v = 0.0
        if v > 0:
            return v, k
    pair = {"dc_basis": ("dc_high_", "dc_low_"), "bb_basis": ("bb_upper_", "bb_lower_")}.get(f)
    if pair:
        try:
            hi, lo = _pos(get(pair[0] + ref_tf)), _pos(get(pair[1] + ref_tf))
        except Exception:
            hi, lo = 0.0, 0.0
        if hi > 0 and lo > 0:
            return (hi + lo) / 2.0, f"({pair[0]}{ref_tf}+{pair[1]}{ref_tf})/2"
    return 0.0, ""


def level_from_ref(is_long: bool, ref: float, buffer_pct: float) -> float:
    r = _pos(ref)
    if r <= 0:
        return 0.0
    b = float(buffer_pct) / 100.0
    return r * (1.0 - b) if is_long else r * (1.0 + b)


def freeze_level_spec(is_long: bool, spec, get, entry_price: float) -> tuple:
    """(software_level, source) for the configured reference. Default reference -> freeze_software_level(dc_low_15m,
    dc_high_15m) unchanged (source "DEFAULT"). Otherwise the reference at its TF ∓buffer; it falls back to today's path
    (source "FALLBACK_MISSING:<field_tf>") when the snapshot lacks the reference, and (source "FALLBACK_WRONG_SIDE")
    when the referenced level is not on the protected side of entry — a LONG stop at/above entry (e.g. dc_basis_4h above
    a dip entry) would fire on the entry bar, which is not an earlier stop but no trade at all."""
    spec = normalize_spec(*spec) if spec is not None else DEFAULT_SPEC

    def _g(k):
        try:
            return get(k)
        except Exception:
            return None
    if is_default_reference(spec):
        return freeze_software_level(is_long, _pos(_g("dc_low_15m")), _pos(_g("dc_high_15m")), entry_price), "DEFAULT"
    v, key = ref_value(is_long, spec[0], spec[1], _g)
    if v <= 0:
        return freeze_software_level(is_long, _pos(_g("dc_low_15m")), _pos(_g("dc_high_15m")), entry_price), f"FALLBACK_MISSING:{spec[0]}_{spec[1]}"
    lvl = level_from_ref(is_long, v, spec[2])
    ep = _pos(entry_price)
    if ep > 0 and ((is_long and lvl >= ep) or ((not is_long) and lvl <= ep)):
        return freeze_software_level(is_long, _pos(_g("dc_low_15m")), _pos(_g("dc_high_15m")), entry_price), f"FALLBACK_WRONG_SIDE:{key}"
    return lvl, f"REF:{key}"


def tighten_candidate(is_long: bool, spec, entry_price: float, peak_gain_pct: float, ref_now: float, price_now: float = 0.0) -> float:
    """The level the tighten mode asks for on this bar, 0.0 = nothing to do. Never decides direction — see tightened().
    breakeven_after_pct: once the peak favourable move (unleveraged %, vec peak_pnl_pct / live max_gain) reached the
    trigger, the stop moves to the entry price. trail_ref: once the trigger is reached, the stop follows the configured
    reference recomputed on the current bar (∓buffer). A stop is a cross, not a state (armed_at_freeze): with price_now
    given, a candidate already on the wrong side of price is not adopted on this bar (it would be an instant retro exit)."""
    spec = normalize_spec(*spec) if spec is not None else DEFAULT_SPEC
    mode, trig = spec[3], spec[4]
    if mode == "off":
        return 0.0
    try:
        pk = float(peak_gain_pct or 0.0)
    except (TypeError, ValueError):
        pk = 0.0
    if not math.isfinite(pk) or pk < trig:
        return 0.0
    if mode == "breakeven_after_pct":
        cand = _pos(entry_price)
    elif mode == "trail_ref":
        cand = level_from_ref(is_long, ref_now, spec[2])
    else:
        return 0.0
    px = _pos(price_now)
    if cand > 0 and px > 0 and breached(is_long, px, cand):
        return 0.0
    return cand


def tightened(is_long: bool, current_level: float, candidate: float) -> float:
    """Ratchet: the candidate replaces the level only when it is TIGHTER (closer to / beyond entry on the profit side);
    a looser candidate never moves the stop (loosens() is the same rule the writer's self-heal enforces)."""
    c, cand = _pos(current_level), _pos(candidate)
    if cand <= 0:
        return c
    if c <= 0:
        return cand
    return max(c, cand) if is_long else min(c, cand)
