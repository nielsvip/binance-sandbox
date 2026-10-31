from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from vec_decisions.tradier_augment_sizing import (
    initial_adjustment_arrays,
    initial_adjustment_components,
)


def test_initial_adjustment_array_matches_scalar_at_time_and_threshold_boundaries():
    et = ZoneInfo("America/New_York")
    local = [
        datetime(2026, 10, 21, 10, 59, 59, tzinfo=et),
        datetime(2026, 10, 21, 11, 0, tzinfo=et),
        datetime(2026, 10, 21, 14, 0, tzinfo=et),
        datetime(2026, 10, 21, 14, 0, 1, tzinfo=et),
        datetime(2026, 10, 21, 15, 30, tzinfo=et),
        datetime(2026, 10, 21, 15, 30, 1, tzinfo=et),
    ]
    ts = np.asarray([d.astimezone(timezone.utc).timestamp() for d in local])
    ha = np.asarray(["red", "neutral", "green", "red", "neutral", "green"], dtype=object)
    k15 = np.asarray([50, 50, 50, 50, 50, 50], dtype=float)
    d15 = np.asarray([50, 51, 49, 51, 49, 51], dtype=float)
    k1h = np.asarray([81, 81, 19, 81, 19, 81], dtype=float)
    k5 = np.asarray([86, 86, 14, 86, 14, 86], dtype=float)

    for is_long in (True, False):
        knife, reduction = initial_adjustment_arrays(is_long, ts, ha, k15, d15, k1h, k5)
        for i, current in enumerate(local):
            scalar = initial_adjustment_components(
                is_long, ha[i], k15[i], d15[i], k1h[i], k5[i], current.time()
            )
            assert knife[i] == scalar[0]
            assert reduction[i] == scalar[1]


def test_initial_adjustment_arrays_rejects_misaligned_inputs():
    with np.testing.assert_raises(ValueError):
        initial_adjustment_arrays(True, [1, 2], ["neutral"], [50, 50], [50, 50], [50, 50], [50, 50])
