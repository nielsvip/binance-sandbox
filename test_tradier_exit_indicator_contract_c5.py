from pathlib import Path

from tradier_exit_indicator_contract_c5 import (
    C5_CONTRACT_VERSION,
    completed_parent_value_pair,
    stdev_bb_rz_signal,
    stdev_reject_signal,
)


ROOT = Path(__file__).resolve().parent


def test_c5_stdev_reject_reads_raw_completed_bar_fields():
    raw = {
        "bb_pct_b_D_prev": 0.84,
        "bb_pct_b_D": 0.61,
        "wt_velocity_1h": -2.0,
    }
    assert stdev_reject_signal(raw, is_long=True).fire
    assert not stdev_reject_signal(
        raw, is_long=True, return_level=0.60
    ).fire


def test_c5_stdev_reject_is_side_specific():
    short_raw = {
        "bb_pct_b_D_prev": 0.15,
        "bb_pct_b_D": 0.40,
        "wt_velocity_1h": 3.0,
    }
    assert stdev_reject_signal(short_raw, is_long=False).fire
    assert not stdev_reject_signal(short_raw, is_long=True).fire


def test_c5_stdev_bb_rz_reads_raw_completed_bar_fields():
    raw = {
        "bb_pct_b_4h_prev": 1.04,
        "bb_pct_b_4h": 0.97,
        "wt_velocity_1h": -1.0,
    }
    signal = stdev_bb_rz_signal(raw, is_long=True, timeframe="4h")
    assert signal.fire
    assert signal.path == "STDEV_BB_RZ_EXIT"


def test_c5_derives_previous_distinct_completed_parent_when_npz_omits_prev():
    state = {}
    first = completed_parent_value_pair(
        {
            "bb_pct_b_D": 1.1,
            "_completed_source_ts_D": 100,
        },
        timeframe="D",
        state=state,
        state_key="MU:D",
    )
    repeated = completed_parent_value_pair(
        {
            "bb_pct_b_D": 1.1,
            "_completed_source_ts_D": 100,
        },
        timeframe="D",
        state=state,
        state_key="MU:D",
    )
    next_parent = completed_parent_value_pair(
        {
            "bb_pct_b_D": 0.9,
            "_completed_source_ts_D": 200,
        },
        timeframe="D",
        state=state,
        state_key="MU:D",
    )
    assert first == (1.1, 1.1)
    assert repeated == (1.1, 1.1)
    assert next_parent == (0.9, 1.1)


def test_c5_helper_is_not_active_in_c4_contract():
    campaign = (ROOT / "tools" / "persym_baseline_campaign.py").read_text()
    manage = (ROOT / "tradier_manage.py").read_text()
    assert C5_CONTRACT_VERSION not in campaign
    assert "tradier_exit_indicator_contract_c5" in manage
    assert 'startswith("tradier-matrix-exec-c5")' in manage
    assert '"tradier_exit_indicator_contract_c5.py"' not in campaign


def test_current_reader_fault_and_c5_patch_are_explicit():
    manage = (ROOT / "tradier_manage.py").read_text()
    assert 'V8_MATRIX_CONTRACT_VERSION", "").startswith("tradier-matrix-exec-c5")' in manage
    assert "_sre_pctb_now = float(_stdev_ind.get(" in manage
    assert "_brz_pctb_now = float(_stdev_ind.get(" in manage
    assert "_exit_ind" in manage
    assert "else i" in manage
