#!/usr/bin/env python3
"""Crypto plot overlays — heatmap off, live-fill arrows + mismatch lines render.

Smoke-pins ez_rankings.plot_dfs_subplots on synthetic bars for a symbol with
real live fills: the PNG must render without exception and carry the new style.
"""
import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ez_rankings as ezr


def _df(n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end="2026-10-24T00:00:00Z", periods=n, freq="15min", tz="UTC")
    close = 5.5 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    return pd.DataFrame({"close_time": idx, "open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close, "volume": 1000})


def test_crypto_overlay_flags():
    assert ezr.SHOW_TOP_BOTTOM_ARROWS is False
    assert ezr.SHOW_STOCH_HEATMAP is False
    assert ezr.SHOW_CRYPTO_LIVE_ARROWS is True
    assert ezr.SHOW_CRYPTO_MISMATCH is True
    assert ezr.SHOW_CRYPTO_RSI_STRIP is True
    assert ezr.SHOW_CRYPTO_STOCH_STRIP is True
    assert ezr.SHOW_CRYPTO_TOP_LEGEND is False
    assert ezr.CRYPTO_ARROW_ACCOUNTS == ["ang", "inf", "flz", "men", "fin"]


def test_crypto_plot_renders_with_live_overlay(tmp_path):
    import live_trade_history as lth
    from pathlib import Path as P
    fills = lth.fetch_trades("ENSUSDT", ["ang", "inf", "flz", "men", "fin"], [ROOT / "data" / "history"])
    assert sum(len(v) for v in fills.values()) > 0
    dfs = {"4h": _df(200, 1), "1h": _df(300, 2), "15m": _df(400, 3), "3m": _df(400, 4)}
    asyncio.run(ezr.plot_dfs_subplots(
        rank_label="W01", symbol="ENSUSDT", dfs=dfs, final_score=0.0,
        final_score_norm_arg=1.0, final_score_recent_arg=1.0, prox_norm_arg=0.0,
        band_score_arg=0.0, trend_val_arg=0.0, rp_global_arg=0.0, output_dir=str(tmp_path)))
    outs = list(P(tmp_path).glob("*.png"))
    assert len(outs) == 1 and outs[0].stat().st_size > 50000
