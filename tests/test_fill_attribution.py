"""Fill attribution + never-hang timeout — no network, no live orders."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import positions_truth as pt  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _fast():
    await asyncio.sleep(0.01)
    return "FAST_OK"


async def _hung_swallow():
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        await asyncio.sleep(60)
        raise
    return "NEVER"


async def _boom():
    raise ValueError("INNER_BOOM")


def test_abandonable_fast_path():
    assert run(pt.await_abandonable(_fast(), timeout=5.0)) == "FAST_OK"


def test_abandonable_abandons_cancel_swallow():
    t0 = time.time()
    with pytest.raises(pt.AbandonTimeout):
        run(pt.await_abandonable(_hung_swallow(), timeout=1.0))
    assert 0.9 < time.time() - t0 < 3.0


def test_abandonable_timeout_is_timeouterror():
    assert isinstance(pt.AbandonTimeout("x"), asyncio.TimeoutError)


def test_abandonable_propagates_exception():
    with pytest.raises(ValueError, match="INNER_BOOM"):
        run(pt.await_abandonable(_boom(), timeout=5.0))


def _fresh_key(tag):
    return f"t:KEY_{tag}_{time.time_ns()}"


def test_match_recent_reason_maker_window():
    pk = _fresh_key("win")
    pt.note_order_reason(pk, 10.0, "B12 |VEC_EXACT", now=time.time() - 120.0)
    assert pt.match_recent_order_reason(pk, 10.0) == "B12 |VEC_EXACT"


def test_match_recent_reason_qty_tolerance():
    pk = _fresh_key("tol")
    pt.note_order_reason(pk, 100.0, "R |VEC_EXACT")
    assert pt.match_recent_order_reason(pk, 100.5) == "R |VEC_EXACT"
    assert pt.match_recent_order_reason(pk, 110.0) is None


def test_match_recent_reason_prunes_expired():
    pk = _fresh_key("prune")
    now = time.time()
    pt.note_order_reason(pk, 10.0, "OLD", now=now - 400.0)
    pt.note_order_reason(pk, 10.0, "NEW |VEC_EXACT", now=now - 200.0)
    assert pt.match_recent_order_reason(pk, 10.0, now=now) == "NEW |VEC_EXACT"
    assert len(pt._RECENT_ORDER_REASONS[pk]) == 1


def test_match_recent_reason_lot_floor():
    pk = _fresh_key("floor")
    pt.note_order_reason(pk, 1.1, "B_KZONE |VEC_EXACT", now=time.time() - 102.0)
    assert pt.match_recent_order_reason(pk, 1.1) == "B_KZONE |VEC_EXACT"
    pk2 = _fresh_key("prefloor")
    pt.note_order_reason(pk2, 1.191319, "B_KZONE |VEC_EXACT", now=time.time() - 102.0)
    assert pt.match_recent_order_reason(pk2, 1.1) is None


def test_is_context_fresh():
    import datetime as _dt

    now = time.time()
    fresh = {"timestamp": _dt.datetime.fromtimestamp(now - 60, tz=_dt.timezone.utc).isoformat()}
    assert pt.is_context_fresh(fresh, now=now) is True
    stale = {"timestamp": _dt.datetime.fromtimestamp(now - 3600, tz=_dt.timezone.utc).isoformat()}
    assert pt.is_context_fresh(stale, now=now) is False
    assert pt.is_context_fresh({}, now=now) is False
    assert pt.is_context_fresh({"timestamp": "not-a-time"}, now=now) is False
    assert pt.is_context_fresh(None, now=now) is False
