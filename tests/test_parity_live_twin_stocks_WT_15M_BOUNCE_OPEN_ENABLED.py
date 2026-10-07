"""PARITY LANE C 2026-10-06 — WT_15M_BOUNCE_OPEN_ENABLED entry SOURCE.

Vec: the inline WT_15M_BOUNCE block of v12_quick_engine.compute_entry_signals is EXTRACTED FROM LIVE v12 SOURCE AND EXECUTED.
Live: tradier_filter_tf_twins.wt15_bounce_fires (prev row = new tradier_indicators _lc_wt*_15m_prev keys), real OPEN path in
process_position Branch B. NOT mirrored (documented): the vec side-effect cooldown_bars=0 / min_hold=1 while enabled, and the
HL/HH 1h-channel prev (vec 15m row-prev vs live previous 1h bar) — the HL/HH case is tested only on the 15m row-prev input.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, Cfg, vsafe, v12_safeb, rng_npz_p3, prev_row, tm_source, v12_block

BLOCK = v12_block("# WT_15M_BOUNCE_OPEN_ENABLED — vectorized parity", "_base_entry = _base_entry | _b15_mask")

BASE = dict(WT_15M_BOUNCE_OPEN_ENABLED=True, WT_15M_BOUNCE_BB_MIN=0.05, WT_15M_BOUNCE_BB_MAX=0.95, WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False, WT_15M_BOUNCE_FILTER_HL_ENABLED=False, WT_15M_BOUNCE_FILTER_HH_ENABLED=False, WT_15M_BOUNCE_FILTER_MODE="AND", WT_15M_BOUNCE_VOLUME_FILTER_ENABLED=False, WT_15M_BOUNCE_VOLUME_MODE="relvol", WT_15M_BOUNCE_VOLUME_THRESHOLD=1.0, WT_15M_BOUNCE_LOW_1H_GT_PREV=False, WT_15M_BOUNCE_HIGH_1H_GT_PREV=False, WT_15M_BOUNCE_REL_VOL_GT_1=False)


def _vec(npz, is_long, cfgd):
    n = len(npz["close"])
    ns = {"np": np, "npz": npz, "n": n, "is_long": is_long, "_safe": vsafe, "_safeb": v12_safeb, "_base_entry": np.zeros(n, dtype=bool), "cfg": Cfg(**cfgd)}
    exec(compile("if True:\n    " + BLOCK.replace("\n", "\n    ") + "\n    pass", "v12_wt15_block", "exec"), ns)
    return ns["_base_entry"]


def _live_dict(npz, i):
    d = {k: float(v[i]) for k, v in npz.items()}
    d["_lc_wt1_15m_prev"] = float(prev_row(npz["wt1_15m"])[i])
    d["_lc_wt2_15m_prev"] = float(prev_row(npz["wt2_15m"])[i])
    d["wt_cross_rising_1h"] = bool(npz["wt_cross_rising_1h"][i])
    d["wt_cross_rising_4h"] = bool(npz["wt_cross_rising_4h"][i])
    return d


VARIANTS = [{}, {"WT_15M_BOUNCE_REQUIRE_BOTH_HTF": True}, {"WT_15M_BOUNCE_BB_MIN": 0.2, "WT_15M_BOUNCE_BB_MAX": 0.8}, {"WT_15M_BOUNCE_REL_VOL_GT_1": True, "WT_15M_BOUNCE_VOLUME_THRESHOLD": 1.2}]


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("var", range(len(VARIANTS)))
def test_live_equals_vec(is_long, var):
    n = 400
    npz = rng_npz_p3(n, 34 + var)
    cfgd = dict(BASE, **VARIANTS[var])
    vm = _vec(npz, is_long, cfgd)
    fires = 0
    for i in range(1, n):
        lf, _ = L.wt15_bounce_fires(lambda k, d=None: cfgd.get(k, d), _live_dict(npz, i).get, is_long)
        assert lf == bool(vm[i]), (i, var)
        fires += int(lf)
    assert 0 < fires < n


def test_disabled_inert():
    npz = rng_npz_p3(100, 40)
    assert not _vec(npz, True, dict(BASE, WT_15M_BOUNCE_OPEN_ENABLED=False)).any()
    assert L.wt15_bounce_fires(lambda k, d=None: False if k == "WT_15M_BOUNCE_OPEN_ENABLED" else d, {}.get, True) == (False, "")


def test_missing_prev_key_never_fires():
    d = {"wt1_15m": 5.0, "wt2_15m": 1.0, "wt_cross_rising_1h": True}
    assert L.wt15_bounce_fires(lambda k, dd=None: BASE.get(k, dd), d.get, True)[0] is False


def test_open_path_hook():
    assert "_ftf_twins.wt15_bounce_fires(_lc_c, _lc_get, is_long)" in tm_source()
