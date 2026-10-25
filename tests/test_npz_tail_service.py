#!/usr/bin/env python3
"""Tests for tools/npz_tail_service.py — each server owns its cat_side rows."""

import json
import time

import numpy as np

from tools.npz_tail_service import (
    owned_symbols,
    sweep_symbol,
)


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
        now_s=1792899000 + 120.0,
        on_append=fired.append,
    )
    assert r == "appended" and len(fired) == 1 and fired[0].endswith(".jsonl")
    r2 = sweep_symbol(
        str(npz),
        "TSTCUSDT",
        1792899000,
        lambda s, t: {"timestamps": t},
        now_s=1792899000 + 120.0,
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
        now_s=1792899000 + 120.0,
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
        now_s=1792899000 + 120.0,
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
