"""Regression: tradier_indicators must emit all fields consumed by tradier_manage.parse_market_data
and key entry/exit gates. Covers fraction-of-indicators outage (2026-09-16) where
_save_data stripped current keys and IndicatorCalculator restricted dc/bb/adx to HTF only.
"""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from tradier_indicators import IndicatorCalculator


def _make_df(n=500):
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="5min")
    price = 100 + np.cumsum(np.random.randn(n) * 0.2)
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": np.random.randint(int(1e5), int(1e6), size=n),
            "timestamp": idx.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "close_time": idx,
        }
    )


def test_required_fields_present_for_all_tfs():
    calc = IndicatorCalculator()
    for tf in ("5m", "15m", "1h", "4h", "D"):
        df = _make_df(600)
        res = calc.compute(df, "AAPL", tf, mark_price=101.5, mark_ts=datetime.now(timezone.utc), mid_run=False)
        required = [
            f"k_{tf}",
            f"d_{tf}",
            f"k_{tf}_prev",
            f"wt1_{tf}",
            f"wt2_{tf}",
            f"ha_{tf}",
            f"atr_{tf}",
            f"rsi_{tf}",
            f"dc_high_{tf}",
            f"dc_low_{tf}",
            f"dc_width_{tf}",
            f"dc_position_{tf}",
            f"bb_pct_b_{tf}",
            f"bb_width_{tf}",
            f"mfi_{tf}",
            f"relative_volume_{tf}",
            f"ema_20_{tf}",
            f"sma_200_{tf}",
            f"volume_{tf}",
            f"ha_streak_{tf}",
            f"open_{tf}",
            f"close_{tf}",
        ]
        missing = [k for k in required if k not in res]
        assert not missing, f"TF {tf} missing required fields: {missing} (have {sorted(k for k in res if tf in k)[:10]})"
        # Alias equality: stoch_k ≡ k, _ant ≡ _prev
        for base in ["k", "d"]:
            assert res.get(f"{base}_{tf}") == res.get(f"stoch_{base}_{tf}"), f"{base}_{tf} vs stoch_{base}_{tf} not equal"
            assert res.get(f"{base}_{tf}_prev") == res.get(f"stoch_{base}_{tf}_prev"), f"{base}_{tf}_prev vs stoch_{base}_{tf}_prev"
            assert res.get(f"{base}_{tf}_prev") == res.get(f"{base}_{tf}_ant"), f"{base}_{tf}_prev vs _ant not equal"
            assert res.get(f"stoch_{base}_{tf}_prev") == res.get(f"stoch_{base}_{tf}_ant"), f"stoch_{base}_{tf}_prev vs _ant"


def test_save_data_keeps_current_keys_after_close():
    """_save_data must not strip current keys when timestamp_{tf} is stale (after-hours).
    Previous bug: all current keys ended with _{tf}, stale TF -> dropped -> file only had _prev."""
    import asyncio
    import json
    import pathlib
    import tempfile
    from types import SimpleNamespace

    import tradier_indicators as ti

    tmp = pathlib.Path(tempfile.mkdtemp())
    # Simulate after-hours: timestamp_15m/1h from 16:00, now is 20:30 UTC -> 4.5h old -> would be stale under old 1200s
    stale_ts = "2026-09-15T16:00:00.000000Z"
    fresh_1m = ti.utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    data = {
        "AAPL": {
            "1m_updated_at": fresh_1m,
            "timestamp_1m": fresh_1m,
            "timestamp_5m": stale_ts,
            "timestamp_15m": stale_ts,
            "timestamp_1h": stale_ts,
            "timestamp_4h": stale_ts,
            "timestamp_D": stale_ts,
            "k_15m": 42.0,
            "k_15m_prev": 41.0,
            "wt1_15m": 10.0,
            "dc_high_15m": 150.0,
            "bb_pct_b_15m": 0.8,
            "current_price": 148.0,
        }
    }
    orch = ti.TradierIndicatorOrchestrator.__new__(ti.TradierIndicatorOrchestrator)
    orch.config = SimpleNamespace(DATA_DIR=tmp)
    orch.data = data
    orch.symbols = ["AAPL"]
    orch.symbol_set = {"AAPL"}
    orch._save_lock = asyncio.Lock()
    orch._last_payload_hash = None
    orch._save_due = True
    orch._dirty = True
    orch._refresh_sentiment = lambda: None

    async def _fake_broadcast(safe, payload):
        (tmp / "tradier_indicators_latest.json").write_bytes(payload if isinstance(payload, bytes) else payload.encode())

    orch._broadcast_to_redis = _fake_broadcast
    asyncio.new_event_loop().run_until_complete(orch._save_data())
    saved = json.loads((tmp / "tradier_indicators_latest.json").read_text())
    assert "AAPL" in saved, "symbol must be kept"
    assert "k_15m" in saved["AAPL"], "current k_15m must not be stripped after-hours (was filtered as stale)"
    assert "wt1_15m" in saved["AAPL"], "wt1_15m must be retained"
    assert "dc_high_15m" in saved["AAPL"], "dc_high_15m must be retained"
    # New file must contain alias equality
    assert saved["AAPL"].get("stoch_k_15m") == saved["AAPL"].get("k_15m"), "stoch_k alias not persisted"
    assert saved["AAPL"].get("k_15m_ant") == saved["AAPL"].get("k_15m_prev"), "_ant vs _prev not aliased in file"
