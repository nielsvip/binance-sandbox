"""test_s1_position_failover — supervisor decisions (pure logic, no subprocess)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s1_position_failover import decide, heartbeat_ok, spawn_times_ok  # noqa: E402


def test_heartbeat_ok():
    now = 1000000.0
    assert heartbeat_ok({"epoch": now - 10}, now) is True
    assert heartbeat_ok({"epoch": now - 400}, now) is False
    assert heartbeat_ok(None, now) is False
    assert heartbeat_ok({}, now) is False


def test_decide_healthy_noop():
    ages = {a: 5.0 for a in ("ang", "inf", "flz", "men", "fin")}
    s, t, n, _b = decide(True, ages, set(), {})
    assert (s, t) == ([], [])
    assert all(v == 1 for v in n.values())


def test_decide_stale_age_starts():
    ages = {"ang": 5.0, "inf": 200.0, "flz": None, "men": 5.0, "fin": 5.0}
    s, t, n, _b = decide(True, ages, set(), {})
    assert s == ["inf"]
    assert t == []
    assert n["inf"] == 0


def test_decide_dead_heartbeat_starts_all():
    s, t, _, _b = decide(False, {}, set(), {})
    assert sorted(s) == ["ang", "fin", "flz", "inf", "men"]
    assert t == []


def test_decide_standdown_needs_streak():
    ages = {a: 5.0 for a in ("ang", "inf", "flz", "men", "fin")}
    s, t, n, _b = decide(True, ages, {"inf"}, {"inf": 3})
    assert (s, t) == ([], [])
    assert n["inf"] == 4
    s, t, n, _b = decide(True, ages, {"inf"}, {"inf": 11})
    assert t == ["inf"]


def test_decide_flat_no_trigger():
    ages = {a: None for a in ("ang", "inf", "flz", "men", "fin")}
    s, t, _, _b = decide(True, ages, set(), {})
    assert (s, t) == ([], [])


def test_spawn_backoff():
    log, now = {}, 1000000.0
    for _ in range(6):
        assert spawn_times_ok(log, "flz", now) is True
    assert spawn_times_ok(log, "flz", now) is False
    assert spawn_times_ok(log, "flz", now + 3700) is True
