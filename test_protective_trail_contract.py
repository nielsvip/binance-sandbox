from pathlib import Path

from protective_trail_contract import (
    BASE_FAMILY,
    ProtectiveTrailParams,
    params_from_recipe,
    protective_trail_step,
    recipe_overrides,
)


WDAY_EXIT = {
    "family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
    "params": {
        "arm_timeframe": "4h",
        "break_buffer_atr": 0.5,
        "distance_mult": 1.0,
        "lookback": 6,
        "mode": "STDEV",
        "trail_timeframe": "5m",
    },
}


def _snapshot(tick, trail_source, trail_close, *, arm_source=50, arm_high=100, arm_low=90, arm_close=95):
    return {
        "_tick_ts": tick,
        "_completed_source_ts_5m": trail_source,
        "close_5m": trail_close,
        "high_5m": trail_close + 1,
        "low_5m": trail_close - 1,
        "atr_5m": 2,
        "_completed_source_ts_4h": arm_source,
        "close_4h": arm_close,
        "high_4h": arm_high,
        "low_4h": arm_low,
        "atr_4h": 4,
    }


def test_wday_extended_recipe_resolves_only_to_base_live_knobs():
    params = params_from_recipe(WDAY_EXIT)
    assert params == ProtectiveTrailParams("4h", "5m", "STDEV", 0.5, 1.0, 6)
    overrides = recipe_overrides(WDAY_EXIT)
    assert overrides == {
        "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED": True,
        "BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME": "4h",
        "BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME": "5m",
        "BOTTOM_A_PROTECTIVE_TRAIL_MODE": "STDEV",
        "BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR": 0.5,
        "BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT": 1.0,
        "BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK": 6,
    }
    assert all("EXTENDED" not in key for key in overrides)
    assert BASE_FAMILY == "BOTTOM_A_PROTECTIVE_TRAIL"


def test_wday_short_arms_on_completed_4h_break_then_fires_later_stdev_trail():
    params = params_from_recipe(WDAY_EXIT)
    state = {}

    # Warm the rolling 5m distance while flat, as the vector book does.
    for source, close in zip((100, 200, 300, 400, 500, 600), (100, 99, 98, 97, 96, 95)):
        assert protective_trail_step(
            state,
            _snapshot(source, source, close),
            side="SHORT",
            active=False,
            params=params,
        ) is None

    # Prior 4h high=100, prior ATR=4: close=103 breaks 100 + 0.5*4.
    arm = _snapshot(
        650,
        600,
        95,
        arm_source=650,
        arm_high=104,
        arm_low=92,
        arm_close=103,
    )
    assert protective_trail_step(
        state, arm, side="SHORT", active=True, params=params
    ) is None
    assert state["armed"] is True
    assert state["arm_source_ts"] == 650

    # First strictly later completed 5m parent initializes/ratchets the stop.
    first_later = _snapshot(
        700, 700, 90, arm_source=650, arm_high=104, arm_low=92, arm_close=103
    )
    assert protective_trail_step(
        state, first_later, side="SHORT", active=True, params=params
    ) is None

    # A later rebound crosses the locked SHORT stop and exits.
    rebound = _snapshot(
        800, 800, 94, arm_source=650, arm_high=104, arm_low=92, arm_close=103
    )
    signal = protective_trail_step(
        state, rebound, side="SHORT", active=True, params=params
    )
    assert signal is not None
    assert signal.reason == "BOTTOM_A_STDEV_5m"
    assert signal.arm_source_ts == 650
    assert signal.trail_source_ts == 800
    assert signal.reclaim_reference == 89

    # A forward-broadcast copy of the same completed parent is not re-fired.
    assert protective_trail_step(
        state, rebound, side="SHORT", active=True, params=params
    ) is None


def test_adverse_parent_completed_while_flat_cannot_arm_a_later_position():
    params = params_from_recipe(WDAY_EXIT)
    state = {}
    prior = _snapshot(100, 100, 100)
    adverse = _snapshot(
        200, 200, 103, arm_source=200, arm_high=104, arm_low=92, arm_close=103
    )
    assert protective_trail_step(
        state, prior, side="SHORT", active=False, params=params
    ) is None
    assert protective_trail_step(
        state, adverse, side="SHORT", active=False, params=params
    ) is None
    assert protective_trail_step(
        state, adverse, side="SHORT", active=True, params=params
    ) is None
    assert state["armed"] is False


def test_real_path_is_default_off_and_calls_shared_contract_without_alias_switch():
    root = Path(__file__).resolve().parent
    config_text = (root / "config_tradier.py").read_text()
    manage_text = (root / "tradier_manage.py").read_text()
    engine_text = (root / "backtest_v8_engine.py").read_text()
    assert "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED: bool = False" in config_text
    assert "_protective_trail_step(" in manage_text
    assert '"BOTTOM_A_PROTECTIVE_TRAIL_ENABLED", False' in manage_text
    assert "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED_ENABLED" not in config_text
    assert "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED_ENABLED" not in manage_text
    assert "await queue_trade_action(" in manage_text
    evaluate_stop = manage_text[manage_text.index("async def evaluate_stop"):]
    assert "_bottom_a_eval_signal = _ordinary_bottom_a_exit(" in evaluate_stop
    assert '"MANDATORY_REENTRY"' in evaluate_stop
    assert "return (\n                        True,\n                        _bottom_a_eval_reason," in evaluate_stop
    assert "import tradier_manage as tm_mod" in engine_text
    assert "tm_mod.process_position(" in engine_text
