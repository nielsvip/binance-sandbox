"""2026-10-24 RLCUSDT Age1m 1588s + IP -1003 storm: the sweep-everything repair
(706 REST calls / fixed 60s cycle) throttled the whole IP into congestion
collapse, per-symbol fetches missed, and the scan fell back to 30-min cold data.

The loop is now self-sustaining: repair ONLY stale symbols stalest-first
(capped), back off exponentially on 429/418, shrink concurrency while throttled.
Steady state costs ~zero calls. These pins make that law load-bearing: if a
future edit reverts to sweep-everything or drops the backoff, this goes red.
"""
from ez_market_data import (
    REST_REPAIR_BACKOFF_CAP_S,
    REST_REPAIR_MAX_SYMS,
    REST_REPAIR_STALE_1M_S,
    REST_REPAIR_STALE_3M_S,
    _pick_due_stale,
    _repair_backoff_s,
    _repair_due,
    _repair_req_symbol,
)

NOW = 1_792_818_945.0


def _store(age_1m, age_3m, n=100):
    return {
        "closes_1m": {NOW - age_1m: 1.0},
        "closes_3m": {NOW - age_3m: 2.0},
        "_n": n,
    }


def test_due_fresh_symbol_is_skipped():
    assert _repair_due(NOW, NOW - 10, NOW - 30, 100) == (False, False)


def test_due_only_the_stale_tf():
    assert _repair_due(NOW, NOW - 500, NOW - 30, 100) == (True, False)
    assert _repair_due(NOW, NOW - 10, NOW - 500, 100) == (False, True)


def test_due_empty_history_always_due():
    assert _repair_due(NOW, 0, 0, 0) == (True, True)


def test_due_boundary_is_not_due():
    assert _repair_due(NOW, NOW - REST_REPAIR_STALE_1M_S, NOW - REST_REPAIR_STALE_3M_S, 100) == (False, False)


def test_backoff_doubles_to_cap():
    assert _repair_backoff_s(1) == 15.0
    assert _repair_backoff_s(2) == 30.0
    assert _repair_backoff_s(3) == 60.0
    assert _repair_backoff_s(5) == 240.0
    assert _repair_backoff_s(6) == REST_REPAIR_BACKOFF_CAP_S
    assert _repair_backoff_s(20) == REST_REPAIR_BACKOFF_CAP_S


def test_pick_returns_only_due_stalest_first():
    data = {
        "FRESH": _store(5, 10),
        "OLD1M": _store(500, 10),
        "OLD3M": _store(5, 900),
        "OLD2M": _store(200, 10),
    }
    picked = _pick_due_stale(data, NOW)
    assert [s for s, _, _ in picked] == ["OLD3M", "OLD1M", "OLD2M"]
    assert dict(((s, (d1, d3)) for s, d1, d3 in picked)) == {
        "OLD3M": (False, True),
        "OLD1M": (True, False),
        "OLD2M": (True, False),
    }


def test_pick_caps_at_max_syms():
    data = {f"S{i:03d}": _store(1000 + i, 1000 + i) for i in range(REST_REPAIR_MAX_SYMS + 20)}
    picked = _pick_due_stale(data, NOW)
    assert len(picked) == REST_REPAIR_MAX_SYMS
    assert picked[0][0] == f"S{REST_REPAIR_MAX_SYMS + 19:03d}"


def test_pick_empty_or_broken_is_empty():
    assert _pick_due_stale({}, NOW) == []
    assert _pick_due_stale(None, NOW) == []
    assert _pick_due_stale({"X": None}, NOW) == [("X", True, True)]


def test_pick_limit_shrinks_under_throttle():
    data = {f"S{i:03d}": _store(1000 + i, 1000 + i) for i in range(40)}
    assert len(_pick_due_stale(data, NOW, 60)) == 40
    assert len(_pick_due_stale(data, NOW, 60 >> 1)) == 30
    assert len(_pick_due_stale(data, NOW, 60 >> 4)) == 3
    assert _pick_due_stale(data, NOW, 60 >> 4)[0][0] == "S039"


def test_req_symbol_strips_side_suffix():
    assert _repair_req_symbol("ZROUSDT_LONG") == "ZROUSDT"
    assert _repair_req_symbol("ZKUSDT_SHORT") == "ZKUSDT"
    assert _repair_req_symbol("BTCUSDT") == "BTCUSDT"
    assert _repair_req_symbol("") == ""


def test_pick_honors_skip_until_expiry():
    data = {f"S{i:03d}": _store(1000 + i, 1000 + i) for i in range(5)}
    skip = {"S004": NOW + 1800.0, "S003": NOW - 1.0}
    picked = _pick_due_stale(data, NOW, 60, skip)
    assert [s for s, _, _ in picked] == ["S003", "S002", "S001", "S000"]
