#!/usr/bin/env python3
"""Tests for tools/npz_tail_service.py — each server owns its cat_side rows."""

import json
import time

import numpy as np

from tools.npz_tail_service import (
    owned_symbols,
    sweep_symbol,
)


def test_stock_tip_uses_final_regular_session_row_not_after_hours_cache(tmp_path):
    from closed_bar_feed import atomic_json
    from tools.npz_tail_service import kline_last_close
    from datetime import datetime, timezone
    final = int(datetime(2026, 10, 26, 19, 45, tzinfo=timezone.utc).timestamp())
    path = tmp_path / 'klines_cache/tradier/TEST_15m.json'
    atomic_json(path, [{'timestamp': '2026-10-26T19:45:00Z'}, {'timestamp': '2026-10-26T20:00:00Z'}])
    assert kline_last_close(tmp_path, 'TEST', 'tradier', now_s=final + 3600) == final


def test_changed_source_recalculates_latest_bar_once_and_keeps_history(tmp_path):
    from npz_tail_store import compact_tail, load_merged
    path = tmp_path / 'TESTUSDT.npz'
    stamp = 1792899000
    np.savez_compressed(path, timestamps=np.array([stamp - 900]), close=np.array([100.0]))
    calls = []
    def compute(symbol, close_ts):
        calls.append(close_ts)
        return {'close': 110.0 + len(calls)}
    assert sweep_symbol(path, 'TESTUSDT', stamp, compute, revision='first') == 'appended'
    compact_tail(path)
    assert sweep_symbol(path, 'TESTUSDT', stamp, compute, revision='first') == 'current'
    assert sweep_symbol(path, 'TESTUSDT', stamp, compute, revision='native-correction') == 'appended'
    assert sweep_symbol(path, 'TESTUSDT', stamp, compute, revision='native-correction') == 'current'
    assert calls == [stamp, stamp]
    result = load_merged(path)
    assert result['timestamps'].tolist() == [stamp - 900, stamp]
    assert result['close'].tolist() == [100.0, 112.0]


def test_universe_falls_back_to_stock_npz(tmp_path):
    import numpy as np

    from tools.npz_tail_service import load_universe, owned_symbols

    (tmp_path / "symbols_trb_long.json").write_text("[]")
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    ind = tmp_path / "backtest_v8" / "indicators"
    ind.mkdir(parents=True)
    np.savez_compressed(
        str(ind / "AAPL.npz"), timestamps=np.array([1]), close=np.ones(1)
    )
    np.savez_compressed(
        str(ind / "BTCUSDC.npz"), timestamps=np.array([1]), close=np.ones(1)
    )
    uni = load_universe(tmp_path)
    assert "AAPL_LONG" in uni and "AAPL_SHORT" in uni
    assert owned_symbols(uni, "stocks") == ["AAPL"]


def test_owned_symbols_split():
    uni = ["BTCUSDC_LONG", "BTCUSDC_SHORT", "AAPL_LONG", "AAPL_SHORT", "XMRUSDT_LONG"]
    assert owned_symbols(uni, "crypto_short") == ["BTCUSDC_SHORT"]
    assert owned_symbols(uni, "crypto_long") == ["BTCUSDC_LONG", "XMRUSDT_LONG"]
    assert owned_symbols(uni, "stock_long") == ["AAPL_LONG"]
    assert owned_symbols(uni, "stock_short") == ["AAPL_SHORT"]
    assert owned_symbols(uni, "crypto") == ["BTCUSDC", "XMRUSDT"]
    assert owned_symbols(uni, "stocks") == ["AAPL"]


def test_on_append_fires_once(tmp_path):
    from npz_tail_store import tail_len

    npz = tmp_path / "TSTCUSDT.npz"
    np.savez_compressed(
        str(npz),
        timestamps=np.array([1792899000 - 900], dtype=np.int64),
        close=np.ones(1),
    )
    fired = []
    r = sweep_symbol(
        str(npz),
        "TSTCUSDT",
        1792899000,
        lambda s, t: {"timestamps": t, "close": 3.0},
        now_s=1792899000 + 900.0,
        on_append=fired.append,
    )
    assert r == "appended" and len(fired) == 1 and fired[0].endswith(".jsonl")
    r2 = sweep_symbol(
        str(npz),
        "TSTCUSDT",
        1792899000,
        lambda s, t: {"timestamps": t},
        now_s=1792899000 + 900.0,
        on_append=fired.append,
    )
    assert r2 == "current" and len(fired) == 1


def test_sweep_appends_one_row_idempotent(tmp_path):
    from tools.npz_tail_service import tip_of
    from npz_tail_store import tail_len

    npz = tmp_path / "TSTUSDT.npz"
    n = 20
    base_ts = 1792899000 - n * 900
    np.savez_compressed(
        str(npz),
        timestamps=np.arange(base_ts, 1792899000, 900, dtype=np.int64),
        close=np.ones(n),
    )
    tip = tip_of(npz)
    assert tip == 1792899000 - 900

    calls = []

    def fake_compute(symbol, close_ts):
        calls.append((symbol, close_ts))
        return {"timestamps": close_ts, "close": 1.5}

    r1 = sweep_symbol(str(npz), "TSTUSDT", 1792899000, fake_compute)
    assert r1 == "appended" and tail_len(str(npz)) == 1
    r2 = sweep_symbol(str(npz), "TSTUSDT", 1792899000, fake_compute)
    assert r2 == "current" and tail_len(str(npz)) == 1
    assert len(calls) == 1


def test_sweep_appends_with_float_now(tmp_path):
    from npz_tail_store import tail_len

    npz = tmp_path / "TSTFUSDT.npz"
    np.savez_compressed(
        str(npz),
        timestamps=np.array([1792899000 - 900], dtype=np.int64),
        close=np.ones(1),
    )
    r = sweep_symbol(
        str(npz),
        "TSTFUSDT",
        1792899000,
        lambda s, t: {"timestamps": t, "close": 2.0},
        now_s=1792899000 + 900.0,
    )
    assert r == "appended" and tail_len(str(npz)) == 1


def test_appended_row_carries_parent_labels(tmp_path):
    from npz_tail_store import load_merged

    npz = tmp_path / "TSTPUSDT.npz"
    np.savez_compressed(
        str(npz),
        timestamps=np.array([1792899000 - 900], dtype=np.int64),
        timestamp_15m=np.array([1792899000 - 900], dtype=np.int64),
        close=np.ones(1),
    )
    r = sweep_symbol(
        str(npz),
        "TSTPUSDT",
        1792899000,
        lambda s, t: {"close": 9.0},
        now_s=1792899000 + 900.0,
    )
    assert r == "appended"
    m = load_merged(str(npz))
    assert int(m["timestamp_15m"][-1]) == 1792899000
    assert int(m["timestamps"][-1]) == 1792899000


def test_sweep_refuses_forming_bar(tmp_path):
    npz = tmp_path / "TST2USDT.npz"
    np.savez_compressed(
        str(npz), timestamps=np.array([1792899000], dtype=np.int64), close=np.ones(1)
    )
    now = 1792899000 + 900 + 10
    r = sweep_symbol(
        str(npz),
        "TST2USDT",
        1792899000 + 900,
        lambda s, t: {"timestamps": t},
        now_s=now,
    )
    assert r == "refused"


def test_kline_last_close_reads_tail_and_ignores_forming_bar(tmp_path, monkeypatch):
    import pandas as pd
    import npz_live_generator
    from tools.npz_tail_service import kline_last_close

    now = pd.Timestamp.now(tz="UTC").floor("15min")
    closed = now - pd.Timedelta(minutes=15)
    forming = now
    path = tmp_path / "TSTUSDT_15m.json"
    path.write_text(
        json.dumps(
            [
                {"timestamp": closed.isoformat(), "close": 1},
                {"timestamp": forming.isoformat(), "close": 2},
            ]
        )
    )
    monkeypatch.setattr(npz_live_generator, "_resolve_kline_path", lambda *args: path)
    assert kline_last_close(tmp_path, "TSTUSDT", "binance") == int(closed.timestamp())


def test_crypto_current_bar_is_used_the_moment_it_closes(tmp_path, monkeypatch):
    """USER 2026-10-27: the mark-price bar is published at the close and S1 calculates the NPZ bar as soon as it receives it;
    the OHLCV kline replaces it later as a correction. No 30 s wait for a verified exchange overlay."""
    import npz_live_generator
    from closed_bar_feed import atomic_json
    from tools.npz_tail_service import kline_last_close
    start = 1793053800 - 900
    path = tmp_path / 'TSTUSDT_15m.json'
    atomic_json(path, [{'timestamp': '2026-10-26T22:00:00Z'}, {'timestamp': '2026-10-26T22:15:00Z'}])
    monkeypatch.setattr(npz_live_generator, '_resolve_kline_path', lambda *args: path)
    assert kline_last_close(tmp_path, 'TSTUSDT', 'crypto', now_s=1793053799.9) == start - 900, "a bar is never used before its close"
    assert kline_last_close(tmp_path, 'TSTUSDT', 'crypto', now_s=1793053800) == start
    assert kline_last_close(tmp_path, 'TSTUSDT', 'crypto', now_s=1793053803) == start


def test_resolve_kline_prefers_live_over_backtest(tmp_path):
    from npz_live_generator import _resolve_kline_path

    live = tmp_path / "klines_cache" / "TSTUSDT_15m.json"
    backtest = tmp_path / "klines_cache_backtest" / "TSTUSDT_15m.json"
    live.parent.mkdir(parents=True)
    backtest.parent.mkdir(parents=True)
    canonical = tmp_path / "klines_canonical" / "TSTUSDT_15m.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("[]")
    live.write_text(json.dumps([{"timestamp": "2026-10-26T06:45:00.000000Z"}]))
    canonical.write_text(json.dumps([{"timestamp": "2026-10-26T07:00:00.000000Z"}]))
    backtest.write_text(json.dumps([{"timestamp": "2026-10-26T06:30:00.000000Z"}]))
    assert _resolve_kline_path("TSTUSDT", "15m", "binance", tmp_path) == canonical
