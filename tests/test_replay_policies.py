from types import SimpleNamespace

import pytest

from vec_decisions.replay_policies import configure_replay_settings, override_check_due


def test_native_replay_settings_preserve_active_risk_values():
    config_class = type(
        "Config",
        (),
        {
            "STRICT_NO_LOSS_ACCOUNTS": ["ang"],
            "FAST_CUT_LOSS_THRESHOLD": -999.0,
            "BREAKOUT_GUARD_LOSS_THRESHOLD": -999.0,
            "STOP_LOSS_THRESHOLD": 999.0,
            "UNIVERSAL_NOLOSS_GATE": False,
        },
    )
    config = config_class()
    module = SimpleNamespace(config=config)
    result = configure_replay_settings(
        config, config_class, [module], native_faithful=True
    )
    assert result["mode"] == "native_faithful"
    assert result["effective"] == result["before"]
    assert result["effective"]["STRICT_NO_LOSS_ACCOUNTS"] == ["ang"]
    assert result["effective"]["FAST_CUT_LOSS_THRESHOLD"] == -999.0
    assert result["effective"]["BREAKOUT_GUARD_LOSS_THRESHOLD"] == -999.0
    assert result["effective"]["STOP_LOSS_THRESHOLD"] == 999.0
    assert result["effective"]["UNIVERSAL_NOLOSS_GATE"] is False


def test_legacy_replay_policy_retains_explicit_technical_stop_compatibility():
    config_class = type(
        "Config",
        (),
        {
            "STRICT_NO_LOSS_ACCOUNTS": ["ang"],
            "FAST_CUT_LOSS_THRESHOLD": -999.0,
            "BREAKOUT_GUARD_LOSS_THRESHOLD": -999.0,
            "STOP_LOSS_THRESHOLD": 999.0,
            "UNIVERSAL_NOLOSS_GATE": False,
        },
    )
    config = config_class()
    module = SimpleNamespace(config=config)
    result = configure_replay_settings(
        config, config_class, [module], native_faithful=False
    )
    assert result["mode"] == "legacy_sweep_compatibility"
    assert result["before"]["FAST_CUT_LOSS_THRESHOLD"] == -999.0
    assert result["effective"]["STRICT_NO_LOSS_ACCOUNTS"] == []
    assert result["effective"]["FAST_CUT_LOSS_THRESHOLD"] == -3.0
    assert result["effective"]["BREAKOUT_GUARD_LOSS_THRESHOLD"] == -3.0
    assert result["effective"]["STOP_LOSS_THRESHOLD"] == 3.0


def test_override_cadence_is_explicit_and_separate_from_live_twenty_callback_clock():
    assert [override_check_due("per_bar", step) for step in range(1, 5)] == [True] * 4
    assert [
        step for step in range(1, 41) if override_check_due("every_20_callbacks", step)
    ] == [20, 40]
    assert not override_check_due("disabled", 1)
    with pytest.raises(ValueError, match="unsupported override check replay cadence"):
        override_check_due("every_15m_maybe", 1)


def test_scalar_replay_cadence_calls_native_override_producer_and_drains_real_queue():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("backtest_v12_engine.py")
    tree = ast.parse(source.read_text())
    simulation = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_simulation"
    )
    producer_calls = [
        node
        for node in ast.walk(simulation)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "_process_single_override_check"
    ]
    assert len(producer_calls) == 1
    call = producer_calls[0].value
    assert any(
        isinstance(keyword.value, ast.Name) and keyword.value.id == "order_queue"
        for keyword in call.keywords
    ) or any(isinstance(arg, ast.Name) and arg.id == "order_queue" for arg in call.args)
    calls = [
        node
        for node in ast.walk(simulation)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "join"
        and isinstance(node.value.func.value, ast.Attribute)
        and node.value.func.value.attr == "_orders"
    ]
    assert calls
    assert "V12_REPLAY_OVERRIDE_CHECK_CADENCE" in source.read_text()
    assert "V12_REAL_EXECUTE" in source.read_text()
