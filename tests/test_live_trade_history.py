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
    assert lth.account_color("trb", "buy") == "#00e676"
    assert lth.account_color("trb", "sell") == "#ff1744"
    assert lth.account_color("ang", "buy") == lth.account_color("ang", "sell") == "#00e676"
    assert lth.account_color("inf", "buy") == "#2196f3"
    assert lth.account_color("flz", "sell") == "#ff1744"
    assert lth.account_color("men", "buy") == "#ff9100"
    assert lth.account_color("fin", "sell") == "#cfd8dc"


def test_mismatch_constants_faint():
    assert lth.MISMATCH_VEC_MPL[1] <= 0.25
    assert lth.MISMATCH_LIVE_MPL[1] <= 0.25


def test_strips_draw_on_datetime_axis():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    fig, ax = plt.subplots()
    idx = pd.date_range(end="2026-10-24T00:00:00Z", periods=120, freq="15min", tz="UTC")
    close = pd.Series(100 + np.sin(np.arange(120) / 4.0) * 5, index=range(120))
    ax.plot(idx, close.values)
    ax.set_xlim(idx[0], idx[-1])
    lth.draw_rsi_strip(ax, idx, lth.wilder_rsi(close).values)
    k, d = lth.stoch_kd(close)
    lth.draw_stoch_strip(ax, idx, k.values, d.values)
    from matplotlib.axes import Axes as _Axes
    insets = [c for c in ax.get_children() if isinstance(c, _Axes)]
    assert len(insets) == 2
    plt.close(fig)


def test_fetch_dedupes_mirror_and_sorts():
    out = lth.fetch_trades("BIDU", ["trb"], [ROOT / "data" / "tradier" / "history",
                                             ROOT / "data" / "history"])
    evs = out.get("trb", [])
    assert len(evs) == 2
    ts = [e["timestamp"] for e in evs]
    assert ts == sorted(ts)
    assert [e["phase"] for e in evs] == ["entry", "adapt"]


def test_mark_boundaries_round_trip():
    from datetime import datetime, timezone
    def rec(h, typ, qty):
        return {"ts": datetime(2026, 10, 15, h, 0, tzinfo=timezone.utc), "type": typ, "qty": qty}
    recs = [rec(8, "AUGMENT", 5.0), rec(9, "AUGMENT", 3.0), rec(10, "REDUCE", 4.0),
            rec(11, "REDUCE", 4.0), rec(12, "AUGMENT", 2.0)]
    out = lth.mark_boundaries(recs)
    assert [r["phase"] for r in out] == ["entry", "adapt", "adapt", "exit", "entry"]


def test_mark_boundaries_missing_qty_still_counts():
    from datetime import datetime, timezone
    recs = [{"ts": datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc), "type": "OPEN", "qty": None}]
    assert lth.mark_boundaries(recs)[0]["phase"] == "entry"


def test_boundary_events_and_arrow_alpha():
    evs = [{"phase": "entry"}, {"phase": "adapt"}, {"phase": "exit"}, {}]
    assert len(lth.boundary_events(evs)) == 2
    assert lth.arrow_alpha({"phase": "entry"}) > lth.arrow_alpha({"phase": "exit"})


def test_draw_mismatch_lines_both_colors():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime, timezone
    from matplotlib.lines import Line2D
    fig, ax = plt.subplots()
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    sig = [base.replace(hour=10), base.replace(hour=14)]
    fills = [base.replace(hour=10, minute=10)]
    nv, nl = lth.draw_mismatch_lines(ax, base.replace(hour=9), base.replace(hour=16),
                                     sig, [], fills, [], lambda t: t.timestamp())
    assert (nv, nl) == (1, 0)
    vecs = [l for l in ax.get_children() if isinstance(l, Line2D)
            and l.get_color() == lth.MISMATCH_VEC_MPL[0]]
    assert len(vecs) == 1
    assert abs(vecs[0].get_alpha() - lth.MISMATCH_VEC_MPL[1]) < 1e-9
    assert lth.MISMATCH_VEC_MPL[0] == "#4ade80" and lth.MISMATCH_LIVE_MPL[0] == "#f87171"
    nv2, nl2 = lth.draw_mismatch_lines(ax, base.replace(hour=9), base.replace(hour=16),
                                       [], [], [], [base.replace(hour=12)], lambda t: t.timestamp())
    assert (nv2, nl2) == (0, 1)
    lives = [l for l in ax.get_children() if isinstance(l, Line2D)
             and l.get_color() == lth.MISMATCH_LIVE_MPL[0]]
    assert len(lives) == 1
    plt.close(fig)


def test_fetch_empty_symbol():
    assert lth.fetch_trades("NO_SUCH_SYM_ZZZ", ["trb"], [ROOT / "data" / "tradier" / "history"]) == {}
