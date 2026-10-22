"""Ledger collateral for the incremental step-value cache (USER 2026-10-22).

The pure-board lane re-paid full forming_values per stocks symbol per pass
(~1,600 s CPU) and every symbol timed out. The cache must therefore be:
exact (content-keyed, revisions cascade), invalidating on code change,
a no-op when absent, and assembly-identical cached vs fresh.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vec_decisions import stock_live_frames as slf  # noqa: E402


class FakeSF:
    """Minimal step-frame source: bundle(j) = rows [:j+1] of one frame."""

    def __init__(self, closes, tf="15m"):
        self.n = len(closes)
        self.tf = tf
        self.closes = list(closes)

    def bundle(self, j, tfs=...):
        tfs = (self.tf,) if tfs is Ellipsis else tuple(tfs)
        idx = pd.date_range("2026-01-01", periods=j + 1, freq="15min", tz="UTC")
        df = pd.DataFrame(
            {"timestamp": idx.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "close": self.closes[: j + 1],
             "open": self.closes[: j + 1], "high": self.closes[: j + 1],
             "low": self.closes[: j + 1], "volume": 100.0},
        )
        return {tf: df for tf in tfs}


CALLS = {"n": 0}


def fake_fn(df, tf):
    CALLS["n"] += 1
    n = len(df)
    return {"v": np.arange(n, dtype=np.float64) + 100.0, "w": np.full(n, 3.0)}


def test_cached_equals_fresh_and_skips_fn(tmp_path):
    sf = FakeSF([float(i) for i in range(1, 41)])
    ref = slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1)
    n_first = CALLS["n"]
    assert n_first == 40
    c = slf.StepValueCache(tmp_path / "a.pkl", code_version="v1")
    CALLS["n"] = 0
    got = slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c)
    assert CALLS["n"] == 40  # cold: all miss
    assert c.misses == 40 and c.hits == 0
    for k in ref["15m"]:
        assert np.array_equal(ref["15m"][k], got["15m"][k]), k
    c2 = slf.StepValueCache(tmp_path / "a.pkl", code_version="v1")
    CALLS["n"] = 0
    got2 = slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c2)
    assert CALLS["n"] == 0  # warm: zero fn calls
    assert c2.hits == 40 and c2.misses == 0
    for k in ref["15m"]:
        assert np.array_equal(ref["15m"][k], got2["15m"][k]), k


def test_mid_history_revision_cascades(tmp_path):
    sf = FakeSF([float(i) for i in range(1, 41)])
    c = slf.StepValueCache(tmp_path / "b.pkl", code_version="v1")
    slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c)
    sf2 = FakeSF([float(i) for i in range(1, 41)])
    sf2.closes[19] = 999.0  # revise step-19 close
    c2 = slf.StepValueCache(tmp_path / "b.pkl", code_version="v1")
    CALLS["n"] = 0
    slf.forming_values(sf2, fake_fn, tfs=("15m",), workers=1, cache=c2)
    assert CALLS["n"] == 21  # step 19 and every later step recompute, earlier hit
    assert c2.hits == 19


def test_version_change_drops_cache(tmp_path):
    sf = FakeSF([1.0, 2.0, 3.0])
    c = slf.StepValueCache(tmp_path / "c.pkl", code_version="v1")
    slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c)
    c2 = slf.StepValueCache(tmp_path / "c.pkl", code_version="v2")
    CALLS["n"] = 0
    slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c2)
    assert CALLS["n"] == 3 and c2.hits == 0


def test_absent_cache_is_noop(tmp_path):
    sf = FakeSF([1.0, 2.0, 3.0])
    c = slf.StepValueCache(tmp_path / "nope" / "d.pkl", code_version="v1")
    out = slf.forming_values(sf, fake_fn, tfs=("15m",), workers=1, cache=c)
    assert list(out["15m"]["v"][:3]) == [100.0, 101.0, 102.0]
    assert (tmp_path / "nope" / "d.pkl").exists()  # parent auto-created on save
