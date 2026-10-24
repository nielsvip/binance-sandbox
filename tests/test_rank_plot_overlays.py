#!/usr/bin/env python3
"""Plot overlays — USER 2026-10-24: arrow flags, /history/ trade mapping, RSI strip.

Pins the stocks-plot behaviour: tops/bottoms arrows off by default (code kept),
per-account history trades mapped to buy/sell arrow directions, Wilder RSI sane.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ez_rankings as ezr
import tradier_rankings as trr


def test_arrow_flags_default_off_but_present():
    assert trr.SHOW_TOP_BOTTOM_ARROWS is False
    assert ezr.SHOW_TOP_BOTTOM_ARROWS is False
    assert trr.SHOW_ACCOUNT_TRADE_ARROWS is True
    assert trr.SHOW_RSI_STRIP is True
    assert trr.SHOW_TOP_LEGEND is False
    assert trr.ACCOUNT_ARROW_COLORS == {"ang": "#2ecc71", "inf": "#42a5f5", "flz": "#ff5252", "men": "#ffa726", "fin": "#9e9e9e"}


def test_history_direction_mapping():
    assert trr._history_event_direction("OPEN", "LONG") == "buy"
    assert trr._history_event_direction("AUGMENT", "LONG") == "buy"
    assert trr._history_event_direction("REDUCE", "LONG") == "sell"
    assert trr._history_event_direction("CLOSE", "LONG") == "sell"
    assert trr._history_event_direction("OPEN", "SHORT") == "sell"
    assert trr._history_event_direction("AUGMENT", "SHORT") == "sell"
    assert trr._history_event_direction("COVER", "SHORT") == "buy"
    assert trr._history_event_direction("CLOSE", "SHORT") == "buy"
    assert trr._history_event_direction("REDUCE", "SHORT") == "buy"


def test_history_reader_finds_trb_aapl():
    out = trr.fetch_account_trades_from_history("AAPL")
    assert "trb" in out and len(out["trb"]) > 0
    for ev in out["trb"]:
        assert ev["direction"] in ("buy", "sell")
        assert pd.notna(ev["timestamp"])
    ts = [e["timestamp"] for e in out["trb"]]
    assert ts == sorted(ts)


def test_history_reader_empty_symbol():
    assert trr.fetch_account_trades_from_history("NO_SUCH_SYM_ZZZ") == {}
    assert trr.fetch_account_trades_from_history("") == {}


def test_wilder_rsi_shape():
    rising = pd.Series(float(i) for i in range(1, 60))
    r = trr._wilder_rsi(rising)
    assert len(r) == len(rising)
    assert r.iloc[-1] > 90.0
    falling = pd.Series(float(60 - i) for i in range(60))
    assert trr._wilder_rsi(falling).iloc[-1] < 10.0
    flat = pd.Series([100.0] * 60)
    assert (trr._wilder_rsi(flat).iloc[-1] - 50.0) ** 2 < 1e-9
    assert bool(((r >= 0.0) & (r <= 100.0)).all())
