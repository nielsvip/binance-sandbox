"""
golden_rule_htf.py — Multi-timeframe indicator confirmation gate.

The GOLDEN RULE: a trade signal only fires when at least MIN_TFS timeframes
each show at least MIN_IND bullish (or bearish for shorts/exits) indicators.

5 indicators checked per TF:
  1. WT   — wt1 > wt2 (bullish) / wt1 < wt2 (bearish)
  2. RSI  — rsi > 50 (bullish) / rsi < 50 (bearish)
  3. MFI  — mfi > 50 (bullish) / mfi < 50 (bearish)
  4. DC   — dc_position < 0.65 (not extended, room to run)  / > 0.35
  5. BB   — bb_pct_b < 0.75 (not at top of band) / > 0.25

TFs checked:
  crypto : 3m, 15m, 1h, 4h, D
  tradier: 5m, 15m, 1h, 4h, D, W  (W = weekly context for stocks)

Usage:
  from golden_rule_htf import score_entry_htf, score_exit_htf

  passes, n_tfs, detail = score_entry_htf(indicators, is_long, mode='tradier',
                                           min_tfs=2, min_ind=2)
  if not passes:
      return "NO_ACTION", f"GOLDEN_RULE_HTF {detail}", 0.0, 0.0
"""

from __future__ import annotations
from typing import Any

_CRYPTO_TFS = ["3m", "15m", "1h", "4h", "D"]
_TRADIER_TFS = ["5m", "15m", "1h", "4h", "D", "W"]

_DC_EXTENDED_LONG = 0.65
_DC_EXTENDED_SHORT = 0.35
_BB_EXTENDED_LONG = 0.75
_BB_EXTENDED_SHORT = 0.25


def _ind_score(ind: dict, tf: str, is_long: bool, px: float) -> tuple[int, str]:
    """Return (n_bullish_indicators, detail_str) for a single timeframe."""
    score = 0
    parts = []

    wt1 = float(ind.get(f"wt1_{tf}") or 0)
    wt2 = float(ind.get(f"wt2_{tf}") or 0)
    if wt1 != 0 or wt2 != 0:
        ok = wt1 > wt2 if is_long else wt1 < wt2
        score += int(ok)
        parts.append(f"WT{'✓' if ok else '✗'}")

    rsi = float(ind.get(f"rsi_{tf}") if ind.get(f"rsi_{tf}") is not None else -1)
    if rsi >= 0:
        ok = rsi > 50 if is_long else rsi < 50
        score += int(ok)
        parts.append(f"RSI{'✓' if ok else '✗'}{rsi:.0f}")

    mfi = float(ind.get(f"mfi_{tf}") if ind.get(f"mfi_{tf}") is not None else -1)
    if mfi >= 0:
        ok = mfi > 50 if is_long else mfi < 50
        score += int(ok)
        parts.append(f"MFI{'✓' if ok else '✗'}{mfi:.0f}")

    dc_pos = float(ind.get(f"dc_position_{tf}") if ind.get(f"dc_position_{tf}") is not None else -1)
    if dc_pos < 0:
        dc_h = float(ind.get(f"dc_high_{tf}") or 0)
        dc_l = float(ind.get(f"dc_low_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if dc_h > dc_l > 0 and ref_px > 0:
            dc_pos = (ref_px - dc_l) / (dc_h - dc_l)
    if dc_pos >= 0:
        ok = dc_pos < _DC_EXTENDED_LONG if is_long else dc_pos > _DC_EXTENDED_SHORT
        score += int(ok)
        parts.append(f"DC{'✓' if ok else '✗'}{dc_pos:.2f}")

    bb_pctb = float(ind.get(f"bb_pct_b_{tf}") if ind.get(f"bb_pct_b_{tf}") is not None else -1)
    if bb_pctb < 0:
        bb_u = float(ind.get(f"bb_upper_{tf}") or 0)
        bb_l = float(ind.get(f"bb_lower_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if bb_u > bb_l > 0 and ref_px > 0:
            bb_pctb = (ref_px - bb_l) / (bb_u - bb_l)
    if bb_pctb >= 0:
        ok = bb_pctb < _BB_EXTENDED_LONG if is_long else bb_pctb > _BB_EXTENDED_SHORT
        score += int(ok)
        parts.append(f"BB{'✓' if ok else '✗'}{bb_pctb:.2f}")

    return score, "|".join(parts)


def _run_gate(
    ind: dict,
    is_long: bool,
    mode: str,
    min_tfs: int,
    min_ind: int,
    px: float = 0.0,
) -> tuple[bool, int, str]:
    """Core gate shared by entry and exit checks."""
    if min_tfs <= 0:
        return True, 0, "GATE_OFF"
    tfs = _TRADIER_TFS if mode == "tradier" else _CRYPTO_TFS
    confirmed = 0
    parts = []
    for tf in tfs:
        n, detail = _ind_score(ind, tf, is_long, px)
        ok = n >= min_ind
        if ok:
            confirmed += 1
        parts.append(f"{tf}:{n}/{min_ind}{'✓' if ok else ''}")
    passes = confirmed >= min_tfs
    summary = f"tfs={confirmed}/{min_tfs}req [{' '.join(parts)}]"
    return passes, confirmed, summary


def score_entry_htf(
    indicators: dict,
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    current_price: float = 0.0,
) -> tuple[bool, int, str]:
    """
    Entry confirmation: requires MIN_TFS timeframes to each have MIN_IND bullish indicators.
    Returns (passes, n_confirmed_tfs, detail).
    """
    return _run_gate(indicators, is_long, mode, min_tfs, min_ind, current_price)


def score_exit_htf(
    indicators: dict,
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    current_price: float = 0.0,
) -> tuple[bool, int, str]:
    """
    Exit confirmation: requires MIN_TFS timeframes to show BEARISH signals (opposite of position).
    Returns (should_exit, n_confirmed_tfs, detail).
    """
    return _run_gate(indicators, not is_long, mode, min_tfs, min_ind, current_price)
