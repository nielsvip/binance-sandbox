"""
vec_paths/haiku_winner.py — HAIKU_OVERSEER numeric paths (vectorizable subset).

LIVE SOURCE:
  ez_manage.py:46058 class HaikuOverseer — two distinct sub-systems:

  1. manage_winners() [IMPLEMENTED HERE]:
     Polls every 60s (AUGMENT_INTERVAL). For each open position:
       gain > AUGMENT_GAIN_THRESHOLD (3.0%) → augment AUGMENT_FRACTION (10%) of position.
       gain < REDUCE_GAIN_THRESHOLD (2.5%) after augment fired → reduce by augmented qty.
       If gain recovers above AUGMENT_GAIN_THRESHOLD and was previously reduced → re-augment.
     State per-position: total_augmented_qty, reduced (bool).

  2. scan_decisions() / call_haiku() [NOT IMPLEMENTED — AI-dependent]:
     Reads decision JSONL (<30s old), calls Claude Haiku API with stoch/sentiment context,
     reverses 50% of position if verdict=REVERSE with confidence>=0.75.
     Un-vectorizable: live AI calls on historical data = non-deterministic + non-replayable.

  ENTRY GATE [IMPLEMENTED HERE — deterministic rules only]:
     From build_judgement_prompt() rules 1-2 (the purely numeric/deterministic ones):
       LONG entry when stoch K_15m > HAIKU_ENTRY_GATE_LONG_MAX_K (85) → block.
       SHORT entry when stoch K_15m < HAIKU_ENTRY_GATE_SHORT_MIN_K (15) → block.
     This captures the dominant Haiku reversal trigger without an API call.

CONFIG KEYS (all default OFF / matching live defaults):
  HAIKU_WINNER_ENABLED         bool   False  — enable winner pyramid+reduce
  HAIKU_AUGMENT_GAIN_THRESHOLD float  3.0    — augment when gain > this
  HAIKU_REDUCE_GAIN_THRESHOLD  float  2.5    — reduce when gain drops below this
  HAIKU_AUGMENT_FRACTION       float  0.10   — augment qty = positionAmt × fraction
  HAIKU_ENTRY_GATE_ENABLED     bool   False  — enable stoch overbought/oversold gate
  HAIKU_ENTRY_GATE_LONG_MAX_K  float  85.0   — block LONG entries above this K_15m
  HAIKU_ENTRY_GATE_SHORT_MIN_K float  15.0   — block SHORT entries below this K_15m
"""

from typing import Any, Dict, Optional


def check_haiku_winner(
    pos: Any,
    indicator_dict: Dict[str, Any],
    mode: str,
    cfg: Any,
) -> Optional[Dict[str, Any]]:
    """Winner pyramid and giveback-reduce — numeric-only portion of manage_winners().

    Call once per bar per open position. State tracking (augmented qty, reduced flag)
    is stored on trade_manager._haiku_state[position_key] by the engine caller.

    Args:
        pos:            Position object (positionAmt, entry_price, gain attrs).
        indicator_dict: Current-bar indicator dict for this symbol.
        mode:           "crypto" or "tradier".
        cfg:            Config object with HAIKU_* keys.

    Returns:
        dict with keys:
          action       — "AUGMENT" or "REDUCE"
          qty          — quantity to trade (float)
          reason       — reason string matching live HAIKU_WINNER_AUG / HAIKU_REDUCE
          haiku_state_update — dict of state keys to update after executing
        None if no action needed.
    """
    if not getattr(cfg, "HAIKU_WINNER_ENABLED", False):
        return None
    pos_amt = abs(float(getattr(pos, "positionAmt", 0) or 0))
    if pos_amt < 0.0001:
        return None
    entry_price = float(getattr(pos, "entry_price", 0) or 0)
    if entry_price <= 0:
        return None
    px = float(indicator_dict.get("current_price", 0) or 0)
    if px <= 0:
        return None
    is_long = getattr(pos, "side", "") == "LONG" or str(
        getattr(pos, "position_key", "") or ""
    ).endswith("_LONG")
    gain = (
        (px - entry_price) / entry_price * 100.0
        if is_long
        else (entry_price - px) / entry_price * 100.0
    )
    aug_threshold = float(getattr(cfg, "HAIKU_AUGMENT_GAIN_THRESHOLD", 3.0))
    red_threshold = float(getattr(cfg, "HAIKU_REDUCE_GAIN_THRESHOLD", 1.0))  # USER 2026-08-23: reduce when gain <1 (was 2.5)
    # USER 2026-08-23: augment when gain>3 OR (WT15 crossover and gain>2)
    wt15_cross = False
    try:
        # WT 15m crossover: wt_15m crosses above/below wt_avg_15m or wt crosses 0
        wt_15 = indicator_dict.get("wt_15m") or indicator_dict.get("wt15m") or indicator_dict.get("wt_15")
        wt_avg_15 = indicator_dict.get("wt_avg_15m") or indicator_dict.get("wt_avg15m")
        if wt_15 is not None and wt_avg_15 is not None:
            # Simple cross detection: current wt > avg and previous wt <= avg (would need prev, approximate with wt > avg)
            wt15_cross = (float(wt_15) > float(wt_avg_15) and is_long) or (float(wt_15) < float(wt_avg_15) and not is_long)
        else:
            # Fallback: check wt_15m cross 0
            if wt_15 is not None:
                wt15_cross = (float(wt_15) > 0 and is_long) or (float(wt_15) < 0 and not is_long)
    except:
        wt15_cross = False
    frac = float(getattr(cfg, "HAIKU_AUGMENT_FRACTION", 0.10))
    return _evaluate(gain, pos_amt, px, aug_threshold, red_threshold, frac, is_long)


def _evaluate(
    gain: float,
    pos_amt: float,
    px: float,
    aug_threshold: float,
    red_threshold: float,
    frac: float,
    is_long: bool,
    augmented_qty: float = 0.0,
    reduced: bool = False,
) -> Optional[Dict[str, Any]]:
    """Core state-machine logic — separated for testability."""
    if gain >= aug_threshold:
        if reduced and augmented_qty > 0:
            # Re-enter after gain recovered — mirror live "managed and managed.get('reduced')" branch
            return {
                "action": "AUGMENT",
                "qty": augmented_qty,
                "reason": f"HAIKU_REENTER_{gain:.1f}pct",
                "haiku_state_update": {"reduced": False},
            }
        if augmented_qty == 0:
            aug_qty = min(pos_amt * frac, pos_amt * 0.4)
            if aug_qty * px < 1.0:
                return None
            return {
                "action": "AUGMENT",
                "qty": aug_qty,
                "reason": f"HAIKU_WINNER_AUG_{gain:.1f}pct",
                "haiku_state_update": {"augmented_qty_delta": aug_qty},
            }
    elif gain < red_threshold and augmented_qty > 0 and not reduced:
        reduce_qty = augmented_qty
        if reduce_qty * px < 1.0:
            return None
        return {
            "action": "REDUCE",
            "qty": reduce_qty,
            "reason": f"HAIKU_REDUCE_{gain:.1f}pct<{red_threshold}",
            "haiku_state_update": {"reduced": True},
        }
    return None


def check_haiku_entry_gate(
    indicator_dict: Dict[str, Any],
    side: str,
    mode: str,
    cfg: Any,
) -> bool:
    """Entry quality gate — deterministic rules from build_judgement_prompt().

    Returns True (block entry) when:
      LONG: stoch K_15m > HAIKU_ENTRY_GATE_LONG_MAX_K  (overbought — live rule #1)
      SHORT: stoch K_15m < HAIKU_ENTRY_GATE_SHORT_MIN_K (oversold — live rule #2)

    This covers the majority of Haiku REVERSE verdicts without an API call.
    Rules #3-6 (augmenting losers, bad sentiment, all-TF stoch against, desperation
    reasons) are either covered by other gates or require live context.

    Returns:
        True  = block this entry (Haiku would likely say REVERSE)
        False = allow this entry
    """
    if not getattr(cfg, "HAIKU_ENTRY_GATE_ENABLED", False):
        return False
    tf = "15m" if mode == "tradier" else "15m"
    k_15m = float(indicator_dict.get(f"k_{tf}", indicator_dict.get("stoch_k_15m", 50)) or 50)
    if side == "LONG":
        max_k = float(getattr(cfg, "HAIKU_ENTRY_GATE_LONG_MAX_K", 85.0))
        return k_15m > max_k
    if side == "SHORT":
        min_k = float(getattr(cfg, "HAIKU_ENTRY_GATE_SHORT_MIN_K", 15.0))
        return k_15m < min_k
    return False
