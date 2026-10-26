"""Tail-store + snapshot tests: ms appends, zip untouched, forming bars refused."""

import hashlib
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
import npz_tail_store as tail
import live_state_snapshot as snap


def _closed_grid(n, end=None):
    if end is None:
        end = tail.last_closed_15m_epoch() - 900
    end = (int(end) // 900) * 900
    return [end - 900 * (n - 1 - i) for i in range(n)]


def _make_base_npz(tmpdir, name="TESTUSDT", n=2000, nkeys=30, end_lag_bars=1):
    end = tail.last_closed_15m_epoch() - 900 * end_lag_bars
    ts = np.array(_closed_grid(n, end=end), dtype=np.int64)
    data = {"timestamps": ts, "close": np.linspace(100.0, 110.0, n).astype(np.float32)}
    for k in range(nkeys):
        data[f"kf_{k:02d}_15m"] = np.full(n, float(k), dtype=np.float32)
    data["wt_cross_15m"] = np.zeros(n, dtype=np.int8)
    path = Path(tmpdir) / f"{name}.npz"
    np.savez_compressed(str(path), **data)
    return path


def _row(ts, close=111.0):
    return {
        "timestamps": int(ts),
        "close": float(close),
        "close_15m": float(close),
        "kf_00_15m": 7.5,
        "wt_cross_15m": "BULL",
    }


def _p95(samples):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def test_latest_closed_correction_replaces_values_without_duplicate_timestamp(tmp_path):
    path = _make_base_npz(tmp_path, n=20)
    original = tail.load_merged(path)
    stamp = int(original['timestamps'][-1]) + 900
    assert tail.append_tail_row(path, _row(stamp, 120))
    assert not tail.append_tail_row(path, _row(stamp, 999))
    assert tail.append_tail_row(path, _row(stamp, 121), replace_latest=True)
    merged = tail.load_merged(path)
    np.testing.assert_array_equal(merged['timestamps'][:-1], original['timestamps'])
    np.testing.assert_array_equal(merged['close'][:-1], original['close'])
    assert len(merged['timestamps']) == 21 and merged['close'][-1] == 121
    assert tail.compact_tail(path) == 2
    assert tail.load_merged(path)['close'][-1] == 121
    assert tail.append_tail_row(path, _row(stamp, 122), replace_latest=True)
    assert tail.load_merged(path)['close'][-1] == 122
    assert not tail.append_tail_row(path, _row(stamp - 900, 999), replace_latest=True)


def test_large_tail_row_keeps_tip_and_correction(tmp_path):
    path = _make_base_npz(tmp_path, n=20)
    stamp = int(np.load(path)["timestamps"][-1]) + 900
    first = dict(_row(stamp, 120), padding="x" * 30000)
    assert tail.append_tail_row(path, first)
    assert tail._tail_tip_ts(tail.tail_path_for_npz(path)) == stamp
    assert not tail.append_tail_row(path, dict(_row(stamp, 999), padding="x" * 30000))
    assert tail.append_tail_row(path, dict(_row(stamp, 121), padding="x" * 30000), replace_latest=True)
    assert tail.load_merged(path)["close"][-1] == 121


def test_append_one_bar_p95_under_50ms(tmp_path):
    npz_path = _make_base_npz(tmp_path, end_lag_bars=65)
    tip = int(np.load(str(npz_path))["timestamps"][-1])
    last_closed = tail.last_closed_15m_epoch()
    stamps = [tip + 900 * (i + 1) for i in range(60)]
    assert stamps[-1] <= last_closed
    timings = []
    for i, ts in enumerate(stamps):
        row = _row(ts, close=111.0 + i * 0.01)
        start = time.perf_counter()
        assert tail.append_tail_row(npz_path, row) is True
        timings.append(time.perf_counter() - start)
    p95 = _p95(timings)
    print(
        f"\nappend-one-bar n={len(timings)} p95={p95 * 1000:.2f}ms max={max(timings) * 1000:.2f}ms"
    )
    assert p95 < 0.050
    assert tail.tail_len(npz_path) == len(stamps)


def test_no_zip_rewrite_per_append(tmp_path):
    npz_path = _make_base_npz(tmp_path, end_lag_bars=25)
    before_stat = npz_path.stat().st_mtime_ns
    before_hash = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    tip = int(np.load(str(npz_path))["timestamps"][-1])
    last_closed = tail.last_closed_15m_epoch()
    appended = 0
    for i in range(20):
        ts = tip + 900 * (i + 1)
        assert ts <= last_closed
        assert tail.append_tail_row(npz_path, _row(ts)) is True
        appended += 1
    assert appended == 20
    assert npz_path.stat().st_mtime_ns == before_stat
    assert hashlib.sha256(npz_path.read_bytes()).hexdigest() == before_hash
    merged = tail.load_merged(npz_path)
    assert len(merged["timestamps"]) == 2000 + appended
    assert int(merged["timestamps"][-1]) == tip + 900 * appended


def test_snapshot_write_read_roundtrip_under_20ms(tmp_path):
    state = {
        "side": "LONG",
        "open_qty": 1.5,
        "entry_price": 100.25,
        "closed_pnl": -3.75,
        "open": True,
    }
    bar_ts = tail.last_closed_15m_epoch()
    start = time.perf_counter()
    dest = snap.write_snapshot(tmp_path, "TESTUSDT_LONG", state, bar_ts=bar_ts)
    payload = snap.read_snapshot(tmp_path, "TESTUSDT_LONG")
    elapsed = time.perf_counter() - start
    print(f"\nsnapshot roundtrip={elapsed * 1000:.2f}ms -> {dest}")
    assert payload is not None
    assert payload["state"] == state
    assert payload["bar_ts"] == bar_ts
    assert payload["sym_side"] == "TESTUSDT_LONG"
    assert elapsed < 0.020


def test_forming_bar_never_persisted(tmp_path):
    npz_path = _make_base_npz(tmp_path, n=100)
    before_hash = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    future_ts = tail.last_closed_15m_epoch() + 900
    assert tail.is_closed_bar(future_ts) is False
    assert tail.append_tail_row(npz_path, _row(future_ts)) is False
    assert (
        tail.append_tail_row(
            npz_path, _row(int(datetime.now(timezone.utc).timestamp()) + 3600)
        )
        is False
    )
    assert tail.tail_len(npz_path) == 0
    assert not tail.tail_path_for_npz(npz_path).exists()
    assert hashlib.sha256(npz_path.read_bytes()).hexdigest() == before_hash
    merged = tail.load_merged(npz_path)
    assert len(merged["timestamps"]) == 100


def test_merge_carries_forward_and_encodes_labels(tmp_path):
    npz_path = _make_base_npz(tmp_path, n=100, nkeys=5, end_lag_bars=5)
    tip = int(np.load(str(npz_path))["timestamps"][-1])
    last_closed = tail.last_closed_15m_epoch()
    assert tip + 2700 <= last_closed
    rows = [
        {"timestamps": tip + 900, "close": 120.0},
        {"timestamps": tip + 1800, "close": 121.0, "wt_cross_15m": "BEAR"},
        {"timestamps": tip + 2700, "close": 122.0},
    ]
    for row in rows:
        assert tail.append_tail_row(npz_path, row) is True
    merged = tail.load_merged(npz_path)
    assert len(merged["timestamps"]) == 103
    assert list(int(v) for v in merged["timestamps"][-3:]) == [
        tip + 900,
        tip + 1800,
        tip + 2700,
    ]
    assert float(merged["close"][-1]) == 122.0
    assert float(merged["kf_01_15m"][-1]) == 1.0
    assert int(merged["wt_cross_15m"][-2]) == -1
    assert int(merged["wt_cross_15m"][-1]) == -1
    assert (
        tail.append_tail_row(npz_path, {"timestamps": tip + 900, "close": 999.0})
        is False
    )
    assert tail.tail_len(npz_path) == 3


def test_append_bar_with_snapshot_same_instant(tmp_path):
    npz_path = _make_base_npz(tmp_path, n=100)
    tip = int(np.load(str(npz_path))["timestamps"][-1])
    ts = min(tip + 900, tail.last_closed_15m_epoch())
    assert ts > tip
    state = {"side": "SHORT", "open_qty": 2.0, "open": True, "closed_pnl": 0.0}
    start = time.perf_counter()
    tail_ok, snap_path = snap.append_bar_with_snapshot(
        npz_path, _row(ts), tmp_path, "TESTUSDT_SHORT", state
    )
    elapsed = time.perf_counter() - start
    print(f"\nbar+snapshot={elapsed * 1000:.2f}ms")
    assert tail_ok is True
    assert snap_path is not None and snap_path.exists()
    payload = snap.read_snapshot(tmp_path, "TESTUSDT_SHORT")
    assert payload["bar_ts"] == ts and payload["state"] == state
    assert elapsed < 0.050
