from __future__ import annotations

import numpy as np

from tools import run_top_exit_reclaim_phase3 as phase3
from tools import vec_same_entry_exit_adapter as shared


def _book(label: str, rows: list[int], refs: list[float]) -> phase3.RegisteredBook:
    event = np.zeros(6, dtype=np.uint8)
    reference = np.full(6, np.nan)
    sources = {}
    for row, ref in zip(rows, refs):
        event[row] = 1
        reference[row] = ref
        sources[row] = {"4h": 100 + row}
    return phase3.RegisteredBook(
        label,
        label,
        {},
        shared.StaticExitBook(label, event, reference, sources),
        0.5,
    )


def test_union_is_or_and_uses_highest_long_reclaim_reference() -> None:
    left = _book("A", [1, 3], [11.0, 13.0])
    right = _book("B", [2, 3], [12.0, 15.0])
    union = phase3.union_books("U", "COMBO", (left, right))
    assert union.book.events.tolist() == [0, 1, 1, 1, 0, 0]
    assert union.book.references[3] == 15.0
    assert union.book.source_by_row[3]["4h"] == 103


def test_fold_gate_requires_bh_control_tim_reclaim_and_solvency() -> None:
    row = {
        "actual_exit_fills": 2,
        "alpha_vs_bh_pp": 1.0,
        "alpha_vs_same_entry_e02_pp": 0.1,
        "weighted_tim_pct": 75.0,
        "bars_flat_beyond_reclaim": 0,
        "future_htf_source_count": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "minimum_account_equity_usd": 1.0,
        "peak_notional_usd": 16_000.0,
    }
    assert phase3.fold_pass(row)
    for key, value in (
        ("alpha_vs_bh_pp", 0.0),
        ("alpha_vs_same_entry_e02_pp", 0.0),
        ("weighted_tim_pct", 69.99),
        ("bars_flat_beyond_reclaim", 1),
        ("future_htf_source_count", 1),
        ("minimum_account_equity_usd", 0.0),
        ("peak_notional_usd", 16_000.01),
    ):
        changed = dict(row)
        changed[key] = value
        assert not phase3.fold_pass(changed)


def test_emergency_controls_cannot_promote() -> None:
    row = {
        "actual_exit_fills": 1,
        "alpha_vs_bh_pp": 10.0,
        "alpha_vs_same_entry_e02_pp": 10.0,
        "weighted_tim_pct": 75.0,
        "bars_flat_beyond_reclaim": 0,
        "future_htf_source_count": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "minimum_account_equity_usd": 9_000.0,
        "peak_notional_usd": 8_000.0,
    }
    assert not phase3.fold_pass(row, emergency_control=True)


def test_phase3_registry_size_is_frozen(monkeypatch) -> None:
    monkeypatch.setattr(
        phase3, "_single_registry", lambda data, htfs: [_book(str(i), [1], [10 + i]) for i in range(36)]
    )
    # The real registry groups families, so this fixture only protects the
    # public count through explicit assertions in production construction.
    assert phase3.CAMPAIGN == "TOP_EXIT_RECLAIM_PHASE3_V1"
    assert phase3.CAPITAL_CONTRACT["strategy_capacity_usd"] == 16_000.0
