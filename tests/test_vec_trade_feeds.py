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
    ve = [_w(10), _w(13)]
    vx = [_w(11)]
    fe = [_w(10, 5)]
    fx = [_w(11, 5), _w(15)]
    nv, nl = vtf.draw_vec_mismatch(ax, lo, hi, ve, vx, fe, fx, lambda t: t.timestamp())
    assert (nv, nl) == (1, 1)
    orange = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_color() == "#f97316"]
    blue = [l for l in ax.get_children() if isinstance(l, Line2D) and l.get_color() == "#2563eb"]
    assert len(orange) == 1 and len(blue) == 1
    assert all(list(line.get_ydata()) == [0, 1] and line.get_linewidth() == 2.0 for line in orange + blue)
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


def _write_trades(path, *trips):
    import json
    path.write_text("const TRADES = [%s];" % ",".join(json.dumps(t) for t in trips))


def test_load_html_trades_newest_30d_only(tmp_path, monkeypatch):
    import os
    import vec_trade_feeds as v
    d = tmp_path / "sheets"
    d.mkdir()
    t1 = {"entry_time": "2026-10-01T10:00:00+00:00", "exit_time": "2026-10-01T12:00:00+00:00"}
    t2 = {"entry_time": "2026-10-02T10:00:00+00:00", "exit_time": "2026-10-02T12:00:00+00:00"}
    pa = d / "TST_LONG_a_30d_matrix.html"
    pb = d / "TST_LONG_b_30d_matrix.html"
    _write_trades(pa, t1)
    _write_trades(pb, t2)
    os.utime(pb, (2000000000, 2000000000))
    monkeypatch.setattr(v, "HTML_DIR", d)
    v._html_cache.clear()
    try:
        he, hx = v.load_html_trades("TST", "LONG")
    finally:
        v._html_cache.clear()
    assert len(he) == 1 and len(hx) == 1 and he[0].day == 2


def test_load_html_trades_stale_365d_excluded_fresh_one_joins(tmp_path, monkeypatch):
    import os
    import vec_trade_feeds as v
    d = tmp_path / "sheets"
    d.mkdir()
    t30 = {"entry_time": "2026-10-02T10:00:00+00:00", "exit_time": "2026-10-02T12:00:00+00:00"}
    told = {"entry_time": "2026-01-05T10:00:00+00:00", "exit_time": "2026-01-05T12:00:00+00:00"}
    tnew = {"entry_time": "2026-10-03T10:00:00+00:00", "exit_time": "2026-10-03T12:00:00+00:00"}
    p30 = d / "TST_LONG_x_30d_matrix.html"
    pold = d / "TST_LONG_y_365d_matrix.html"
    pnew = d / "TST_LONG_z_365d_matrix.html"
    _write_trades(p30, t30)
    _write_trades(pold, told)
    _write_trades(pnew, tnew)
    os.utime(pold, (1000000000, 1000000000))
    os.utime(p30, (1500000000, 1500000000))
    os.utime(pnew, (2000000000, 2000000000))
    monkeypatch.setattr(v, "HTML_DIR", d)
    v._html_cache.clear()
    try:
        he, hx = v.load_html_trades("TST", "LONG")
    finally:
        v._html_cache.clear()
    days = sorted(e.day for e in he)
    assert days == [2, 3] and len(hx) == 2


def test_load_forward_trips_tags_round_trips(tmp_path, monkeypatch):
    import json
    import vec_trade_feeds as v
    d = tmp_path / "fwd"
    d.mkdir()
    rep = {"rows": [{"ss": "TST_LONG",
                     "vec_window_events": [[1760512800.0, "OPEN", "x"],
                                           [1760516400.0, "AUGMENT", "y"],
                                           [1760520000.0, "CLOSE", "z"]]}]}
    (d / "crypto_latest.json").write_text(json.dumps(rep))
    monkeypatch.setattr(v, "FWD_CRYPTO", d / "crypto_latest.json")
    monkeypatch.setattr(v, "FWD_RUNS", d / "noruns")
    v._fwd_index = None
    try:
        fe, fx = v.load_forward_trips("TST", "LONG")
    finally:
        v._fwd_index = None
    assert len(fe) == 2 and len(fx) == 1
    assert fe[0].tzinfo is not None and fx[0] > fe[-1]
