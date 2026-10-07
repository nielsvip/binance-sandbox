"""WT_DIV_ENTRY_GATE vec twin — faithful numpy port of the rate() R-G7 gate.

LIVE SOURCE (ez_positions_quick.py:2317-2322, hard BOYCOTT, default True):
  LONG blocked when ind['wt_any_bear_div']; SHORT when ind['wt_any_bull_div'].
Live flags aggregate ez_indicators.detect_divergence strings per TF over
(3m,15m,1h,4h,D): BEAR = div in (BEAR,HIDDEN_BEAR) — same lookback=5/decay=10
pivot method as the NPZ div_reg/div_hid_wt_* flags (backtest_v8_precompute
Improvement-Framework-A4 writer). The twin aggregates THOSE flags (reg+hid =
live's BEAR+HIDDEN set), NOT wt_any_* (structure-proxy method — differs).

15m floor: live's 3m term has no NPZ array → dropped (USER 2026-10-01: 3m
terms IGNORED, never proxied). TF set: 15m/1h/4h/D (+W/M arrays exist but
live does not aggregate them → excluded for parity).

PLACEMENT: veto mask on final entry_sig + _open-choke veto (REQUIRE-style
gate — ported_entry 2026-10-02 NOTE). Live default True = gate ACTIVE, so
landing this twin reduces vec opens toward live (T2 post-cut must show
fewer opens vs twin-off, and twin-off must reproduce the pre-cut fp).
"""
from __future__ import annotations

import numpy as np

_TFS = ("15m", "1h", "4h", "D")


def div_block_mask(npz, n, is_long, cfg, safe):
    """Bool[n] True = BLOCK the open. None when disabled (passthrough)."""
    if not bool(getattr(cfg, "WT_DIV_ENTRY_GATE_ENABLED", True)):
        return None
    bear = np.zeros(n, dtype=bool)
    bull = np.zeros(n, dtype=bool)
    for tf in _TFS:
        rb = safe(npz, f"div_reg_bear_wt_{tf}", n, 0.0)
        hb = safe(npz, f"div_hid_bear_wt_{tf}", n, 0.0)
        ru = safe(npz, f"div_reg_bull_wt_{tf}", n, 0.0)
        hu = safe(npz, f"div_hid_bull_wt_{tf}", n, 0.0)
        bear |= (rb > 0) | (hb > 0)
        bull |= (ru > 0) | (hu > 0)
    block = bear if is_long else bull
    return np.asarray(block, dtype=bool)
