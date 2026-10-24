#!/usr/bin/env python3
"""Vec trade feeds: HTML frozen backtest trades + forward parity trips."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vec_trade_feeds as vtf


def _w(h, m=0):
    return datetime(2026, 10, 15, h, m, tzinfo=timezone.utc)


def test_draw_vec_mismatch_marks_only_unmatched():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    fig, ax = plt.subplots()
    lo, hi = _w(9), _w(16)
    lo0, hi0 = ax.get_ylim()
    ve = [_w(10), _w(13)]
    vx = [_w(11)]
    fe = [_w(10, 5)]
    fx = [_w(11, 5), _w(15)]
    nv, nl = vtf.draw_vec_mismatch(ax, lo, hi, ve, vx, fe, fx, lambda t: t.timestamp())
    assert (nv, nl) == (1, 1)
    ups = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_marker() == "^"]
    dns = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_marker() == "v"]
    assert len(ups) == 1 and len(dns) == 1
    assert ups[0].get_ydata()[0] < lo0 and dns[0].get_ydata()[0] > hi0
    plt.close(fig)


def test_draw_vec_mismatch_empty_vec_draws_nothing():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    n0 = len(ax.get_children())
    assert vtf.draw_vec_mismatch(ax, _w(9), _w(16), [], [], [_w(10)], [_w(11)],
                                 lambda t: t.timestamp()) == (0, 0)
    assert len(ax.get_children()) == n0
    plt.close(fig)


def test_load_html_trades_merges_files_with_dedupe(tmp_path, monkeypatch):
    import vec_trade_feeds as v
    d = tmp_path / "sheets"
    d.mkdir()
    t1 = {"entry_time": "2026-10-01T10:00:00+00:00", "exit_time": "2026-10-01T12:00:00+00:00"}
    t2 = {"entry_time": "2026-10-02T10:00:00+00:00", "exit_time": "2026-10-02T12:00:00+00:00"}
    (d / "TST_LONG_a_30d_matrix.html").write_text('const TRADES = [%s];' % ",".join(
        __import__("json").dumps(t) for t in [t1, t2]))
    (d / "TST_LONG_b_365d_matrix.html").write_text('const TRADES = [%s];' % ",".join(
        __import__("json").dumps(t) for t in [t2]))
    monkeypatch.setattr(v, "HTML_DIR", d)
    v._html_cache.clear()
    try:
        he, hx = v.load_html_trades("TST", "LONG")
    finally:
        v._html_cache.clear()
    assert len(he) == 2 and len(hx) == 2
    assert he[0].day == 1 and he[1].day == 2


def test_load_forward_trips_reads_newest_report(tmp_path, monkeypatch):
    import json
    import vec_trade_feeds as v
    d = tmp_path / "fwd"
    d.mkdir()
    rep = {"vec_trips_all": [{"entry_ts": 1760512800.0, "exit_ts": 1760520000.0}],
           "live_trips_all": [{"entry_ts": 1760600000.0, "exit_ts": None}]}
    (d / "TST_LONG_trade_parity.json").write_text(json.dumps(rep))
    monkeypatch.setattr(v, "FWD_DIR", d)
    v._fwd_index = None
    try:
        fe, fx = v.load_forward_trips("TST", "LONG")
    finally:
        v._fwd_index = None
    assert len(fe) == 2 and len(fx) == 1
    assert fe[0].tzinfo is not None
