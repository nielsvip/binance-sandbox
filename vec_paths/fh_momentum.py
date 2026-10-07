"""
vec_paths/fh_momentum.py — First-Hour Momentum entry path (vectorized).

LIVE SOURCE (tradier):
  tradier_manage.py:5876-5917 (_should_enter_position, BRANCH 0 — fires FIRST):
    if FH_MOMENTUM_ENABLED:
      _fh_move = (current_price - open_D) / open_D * 100.0
      if abs(_fh_move) >= FH_MOMENTUM_MIN_MOVE_PCT (0.5%):
        if FH_MOMENTUM_MFI_CONFIRM: check mfi_D vs 50 (LONG: mfi_D >= 50)
        if FH_MOMENTUM_DC_CONFIRM:  check dc_position_D < FH_MOMENTUM_DC_MAX_LONG (0.5) for LONG
        block if retest score favors opposite direction
        return OPEN, "FH_MOMENTUM_L move={...}%_mfi=..._dc=...", 85.0, qty

LIVE SOURCE (crypto):
  ez_manage.py:24001-24082 (crypto_first_hour_momentum_loop):
    Fires at 13:30-14:00 UTC (US market open window). Checks:
      (current_price - open_D) / open_D * 100.0 vs CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT
      CRYPTO_FH_MOMENTUM_DC_CONFIRM: dc_position_3m (LTF) vs DC_MAX_LONG (0.5)
    Reason: "FH_MOM_CRYPTO_L move={...}%_dc={dc_pos:.2f}_rt={retest_bonus}"

ADDITIVE path:
  FH_MOMENTUM fires FIRST in the live engine — it returns OPEN before any other logic.
  In the vectorized engine it is an INDEPENDENT entry that fires when:
    1. The bar timestamp falls within the first-hour window (13:30–14:00 UTC for stocks;
       13:00–14:00 UTC for crypto's US open manipulation window).
    2. open_D is available and the move % >= min_move threshold.
    3. MFI and DC gates pass (if enabled).

VEC LIMITATIONS:
  - bar_interval = 180s (3m) for crypto, 300s (5m) for tradier. We detect this from
    store timestamps.
  - open_D forward-filled by _NPZStore for HTF → always available at 3m bars.
    HOWEVER: open_D represents the daily open, which is static all day. For crypto this
    is reasonable (price vs daily open). For stocks, live reads the actual day open.
  - dc_position_D is forward-filled HTF (correct approximation).
  - No compute_dc_retest_score — live uses _dc_long_rt / _dc_short_rt (DC retest bonus
    scoring). We approximate: block LONG if dc_position_D >= FH_DC_MAX_LONG.
  - Window detection: we use UTC timestamp mod 86400 to check time-of-day.
    Stocks first hour: 09:30-10:30 ET = 13:30-14:30 UTC (60 min window).
    Crypto FH window:  13:00-14:30 UTC (90 min, per CRYPTO_FH_MOM code).
    We use a cfg field FH_MOMENTUM_WINDOW_MINUTES to override (default 60 for tradier).

NPZ FIELDS:
  open_D          — daily open price (forward-filled from D bar)
  mfi_D           — daily MFI (forward-filled)
  dc_position_D   — daily DC channel position (0=at low, 1=at high)
  close           — current bar close price
  timestamps      — bar unix timestamps (UTC)

RETURNS (check_fh_momentum_entry):
  dict with keys:
    side       — "LONG" or "SHORT"
    move_pct   — float (price % move from open_D)
    score      — float entry score (85.0 matching live)
    reason     — str reason string matching live format
  or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


_SECONDS_PER_DAY = 86400
_STOCKS_FH_START_UTC = 13 * 3600 + 30 * 60  # 13:30 UTC = 09:30 ET
_STOCKS_FH_END_UTC = 14 * 3600 + 30 * 60    # 14:30 UTC = 10:30 ET
_CRYPTO_FH_START_UTC = 13 * 3600            # 13:00 UTC
_CRYPTO_FH_END_UTC = 14 * 3600 + 30 * 60   # 14:30 UTC


def check_fh_momentum_entry(
    store,
    bar_idx: int,
    side: str,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """First-Hour Momentum entry path.

    Args:
        store:   _NPZStore for this symbol.
        bar_idx: Current bar index.
        side:    "LONG" or "SHORT".
        mode:    "crypto" or "tradier".
        cfg:     VecConfig with FH_MOMENTUM_* / CRYPTO_FH_MOMENTUM_* fields.

    Returns:
        dict if signal fires, None otherwise.
        dict keys:
          side, move_pct, score, reason
    """
    is_long = (side == "LONG")
    if mode == "tradier":
        enabled = getattr(cfg, "FH_MOMENTUM_ENABLED", False)
        if not enabled:
            return None
        min_move = getattr(cfg, "FH_MOMENTUM_MIN_MOVE_PCT", getattr(cfg, "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", 0.5))
        mfi_confirm = getattr(cfg, "FH_MOMENTUM_MFI_CONFIRM", getattr(cfg, "TRADIER_FH_MOMENTUM_MFI_CONFIRM", True))
        dc_confirm = getattr(cfg, "FH_MOMENTUM_DC_CONFIRM", getattr(cfg, "TRADIER_FH_MOMENTUM_DC_CONFIRM", True))
        dc_max_long = getattr(cfg, "FH_MOMENTUM_DC_MAX_LONG", getattr(cfg, "TRADIER_FH_MOMENTUM_DC_MAX_LONG", 0.5))
        window_minutes = getattr(cfg, "FH_MOMENTUM_WINDOW_MINUTES", 60.0)
        ts_i = int(store.timestamps[bar_idx])
        seconds_into_day = ts_i % _SECONDS_PER_DAY
        fh_start = _STOCKS_FH_START_UTC
        fh_end = fh_start + int(window_minutes * 60)
        if not (fh_start <= seconds_into_day < fh_end):
            return None
        reason_prefix = "FH_MOMENTUM"
    else:
        enabled = getattr(cfg, "CRYPTO_FH_MOMENTUM_ENABLED", True)
        if not enabled:
            return None
        min_move = getattr(cfg, "CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT", 0.5)
        mfi_confirm = getattr(cfg, "CRYPTO_FH_MOMENTUM_DC_CONFIRM", True)
        dc_confirm = getattr(cfg, "CRYPTO_FH_MOMENTUM_DC_CONFIRM", True)
        dc_max_long = getattr(cfg, "CRYPTO_FH_MOMENTUM_DC_MAX_LONG", 0.5)
        ts_i = int(store.timestamps[bar_idx])
        seconds_into_day = ts_i % _SECONDS_PER_DAY
        if not (_CRYPTO_FH_START_UTC <= seconds_into_day < _CRYPTO_FH_END_UTC):
            return None
        reason_prefix = "FH_MOM_CRYPTO"
        mfi_confirm = False
    open_d = store.f("open_D", bar_idx, 0.0)
    if open_d <= 0:
        return None
    price = store.price(bar_idx)
    if price <= 0:
        return None
    move_pct = (price - open_d) / open_d * 100.0
    if abs(move_pct) < min_move:
        return None
    mfi_d = store.f("mfi_D", bar_idx, 50.0)
    dc_pos_d = store.f("dc_position_D", bar_idx, 0.5)
    if mfi_confirm:
        if is_long and move_pct > 0 and mfi_d < 50:
            return None
        if not is_long and move_pct < 0 and mfi_d > 50:
            return None
    if dc_confirm:
        if is_long and move_pct > 0 and dc_pos_d > dc_max_long:
            return None
        if not is_long and move_pct < 0 and dc_pos_d < (1.0 - dc_max_long):
            return None
    if is_long and move_pct <= 0:
        return None
    if not is_long and move_pct >= 0:
        return None
    if is_long:
        reason = (
            f"{reason_prefix}_L move={move_pct:+.2f}%"
            f"_mfi={mfi_d:.0f}_dc={dc_pos_d:.2f}"
        )
    else:
        reason = (
            f"{reason_prefix}_S move={move_pct:+.2f}%"
            f"_mfi={mfi_d:.0f}_dc={dc_pos_d:.2f}"
        )
    return {
        "side": side,
        "move_pct": move_pct,
        "score": 85.0,
        "reason": reason,
    }
