"""
vec_paths/satoshit.py — SATOSHIT entry signal (vectorized).

LIVE SOURCE:
  ez_manage.py:10116 (LONG path) / ez_manage.py:10397 (SHORT path):
    if getattr(config, 'SATOSHIT_ENTRY_FILTER', True):
        from ez_satoshit import satoshit_entry_signal
        _sat_ok, _sat_votes, _sat_reason = satoshit_entry_signal(indicators, is_long, config)

  tradier_manage.py:10116 / 10397 mirrors the same filter.

  ez_satoshit.satoshit_entry_signal():
    5-vote system on 15m indicators:
      v_rsi  — rsi_15m < SATOSHIT_LONG_RSI_MAX (50) for LONG
      v_bb   — bb_pct_b_1h < SATOSHIT_LONG_BB_PCTB_MAX (0.50) for LONG
      v_ha   — ha_15m bearish/neutral (streak_val < SATOSHIT_LONG_HA_STREAK_MAX=1) for LONG
      v_k    — stoch_k_15m < SATOSHIT_LONG_STOCH_K_MAX (60) for LONG
      v_mfi  — mfi_15m < SATOSHIT_LONG_MFI_MAX (60) for LONG
    Plus HTF gates:
      mfi_D >= SATOSHIT_HTF_MFI_D_MIN (30)
      relative_volume_1h >= SATOSHIT_HTF_RVOL_1H_MIN (0.3)
    votes >= SATOSHIT_MIN_VOTES (3) → entry confirmed

ADDITIVE path:
  This module is ADDITIVE — it returns a score boost amount and vote detail,
  not a hard gate. When SATOSHIT_ENABLED=True the engine adds the boost to entry
  score (matching how config.SATOSHIT_SCORE_BONUS was used in the dead-switch wiring).
  When SATOSHIT_ENTRY_FILTER=True (crypto default), it acts as a GATE (return None
  to block). The VecEngine handles both modes via its cfg fields.

NPZ FIELDS REQUIRED:
  rsi_15m, stoch_k_15m, mfi_15m, ha_15m (decoded to str in _NPZStore)
  bb_pct_b_1h, mfi_D, relative_volume_1h

LIMITATIONS vs live:
  - ha_15m in NPZ is decoded from int via _NPZStore._decode_integers → "red"/"green"/"neutral".
    ha_streak_15m is also available but live ez_satoshit uses the raw ha_15m string value.
  - bb_pct_b_1h is forward-filled (HTF) — same as live indicator cache.
  - No per-position cooldown (live has COOLDOWN_SEC=120 per position_key).
  - No re-entry bonus (live's check_reentry_ready is stateful, not modelled here).

RETURNS (check_satoshit_entry):
  dict with keys:
    side       — "LONG" or "SHORT"
    votes      — int (0–5), number of conditions met
    score_boost — float boost to add to entry score (config.SATOSHIT_SCORE_BONUS)
    reason     — str reason string matching live format
  or None if votes < min_votes or HTF gates fail.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def check_satoshit_entry(
    store,
    bar_idx: int,
    side: str,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """SATOSHIT 5-vote entry signal (crypto + tradier).

    Args:
        store:   _NPZStore for this symbol.
        bar_idx: Current bar index.
        side:    "LONG" or "SHORT".
        mode:    "crypto" or "tradier".
        cfg:     VecConfig with SATOSHIT_* fields.

    Returns:
        dict if signal fires, None otherwise.
        dict keys:
          side, votes, score_boost, reason
    """
    is_long = (side == "LONG")
    rsi_15m = store.f("rsi_15m", bar_idx, 50.0)
    k_15m = store.f("stoch_k_15m", bar_idx, 50.0)
    mfi_15m = store.f("mfi_15m", bar_idx, 50.0)
    ha_15m = store.s("ha_15m", bar_idx)
    bb_pctb_1h = store.f("bb_pct_b_1h", bar_idx, 0.5)
    mfi_D = store.f("mfi_D", bar_idx, 50.0)
    rvol_1h = store.f("relative_volume_1h", bar_idx, 1.0)
    min_votes = getattr(cfg, "SATOSHIT_MIN_VOTES", 3)
    if is_long:
        ha_streak_val = -1 if ha_15m == "red" else (1 if ha_15m == "green" else 0)
        v_rsi = int(rsi_15m < getattr(cfg, "SATOSHIT_LONG_RSI_MAX", 50.0))
        v_bb = int(bb_pctb_1h < getattr(cfg, "SATOSHIT_LONG_BB_PCTB_MAX", 0.50))
        v_ha = int(ha_streak_val < getattr(cfg, "SATOSHIT_LONG_HA_STREAK_MAX", 1))
        v_k = int(k_15m < getattr(cfg, "SATOSHIT_LONG_STOCH_K_MAX", 60.0))
        v_mfi = int(mfi_15m < getattr(cfg, "SATOSHIT_LONG_MFI_MAX", 60.0))
    else:
        ha_streak_val = 1 if ha_15m == "green" else (-1 if ha_15m == "red" else 0)
        v_rsi = int(rsi_15m > getattr(cfg, "SATOSHIT_SHORT_RSI_MIN", 55.0))
        v_bb = int(bb_pctb_1h > getattr(cfg, "SATOSHIT_SHORT_BB_PCTB_MIN", 0.55))
        v_ha = int(ha_streak_val > getattr(cfg, "SATOSHIT_SHORT_HA_STREAK_MIN", 0))
        v_k = int(k_15m > getattr(cfg, "SATOSHIT_SHORT_STOCH_K_MIN", 50.0))
        v_mfi = int(mfi_15m > getattr(cfg, "SATOSHIT_SHORT_MFI_MIN", 50.0))
    votes = v_rsi + v_bb + v_ha + v_k + v_mfi
    if votes < min_votes:
        return None
    htf_mfi_min = getattr(cfg, "SATOSHIT_HTF_MFI_D_MIN", 30.0)
    if mfi_D < htf_mfi_min:
        return None
    htf_rvol_min = getattr(cfg, "SATOSHIT_HTF_RVOL_1H_MIN", 0.3)
    if rvol_1h < htf_rvol_min:
        return None
    vote_detail = (
        ("R" if v_rsi else ".")
        + ("B" if v_bb else ".")
        + ("H" if v_ha else ".")
        + ("K" if v_k else ".")
        + ("M" if v_mfi else ".")
    )
    score_boost = float(getattr(cfg, "SATOSHIT_SCORE_BONUS", 30))
    if is_long:
        reason = (
            f"SATOSHIT_LONG_v{votes}of5[{vote_detail}]"
            f"_rsi{rsi_15m:.0f}_k{k_15m:.0f}_mfi15m{mfi_15m:.0f}"
            f"_ha{ha_15m}_bb1h{bb_pctb_1h:.2f}_mfiD{mfi_D:.0f}_rv{rvol_1h:.1f}"
        )
    else:
        reason = (
            f"SATOSHIT_SHORT_v{votes}of5[{vote_detail}]"
            f"_rsi{rsi_15m:.0f}_k{k_15m:.0f}_mfi15m{mfi_15m:.0f}"
            f"_ha{ha_15m}_bb1h{bb_pctb_1h:.2f}_mfiD{mfi_D:.0f}_rv{rvol_1h:.1f}"
        )
    return {
        "side": side,
        "votes": votes,
        "score_boost": score_boost,
        "reason": reason,
    }
