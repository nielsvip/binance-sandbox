"""DC_SLOPE_INCENTIVE 2026-10-22: MTF DC-slope entry incentive (sizing only, never a gate).

Contracts: (1) defaults are inert on both engines (OFF = all-1.0 mult);
(2) LONG fires only on a rising dc_low_TF vs _ant, SHORT only on a falling
dc_high_TF (mirrored, never the same comparison); (3) missing/zero _ant
never fires (fail-closed); (4) Config/TradierConfig/QuickConfig carry the
same four fields with identical defaults (§17 parity); (5) the vector call
site and both live reads exist (no silent unwiring).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vec_decisions import dc_slope_incentive as D  # noqa: E402

N = 40


def _npz_rising(n=N, tf="4h", base=100.0, step=0.5):
    cur = base + step * np.arange(n)
    return {f"dc_low_{tf}": cur, f"dc_low_{tf}_ant": np.full(n, base), f"dc_high_{tf}": cur + 10.0, f"dc_high_{tf}_ant": np.full(n, base + 10.0)}


def _cfg(**kw):
    d = {"DC_SLOPE_INCENTIVE_ENABLED": False, "DC_SLOPE_INCENTIVE_TF": "4h", "DC_SLOPE_INCENTIVE_MIN_PCT": 0.0, "DC_SLOPE_INCENTIVE_ENTRY_MULT": 2.0}
    d.update(kw)
    return SimpleNamespace(**d)


def test_defaults_inert_vec():
    m = D.open_mult_vec(_npz_rising(), N, True, _cfg())
    assert m.shape == (N,) and bool((m == 1.0).all())


def test_long_rising_low_doubles():
    m = D.open_mult_vec(_npz_rising(), N, True, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True))
    assert m[0] == 1.0  # bar 0: no rise yet (strict inequality)
    assert bool((m[1:] == 2.0).all())


def test_long_flat_channel_no_fire():
    npz = _npz_rising(step=0.0)
    m = D.open_mult_vec(npz, N, True, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True))
    assert bool((m == 1.0).all())


def test_short_mirror_falling_high():
    npz = _npz_rising()
    npz["dc_high_4h"] = 110.0 - 0.5 * np.arange(N)
    m = D.open_mult_vec(npz, N, False, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True))
    assert m[0] == 1.0
    assert bool((m[1:] == 2.0).all())


def test_short_rising_high_no_fire():
    m = D.open_mult_vec(_npz_rising(), N, False, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True))
    assert bool((m == 1.0).all())


def test_min_pct_binds():
    npz = _npz_rising(base=100.0, step=0.01)  # max +0.39% vs ant, under the 0.5% bar
    m = D.open_mult_vec(npz, N, True, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True, DC_SLOPE_INCENTIVE_MIN_PCT=0.5))
    assert bool((m == 1.0).all())


def test_tf_off_and_unknown_inert():
    for tf in ("OFF", "W"):
        m = D.open_mult_vec(_npz_rising(), N, True, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True, DC_SLOPE_INCENTIVE_TF=tf))
        assert bool((m == 1.0).all())


def test_missing_ant_fail_closed():
    m = D.open_mult_vec({"dc_low_4h": np.full(N, 101.0)}, N, True, _cfg(DC_SLOPE_INCENTIVE_ENABLED=True))
    assert bool((m == 1.0).all())


def test_live_scalar_both_sides():
    ind = {"dc_low_4h": 102.0, "dc_low_4h_ant": 100.0, "dc_high_4h": 108.0, "dc_high_4h_ant": 110.0}
    get = lambda k, d: {"DC_SLOPE_INCENTIVE_ENABLED": True, "DC_SLOPE_INCENTIVE_TF": "4h", "DC_SLOPE_INCENTIVE_MIN_PCT": 0.0, "DC_SLOPE_INCENTIVE_ENTRY_MULT": 2.0}.get(k, d)
    assert D.open_mult_live(ind, True, get) == 2.0
    assert D.open_mult_live(ind, False, get) == 2.0
    get_off = lambda k, d: d
    assert D.open_mult_live(ind, True, get_off) == 1.0
    assert D.open_mult_live({}, True, get) == 1.0


def test_triple_config_parity():
    import config
    import config_tradier
    import v12_quick_engine as V

    want = {"DC_SLOPE_INCENTIVE_ENABLED": False, "DC_SLOPE_INCENTIVE_TF": "4h", "DC_SLOPE_INCENTIVE_MIN_PCT": 0.0, "DC_SLOPE_INCENTIVE_ENTRY_MULT": 2.0}
    for k, v in want.items():
        assert getattr(config.Config(), k) == v
        assert getattr(config_tradier.TradierConfig(), k) == v
        assert getattr(V.QuickConfig(), k) == v


def test_wiring_present_all_engines():
    v12 = (ROOT / "v12_quick_engine.py").read_text()
    assert "dc_slope_incentive" in v12 and "_dcsi_open_mult" in v12
    ez = (ROOT / "ez_manage.py").read_text()
    assert "DC_SLOPE_INCENTIVE_ENABLED" in ez and "open_mult_live" in ez
    tr = (ROOT / "tradier_manage.py").read_text()
    assert "DC_SLOPE_INCENTIVE_ENABLED" in tr and "open_mult_live" in tr
