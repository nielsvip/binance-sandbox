"""
vec_paths/price_cross_back.py — PRICE_CROSS_BACK_REENTRY path (vec approximation).

LIVE SOURCE:
  tradier_manage.py:1880-1912 (BRANCH B — positionAmt==0).
  Also in tradier_manage.py:6466 (evaluate_reentry fallback).

WHAT THE LIVE SYSTEM DOES:
  - After a close event, track last_exit_time and last_exit_price per symbol.
  - On next bar with positionAmt==0: if recent close within max_age AND
    current_price within band_pct% of last_exit_price AND direction is sane
    → OPEN immediately (no WT gate required).
  - Direction sanity:
    LONG: price <= last_exit_price * (1 + band_pct/100)  [price at/below exit = pullback]
    SHORT: price >= last_exit_price * (1 - band_pct/100) [price at/above exit = rally back]
  - Reason format: PRICE_CROSS_BACK_REENTRY_exit{px}_cur{px}_dist{pct}%_age{min}m
  - Config:
    PRICE_CROSS_BACK_REENTRY_ENABLED (default True)
    PRICE_CROSS_BACK_MAX_AGE_MIN (default 240.0 = 4h)
    PRICE_CROSS_BACK_BAND_PCT (default 0.3 = 0.3%)

VEC APPROXIMATION:
  - We extend _PositionState to track last_close_price (a new field we add here
    if not already present on the pos_state object).
  - last_close_ts is already on _PositionState (used for AUGMENT_LOCK cooldown).
  - last_close_price must be set by the caller (VecEngine.simulate()) whenever
    a position closes. If not present (0.0), this path will not fire.

RETURNS:
  dict with keys: {side, qty, reason, dist_pct, age_min} or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Optional, Dict


def check_price_cross_back(
    store,
    bar_idx: int,
    sym: str,
    side: str,
    pos_state,
    cfg,
    mode: str = "tradier",
) -> Optional[Dict[str, Any]]:
    """Model PRICE_CROSS_BACK_REENTRY for one symbol/side at one bar.

    Args:
        store:     _NPZStore instance for this symbol.
        bar_idx:   Current bar index.
        sym:       Symbol name (for logging; not used in core logic).
        side:      "LONG" or "SHORT".
        pos_state: _PositionState. Must have:
                   - open == False (closed position only)
                   - last_close_ts: unix timestamp of last close (float)
                   - last_close_price: price at last close (float, 0.0 if never set)
        cfg:       VecConfig with PRICE_CROSS_BACK_* fields.
        mode:      "crypto" or "tradier" (not used in core logic, present for API parity).

    Returns:
        dict if PRICE_CROSS_BACK_REENTRY would fire, None otherwise.
        dict keys:
          side       — "LONG" or "SHORT"
          qty        — float quantity to open
          reason     — reason string matching live format
          dist_pct   — float distance % from exit price
          age_min    — float age in minutes since close
    """
    if not getattr(cfg, 'PRICE_CROSS_BACK_REENTRY_ENABLED', True):
        return None

    # Only fires when position is CLOSED
    if pos_state.open:
        return None

    # Requires a prior close with price recorded
    last_close_ts = float(getattr(pos_state, 'last_close_ts', 0.0))
    last_close_price = float(getattr(pos_state, 'last_close_price', 0.0))

    if last_close_ts <= 0 or last_close_price <= 0:
        return None

    # ── Current price ───────────────────────────────────────────────────────
    price = store.price(bar_idx)
    if price <= 0:
        return None

    # ── Current bar timestamp ───────────────────────────────────────────────
    ts = float(store.timestamps[bar_idx]) if bar_idx < len(store.timestamps) else 0.0
    if ts <= 0:
        return None

    # ── Age check ──────────────────────────────────────────────────────────
    max_age_min = float(getattr(cfg, 'PRICE_CROSS_BACK_MAX_AGE_MIN', 240.0))
    band_pct = float(getattr(cfg, 'PRICE_CROSS_BACK_BAND_PCT', 0.3))
    age_min = (ts - last_close_ts) / 60.0
    if age_min < 0 or age_min >= max_age_min:
        return None

    # ── Distance check ─────────────────────────────────────────────────────
    dist_pct = abs(price - last_close_price) / last_close_price * 100.0
    is_long = (side == "LONG")
    # 2026-05-21 USER MANDATE — directional cross-back option (mirror live SNDK fix).
    # Old logic (still default): symmetric band check (dist <= band_pct).
    # Directional logic: fire whenever price has crossed BACK THROUGH exit in favorable
    # direction (LONG: cur >= exit, no upper cap; SHORT: cur <= exit, no lower cap).
    # Within-band on the OTHER side still counts (catches refire just below the level).
    directional_enabled = bool(getattr(cfg, "PRICE_CROSS_BACK_DIRECTIONAL_ENABLED", False))
    if directional_enabled:
        favorable = (price >= last_close_price) if is_long else (price <= last_close_price)
        within_band = dist_pct <= band_pct
        if not (favorable or within_band):
            return None
    else:
        if dist_pct > band_pct:
            return None
        # ── Direction sanity (mirrors tradier_manage:1903 OLD behavior) ─────
        if is_long:
            dir_ok = price <= last_close_price * (1 + band_pct / 100.0)
        else:
            dir_ok = price >= last_close_price * (1 - band_pct / 100.0)
        if not dir_ok:
            return None

    # ── Qty (mirrors live: START_POSITION_SIZE / price, int for stocks) ─────
    start_size = float(getattr(cfg, 'START_POSITION_SIZE', 600.0))
    qty = start_size / max(price, 1e-9)
    if mode == "tradier":
        qty = max(1, int(qty))

    # ── Reason matching live format ─────────────────────────────────────────
    reason = (
        f"PRICE_CROSS_BACK_REENTRY_exit{last_close_price:.4f}"
        f"_cur{price:.4f}"
        f"_dist{dist_pct:.2f}%"
        f"_age{age_min:.0f}m"
    )

    return {
        "side": side,
        "qty": qty,
        "reason": reason,
        "dist_pct": dist_pct,
        "age_min": age_min,
        "last_close_price": last_close_price,
    }
