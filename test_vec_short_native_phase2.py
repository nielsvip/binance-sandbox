from __future__ import annotations

from tools.vec_short_native_phase2 import (
    CAPACITY_USD,
    COVER_PROFILES,
    ENTRY_PROFILES,
    discovery_gate,
    profiles,
)


def _fold(capital: float, floor: float, *, exits: int = 3) -> dict:
    return {
        "capital_return_pct": capital,
        "opportunity_benchmark_pct": floor,
        "beats_opportunity_benchmark": capital > floor,
        "insolvent": False,
        "max_drawdown_account_pct": 5.0,
        "peak_post_fill_notional_usd": CAPACITY_USD,
        "technical_exits": exits,
    }


def test_books_are_preregistered_separately() -> None:
    assert len(profiles("CORRECTION")) == 24
    assert len(profiles("BEAR")) == 24
    assert all(x.book in {"CORRECTION", "BEAR"} for x in ENTRY_PROFILES)
    assert all(x.book in {"CORRECTION", "BEAR"} for x in COVER_PROFILES)
    assert not {
        x.label for x in ENTRY_PROFILES if x.book == "CORRECTION"
    } & {
        x.label for x in ENTRY_PROFILES if x.book == "BEAR"
    }


def test_discovery_must_beat_short_bh_or_cash_each_fold() -> None:
    passed, _, failures = discovery_gate({
        "D1": _fold(12, 5),
        "D2": _fold(3, 0),
    })
    assert passed and failures == []
    passed, _, failures = discovery_gate({
        "D1": _fold(12, 5),
        "D2": _fold(3, 20),
    })
    assert not passed
    assert "D2:OPPORTUNITY" in failures


def test_discovery_fails_capacity_activity_or_solvency() -> None:
    d1 = _fold(12, 5)
    d1["peak_post_fill_notional_usd"] = CAPACITY_USD + 1
    d2 = _fold(3, 0, exits=1)
    d2["insolvent"] = True
    passed, _, failures = discovery_gate({"D1": d1, "D2": d2})
    assert not passed
    assert set(failures) >= {"D1:CAPACITY", "D2:ACTIVITY", "D2:SOLVENCY"}
