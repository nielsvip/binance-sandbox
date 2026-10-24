#!/usr/bin/env python3
"""Shared live-trade history helper — direction, colors, matching, dedupe."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import live_trade_history as lth


def test_direction_mapping():
    assert lth.event_direction("OPEN", "LONG") == "buy"
    assert lth.event_direction("REDUCE", "LONG") == "sell"
    assert lth.event_direction("OPEN", "SHORT") == "sell"
    assert lth.event_direction("COVER", "SHORT") == "buy"
    assert lth.event_direction("REDUCE", "SHORT") == "buy"


def test_account_colors():
    assert lth.account_color("trb", "buy") == "#2ecc71"
    assert lth.account_color("trb", "sell") == "#ff5252"
    assert lth.account_color("ang", "buy") == lth.account_color("ang", "sell") == "#2ecc71"
    assert lth.account_color("inf", "buy") == "#42a5f5"
    assert lth.account_color("flz", "sell") == "#ff5252"
    assert lth.account_color("men", "buy") == "#ffa726"
    assert lth.account_color("fin", "sell") == "#9e9e9e"


def test_fetch_dedupes_mirror_and_sorts():
    out = lth.fetch_trades("BIDU", ["trb"], [ROOT / "data" / "tradier" / "history",
                                             ROOT / "data" / "history"])
    evs = out.get("trb", [])
    assert len(evs) == 2
    ts = [e["timestamp"] for e in evs]
    assert ts == sorted(ts)


def test_fetch_empty_symbol():
    assert lth.fetch_trades("NO_SUCH_SYM_ZZZ", ["trb"], [ROOT / "data" / "tradier" / "history"]) == {}
