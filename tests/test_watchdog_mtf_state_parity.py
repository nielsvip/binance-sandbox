from types import SimpleNamespace

import numpy as np
import pytest

import mtf_live_evaluator as native
from vec_decisions.watchdog_mtf_gate import prepare, step

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("bands", ["dc,bb,wt", "dc,bb", "wt"])
@pytest.mark.parametrize("suspend", [True, False])
def test_attempted_entry_cadence_matches_real_native_state_transitions(monkeypatch, is_long, bands, suspend):
    rng = np.random.default_rng(608)
    n = 30
    data = {}
    for timeframe in ("3m", "15m", "1h", "4h", "D", "W"):
        for field in ("close", "dc_high", "dc_low", "bb_upper", "bb_lower", "wt1", "wt2"):
            data[f"{field}_{timeframe}"] = rng.uniform(1., 10., n)
            data[f"{field}_{timeframe}_prev"] = rng.uniform(0., 10., n)
        for field in ("rsi", "mfi", "dc_pct", "bb_pct_b", "relative_volume", "k", "d", "adx", "macd_hist", "ha_color"):
            data[f"{field}_{timeframe}"] = rng.uniform(-5., 100., n)
    data["dc_pos_1h"] = np.where(np.arange(n) % 3 == 0, 0.95, 0.5)
    data["current_price"] = np.full(n, 5.)
    cfg = SimpleNamespace(MODE="crypto", MTF_ARMED_ENTRY_ENABLED=True, MTF_ARMED_BANDTYPES=bands, MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED=suspend, MTF_REQUIRE_ARMED_ANY=True, MTF_GR_MIN_TFS=2, MTF_GR_MIN_IND=5)
    prepared = prepare(data, n, is_long, cfg, data["current_price"])
    monkeypatch.setattr(native, "save_mtf_states", lambda state: None)
    states = {}
    side = "LONG" if is_long else "SHORT"
    for index in (1, 2, 7, 9, 12, 19, 29):
        indicators = {key: values[index] for key, values in data.items()}
        expected = native.mtf_entry_filter_passes(states, "TEST", side, indicators, "crypto", cfg)
        assert step(prepared, index) == expected
        assert prepared["state"] == states[f"TEST_{side}"]

def test_per_symbol_block_does_not_mutate_armed_state():
    cfg = SimpleNamespace(MTF_ENTRY_BLOCKED=True)
    prepared = prepare({}, 1, True, cfg, np.array([100.]))
    before = repr(prepared["state"])
    assert step(prepared, 0) == (False, "MTF_PER_SYM_BLOCKED")
    assert repr(prepared["state"]) == before


def test_engine_watchdog_open_waits_for_native_arming(monkeypatch):
    import v12_quick_engine as engine
    from vec_decisions import watchdog_dc_breakout
    from tests.test_quick_srs_paired_15m import _cfg, _npz
    data = _npz()
    n = len(data["timestamps"])
    data["close_1h"] = np.full(n, 100.)
    data["wt1_1h"] = np.full(n, -1.)
    data["wt1_1h"][2:] = 1.
    data["wt2_1h"] = np.zeros(n)
    cfg = _cfg()
    settings = {"MOMENTUM_SMA_WATCHDOG_ENABLED": True, "WATCHDOG_DC_FORCE_OPEN_ENABLED": True, "WATCHDOG_DC_VEC_ENABLED": True,
                "ABLATION_DISABLE_QUICK_ENTRY": True, "QUICK_ENTRY_ABLATION_SUPPRESS_VEC": True, "FORCE_OPEN_REQUIRE_MTF_GR": True,
                "MTF_ARMED_ENTRY_ENABLED": True, "MTF_ARMED_HTF_LIST": "1h", "MTF_ARMED_BANDTYPES": "wt", "MTF_REQUIRE_ARMED_ANY": True,
                "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED": False, "MTF_ENTRY_REQUIRE_GR_FILTER": False,
                "STRUCTURAL_RANGE_SHIFT_EXIT": False, "MTF_ENTRY_BLOCKED": False, "OPTIMAL_HOLD_BARS_EXIT_ENABLED": False}
    for name, value in settings.items():
        setattr(cfg, name, value)
    proposals = np.zeros(n, dtype=bool)
    proposals[1:3] = True
    monkeypatch.setattr(watchdog_dc_breakout, "dc_breakout_open_mask", lambda *args: (proposals, np.full(n, "TEST_WATCHDOG")))
    result = engine.simulate_one(data, "WATCHDOG_MTF_TEST", True, cfg)
    opens = [row for row in result["ledger"] if row.get("type") == "OPEN"]
    assert opens and opens[0]["bar"] == 2


def test_native_entry_filter_explicit_defaults_override_golden_rule_fallback(monkeypatch):
    cfg = SimpleNamespace(MTF_REQUIRE_ARMED_ANY=False, GOLDEN_RULE_MIN_IND=0, GOLDEN_RULE_HTF_MIN_TFS=0)
    monkeypatch.setattr(native, "save_mtf_states", lambda state: None)
    expected = native.mtf_entry_filter_passes({}, "TEST", "LONG", {}, "crypto", cfg)
    assert expected == (False, "MTF_GR_FILTER_FAIL_3tf_5ind")
    assert step(prepare({}, 1, True, cfg, np.array([100.])), 0) == expected
