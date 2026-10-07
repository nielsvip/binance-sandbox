"""vec_paths/gr_v5_state.py — GR v5 Breakout-confirm → Bounce-entry state machine (SKELETON).

STATUS: 2026-05-18 — SKELETON ONLY. Full per-(symbol, side) state arrays + arm
tracking land in next session. Until then `evaluate_gr_v5_vec()` returns zero
fire masks regardless of inputs so toggling `GR_V5_ENABLED=True` is provably
inert across all backtest paths.

DESIGN DOC: data/research_20260518/gr_v5_breakout_bounce_design.md
SCALAR REF: golden_rule_v5.py (to be authored in followup session, mirrors this
            module 1:1 for backtest_v8_engine parity validation).

STATE MACHINE (per (symbol, side)):
    IDLE → ARMED     each bar, if ≥ GR_V5_HTF_MIN_ALIGN of GR_V5_HTF_TFS:
                       close > dc_high4_<TF>_prev (LONG; mirror for SHORT)
                       AND wt1_<TF> > wt2_<TF>
                       AND (NOT GR_V5_BREAKOUT_REQUIRE_VOLUME OR
                            relative_volume_<TF> >= GR_V5_BREAKOUT_VOL_MULT)
                     → record armed_at_bar, armed_at_price, breakout_tfs

    ARMED → FIRE     within GR_V5_ARM_WINDOW_BARS, if ≥ GR_V5_LTF_MIN_ALIGN of GR_V5_LTF_TFS:
                       stoch_k_<TF> < GR_V5_BOUNCE_STOCH_LONG (LONG)
                       AND wt1_<TF> > wt2_<TF>
                       AND wt1_<TF>_prev <= wt2_<TF>_prev  (FRESH cross)
                       AND |close - armed_at_price| / armed_at_price < GR_V5_RETEST_BAND_PCT
                     → emit fire bit; reset state to IDLE

    ARMED → DISARM   close < armed_at_price * (1 - GR_V5_INVALIDATE_PCT) (LONG)
                     OR (current_bar - armed_at_bar) > GR_V5_ARM_WINDOW_BARS
                     → reset state to IDLE

NPZ AVAILABILITY (2026-05-18, BTCUSDC.npz / tradier NPZ schema):
    HTF break TFs {4h, D, W}     — fully available both modes
    crypto LTF bounce TFs        — {3m, 15m, 1h}  (no 1m, no 5m-crypto)
    tradier LTF bounce TFs       — {5m, 15m, 1h}  (no 1m, no 3m-tradier)

INTEGRATION:
    Caller (v8_vec_sweep / vec_engine_v1) hands `features` dict with per-TF
    arrays keyed `wt1_<tf>`, `wt2_<tf>`, `stoch_k_<tf>`, `dc_high4_<tf>`,
    `dc_low4_<tf>`, `relative_volume_<tf>`, plus `close`. Mode chooses which
    LTF list to use.

REFUSAL:
    `GR_V5_ENABLED=False` → return zero masks. Same when knob missing.
    This keeps the queue-able sweep arms inert until the body lands.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

# Module identity for sweep coordinator dedup / md5 tracking.
GR_V5_MODULE_VERSION = "0.1.0-skeleton"


def _zero_masks(n: int) -> Tuple[np.ndarray, np.ndarray]:
    """Return (long_fire, short_fire) all-False masks of length n."""
    return (np.zeros(n, dtype=bool), np.zeros(n, dtype=bool))


def evaluate_gr_v5_vec(
    features: Dict[str, Any],
    config: Any,
    mode: str = "crypto",
) -> Tuple[np.ndarray, np.ndarray]:
    """Per-bar GR v5 fire masks.

    Args:
        features: dict of per-TF arrays. Must include 'close' (shape (N,)).
                  Per-TF keys: 'wt1_<tf>', 'wt2_<tf>', 'stoch_k_<tf>',
                               'dc_high4_<tf>', 'dc_low4_<tf>',
                               'relative_volume_<tf>'.
        config:   loaded config object (dataclass or module) with GR_V5_*.
        mode:     'crypto' or 'tradier'. Selects default LTF tuple if config
                  doesn't override.

    Returns:
        (long_fire, short_fire) — np.ndarray[bool] shape (N,). Skeleton always
        returns zeros so the knob is inert until full impl lands.
    """
    close = features.get("close") if isinstance(features, dict) else None
    if close is None:
        return _zero_masks(0)
    close = np.asarray(close)
    n = close.shape[0] if close.ndim else 0

    enabled = bool(getattr(config, "GR_V5_ENABLED", False))
    if not enabled:
        return _zero_masks(n)

    # ----- SKELETON STOP -----
    # The body below is INTENTIONALLY a no-op. Full implementation lands in
    # the followup session and must include:
    #
    # TODO(gr_v5_full_impl):
    #   1. Per-symbol state arrays
    #        phase           : np.int8[N]  # 0=IDLE, 1=ARMED, 2=DISARMED
    #        armed_at_bar    : np.int64[N]
    #        armed_at_price  : np.float64[N]
    #        armed_dir       : np.int8[N]  # 0=none, +1=long, -1=short
    #      Initialise to IDLE; carry forward bar-to-bar.
    #
    #   2. Forward-only loop (no look-ahead!). Vec-style: build per-TF
    #      per-bar boolean masks for breakout + bounce conditions, then run
    #      a single numba @njit pass to advance the state machine. Engine
    #      determinism requires the loop be deterministic — no asyncio, no
    #      hash-set iteration.
    #
    #   3. HTF arm tracking — per bar, count how many of GR_V5_HTF_TFS pass
    #      breakout. Compare to GR_V5_HTF_MIN_ALIGN. When phase==IDLE and
    #      htf_align_count >= min: transition to ARMED, snapshot bar+price.
    #
    #   4. LTF bounce check — per bar, when phase==ARMED, count how many of
    #      GR_V5_LTF_TFS pass bounce. Compare to GR_V5_LTF_MIN_ALIGN. Also
    #      gate on |close - armed_at_price|/armed_at_price < GR_V5_RETEST_BAND_PCT.
    #      On fire: set long_fire[i] or short_fire[i]; reset phase to IDLE
    #      (allow re-arm same bar? NO — next bar earliest).
    #
    #   5. DISARM — every bar phase==ARMED check:
    #        long: close < armed_at_price * (1 - GR_V5_INVALIDATE_PCT)
    #        short: close > armed_at_price * (1 + GR_V5_INVALIDATE_PCT)
    #        OR (i - armed_at_bar) > GR_V5_ARM_WINDOW_BARS
    #      → phase = IDLE (reset armed_at_*).
    #
    #   6. Mode-aware LTF default
    #        ltf_tfs = getattr(config, 'GR_V5_LTF_TFS', None)
    #        if ltf_tfs is None:
    #            ltf_tfs = ('5m', '15m', '1h') if mode == 'tradier' else ('3m', '15m', '1h')
    #
    #   7. Fresh-cross detection — wt1_<tf>[i] > wt2_<tf>[i] AND
    #      wt1_<tf>[i-1] <= wt2_<tf>[i-1]. Use np.roll/prepend NaN for i==0
    #      so first bar is never "fresh".
    #
    #   8. Volume gate — relative_volume_<tf>[i] >= GR_V5_BREAKOUT_VOL_MULT
    #      when GR_V5_BREAKOUT_REQUIRE_VOLUME=True. Missing rel_vol field
    #      should fail-closed (no breakout) for crypto/tradier consistency.
    #
    #   9. Parity test harness in tools/test_gr_v5_parity.py:
    #        scalar reference golden_rule_v5.py vs evaluate_gr_v5_vec on
    #        synthetic + replay datasets. Target 0 diffs over 10k bars.
    #
    # Until #1-#9 land, this skeleton returns zero masks unconditionally.
    return _zero_masks(n)
