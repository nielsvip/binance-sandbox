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
    assert lth.MISMATCH_VEC_MPL == ("#bdbdbd", 0.90)
    assert lth.MISMATCH_LIVE_MPL == ("#757575", 0.90)


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
    lo0, hi0 = ax.get_ylim()
    nv, nl = lth.draw_mismatch_lines(ax, base.replace(hour=9), base.replace(hour=16),
                                     sig, [], fills, [], lambda t: t.timestamp())
    assert (nv, nl) == (1, 0)
    vecs = [l for l in ax.get_children() if isinstance(l, Line2D)
            and l.get_marker() == "^" and l.get_color() == lth.MISMATCH_VEC_MPL[0]]
    assert len(vecs) == 1
    assert abs(vecs[0].get_alpha() - lth.MISMATCH_VEC_MPL[1]) < 1e-9
    assert vecs[0].get_ydata()[0] < lo0
    assert lth.MISMATCH_VEC_MPL[0] == "#bdbdbd" and lth.MISMATCH_LIVE_MPL[0] == "#757575"
    plt.close(fig)


def _wlt(h, phase, qty, px):
    from datetime import datetime, timezone
    return {"ts": datetime(2026, 10, 15, h, 0, tzinfo=timezone.utc),
            "phase": phase, "qty": qty, "price": px, "type": "OPEN"}


def test_annotate_round_trips_long_win():
    evs = [_wlt(8, "entry", 5.0, 100.0), _wlt(9, "exit", 5.0, 110.0)]
    lth.annotate_round_trips(evs, "LONG")
    assert [e["win"] for e in evs] == [True, True]


def test_annotate_round_trips_long_loss_and_short_sign():
    evs = [_wlt(8, "entry", 5.0, 100.0), _wlt(9, "exit", 5.0, 90.0)]
    lth.annotate_round_trips(evs, "LONG")
    assert [e["win"] for e in evs] == [False, False]
    evs = [_wlt(8, "entry", 5.0, 100.0), _wlt(9, "exit", 5.0, 90.0)]
    lth.annotate_round_trips(evs, "SHORT")
    assert [e["win"] for e in evs] == [True, True]


def test_annotate_round_trips_unmatched_and_open_stay_grey():
    evs = [_wlt(8, "exit", 5.0, 90.0), _wlt(9, "entry", 5.0, 100.0)]
    lth.annotate_round_trips(evs, "LONG")
    assert [e["win"] for e in evs] == [None, None]


def test_dedupe_mirror_lines_before_accounting():
    from datetime import datetime, timezone
    ts = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    recs = [{"timestamp": ts, "side": "LONG", "type": "OPEN", "qty": 5.0, "price": 100.0},
            dict(timestamp=ts, side="LONG", type="OPEN", qty=5.0, price=100.0)]
    assert len(lth._dedupe_mirror_lines(recs)) == 1


def test_collapse_stuck_relogs_keeps_first():
    from datetime import datetime, timezone
    base = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    recs = [{"timestamp": base + timedelta(minutes=2 * i), "side": "SHORT",
             "type": "AUGMENT", "qty": 21.0, "price": 89.075} for i in range(5)]
    out = lth._collapse_stuck_relogs(recs)
    assert len(out) == 1 and out[0]["timestamp"] == base


def test_no_phantom_win_without_exit():
    from datetime import datetime, timezone
    ts = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    recs = [{"timestamp": ts, "side": "LONG", "type": "OPEN", "qty": 5.0, "price": 100.0},
            dict(timestamp=ts, side="LONG", type="OPEN", qty=5.0, price=100.0)]
    evs = lth.annotate_round_trips(lth.mark_boundaries(lth._dedupe_mirror_lines(recs)), "LONG")
    assert [e["win"] for e in lth.boundary_events(evs)] == [None]


def test_draw_outcome_lines_win_loss_skip_unknown():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime, timezone
    from matplotlib.lines import Line2D
    fig, ax = plt.subplots()
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    lo, hi = base.replace(hour=9), base.replace(hour=16)
    evs = [{"timestamp": base.replace(hour=10), "win": True, "phase": "exit"},
           {"timestamp": base.replace(hour=11), "win": False, "phase": "exit"},
           {"timestamp": base.replace(hour=12), "win": None, "phase": "exit"},
           {"timestamp": base.replace(hour=13), "win": True, "phase": "entry"},
           {"timestamp": base.replace(hour=18), "win": True, "phase": "exit"}]
    nw, nl = lth.draw_outcome_lines(ax, lo, hi, evs, lambda t: t.timestamp())
    assert (nw, nl) == (1, 1)
    greens = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_color() == lth.WIN_COLOR]
    reds = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_color() == lth.LOSS_COLOR]
    assert len(greens) == 1 and len(reds) == 1
    assert abs(greens[0].get_alpha() - lth.OUTCOME_ALPHA) < 1e-9
    plt.close(fig)


def test_draw_mismatch_lines_skips_without_signal_feed():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime, timezone
    from matplotlib.lines import Line2D
    fig, ax = plt.subplots()
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    n0 = len(ax.get_children())
    nv, nl = lth.draw_mismatch_lines(ax, base.replace(hour=9), base.replace(hour=16),
                                     [], [], [base.replace(hour=10)], [], lambda t: t.timestamp())
    assert (nv, nl) == (0, 0)
    assert len(ax.get_children()) == n0
    plt.close(fig)


def test_draw_mismatch_lines_live_side():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime, timezone
    from matplotlib.lines import Line2D
    fig, ax = plt.subplots()
    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    lo0, hi0 = ax.get_ylim()
    nv2, nl2 = lth.draw_mismatch_lines(ax, base.replace(hour=9), base.replace(hour=16),
                                       [], [], [], [base.replace(hour=12)], lambda t: t.timestamp())
    assert (nv2, nl2) == (0, 1)
    lives = [l for l in ax.get_children() if isinstance(l, Line2D)
             and l.get_marker() == "v" and l.get_color() == lth.MISMATCH_LIVE_MPL[0]]
    assert len(lives) == 1
    assert lives[0].get_ydata()[0] > hi0
    plt.close(fig)


def test_fetch_empty_symbol():
    assert lth.fetch_trades("NO_SUCH_SYM_ZZZ", ["trb"], [ROOT / "data" / "tradier" / "history"]) == {}
