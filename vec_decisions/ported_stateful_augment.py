# -*- coding: utf-8 -*-
"""ported_stateful_augment — vectorized INDICATOR masks + gain/age/count GATE specs for STATEFUL AUGMENT/REDUCE switches whose live
decision depends on position gain/age/count (not a pure per-bar indicator). The parent wires the gate
into simulate_one's bar-walk (where live_pnl_pct, held_bars, peak_pnl_pct, augment_count exist). Rules:
IDENTICAL to ez_manage (15m floor, NO proxies, NO fabrication — BIBLE §19). Each entry returns (mask, gate) where:
  mask  = np.ndarray[bool] of the gain-INDEPENDENT indicator condition (per bar). Pure gain/count gates
          (no indicator) => mask = np.ones(n, bool).
  gate  = dict describing the position-state condition the walk must apply, using ONLY these keys:
          {'gain_op':'>=','gain_thr':X} / {'gain_op':'<','gain_thr':0.0} (loss),
          {'age_min_bars':N} / {'age_min_minutes':N},
          {'augment_count_max':K}  (augment permitted only while augment_count < K),
          combined (all must hold). Values match LIVE thresholds exactly.
An empty return (switch inactive / not differing from effective default) = the parent skips it (no per-bar cost).

WORKLIST STATUS — determined by reading each live decision site 1:1 (ez_manage.py):

  WIRED (1):
    MAX_AUGMENTS_PER_POSITION — ez_manage.py:24150-24157. Real cap:
        `_aug_count = position.augmented_count; _max = MAX_AUGMENTS_PER_POSITION;
         if is_augment and not reentry and _aug_count >= _max: BLOCK`.
        Pure count gate, no indicator => mask = ones(n), gate = {'augment_count_max': K}.
        Effective default 999999 (no cap, config.py:145 / config_tradier.py:3500) => emitted ONLY when a
        real finite cap is set (0 <= K < 999999). config_tradier.py:3500 states this is already wired in
        backtest_v12_engine as `augmented_count >= max, REENTRY exempt`, i.e. the scalar walk tracks
        augmented_count. CAVEAT the parent must confirm: (a) the walk exposes per-position augment_count to
        the gate; (b) REENTRY adds are exempt (live gates augments only, not reentry rebuilds). If the walk
        does NOT track augment_count, this switch is VEC_UNSUPPORTED — parent to confirm.

  VEC_UNSUPPORTED (rest) — each needs per-position state the allowed gate keys can NOT express, or the only
  live site is a dead stub / generic templated proxy (NOT the switch's true semantics). NEVER proxied.
  See module-level VEC_UNSUPPORTED for the per-switch reason.
"""
import numpy as np


# Sentinel above which MAX_AUGMENTS_PER_POSITION means "no cap" (effective default, config.py:145).
_NO_CAP = 999999


def masks(npz, n, is_long, cfg, _safe, close):
    """Return {SWITCH_NAME: (mask_ndarray_bool, gate_dict)} for every stateful augment/reduce switch that
    is ACTIVE (cfg value differs from its effective default) AND genuinely ported from a real ez_manage
    decision site. Impossible ones are documented in module-level VEC_UNSUPPORTED."""
    out = {}

    # === MAX_AUGMENTS_PER_POSITION — ez_manage.py:24150-24157 (real count cap) ===
    # Live: block AUGMENT (not REENTRY) once position.augmented_count >= MAX_AUGMENTS_PER_POSITION.
    # No indicator => mask all-True; the gate carries the count condition. Effective default 999999 = no cap,
    # so only emit when a real finite cap is configured.
    _mx_raw = getattr(cfg, "MAX_AUGMENTS_PER_POSITION", None)
    if _mx_raw is not None:
        try:
            _mx = int(float(_mx_raw))
        except (TypeError, ValueError):
            _mx = None
        if _mx is not None and 0 <= _mx < _NO_CAP:
            out["MAX_AUGMENTS_PER_POSITION"] = (np.ones(n, dtype=bool), {"augment_count_max": _mx})

    return out


# Switches whose live decision cannot be faithfully expressed as (per-bar indicator mask + allowed gate).
# Each reason names the real ez_manage site and why it is not vectorizable. NEVER proxy any of these.
VEC_UNSUPPORTED = {
    "AUGMENT_MIN_GAIN_PCT": (
        "ez_manage.py:31592-31629 (UNIVERSAL_AUGMENT_GAIN_GATE). Blocks augment when "
        "gain_since_last_add = (cur_px - last_augmentation_price)/last_augmentation_price*100 < min_gain. "
        "The reference is per-position last_augmentation_price (walk's live_pnl_pct is gain-from-avg-entry, "
        "NOT since-last-add) AND there is a peak-based pullback bypass "
        "(max_gain>=min_gain and raw_gain>=0.5*min_gain and (max_gain-raw_gain)>=PULLBACK_AUGMENT_REVERSAL_MIN). "
        "A plain gain>=thr-from-entry gate would over-block the augments live allows via pullback = wrong "
        "ledger. Not expressible with the allowed keys. (Sites 58581 / 59379 are dead `_=1` stubs.)"
    ),
    "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": (
        "ez_manage.py:31579-31582 — the enable flag for the AUGMENT_MIN_GAIN_PCT gate above; same "
        "last_augmentation_price reference + peak pullback bypass. Not expressible with the allowed keys."
    ),
    "AUGMENTED_POSITIONS_GUARD_FLOOR_MULT": (
        "ez_manage.py:27315-27325. `_gain_ok = position.gain >= mult*MIN_GAIN` (gain-from-entry, expressible) "
        "BUT the block applies ONLY when position_key already in self.augmented_positions, i.e. augment_count>=1. "
        "Allowed keys have augment_count_MAX (upper bound), not a >=1 lower bound, so applying the gain gate to "
        "every augment would over-block the FIRST add that live permits. thr = FLOOR_MULT * MIN_GAIN. "
        "(Site 6618 is the generic close<=thr batch1 proxy; 59460 a `_=1` stub.)"
    ),
    "AUGMENT_AT_LOSS_ENABLED": (
        "ez_manage.py — mode flag that RELAXES the augment gain gate to permit adding while at a loss; it "
        "modifies another gate rather than firing on a per-bar indicator. Its only concrete site (6624-6628) is "
        "the generic close-vs-sma_200_1h batch1 entry proxy shared verbatim with BAND_ARROW_ENABLED etc. — NOT "
        "its true semantics. Site 58472 is a `_=1` stub. No faithful standalone predicate."
    ),
    "AUGMENT_WT_4H_BOUNCE_ENABLED": (
        "ez_manage.py — no real live predicate exists. Only sites are the generic close-vs-sma_200_1h batch1 "
        "proxy (6635-6639, identical to AUGMENT_AT_LOSS/BAND_ARROW) and a `_=1` stub (58473). A wt_4h bounce "
        "augment trigger is not implemented live; nothing to port."
    ),
    "AUGMENT_BOUNCE_MIN_GAIN_PCT": (
        "ez_manage.py:58557-58560 — only site reads position.gain into `_a` and discards it (`_=_a` no-op). "
        "Gain-gated bounce-augment threshold with no real fire and no indicator; nothing to port."
    ),
    "AUGMENT_BREAKOUT_MIN_GAIN_PCT": (
        "ez_manage.py:58561-58564 — only site reads position.gain into `_a` and discards it (`_=_a` no-op). "
        "Gain-gated breakout-augment threshold with no real fire and no indicator; nothing to port."
    ),
    "AUGMENT_FALLBACK_GAIN_PCT": (
        "ez_manage.py:58565-58572 — reads position.unrealizedProfit; `if _aug_gain < -_aug_fallback: _ = 1` "
        "(loss gate) but the result is discarded (no-op stub). No real augment/reduce fires; nothing to port."
    ),
    "AUGMENT_FALLBACK_REDUCE_ENABLED": (
        "ez_manage.py:58573-58576 — reads position.gain into `_aug` and discards it (no-op stub). "
        "Gain-gated with no real fire and no indicator; nothing to port."
    ),
    "AUGMENT_FALLBACK_REDUCE_PCT": (
        "ez_manage.py:58577-58580 — reads position.gain into `_aug` and discards it (no-op stub). "
        "A reduce-size percent gated on gain; no boolean fire and no indicator; nothing to port."
    ),
    "DELTA_PYRAMID_MAX": (
        "ez_manage.py — pyramid add-COUNT limit for the delta engine. Only concrete site (6914-6918) is the "
        "generic close<=thr batch1 proxy (a count compared to price = nonsense); 59477 is a `_=1` stub. The "
        "delta-pyramid add path is not modeled in the vec walk, and a count cap here is a distinct pyramid "
        "counter, not position.augmented_count. Nothing faithful to port."
    ),
    "DELTA_PYRAMID_PRICE_TOL": (
        "ez_manage.py — pyramid price-distance-from-last-add tolerance (needs per-position last_augmentation_price). "
        "Only site (6920-6924) is the generic close<=thr batch1 proxy; 59478 a `_=1` stub. Price-distance state is "
        "not available to the walk and is not a per-bar indicator; nothing to port."
    ),
    "BANDAID_OFF_LOSER_RECOVER_PCT": (
        "ez_manage.py:49056-49058 — cross-position HEDGE state: `origin_gain < BANDAID_OFF_LOSER_RECOVER_PCT`, "
        "where origin is the SEPARATE hedged origin position (plus a wt_3m flip). Not a gate on the current "
        "position's own gain/age/count; references another position's state the single-position walk has no "
        "access to. (Site 6640 is the generic close<=thr batch1 proxy; 59462 a `_=1` stub.)"
    ),
    "PARTIAL_EXIT_FRAC": (
        "ez_manage.py:58938-58945 — a reduce SIZE fraction (how much to trim), gated on "
        "position.unrealizedProfit != 0, in a no-op `_=1` stub. Not a boolean fire mask; a sizing param the "
        "split cannot represent. (tradier_manage.py:33354 is a dead `_=` stub.)"
    ),
    "WT_REDUCE_FRAC_HIGH": (
        "ez_manage.py:59232-59239 — a reduce SIZE fraction, and its only site misuses the fraction as a WT "
        "threshold (`abs(wt1_1h) > frac`) then discards the result (`_=1` no-op). It is a reduce-size param, "
        "not a boolean fire. (tradier_manage.py:33569 is a dead `_=` stub.)"
    ),
    "QUICK_REDUCE_TECHNICAL_ONLY": (
        "ez_manage.py:58964-58965 / tradier_manage.py:33362 — dead `_=` stubs. A MODE flag that changes HOW "
        "other reduces behave (technical-only vs gain-based) with no standalone per-bar predicate; nothing to port."
    ),
    "INTRADAY_RATIO_MAX_TRIMS_PER_DAY": (
        "tradier_manage.py:23675 (ez ABSENT) — gates on per-DAY trim COUNT (_trim_counts[today] >= max), a "
        "session-level counter, not per-position augment_count. Not a per-bar predicate; nothing to port."
    ),
    "INTRADAY_RATIO_TRIM_FRAC": (
        "tradier_manage.py:23705 (ez ABSENT) — the trim FRACTION inside the intraday L/S-ratio rebalance "
        "(portfolio/account-ratio state). A sizing param, not a boolean fire; nothing to port."
    ),
    "DYNAMIC_SCORE_AUGMENT_ENABLED": (
        "ez_manage.py:6543 / 58529 — read-only `_ = getattr(...)`; no live logic (dead). Nothing to port."
    ),
    "HTF_GATE_APPLY_TO_AUGMENT": (
        "ez_manage.py:59560 — an application-SCOPE flag (whether the HTF entry gate also applies to augments), "
        "not a gain/count predicate of its own; the HTF gate itself belongs to the entry-gate family, not this "
        "stateful augment/reduce split. Site is a dead `_=_htf` stub. Nothing to port here."
    ),
}
