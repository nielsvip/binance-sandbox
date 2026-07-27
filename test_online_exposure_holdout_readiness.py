import hashlib

import numpy as np

from tools.online_exposure_holdout_readiness import (
    holdout_readiness,
    prefix_sha256,
)


def _write_npz(path, hours):
    ts = np.arange(hours, dtype=np.int64) * 3600
    values = np.arange(hours, dtype=np.float64)
    np.savez(
        path,
        timestamps=ts,
        timestamp_1h=ts,
        open_5m=values,
        high_5m=values + 1,
        low_5m=values - 1,
        close_5m=values + 0.5,
    )


def _receipt(prefix, *, not_before):
    return {
        "symbol": "PBF",
        "freeze_end_utc": "1970-01-01T09:00:00+00:00",
        "holdout_not_before_utc": not_before,
        "controller_window_completed_1h_slots": 4,
        "required_complete_windows": 2,
        "frozen_prefix_sha256": prefix,
    }


def test_not_ready_fails_closed_without_launch_or_tuning(tmp_path):
    path = tmp_path / "PBF.npz"
    _write_npz(path, 14)
    prefix, _ = prefix_sha256(path, 9 * 3600)
    result = holdout_readiness(
        path, _receipt(prefix, not_before="1970-01-01T13:00:00+00:00")
    )
    assert result["status"] == "BLOCKED"
    assert result["launch_performed"] is False
    assert result["tuning_performed"] is False
    assert result["remaining_completed_1h_slots"] > 0


def test_ready_only_after_slots_calendar_and_prefix_pass(tmp_path):
    path = tmp_path / "PBF.npz"
    _write_npz(path, 20)
    prefix, _ = prefix_sha256(path, 9 * 3600)
    result = holdout_readiness(
        path, _receipt(prefix, not_before="1970-01-01T13:00:00+00:00")
    )
    assert result["status"] == "READY_FOR_FROZEN_EVALUATION"
    assert result["complete_controller_windows"] >= 2


def test_historical_prefix_drift_blocks_even_with_enough_new_data(tmp_path):
    path = tmp_path / "PBF.npz"
    _write_npz(path, 20)
    prefix, _ = prefix_sha256(path, 9 * 3600)
    with np.load(path) as data:
        arrays = {key: data[key].copy() for key in data.files}
    arrays["close_5m"][4] += 99
    np.savez(path, **arrays)
    result = holdout_readiness(
        path, _receipt(prefix, not_before="1970-01-01T13:00:00+00:00")
    )
    assert result["status"] == "BLOCKED"
    assert "FROZEN_PREFIX_DRIFT" in result["blocked_reasons"]
