"""Stale-flatten backstop: owned keys paper-flat + live-holds with no act flatten after N bars."""
from live_twins import vec_exact as vx


def _reset():
    vx._STALE_FLAT_FIRST.clear()
    vx._EMITTED.clear()


def test_first_sighting_arms_not_fires():
    _reset()
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0) is False


def test_due_after_stale_bars():
    _reset()
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 8 * 900.0, 8.0) is True


def test_not_due_before_window():
    _reset()
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 7 * 900.0, 8.0) is False


def test_paper_holds_resets():
    _reset()
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", True, 2947.0, 1791612000.0 + 8 * 900.0, 8.0) is False
    assert "ang:PUMPUSDT_SHORT" not in vx._STALE_FLAT_FIRST


def test_live_flat_resets():
    _reset()
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 0.0, 1791612000.0 + 8 * 900.0, 8.0) is False


def test_recent_act_blocks():
    _reset()
    vx._EMITTED.add(("PUMPUSDT_SHORT", 1791612000.0 + 7 * 900.0, 0))
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 8 * 900.0, 8.0) is False


def test_due_rearms_one_bar():
    _reset()
    vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0, 8.0)
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 8 * 900.0, 8.0) is True
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 8 * 900.0 + 100.0, 8.0) is False
    assert vx.stale_flat_due("ang:PUMPUSDT_SHORT", "PUMPUSDT_SHORT", False, 2947.0, 1791612000.0 + 9 * 900.0 + 1.0, 8.0) is True
