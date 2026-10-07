"""MU_LONG 19-vs-61 gap: stoch twin 5m fallback parity (2026-10-06).

Live reads _entry_ind.get('k_5m', 50) for the COMBINED_STOCH gate; the S1
parity trace proves backtest-live k5m is the constant 50.0 (frozen NPZ has
no 5m series), so the gate never blocks LONG (50 < thr 60). The vec twin
used to floor to stoch_k_15m, which read hot (>=60) on 689/1861 MU bars and
vetoed B_WT_DC_LIVE exactly where live fired (live WT_DC_68 bars 1387/1389).
"""
import numpy as np
from types import SimpleNamespace

from vec_decisions import wtdc_score_gates as G


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(float)
    return np.full(n, default, dtype=float)


def _cfg(**kw):
    d = {"COMBINED_STOCH_GATE_TRADIER": 60.0}
    d.update(kw)
    return SimpleNamespace(**d)


def test_missing_5m_falls_back_to_50_like_live():
    n = 64
    npz = {"stoch_k_15m": np.full(n, 95.0)}  # hot 15m must NOT leak in
    blk = G.stoch_block_mask(npz, n, True, _cfg(), _safe)
    assert blk is not None
    assert int(np.asarray(blk).sum()) == 0  # 50 < 60 -> live never blocks


def test_missing_5m_short_side_also_open():
    n = 64
    npz = {"stoch_k_15m": np.full(n, 5.0)}  # cold 15m must NOT leak in
    blk = G.stoch_block_mask(npz, n, False, _cfg(), _safe)
    assert blk is not None
    assert int(np.asarray(blk).sum()) == 0  # 50 > 100-60 -> no block


def test_present_5m_still_binds():
    n = 64
    npz = {"stoch_k_5m": np.full(n, 75.0)}
    blk = G.stoch_block_mask(npz, n, True, _cfg(), _safe)
    assert int(np.asarray(blk).sum()) == n  # 75 >= 60 -> block


def test_gate_inert_at_100():
    n = 16
    npz = {"stoch_k_5m": np.full(n, 99.0)}
    assert G.stoch_block_mask(npz, n, True, _cfg(COMBINED_STOCH_GATE_TRADIER=100.0), _safe) is None
