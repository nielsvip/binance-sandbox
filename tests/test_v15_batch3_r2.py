"""Batch3 R2 tests: r2 rotor semantics, registry/hook/ledger pins, BLOCKED holds."""
import json
from types import SimpleNamespace

import vec_decisions.twin_yellow_filters as T


def _cfg(**kw):
    d = {"R2_PEAK_MIN_PCT": 0.5, "WT_15M_VEL_SLOW_GAIN_BAND_PCT": 0.10, "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT": 0.01, "WT_VEL_DECEL_RATIO": 0.5, "WT_VEL_USE_DECEL_RATIO_ONLY": True, "WT_15M_VEL_NEAR_ZERO_THRESHOLD": 0.1}
    d.update(kw)
    return SimpleNamespace(**d)


def test_r2_eff_tfs_off_and_default_is_family():
    assert T.r2_eff_tfs("OFF", ("15m",)) == ("15m",)
    assert T.r2_eff_tfs("15m", ("15m",)) == ("15m",)
    assert T.r2_eff_tfs("OFF", ("1h", "4h", "D")) == ("1h", "4h", "D")
    assert T.r2_eff_tfs("D", ("1h", "4h", "D")) == ("D",)
    assert T.r2_eff_tfs("1h", ("15m",)) == ("1h",)


def test_r2_vel_prev_venues():
    assert T.r2_vel_prev(-2.0, 3.0) == -5.0
    assert T.r2_vel_prev(-2.0, 3.0, None, True) == -2.0
    assert T.r2_vel_prev(-2.0, 3.0, -5.0, True) == -5.0
    assert T.r2_vel_prev(None, None) == 0.0


def test_r2_leg_fires_matrix():
    assert T.r2_leg_fires(-2.0, -5.0, True) == (True, "DECEL")
    assert T.r2_leg_fires(2.0, 5.0, False) == (True, "DECEL")
    assert T.r2_leg_fires(2.0, 5.0, True) == (False, "")
    assert T.r2_leg_fires(-2.0, -5.0, False) == (False, "")
    assert T.r2_leg_fires(-2.0, 0.0, True) == (False, "")
    assert T.r2_leg_fires(-0.05, -0.06, True, 0.5, False, 0.1) == (True, "DYING")
    assert T.r2_leg_fires(-0.05, -0.06, True, 0.5, True, 0.1) == (False, "")
    assert T.r2_leg_fires(None, None, True) == (False, "")


def test_r2_htf_hold_veto():
    assert T.r2_htf_hold_veto(2.0, 1.0, True) is True
    assert T.r2_htf_hold_veto(1.0, 2.0, True) is False
    assert T.r2_htf_hold_veto(1.0, 2.0, False) is True
    assert T.r2_htf_hold_veto(2.0, 1.0, False) is False
    assert T.r2_htf_hold_veto(0.0, 0.0, True) is False


def test_r2_bar_fires_crypto_gate_and_veto():
    legs = [(-2.0, 3.0, None, "15m")]
    assert T.r2_bar_fires(True, 0.05, 0.6, legs, 1.0, 2.0, _cfg()) == (True, "15m", "DECEL")
    assert T.r2_bar_fires(True, 0.05, 0.6, legs, 2.0, 1.0, _cfg()) == (False, None, "")
    assert T.r2_bar_fires(True, 0.05, 0.4, legs, 1.0, 2.0, _cfg()) == (False, None, "")
    assert T.r2_bar_fires(True, 0.005, 0.6, legs, 1.0, 2.0, _cfg()) == (False, None, "")
    assert T.r2_bar_fires(True, 0.15, 0.6, legs, 1.0, 2.0, _cfg()) == (False, None, "")
    assert T.r2_bar_fires(True, 0.05, 0.6, [], 1.0, 2.0, _cfg()) == (False, None, "")


def test_r2_bar_fires_stocks_prev_and_no_veto():
    assert T.r2_bar_fires(True, 0.05, 0.6, [(-2.0, 3.0, None, "1h")], 1.0, 2.0, _cfg(), True) == (False, None, "")
    assert T.r2_bar_fires(True, 0.05, 0.6, [(-2.0, 3.0, -5.0, "1h")], 2.0, 1.0, _cfg(), True) == (True, "1h", "DECEL")


def test_registry_batch3_r2():
    r = T.REGISTRY["EXIT_R1_R2_FILTER_TF"]
    assert r["verdict"] == "WIRED-R2-ONLY"
    assert r["kind"] == "r2_vel_slow"
    assert r["master"] == "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED"


def test_hook_spec_r2_ready_blocked_hold():
    h = json.load(open("hook_spec_yellow_filters.json"))
    ez = [e for e in h["insertions"] if e.get("id") == "EZ-R2-TFS"][0]
    assert ez["status"] == "READY"
    noop = set()
    for e in h["noop"]:
        noop.update(e.get("switches", []))
    assert "EXIT_R1_R2_FILTER_TF" not in noop
    for n in ("EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF", "FIRST_OPEN_THROTTLE_FILTER_TF", "NEWBORN_PROTECT_FILTER_TF", "OPEN_INTENT_SIZE_GATES_FILTER_TF"):
        assert n in noop, n


def test_vec_r2_master_default_pinned():
    import v12_quick_engine as V
    # Known live divergence: live defaults True (ez:50131/tr:11586), vec False —
    # the twin honors cfg master; flipping the default = BASE shift, post-open call.
    assert V.QuickConfig().WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED is False


def test_ledger_batch3_pruned():
    d = json.load(open("data/vec_unwired.json"))
    assert "EXIT_R1_R2_FILTER_TF" not in d["filters"]
    assert len(d["filters"]) == 17
