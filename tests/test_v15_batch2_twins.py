"""Batch2 twin tests: FROZEN capture semantics, FH orange knob threading, registry/spec pins."""
import json

import vec_decisions.generic_filter_tf as G
import vec_decisions.twin_orange_ports_b as OB
import vec_decisions.twin_yellow_filters as T


def test_frozen_fires_long_short():
    assert T.frozen_stop_fires(100.0, 99.0, -1.5, True) is True
    assert T.frozen_stop_fires(100.0, 101.0, -1.5, False) is True


def test_frozen_no_fire_cases():
    assert T.frozen_stop_fires(100.0, 99.0, 0.5, True) is False
    assert T.frozen_stop_fires(100.0, 99.0, 0.0, True) is False
    assert T.frozen_stop_fires(0.0, 99.0, -1.5, True) is False
    assert T.frozen_stop_fires(None, 99.0, -1.5, True) is False
    assert T.frozen_stop_fires(100.0, 101.0, -1.5, True) is False
    assert T.frozen_stop_fires(100.0, 99.0, -1.5, False) is False


def test_frozen_bb_key_side_mapping():
    assert T.frozen_bb_key("15m", "lower", True) == "bb_lower_15m"
    assert T.frozen_bb_key("15m", "lower", False) == "bb_upper_15m"
    assert T.frozen_bb_key("1h", "upper", True) == "bb_lower_1h"
    assert T.frozen_bb_key("4h", "basis", True) == "bb_basis_4h"


def test_frozen_stop_tf_off_and_value():
    assert T.frozen_stop_tf("OFF", "1h") is None
    assert T.frozen_stop_tf("4h", "1h") == "4h"


def _fh_cfg(knob="15m", on=True):
    return {"FH_MOMENTUM_ENABLED": on, "MODE": "crypto", "FH_MOMENTUM_FILTER_TF": knob}


def _fh_ind(dc15=0.1, dc4h=0.1):
    return {"timestamps": 50000.0, "open_D": 100.0, "close": 101.0, "dc_position_15m": dc15, "dc_position_4h": dc4h, "mfi_1h": 60.0}


def test_orange_fh_knob_off_skips_dc_leg():
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.9), True, _fh_cfg("15m")) is False
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.9), True, _fh_cfg("OFF")) is True
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.9), True, _fh_cfg("off")) is True


def test_orange_fh_knob_selects_tf():
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.9, dc4h=0.1), True, _fh_cfg("4h")) is True
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.1, dc4h=0.9), True, _fh_cfg("4h")) is False


def test_orange_fh_knob_coerce_and_master():
    assert OB._fh_momentum_fires(_fh_ind(dc15=0.9), True, _fh_cfg("3m")) is False
    assert OB._fh_momentum_fires(_fh_ind(), True, _fh_cfg("15m", on=False)) is None


def test_registry_batch2_verdicts():
    assert T.REGISTRY["FH_MOMENTUM_FILTER_TF"]["verdict"] == "WIRED-VEC-INLINE"
    assert T.REGISTRY["FH_MOMENTUM_FILTER_TF"]["kind"] == "fh_momentum_dc_leg"
    assert T.REGISTRY["MANDATORY_REENTRY_WT_FILTER_TF_MODE"]["verdict"] == "WIRED-STOCKS-ONLY"
    assert T.REGISTRY["FROZEN_STOP_FILTER_TF"]["verdict"] == "WIRED-BOTH-SPEC"
    for n in ("DELTA_ENGINE_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "CIRCUIT_SHARPE_GATES_FILTER_TF"):
        assert T.REGISTRY[n]["verdict"] == "WIRED-VEC-MAP"
        assert T.REGISTRY[n]["kind"] == "wt_cross_side"
        assert G.FILTER_TF_MAP[n] == ("entry", "wt_cross_side")


def test_vec_master_defaults_match_live():
    import v12_quick_engine as V
    assert V.QuickConfig.MTF_DC_REJECT_EXIT_ENABLED is False
    assert V.QuickConfig.BB_FROZEN_STOP_ENABLED is False
    assert V.QuickConfig.FH_MOMENTUM_ENABLED is True


def test_hook_spec_bucket_pruned():
    h = json.load(open("hook_spec_yellow_filters.json"))
    wired = {"FH_MOMENTUM_FILTER_TF", "DELTA_ENGINE_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "CIRCUIT_SHARPE_GATES_FILTER_TF"}
    for e in h["noop"]:
        for s in e.get("switches", []):
            assert s not in wired, s
