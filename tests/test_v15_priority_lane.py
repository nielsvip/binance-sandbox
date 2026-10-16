"""Task 05.2 priority lane (USER 2026-10-15): critical-path eval fetch goes to a 1-worker
priority pool instead of the shared FIFO. V15_PRIORITY_LANE=0 disables, default 0."""
import v15_pilot as P


def test_default_off(monkeypatch):
    monkeypatch.delenv("V15_PRIORITY_LANE", raising=False)
    assert P._priority_lane_on() is False


def test_env_enables(monkeypatch):
    monkeypatch.setenv("V15_PRIORITY_LANE", "1")
    assert P._priority_lane_on() is True
    monkeypatch.setenv("V15_PRIORITY_LANE", "0")
    assert P._priority_lane_on() is False


def test_router_none_when_lane_off():
    assert P._get_critical_pool("ck", None, {}, {}, []) is None
    assert P._get_critical_pool("ck", object(), {}, {}, []) is not None


def test_router_none_when_cached_or_inflight():
    pool = object()
    assert P._get_critical_pool("ck", pool, {"ck": 1}, {}, []) is None
    assert P._get_critical_pool("ck", pool, {}, {"ck": (1, 2)}, []) is None


def test_router_none_when_batched():
    pool = object()
    assert P._get_critical_pool("ck", pool, {}, {}, [("ck", {}), ("other", {})]) is None
    assert P._get_critical_pool("ck", pool, {}, {}, [("other", {})]) is pool


def test_router_true_miss_goes_to_prio():
    pool = object()
    assert P._get_critical_pool("ck", pool, {"other": 1}, {"x": 1}, []) is pool
    assert P._get_critical_pool("ck", pool, {}, {}, None) is pool
