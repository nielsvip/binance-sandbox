"""Regression: market_data (ez_indicators) must contain REQUIRED_INDICATORS technical fields.

Covers alias mismatch where REQUIRED expects stoch_k_3m etc. while compute
historically emitted k_3m only, and ema_20_std_4h only emitted for 3m.
"""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

import numpy as _np

_np.NaN = _np.nan  # pandas_ta compat numpy 2

from config import REQUIRED_INDICATORS
from ez_indicators import IndicatorCalculator


def _make_df(n=900):
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="1min")
    price = 50000 + np.cumsum(np.random.randn(n) * 10)
    df = pd.DataFrame(
        {
            "open": price,
            "high": price + 20,
            "low": price - 20,
            "close": price + np.random.randn(n) * 5,
            "volume": np.random.randint(500, 2000, size=n),
            "timestamp": idx.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    )
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True)
    df["close_time"] = df["timestamp_dt"]
    return df


def test_market_data_technical_required_present():
    calc = IndicatorCalculator()
    merged = {}
    for tf in ["3m", "15m", "1h", "4h", "D"]:
        res = calc.compute(_make_df(1000), timeframe=tf, mark_price=None, mid_run=False, mark_price_ts=None)
        merged.update(res)
    # REQUIRED includes ranking/sentiment/zconviction/1m which are orchestrator-level, not per-TF compute.
    # Filter to per-TF technical indicators that compute should emit.
    tech_required = [
        k
        for k in REQUIRED_INDICATORS
        if not k.startswith("0") and not k.startswith("zconviction") and "1m" not in k
    ]
    missing = [k for k in tech_required if k not in merged]
    assert not missing, f"missing technical REQUIRED in merged compute: {missing[:20]} (merged {len(merged)} keys)"

    # Alias check: stoch_k/d must be present alongside k/d and _ant ≡ _prev
    for tf in ["3m", "15m", "1h", "4h", "D"]:
        assert f"stoch_k_{tf}" in merged, f"stoch_k_{tf} alias missing (k_{tf}={merged.get(f'k_{tf}')})"
        assert f"stoch_d_{tf}" in merged, f"stoch_d_{tf} alias missing"
        assert f"stoch_k_{tf}_prev" in merged
        assert f"stoch_d_{tf}_prev" in merged
        assert merged.get(f"stoch_k_{tf}") == merged.get(f"k_{tf}"), f"stoch_k_{tf} != k_{tf}"
        assert merged.get(f"stoch_d_{tf}") == merged.get(f"d_{tf}"), f"stoch_d_{tf} != d_{tf}"
        assert merged.get(f"k_{tf}_prev") == merged.get(f"k_{tf}_ant"), f"k_{tf}_prev != _ant"
        assert merged.get(f"stoch_k_{tf}_prev") == merged.get(f"stoch_k_{tf}_ant"), f"stoch_k_{tf}_prev != _ant"

    # ema_20_std must exist for all TFs where ema_20 exists (including 4h)
    for tf in ["3m", "15m", "1h", "4h", "D"]:
        if f"ema_20_{tf}" in merged:
            assert f"ema_20_std_{tf}" in merged, f"ema_20_std_{tf} missing while ema_20_{tf} present"


def test_tradier_mirror_fields_still_present():
    """Ensure crypto fix didn't drop HTF fields that tradier also relies on."""
    calc = IndicatorCalculator()
    res = calc.compute(_make_df(1200), timeframe="1h", mark_price=None, mid_run=False, mark_price_ts=None)
    for k in ["dc_high_1h", "dc_low_1h", "bb_pct_b_1h", "adx_1h", "rsi_1h", "mfi_1h", "ha_1h"]:
        assert k in res, f"HTF {k} missing in 1h"
