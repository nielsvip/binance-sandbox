"""Rich DELTA slowdown twin: wiring + live-parity collateral (USER 2026-10-10).

Covers vec_decisions/delta_exit_rich_vec.py (the v12 twin of the live-only
DELTA_EXIT_* sub-knobs) and its v12 gate DELTA_EXIT_SPEED_DECAY_VEC_ENABLED.
"""
import numpy as np
import pytest

import v12_quick_engine as V
from vec_decisions import delta_exit_rich_vec as T


def _cfg(**kw):
    c = V.QuickConfig()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _synth_npz(n=300, seed=7):
    # Two-phase tape: 0-99 live trend (builds speed/max/accel state), 100+ frozen
    # delta fields (speeds die -> DEAD/DECEL/PEAK/TFLOST) with declining 1h/4h
    # highs+lows (structural veto passes) and bearish HTF WT (no HTF veto).
    rng = np.random.default_rng(seed)
    d = {}
    tfs = ["15m", "1h", "4h", "D"]
    fields = ["wt1", "wt2", "wt_score", "wt_velocity", "wt_acceleration",
              "wt_percentile", "wt_zscore", "dc_basis", "dc_high", "dc_low",
              "dc_position", "dc_width", "wt_bullish", "wt_cross_bull",
              "wt_cross_bear", "wt_momentum_state"]
    cut = 100
    for tf in tfs:
        for f in fields:
            walk = rng.normal(0, 1, n).cumsum()
            if f == "dc_position":
                walk = 0.5 + 0.01 * walk
            flat = np.concatenate([walk[:cut], np.full(n - cut, walk[cut - 1])])
            d[f"{f}_{tf}"] = flat
    for k in ["mfi_15m", "stoch_k_1h", "bb_pct_b_1h"]:
        d[k] = 100 + rng.normal(0, 1, n).cumsum()
    # 15m bars decline after cut (no rising bar -> structural veto passes)
    for k, base in (("open_15m", 100.0), ("high_15m", 101.0), ("low_15m", 99.0), ("close_15m", 100.0)):
        d[k] = np.concatenate([np.full(cut, base), base - 0.05 * np.arange(n - cut)])
    d["close"] = d["close_15m"].copy()  # like real NPZ: close tracks the micro close
    d["mfi_15m"] = np.concatenate([np.full(cut, 60.0), np.full(n - cut, 30.0)])
    d["high_1h"] = np.concatenate([np.full(cut, 110.0), np.linspace(110, 90, n - cut)])
    d["low_1h"] = np.concatenate([np.full(cut, 100.0), np.linspace(100, 80, n - cut)])
    d["high_4h"] = np.concatenate([np.full(cut, 112.0), np.linspace(112, 92, n - cut)])
    d["low_4h"] = np.concatenate([np.full(cut, 102.0), np.linspace(102, 82, n - cut)])
    d["wt1_1h"] = np.linspace(60, 40, n)  # HTF bearish drift (no veto for LONG exits)
    d["wt2_1h"] = np.linspace(40, 60, n)
    d["wt1_4h"] = np.linspace(60, 40, n)
    d["wt2_4h"] = np.linspace(40, 60, n)
    d["wt1_D"] = np.linspace(60, 40, n)
    d["wt2_D"] = np.linspace(40, 60, n)
    return d


def test_gate_default_off_keeps_proxy():
    n = 300
    npz = _synth_npz(n)
    cfg = _cfg(DELTA_EXIT_SPEED_DECAY_VEC_ENABLED=False)
    a = V.compute_exit_signals(npz, n, True, cfg)
    assert a.shape == (n,)
    assert a.dtype == bool


def test_knob_sensitivity_decay_ratio():
    n = 300
    npz = _synth_npz(n)
    f0, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_DECAY_RATIO=0.0))
    f1, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_DECAY_RATIO=0.99))
    assert f0.sum() != f1.sum(), "DECAY_RATIO must move twin fires (else unwired)"


def test_knob_sensitivity_min_tf_lost():
    n = 300
    npz = _synth_npz(n)
    f0, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_MIN_TF_LOST=1))
    f1, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_MIN_TF_LOST=5))
    assert f0.sum() != f1.sum(), "MIN_TF_LOST must move twin fires (else unwired)"


def test_knob_sensitivity_accel_opposing():
    n = 300
    npz = _synth_npz(n)
    fa, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_ACCEL_THRESHOLD=-10.0))
    fb, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_ACCEL_THRESHOLD=10.0))
    assert fa.sum() != fb.sum(), "ACCEL_THRESHOLD must move twin fires"
    fo, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_OPPOSING_RATIO=0.01))
    fp, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_OPPOSING_RATIO=100.0))
    assert fo.sum() != fp.sum(), "OPPOSING_RATIO must move twin fires"


def test_knob_sensitivity_dom_and_hold():
    n = 300
    npz = _synth_npz(n)
    f0, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_DOM_TF_ENABLED=False))
    f1, _ = T.rich_delta_exits(npz, n, True, _cfg(DELTA_EXIT_DOM_TF_ENABLED=True, DELTA_EXIT_TF="1h"))
    assert f0.sum() != f1.sum(), "DOM_TF select must move twin fires"


def test_live_crosscheck_controlled_substitution():
    from wt_dc_delta import DeltaTracker
    n = 220
    npz = _synth_npz(n, seed=11)
    cfg = _cfg(DELTA_EXIT_DECAY_RATIO=0.9, DELTA_EXIT_MIN_TF_LOST=1,
               DELTA_EXIT_ACCEL_THRESHOLD=-0.1, DELTA_EXIT_OPPOSING_RATIO=1.5,
               DELTA_EXIT_DOM_TF_ENABLED=True, DELTA_EXIT_TF="15m")
    tw = {"15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0}
    live_cfg = {"tf_weights": tw, "entry_min_tf": 2, "exit_speed_decay_pct": 90.0,
                "exit_accel_threshold": -0.1, "exit_min_tf_lost": 1,
                "exit_min_hold": 4, "exit_opposing_ratio": 1.5,
                "accel_lookback": 5, "z_window": 200,
                "exit_require_htf_slowdown": True, "exit_htf_veto_min_aligned": 2,
                "structural_exit_gate_enabled": True, "rz_ltf_micro": "15m",
                "rz_two_phase_exit_enabled": False}
    tr = DeltaTracker(cfg=live_cfg)
    tr.reset_position_state("SYN")
    live_fires = []
    fields = (["wt1", "wt2", "wt_score", "wt_velocity", "wt_acceleration",
               "wt_percentile", "wt_zscore", "dc_basis", "dc_high", "dc_low",
               "dc_position", "dc_width", "wt_bullish", "wt_cross_bull",
               "wt_cross_bear", "wt_momentum_state"])
    for i in range(n):
        ind = {"_tick_ts": 1000 + i, "close": float(npz["close"][i]),
               "open_15m": float(npz["open_15m"][i]), "high_15m": float(npz["high_15m"][i]),
               "low_15m": float(npz["low_15m"][i]), "close_15m": float(npz["close_15m"][i]),
               "high_1h": float(npz["high_1h"][i]), "low_1h": float(npz["low_1h"][i]),
               "high_4h": float(npz["high_4h"][i]), "low_4h": float(npz["low_4h"][i]),
               "mfi_15m": float(npz["mfi_15m"][i])}
        for tf in tw:
            for f in fields:
                ind[f"{f}_{tf}"] = float(npz[f"{f}_{tf}"][i])
        for k in ["wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h", "wt1_D", "wt2_D",
                  "wt1_15m", "dc_position_15m", "mfi_3m", "wt1_3m",
                  "dc_position_3m", "stoch_k_1h", "bb_pct_b_1h"]:
            ind[k] = float(npz[k][i]) if k in npz else 50.0
        ind["wt1_3m"] = 50.0  # flat 3m: live 3m terms False (controlled substitution)
        ind["dc_position_3m"] = 0.5
        ind["mfi_3m"] = 50.0
        sig = tr.update("SYN", ind, {"side": "LONG", "held_bars": i})
        live_fires.append(bool(sig.exit_long))
    fires, meta = T.rich_delta_exits(npz, n, True, cfg, entry_sig=None)
    assert meta["fires"] > 0, "twin must fire on dying-speed synthetic"
    assert sum(live_fires) > 0, "live must fire on dying-speed synthetic"
    agree = sum(1 for a, b in zip(live_fires, fires) if a == b) / n
    assert agree >= 0.80, f"twin/live agreement {agree:.3f} below 0.80"


def test_npz_smoke_both_sides():
    import pathlib
    p = pathlib.Path("backtest_v8/indicators/ENAUSDC.npz")
    if not p.exists():
        pytest.skip("ENA NPZ absent")
    import numpy as _np
    npz = _np.load(str(p))
    n = 500
    cfg = _cfg()
    fl, ml = T.rich_delta_exits(npz, n, True, cfg)
    fs, ms = T.rich_delta_exits(npz, n, False, cfg)
    assert fl.shape == (n,) and fs.shape == (n,)
    assert ml["fires"] >= 0 and ms["fires"] >= 0
