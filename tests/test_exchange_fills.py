#!/usr/bin/env python3
"""Exchange fills: order grouping, FIFO trips, direction mapping."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import exchange_fills as xf


def _mk(ts, side, direction, qty, px, pnl=0.0, oid=1, pos="LONG"):
    return {"timestamp": ts, "side": pos, "direction": direction, "qty": qty,
            "price": px, "pnl": pnl, "order": oid, "phase": None, "win": None}


def test_trip_win_from_realized_pnl():
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    evs = [_mk(base, "b", "buy", 5.0, 100.0, 0.0, 11),
           _mk(base + timedelta(minutes=30), "s", "sell", 5.0, 110.0, 50.0, 12)]
    out = xf.trip_tagged(evs)
    assert [(e["phase"], e["win"]) for e in out] == [("entry", True), ("exit", True)]


def test_trip_loss_and_fifo_fallback_without_pnl():
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    evs = [_mk(base, "b", "buy", 5.0, 100.0, 0.0, 11),
           _mk(base + timedelta(minutes=30), "s", "sell", 5.0, 90.0, 0.0, 12)]
    out = xf.trip_tagged(evs)
    assert [(e["phase"], e["win"]) for e in out] == [("entry", False), ("exit", False)]


def test_short_direction_and_partial_trim_adapt():
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    evs = [_mk(base, "s", "sell", 10.0, 100.0, 0.0, 11, "SHORT"),
           _mk(base + timedelta(minutes=5), "b", "buy", 4.0, 99.0, 4.0, 12, "SHORT"),
           _mk(base + timedelta(minutes=9), "b", "buy", 6.0, 98.0, 12.0, 13, "SHORT")]
    out = xf.trip_tagged(evs)
    assert [e["phase"] for e in out] == ["entry", "adapt", "exit"]
    assert out[-1]["win"] is True and out[0]["win"] is True


def test_open_position_stays_win_none():
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    out = xf.trip_tagged([_mk(base, "b", "buy", 5.0, 100.0, 0.0, 11)])
    assert out[0]["phase"] == "entry" and out[0]["win"] is None
