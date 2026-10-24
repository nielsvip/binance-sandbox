#!/usr/bin/env python3
"""Parity vlines — greedy time matching and fail-closed anchors."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import v15_html_parity_vlines as pv


def _ts(h, m):
    return datetime(2026, 10, 14, h, m, tzinfo=timezone.utc)


def test_greedy_match_basic():
    vec = [_ts(13, 0), _ts(15, 0)]
    live = [_ts(13, 20), _ts(16, 30)]
    mv, ml = pv.greedy_match(vec, live, 30 * 60)
    assert mv == {0} and ml == {0}


def test_greedy_match_respects_tolerance():
    vec = [_ts(13, 0)]
    live = [_ts(14, 0)]
    mv, ml = pv.greedy_match(vec, live, 30 * 60)
    assert mv == set() and ml == set()
    mv2, ml2 = pv.greedy_match(vec, live, 60 * 60)
    assert mv2 == {0} and ml2 == {0}


def test_greedy_match_no_double_count():
    vec = [_ts(13, 0), _ts(13, 10)]
    live = [_ts(13, 5)]
    mv, ml = pv.greedy_match(vec, live, 30 * 60)
    assert len(mv) == 1 and ml == {0}


def test_to_utc_naive_assumed_utc():
    assert pv.to_utc("2026-10-14 13:36") == datetime(2026, 10, 14, 13, 36, tzinfo=timezone.utc)


def test_missing_anchor_fails_closed(tmp_path):
    bad = tmp_path / "BIDU_SHORT_x_matrix.html"
    bad.write_text("<html>const LIVE = [];</html>")
    sys.argv = ["pv", str(bad)]
    try:
        pv.main()
    except SystemExit as e:
        assert "anchor" in str(e)
    else:
        raise AssertionError("should have failed closed")
