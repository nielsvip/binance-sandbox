"""Fill attribution + never-hang timeout — no network, no live orders."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import positions_truth as pt  # noqa: E402
from ez_positions_service import PositionService  # noqa: E402


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


def test_match_recent_reason_maker_window():
    now = time.time()
    ror = [(now - 120.0, 10.0, "B12 |VEC_EXACT")]
    assert PositionService._match_recent_order_reason(ror, 10.0, now) == "B12 |VEC_EXACT"


def test_match_recent_reason_qty_tolerance():
    now = time.time()
    ror = [(now - 5.0, 100.0, "R |VEC_EXACT")]
    assert PositionService._match_recent_order_reason(ror, 100.5, now) == "R |VEC_EXACT"
    assert PositionService._match_recent_order_reason(ror, 110.0, now) is None


def test_match_recent_reason_prunes_expired():
    now = time.time()
    ror = [(now - 400.0, 10.0, "OLD"), (now - 200.0, 10.0, "NEW |VEC_EXACT")]
    assert PositionService._match_recent_order_reason(ror, 10.0, now) == "NEW |VEC_EXACT"
    assert len(ror) == 1


def test_match_recent_reason_lot_floor():
    now = time.time()
    ror = [(now - 102.0, 1.1, "B_KZONE |VEC_EXACT")]
    assert PositionService._match_recent_order_reason(ror, 1.1, now) == "B_KZONE |VEC_EXACT"
    ror_pre = [(now - 102.0, 1.191319, "B_KZONE |VEC_EXACT")]
    assert PositionService._match_recent_order_reason(ror_pre, 1.1, now) is None
