from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.asr_entry_prefix import score_asr_entry_prefix_array


def _cfg(**overrides):
    values = {
        "USE_1M_3M_SIGNALS_ENABLED": False,
        "WT_COMPOSITE_SCORING_ENABLED": False,
        "WT_COMPOSITE_SCORING_ENABLED_TRADIER": False,
        "WT_COMPOSITE_HTF_GATE": False,
        "HTF_STRICT": False,
        "REGIME_DETECTION_ENABLED": False,
        "ADX_REGIME_FILTER_ENABLED": False,
        "MI_ENTRY_ENABLED": False,
        "MTS_GATE_ENABLED": False,
        "MTS_GATE_ENABLED_TRADIER": False,
        "RANK_CONVICTION_ENABLED": False,
        "DC_MOMENT_ENABLED": False,
        "WT_COMPOSITE_DELTA_SCORE_ENABLED": False,
        "K3M_CAP": 0,
        "SHORT_RSI_MIN_1H": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _arrays(n=3):
    return {
        "wt_bullish_scalp": np.array([True, False, True][:n]),
        "wt_score_scalp": np.array([10.0, -20.0, -40.0][:n]),
        "wt_velocity_scalp": np.array([0.0, 0.0, -2.0][:n]),
        "wt_cross_bull_scalp": np.array([False, False, True][:n]),
        "wt_momentum_state_1h": np.array(["IMPULSE_UP", "EXHAUST_UP", "EXHAUST_DOWN"][:n], dtype=object),
        "k_15m": np.array([50.0, 80.0, 25.0][:n]),
        "k_15m_prev": np.array([50.0, 90.0, 24.0][:n]),
        "k_3m": np.array([40.0, 70.0, 60.0][:n]),
        "d_3m": np.array([35.0, 75.0, 55.0][:n]),
        "k_1m": np.full(n, 50.0), "d_1m": np.full(n, 50.0),
        "k_1m_prev": np.full(n, 50.0), "k_3m_prev": np.full(n, 50.0),
        "wt_cross_bull_3m": np.zeros(n), "wt_cross_bear_3m": np.zeros(n),
        "wt_crossover_value_3m": np.zeros(n), "wt_crossover_value_3m_prev": np.zeros(n),
        "mts_bottom": np.zeros(n), "mts_entry_quality": np.zeros(n),
        "wt_trough_structure_1h": np.full(n, "", object),
        "wt_trough_structure_4h": np.full(n, "", object),
        "wt_momentum_state_4h": np.full(n, "", object),
        "wt_divergence_1h": np.full(n, "", object),
        "wt_peak_structure_1h": np.full(n, "", object),
        "wt_peak_structure_4h": np.full(n, "", object),
        "dc_high_1h": np.zeros(n), "dc_high_3m": np.zeros(n),
        "dc_low_1h": np.zeros(n), "dc_low_3m": np.zeros(n),
        "dc_high_4h": np.zeros(n), "dc_low_4h": np.zeros(n),
        "dc_high_D": np.zeros(n), "dc_low_D": np.zeros(n),
    }


def test_entry_prefix_preserves_native_score_order_and_explicit_context():
    npz = _arrays()
    context = {"current_price": np.array([100.0, 100.0, 100.0]), "portfolio_ratio_active": False}
    result = score_asr_entry_prefix_array(npz, 3, True, _cfg(), context)
    assert result["entry_allowed"].tolist() == [True, True, True]
    assert result["score"].shape == (3,)
    assert result["complete_rate"] is False
    assert result["covered_native_spans"] == (
        "ez_positions_quick.py:4696-4880 extracted score/gate blocks",
        "ez_positions_quick.py:4956-5010 channel/K/DC-cross and adjacent directional scores",
    )
    assert result["masks"]["stoch_entry:long_ok"].tolist() == [True, False, True]


def test_entry_prefix_requires_external_state_and_active_ratio_inputs():
    npz = _arrays(1)
    with pytest.raises(ValueError, match="current_price"):
        score_asr_entry_prefix_array(npz, 1, True, _cfg(), {"portfolio_ratio_active": False})
    with pytest.raises(ValueError, match="portfolio_ratio_active"):
        score_asr_entry_prefix_array(npz, 1, True, _cfg(), {"current_price": [100.0]})
    with pytest.raises(ValueError, match="portfolio_ratio"):
        score_asr_entry_prefix_array(
            npz, 1, True, _cfg(),
            {"current_price": [100.0], "portfolio_ratio_active": True},
        )


def test_entry_prefix_fails_closed_when_regime_state_is_enabled_but_absent():
    npz = _arrays(1)
    cfg = _cfg(REGIME_DETECTION_ENABLED=True)
    with pytest.raises(ValueError, match="regime_delta"):
        score_asr_entry_prefix_array(
            npz, 1, True, cfg,
            {"current_price": [100.0], "portfolio_ratio_active": False},
        )
