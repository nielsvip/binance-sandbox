"""QUICK_DC4 twin parity (2026-10-25): the v12 ledger-walk DC4 structural stop
mirrors live ez_positions_quick.py:27185-27241 exactly — DC4 4-bar break + HTF
veto, causal same-bar reads, NumPy-only, gated by BREAKEVEN_DC_LOW4_ENABLED
(default True to match live).

The oracle below re-implements the live predicate (gate / grace / basis
switch / break comparison / HTF veto / reason format) against the same
synthetic bars the engine walks from the bar-0 seed; the engine's first CLOSE
must equal the oracle's (bar, reason) exactly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

import v12_quick_engine as V
from vec_decisions import breakeven_gain_erosion as _bgem

BREAK_BAR = 10
N = 120


def _npz(is_long=True, break_bar=BREAK_BAR, veto_aligned=False,
         dc_low4=99.0, dc_high4=101.0, dc_low=98.0, dc_high=102.0):
    close = np.full(N, 100.0)
    close[break_bar:] = 98.5 if is_long else 101.5
    ts = np.arange(N, dtype=float) * 900 + 1_700_000_000.0
    w1, w2 = (5.0, 0.0) if (veto_aligned == is_long) else (-5.0, 0.0)
    return {
        "close": close, "close_15m": close,
        "timestamps": ts, "timestamp_15m": ts,
        "open_15m": close, "high_15m": close, "low_15m": close * 0.999,
        "volume_15m": np.ones(N),
        "dc_low4_3m": np.full(N, dc_low4), "dc_high4_3m": np.full(N, dc_high4),
        "dc_low_3m": np.full(N, dc_low), "dc_high_3m": np.full(N, dc_high),
        "dc_low_15m": np.full(N, 90.0), "dc_high_15m": np.full(N, 200.0),
        "dc_low_4h": np.full(N, 50.0), "dc_high_4h": np.full(N, 300.0),
        "dc_low_1h": np.full(N, 50.0), "dc_high_1h": np.full(N, 300.0),
        "wt1_15m": np.zeros(N), "wt2_15m": np.zeros(N),
        "wt1_1h": np.full(N, w1), "wt2_1h": np.full(N, w2),
        "wt1_4h": np.full(N, w1), "wt2_4h": np.full(N, w2),
        "wt1_D": np.full(N, w1), "wt2_D": np.full(N, w2),
        "wt_velocity_1h": np.zeros(N),
        "stoch_k_15m": np.full(N, 50.0), "stoch_k_1h": np.full(N, 50.0),
        "stoch_k": np.full(N, 50.0),
    }


def _cfg(**kw):
    cfg = V.QuickConfig()
    # Isolate the DC4 family: the unrelated MTF_GR exit would close the flat
    # synthetic seed at bar 2 (same isolation idea as ENTRY_ISOLATE_FAMILY).
    cfg.MTF_GR_EXIT_GATE_ENABLED = False
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _closes(res):
    return [e for e in res["ledger"] if e.get("type") == "CLOSE"]


def _live_oracle(npz, is_long, cfg, entry_px=100.0, entry_bar=0):
    """Live ez_positions_quick.py:27185-27241 on the synthetic bars.

    Returns (fire_bar, reason) or (None, None). Seed entry at bar 0 is never
    a REENTRY and never augmented, so grace = 3.0 min past entry.
    """
    get = lambda k, d: getattr(cfg, k, d)  # noqa: E731
    if not bool(get("BREAKEVEN_DC_LOW4_ENABLED", True)):
        return None, None
    bmin = V._bar_minutes(cfg)
    mode = str(get("BREAKEVEN_DC_FIELD_MODE", "DC4")).upper()
    for i in range(1, N):
        px = float(npz["close"][i])
        avg = float(entry_px)
        g = ((px - avg) / avg * 100.0) if is_long else ((avg - px) / avg * 100.0)
        age = float(i - entry_bar) * bmin
        if _bgem.in_grace(age, False, False, get("REENTRY_GRACE_MINUTES", 30.0)):
            continue
        if mode == "DC":
            lo, hi, tag = float(npz["dc_low_3m"][i]), float(npz["dc_high_3m"][i]), "DC"
        else:
            lo, hi, tag = float(npz["dc_low4_3m"][i]), float(npz["dc_high4_3m"][i]), "DC4"
        wt = {k: float(npz[k][i]) for k in ("wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h", "wt1_D", "wt2_D")}
        veto = _bgem.htf_veto(get, g, is_long, wt)[0]
        if is_long and lo > 0 and px > 0 and px < lo:
            if not veto:
                return i, f"{tag}_LOW_3M_GAIN_EROSION_STOP_p{px:.6f}<{tag.lower()}{lo:.6f}_g{g:.2f}%"
        elif (not is_long) and hi > 0 and px > 0 and px > hi:
            if not veto:
                return i, f"{tag}_HIGH_3M_GAIN_EROSION_STOP_p{px:.6f}>{tag.lower()}{hi:.6f}_g{g:.2f}%"
    return None, None


def _check_side(is_long, **npz_kw):
    npz = _npz(is_long, **npz_kw)
    cfg = _cfg()
    fire_bar, reason = _live_oracle(npz, is_long, cfg)
    assert fire_bar == BREAK_BAR, (fire_bar, reason)
    res = V.simulate_one(npz, "PROBE", is_long, cfg, force_initial_seed=True)
    closes = _closes(res)
    assert closes, "seed held to end — probe proves nothing"
    first = closes[0]
    assert first["bar_exit"] == fire_bar, (first, fire_bar)
    assert first["exit_reason"] == reason, (first["exit_reason"], reason)


def test_dc4_twin_fires_exactly_like_live_long():
    _check_side(True)


def test_dc4_twin_fires_exactly_like_live_short():
    _check_side(False)


def test_dc4_twin_gate_off_is_inert():
    npz = _npz(True)
    cfg = _cfg(BREAKEVEN_DC_LOW4_ENABLED=False)
    fire_bar, _ = _live_oracle(npz, True, cfg)
    assert fire_bar is None
    closes = _closes(V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True))
    assert all("GAIN_EROSION_STOP" not in str(c.get("exit_reason")) for c in closes)


def test_dc4_twin_htf_veto_blocks():
    npz = _npz(True, veto_aligned=True)
    cfg = _cfg()
    fire_bar, _ = _live_oracle(npz, True, cfg)
    assert fire_bar is None  # |gain| 1.5% <= 2.0 and 3/3 WT aligned
    closes = _closes(V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True))
    assert all("GAIN_EROSION_STOP" not in str(c.get("exit_reason")) for c in closes)


def test_dc4_twin_dc_basis_switch():
    # DC mode reads dc_low_3m (breaks: 98.5 < 99.5) while dc_low4_3m is intact.
    npz = _npz(True, dc_low4=90.0, dc_low=99.5)
    cfg = _cfg(BREAKEVEN_DC_FIELD_MODE="DC")
    fire_bar, reason = _live_oracle(npz, True, cfg)
    assert fire_bar == BREAK_BAR
    assert reason.startswith("DC_LOW_3M_GAIN_EROSION_STOP_")
    closes = _closes(V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True))
    assert closes[0]["bar_exit"] == fire_bar
    assert closes[0]["exit_reason"] == reason


def test_dc4_twin_no_lookahead_before_break():
    # Flat bars 1..9 must not fire: first close is exactly the break bar.
    npz = _npz(True, break_bar=40)
    cfg = _cfg()
    fire_bar, reason = _live_oracle(npz, True, cfg)
    assert fire_bar == 40
    closes = _closes(V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True))
    assert closes[0]["bar_exit"] == 40
    assert closes[0]["exit_reason"] == reason


def test_dc4_config_defaults_match_live():
    cfg = V.QuickConfig()
    assert cfg.BREAKEVEN_DC_LOW4_ENABLED is True
    assert cfg.BREAKEVEN_DC_FIELD_MODE == "DC4"
    assert cfg.HTF_EXIT_VETO_ENABLED is True
    assert cfg.HTF_EXIT_VETO_MAX_LOSS_PCT == 2.0
    assert cfg.HTF_EXIT_VETO_MIN_ALIGNED == 2
    assert cfg.REENTRY_GRACE_MINUTES == 30.0
