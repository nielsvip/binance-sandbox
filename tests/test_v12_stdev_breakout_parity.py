"""STDEV breakout vec-live parity (USER 2026-10-11 knob#1, ABSOLUTE parity).

Live worker opens breakouts standalone (ez_positions_quick:30373, no pullback/zone
veto before execute); vec vetoed 10/10 PEPE fire bars via ~_bb_pullback_vec. Fix:
re-OR B_STDEV_BREAKOUT after the veto stack (BB/WT_DC precedent).
"""
import numpy as np

import v12_quick_engine as V
from vec_decisions import stdev_breakout_crypto as T


def _fires(sym="1000PEPEUSDC"):
    z = np.load(f"backtest_v8/indicators/{sym}.npz", allow_pickle=True)
    return {k: z[k] for k in z.files}


def test_reor_restores_uncovered_fires():
    npz = _fires()
    n = len(npz["close"])
    off = V.QuickConfig()
    on = V.QuickConfig()
    on.STDEV_BREAKOUT_ENABLED = True
    m0 = np.asarray(V.compute_entry_signals(npz, n, True, off), dtype=bool)
    m1 = np.asarray(V.compute_entry_signals(npz, n, True, on), dtype=bool)
    fires = T.fires(npz, n, True, on, V._safe)
    assert fires is not None
    uncovered = fires & ~m0
    assert int(np.sum((~m0) & m1)) == int(uncovered.sum())  # every uncovered fire bar opens
    assert int(m1.sum()) == int(m0.sum()) + int(uncovered.sum())


def test_no_setup_no_change():
    npz = _fires("ENAUSDC")
    n = len(npz["close"])
    off = V.QuickConfig()
    on = V.QuickConfig()
    on.STDEV_BREAKOUT_ENABLED = True
    fires = T.fires(npz, n, True, on, V._safe)
    m0 = np.asarray(V.compute_entry_signals(npz, n, True, off), dtype=bool)
    m1 = np.asarray(V.compute_entry_signals(npz, n, True, on), dtype=bool)
    if fires is None or int(fires.sum()) == 0:
        assert int(np.sum(m0 != m1)) == 0  # no setup -> parent is a safe no-op (anti-always-fire)
    else:
        assert int(np.sum((~m0) & m1)) == int(np.sum(fires & ~m0))


def test_twin_gate_off_none():
    npz = {"bb_pct_b_D": np.full(10, 1.5), "relative_volume_D": np.full(10, 2.0)}
    assert T.fires(npz, 10, True, V.QuickConfig(), V._safe) is None


def test_synthetic_breakout_phase1():
    n = 10
    pctb = np.full(n, 0.5)
    pctb[5] = 1.5
    npz = {"bb_pct_b_D": pctb, "relative_volume_D": np.full(n, 1.5),
           "bb_pct_b_1h": np.full(n, 0.5), "bb_pct_b_15m": np.full(n, 0.5)}
    cfg = V.QuickConfig()
    cfg.STDEV_BREAKOUT_ENABLED = True
    out = T.fires(npz, n, True, cfg, V._safe)
    assert out is not None and bool(out[5]) and int(out.sum()) >= 1


def test_live_twin_same_bar(monkeypatch):
    import ez_positions_quick as epq
    monkeypatch.setattr(epq.config, "STDEV_BREAKOUT_ENABLED", True)
    sym = "PARITY_PROBE_SB"
    epq._stdev_breakout_state.pop(sym, None)
    ind = {"bb_pct_b_D": 1.5, "relative_volume_D": 1.5}
    live = epq.detect_stdev_breakout(sym, True, ind, {})
    assert live is not None and live["signal"] == "BUY" and live["phase"] == "BREAKOUT"
    n = 10
    pctb = np.full(n, 0.5)
    pctb[5] = 1.5
    npz = {"bb_pct_b_D": pctb, "relative_volume_D": np.full(n, 1.5)}
    cfg = V.QuickConfig()
    cfg.STDEV_BREAKOUT_ENABLED = True
    out = T.fires(npz, n, True, cfg, V._safe)
    assert out is not None and bool(out[5])  # twin fires the same bar live opens
    epq._stdev_breakout_state.pop(sym, None)
