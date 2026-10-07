"""WT_DC score-path sub-gates vec lacks — the two live WT_DC_ENTRY sub-gates
with no vector twin (the other sub-gates exist inline at v12:9428-9444 behind
the vec-only WT_DC_LIVE_GATES master; the staged diff wires these two in and
drops the master since live has none).

LIVE SITE (tradier_manage.py:12825-12852, WT_DC final condition 12921):
  _stoch_gate_thr = COMBINED_STOCH_GATE_TRADIER (live 60.0); when < 100:
      LONG blocked when k_5m >= thr; SHORT blocked when k_5m <= 100-thr.
  _gr_htf_enabled = GR_HTF_GATE_ENABLED (live True):
      LONG blocked when wt_bull_alignment < GR_HTF_REQUIRE_BULL (live 1);
      SHORT blocked when wt_bear_alignment < GR_HTF_REQUIRE_BEAR (live 1).

VEC GAP: no reader of either gate constrains B_WT_DC_LIVE. The existing
COMBINED_STOCH twin (v12:9202-9210) ANDs only into extra_ok -> _base_entry,
which B_WT_DC_LIVE bypasses via downstream OR (v12:9649-9650); GR_HTF has no
vec reader at all. Vec B_WT_DC_LIVE fires on score>=45 alone while live
demands score>=45 AND all sub-gates.

5m FALLBACK (MU_GAP 2026-10-06): NPZ has no 5m arrays (precompute TFs are
15m/1h/4h/D/W/M). The stoch twin prefers stoch_k_5m when present, else uses
the scalar 50.0 — exactly live's _entry_ind.get('k_5m', 50) missing-data
default (proven by the S1 parity trace: backtest-live k5m is constant 50).
The old stoch_k_15m floor is REMOVED: it vetoed bars where live fires.
wt_bull/bear_alignment ARE in the stocks NPZ (verified S1 GOOGL).
"""
from __future__ import annotations

import numpy as np


def stoch_block_mask(npz, n, is_long, cfg, safe):
    """Bool[n], True = live COMBINED_STOCH gate would block. None = inert."""
    try:
        thr = float(getattr(cfg, "COMBINED_STOCH_GATE_TRADIER", 100.0))
    except Exception:
        return None
    if not (thr < 100.0):
        return None  # live: gate only when thr < 100
    if "stoch_k_5m" in npz:
        k5 = np.asarray(safe(npz, "stoch_k_5m", n, 50.0), dtype=float)
    else:
        # MU_GAP 2026-10-06: live parity — live reads _entry_ind.get('k_5m', 50)
        # (tradier_manage WT_DC + post-veto COMBINED_STOCH gates); the S1 parity
        # trace proves backtest-live k5m is the constant 50.0 fallback (no 5m
        # series on the frozen NPZ), so the gate never blocks LONG (50 < 60).
        # The old stoch_k_15m floor read hot on 689/1861 MU bars and vetoed
        # B_WT_DC_LIVE where live fired (e.g. live WT_DC_68 bars 1387/1389).
        # Fall back to the scalar 50.0 exactly like live. Live is truth (§17.4).
        # Caveat: the real-time daemon HAS 5m data (gate binds there); this
        # twin targets vec↔scalar-backtest parity on the frozen NPZ.
        k5 = np.full(n, 50.0, dtype=float)
    if is_long:
        blk = k5 >= thr
    else:
        blk = k5 <= (100.0 - thr)
    return np.asarray(blk, dtype=bool)


def gr_htf_block_mask(npz, n, is_long, cfg, safe):
    """Bool[n], True = live GR_HTF gate would block. None = gate off."""
    if not bool(getattr(cfg, "GR_HTF_GATE_ENABLED", False)):
        return None
    try:
        req_bull = int(float(getattr(cfg, "GR_HTF_REQUIRE_BULL", 1)))
    except Exception:
        req_bull = 1
    try:
        req_bear = int(float(getattr(cfg, "GR_HTF_REQUIRE_BEAR", 1)))
    except Exception:
        req_bear = 1
    if is_long:
        bull = np.asarray(safe(npz, "wt_bull_alignment", n, 0), dtype=float)
        blk = bull < req_bull
    else:
        bear = np.asarray(safe(npz, "wt_bear_alignment", n, 0), dtype=float)
        blk = bear < req_bear
    return np.asarray(blk, dtype=bool)
