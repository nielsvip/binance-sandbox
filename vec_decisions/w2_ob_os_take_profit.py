"""w2-exits staged twin: Overbought/Oversold Take-Profit REDUCEs.

Live source: tradier_manage.py:14890-14909 (periodic override_check).
  LONG : k_5m>85 & rsi_5m>75 & k_5m<d_5m (rolling over) & gain>2.0 & min-hold
         -> REDUCE 25% "Overbought_Take_Profit_Gain_{g}%"
  SHORT: k_5m<15 & rsi_5m<25 & k_5m>d_5m & gain>2.0 & min-hold
         -> REDUCE 25% "Oversold_Take_Profit_Gain_{g}%"

Vec status: HONEST ZERO on current NPZs — k_5m/rsi_5m/d_5m are ABSENT from
stock NPZs (verified S1 MSFT.npz 1155 keys; local AAPL.npz 940 keys) and the
LG-13 USER ORDER forbids 5m approximation ("needs 5m, ignored", never
substitute another TF). The predicate below is the faithful twin, staged for
the cut + a 5m-stoch NPZ regen; until then it fails closed (all-zero inputs
can never satisfy k>85 / k>d-with-k<15... — proven, not assumed).
Scalar walk predicate + ONE call site in the REDUCE section.

Min-hold delta (disclosed): live gates on the override-loop min hold
(TRADIER_MIN_HOLD_MINUTES=4320 dominates); vec passes the engine walk
min_hold (held_bars >= MIN_HOLD_BARS_BEFORE_EXIT). Same shape (no exit
before min hold), different clock.
"""
from __future__ import annotations


def ob_os_tp_reduce_qty(pos_qty: float, gain_pct: float, k5: float, rsi5: float,
                         d5: float, is_long: bool, min_hold_ok: bool,
                         cfg=None):
    """Return (reduce_qty, reason). gain_pct in percent (2.0 = 2%)."""
    try:
        if cfg is not None and not bool(getattr(cfg, "W2_OB_OS_TP_ENABLED", False)):
            return 0.0, ""
        if not min_hold_ok:
            return 0.0, ""
        if not (gain_pct is not None and float(gain_pct) > 2.0):
            return 0.0, ""
        if not (pos_qty and float(pos_qty) > 0):
            return 0.0, ""
        k = float(k5)
        r = float(rsi5)
        d = float(d5)
        fire = False
        if is_long:
            fire = (k > 85.0) and (r > 75.0) and (k < d)
        else:
            fire = (k < 15.0) and (r < 25.0) and (k > d)
        if not fire:
            return 0.0, ""
        qty = max(1, int(float(pos_qty) * 0.25))  # live :14895/:14904
        tag = "Overbought_Take_Profit" if is_long else "Oversold_Take_Profit"
        return float(qty), f"{tag}_Gain_{float(gain_pct):.1f}%"
    except Exception:
        return 0.0, ""
