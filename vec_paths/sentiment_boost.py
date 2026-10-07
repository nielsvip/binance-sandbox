"""
vec_paths/sentiment_boost.py — SENTIMENT_BOOST augment path (vec approximation).

SOURCE: tradier_manage.py:7408 periodic_sentiment_rebalancing()

Live logic summary:
  1. For each open position, compute ideal_qty based on market_sentiment (via
     calculate_quantity_complex).
  2. If current_qty < ideal_qty by more than 25% AND pnl > 0.5%, AUGMENT with
     the delta qty.
  3. Augment reason: "SENTIMENT_BOOST ideal=N cur=N loc=X.X"
  4. Cooldown: 30-min since last augment/reduce.

Vec approximation:
  - ideal_qty is estimated from market_sentiment_score (cross-symbol breadth
    from NPZ, range 0-100) scaled against START_POSITION_SIZE.
  - sentiment_local (per-stock score) NOT available in NPZ — using
    market_sentiment_score as proxy.
  - The deviation threshold and PnL gate are configurable via VecConfig.
  - Cooldown is tracked in pos_state.last_augment_ts (new field).

LIMITATIONS vs live:
  - calculate_quantity_complex() is a 100-line async function that includes
    GA multiplier, technical structure, velocity, etc. We approximate as:
    ideal_qty = base_qty * sentiment_mult
  - sentiment_local (per-stock) uses global market_sentiment_score as proxy.
  - No market_snapshot context (live reads all positions).

See validate_against_live.py for accuracy measurements.
"""
from __future__ import annotations
from typing import Dict, Any, Optional

# ── Sentiment multiplier (mirrors calculate_quantity_complex section 3) ──
# sent_global in live = 0market_sentiment_score (range 0-100, 50=neutral).
# In NPZ market_sentiment_score = cross-sym WT breadth score (0-100).
# Map 0-100 → -100..+100 for live API compatibility: sent_global = (mss - 50) * 2
_SENT_GLOBAL_NEUTRAL = 50.0

def _sentiment_mult_from_score(sent_global: float, sent_local: float, is_long: bool) -> float:
    """Approximate calculate_quantity_complex sentiment_mult block.

    Live code (tradier_manage.py:4373-4391):
      if is_long:
        if sent_global > 0:
          if sent_local > sent_global + 40: mult = 1.5
          elif sent_local > sent_global + 20: mult = 1.2
          elif sent_local < -20: mult = 0.3
        else (Bear): ...
      else (Short): ...
    sent_strength_boost = 1 + (sent_strength / 200.0) → typically ~1.25 when sent_strength≈50.
    We approximate sent_strength as 50 (neutral).
    """
    mult = 1.0
    sent_strength_boost = 1.25  # 1 + 50/200
    if is_long:
        if sent_global > 0:
            if sent_local > sent_global + 40:
                mult = 1.5
            elif sent_local > sent_global + 20:
                mult = 1.2
            elif sent_local < -20:
                mult = 0.3
        else:
            if sent_local > 60:
                mult = 1.3
            elif sent_local < sent_global:
                mult = 0.1
    else:
        if sent_global < 0:
            if sent_local < sent_global - 40:
                mult = 1.5
            elif sent_local < sent_global:
                mult = 1.2
            elif sent_local > 20:
                mult = 0.3
        else:
            if sent_local < -80:
                mult = 1.3
            elif sent_local > sent_global:
                mult = 0.1
    return mult * sent_strength_boost


def check_sentiment_boost_augment(
    store,
    bar_idx: int,
    pos_state,
    ts_i: int,
    cfg,
) -> Optional[Dict[str, Any]]:
    """Check whether a SENTIMENT_BOOST augment should fire for an open position.

    Parameters
    ----------
    store : _NPZStore
        The indicator store for this symbol.
    bar_idx : int
        Current bar index.
    pos_state : _PositionState
        Must have: open, side, gain_pct, qty, entry_price, entry_ts.
        Extended fields (added by sentiment path): last_augment_ts (float, default 0).
    ts_i : int
        Current bar Unix timestamp (seconds).
    cfg : VecConfig
        Config with SENTIMENT_BOOST_ENABLED, SENTIMENT_BOOST_DEVIATION_PCT,
        SENTIMENT_BOOST_MIN_GAIN_PCT, SENTIMENT_BOOST_COOLDOWN_SEC,
        SENTIMENT_BOOST_MULT, START_POSITION_SIZE.

    Returns
    -------
    dict with keys: reason, qty_mult — or None if not firing.
    reason: str  — matches live "SENTIMENT_BOOST ideal=N cur=N loc=X.X" format.
    qty_mult: float — multiplier to apply to base_qty for the augment (≥ 1.0).
    """
    if not getattr(cfg, "SENTIMENT_BOOST_ENABLED", False):
        return None
    if not pos_state.open:
        return None

    # ── Cooldown gate (mirrors REBAL_ATTEMPT_COOLDOWN_SEC=300 + 30-min cooldown) ──
    cooldown_sec = float(getattr(cfg, "SENTIMENT_BOOST_COOLDOWN_SEC", 1800.0))
    last_aug_ts = float(getattr(pos_state, "last_augment_ts", 0.0))
    if last_aug_ts > 0 and (ts_i - last_aug_ts) < cooldown_sec:
        return None

    # ── PnL gate (mirrors "Only add if slightly green": pnl > 0.5) ──
    min_gain = float(getattr(cfg, "SENTIMENT_BOOST_MIN_GAIN_PCT", 0.5))
    if pos_state.gain_pct <= min_gain:
        return None

    # ── Market sentiment score from NPZ (cross-symbol breadth, 0-100, 50=neutral) ──
    mss = float(store.f("market_sentiment_score", bar_idx, 50.0))
    # Map NPZ 0-100 to live sent_global range (-100..+100)
    sent_global = (mss - 50.0) * 2.0
    # sent_local: NOT in NPZ per CLAUDE.md (runtime-only). Use mss-based proxy.
    # Live `0market_sentiment_local` = per-stock score from scraper.
    # Approximation: use same mss (conservative — no stock-specific signal).
    sent_local = sent_global

    is_long = pos_state.side == "LONG"
    s_mult = _sentiment_mult_from_score(sent_global, sent_local, is_long)

    # ── Ideal qty estimation ──
    start_size = float(getattr(cfg, "SENTIMENT_BOOST_START_POSITION_SIZE", 600.0))
    price = store.price(bar_idx)
    if price <= 0:
        return None
    base_qty = start_size / price
    ideal_qty = base_qty * s_mult
    current_qty = pos_state.qty

    if current_qty <= 0:
        return None

    deviation_pct = (ideal_qty - current_qty) / current_qty
    deviation_threshold = float(getattr(cfg, "SENTIMENT_BOOST_DEVIATION_PCT", 0.25))

    if deviation_pct <= deviation_threshold:
        return None

    qty_to_add = ideal_qty - current_qty
    if qty_to_add * price < 100:
        return None

    reason = (
        f"SENTIMENT_BOOST ideal={int(ideal_qty)} cur={int(current_qty)} "
        f"loc={sent_local:.1f}"
    )
    return {
        "reason": reason,
        "qty_to_add": qty_to_add,
        "qty_mult": qty_to_add / max(current_qty, 1.0),
        "ideal_qty": ideal_qty,
        "sent_score": mss,
    }
