import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tradier_klines as tk


def test_export_converts_legacy_rows_and_never_drops_any():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "TST_D.json"
        legacy = [
            {"timestamp": "2024-10-15T20:00:00+00:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5, "timestamp_dt": "NaT"},
            {"timestamp": "2024-10-16", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 6},
            {"timestamp": "2024-10-16T20:00:00.000000Z", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 6},
        ]
        path.write_text(json.dumps(legacy))
        frame = pd.DataFrame({"open": [4.0], "high": [5.0], "low": [4.0], "close": [5.0], "volume": [9.0]}, index=pd.DatetimeIndex(["2024-10-17T20:00:00Z"]))
        assert tk._export_atomic_locked(frame, path, None) in (True, False)
        rows = json.loads(path.read_text())
        stamps = [r["timestamp"] for r in rows]
        assert all(len(s) == 27 and s.endswith("Z") for s in stamps), stamps
        assert len(rows) >= 4, "a row was dropped"
        assert rows[0]["timestamp"] == "2024-10-15T20:00:00.000000Z" and rows[0]["timestamp_dt"] == rows[0]["timestamp"]
        assert [r["close"] for r in rows if r["timestamp"].startswith("2024-10-16")] == [3, 3]
