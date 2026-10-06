"""test_npz_bar_refresh_v2 — focused cover for the v2 rewrite (both venues, sentiment wave, loop math).

Pure/synthetic only: no S1, no Tradier/Binance calls, no builder runs. Run: pytest test_npz_bar_refresh_v2.py -q
"""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))


def _mod():
    spec = importlib.util.spec_from_file_location("npz_bar_refresh", str(ROOT / "tools" / "npz_bar_refresh.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


nbr = _mod()


def test_venue_split():
    assert nbr.is_crypto_symbol("BTCUSDC") and nbr.is_crypto_symbol("ETHUSDT")
    assert not nbr.is_crypto_symbol("AAPL") and not nbr.is_crypto_symbol("MU")


def test_last_closed_bar_math():
    # 04:17:30Z -> last closed 15m bar opened 04:00Z
    now = int(dt.datetime(2026, 10, 6, 4, 17, 30, tzinfo=dt.timezone.utc).timestamp())
    assert nbr.last_closed_bar(now) == int(dt.datetime(2026, 10, 6, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    # stocks: 30s grace -> a bar that closed <30s ago is not trusted yet
    assert nbr.last_closed_stock_bar(now) == nbr.last_closed_bar(now) - (900 if (now - 30) // 900 < now // 900 else 0)


def test_stocks_gate_boundaries():
    wed_session = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc)  # 10:00 ET Wed
    wed_night = dt.datetime(2026, 10, 8, 2, 0, tzinfo=dt.timezone.utc)  # 22:00 ET Wed
    sat = dt.datetime(2026, 10, 10, 14, 0, tzinfo=dt.timezone.utc)
    assert nbr.stocks_gate(wed_session) == (True, "session")
    assert nbr.stocks_gate(wed_night) == (False, "night")
    assert nbr.stocks_gate(sat) == (False, "weekend")


def test_tradier_rows_grid_and_cutoff():
    import pandas as pd
    # Monday 2026-10-05 ET session bars as epoch UTC (true), incl. off-grid + future rows
    def e(h, m):
        return int(pd.Timestamp(f"2026-10-05 {h:02d}:{m:02d}", tz="America/New_York").timestamp())
    rows = [
        {"timestamp": e(2, 0), "open": 1, "high": 1, "low": 1, "close": 100.0, "volume": 5},  # off-grid (pre 04:00) -> dropped
        {"timestamp": e(9, 30), "open": 1, "high": 1, "low": 1, "close": 101.0, "volume": 10},
        {"timestamp": e(19, 45), "open": 1, "high": 1, "low": 1, "close": 102.0, "volume": 20},  # ext close -> kept
        {"timestamp": e(20, 0), "open": 1, "high": 1, "low": 1, "close": 103.0, "volume": 30},  # off-grid -> dropped
        {"timestamp": "bad", "open": 1, "high": 1, "low": 1, "close": 104.0, "volume": 1},  # bad ts -> dropped
        {"timestamp": e(9, 45), "open": 1, "high": 1, "low": 1, "close": 0.0, "volume": 1},  # bad close -> dropped
    ]
    df = nbr.tradier_rows_to_df(rows, e(19, 45))
    assert df is not None and len(df) == 2
    assert df["close"].tolist() == [101.0, 102.0]
    assert str(df.index.tz) == "UTC" and df.index.name == "timestamp_dt"
    assert df["timestamp"].iloc[0].startswith("2026-10-05T13:30")
    assert nbr.tradier_rows_to_df(rows, e(9, 30)) is not None and len(nbr.tradier_rows_to_df(rows, e(9, 30))) == 1
    assert nbr.tradier_rows_to_df([], e(9, 30)) is None


def _synth_npz(path, ts, bias, mss=None):
    n = len(ts)
    d = {"timestamps": np.asarray(ts, dtype=np.int64), "wt_composite_bias": np.asarray(bias, dtype=np.int8),
         "close_15m": np.full(n, 100.0, dtype=np.float32)}
    if mss is not None:
        d["market_sentiment_score"] = np.asarray(mss, dtype=np.float32)
    np.savez_compressed(str(path), **d)


def test_breadth_scores_formula(tmp_path):
    base = 1791259200
    ts = [base - 900, base]
    _synth_npz(tmp_path / "AAA.npz", ts, [1, 1], [50.0, 50.0])
    _synth_npz(tmp_path / "BBB.npz", ts, [-1, 1], [50.0, 50.0])
    _synth_npz(tmp_path / "CCC.npz", ts, [0, -1], [50.0, 50.0])
    scores, n = nbr.breadth_scores(tmp_path, ["AAA", "BBB", "CCC", "MISSING"], {base})
    assert n == 3
    assert scores[base][1] == 3
    assert abs(scores[base][0] - (50.0 + (2 - 1) / 3 * 50.0)) < 1e-9  # 2 bull 1 bear


def test_patch_sentiment_file_updates_only_new_ts(tmp_path):
    base = 1791259200
    ts = [base - 1800, base - 900, base]
    _synth_npz(tmp_path / "AAA.npz", ts, [1, 1, -1], [60.0, 61.0, 50.0])
    r = nbr.patch_sentiment_file(tmp_path / "AAA.npz", [base], {base: (25.0, 4)})
    assert r["status"] == "PATCHED" and r["n_ts"] == 1
    z = np.load(tmp_path / "AAA.npz", allow_pickle=True)
    got = np.asarray(z["market_sentiment_score"], dtype=float).tolist()
    assert got == [60.0, 61.0, 25.0]  # old rows untouched
    assert sorted(z.files) == ["close_15m", "market_sentiment_score", "timestamps", "wt_composite_bias"]


def test_patch_sentiment_file_adds_missing_key(tmp_path):
    base = 1791259200
    _synth_npz(tmp_path / "BBB.npz", [base - 900, base], [1, 1])
    r = nbr.patch_sentiment_file(tmp_path / "BBB.npz", [base], {base: (80.0, 2)})
    assert r["status"] == "PATCHED"
    z = np.load(tmp_path / "BBB.npz", allow_pickle=True)
    assert np.asarray(z["market_sentiment_score"], dtype=float).tolist() == [50.0, 80.0]


def test_splice_appends_only_new_rows_and_reports_ts():
    n, k = 40, 2
    ts_old = np.arange(1000, 1000 + n * 900, 900, dtype=np.int64)
    ts_new = np.arange(1000 + n * 900, 1000 + (n + k) * 900, 900, dtype=np.int64)
    mk = lambda v, m: np.full(m, v, dtype=np.float32)
    L = {"timestamps": ts_old, "close_15m": mk(100.0, n), "open_15m": mk(99.0, n), "high_15m": mk(101.0, n),
         "low_15m": mk(98.0, n), "volume_15m": mk(10.0, n), "wt_composite_bias": np.ones(n, dtype=np.int8)}
    tail = {"timestamps": np.concatenate([ts_old[-8:], ts_new]), "close_15m": mk(100.0, 8 + k), "open_15m": mk(99.0, 8 + k),
            "high_15m": mk(101.0, 8 + k), "low_15m": mk(98.0, 8 + k), "volume_15m": mk(10.0, 8 + k),
            "wt_composite_bias": np.ones(8 + k, dtype=np.int8)}
    D, info = nbr.splice(L, tail, "TEST", overlap=8, mode="crypto")
    assert info["appended"] == k and info["new_ts"] == ts_new.tolist()
    assert len(D["timestamps"]) == n + k
    assert np.array_equal(np.asarray(D["close_15m"])[:n], np.asarray(L["close_15m"]))  # history frozen


def test_next_cycle_wait_aligns():
    now = float(dt.datetime(2026, 10, 6, 4, 10, 0, tzinfo=dt.timezone.utc).timestamp())
    assert nbr.next_cycle_wait(now) == 75.0 + 5 * 60  # next boundary 04:15 + 75s


def _et(y, mo, d, h, mi):
    import pandas as pd
    return int(pd.Timestamp(f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}", tz="America/New_York").timestamp())


def test_last_session_final_bar():
    tue_night = dt.datetime(2026, 10, 6, 4, 35, tzinfo=dt.timezone.utc)  # Tue 00:35 ET
    assert nbr.last_session_final_bar(tue_night) == _et(2026, 10, 5, 19, 45)  # Monday ext close
    mon_open = dt.datetime(2026, 10, 5, 14, 0, tzinfo=dt.timezone.utc)  # Mon 10:00 ET
    assert nbr.last_session_final_bar(mon_open) == _et(2026, 10, 2, 19, 45)  # Friday (Monday not closed)
    sat = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
    assert nbr.last_session_final_bar(sat) == _et(2026, 10, 9, 19, 45)  # Friday
    mon_eve = dt.datetime(2026, 10, 6, 0, 30, tzinfo=dt.timezone.utc)  # Mon 20:30 ET (ext closed)
    assert nbr.last_session_final_bar(mon_eve) == _et(2026, 10, 5, 19, 45)


def test_stock_prefetch_action_routing():
    assert nbr.stock_prefetch_action("A", 100, None, 90)[0] == "job"
    act, r = nbr.stock_prefetch_action("V", None, "no prefetch", None)
    assert act == "result" and r["status"] == "NO_NPZ"
    act, r = nbr.stock_prefetch_action("A", None, None, 90)
    assert act == "result" and r["status"] == "FRESH" and r["ts_before"] == 90
    act, r = nbr.stock_prefetch_action("A", None, "boom", 90)
    assert act == "fail" and r["status"] == "PREFETCH_FAILED"


def test_backfill_volume_D_50_sma_verified():
    import pandas as pd
    d1, d2, d3 = _et(2026, 10, 1, 16, 0), _et(2026, 10, 2, 16, 0), _et(2026, 10, 5, 16, 0)
    td = np.array([d1] * 4 + [d2] * 4 + [d3] * 4)
    vol = np.array([100.0] * 4 + [200.0] * 4 + [300.0] * 4, dtype=np.float32)
    sma_native = pd.Series([100.0, 200.0, 300.0]).rolling(50, min_periods=1).mean().values
    full = {"volume_D": vol, "timestamp_D": td}
    L = {"volume_D_50_sma": np.repeat(sma_native, 4)[:10].astype(np.float32)}
    out = nbr.backfill_legacy_keys(full, L, ["volume_D_50_sma"], "TEST")
    assert "volume_D_50_sma" in out and len(out["volume_D_50_sma"]) == 12
    assert np.allclose(out["volume_D_50_sma"][:10].astype(float), L["volume_D_50_sma"].astype(float), rtol=1e-6)


def test_backfill_volume_D_50_sma_refuses_mismatch():
    d1, d2 = 1000, 2000
    full = {"volume_D": np.full(12, 100.0, dtype=np.float32), "timestamp_D": np.array([d1] * 6 + [d2] * 6)}
    L = {"volume_D_50_sma": np.full(10, 999.0, dtype=np.float32)}  # wrong values
    assert nbr.backfill_legacy_keys(full, L, ["volume_D_50_sma"], "TEST") == {}
