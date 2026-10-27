"""MIN_EDGE_OVER_FEE — refuse an entry whose own target cannot pay its fee.

This is arithmetic, not a strategy opinion. The baseline strategy's take-profit
is a Donchian channel edge (Bible 15): long exits at `dc_high_{TF}` less
`DAYTRADE_DC_TARGET_BUFFER_PCT`, short at `dc_low_{TF}` plus the same buffer. If
the distance from the entry price to that target is smaller than the round-trip
commission, the trade's own best case is a loss. Opening it cannot be correct
under any parameter set.

Measured 2026-10-27, which is why this exists: across five crypto accounts over
30 days the book turned over $247,533 on roughly $1,500 of equity (15,062 fills,
~260 closes/day) for a gross realized result of -$78.90 and $65.41 of
commission -- net -$144.31, with fees 45% of the bleed and the gross edge
itself slightly negative (-0.032% of turnover). Live was computing gain with
`config.Config.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.0`, so a reopen for a move
that could not cover the fee looked free.

The mask is a REAL veto on real NPZ arrays and changes the trade ledger, so its
sweep delta is honest (Bible 19). Returns a boolean BLOCK mask (True = refuse
this bar's open) or None when the gate is off or the channel is unavailable, in
which case the caller must not veto anything.
"""

from __future__ import annotations

import numpy as np

_TF_ORDER = ("15m", "1h", "4h", "D")


def _arr(npz, key, n):
    v = npz.get(key)
    if v is None:
        return None
    v = np.asarray(v, dtype=float)
    if v.ndim != 1 or v.shape[0] != n:
        return None
    return v


def _target_tfs(cfg) -> list:
    """TFs whose channel edge is the live take-profit.

    These switches hold a LIST, e.g. `DAYTRADE_DC_TARGET_TF = '15m,1h,4h'`
    (Bible 15: "{TF} is one of 15m/1h/4h ... and combinations"). The exit fires
    at whichever listed edge price reaches first, so the binding target is the
    NEAREST one, and `_nearest_target` takes the min dc_high for a long / max
    dc_low for a short. An earlier version of this file looked up a single key
    and silently found nothing, making the gate inert on every symbol.
    """
    out = []
    for name in ("DAYTRADE_DC_TARGET_TF", "TECHNICAL_DC_TARGET_TF"):
        raw = str(getattr(cfg, name, "OFF") or "OFF").strip()
        if not raw or raw.upper() == "OFF":
            continue
        for part in raw.replace(";", ",").split(","):
            tf = part.strip()
            if tf and tf.upper() != "OFF" and tf in _TF_ORDER and tf not in out:
                out.append(tf)
        if out:
            break
    return out


def _nearest_target(npz, n, is_long, tfs, buf):
    """Nearest take-profit price per bar across the configured TFs."""
    best = None
    for tf in tfs:
        edge = _arr(npz, f"dc_high_{tf}" if is_long else f"dc_low_{tf}", n)
        if edge is None:
            continue
        with np.errstate(invalid="ignore"):
            target = edge * ((1.0 - buf / 100.0) if is_long else (1.0 + buf / 100.0))
            target = np.where(np.isfinite(target) & (target > 0), target, np.nan)
        if best is None:
            best = target
        else:
            best = np.fmin(best, target) if is_long else np.fmax(best, target)
    return best


def fee_round_trip_pct(cfg) -> float:
    if str(getattr(cfg, "MODE", "crypto")) == "tradier":
        return 0.0
    try:
        v = float(getattr(cfg, "CRYPTO_ROUND_TRIP_COMMISSION_PCT", 0.0) or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    if v > 0.0:
        return v
    try:
        import fee_model

        return float(fee_model.round_trip_pct("crypto"))
    except Exception:
        return 0.0529


def block_mask(npz, n, is_long, cfg, close=None):
    """True where the technical target cannot cover the round-trip commission."""
    if not bool(getattr(cfg, "MIN_EDGE_OVER_FEE_ENABLED", False)):
        return None
    try:
        mult = float(getattr(cfg, "MIN_EDGE_OVER_FEE_MULT", 1.0) or 0.0)
    except (TypeError, ValueError):
        mult = 1.0
    if mult <= 0.0:
        return None
    fee = fee_round_trip_pct(cfg)
    if fee <= 0.0:
        return None
    required_pct = mult * fee
    tfs = _target_tfs(cfg)
    if not tfs:
        return None
    px = _arr(npz, "close", n) if close is None else np.asarray(close, dtype=float)
    if px is None or px.shape[0] != n:
        return None
    try:
        buf = float(getattr(cfg, "DAYTRADE_DC_TARGET_BUFFER_PCT", 0.10) or 0.0)
    except (TypeError, ValueError):
        buf = 0.10
    target = _nearest_target(npz, n, is_long, tfs, buf)
    if target is None:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        room_pct = 100.0 * ((target - px) if is_long else (px - target)) / px
    usable = np.isfinite(px) & (px > 0) & np.isfinite(target) & (target > 0) & np.isfinite(room_pct)
    # A bar with no usable channel or price is left alone: an unknown is not a
    # reason to veto (fail-open), matching how the other entry gates behave.
    block = usable & (room_pct < required_pct)
    return np.asarray(block, dtype=bool)


def room_pct_and_required(npz, n, is_long, cfg, close=None):
    """Diagnostic twin of `block_mask`: (room_pct array, required_pct) or None."""
    tfs = _target_tfs(cfg)
    if not tfs:
        return None
    px = _arr(npz, "close", n) if close is None else np.asarray(close, dtype=float)
    if px is None:
        return None
    try:
        buf = float(getattr(cfg, "DAYTRADE_DC_TARGET_BUFFER_PCT", 0.10) or 0.0)
    except (TypeError, ValueError):
        buf = 0.10
    try:
        mult = float(getattr(cfg, "MIN_EDGE_OVER_FEE_MULT", 1.0) or 1.0)
    except (TypeError, ValueError):
        mult = 1.0
    target = _nearest_target(npz, n, is_long, tfs, buf)
    if target is None:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        room = 100.0 * ((target - px) if is_long else (px - target)) / px
    return np.where(np.isfinite(room), room, 0.0), mult * fee_round_trip_pct(cfg)


def live_room_pct(entry_price, channel_edge, is_long, target_buffer_pct=0.10):
    """Scalar twin for the live path (ez_manage) — same formula, one bar."""
    try:
        px = float(entry_price)
        edge = float(channel_edge)
        buf = float(target_buffer_pct or 0.0)
    except (TypeError, ValueError):
        return None
    if px <= 0 or edge <= 0:
        return None
    if is_long:
        return 100.0 * (edge * (1.0 - buf / 100.0) - px) / px
    return 100.0 * (px - edge * (1.0 + buf / 100.0)) / px
