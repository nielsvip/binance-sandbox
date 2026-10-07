"""
vec_paths/ratio_reduce.py — RATIO_REDUCE portfolio rebalancing path (vectorized).

LIVE SOURCE:
  ez_manage.py:~28693 ratio_rebalance_loop() — a 30-second async loop that:
    1. Computes market breadth from `_market_breadth_signal` (bullish_count / total).
    2. Amplifies the breadth signal by RATIO_MULTIPLIER (3.0x default) around 0.5.
    3. Derives target_long / target_short allocation percentages from the signal.
    4. If the CURRENT ratio of open LONGs:SHORTs is more skewed than the target,
       selects up to 3 of the over-represented side (lowest-gain profitable positions
       first) and queues REDUCE actions.
    5. Reason string in live: RATIO_REDUCE_{side}_L{l}_S{s}_tgt{tl}/{ts}_breadth{b}_gain{g}

  See also ez_manage.py:17289–17292 (RATIO_REDUCE annotation in position logs).

WHY IT MATTERS FOR BACKTEST ACCURACY:
  The ratio_rebalance_loop fires 85+ REDUCE events and 22+ REDUCE events every 30 days
  in live (per PENDING_TASKS.md P3-#1/#2). In the backtest engine these closes are
  MISSING — the position accumulates unrealised PnL without ever being trimmed.
  This creates upward PnL bias: positions that should have been closed at small gains
  (or loss) survive until the next technical exit, which may never come.

VEC APPROXIMATION LIMITATIONS:
  The live ratio_rebalance_loop uses:
    - Real-time market breadth signal (L/S ratio across the WHOLE live universe).
    - PnL-weighted rebalance signal (compares avg LONG PnL vs avg SHORT PnL).
    - Per-account cooldown timers (3600s normal, 300s crash, 600s extreme).
    - Stochastic k_1h cross trigger for immediate rebalance.

  In this vectorized approximation we use ONLY the simple L/S count ratio vs
  RATIO_MULTIPLIER, without breadth signal or PnL weighting. This is the
  dominant term in the live logic and matches the 85/22 event split well.
  The approximation may fire slightly differently from live in mixed-signal
  breadth conditions, but the structural effect (trimming over-represented side)
  is correctly replicated.

PORTFOLIO-LEVEL NOTE:
  This module is portfolio-level: it takes pool counts (how many LONGs and SHORTs
  are currently open across all symbols), not per-position NPZ store fields.
  There is therefore NO `store` or `bar_idx` parameter in the scalar functions.
  The vec function operates on count arrays across bars.

CONFIG KEYS (mirrored from config.py):
  RATIO_REBALANCE_ENABLED: bool = True          (globally enables the path)
  RATIO_MULTIPLIER: float = 3.0                  (skew threshold — live BC_253: 3.0x)
  RATIO_REDUCE_PREFER_LOWEST_GAIN: bool = True   (prefer closing weakest-gain positions
                                                   from the over-represented side;
                                                   matches live sort key)
  RATIO_REDUCE_MAX_CLOSES: int = 3               (max closes per rebalance cycle)
  RATIO_REDUCE_REQUIRE_PROFIT: bool = False      (if True, only close profitable positions;
                                                   live has gain > 0.1 guard but that
                                                   is STRICT_NO_LOSS dependent — default
                                                   False here because STRICT_NO_LOSS is
                                                   eliminated per config STATE OF AFFAIRS)

PUBLIC API:
  check_ratio_reduce(pool_long_count, pool_short_count, pos_side, mode, cfg)
      → Optional[dict]   — per-position scalar check

  select_ratio_reduce_targets(positions, pool_long_count, pool_short_count, mode, cfg)
      → list[str]        — returns position_keys of positions that should be closed,
                            sorted lowest-gain first (matches live _reduce_candidates sort)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _ratio_exceeds_multiplier(
    pool_long_count: int,
    pool_short_count: int,
    ratio_mult: float,
) -> str:
    """Return the over-represented side ("LONG", "SHORT") or "" if balanced.

    Mirrors the logic at ez_manage.py:28987–29021:
      if long_pct > target_long → LONG is overweight
      if short_pct > target_short → SHORT is overweight

    Simplified: if L / S > ratio_mult → LONG heavy; S / L > ratio_mult → SHORT heavy.
    Edge case: if one side is 0 and the other has positions, that side is
    infinitely overweight — treat as over-represented.
    """
    if pool_long_count <= 0 and pool_short_count <= 0:
        return ""
    if pool_short_count <= 0 and pool_long_count > 0:
        return "LONG"
    if pool_long_count <= 0 and pool_short_count > 0:
        return "SHORT"
    if pool_long_count / pool_short_count > ratio_mult:
        return "LONG"
    if pool_short_count / pool_long_count > ratio_mult:
        return "SHORT"
    return ""


def check_ratio_reduce(
    pool_long_count: int,
    pool_short_count: int,
    pos_side: str,
    mode: str,
    cfg: Any,
) -> Optional[Dict[str, Any]]:
    """Check whether this position's side is over-represented and should be reduced.

    This is a per-position check: it answers "should THIS position be a reduce
    candidate?" based on the portfolio-level L/S counts. The caller then applies
    additional tie-breaking (lowest-gain first) via select_ratio_reduce_targets().

    Args:
        pool_long_count:  Number of open LONG positions in the pool right now.
        pool_short_count: Number of open SHORT positions in the pool right now.
        pos_side:         This position's side — "LONG" or "SHORT".
        mode:             "crypto" or "tradier" (informational, not used in logic).
        cfg:              VecConfig with RATIO_REBALANCE_ENABLED, RATIO_MULTIPLIER, etc.

    Returns:
        dict if this position is a reduce candidate, None if balanced or disabled.
        dict keys:
          side       — pos_side (echoed back)
          reason     — RATIO_REDUCE reason string (matches live format)
          close      — True (full close; live sends REDUCE but then full-closes via 50% queue)
          frac       — float — 1.0 (close the full position, matching live REDUCE→close flow)
    """
    if not getattr(cfg, "RATIO_REBALANCE_ENABLED", True):
        return None
    ratio_mult = float(getattr(cfg, "RATIO_MULTIPLIER", 3.0))
    overweight_side = _ratio_exceeds_multiplier(pool_long_count, pool_short_count, ratio_mult)
    if not overweight_side:
        return None
    if pos_side != overweight_side:
        return None
    if pool_short_count > 0:
        ratio_val = pool_long_count / pool_short_count
    elif pool_long_count > 0:
        ratio_val = float(pool_long_count)
    else:
        ratio_val = 0.0
    reason = (
        f"RATIO_REDUCE_{overweight_side}"
        f"_L{pool_long_count}_S{pool_short_count}"
        f"_ratio{ratio_val:.2f}"
    )
    return {
        "side": pos_side,
        "reason": reason,
        "close": True,
        "frac": 1.0,
    }


def select_ratio_reduce_targets(
    positions: Dict[str, Any],
    pool_long_count: int,
    pool_short_count: int,
    mode: str,
    cfg: Any,
) -> List[str]:
    """Select which position_keys should be reduced to fix the L/S imbalance.

    Takes the full positions dict and returns a sorted list of position_keys
    that belong to the over-represented side, ordered lowest-gain first
    (matching the live sort key: _reduce_candidates.sort(key=lambda x: x[2])).

    Args:
        positions:        Dict mapping position_key → position object.
                          Position objects must have .gain_pct (float) and
                          .positionAmt (float, > 0 = open) attributes.
                          String position_keys must end with "_LONG" or "_SHORT".
        pool_long_count:  Total open LONG count (usually len of LONG positions).
        pool_short_count: Total open SHORT count.
        mode:             "crypto" or "tradier".
        cfg:              VecConfig — same keys as check_ratio_reduce().

    Returns:
        List of position_key strings to close, sorted lowest-gain first.
        Empty list if the ratio is balanced or RATIO_REBALANCE_ENABLED=False.

    EXAMPLE:
        >>> targets = select_ratio_reduce_targets(
        ...     positions=my_positions,
        ...     pool_long_count=9,
        ...     pool_short_count=2,
        ...     mode="crypto",
        ...     cfg=cfg,  # RATIO_MULTIPLIER=3.0 → 9/2=4.5 > 3.0 → LONG overweight
        ... )
        >>> # Returns up to 3 LONG position_keys, lowest-gain first.
    """
    if not getattr(cfg, "RATIO_REBALANCE_ENABLED", True):
        return []
    ratio_mult = float(getattr(cfg, "RATIO_MULTIPLIER", 3.0))
    overweight_side = _ratio_exceeds_multiplier(pool_long_count, pool_short_count, ratio_mult)
    if not overweight_side:
        return []
    prefer_lowest_gain = getattr(cfg, "RATIO_REDUCE_PREFER_LOWEST_GAIN", True)
    require_profit = getattr(cfg, "RATIO_REDUCE_REQUIRE_PROFIT", False)
    max_closes = int(getattr(cfg, "RATIO_REDUCE_MAX_CLOSES", 3))
    candidates = []
    for pk, pos in positions.items():
        if not pk.endswith(f"_{overweight_side}"):
            continue
        try:
            amt = abs(float(getattr(pos, "positionAmt", 0.0)))
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:
            continue
        try:
            gain = float(getattr(pos, "gain_pct", 0.0))
        except (TypeError, ValueError):
            gain = 0.0
        if require_profit and gain < 0:
            continue
        candidates.append((pk, gain))
    if prefer_lowest_gain:
        candidates.sort(key=lambda x: x[1])
    else:
        candidates.sort(key=lambda x: x[1], reverse=True)
    return [pk for pk, _ in candidates[:max_closes]]
