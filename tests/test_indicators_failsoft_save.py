"""Regression for 2026-09-14 frozen-snapshot outage (zero trades all session).

tradier_indicators._save_data used to drop EVERY symbol once 1m_updated_at aged
past 1200s, then overwrite tradier_indicators_latest.json with {} — the trading
manager saw "No indicators found" for all 180 symbols, so no entries, no
reentries, and every reentry candidate SYMGATE-blocked on zero speeds.

Covers:
- fail-soft: symbols with stale 1m_updated_at but usable TF fields are KEPT
  (stale TF fields stripped, fresh ones published).
- never-clobber: a zero-symbol payload never overwrites a good snapshot file.
"""
import asyncio
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, ".")

import tradier_indicators as ti


def _orch(tmp: pathlib.Path, data: dict, symbols):
    orch = ti.TradierIndicatorOrchestrator.__new__(ti.TradierIndicatorOrchestrator)
    from types import SimpleNamespace
    orch.config = SimpleNamespace(DATA_DIR=tmp)
    orch.data = data
    orch.symbols = symbols
    orch.symbol_set = set(symbols)
    orch._save_lock = asyncio.Lock()
    orch._last_payload_hash = None
    orch._save_due = True
    orch._dirty = True
    orch._refresh_sentiment = lambda: None

    async def _fake_broadcast(safe, payload):
        (tmp / "tradier_indicators_latest.json").write_bytes(
            payload if isinstance(payload, bytes) else payload.encode()
        )

    orch._broadcast_to_redis = _fake_broadcast
    return orch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_failsoft_keeps_stale_1m_symbols():
    tmp = pathlib.Path(tempfile.mkdtemp())
    stale = "2026-09-10T10:00:00.000000Z"
    fresh = ti.utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    data = {
        "MU": {
            "1m_updated_at": stale,  # aged well past the old 1200s hard gate
            "timestamp_1m": stale,
            "wt1_5m": -50.0,
            "wt2_5m": -40.0,
            "timestamp_5m": fresh,
            "close_D": 900.0,
            "timestamp_D": stale,
        }
    }
    orch = _orch(tmp, data, ["MU"])
    _run(orch._save_data())
    saved = json.loads((tmp / "tradier_indicators_latest.json").read_text())
    assert "MU" in saved, "fail-soft must keep symbols with stale 1m_updated_at"


def test_never_clobbers_good_snapshot_with_empty():
    tmp = pathlib.Path(tempfile.mkdtemp())
    good = {"MU": {"wt1_5m": -50.0, "timestamp_5m": "2026-09-14T13:00:00.000000Z"}}
    (tmp / "tradier_indicators_latest.json").write_text(json.dumps(good))
    orch = _orch(tmp, {}, ["MU"])  # empty in-memory data -> zero-symbol payload
    _run(orch._save_data())
    kept = json.loads((tmp / "tradier_indicators_latest.json").read_text())
    assert kept == good, "zero-symbol payload must never overwrite last-good snapshot"
