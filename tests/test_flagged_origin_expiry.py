"""Flagged-origin expiry (USER 2026-10-24: stale flags never veto).

A flag means the origin recently needed rescue hedges. Entries whose last
strike is older than FLAGGED_ORIGIN_MAX_AGE_H (default 7d) expired — a 2022 or
April-2026 flag must not veto today's entries while the vector dictates
through (flz:BNBUSDC_SHORT flagged 8x, last 2026-04-22, blocked native daily).
Both read sites (ez_manage inline + tradeable_state_gates core) share
flagged_hit_count, so one test pins both paths' logic.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_fresh_strikes_count():
    from vec_paths.tradeable_state_gates import flagged_hit_count

    now = time.time()
    assert flagged_hit_count({"count": 3, "last_ts": now - 100}, now) == 3
    assert flagged_hit_count({"count": 8, "last_ts": now - 6 * 86400}, now) == 8


def test_stale_strikes_expire():
    from vec_paths.tradeable_state_gates import flagged_hit_count

    now = time.time()
    assert flagged_hit_count({"count": 8, "last_ts": now - 8 * 86400}, now) == 0
    assert flagged_hit_count({"count": 5, "last_ts": 1641012480.0}, now) == 0
    assert flagged_hit_count({"count": 8, "last_ts": 1776382568.0}, now, 168.0) == 0


def test_boundary_and_malformed():
    from vec_paths.tradeable_state_gates import flagged_hit_count

    now = time.time()
    assert flagged_hit_count({"count": 3, "last_ts": now - 168 * 3600 + 60}, now) == 3
    assert flagged_hit_count({"count": 0, "last_ts": now}, now) == 0
    assert flagged_hit_count({}, now) == 0
    assert flagged_hit_count(None, now) == 0
    assert flagged_hit_count({"count": "x", "last_ts": now}, now) == 0
    assert flagged_hit_count({"count": 4}, now) == 4
    assert flagged_hit_count("plain-member", now) == 1


def test_custom_window():
    from vec_paths.tradeable_state_gates import flagged_hit_count

    now = time.time()
    e = {"count": 3, "last_ts": now - 30 * 3600}
    assert flagged_hit_count(e, now, 24.0) == 0
    assert flagged_hit_count(e, now, 48.0) == 3
