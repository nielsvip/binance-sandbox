"""PARITY LANE C 2026-10-06 — EXIT_TOP_FADE_FILTER_TF + CANDLE_PATTERN_STOPS_FILTER_TF in the EXIT-confirm lifecycle.

Vec: generic_filter_tf FILTER_TF_MAP ('exit_confirm', 'wt_top_fade' / 'bar_pattern_against') -> simulate_one `_xc_ok` gates the
exit_sig-block closes (TECHNICAL_EXIT dc_*, WT final). Live: _ftf_twins.exit_confirm_block at the TECHNICAL_DC chain, the
WT_CROSSUNDER_FINAL chain twin and the evaluate_stop consumer. Prev row = tradier_indicators _lc_wt1_{tf}_prev
(exact on 15m; on 1h/4h/D live prev = previous TF bar vs vec previous 15m row of the ffilled HTF array — documented).
"""
import numpy as np
import pytest

import vec_decisions.generic_filter_tf as G
from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz_p3, prev_row, bar_dict, tm_source


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_top_fade_live_equals_vec(tf, is_long):
    n = 400
    npz = rng_npz_p3(n, 50)
    npz[f"wt1_{tf}"] = np.random.default_rng(1).normal(0, 70, n)
    vm = G.build_masks(npz, n, is_long, Cfg(EXIT_TOP_FADE_FILTER_TF=tf), npz["close"], vsafe)["exit_confirm"]
    pv = prev_row(npz[f"wt1_{tf}"])
    ok_cnt = 0
    for i in range(1, n):
        d = {f"wt1_{tf}": npz[f"wt1_{tf}"][i], f"_lc_wt1_{tf}_prev": pv[i]}
        lv = L.top_fade_confirm(d.get, is_long, tf)
        assert lv == bool(vm[i]), i
        ok_cnt += int(lv)
    assert 0 < ok_cnt < n


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_candle_live_equals_vec(tf, is_long):
    n = 400
    npz = rng_npz_p3(n, 51)
    vm = G.build_masks(npz, n, is_long, Cfg(CANDLE_PATTERN_STOPS_FILTER_TF=tf), npz["close"], vsafe)["exit_confirm"]
    for i in range(n):
        assert L.candle_against_confirm(bar_dict(npz, i).get, is_long, tf) == bool(vm[i]), i


def test_block_scope_and_off():
    g = {"wt1_15m": 10.0, "_lc_wt1_15m_prev": 20.0}.get
    assert L.exit_confirm_block(g, True, "TECHNICAL_STOP dc_15m", "15m", None) is not None
    assert L.exit_confirm_block(g, True, "WT_CROSSUNDER_FINAL_L_k=80", "15m", None) is not None
    assert L.exit_confirm_block(g, True, "DAYTRADE_STOP dc_15m", "15m", None) is None
    assert L.exit_confirm_block(g, True, "TECHNICAL_STOP dc_15m", None, None) is None


def test_hooks_present_and_entry_vetoes_removed():
    src = tm_source()
    assert src.count("_ftf_twins.exit_confirm_block(") == 3
    for gone in ("EXIT_TOP_FADE_TF_{_ftf}_BLOCK", "CANDLE_PATTERN_STOPS_TF_{_ftf}_BLOCK", "PEAK_GIVEBACK_BE_EROSION_TF_{_ftf}_BLOCK", "BREAKEVEN_GAIN_EROSION_TF_{_ftf}_BLOCK"):
        assert gone not in src
