"""ENTRY_VET vec twin — faithful numpy port of ez_manage.check_entry_vetting.

LIVE SOURCE (ez_manage.py:1067-1127, crypto; stocks: no consumer — crypto only):
  LONG:  dc_breakout = dc_high_3m rising (vs _ant); structure_ok = low_15m higher-low;
         trigger = wt_cross_1m/3m BULL | px > dc_high_3m | (wt1_3m>wt2_3m & k_3m>d_3m)
  SHORT: dc_breakout = dc_low_3m falling; structure_ok = high_15m lower-high;
         trigger = wt_cross BEAR | px < dc_low_3m | (wt1_3m<wt2_3m & k_3m<d_3m)
  Modes (ENTRY_VET_RELAX_MODE, legacy ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED):
    0 = strict (structure AND breakout), 1 = either (OR), 2 = trigger-only,
    3 = auto-pass. Mode != 3 also requires `trigger`.

15m FLOOR (§40; NPZ has no 1m/3m arrays — verified S1): every 3m term is
evaluated on the 15m array (the ported_entry sanctioned floor convention);
the wt_cross_1m term merges into the 15m cross (1m has no floor proxy).
ALL_TF_AGAINST tail of check_entry_vetting is NOT duplicated here — it is
already wired at v12:11929 + fire veto v12:12697.

PLACEMENT (staged): veto mask ANDed into final entry_sig in
ported_entry.apply + precomputed _open-choke veto (rally/watchdog bypass
entry_sig — ported_entry 2026-10-02 NOTE). Default RELAX_MODE=3 = passthrough.
"""
from __future__ import annotations

import numpy as np


def _sstr(npz, key, n, default=""):
    try:
        a = np.asarray(npz.get(key, None))
        if a is None or a.size != n:
            return np.full(n, default, dtype=object)
        return np.asarray(a, dtype=object)
    except Exception:
        return np.full(n, default, dtype=object)


def vet_pass_mask(npz, n, is_long, cfg, close, safe):
    """Bool[n] True = entry vetted (passes). None-style passthrough when mode 3."""
    legacy = bool(getattr(cfg, "ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED", True))
    try:
        mode = int(getattr(cfg, "ENTRY_VET_RELAX_MODE", 1 if legacy else 2))
    except Exception:
        mode = 3
    if mode == 3:
        return None  # auto-pass: no constraint (live default)
    dch = safe(npz, "dc_high_15m", n, 0.0)
    dch_ant = safe(npz, "dc_high_15m_ant", n, 0.0)
    dcl = safe(npz, "dc_low_15m", n, 0.0)
    dcl_ant = safe(npz, "dc_low_15m_ant", n, 0.0)
    low = safe(npz, "low_15m", n, 0.0)
    low_prev = safe(npz, "low_15m_prev", n, 0.0)
    high = safe(npz, "high_15m", n, 0.0)
    high_prev = safe(npz, "high_15m_prev", n, 0.0)
    wtc = _sstr(npz, "wt_cross_15m", n)
    wt1 = safe(npz, "wt1_15m", n, 0.0)
    wt2 = safe(npz, "wt2_15m", n, 0.0)
    k = safe(npz, "stoch_k", n, 50.0)
    d = safe(npz, "stoch_d", n, 50.0)
    px = np.asarray(close, dtype=float)
    if is_long:
        breakout = (dch > 0) & (dch_ant > 0) & (dch > dch_ant)
        structure = (low > 0) & (low_prev > 0) & (low > low_prev)
        trigger = (wtc == "BULL") | ((px > dch) & (dch > 0)) | ((wt1 > wt2) & (k > d))
    else:
        breakout = (dcl > 0) & (dcl_ant > 0) & (dcl < dcl_ant)
        structure = (high > 0) & (high_prev > 0) & (high < high_prev)
        trigger = (wtc == "BEAR") | ((px < dcl) & (dcl > 0)) | ((wt1 < wt2) & (k < d))
    if mode == 0:
        ok = breakout & structure
    elif mode == 1:
        ok = breakout | structure
    else:  # mode 2 (and any other non-3): trigger-only
        ok = np.ones(n, dtype=bool)
    ok = ok & trigger
    return np.asarray(ok, dtype=bool)
