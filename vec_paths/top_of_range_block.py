"""
vec_paths/top_of_range_block.py — TOP_OF_RANGE entry block.

USER MANDATE 2026-05-21 (post-ORDIUSDC incident):
    "STUPIDITIES like the ORDI entry AT THE VERY FUCKING TOP of all timeframes
    can NEVER happen."

    ORDI was bought via GOLDEN_RULE at price=4.36393 / dc_high_1h=4.366 — i.e.
    AT the 1h channel high. Then the price wicked 14% (4.4 → 3.8) and the
    position bled. The lesson: stop buying at extreme range positions.

LOGIC:
    For LONG OPEN/AUGMENT: block if dc_position is in the top N% on ALL listed TFs.
    For SHORT OPEN/AUGMENT: block if dc_position is in the bottom N% on ALL TFs.

    dc_position(TF) = (price - dc_low_TF) / (dc_high_TF - dc_low_TF), clipped [0,1].

    REQUIRE_ALL=True: ALL listed TFs must agree before blocking — stricter.
    REQUIRE_ALL=False: ANY listed TF in extreme → block — laxer (more blocks).

CONFIG KEYS:
    TOP_OF_RANGE_BLOCK_ENABLED: bool = False  # default-OFF until A/B proves positive
    TOP_OF_RANGE_BLOCK_THRESHOLD: float = 0.95  # top 5% / bottom 5%
    TOP_OF_RANGE_BLOCK_TF_LIST: str = "1h,4h,D"
    TOP_OF_RANGE_BLOCK_REQUIRE_ALL: bool = True

RETURNS:
    build_top_of_range_block_masks(store, n_bars, cfg) -> (block_long, block_short)
        two bool np.ndarray of length n_bars.
        block_long[i] = True if LONG entry at bar i should be blocked.
        block_short[i] = True if SHORT entry at bar i should be blocked.

If the feature is disabled or TF list empty or all NPZ fields missing,
both masks return all-False (no blocks).
"""
from __future__ import annotations

from typing import Any, Tuple

import numpy as np


def _resolve_tfs(cfg: Any) -> list:
    tf_str = getattr(cfg, "TOP_OF_RANGE_BLOCK_TF_LIST", "1h,4h,D")
    if isinstance(tf_str, (list, tuple)):
        return [str(t).strip() for t in tf_str if str(t).strip()]
    return [t.strip() for t in str(tf_str).split(",") if t.strip()]


def build_top_of_range_block_masks(
    store: Any,
    n_bars: int,
    cfg: Any,
) -> Tuple[np.ndarray, np.ndarray]:
    """Precompute per-bar TOP_OF_RANGE block masks (called once per symbol).

    Args:
        store:   _NPZStore (supports .f(key, idx) per-bar reads OR .arrays dict)
        n_bars:  number of bars
        cfg:     VecConfig

    Returns:
        (block_long, block_short) — bool arrays length n_bars.
    """
    block_long = np.zeros(n_bars, dtype=bool)
    block_short = np.zeros(n_bars, dtype=bool)

    if not bool(getattr(cfg, "TOP_OF_RANGE_BLOCK_ENABLED", False)):
        return block_long, block_short

    tfs = _resolve_tfs(cfg)
    if not tfs:
        return block_long, block_short

    threshold = float(getattr(cfg, "TOP_OF_RANGE_BLOCK_THRESHOLD", 0.95))
    require_all = bool(getattr(cfg, "TOP_OF_RANGE_BLOCK_REQUIRE_ALL", True))

    # Fetch price + per-TF dc_low/dc_high arrays.
    # NPZ price field is "close" (OHLCV convention).
    price = None
    try:
        if hasattr(store, "prices") and store.prices is not None:
            price = np.asarray(store.prices, dtype=np.float64)
        elif hasattr(store, "arrays"):
            for k in ("close", "price", "mark_price"):
                v = store.arrays.get(k)
                if v is not None:
                    price = np.asarray(v, dtype=np.float64)
                    break
    except Exception:
        price = None
    if price is None or price.size == 0:
        return block_long, block_short

    long_hit = np.zeros(n_bars, dtype=bool) if not require_all else np.ones(n_bars, dtype=bool)
    short_hit = np.zeros(n_bars, dtype=bool) if not require_all else np.ones(n_bars, dtype=bool)
    # 2026-05-22 USER MANDATE: "BREAKOUTS GET RESPECTED but CLOSE AT ENTRY PRICE
    # as they most likely fall back". Detect breakout = raw_dc_pos > 1.0 (LONG) or
    # < 0.0 (SHORT) on ANY listed TF — price has broken the prior channel range.
    # If breakout on any TF, the top-of-range block is SKIPPED (entry allowed).
    breakout_long_any = np.zeros(n_bars, dtype=bool)
    breakout_short_any = np.zeros(n_bars, dtype=bool)
    any_valid_tf = False

    # 2026-05-22 BUG FIX: Donchian channels auto-extend the bar price breaks above
    # them, so dc_high == close on the breakout bar (raw_dc_pos==1.0, not > 1.0).
    # Use dc_high_prev / dc_low_prev (previous bar's channel) to detect breakouts
    # properly. With current-bar channel, breakout rate was 0.19% of bars (only
    # instant of crossover). With _prev, 3-4% — captures sustained breakouts.
    for tf in tfs:
        try:
            if hasattr(store, "arrays"):
                dc_low = store.arrays.get(f"dc_low_{tf}")
                dc_high = store.arrays.get(f"dc_high_{tf}")
                dc_low_prev = store.arrays.get(f"dc_low_{tf}_prev")
                dc_high_prev = store.arrays.get(f"dc_high_{tf}_prev")
            else:
                dc_low = None
                dc_high = None
                dc_low_prev = None
                dc_high_prev = None
            if dc_low is None or dc_high is None:
                dc_low = np.array([store.f(f"dc_low_{tf}", i, 0.0) for i in range(n_bars)], dtype=np.float64)
                dc_high = np.array([store.f(f"dc_high_{tf}", i, 0.0) for i in range(n_bars)], dtype=np.float64)
            dc_low = np.asarray(dc_low, dtype=np.float64)
            dc_high = np.asarray(dc_high, dtype=np.float64)
            if dc_high_prev is None:
                dc_high_prev = dc_high  # fallback (no breakout detection)
            if dc_low_prev is None:
                dc_low_prev = dc_low
            dc_high_prev = np.asarray(dc_high_prev, dtype=np.float64)
            dc_low_prev = np.asarray(dc_low_prev, dtype=np.float64)
        except Exception:
            continue
        if dc_low.size != n_bars or dc_high.size != n_bars:
            continue
        rng = dc_high - dc_low
        valid = (rng > 1e-9) & (price > 0)
        if not valid.any():
            continue
        any_valid_tf = True
        # Top-of-range detection uses CURRENT-bar channel (price near top).
        with np.errstate(divide="ignore", invalid="ignore"):
            raw_dc_pos = np.where(valid, (price - dc_low) / np.where(rng > 0, rng, 1.0), 0.5)
        # Breakout uses PREVIOUS-bar channel — price > prev_high == fresh breakout
        # (current dc_high already auto-extended to match price).
        breakout_long_any |= (price > dc_high_prev) & valid & (dc_high_prev > 0)
        breakout_short_any |= (price < dc_low_prev) & valid & (dc_low_prev > 0)

        dc_pos = np.clip(raw_dc_pos, 0.0, 1.0)
        tf_long_extreme = (dc_pos >= threshold) & valid
        tf_short_extreme = (dc_pos <= (1.0 - threshold)) & valid

        if require_all:
            long_hit &= tf_long_extreme
            short_hit &= tf_short_extreme
        else:
            long_hit |= tf_long_extreme
            short_hit |= tf_short_extreme

    if any_valid_tf:
        # Block only when at the edge AND no breakout on any TF.
        block_long = long_hit & ~breakout_long_any
        block_short = short_hit & ~breakout_short_any

    # Stash breakout masks on the cfg so the caller can read them when tagging
    # entries (surgical NLK). Backwards-compatible — existing callers ignore.
    try:
        cfg._tor_breakout_long_any = breakout_long_any
        cfg._tor_breakout_short_any = breakout_short_any
    except Exception:
        pass

    return block_long, block_short


def build_breakout_masks(
    store: Any,
    n_bars: int,
    cfg: Any,
) -> Tuple[np.ndarray, np.ndarray]:
    """Standalone helper to compute breakout-any masks (LONG/SHORT). Reuses the
    same logic as build_top_of_range_block_masks but always returns masks
    regardless of TOP_OF_RANGE_BLOCK_ENABLED.

    Returns:
        (breakout_long_any, breakout_short_any) — bool arrays length n_bars.
        True at bar i if raw_dc_pos > 1.0 (LONG) or < 0.0 (SHORT) on any listed TF.
    """
    breakout_long = np.zeros(n_bars, dtype=bool)
    breakout_short = np.zeros(n_bars, dtype=bool)
    tfs = _resolve_tfs(cfg)
    if not tfs:
        return breakout_long, breakout_short

    price = None
    try:
        if hasattr(store, "prices") and store.prices is not None:
            price = np.asarray(store.prices, dtype=np.float64)
        elif hasattr(store, "arrays"):
            for k in ("close", "price", "mark_price"):
                v = store.arrays.get(k)
                if v is not None:
                    price = np.asarray(v, dtype=np.float64)
                    break
    except Exception:
        price = None
    if price is None or price.size == 0:
        return breakout_long, breakout_short

    # Use _prev channels: breakout = close > prev-bar channel high (LONG) / < prev-bar channel low (SHORT).
    # Current dc_high auto-extends to match price on the breakout bar, so price > dc_high is too strict.
    for tf in tfs:
        try:
            if hasattr(store, "arrays"):
                dc_high_prev = store.arrays.get(f"dc_high_{tf}_prev")
                dc_low_prev = store.arrays.get(f"dc_low_{tf}_prev")
            else:
                dc_high_prev = None
                dc_low_prev = None
            if dc_high_prev is None and dc_low_prev is None:
                continue
            if dc_high_prev is not None:
                dc_high_prev = np.asarray(dc_high_prev, dtype=np.float64)
            if dc_low_prev is not None:
                dc_low_prev = np.asarray(dc_low_prev, dtype=np.float64)
        except Exception:
            continue
        valid = price > 0
        if dc_high_prev is not None and dc_high_prev.size == n_bars:
            breakout_long |= (price > dc_high_prev) & valid & (dc_high_prev > 0)
        if dc_low_prev is not None and dc_low_prev.size == n_bars:
            breakout_short |= (price < dc_low_prev) & valid & (dc_low_prev > 0)

    return breakout_long, breakout_short
