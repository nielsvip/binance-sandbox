# -*- coding: utf-8 -*-
"""ported_augment — faithful numpy twins of ez_manage/tradier_manage augment-lifecycle switches.
Owned by the augment wiring agent (SWITCH_WIRING_GUIDE.md). Rules: IDENTICAL to live (same thresholds,
operators, TF; 15m floor, 3m/5m->15m), NO proxies, NO fabrication (BIBLE §19). Each switch block is
gated on its cfg value differing from the effective default and applies a REAL mask to the signal.
apply() is a pure passthrough when no switch is active (returns sig unchanged).

sig is the AUGMENT signal. Add augments with `sig = sig | fire`; remove/block augments with
`sig = sig & ~block`.

WORKLIST STATUS (16 switches) — determined by reading each live decision site 1:1:
  WIRED (1):
    COUNTER_TREND_ADD_BLOCK_ENABLED — ez_manage.py:29866-29908, real indicator-only augment/open block
      (wt1_1h/wt2_1h vs sma_200_15m + 1h HH/LL structure). Faithful per-bar block.
  VEC_UNSUPPORTED (15) — require position gain/count/state not available per-bar, or the only live
  site is a dead stub / generic templated proxy (NOT the switch's true semantics). NEVER proxied:
    AUGMENTED_POSITIONS_GUARD_FLOOR_MULT — ez:27315-27325 gates on _pos_gain >= mult*MIN_GAIN (position gain).
    AUGMENT_AT_LOSS_ENABLED           — position gain/loss state; only concrete site (ez:6624) is the
                                         generic _batch1_template_live_gate entry proxy (close vs sma_200_1h,
                                         identical across unrelated switches) — not a real augment predicate.
    AUGMENT_WT_4H_BOUNCE_ENABLED      — same: only site (ez:6635) is that generic close-vs-sma entry proxy.
    AUGMENT_BOUNCE_MIN_GAIN_PCT       — ez:58557 reads position.gain (gain-gated stub).
    AUGMENT_BREAKOUT_MIN_GAIN_PCT     — ez:58561 reads position.gain (gain-gated stub).
    AUGMENT_FALLBACK_GAIN_PCT         — ez:58565 reads position.unrealizedProfit (gain-gated).
    AUGMENT_FALLBACK_REDUCE_ENABLED   — ez:58573 reads position.gain (gain-gated stub).
    AUGMENT_FALLBACK_REDUCE_PCT       — ez:58577 reads position.gain (gain-gated stub).
    AUGMENT_MIN_GAIN_PCT              — ez:58581 gain-gated `_=1` stub.
    DELTA_PYRAMID_MAX                 — pyramid add-count/entry-price state; only site (ez:6914) is the
                                         generic `close<=thr` batch1 proxy (nonsense vs a count) + BATCH2 stub.
    DELTA_PYRAMID_PRICE_TOL          — pyramid price-distance-from-entry state; same generic proxy (ez:6920).
    DYNAMIC_SCORE_AUGMENT_ENABLED     — ez:6543/58529 are `_=` reads only; no live logic (dead).
    HTF_GATE_APPLY_TO_AUGMENT         — ez:59560 dead `_=_htf` stub; an application-scope flag, no predicate.
    MAX_AUGMENTS_PER_POSITION         — ez:24150-24157 gates on position.augmented_count (position state).
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED — ez:59110 gain-gate `_=1` stub (position gain).
"""
import numpy as np


def apply(npz, n, is_long, cfg, sig, _safe, close):
    # === COUNTER_TREND_ADD_BLOCK_ENABLED — ez_manage.py:29866-29908 (real, indicator-only) ===
    # Live: blocks OPEN/AUGMENT/REENTRY when the side is against wt1_1h, UNLESS SMA200-bypass says the
    # position is trend-aligned (right side of sma_200_15m AND 1h HH/LL structure OR wt already agreeing).
    # CLOSE/REDUCE/HEDGE pass (not relevant here — this is the augment signal). Default True.
    if bool(getattr(cfg, "COUNTER_TREND_ADD_BLOCK_ENABLED", True)):
        _w1 = _safe(npz, "wt1_1h", n, 0.0)
        _w2 = _safe(npz, "wt2_1h", n, 0.0)
        _aligned = np.zeros(n, dtype=bool)
        if bool(getattr(cfg, "COUNTER_TREND_SMA200_BYPASS_ENABLED", True)):
            _sma200 = _safe(npz, "sma_200_15m", n, 0.0)
            _valid = (_sma200 > 0) & (close > 0)
            if is_long:
                _hh_n = _safe(npz, "high_1h", n, 0.0)
                _hh_p = _safe(npz, "high_1h_prev", n, 0.0)
                _struct = ((_hh_n > 0) & (_hh_p > 0) & (_hh_n > _hh_p)) | (_w1 > _w2)
                _aligned = _valid & (close > _sma200) & _struct
            else:
                _ll_n = _safe(npz, "low_1h", n, 0.0)
                _ll_p = _safe(npz, "low_1h_prev", n, 0.0)
                _struct = ((_ll_n > 0) & (_ll_p > 0) & (_ll_n < _ll_p)) | (_w1 < _w2)
                _aligned = _valid & (close < _sma200) & _struct
        _wt_present = (np.abs(_w1) > 1e-9) | (np.abs(_w2) > 1e-9)
        _counter = (_w1 < _w2) if is_long else (_w1 > _w2)
        _block = _wt_present & (~_aligned) & _counter
        sig = sig & ~_block
    return sig
