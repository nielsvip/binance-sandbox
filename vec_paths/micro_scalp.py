"""
vec_paths/micro_scalp.py — MICRO_SCALP (stocks + USDC crypto) for vec_engine_v1.

Mirrors the micro-scalp logic from:
  - tradier_manage.py:1646 (MICRO_SCALP_STOCKS_MAKER — stocks)
  - ez_manage.py:21825 (MICRO_SCALP_USDC_MAKER — crypto USDC)

Per CLAUDE.md: Tier-1 shortlist tool only.

CLOSE LOGIC (both modes):
    Fire CLOSE when:
        gain >= threshold  AND  gain < prev_gain  (first deceleration)
    With stocks: also requires hold >= TRADIER_MIN_HOLD_MINUTES.

REOPEN LOGIC (both modes):
    Fire OPEN when:
        position is FLAT  AND  reopen_pending  AND
        (LONG: price <= exit_price) | (SHORT: price >= exit_price)

LIVE REASON STRINGS seen in /history/:
    MICRO_SCALP_STOCKS_CLOSE_g<gain>%_prev<prev>%_qty_999999  (close, stocks)
    MICRO_SCALP_STOCKS_REOPEN_exit<px>_now<px>                (reopen, stocks)
    MICRO_SCALP_USDC_CLOSE_g<gain>%_prev<prev>%               (close, crypto)
    MICRO_SCALP_USDC_REOPEN_exit<px>_now<px>                  (reopen, crypto)

NOTE: The vec engine cannot replicate the gain exactly because it doesn't have
the per-position profit calculated from partial fills. Instead it uses the raw
close-vs-entry_price percentage as the gain proxy.

VecConfig fields consumed:
    MICRO_SCALP_STOCKS_MAKER_ENABLED      (default False)
    MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT (default 0.05)
    MICRO_SCALP_USDC_MAKER_ENABLED        (default False)  — renamed MICRO_SCALP_USDC_MAKER_ENABLED
    MICRO_SCALP_GAIN_THRESHOLD_PCT        (default 0.02)
    MICRO_SCALP_MIN_HOLD_BARS             (default 0 = no hold gate in backtest)
"""
from __future__ import annotations

from typing import Any, Optional, Dict

_STOCKS_CLOSE_PREFIX = "MICRO_SCALP_STOCKS_CLOSE"
_STOCKS_REOPEN_PREFIX = "MICRO_SCALP_STOCKS_REOPEN"
_USDC_CLOSE_PREFIX = "MICRO_SCALP_USDC_CLOSE"
_USDC_REOPEN_PREFIX = "MICRO_SCALP_USDC_REOPEN"


def _sf(store: Any, key: str, idx: int, default: float = 0.0) -> float:
    try:
        return store.f(key, idx, default)
    except Exception:
        return default


def check_micro_scalp_close(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict]:
    """Check micro-scalp CLOSE signal at bar_idx.

    Args:
        store:     NPZ store with .f(key, idx, default) and .price(idx).
        bar_idx:   Current bar index.
        pos_state: Position state with attributes:
                     .open (bool)
                     .entry_price (float)
                     .open_bar (int)         — bar index when opened
                     .prev_gain (float)      — gain pct at previous bar (maintained by caller)
        mode:      "stocks" | "usdc"
        cfg:       VecConfig instance.

    Returns {"reason": str, "mode": mode} on fire, else None.
    """
    if mode == "stocks":
        if not getattr(cfg, "MICRO_SCALP_STOCKS_MAKER_ENABLED", False):
            return None
        threshold = float(getattr(cfg, "MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT", 0.05))
        min_hold_bars = int(getattr(cfg, "MICRO_SCALP_MIN_HOLD_BARS", 0))
    elif mode == "usdc":
        if not getattr(cfg, "MICRO_SCALP_USDC_MAKER_ENABLED", False):
            return None
        threshold = float(getattr(cfg, "MICRO_SCALP_GAIN_THRESHOLD_PCT", 0.02))
        min_hold_bars = 0
    else:
        return None

    if not getattr(pos_state, "open", False):
        return None

    price = store.price(bar_idx)
    entry_price = float(getattr(pos_state, "entry_price", 0.0) or 0.0)
    if price <= 0 or entry_price <= 0:
        return None

    side = str(getattr(pos_state, "side", "LONG") or "LONG").upper()
    gain = ((price - entry_price) / entry_price * 100.0) if side == "LONG" else ((entry_price - price) / entry_price * 100.0)

    # Hold gate (stocks only in live, min_hold_bars=0 disables in backtest by default)
    open_bar = int(getattr(pos_state, "open_bar", 0) or 0)
    bars_held = bar_idx - open_bar if open_bar > 0 else 0
    if min_hold_bars > 0 and bars_held < min_hold_bars:
        return None

    prev_gain = float(getattr(pos_state, "prev_gain", gain) or gain)

    if gain >= threshold and gain < prev_gain:
        if mode == "stocks":
            reason = f"{_STOCKS_CLOSE_PREFIX}_g{gain:.3f}%_prev{prev_gain:.3f}%"
        else:
            reason = f"{_USDC_CLOSE_PREFIX}_g{gain:.3f}%_prev{prev_gain:.3f}%"
        return {"reason": reason, "mode": mode, "gain": gain, "prev_gain": prev_gain}

    return None


def check_micro_scalp_reopen(
    store: Any,
    bar_idx: int,
    last_exit_price: float,
    mode: str,
    cfg: Any,
    original_side: str = "LONG",
) -> Optional[Dict]:
    """Check micro-scalp REOPEN signal at bar_idx (position flat after close).

    Args:
        store:            NPZ store with .price(idx).
        bar_idx:          Current bar index.
        last_exit_price:  Price at which the micro-scalp close fired.
        mode:             "stocks" | "usdc"
        cfg:              VecConfig instance.
        original_side:    "LONG" | "SHORT" — original position direction.

    Returns {"reason": str, "mode": mode} on fire, else None.
    """
    if mode == "stocks":
        if not getattr(cfg, "MICRO_SCALP_STOCKS_MAKER_ENABLED", False):
            return None
    elif mode == "usdc":
        if not getattr(cfg, "MICRO_SCALP_USDC_MAKER_ENABLED", False):
            return None
    else:
        return None

    if last_exit_price <= 0:
        return None

    price = store.price(bar_idx)
    if price <= 0:
        return None

    orig_long = (original_side.upper() == "LONG")
    # Crossed back: LONG re-enters when price falls back to/below exit; SHORT when rises back
    crossed = (orig_long and price <= last_exit_price) or (not orig_long and price >= last_exit_price)
    if not crossed:
        return None

    if mode == "stocks":
        reason = f"{_STOCKS_REOPEN_PREFIX}_exit{last_exit_price:.4f}_now{price:.4f}"
    else:
        reason = f"{_USDC_REOPEN_PREFIX}_exit{last_exit_price:.6f}_now{price:.6f}"

    return {"reason": reason, "mode": mode, "price": price, "exit_price": last_exit_price}
