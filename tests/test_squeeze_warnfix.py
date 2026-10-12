"""Squeeze-release FutureWarning regression (USER 2026-10-12 log-noise fix)."""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest_v8_precompute as P


def _frame(n=60):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


def test_squeeze_release_no_future_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        out = P.compute_tf_arrays(_frame(), "15m")
    assert "squeeze_fire_15m" in out
    assert set(np.unique(out["squeeze_fire_15m"])) <= {0, 1, -1}


def test_squeeze_release_first_bar_zero():
    out = P.compute_tf_arrays(_frame(), "15m")
    assert out["squeeze_fire_15m"][0] == 0  # shift fill_value=False, never NaN


def test_shift_fill_value_equivalent_to_fillna():
    import hashlib

    s = pd.Series([True, True, False, False, True])
    new = s.shift(1, fill_value=False)
    old = s.shift(1).fillna(False)
    assert list(new) == list(bool(x) for x in old)
    assert new.dtype == bool  # old path degrades to object on pandas 3
    out = P.compute_tf_arrays(_frame(), "15m")
    h = hashlib.md5(b"".join(np.asarray(v).tobytes() for _, v in sorted(out.items()))).hexdigest()
    assert h == "1e0ae1b0f039bde97a8655c91c7419ad"  # fix changed zero bytes of math
