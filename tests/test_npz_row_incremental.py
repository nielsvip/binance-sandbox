"""Incremental NPZ row == full computation (USER 2026-10-28 "NO complete npz calculation only 1 bar per 15min")."""
import json
import math
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
import npz_live_generator as gen  # noqa: E402

STEP = {"3m": 180, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}
ROWS = {"3m": 9000, "15m": 6000, "1h": 3000, "4h": 2200, "D": 700}


def _write_klines(base: Path, symbol: str, end: datetime, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    (base / "klines_cache").mkdir(parents=True, exist_ok=True)
    for tf, n in ROWS.items():
        step = STEP[tf]
        last = int(end.timestamp()) // step * step
        price = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
        rows = []
        for i in range(n):
            ts = datetime.fromtimestamp(last - (n - 1 - i) * step, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            o = float(round(price[i - 1] if i else price[0], 6))
            c = float(round(price[i], 6))
            h = float(round(max(o, c) * (1 + abs(rng.normal(0, 0.002))), 6))
            l = float(round(min(o, c) * (1 - abs(rng.normal(0, 0.002))), 6))
            rows.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": float(round(abs(rng.normal(1e5, 3e4)), 2))})
        (base / "klines_cache" / f"{symbol}_{tf}.json").write_text(json.dumps(rows))


def _diffs(full, inc):
    out = []
    for key in sorted((set(full) | set(inc)) - {"1m_updated_at", "timestamp_1m"}):
        if key not in full or key not in inc:
            out.append((key, "missing"))
            continue
        a, b = full[key], inc[key]
        if isinstance(a, float) and isinstance(b, float) and not isinstance(a, bool):
            if a == b or (math.isnan(a) and math.isnan(b)):
                continue
            if abs(a - b) / max(abs(a), abs(b)) > 1e-9:
                out.append((key, a, b))
        elif a != b or type(a) is not type(b):
            out.append((key, a, b))
    return out


def test_incremental_row_equals_full_row(tmp_path, monkeypatch):
    end = datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc)
    _write_klines(tmp_path, "TESTUSDC", end)
    manager = types.SimpleNamespace(base_path=tmp_path)
    for back in (0, 1, 4):
        close_ts = end - timedelta(minutes=15 * (back + 1))
        monkeypatch.setenv("NPZ_ROW_MODE", "full")
        full = gen._compute_row_for_15m_close("TESTUSDC", close_ts, manager, None, "crypto")
        monkeypatch.delenv("NPZ_ROW_MODE")
        inc = gen._compute_row_incremental("TESTUSDC", close_ts, manager, "crypto")
        assert inc is not None and full is not None
        assert _diffs(full, inc) == []
        assert {k for k in inc if k.startswith("smfi_")}, "smfi fields must be present and patched"
    assert list((tmp_path / "data" / "npz_row_prefix_cache").glob("TESTUSDC_15m.*.npz")), "prefix cache built on the newest bar"


def test_prefix_cache_rejected_when_history_changes(tmp_path):
    end = datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc)
    _write_klines(tmp_path, "TESTUSDC", end)
    manager = types.SimpleNamespace(base_path=tmp_path)
    close_ts = end - timedelta(minutes=15)
    assert gen._compute_row_incremental("TESTUSDC", close_ts, manager, "crypto") is not None
    path = tmp_path / "klines_cache" / "TESTUSDC_15m.json"
    rows = json.loads(path.read_text())
    rows[10]["close"] = rows[10]["close"] * 1.01  # a healed bar deep in history
    path.write_text(json.dumps(rows))
    raw = path.read_bytes()
    assert gen._prefix_cache_load(tmp_path / "data" / "npz_row_prefix_cache", path, raw) is None
    os.environ["NPZ_ROW_MODE"] = "full"
    try:
        full = gen._compute_row_for_15m_close("TESTUSDC", close_ts, manager, None, "crypto")
    finally:
        os.environ.pop("NPZ_ROW_MODE")
    inc = gen._compute_row_incremental("TESTUSDC", close_ts, manager, "crypto")
    assert _diffs(full, inc) == []


def test_unordered_file_refuses_to_full(tmp_path):
    end = datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc)
    _write_klines(tmp_path, "TESTUSDC", end)
    path = tmp_path / "klines_cache" / "TESTUSDC_1h.json"
    rows = json.loads(path.read_text())
    rows.append(dict(rows[5]))  # an old bar re-appended at the end
    path.write_text(json.dumps(rows))
    manager = types.SimpleNamespace(base_path=tmp_path)
    assert gen._compute_row_incremental("TESTUSDC", end - timedelta(minutes=15), manager, "crypto") is None
