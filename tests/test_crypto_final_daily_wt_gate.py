import numpy as np
import pytest
import v12_quick_engine as V
from tests.test_quick_srs_paired_15m import _cfg, _npz
from vec_decisions.net_exit_gate import vector_exit_is_mandatory


@pytest.mark.parametrize("is_long", [True, False])
def test_daily_wt_final_gate_holds_discretionary_exit_until_support_ends(is_long):
    data = _npz()
    n = len(data["close"])
    prices = np.full(n, 101.0 if is_long else 99.0)
    prices[0] = 100.0
    for key in ("close", "close_15m", "open_15m", "high_15m", "low_15m"):
        data[key] = prices.copy()
    data["dc_low_15m"] = np.full(n, 80.0)
    data["dc_high_15m"] = np.full(n, 120.0)
    data["wt_velocity_4h"] = np.full(n, -20.0 if is_long else 20.0)
    data["wt1_D"] = np.full(n, 20.0 if is_long else -20.0)
    data["wt2_D"] = np.full(n, 10.0 if is_long else -10.0)
    data["wt1_D"][3:] *= -1.0
    def run(enabled):
        cfg = _cfg()
        cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
        cfg.AUGMENTED_DC_BREAK_ENABLED = False
        cfg.WT_4H_VEL_EXIT_ENABLED = True
        cfg.WT_4H_VEL_EXIT_REQUIRE_K_EXTREME = False
        cfg.WT_4H_VEL_EXIT_REQUIRE_PROFIT = True
        cfg.HTF_TREND_VETO_ON_REDUCE_ENABLED = enabled
        cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = False
        cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = False
        return V.simulate_one(data, "WT_GATE_TEST", is_long, cfg, force_initial_seed=True)
    off, on = run(False), run(True)
    closes = lambda result:[r for r in result["ledger"] if r.get("type") == "CLOSE" and not r.get("unrealised")]
    assert closes(off)[0]["bar_exit"] == 1
    assert closes(on)[0]["bar_exit"] == 3
    assert closes(on)[0]["entry_price"] == 100.0


def test_mandatory_event_markers_match_existing_net_exit_contract():
    assert vector_exit_is_mandatory({"reason":"FIXED_STOP", "mandatory_stop_kind":"DC15_FROZEN_DEFAULT"})
    assert vector_exit_is_mandatory({"reason":"DELTA_EXIT_DC15M_FLOOR_BREAK", "mandatory_stop_kind":"DC15M_FORCE_EXIT_ARMED"})
    assert not vector_exit_is_mandatory({"reason":"STRUCTURAL_RANGE_SHIFT_LONG_CASCADE_dc_4h"})
    assert not vector_exit_is_mandatory({"reason":"WT_4H_VEL_EXIT_vel=-12"})
