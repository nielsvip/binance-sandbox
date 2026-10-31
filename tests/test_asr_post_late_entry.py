import numpy as np
import pytest

from vec_decisions.asr_post_late_entry import (
    apply_asr_post_late_entry_array,
    market_quality_parts_array,
)


class Cfg:
    MARKET_QUALITY_SCORE_ENABLED = True
    DELTA_ENGINE_ENABLED = True
    DELTA_MIN_TF_FOR_ACTION = 2
    DELTA_ENTRY_SCORE_BONUS = 15
    DELTA_ENTRY_SCORE_PENALTY = -25
    DELTA_HTF_GATE = "4h_D"


def test_market_quality_matches_native_rate_scalar_both_sides():
    from ez_positions_quick import _market_quality_score
    rng = np.random.default_rng(119)
    n = 200
    numeric = ("bb_width_1h", "bb_width_4h", "dc_width_1h", "dc_width", "dc_width_4h",
               "adx_1h", "adx_4h", "sma_200_1h", "relative_volume_15m", "relative_volume_1h",
               "mfi_15m", "mfi_1h", "mfi_4h", "rsi_15m", "rsi_1h", "rsi_4h",
               "k_3m", "k_15m", "k_1h", "rsi_2_1h")
    labels = ("wt_signal_15m", "wt_signal_1h", "ha_3m", "ha_3m_prev", "ha_15m", "ha_15m_prev",
              "wt_divergence_1h", "wt_divergence_4h", "wt_momentum_state_1h", "wt_momentum_state_4h", "bar_pattern_15m")
    for is_long in (True, False):
        values = {key: rng.uniform(0, 100, n) for key in numeric}
        values["sma_200_1h"] = rng.uniform(80, 120, n)
        values["current_price"] = rng.uniform(80, 120, n)
        for key in labels:
            values[key] = rng.choice(["", "BUY", "SELL", "green", "red", "BULL", "BEAR",
                                      "EXHAUST_UP", "EXHAUST_DOWN", "hammer", "shooting_star"], n)
        for key in ("stoch_crossover_15m", "stoch_crossunder_15m", "stoch_crossover_1h", "stoch_crossunder_1h"):
            values[key] = rng.choice([False, True], n)
        q, r = market_quality_parts_array(values, values["current_price"], is_long)
        for i in range(n):
            native_q, native_r, _, _ = _market_quality_score({key: val[i] for key, val in values.items()}, is_long)
            assert q[i] == pytest.approx(native_q)
            assert r[i] == pytest.approx(native_r)


def test_post_late_delta_bonus_penalty_and_htf_checks_are_causal():
    n = 4
    npz = {"wt1_4h": [2, 0, 0, 0], "wt2_4h": [1, 1, 1, 1],
           "wt1_D": [2, 2, 0, 0], "wt2_D": [1, 1, 1, 1]}
    state = {"current_price": np.full(n, 100.0),
             "delta_tracker_available": [True] * n,
             "delta_signal_present": [True, True, False, True],
             "delta_entry_ok": [True, False, True, True],
             "delta_active_tfs": [2, 1, 2, 3]}
    got, masks = apply_asr_post_late_entry_array(npz, Cfg(), True, state, np.zeros(n))
    assert got.tolist() == [50.0, -15.0, 35.0, 0.0]
    assert masks["DELTA_CONFIRM"].tolist() == [True, False, False, True]
    assert masks["DELTA_OPPOSE"].tolist() == [False, True, False, False]
    assert masks["DELTA_HTF_AGAINST"].tolist() == [False, True, False, True]
    assert masks["DELTA_HTF_4H_AGAINST"].tolist() == [False, True, False, True]
    assert masks["DELTA_HTF_D_AGAINST"].tolist() == [False, False, False, True]
    assert masks["family:B_SRS_ENTRY_FAMILY"].all()


def test_active_delta_tracker_cannot_silently_default_missing_result():
    with pytest.raises(ValueError, match="delta_signal_present"):
        apply_asr_post_late_entry_array({}, Cfg(), True, {"current_price": [100], "delta_tracker_available": [True]}, [0])


def test_disabled_delta_engine_needs_no_tracker_state():
    class Disabled:
        MARKET_QUALITY_SCORE_ENABLED = False
        DELTA_ENGINE_ENABLED = False
    score, masks = apply_asr_post_late_entry_array({}, Disabled(), False, {}, [3])
    assert score.tolist() == [38]
    assert not masks["MARKET_QUALITY_SCORE"].any()


def test_native_delta_signal_without_speed_z_keeps_entry_flag():
    import ast
    import time
    from pathlib import Path
    from types import SimpleNamespace

    from wt_dc_delta import DeltaSignal

    tree = ast.parse(Path("ez_positions_quick.py").read_text())
    rate = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616
    )
    branch = next(
        node for node in ast.walk(rate)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
        and node.test.id == "_delta_sig"
        and any(
            isinstance(child, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_delta_active_tfs" for target in child.targets)
            for child in node.body
        )
    )
    signal = DeltaSignal()
    signal.entry_long = True
    signal.zone_action = "BUY"
    signal.bull_tf_count = 1
    env = {
        "_delta_sig": signal,
        "_delta_active_tfs": 0,
        "_delta_z": 0.0,
        "_delta_entry_ok": False,
        "_delta_exit_ok": False,
        "is_long": True,
        "position_key": "ang:FIXTURE_LONG",
        "tracker_manager": SimpleNamespace(_rate_delta_sig_cache={}),
        "time": time,
    }
    block = ast.Module(body=branch.body, type_ignores=[])
    exec(compile(block, "ez_positions_quick.py", "exec"), env)
    assert env["_delta_entry_ok"] is True
    assert env["_delta_active_tfs"] == 1
    assert env["_delta_z"] == 0.0
