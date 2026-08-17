from __future__ import annotations

import numpy as np

from tools import run_mu_coherent_bundle_contingency as target
from tools import run_top_exit_reclaim_phase3 as phase3
from tools import vec_same_entry_exit_adapter as shared


def _fold(passed, alpha, tim=70.0, checks=None):
    base_checks = {
        "positive_side_bh": True,
        "at_least_2x_side_bh": True,
        "positive_alpha_vs_same_entry_hold": alpha > 0,
        "weighted_tim_65_80": 65 <= tim <= 80,
        "actual_top_exit": True,
        "same_entry_schedule": True,
        "mandatory_resting_reclaim": True,
        "never_flat_beyond_reclaim": True,
        "no_future_htf": True,
        "solvent": True,
        "no_capacity_breach": True,
        "minimum_equity_positive": True,
        "bounded_clamps": True,
    }
    base_checks.update(checks or {})
    return {
        "gate_pass": passed,
        "evidence": {
            "checks": base_checks,
            "alpha_vs_same_entry_hold_pp": alpha,
            "weighted_tim_pct": tim,
        },
    }


def _row(entry, family, label, fold1, fold2):
    return {
        "entry": {"label": entry},
        "exit": {"family": family, "label": label},
        "folds": [fold1, fold2],
    }


def test_selection_uses_discovery_only_and_dedupes_family():
    source = {
        "summary": {"final_opened": False},
        "all_discovery_rows": [
            _row("A", "E03", "one", _fold(True, 2), _fold(False, -3)),
            _row("A", "E03", "two", _fold(True, 4), _fold(False, -1)),
            _row("A", "E04", "three", _fold(True, 1), _fold(False, -2)),
            _row("B", "E03", "four", _fold(False, 9), _fold(False, -0.1)),
        ],
    }
    selected = target.select_neighbourhoods(source)
    assert [(r["entry"]["label"], r["exit"]["family"], r["exit"]["label"]) for r in selected] == [
        ("A", "E03", "two"),
        ("A", "E04", "three"),
    ]


def test_selection_rejects_fold2_mechanical_fault():
    source = {
        "summary": {"final_opened": False},
        "all_discovery_rows": [
            _row(
                "A",
                "E03",
                "bad",
                _fold(True, 2),
                _fold(False, -1, checks={"no_future_htf": False}),
            )
        ],
    }
    assert target.select_neighbourhoods(source) == []


def test_exit_fill_reclaim_variant_preserves_events_and_sources():
    original_book = shared.StaticExitBook(
        "x",
        np.array([0, 1, 0], dtype=np.uint8),
        np.array([np.nan, 123.0, np.nan]),
        {1: {"4h": 10}},
    )
    original = phase3.RegisteredBook(
        "E03", "x", {}, original_book, 0.5
    )
    variant = target._variant_book(original, "EXIT_FILL")
    assert np.array_equal(variant.book.events, original.book.events)
    assert np.array_equal(variant.book.references, np.zeros(3))
    assert variant.book.source_by_row == original.book.source_by_row
    assert variant.label.endswith("RECLAIM_EXIT_FILL")
