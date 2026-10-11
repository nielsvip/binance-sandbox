"""k is NEVER a gate (USER 2026-10-11) — WT decides; vec defines 30D, live never diverges.

Live pre-WT k-vetoes stubbed (ez_manage alignment/trigger/winner-momentum,
tradier chase, K3M_FLOOR default); vec k-vetoes stubbed in lockstep (WTDC_STOCH,
chase, zone honors LIVE_ENABLED). Sacred WT_DC family intact (k sub-veto only).
"""
import numpy as np


def test_config_defaults():
    import config as C
    cfg = C.Config() if hasattr(C, "Config") else C
    assert cfg.K3M_FLOOR_ENABLED is False
    assert cfg.ENTRY_ZONE_LONG_LIVE_ENABLED is False
    assert cfg.ENTRY_ZONE_SHORT_LIVE_ENABLED is False
    assert cfg.STOCH_CROSS_ENTRY_TRADIER is False
    import config_tradier as CT
    tcfg = CT.TradierConfig()
    assert tcfg.STOCH_CROSS_ENTRY_TRADIER is False


def _hostile_long():
    # k hostile everywhere (would trip every pre-WT k-veto), WT aligned, rest passable.
    return {"symbol": "AAPL", "k_1m": 10.0, "d_1m": 90.0, "k_3m": 95.0, "d_3m": 5.0,
            "k_15m": 95.0, "d_15m": 5.0, "k_1h": 95.0, "d_1h": 5.0, "k_4h": 95.0,
            "d_4h": 5.0, "k_D": 10.0, "d_D": 90.0, "ha_D": "neutral",
            "wt1_1h": 60.0, "wt2_1h": 40.0, "wt1_4h": 60.0, "wt2_4h": 40.0,
            "wt1_D": 60.0, "wt2_D": 40.0, "wt1_3m": 60.0, "wt2_3m": 40.0,
            "bb_pct_b_15m": 0.2, "mfi_1h": 50.0, "mfi_4h": 50.0, "close": 100.0,
            "current_price": 100.0}


def test_alignment_no_k_veto():
    from ez_manage import check_entry_alignment
    ok, reason = check_entry_alignment(_hostile_long(), True)
    for marker in ("LTF_ALIGN_FAIL", "HTF_ALIGN_FAIL", "D_ALIGN_MANDATORY", "K3M_CAP",
                   "K3M_FLOOR", "WTDC_STOCH_VETO", "K5M_VETO", "STOCH_CROSS_VETO"):
        assert marker not in str(reason), reason


def test_trigger_wt_only():
    from ez_manage import check_entry_trigger
    base = {"k_1m": 10.0, "d_1m": 90.0, "d_1m_prev": 90.0, "k_1m_prev": 10.0,
            "k_3m": 10.0, "d_3m": 90.0, "k_3m_prev": 10.0, "k_15m": 50.0,
            "rsi_1h": 40.0, "rsi_4h": 40.0, "wt1_3m": 60.0, "wt2_3m": 40.0,
            "wt_cross_1m": "BULL", "wt_cross_3m": "BULL"}
    ok, _ = check_entry_trigger(dict(base), True)
    assert ok is True  # WT cross opens despite hostile k
    # k crossed on 1m+3m but NO WT cross -> blocked (pre-change the k-fallback opened)
    crossed = {"k_1m": 60.0, "d_1m": 50.0, "k_1m_prev": 40.0, "d_1m_prev": 55.0,
               "k_3m": 60.0, "d_3m": 50.0, "k_3m_prev": 40.0, "k_15m": 50.0,
               "rsi_1h": 40.0, "rsi_4h": 40.0, "wt1_3m": 60.0, "wt2_3m": 40.0}
    nok, rs = check_entry_trigger(crossed, True)
    assert nok is False and "TRIGGER_BLOCKED" in rs


def test_winner_momentum_k_stub():
    from ez_manage import check_winner_momentum
    ind = {"k_15m": 95.0, "wt1_15m": 60.0, "wt2_15m": 40.0, "high_3m": 99.0,
           "high_3m_prev": 100.0, "low_3m": 0.0, "low_3m_prev": 0.0}
    hold, reason = check_winner_momentum(ind, True, 0.0)
    assert "EXHAUSTED" not in str(reason) and hold is True


def _npz():
    for s in ("1000PEPEUSDC", "ENAUSDC"):
        try:
            z = np.load(f"backtest_v8/indicators/{s}.npz", allow_pickle=True)
            return {k: z[k] for k in z.files}
        except Exception:
            continue
    raise RuntimeError("no NPZ")


def test_vec_zone_honors_switch():
    import v12_quick_engine as V
    npz = _npz()
    n = len(npz["close"])
    dflt = V.QuickConfig()
    assert dflt.ENTRY_ZONE_LONG_LIVE_ENABLED is False
    on = V.QuickConfig()
    on.ENTRY_ZONE_LONG_LIVE_ENABLED = True
    on.ENTRY_ZONE_LONG = 80.0
    m0 = np.asarray(V.compute_entry_signals(npz, n, True, dflt), dtype=bool)
    m1 = np.asarray(V.compute_entry_signals(npz, n, True, on), dtype=bool)
    assert int(np.sum(m1 & ~m0)) == 0  # gate only removes
    k1h = np.asarray(V._safe(npz, "stoch_k_1h", n, 50), dtype=float)
    removed = m0 & ~m1
    if int(removed.sum()):
        assert bool(np.all(k1h[removed] > 80.0))  # everything removed is k>80


def test_vec_chase_stubbed():
    import v12_quick_engine as V
    npz = _npz()
    n = len(npz["close"])
    a = V.QuickConfig()
    a.LONG_STOCH_CHASE_BLOCK = False
    b = V.QuickConfig()
    b.LONG_STOCH_CHASE_BLOCK = True
    m0 = np.asarray(V.compute_entry_signals(npz, n, True, a), dtype=bool)
    m1 = np.asarray(V.compute_entry_signals(npz, n, True, b), dtype=bool)
    assert int(np.sum(m0 != m1)) == 0  # stubbed: switch inert both venues


def test_vec_wtdc_stoch_stubbed():
    import v12_quick_engine as V
    npz = _npz()
    n = len(npz["close"])
    a = V.QuickConfig()
    a.WT_DC_STOCH_THRESHOLD_LONG = 40.0
    b = V.QuickConfig()
    b.WT_DC_STOCH_THRESHOLD_LONG = 5.0
    m0 = np.asarray(V.compute_entry_signals(npz, n, True, a), dtype=bool)
    m1 = np.asarray(V.compute_entry_signals(npz, n, True, b), dtype=bool)
    assert int(np.sum(m0 != m1)) == 0  # stubbed: threshold inert (sacred family intact)
