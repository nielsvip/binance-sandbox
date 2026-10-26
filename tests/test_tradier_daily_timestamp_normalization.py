import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backtest_v8_precompute as B


def test_malformed_tradier_daily_date_timestamps_normalize_to_market_close(tmp_path):
    cache = tmp_path / "klines_cache" / "tradier"
    cache.mkdir(parents=True)
    payloads = {
        "MSTR_D.json": "2026-10-16.000000Z",
        "AEM_D.json": "2026-10-16",
    }
    for filename, timestamp in payloads.items():
        path = cache / filename
        path.write_text(json.dumps([{
            "timestamp": timestamp,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1234.0,
        }]))
        frame = B.load_klines(path)
        assert frame is not None
        assert len(frame) == 1
        assert frame.index[0].isoformat() == "2026-10-16T20:00:00+00:00"
