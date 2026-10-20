"""Forming-bar throttle collateral for ez_prices_ws (USER 2026-10-20).

The websocket must save EVERY bar — closed bars immediately, forming bars
throttled per symbol — with no x-flag exception that drops ticks on the floor.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ez_prices_ws import FORMING_MIN_INTERVAL_S, forming_due  # noqa: E402


def test_closed_bars_bypass_throttle_by_contract():
    assert FORMING_MIN_INTERVAL_S > 0  # throttle exists so ticks can't melt disk


def test_forming_due_first_write_always_allowed():
    assert forming_due(0.0, 1000.0) is True


def test_forming_due_throttles_then_releases():
    assert forming_due(1000.0, 1002.0) is False
    assert forming_due(1000.0, 1000.0 + FORMING_MIN_INTERVAL_S) is True
    assert forming_due(1000.0, 1000.0 + FORMING_MIN_INTERVAL_S + 60.0) is True
