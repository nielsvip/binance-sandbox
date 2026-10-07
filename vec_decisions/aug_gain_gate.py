"""AUG_GAIN_GATE — vec twin of the live execute_trade_action AUGMENT gain gate (ez_manage.py ~24930-24997, crypto).

LIVE (every AUGMENT on an existing position whose value exceeds MIN_POSITION_SIZE):
  gain < 0                                   -> BLOCKED_LOSER_KILL (never augment a loser)
  gain < thr                                 -> BLOCKED_GAIN_GATE
  thr = MIN_GAIN (3.0); thr = 0.5*MIN_GAIN only at a BOUNCE:
        price beyond the position's last reduction price (LONG below / SHORT above)
    AND wt1_1h vs wt2_1h aligned with the side
    AND 15m wt structure: LONG trough 'HL' / SHORT peak 'LH'
This is a second gate on top of UNIVERSAL_AUGMENT_GAIN_GATE (vec_decisions/uagain_gate). The vec modelled only the first, so the
PULLBACK_AUG ladder augmented at 1.5-3% where live refuses (parity-loop-crypto it13 SNX: BLOCKED_GAIN_GATE_2.14%_lt_3.00%).
NPZ encodes wt_trough_structure_15m / wt_peak_structure_15m as int8 (+1 HL / -1 LL, +1 HH / -1 LH; vec_decisions/grey_wire_exits).
"""
from __future__ import annotations


def threshold(cfg, is_long: bool, px: float, last_exit_px: float, wt1_1h: float, wt2_1h: float, trough_15m: float, peak_15m: float) -> float:
    thr = float(getattr(cfg, "MIN_GAIN", 3.0) or 3.0)
    below = last_exit_px > 0 and ((is_long and px < last_exit_px) or ((not is_long) and px > last_exit_px))
    h1 = (wt1_1h > wt2_1h) if is_long else (wt1_1h < wt2_1h)
    bounce = (trough_15m > 0) if is_long else (peak_15m < 0)
    return thr * 0.5 if (below and h1 and bounce) else thr


def passes(cfg, is_long: bool, px: float, raw_gain_pct: float, pos_value: float, last_exit_px: float,
           wt1_1h: float, wt2_1h: float, trough_15m: float, peak_15m: float) -> bool:
    if pos_value <= float(getattr(cfg, "MIN_POSITION_SIZE", 1.0) or 1.0):
        return True  # live foothold branch (separate pile-on rules), not the gain gate
    if raw_gain_pct < 0.0:
        return False
    return raw_gain_pct >= threshold(cfg, is_long, px, last_exit_px, wt1_1h, wt2_1h, trough_15m, peak_15m)
