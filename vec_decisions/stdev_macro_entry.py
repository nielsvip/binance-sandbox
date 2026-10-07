"""STDEV_MACRO entry vec twin — faithful numpy port of stdev_macro.py.

LIVE SOURCE: compute_stdev_macro_state (reads auto-tuned bb_pct_b_D/4h —
present in NPZ as bb_pct_b_D/bb_pct_b_4h, verified S1) → derive_state 5-state
classifier (STRONG_TOP/TOP/MID/BOT/STRONG_BOT, TF-conflict → MID) →
entry_veto (block OPEN+AUGMENT at same-side STRONG extreme) /
entry_size_boost (×BOOST_MULT entering AGAINST the extreme).

Transcription notes:
- derive_state ported operator-for-operator (strict >/<, strong-D + top-4h
  conjunction, conflict→MID). Non-finite → 0.5 (live: math.isfinite guard).
- data_present heuristic ported exactly: (d != 0.5) | (h4 != 0.5).
  All-False when no data (fail-open), matching live.
- Thresholds from cfg (STDEV_MACRO_{TOP,STRONG_TOP,BOT,STRONG_BOT}_PCTB,
  defaults 0.90/0.97/0.10/0.03) so sweeps vary them — same as live
  _read_thresholds.
- Boost is a per-bar multiplier array (1.0 default); the sizing lane applies
  it at the open. Veto is a block mask for final entry_sig + _open choke.

Switches: STDEV_MACRO_ENTRY_VETO_ENABLED (default False),
STDEV_MACRO_ENTRY_BOOST_ENABLED (False), STDEV_MACRO_ENTRY_BOOST_MULT (1.3).
"""
from __future__ import annotations

import numpy as np


def _derive_state_vec(pctb_d, pctb_4h, top, strong_top, bot, strong_bot):
    d = np.where(np.isfinite(pctb_d), pctb_d, 0.5)
    h = np.where(np.isfinite(pctb_4h), pctb_4h, 0.5)
    d_top = d > top
    d_bot = d < bot
    h_top = h > top
    h_bot = h < bot
    conflict = (d_top & h_bot) | (d_bot & h_top)
    strong_top_m = (d > strong_top) & (h > top) & ~conflict
    strong_bot_m = (d < strong_bot) & (h < bot) & ~conflict
    return strong_top_m, strong_bot_m


def _state(npz, n, cfg, safe):
    pctb_d = safe(npz, "bb_pct_b_D", n, 0.5)
    pctb_4h = safe(npz, "bb_pct_b_4h", n, 0.5)
    top = float(getattr(cfg, "STDEV_MACRO_TOP_PCTB", 0.90))
    strong_top = float(getattr(cfg, "STDEV_MACRO_STRONG_TOP_PCTB", 0.97))
    bot = float(getattr(cfg, "STDEV_MACRO_BOT_PCTB", 0.10))
    strong_bot = float(getattr(cfg, "STDEV_MACRO_STRONG_BOT_PCTB", 0.03))
    st, sb = _derive_state_vec(pctb_d, pctb_4h, top, strong_top, bot, strong_bot)
    present = (pctb_d != 0.5) | (pctb_4h != 0.5)
    return (st & present), (sb & present)


def entry_veto_mask(npz, n, is_long, cfg, safe):
    """Bool[n] True = BLOCK the open. None when disabled (passthrough)."""
    if not bool(getattr(cfg, "STDEV_MACRO_ENTRY_VETO_ENABLED", False)):
        return None
    st, sb = _state(npz, n, cfg, safe)
    return np.asarray(st if is_long else sb, dtype=bool)


def entry_boost_mult(npz, n, is_long, cfg, safe):
    """Float[n] size multiplier (1.0 default). None when disabled."""
    if not bool(getattr(cfg, "STDEV_MACRO_ENTRY_BOOST_ENABLED", False)):
        return None
    st, sb = _state(npz, n, cfg, safe)
    mult = float(getattr(cfg, "STDEV_MACRO_ENTRY_BOOST_MULT", 1.3))
    against = sb if is_long else st  # LONG against extreme = at STRONG_BOT
    return np.where(against, mult, 1.0).astype(np.float64)
