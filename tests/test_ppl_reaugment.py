"""PPL re-augment switch (USER 2026-10-22): the PPL standing order becomes a
per-sym_side backtest switch; ON implies a re-augment of MULT x the TP qty
the moment WT crosses back above dc_low_15m (one-shot per tap)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

import v12_quick_engine as V


def _npz():
    n = 120
    close = np.empty(n)
    close[:8] = 100.0 * (1.005 ** np.arange(8))
    close[8:] = close[7] * (0.999 ** np.arange(1, n - 7))
    ts = np.arange(n, dtype=float) * 900 + 1_700_000_000.0
    wt = np.array([5.0] * 7 + [20.0] * (n - 7))  # cross above dc (10) at bar 7
    return {
        "close": close,
        "close_15m": close,
        "timestamps": ts,
        "timestamp_15m": ts,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close * 0.999,
        "volume_15m": np.ones(n),
        "wt1_15m": wt,
        "wt2_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 10.0),
        "dc_high_15m": np.full(n, 200.0),
        "stoch_k_15m": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
    }


def _npz_wt(wt1, wt2=None):
    d = _npz()
    d["wt1_15m"] = wt1
    d["wt2_15m"] = wt2 if wt2 is not None else np.zeros(len(wt1))
    return d


def _run(npz, reaug, mult=1.5, trig="DC_RECLAIM"):
    cfg = V.QuickConfig()
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = True
    cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT = 1.0
    cfg.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT = 2.0
    cfg.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT = 0.02
    cfg.PARTIAL_PROFIT_LOCK_FRAC = 0.5
    cfg.PPL_REAUGMENT_ENABLED = reaug
    cfg.PPL_REAUGMENT_SIZE_MULT = mult
    cfg.PPL_REAUGMENT_TRIGGER = trig
    return V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True)


def _taps(ledger):
    return [
        e
        for e in ledger
        if e.get("type") == "REDUCE" and "PPL_TP" in str(e.get("reason"))
    ]


def _reaugs(ledger, trig="DC_RECLAIM"):
    return [e for e in ledger if e.get("reason") == f"PPL_REAUGMENT_{trig}"]


def test_reaugment_off_is_inert():
    """Default OFF: the PPL tap still fires, but no re-augment follows."""
    res = _run(_npz(), False)
    assert _taps(res["ledger"]), "probe must tap PPL or it proves nothing"
    assert _reaugs(res["ledger"]) == []


def test_reaugment_fires_on_dc_reclaim():
    """ON: armed by the tap, the WT cross back above dc_low_15m augments."""
    res = _run(_npz(), True)
    taps = _taps(res["ledger"])
    reaugs = _reaugs(res["ledger"])
    assert taps, "probe must tap PPL or it proves nothing"
    assert len(reaugs) == 1
    assert reaugs[0]["type"] == "AUGMENT"
    assert reaugs[0]["bar"] > taps[0]["bar"]
    assert reaugs[0]["qty"] == taps[0]["qty"] * 1.5


def test_reaugment_size_mult_scales():
    """MULT 2.0 re-augments twice the TP qty."""
    res = _run(_npz(), True, mult=2.0)
    taps = _taps(res["ledger"])
    reaugs = _reaugs(res["ledger"])
    assert taps and len(reaugs) == 1
    assert reaugs[0]["qty"] == taps[0]["qty"] * 2.0


def test_reaugment_wt_cross_trigger():
    """WT_CROSS: armed by the tap, wt1 crossing back above wt2 re-augments
    (WT stays below the DC band throughout, so DC_RECLAIM could not fire)."""
    wt1 = np.array([-5.0] * 8 + [-1.0, 2.0] + [3.0] * 110)
    res = _run(_npz_wt(wt1), True, trig="WT_CROSS")
    taps = _taps(res["ledger"])
    reaugs = _reaugs(res["ledger"], "WT_CROSS")
    assert taps, "probe must tap PPL or it proves nothing"
    assert len(reaugs) == 1
    assert reaugs[0]["type"] == "AUGMENT"
    assert reaugs[0]["bar"] == 9
    assert reaugs[0]["bar"] > taps[0]["bar"]
    assert reaugs[0]["qty"] == taps[0]["qty"] * 1.5
    assert _reaugs(res["ledger"], "DC_RECLAIM") == []


def test_reaugment_wt_slowdown_trigger():
    """WT_SLOWDOWN: wt1 hooking back up (decline decelerating) re-augments
    before any band reclaim."""
    wt1 = np.array([0.0, 0.0, -2.0, -4.0, -3.0, -1.0] + [0.0] * 114)
    res = _run(_npz_wt(wt1), True, trig="WT_SLOWDOWN")
    taps = _taps(res["ledger"])
    reaugs = _reaugs(res["ledger"], "WT_SLOWDOWN")
    assert taps, "probe must tap PPL or it proves nothing"
    assert len(reaugs) == 1
    assert reaugs[0]["type"] == "AUGMENT"
    assert reaugs[0]["bar"] == 4
    assert reaugs[0]["qty"] == taps[0]["qty"] * 1.5


def test_reaugment_unknown_trigger_falls_back():
    """An unknown trigger value fails safe to the proven DC_RECLAIM."""
    res = _run(_npz(), True, trig="BOGUS")
    assert len(_reaugs(res["ledger"], "DC_RECLAIM")) == 1
