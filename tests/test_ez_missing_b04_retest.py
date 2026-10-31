import copy
import pickle

import numpy as np
import v12_quick_engine as V

from vec_decisions.ez_missing_b04_retest import (
    dc_high_break_retest_live_adapter,
    dc_high_break_retest_mask,
)


class Config:
    REENTRY_B04_DC_RETEST_ENABLED = True


def _safe(npz, key, n, default):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default)
    return np.asarray(value)


def test_b04_predicate_switch_and_side_masks():
    long_npz = {
        "close": np.array([100.2, 98.9]),
        "dc_high_4h": np.array([102.0, 102.0]),
        "dc_high_4h_ant": np.array([100.0, 100.0]),
        "stoch_k_15m": np.array([62.0, 62.0]),
        "stoch_d_15m": np.array([58.0, 58.0]),
        "stoch_k_15m_prev": np.array([60.0, 60.0]),
    }
    short_npz = {
        "close": np.array([99.8, 101.1]),
        "dc_low_1h": np.array([98.0, 98.0]),
        "dc_low_1h_ant": np.array([100.0, 100.0]),
        "stoch_k_15m": np.array([38.0, 38.0]),
        "stoch_d_15m": np.array([42.0, 42.0]),
        "stoch_k_15m_prev": np.array([40.0, 40.0]),
    }
    cfg = Config()
    long_mask, long_mult, long_tf = dc_high_break_retest_mask(long_npz, 2, True, cfg, _safe)
    short_mask, short_mult, short_tf = dc_high_break_retest_mask(short_npz, 2, False, cfg, _safe)
    assert np.flatnonzero(long_mask).tolist() == [0]
    assert np.flatnonzero(short_mask).tolist() == [0]
    assert long_mult[0] == 2.0 and long_tf[0] == "4h"
    assert short_mult[0] == 1.5 and short_tf[0] == "1h"
    cfg.REENTRY_B04_DC_RETEST_ENABLED = False
    assert not dc_high_break_retest_mask(long_npz, 2, True, cfg, _safe)[0].any()
    assert not dc_high_break_retest_mask(short_npz, 2, False, cfg, _safe)[0].any()


def test_b04_uses_priority_first_match_and_missing_inputs_fail_open():
    npz = {
        "close": np.array([100.2]),
        "dc_high_4h": np.array([102.0]),
        "dc_high_4h_ant": np.array([100.0]),
        "dc_high_1h": np.array([103.0]),
        "dc_high_1h_ant": np.array([100.0]),
        "stoch_k_15m": np.array([62.0]),
        "stoch_d_15m": np.array([58.0]),
        "stoch_k_15m_prev": np.array([60.0]),
    }
    mask, mult, tf = dc_high_break_retest_mask(npz, 1, True, Config(), _safe)
    assert mask.tolist() == [True]
    assert mult.tolist() == [2.0]
    assert tf.tolist() == ["4h"]
    missing = {"close": np.array([100.2]), "dc_high_4h": np.array([102.0]), "dc_high_4h_ant": np.array([100.0])}
    assert not dc_high_break_retest_mask(missing, 1, True, Config(), _safe)[0].any()


def test_b04_matches_live_helper_when_ltf_stochastic_is_mapped_to_15m():
    from ez_manage import check_dc_high_break_retest

    cases = [
        (True, 100.2, 102.0, 100.0, 62.0, 58.0, 60.0),
        (False, 99.8, 98.0, 100.0, 38.0, 42.0, 40.0),
        (True, 103.0, 102.0, 100.0, 62.0, 58.0, 60.0),
    ]
    cfg = Config()
    for is_long, price, current, previous, k, d, k_prev in cases:
        npz = {
            "close": np.array([price]),
            "dc_high_4h": np.array([current]),
            "dc_high_4h_ant": np.array([previous]),
            "dc_low_4h": np.array([current]),
            "dc_low_4h_ant": np.array([previous]),
            "stoch_k_15m": np.array([k]),
            "stoch_d_15m": np.array([d]),
            "stoch_k_15m_prev": np.array([k_prev]),
        }
        indicators = {
            "k_3m": k,
            "d_3m": d,
            "k_3m_prev": k_prev,
            "dc_high_4h": current,
            "dc_high_4h_ant": previous,
            "dc_low_4h": current,
            "dc_low_4h_ant": previous,
        }
        live_fires, _, live_mult = check_dc_high_break_retest(indicators, price, is_long)
        vec_mask, vec_mult, _ = dc_high_break_retest_mask(npz, 1, is_long, cfg, _safe)
        ltf_off_indicators = {**indicators, "k_15m": k, "d_15m": d, "k_15m_prev": k_prev}
        adapter_fires, _, adapter_mult = dc_high_break_retest_live_adapter(ltf_off_indicators, price, is_long)
        assert bool(vec_mask[0]) == live_fires
        assert float(vec_mult[0]) == live_mult
        assert adapter_fires == live_fires
        assert adapter_mult == live_mult


def test_b04_uses_only_prior_bar_when_explicit_stochastic_previous_is_absent():
    npz = {
        "close": np.array([100.2, 100.3, 100.4]),
        "dc_high_4h": np.array([102.0, 102.0, 102.0]),
        "dc_high_4h_ant": np.array([100.0, 100.0, 100.0]),
        "stoch_k_15m": np.array([60.0, 62.0, 59.0]),
        "stoch_d_15m": np.array([58.0, 58.0, 58.0]),
    }
    mask, _, _ = dc_high_break_retest_mask(npz, 3, True, Config(), _safe)
    assert mask.tolist() == [False, True, False]


def test_reentry_block_metadata_survives_copy_pickle_without_changing_dict_iteration():
    npz = {
        "close": np.array([100.2]),
        "dc_high_4h": np.array([102.0]),
        "dc_high_4h_ant": np.array([100.0]),
        "stoch_k_15m": np.array([62.0]),
        "stoch_d_15m": np.array([58.0]),
        "stoch_k_15m_prev": np.array([60.0]),
    }
    cfg = V.QuickConfig()
    cfg.REENTRY_B04_DC_RETEST_ENABLED = True
    blocks = V.compute_reentry_blocks(npz, 1, True, cfg)
    assert isinstance(blocks, dict)
    assert list(blocks).count("B04") == 1
    for clone in (copy.deepcopy(blocks), pickle.loads(pickle.dumps(blocks))):
        assert list(clone) == list(blocks)
        assert clone.size_multipliers["B04"].tolist() == [2.0]


def test_b04_switch_flips_simulate_one_trade_ledger():
    n = 120
    close = np.empty(n)
    close[:8] = 100.0 * (1.005 ** np.arange(8))
    close[8:] = close[7] * (0.999 ** np.arange(1, n - 7))
    npz = {
        "close": close,
        "close_15m": close,
        "timestamps": np.arange(n, dtype=float) * 900 + 1_700_000_000.0,
        "timestamp_15m": np.arange(n, dtype=float) * 900 + 1_700_000_000.0,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close * 0.999,
        "volume_15m": np.ones(n),
        "wt1_15m": np.array([5.0] * 7 + [20.0] * (n - 7)),
        "wt2_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 10.0),
        "dc_high_15m": np.full(n, 200.0),
        "stoch_k_15m": np.full(n, 62.0),
        "stoch_d_15m": np.full(n, 58.0),
        "stoch_k_15m_prev": np.full(n, 60.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
        "dc_high_4h": np.full(n, 102.0),
        "dc_high_4h_ant": np.full(n, 101.0),
    }

    def ledger_with_b04(enabled, winning_tf):
        fixture = dict(npz)
        if winning_tf == "4h":
            fixture["dc_high_4h_ant"] = np.full(n, 100.0)
        else:
            fixture["dc_high_15m"] = np.full(n, 102.0)
            fixture["dc_high_15m_ant"] = np.full(n, 100.0)
        cfg = V.QuickConfig()
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        cfg.REENTRY_B04_DC_RETEST_ENABLED = enabled
        cfg.MIN_HOLD_BARS = 0
        cfg.ENTRY_ISOLATE_FAMILY = "B04"
        cfg.CYCLE_TP_ENABLED = False
        for name, value in vars(cfg).items():
            if name.startswith("REENTRY_") and isinstance(value, bool) and name != "REENTRY_B04_DC_RETEST_ENABLED":
                setattr(cfg, name, False)
        return V.simulate_one(fixture, "B04_LEDGER", True, cfg, force_initial_seed=True)["ledger"]

    off_ledger = ledger_with_b04(False, "4h")
    on_ledger = ledger_with_b04(True, "4h")
    one_x_ledger = ledger_with_b04(True, "15m")
    off_b04 = [row for row in off_ledger if row.get("reason") == "B04"]
    on_b04 = [row for row in on_ledger if row.get("reason") == "B04"]
    one_x_b04 = [row for row in one_x_ledger if row.get("reason") == "B04"]
    assert off_b04 == []
    assert len(on_b04) == 1 and on_b04[0].get("type") == "OPEN"
    assert len(one_x_b04) == 1 and one_x_b04[0].get("type") == "OPEN"
    assert np.isclose(on_b04[0]["qty"], 2.0 * one_x_b04[0]["qty"])
    assert np.isclose(on_b04[0]["pos_deployed"], 2.0 * one_x_b04[0]["pos_deployed"])
