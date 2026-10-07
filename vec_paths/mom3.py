"""
vec_paths/mom3.py — MOM3 / MOM5 additive entry score boost (vectorized).

LIVE SOURCE:
  ez_manage.py:18765-18778 (inside check_entry_candidates_for_account, final_order_quantity):
    # BACKTEST_CHANGE_4: MOM3 3-bar momentum mean-reversion (#2 signal)
    if getattr(config, 'MOM3_ENTRY_ENABLED', False):
        _close_3 = float(i.get(f'close_3bar_{config.TF_FOCUS}', 0) or 0)
        if _close_3 > 0:
            _mom3 = (current_price - _close_3) / _close_3 * 100
            if is_long and _mom3 < config.MOM3_LONG_THRESHOLD:
                factors.append((22, True, f"MOM3_LONG({_mom3:.2f}%<{config.MOM3_LONG_THRESHOLD})"))
            elif not is_long and _mom3 > config.MOM3_SHORT_THRESHOLD:
                factors.append((22, True, f"MOM3_SHORT({_mom3:.2f}%>{config.MOM3_SHORT_THRESHOLD})"))

    # BACKTEST_CHANGE_5: MOM5 5-bar momentum mean-reversion (#3 signal)
    if getattr(config, 'MOM5_ENTRY_ENABLED', False):
        _close_5 = float(i.get(f'close_5bar_{config.TF_FOCUS}', 0) or 0)
        if _close_5 > 0:
            _mom5 = (current_price - _close_5) / _close_5 * 100
            if is_long and _mom5 < config.MOM5_LONG_THRESHOLD:
                factors.append((22, True, f"MOM5_LONG({_mom5:.2f}%<{config.MOM5_LONG_THRESHOLD})"))
            elif not is_long and _mom5 > config.MOM5_SHORT_THRESHOLD:
                factors.append((22, True, f"MOM5_SHORT({_mom5:.2f}%>{config.MOM5_SHORT_THRESHOLD})"))

ADDITIVE path:
  MOM3/MOM5 are ADDITIVE signals — they each add +22 to the entry score factor list,
  contributing to whether the final entry score meets the threshold. They are NOT
  hard gates. Returning a score boost amount (22) to be added by the engine.

  config.py defaults:
    MOM3_ENTRY_ENABLED = True
    MOM3_LONG_THRESHOLD = -1.0   (LONG when mom3 < -1.0%, i.e., 3-bar pullback)
    MOM3_SHORT_THRESHOLD = 1.0   (SHORT when mom3 > +1.0%, i.e., 3-bar rally)
    MOM5_ENTRY_ENABLED = implied True

  TF_FOCUS for crypto = "3m" (base TF). For tradier = "5m".

NPZ FIELDS:
  close_3bar_{btf}  — close 3 bars ago (precomputed in NPZ)
  close_5bar_{btf}  — close 5 bars ago
  close             — current bar close

RETURNS (check_mom3_boost):
  dict with keys:
    side         — "LONG" or "SHORT"
    score_boost  — float boost amount (22.0 per signal that fires)
    reason       — str describing which signals fired
  or None if no signal fires.

  NOTE: Both MOM3 and MOM5 are checked. If both fire, score_boost = 44.0.
  If only one fires, score_boost = 22.0.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def check_mom3_boost(
    store,
    bar_idx: int,
    side: str,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """MOM3 + MOM5 additive entry score boost.

    Args:
        store:   _NPZStore for this symbol.
        bar_idx: Current bar index.
        side:    "LONG" or "SHORT".
        mode:    "crypto" or "tradier".
        cfg:     VecConfig with MOM3_ENTRY_ENABLED, MOM3_LONG/SHORT_THRESHOLD, etc.

    Returns:
        dict if at least one signal fires, None otherwise.
        dict keys:
          side, score_boost, reason
    """
    is_long = (side == "LONG")
    mom3_enabled = getattr(cfg, "MOM3_ENTRY_ENABLED", True)
    mom5_enabled = getattr(cfg, "MOM5_ENTRY_ENABLED", True)
    if not mom3_enabled and not mom5_enabled:
        return None
    if mode == "crypto":
        btf = "3m"
    else:
        btf = "5m"
    price = store.price(bar_idx)
    if price <= 0:
        return None
    total_boost = 0.0
    reasons = []
    if mom3_enabled:
        close_3 = store.f(f"close_3bar_{btf}", bar_idx, 0.0)
        if close_3 > 0:
            mom3_pct = (price - close_3) / close_3 * 100.0
            thr_long = getattr(cfg, "MOM3_LONG_THRESHOLD", -1.0)
            thr_short = getattr(cfg, "MOM3_SHORT_THRESHOLD", 1.0)
            if is_long and mom3_pct < thr_long:
                total_boost += 22.0
                reasons.append(f"MOM3_LONG({mom3_pct:.2f}%<{thr_long})")
            elif not is_long and mom3_pct > thr_short:
                total_boost += 22.0
                reasons.append(f"MOM3_SHORT({mom3_pct:.2f}%>{thr_short})")
    if mom5_enabled:
        close_5 = store.f(f"close_5bar_{btf}", bar_idx, 0.0)
        if close_5 > 0:
            mom5_pct = (price - close_5) / close_5 * 100.0
            thr_long5 = getattr(cfg, "MOM5_LONG_THRESHOLD", -1.5)
            thr_short5 = getattr(cfg, "MOM5_SHORT_THRESHOLD", 1.5)
            if is_long and mom5_pct < thr_long5:
                total_boost += 22.0
                reasons.append(f"MOM5_LONG({mom5_pct:.2f}%<{thr_long5})")
            elif not is_long and mom5_pct > thr_short5:
                total_boost += 22.0
                reasons.append(f"MOM5_SHORT({mom5_pct:.2f}%>{thr_short5})")
    if total_boost <= 0:
        return None
    return {
        "side": side,
        "score_boost": total_boost,
        "reason": "+".join(reasons),
    }
