from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_pre_score_gates import asr_pre_score_entry_gates


def _base(n=4):
    return {"dc_basis_15m": np.array([100., 100., 0., 100.]),
            "dc_basis_1h": np.full(n, 100.), "dc_basis_4h": np.full(n, 100.),
            "dc_basis_D": np.full(n, 100.), "k_15m": np.array([50., 60., 10., 50.]),
            "d_15m": np.full(n, 50.), "sma_200_1h": np.full(n, 90.),
            "0sentiment_rank": np.array([1, 21, 2, 3]),
            "0ranking_points": np.array([80., 5., 0., 10.]),
            "wt_velocity_up_count": np.array([0, 2, 4, -1]),
            "wt_velocity_down_count": np.array([3, 3, 1, -1]),
            "wt_composite_delta": np.array([0., -101., 101., 100.]),
            "wt_momentum_state_3m": np.array(["IMPULSE_UP", "EXHAUST_UP", "", "EXHAUST_DOWN"], object),
            "wt_momentum_state_15m": np.array(["", "EXHAUST_UP", "", "EXHAUST_DOWN"], object),
            "wt_percentile_D": np.array([50., 50., 95., 5.]),
            "wt_any_bear_div": np.array([False, False, True, False]),
            "wt_any_bull_div": np.array([False, True, False, False]),
            "wt_divergence_4h": np.array(["", "BEAR", "", "BULL"], object),
            "wt_divergence_D": np.array(["", "", "BEAR", ""], object),
            **{f"wt_cross_count_bull_{tf}": np.array([0, 9, 9, 0]) for tf in ("3m", "15m", "1h")},
            **{f"wt_cross_count_bear_{tf}": np.array([0, 0, 9, 9]) for tf in ("3m", "15m", "1h")}}

def test_long_gate_order_and_native_boundary_defaults():
    cfg = SimpleNamespace(BASIS_CONDITION=True, SENTIMENT_TOP_N_GATE_ENABLED=True,
        SENTIMENT_TOP_N=20, WT_MTF_VEL_GATE_ENABLED=True, WT_MTF_VEL_MIN=3,
        WT_CHOP_GATE_ENABLED=True, WT_CHOP_MAX=8, WT_COMPOSITE_DELTA_GATE_ENABLED=True,
        WT_COMPOSITE_DELTA_LONG_MIN=-100., WT_EXHAUST_ENTRY_GATE_ENABLED=True,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=True, WT_PERCENTILE_ENTRY_OB_D=90.,
        WT_DIV_ENTRY_GATE_ENABLED=True, R_G10_HTF_DIV_GATE_ENABLED=True,
        R_G10_HTF_DIV_TFS="4h,D", R_S6_WT_MSTATE_GATE_MODE=2)
    result = asr_pre_score_entry_gates(_base(), cfg, True, np.array([99., 99., 101., 99.]))
    assert result["reason"].tolist() == ["", "BELOW_BASIS_ALL_TF", "STOCH_15M_BEARISH", ""]
    assert result["blocked"].tolist() == [False, True, True, False]

def test_gate_precedence_across_enabled_rg_families():
    cfg = SimpleNamespace(SENTIMENT_TOP_N_GATE_ENABLED=True, SENTIMENT_TOP_N=20,
        WT_MTF_VEL_GATE_ENABLED=True, WT_MTF_VEL_MIN=3, WT_CHOP_GATE_ENABLED=True,
        WT_CHOP_MAX=8, WT_COMPOSITE_DELTA_GATE_ENABLED=True, WT_COMPOSITE_DELTA_LONG_MIN=-100.,
        WT_EXHAUST_ENTRY_GATE_ENABLED=True, WT_PERCENTILE_ENTRY_GATE_ENABLED=True,
        WT_PERCENTILE_ENTRY_OB_D=90., WT_DIV_ENTRY_GATE_ENABLED=True,
        R_G10_HTF_DIV_GATE_ENABLED=True, R_G10_HTF_DIV_TFS="4h,D", R_S6_WT_MSTATE_GATE_MODE=2)
    result = asr_pre_score_entry_gates(_base(), cfg, True, np.full(4, 100.))
    assert result["reason"].tolist() == ["", "SENTIMENT_TOP_N", "WT_MTF_VELOCITY", "WT_COMPOSITE_DELTA"]

def test_short_rg_direction_and_mode3_warn_only():
    cfg = SimpleNamespace(SENTIMENT_TOP_N_GATE_ENABLED=True, SENTIMENT_TOP_N=20,
        WT_MTF_VEL_GATE_ENABLED=True, WT_MTF_VEL_MIN=3, WT_EXHAUST_ENTRY_GATE_ENABLED=True,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=True, WT_PERCENTILE_ENTRY_OS_D=10.,
        WT_DIV_ENTRY_GATE_ENABLED=True, R_G10_HTF_DIV_GATE_ENABLED=True,
        R_G10_HTF_DIV_TFS="4h,D", R_S6_WT_MSTATE_GATE_MODE=3)
    result = asr_pre_score_entry_gates(_base(), cfg, False, np.full(4, 100.))
    assert result["reason"].tolist() == ["", "SENTIMENT_TOP_N", "WT_MTF_VELOCITY", "WT_EXHAUST"]
    assert not result["masks"]["R_S6_MSTATE"].any()

def test_master_boycott_precedes_basis_and_skip_boycott_only_skips_wtdc_basis():
    cfg = SimpleNamespace(WT_DC_LONG_ENABLED=False, BASIS_CONDITION=True)
    values = _base()
    values["dc_basis_15m"] = np.full(4, 100.)
    result = asr_pre_score_entry_gates(values, cfg, True, np.full(4, 90.))
    assert result["reason"].tolist() == ["WT_DC_LONG_DISABLED"] * 4
    skip = asr_pre_score_entry_gates(values, cfg, True, np.full(4, 90.), skip_boycott=True)
    assert skip["reason"].tolist() == ["BELOW_BASIS_ALL_TF"] * 4
