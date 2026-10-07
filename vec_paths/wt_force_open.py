"""
vec_paths/wt_force_open.py — WT_3M_FORCE_OPEN path (vec approximation).

LIVE SOURCE:
  Crypto: ez_manage.py:19969 (within TRADEABLE_KEYS_MANDATORY block, ZERO position check).
  Stocks: tradier_manage.py:1604-1624.

WHAT THE LIVE SYSTEM DOES:
  - For every tradeable_key with positionAmt == 0 (ZERO position):
    • LONG: if wt1_{btf} > wt2_{btf} → OPEN ~WT_3M_FORCE_OPEN_SIZE_USD
    • SHORT: if wt1_{btf} < wt2_{btf} → OPEN ~WT_3M_FORCE_OPEN_SIZE_USD
  - Bypasses HARD_AUGMENT_LOCK, DUP_GUARD cooldown, and most entry gates
    (WT_3M_FORCE_OPEN_BYPASS_GATES=True in execute_now).
  - Reason format: WT_3M_FORCE_OPEN_{LONG|SHORT}_wt1={val}_wt2={val}_px{price}
  - Default size_usd: WT_3M_FORCE_OPEN_SIZE_USD (crypto default 9.0, stocks default 100.0).

VEC APPROXIMATION LIMITATIONS:
  - Only fires when pos_state.open == False (ZERO position), matching live positionAmt==0.
  - The wt1/wt2 condition uses base TF (3m for crypto, 5m for stocks).
    NOTE: tradier NPZ has wt1_5m populated; tradier_manage uses wt1_3m (labeled 3m but
    may be the 5m in NPZ — we try both and fall back gracefully). The comment in
    tradier_manage.py:1613 uses 'wt1_3m' key from the indicator cache, but the NPZ
    for tradier stores wt1_5m as the base TF. We read wt1_3m first, then wt1_5m.
  - No is_symbol_tradeable() check (requires live account state).
  - No per-account cooldown after a prior FORCE_OPEN.

RETURNS:
  dict with keys: {side, size_usd, reason, qty} or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Optional, Dict


def check_wt_force_open(
    store,
    bar_idx: int,
    side: str,
    mode: str,
    cfg,
    pos_state=None,
) -> Optional[Dict[str, Any]]:
    """Model WT_3M_FORCE_OPEN for one symbol/side at one bar.

    Args:
        store:     _NPZStore instance for this symbol.
        bar_idx:   Current bar index.
        side:      "LONG" or "SHORT".
        mode:      "crypto" or "tradier".
        cfg:       VecConfig with WT_3M_FORCE_OPEN_* fields.
        pos_state: _PositionState (must be open=False to fire). If None, assumes ZERO.

    Returns:
        dict if WT_3M_FORCE_OPEN would fire, None otherwise.
        dict keys:
          side      — "LONG" or "SHORT"
          size_usd  — float USD notional for the forced open
          qty       — float quantity (size_usd / price, minimum 1.0 for stocks)
          reason    — reason string matching live format
    """
    if not getattr(cfg, 'WT_3M_FORCE_OPEN_ENABLED', True):
        return None

    # Only fires on ZERO position
    if pos_state is not None and pos_state.open:
        return None

    # ── Price ──────────────────────────────────────────────────────────────
    price = store.price(bar_idx)
    if price <= 0:
        return None

    # ── WT values ─────────────────────────────────────────────────────────
    # Crypto base TF = 3m, stocks base TF = 5m but tradier_manage reads wt1_3m
    # from indicator cache (which may be populated as the 5m-derived value).
    # We read wt1_3m first; if 0 try wt1_5m as fallback.
    wt1 = store.f("wt1_3m", bar_idx, 0.0)
    wt2 = store.f("wt2_3m", bar_idx, 0.0)
    if wt1 == 0.0 and wt2 == 0.0 and mode == "tradier":
        wt1 = store.f("wt1_5m", bar_idx, 0.0)
        wt2 = store.f("wt2_5m", bar_idx, 0.0)

    is_long = (side == "LONG")
    trigger = (is_long and wt1 > wt2) or (not is_long and wt1 < wt2)
    if not trigger:
        return None

    # ── Size ───────────────────────────────────────────────────────────────
    # Default: 9.0 crypto, 100.0 stocks (config_tradier.py default)
    if mode == "tradier":
        default_usd = 100.0
    else:
        default_usd = 9.0
    size_usd = float(getattr(cfg, 'WT_3M_FORCE_OPEN_SIZE_USD', default_usd))
    qty = size_usd / price
    if mode == "tradier":
        qty = max(qty, 1.0)  # tradier: minimum 1 share (mirrors _wf_qty calculation)

    # ── Reason ─────────────────────────────────────────────────────────────
    reason = f"WT_3M_FORCE_OPEN_{'LONG' if is_long else 'SHORT'}_wt1={wt1:.1f}_wt2={wt2:.1f}_px{price:.6f}"

    return {
        "side": side,
        "size_usd": size_usd,
        "qty": qty,
        "reason": reason,
        "wt1": wt1,
        "wt2": wt2,
    }
