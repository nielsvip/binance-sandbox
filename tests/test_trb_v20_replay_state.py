from datetime import datetime, timedelta, timezone

import numpy as np

from tools import vec_live_executor
from vec_decisions.trb_v20_replay_state import rolling_anchor_for_completed_prefix


def test_replay_anchor_matches_canonical_anchor_on_exact_completed_prefix():
    first = datetime(2026, 8, 1, 13, 30, tzinfo=timezone.utc)
    completed = np.asarray(
        [(first + timedelta(days=i)).timestamp() for i in range(35)],
        dtype="float64",
    )
    raw = {"timestamps": np.concatenate((completed, [completed[-1] + 900.0]))}
    bar_ts = float(completed[-1])
    prefix = raw["timestamps"][: np.searchsorted(raw["timestamps"], bar_ts, side="right")]

    replay_anchor = rolling_anchor_for_completed_prefix("AAPL_LONG", raw, bar_ts)
    canonical_anchor = vec_live_executor.canonical_anchor(
        "AAPL_LONG", timestamps=prefix
    )

    assert replay_anchor == canonical_anchor
    assert replay_anchor["npz_end_ts"] == bar_ts
