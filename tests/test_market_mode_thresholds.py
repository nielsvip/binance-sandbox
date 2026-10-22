#!/usr/bin/env python3
"""Market-mode sensor thresholds — USER 2026-10-22: light <12, normal 12-40, extreme >40.

The old 65/35 pair never fired (5d of log, 372 readings, max index 26.0), so
NORMAL/EXTREME size tables were dead. Pins the mapping through the real
trigger_market_mode with all side effects stubbed (no file write, no Config
mutation, no signal record).
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ez_rankings as ezr


def _run(index: float) -> dict:
    seen = {}
    ezr.last_market_mode = "SENTINEL"
    orig_set = ezr.Config.set_market_mode
    orig_write = ezr._write_market_mode_file
    orig_record = ezr.record_market_mode_signal
    ezr.Config.set_market_mode = classmethod(lambda cls, m: seen.setdefault("mode", m))
    ezr._write_market_mode_file = lambda mode_data: seen.setdefault("written", mode_data)
    async def _noop(_mode, _index):
        seen["recorded"] = _mode
    ezr.record_market_mode_signal = _noop
    try:
        new_mode, _changed, mode_data = asyncio.run(ezr.trigger_market_mode(index))
    finally:
        ezr.Config.set_market_mode = orig_set
        ezr._write_market_mode_file = orig_write
        ezr.record_market_mode_signal = orig_record
    assert seen.get("mode") == new_mode
    assert seen.get("written", {}).get("mode") == new_mode
    return mode_data


def test_light_below_12():
    for index in (0.0, 6.8, 11.9):
        assert _run(index)["mode"] == "LIGHT_MODE", index


def test_normal_12_to_40():
    for index in (12.0, 13.0, 26.0, 40.0):
        assert _run(index)["mode"] == "NORMAL_MODE", index


def test_extreme_above_40():
    for index in (40.1, 65.0, 100.0):
        assert _run(index)["mode"] == "EXTREME_MODE", index


def test_thresholds_published():
    assert _run(20.0)["thresholds"] == {"extreme": 40, "light": 12}


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
