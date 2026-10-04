"""NOLOSS-hold + STOP_LOSS live-twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.noloss_hold reproduces v12_quick_engine.simulate_one
semantics exactly:
  NOLOSS (vec ~13926-13938): hold TECHNICAL exits at a loss unless the
    WT-5OF5 bypass passes or the DC-recovery escape fires.
  STOP_LOSS (vec ~13710): fixed-% full close at gain% <= -STOP_LOSS_PCT.
"""
import vec_decisions.noloss_hold as X


def _get(d):
    return lambda k, default: d.get(k, default)


def test_noloss_off_inert():
    assert X.noloss_hold_loss_exit(_get({}), True, -5.0, 100.0, 95.0, {}) == (False, "")
    assert X.noloss_hold_loss_exit(_get({"NOLOSS_ENABLED": False}), True, -5.0, 100.0, 95.0, {}) == (False, "")


def test_noloss_holds_loss_no_recovery():
    ind = {"dc_high_4h": 200.0}
    assert X.noloss_hold_loss_exit(_get({"NOLOSS_ENABLED": True}), True, -1.5, 100.0, 98.0, ind) == (True, "NOLOSS_HOLD")


def test_noloss_ignores_gains():
    ind = {"dc_high_4h": 200.0}
    assert X.noloss_hold_loss_exit(_get({"NOLOSS_ENABLED": True}), True, 0.0, 100.0, 100.0, ind) == (False, "")
    assert X.noloss_hold_loss_exit(_get({"NOLOSS_ENABLED": True}), False, 2.0, 100.0, 102.0, ind) == (False, "")


def test_noloss_bypass_wt():
    ind = {"wt1_5m": -10.0, "wt2_5m": 5.0, "wt1_15m": -8.0, "wt2_15m": 6.0, "wt1_1h": -5.0, "wt2_1h": 7.0, "wt1_4h": 1.0, "wt2_4h": 2.0, "wt1_D": 3.0, "wt2_D": 1.0, "dc_high_4h": 200.0}
    assert X.count_wt_against(ind, True) == 4
    d = {"NOLOSS_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 3}
    assert X.noloss_hold_loss_exit(_get(d), True, -1.5, 100.0, 98.0, ind) == (False, "NOLOSS_BYPASS_WT")
    d["NOLOSS_BYPASS_WT_5OF5_MIN_TFS"] = 5
    assert X.noloss_hold_loss_exit(_get(d), True, -1.5, 100.0, 98.0, ind) == (True, "NOLOSS_HOLD")


def test_noloss_dc_recovery_escape():
    ind = {"dc_high_4h": 95.0}
    d = {"NOLOSS_ENABLED": True, "DC_RECOVERY_EXIT_ENABLED": True, "DC_RECOVERY_EXIT_TOLERANCE_PCT": 0.10}
    assert X.noloss_hold_loss_exit(_get(d), True, -0.05, 100.0, 99.95, ind) == (False, "DC_RECOVERY")
    assert X.noloss_hold_loss_exit(_get(d), True, -1.5, 100.0, 98.0, ind) == (True, "NOLOSS_HOLD")
    d["DC_RECOVERY_EXIT_ENABLED"] = False
    assert X.noloss_hold_loss_exit(_get(d), True, -0.05, 100.0, 99.95, ind) == (True, "NOLOSS_HOLD")
    inds = {"dc_low_4h": 105.0}
    d2 = {"NOLOSS_ENABLED": True, "DC_RECOVERY_EXIT_ENABLED": True, "DC_RECOVERY_EXIT_TOLERANCE_PCT": 0.10}
    assert X.noloss_hold_loss_exit(_get(d2), False, -0.05, 100.0, 100.05, inds) == (False, "DC_RECOVERY")


def test_count_wt_against_skips_missing():
    assert X.count_wt_against({}, True) == 0
    assert X.count_wt_against({"wt1_15m": -5.0, "wt2_15m": 5.0}, True) == 1
    assert X.count_wt_against({"wt1_15m": -5.0, "wt2_15m": 5.0}, False) == 0
    assert X.count_wt_against({"wt1_15m": 0.0, "wt2_15m": 0.0}, True) == 0


def test_stop_loss():
    assert X.stop_loss_fires(_get({}), -5.0) == (False, "")
    assert X.stop_loss_fires(_get({"STOP_LOSS_ENABLED": True}), -2.0)[0] is True
    assert X.stop_loss_fires(_get({"STOP_LOSS_ENABLED": True}), -1.99)[0] is False
    assert X.stop_loss_fires(_get({"STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": 0.5}), -0.5)[0] is True
    assert X.stop_loss_fires(_get({"STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": 0.5}), 1.0)[0] is False
