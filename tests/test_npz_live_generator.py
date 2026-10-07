"""Durable test for NPZ live generator — point-in-time correctness and live vs vectorized parity."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import numpy as _np
_np.NaN = _np.nan

import npz_live_generator as gen


def _make_klines(tmpdir: Path, sym: str, tf: str, n: int, end: datetime):
    freq = {"3m": "3min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "D": "1D", "W": "1W", "M": "1ME"}[tf]
    idx = pd.date_range(end=end, periods=n, freq=freq)
    price = 100 + np.cumsum(np.random.randn(n) * 0.5)
    import json
    data = [
        {"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "open": float(p), "high": float(p + 1), "low": float(p - 1), "close": float(p), "volume": 1000.0}
        for ts, p in zip(idx, price)
    ]
    (tmpdir / f"{sym}_{tf}.json").write_text(json.dumps(data))
    return idx


def test_alias_equality_in_row():
    tmpdir = Path(tempfile.mkdtemp())
    sym = "FAKEUSDT"
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Use end aligned to 15m
    end = end.replace(minute=(end.minute // 15) * 15)
    for tf, n in [("15m", 600), ("3m", 800), ("1h", 400), ("4h", 200), ("D", 120)]:
        _make_klines(tmpdir, sym, tf, n, end)

    from ez_indicators import IndicatorCalculator
    calc = IndicatorCalculator()
    close_ts = end

    orig = gen._resolve_kline_path
    def _fake(s, tf_, m, bp):
        p = tmpdir / f"{s}_{tf_}.json"
        return p if p.exists() else None
    gen._resolve_kline_path = _fake
    try:
        row = gen._compute_row_for_15m_close(sym, close_ts, None, calc, "crypto")
    finally:
        gen._resolve_kline_path = orig

    assert row is not None
    assert "close" in row and "timestamps" in row
    for tf in ["3m", "15m", "1h", "4h", "D"]:
        if f"k_{tf}" in row:
            # k and stoch_k must be equal where both present
            if f"stoch_k_{tf}" in row:
                assert row[f"k_{tf}"] == row[f"stoch_k_{tf}"]
            # prev/ant must be present and equal (where computed)
            if f"k_{tf}_prev" in row:
                assert f"k_{tf}_ant" in row
                assert row[f"k_{tf}_prev"] == row[f"k_{tf}_ant"]
                assert f"stoch_k_{tf}_prev" in row
                assert row[f"stoch_k_{tf}_prev"] == row[f"stoch_k_{tf}_ant"]


def test_should_update_and_append():
    tmpdir = Path(tempfile.mkdtemp())
    npz_path = tmpdir / "TEST.npz"
    now = datetime.now(timezone.utc)
    ts_old = int((now - timedelta(hours=2)).timestamp())
    ts_new = int(now.timestamp())
    np.savez_compressed(str(npz_path), timestamps=np.array([ts_old - 900, ts_old], dtype=np.int64), close=np.array([100.0, 101.0], dtype=np.float32))
    assert gen._should_update_npz(npz_path, now) is True
    old_close = datetime.fromtimestamp(ts_old - 900, tz=timezone.utc)
    assert gen._should_update_npz(npz_path, old_close) is False
    row = {"timestamps": ts_new, "close": 102.0, "close_15m": 102.0, "k_15m": 55.0, "stoch_k_15m": 55.0, "k_15m_prev": 50.0, "stoch_k_15m_prev": 50.0, "k_15m_ant": 50.0}
    ok = gen._append_row_to_npz(npz_path, row)
    assert ok is True
    data = gen._load_npz(npz_path)
    assert len(data["timestamps"]) == 3
    assert int(data["timestamps"][-1]) == ts_new
    assert data["k_15m"][-1] == data["stoch_k_15m"][-1] == 55.0


def test_live_vs_vectorized_parity():
    tmpdir = Path(tempfile.mkdtemp())
    sym = "PARITYUSDT"
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = end.replace(minute=(end.minute // 15) * 15)
    for tf, n in [("15m", 600), ("3m", 800), ("1h", 400), ("4h", 200), ("D", 120)]:
        _make_klines(tmpdir, sym, tf, n, end)

    from ez_indicators import IndicatorCalculator
    calc = IndicatorCalculator()
    close_ts = end

    orig = gen._resolve_kline_path
    def _fake(s, tf_, m, bp):
        p = tmpdir / f"{s}_{tf_}.json"
        return p if p.exists() else None
    gen._resolve_kline_path = _fake
    try:
        row = gen._compute_row_for_15m_close(sym, close_ts, None, calc, "crypto")
    finally:
        gen._resolve_kline_path = orig

    assert row is not None
    # Live compute for 15m at same close_ts must match vectorized row (point-in-time, no lookahead)
    df15 = pd.read_json(tmpdir / f"{sym}_15m.json")
    df15["timestamp_dt"] = pd.to_datetime(df15["timestamp"], utc=True)
    df15 = df15.set_index("timestamp_dt", drop=False).sort_index()
    live_res = calc.compute(df15, "15m", None, False, close_ts)
    # Allow small floating epsilon due to resampling vs direct
    assert abs(live_res["k_15m"] - row["k_15m"]) < 1e-4
    assert abs(live_res["stoch_k_15m"] - row["stoch_k_15m"]) < 1e-4
