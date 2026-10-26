"""Project candidate NumPy twin for the causal MTF BB-reject CLOSE.

Live semantics are in ez_manage.py's MTF compound close branch and the scalar
reference is vec_decisions.mtf_compound_exits.bb_reject_step. This module is a
candidate only; it is intentionally not imported by a manager or engine.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def bb_reject_close_mask(
    timestamps: Any,
    high: Any,
    low: Any,
    bb_upper: Any,
    bb_lower: Any,
    lookback_bars: int,
    is_long: bool,
    tf_secs: int,
) -> np.ndarray | None:
    """Return bar-wise BB tag-then-fail CLOSE fires, or None for invalid inputs.

    The source rule tags the relevant band, then closes on a later/current
    failure while any tag remains inside ``lookback_bars * tf_secs``. Missing,
    short, nonfinite or invalid inputs return None rather than fabricated data.
    """
    try:
        arrays = [np.asarray(x, dtype=float).reshape(-1) for x in
                  (timestamps, high, low, bb_upper, bb_lower)]
        n = arrays[0].size
        if n == 0 or any(a.size != n for a in arrays):
            return None
        ts, hi, lo, upper, lower = arrays
        if not all(np.all(np.isfinite(a)) for a in arrays) or np.any(np.diff(ts) < 0):
            return None
        window = int(lookback_bars) * int(tf_secs)
        if lookback_bars < 0 or tf_secs <= 0:
            return None
        if is_long:
            tag = (upper > 0) & (hi >= upper - 1e-9)
            fail = (upper > 0) & (hi < upper) & (hi > 0)
        else:
            tag = (lower > 0) & (lo <= lower + 1e-9) & (lo > 0)
            fail = (lower > 0) & (lo > lower) & (lo > 0)
        left = np.searchsorted(ts, ts - window, side="left")
        prefix_tags = np.concatenate(([0], np.cumsum(tag, dtype=np.int64)))
        recent_tag_count = prefix_tags[np.arange(1, n + 1)] - prefix_tags[left]
        return fail & (recent_tag_count > 0)
    except (TypeError, ValueError, OverflowError):
        return None
