import pytest

from mtf_atr_multitf_contract import CompletedATRParent, MtfAtrMultitfExit, params_from_recipe


RECIPE = {"family": "EXIT_MTF_ATR_TRAIL", "params": {"timeframes": ["1h", "4h", "D"], "atr_mult": 1.5, "min_profit_pct": .5, "min_confirming_tfs": 1}}


def parent(tf, ts, close, atr=2., observed=None):
    return CompletedATRParent(tf, ts, ts if observed is None else observed, close, atr)


def test_bg_multitf_atr_ratchets_completed_parents_and_profit_gate():
    route = MtfAtrMultitfExit(params_from_recipe(RECIPE), side="LONG")
    # First parent establishes trail 97; no adverse state.
    assert not route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=105, execution_low=99, parents=[parent("1h", 100, 100)]).exit_event
    # Ratchet rises to 107 (close 110 - 3); later close 106 is adverse and
    # satisfies the selected +0.5% profit requirement.
    assert not route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=112, execution_low=105, parents=[parent("1h", 200, 110)]).exit_event
    signal = route.step(active=True, entry_price=100, current_gain_pct=.5, execution_high=112, execution_low=105, parents=[parent("1h", 300, 106)])
    assert signal.exit_event and signal.confirming_timeframes == ("1h",)
    assert signal.reason.startswith("EXIT_MTF_ATR_TRAIL_1TF_x1.5_p0.5")
    assert signal.reclaim_reference == 112


def test_short_mirror_and_future_or_mutated_parent_fail_closed():
    route = MtfAtrMultitfExit(params_from_recipe(RECIPE), side="SHORT")
    route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=101, execution_low=95, parents=[parent("4h", 100, 100)])
    route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=96, execution_low=90, parents=[parent("4h", 200, 90)])
    assert route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=96, execution_low=90, parents=[parent("4h", 300, 94)]).exit_event
    assert "FUTURE_OR_INVALID_COMPLETED_PARENT:D" in route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=96, execution_low=90, parents=[parent("D", 400, 90, observed=399)]).blockers
    assert "MUTATED_COMPLETED_PARENT:4h" in route.step(active=True, entry_price=100, current_gain_pct=1., execution_high=96, execution_low=90, parents=[parent("4h", 300, 95)]).blockers


def test_wrong_timeframe_recipe_is_rejected():
    with pytest.raises(ValueError):
        params_from_recipe({"family": "EXIT_MTF_ATR_TRAIL", "params": {**RECIPE["params"], "timeframes": ["5m"]}})
