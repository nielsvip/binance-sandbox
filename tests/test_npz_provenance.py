"""Unit tests for tools/opt/npz_provenance.py: pass on generating window, fail after drift."""
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.opt.npz_provenance import record_window_provenance, verify_window_provenance


def _write_npz(path, n=100, drift_idx=None, drift_val=999.0, drop_last=0):
    ts = (1_791_700_000 + np.arange(n) * 900).astype(np.int64)
    close = (100.0 + np.arange(n) * 0.1).astype(np.float64)
    if drift_idx is not None:
        close[drift_idx] = drift_val
    if drop_last:
        ts = ts[:-drop_last]
        close = close[:-drop_last]
    n2 = len(ts)
    o = close - 1.0
    h = close + 2.0
    low = close - 2.0
    v = (1000.0 + np.arange(n2)).astype(np.float64)
    np.savez(path, timestamps=ts, open_15m=o, high_15m=h, low_15m=low, close_15m=close, volume_15m=v, extra_key=np.ones(n2))


def _window_dict(path, lo, hi):
    z = np.load(path, allow_pickle=True)
    sl = slice(lo, hi)
    return np.asarray(z["timestamps"])[sl], {k: np.asarray(z[k])[sl] for k in ("open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")}


def test_verify_passes_on_generating_window(tmp_path):
    p = str(tmp_path / "w.npz")
    _write_npz(p)
    ts, win = _window_dict(p, 10, 60)
    rec = record_window_provenance(p, ts, win)
    assert set(rec) == {"npz_mtime_ns", "npz_size", "win_ts_first", "win_ts_last", "win_slice_sha"}
    ok, reason = verify_window_provenance(p, rec)
    assert ok, reason


def test_verify_fails_after_value_drift(tmp_path):
    p = str(tmp_path / "w.npz")
    _write_npz(p)
    ts, win = _window_dict(p, 10, 60)
    rec = record_window_provenance(p, ts, win)
    _write_npz(p, drift_idx=30, drift_val=12345.0)
    ok, reason = verify_window_provenance(p, rec)
    assert not ok, reason
    assert "drift" in reason or "mismatch" in reason


def test_verify_fails_after_truncation(tmp_path):
    p = str(tmp_path / "w.npz")
    _write_npz(p)
    ts, win = _window_dict(p, 80, 100)
    rec = record_window_provenance(p, ts, win)
    _write_npz(p, drop_last=5)
    ok, reason = verify_window_provenance(p, rec)
    assert not ok, reason


def test_verify_passes_after_append_only_growth(tmp_path):
    p = str(tmp_path / "w.npz")
    _write_npz(p, n=100)
    ts, win = _window_dict(p, 10, 60)
    rec = record_window_provenance(p, ts, win)
    _write_npz(p, n=120)
    ok, reason = verify_window_provenance(p, rec)
    assert ok, reason


def test_ndarray_window_and_missing_file(tmp_path):
    p = str(tmp_path / "w.npz")
    _write_npz(p)
    z = np.load(p, allow_pickle=True)
    ts = np.asarray(z["timestamps"])[10:60]
    arr = np.column_stack([np.asarray(z[k])[10:60] for k in ("open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")])
    rec = record_window_provenance(p, ts, arr)
    ok, _ = verify_window_provenance(p, rec)
    assert ok
    ok2, reason2 = verify_window_provenance(str(tmp_path / "nope.npz"), rec)
    assert not ok2 and "missing" in reason2
