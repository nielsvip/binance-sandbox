"""Patch/precompute convention agreement for dc_width_{tf}_prev (synthetic only)."""
import numpy as np


def roll_first(arr):
    pr = np.roll(np.asarray(arr, dtype=np.float32), 1).astype(np.float32)
    if len(pr):
        pr[0] = arr[0]
    return pr


def test_roll_first_convention():
    w = np.array([10.0, 11.0, 12.0, 13.0], dtype=np.float32)
    assert list(roll_first(w)) == [10.0, 10.0, 11.0, 12.0]


def test_roll_first_empty_safe():
    assert len(roll_first(np.array([], dtype=np.float32))) == 0


def test_precompute_emits_prev_key():
    src = open("backtest_v8_precompute.py").read()
    assert 'dc_width_{tf}_prev' in src
