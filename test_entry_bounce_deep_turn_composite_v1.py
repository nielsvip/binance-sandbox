from pathlib import Path
from types import SimpleNamespace

import pytest

from entry_bounce_deep_turn_composite_v1 import (
    PREFIX,
    evaluate_bounce_deep_turn_composite_v1,
    wday_short_recipe_overrides,
)


def _cfg(**changes):
    values = wday_short_recipe_overrides()
    values.update(changes)
    return SimpleNamespace(**values)


def _short_indicators():
    return {
        "dc_high_5m": 100.0,
        "stoch_k_4h": 60.0,
        "stoch_k_1h": 70.0,
        "stoch_k_1h_prev": 75.0,
        "stoch_d_1h": 65.0,
        "stoch_k_5m": 30.0,
        "stoch_d_5m": 40.0,
        "stoch_k_15m": 45.0,
        "stoch_d_15m": 55.0,
        "wt1_15m": 5.0,
        "wt2_15m": 4.0,
    }


def test_default_off_even_when_recipe_matches():
    cfg = _cfg(**{f"{PREFIX}_ENABLED": False})
    assert evaluate_bounce_deep_turn_composite_v1(
        _short_indicators(), "SHORT", 99.0, cfg, symbol="WDAY"
    ) is None


def test_frozen_wday_short_recipe_fires_with_two_of_three_confirmation():
    result = evaluate_bounce_deep_turn_composite_v1(
        _short_indicators(), "SHORT", 99.0, _cfg(), symbol="WDAY"
    )
    assert result is not None
    assert result["side"] == "SHORT"
    assert result["confirmation_votes"] == 2
    assert result["bounce_distance"] == pytest.approx(0.01)
    assert result["reason"].startswith(f"{PREFIX}_SHORT_")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dc_high_5m", 98.0),       # price is above the channel
        ("stoch_k_4h", 50.0),      # strict > 100 - deep
        ("stoch_k_1h", 60.0),      # strict > 100 - turn
        ("stoch_k_1h_prev", 69.0), # no downward turn; k1 is also > d1
        ("stoch_d_5m", 20.0),      # removes a confirming vote
    ],
)
def test_each_frozen_gate_fails_closed(field, value):
    indicators = _short_indicators()
    indicators[field] = value
    assert evaluate_bounce_deep_turn_composite_v1(
        indicators, "SHORT", 99.0, _cfg(), symbol="WDAY"
    ) is None


def test_wrong_symbol_side_and_missing_required_field_fail_closed():
    indicators = _short_indicators()
    assert evaluate_bounce_deep_turn_composite_v1(
        indicators, "SHORT", 99.0, _cfg(), symbol="MU"
    ) is None
    assert evaluate_bounce_deep_turn_composite_v1(
        indicators, "LONG", 99.0, _cfg(), symbol="WDAY"
    ) is None
    del indicators["wt2_15m"]
    assert evaluate_bounce_deep_turn_composite_v1(
        indicators, "SHORT", 99.0, _cfg(), symbol="WDAY"
    ) is None


def test_live_k_aliases_match_npz_stoch_aliases():
    npz_names = _short_indicators()
    live_names = {
        key.replace("stoch_", ""): value for key, value in npz_names.items()
    }
    expected = evaluate_bounce_deep_turn_composite_v1(
        npz_names, "SHORT", 99.0, _cfg(), symbol="WDAY"
    )
    actual = evaluate_bounce_deep_turn_composite_v1(
        live_names, "SHORT", 99.0, _cfg(), symbol="WDAY"
    )
    assert actual == expected


def test_long_mirror_uses_research_threshold_semantics():
    indicators = {
        "dc_low_5m": 100.0,
        "k_4h": 40.0,
        "k_1h": 30.0,
        "k_1h_prev": 25.0,
        "d_1h": 35.0,
        "k_5m": 40.0,
        "d_5m": 30.0,
        "k_15m": 55.0,
        "d_15m": 45.0,
        "wt1_15m": 4.0,
        "wt2_15m": 5.0,
    }
    cfg = _cfg(
        **{
            f"{PREFIX}_SIDE": "LONG",
            f"{PREFIX}_SYMBOLS": ("WDAY",),
        }
    )
    result = evaluate_bounce_deep_turn_composite_v1(
        indicators, "LONG", 101.0, cfg, symbol="WDAY"
    )
    assert result is not None
    assert result["confirmation_votes"] == 2


def test_live_and_v8_share_one_action_site():
    root = Path(__file__).resolve().parent
    quick = (root / "ez_positions_quick.py").read_text()
    v8 = (root / "backtest_v8_engine.py").read_text()
    config = (root / "config_tradier.py").read_text()
    assert "evaluate_bounce_deep_turn_composite_v1(" in quick
    assert "check_entry_candidates_for_account(" in v8
    assert "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED: bool = False" in config
    # Guard against a divergent V8 copy of the predicate.
    assert "evaluate_bounce_deep_turn_composite_v1(" not in v8

