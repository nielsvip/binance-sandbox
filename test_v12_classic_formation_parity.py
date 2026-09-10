from dataclasses import replace

import numpy as np

import v12_quick_engine as v12
from classic_formations import formation_vector_mask


FORMATION_FIELDS = (
    "FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED",
    "FORMATION_HEAD_SHOULDERS_EXIT_ENABLED",
    "FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED",
    "FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED",
    "FORMATION_WEDGE_ENTRY_ENABLED",
    "FORMATION_WEDGE_EXIT_ENABLED",
    "FORMATION_TRIANGLE_ENTRY_ENABLED",
    "FORMATION_TRIANGLE_EXIT_ENABLED",
    "FORMATION_FLAG_PENNANT_ENTRY_ENABLED",
    "FORMATION_FLAG_PENNANT_EXIT_ENABLED",
    "FORMATION_CUP_HANDLE_ENTRY_ENABLED",
    "FORMATION_CUP_HANDLE_EXIT_ENABLED",
    "FORMATION_TREND_STRUCTURE_ENTRY_ENABLED",
    "FORMATION_TREND_STRUCTURE_EXIT_ENABLED",
    "FORMATION_TFS",
    "FORMATION_MIN_SCORE",
    "FORMATION_POSITION_SIZE_MULT",
    "FORMATION_EXIT_MIN_GAIN_PCT",
)


def test_all_formation_fields_are_typed_quickconfig_members():
    fields = v12.QuickConfig.__dataclass_fields__
    assert set(FORMATION_FIELDS) <= set(fields)
    assert fields["FORMATION_TFS"].type == str
    assert fields["FORMATION_MIN_SCORE"].type == float


def test_shared_selector_is_family_tf_and_side_distinct_and_fails_closed():
    n = 6
    arrays = {
        "formation_wedge_bull_score_15m": np.array([0, .8, 0, 0, 0, 0]),
        "formation_triangle_bull_score_1h": np.array([0, 0, .9, 0, 0, 0]),
        "formation_wedge_bear_score_15m": np.array([0, 0, 0, .85, 0, 0]),
    }
    cfg = replace(v12.QuickConfig(), FORMATION_WEDGE_ENTRY_ENABLED=True, FORMATION_TFS="15m")
    mask, score, family = formation_vector_mask(arrays, is_long=True, action="ENTRY", config=cfg, n=n)
    assert mask.tolist() == [False, True, False, False, False, False]
    assert score[1] == np.float32(.8)
    assert family[1] >= 0

    cfg = replace(cfg, FORMATION_WEDGE_ENTRY_ENABLED=False, FORMATION_TRIANGLE_ENTRY_ENABLED=True, FORMATION_TFS="1h")
    mask, _, _ = formation_vector_mask(arrays, is_long=True, action="ENTRY", config=cfg, n=n)
    assert mask.tolist() == [False, False, True, False, False, False]

    cfg = replace(cfg, FORMATION_TFS="4h")
    mask, score, family = formation_vector_mask(arrays, is_long=True, action="ENTRY", config=cfg, n=n)
    assert not mask.any()
    assert not score.any()
    assert np.all(family == -1)


def test_simulator_applies_formation_size_and_gain_qualified_exit(monkeypatch):
    n = 120
    close = np.full(n, 100.0)
    close[30:] = 110.0
    arrays = {
        "timestamps": np.arange(n, dtype=np.int64) * 180,
        "close_3m": close,
        "formation_wedge_bull_score_15m": np.eye(1, n, 5, dtype=np.float32)[0] * .8,
        "formation_triangle_bear_score_1h": (
            np.eye(1, n, 6, dtype=np.float32)[0] * .9
            + np.eye(1, n, 30, dtype=np.float32)[0] * .9
        ),
    }
    monkeypatch.setattr(v12, "compute_entry_signals", lambda *_: np.zeros(n, dtype=bool))
    monkeypatch.setattr(v12, "compute_exit_signals", lambda *_: np.zeros(n, dtype=bool))
    monkeypatch.setattr(v12, "compute_augment_signals", lambda *_: (np.zeros(n, dtype=bool), np.zeros(n)))
    monkeypatch.setattr(v12, "compute_reduce_signals", lambda *_: (np.zeros(n, dtype=bool), np.zeros(n)))
    monkeypatch.setattr(v12, "compute_regime_sizing_mult", lambda *_: np.ones(n))
    cfg = replace(
        v12.QuickConfig(),
        MODE="crypto",
        FORMATION_WEDGE_ENTRY_ENABLED=True,
        FORMATION_TRIANGLE_EXIT_ENABLED=True,
        FORMATION_TFS="15m,1h",
        FORMATION_POSITION_SIZE_MULT=1.5,
        FORMATION_EXIT_MIN_GAIN_PCT=5.0,
        MIN_HOLD_BARS=0,
        COOLDOWN_BARS=0,
        START_POSITION_SIZE=2000.0,
    )
    result = v12.simulate_one(arrays, "MU", True, cfg)
    first = result["ledger"][0]
    assert first["bar_entry"] == 5
    assert first["bar_exit"] == 30
    assert first["deployed"] == 3000.0
    assert first["entry_reason"].startswith("CLASSIC_FORMATION_ENTRY_WEDGE_score")
    assert first["exit_reason"].startswith("CLASSIC_FORMATION_EXIT_TRIANGLE_score")


def _mts_fixture(n=4):
    arrays = {"mfi_4h": np.full(n, 20.0), "mfi_1h": np.full(n, 80.0)}
    for tf in ("5m", "15m", "1h", "4h", "D"):
        arrays[f"stoch_k_{tf}"] = np.full(n, 50.0)
        arrays[f"stoch_d_{tf}"] = np.full(n, 50.0)
        arrays[f"wt1_{tf}"] = np.zeros(n)
        arrays[f"wt2_{tf}"] = np.ones(n)
        arrays[f"dc_position_{tf}"] = np.full(n, .5)
        arrays[f"wt_cross_bars_ago_{tf}"] = np.full(n, 999.0)
    # Keep the ordinary Tradier entry union open independently of MTS weights.
    arrays["stoch_k_5m"] = np.full(n, 5.0)
    arrays["stoch_d_5m"] = np.full(n, 1.0)
    return arrays


def test_each_declared_mts_weight_reweights_its_completed_tf():
    weight_fields = {
        "5m": "MTS_WEIGHT_5m",
        "15m": "MTS_WEIGHT_15m",
        "1h": "MTS_WEIGHT_1h",
        "4h": "MTS_WEIGHT_4h",
        "D": "MTS_WEIGHT_D",
    }
    for tf, field in weight_fields.items():
        arrays = _mts_fixture()
        arrays[f"stoch_k_{tf}"] = np.full(4, 5.0)
        arrays[f"stoch_d_{tf}"] = np.full(4, 1.0)
        arrays[f"wt1_{tf}"] = np.full(4, -50.0)
        arrays[f"wt2_{tf}"] = np.full(4, -40.0)
        arrays[f"dc_position_{tf}"] = np.full(4, .05)
        cfg = replace(
            v12.QuickConfig(),
            MODE="tradier",
            WT_COMPOSITE_SCORING_ENABLED=False,
            WT_COMPOSITE_SCORING_ENABLED_TRADIER=False,
            MTS_GATE_ENABLED_TRADIER=False,
            MTS_WEIGHT_5m=.01,
            MTS_WEIGHT_15m=.01,
            MTS_WEIGHT_1h=.01,
            MTS_WEIGHT_4h=.01,
            MTS_WEIGHT_D=.01,
        )
        _, low_score = v12._tradier_signal_score_family(arrays, 4, True, cfg)
        _, high_score = v12._tradier_signal_score_family(
            arrays, 4, True, replace(cfg, **{field: 100.0})
        )
        assert np.all(high_score > low_score), field


def test_entry_primary_tf_and_generic_wt_composite_switch_are_causal():
    arrays = _mts_fixture()
    # Isolate the MFI/primary-TF branch from the independent 5m stoch union.
    arrays["stoch_k_5m"] = np.full(4, 50.0)
    arrays["stoch_d_5m"] = np.full(4, 50.0)
    base = replace(
        v12.QuickConfig(),
        MODE="tradier",
        WT_COMPOSITE_SCORING_ENABLED_TRADIER=False,
        WT_COMPOSITE_SCORING_ENABLED=False,
        MTS_GATE_ENABLED_TRADIER=False,
    )
    gate_4h, _ = v12._tradier_signal_score_family(arrays, 4, True, replace(base, ENTRY_PRIMARY_TF="4h"))
    gate_1h, _ = v12._tradier_signal_score_family(arrays, 4, True, replace(base, ENTRY_PRIMARY_TF="1h"))
    assert gate_4h.all()
    assert not gate_1h.any()

    arrays["wt_bull_alignment"] = np.zeros(4)
    arrays["wt_composite_long"] = np.full(4, 100.0)
    gate_off, _ = v12._tradier_signal_score_family(arrays, 4, True, base)
    gate_on, _ = v12._tradier_signal_score_family(
        arrays, 4, True, replace(base, WT_COMPOSITE_SCORING_ENABLED=True)
    )
    assert gate_off.all()
    assert not gate_on.any()
