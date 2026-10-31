"""Frozen 1000PEPE per-bar recurrence parity against scalar DeltaTracker.update."""

from pathlib import Path

import numpy as np
import pytest

from vec_decisions.parabolic_delta import (
    DC_DELTA_FIELDS,
    TF_FIELDS,
    WT_DELTA_FIELDS,
    snapshot_from_npz,
    step,
)
from wt_dc_delta import DEFAULT_CFG, DeltaTracker


NPZ_PATH = Path("/tmp/1000PEPEUSDC_30d_reentry_audit.npz")


@pytest.mark.skipif(not NPZ_PATH.is_file(), reason="frozen 1000PEPE NPZ unavailable")
@pytest.mark.parametrize("is_long", [True, False])
def test_delta_recurrence_matches_scalar_for_every_frozen_bar(is_long):
    del is_long  # DeltaTracker's raw acceleration is side-independent.
    with np.load(NPZ_PATH, allow_pickle=True) as archive:
        keys = {"timestamps"}
        for tf in ("15m", "1h", "4h", "D"):
            keys.update(f"{field}_{tf}" for field in TF_FIELDS)
            keys.update(f"{field}_{tf}_prev" for field in DC_DELTA_FIELDS)
            keys.update(f"{field}_prev_{tf}" for field in WT_DELTA_FIELDS)
        npz = {key: archive[key] for key in keys if key in archive.files}
    timestamps = np.asarray(npz["timestamps"], dtype=np.float64)
    tracker = DeltaTracker(cfg={"tf_weights": dict(DEFAULT_CFG["tf_weights"])})
    state = {}
    for index, timestamp in enumerate(timestamps):
        indicators = snapshot_from_npz(npz, index, ltf_enabled=False)
        indicators["_tick_ts"] = float(timestamp)
        scalar = tracker.update("1000PEPEUSDC", indicators, position_state=None)
        vector = step(
            state,
            indicators,
            float(timestamp),
            dict(DEFAULT_CFG["tf_weights"]),
            ltf_enabled=False,
        )
        assert vector["bull_accel"] is bool(scalar.bull_accel), index
        assert vector["bear_accel"] is bool(scalar.bear_accel), index
        assert vector["total_bull"] == pytest.approx(scalar.bull_speed, nan_ok=True)
        assert vector["total_bear"] == pytest.approx(scalar.bear_speed, nan_ok=True)
