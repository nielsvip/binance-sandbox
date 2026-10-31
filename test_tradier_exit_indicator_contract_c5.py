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


def test_stdev_reject_parent_pair_is_scoped_to_existing_switch():
    manage = (ROOT / "tradier_manage.py").read_text()
    start = manage.index("# STDEV_REJECT_EXIT: exit when price approached band")
    end = manage.index("# Scorer says HOLD", start)
    branch = manage[start:end]
    assert 'if _cfg_auto("STDEV_REJECT_EXIT_ENABLED", False):' in branch
    assert "completed_parent_value_pair as _c5_parent_pair" in branch
    assert "_c5_parent_pair(" in branch
    assert 'V8_MATRIX_CONTRACT_VERSION", "").startswith("tradier-matrix-exec-c5")' not in branch
    assert 'if os.environ.get("V8_MATRIX_CONTRACT_VERSION", "").startswith(' in manage
    assert "_exit_ind" in manage


def test_parent_pair_restart_and_skipped_observation_are_explicit():
    state = {}
    first_after_start = completed_parent_value_pair(
        {"bb_pct_b_D": 0.72, "timestamp_D": 300},
        timeframe="D",
        state=state,
        state_key="AAPL:LONG:STDEV_REJECT:D",
    )
    after_skipped_parent = completed_parent_value_pair(
        {"bb_pct_b_D": 0.41, "timestamp_D": 500},
        timeframe="D",
        state=state,
        state_key="AAPL:LONG:STDEV_REJECT:D",
    )
    state = {}
    after_restart = completed_parent_value_pair(
        {"bb_pct_b_D": 0.41, "timestamp_D": 500},
        timeframe="D",
        state=state,
        state_key="AAPL:LONG:STDEV_REJECT:D",
    )
    assert first_after_start == (0.72, 0.72)
    assert after_skipped_parent == (0.41, 0.72)
    assert after_restart == (0.41, 0.41)


def test_completed_snapshot_values_override_state_and_raw_formed_values():
    indicators = {
        "bb_pct_b_D": 0.21,
        "bb_pct_b_D_prev": 0.95,
        "_completed_bb_pct_b_D": 0.62,
        "_completed_bb_pct_b_D_prev": 0.83,
        "_completed_source_ts_D": 200,
        "wt_velocity_1h": -1.0,
    }
    pair = completed_parent_value_pair(
        indicators,
        timeframe="D",
        state={},
        state_key="AAPL:LONG:STDEV_REJECT:D",
    )
    assert pair == (0.62, 0.83)
