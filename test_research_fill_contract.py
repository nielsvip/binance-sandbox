from __future__ import annotations

import pytest

from tools.research_fill_contract import adverse_fill_price


@pytest.mark.parametrize(
    ("side", "opening", "expected"),
    [
        ("LONG", True, 100.02),
        ("LONG", False, 99.98),
        ("SHORT", True, 99.98),
        ("SHORT", False, 100.02),
    ],
)
def test_adverse_fill_direction(side: str, opening: bool, expected: float) -> None:
    assert adverse_fill_price(
        100.0,
        position_side=side,
        opening=opening,
        slippage_rate=0.0002,
    ) == pytest.approx(expected)


def test_adverse_slippage_never_improves_execution() -> None:
    raw = 100.0
    slip = 0.001
    assert adverse_fill_price(
        raw, position_side="LONG", opening=True, slippage_rate=slip
    ) > raw
    assert adverse_fill_price(
        raw, position_side="LONG", opening=False, slippage_rate=slip
    ) < raw
    assert adverse_fill_price(
        raw, position_side="SHORT", opening=True, slippage_rate=slip
    ) < raw
    assert adverse_fill_price(
        raw, position_side="SHORT", opening=False, slippage_rate=slip
    ) > raw

