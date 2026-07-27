import argparse
from pathlib import Path

import numpy as np

from tools import run_short_metric_polarity_audit as audit


def _fold(capital, benchmark, exits=2):
    return {
        "capital_return_pct": capital,
        "opportunity_benchmark_pct": benchmark,
        "technical_exits": exits,
        "insolvent": False,
        "minimum_account_equity_usd": 9_000.0,
        "peak_post_fill_notional_usd": 8_000.0,
        "max_drawdown_account_pct": 5.0,
    }


def test_inventory_uses_only_declared_classes_and_covers_core_contract():
    rows = audit.metric_inventory()
    assert rows
    assert {r["classification"] for r in rows} == {
        "SIGN_INVERTED", "THRESHOLD_COMPLEMENTED", "SIDE_NEUTRAL", "SHORT_NATIVE"
    }
    metrics = {r["metric"] for r in rows}
    assert {"SHORT_open_fill", "SHORT_cover_fill", "SHORT_pnl", "SHORT_BH"} <= metrics
    assert {"price_velocity_atr_1h", "LH_LL_1h", "bull_structural_reclaim"} <= metrics
    assert len({(r["family"], r["metric"]) for r in rows}) == len(rows)


def test_discovery_gate_uses_cash_floor_and_both_folds():
    passed, failures, _ = audit.discovery_gate({
        "D1": _fold(3.0, 0.0),
        "D2": _fold(8.0, 5.0),
    })
    assert passed and failures == []
    passed, failures, _ = audit.discovery_gate({
        "D1": _fold(-0.1, 0.0),
        "D2": _fold(8.0, 5.0),
    })
    assert not passed
    assert "D1:OPPORTUNITY" in failures


def test_profile_grid_is_small_coherent_and_side_native():
    assert len(audit.frozen_profiles("CORRECTION")) == 4
    assert len(audit.frozen_profiles("BEAR")) == 4
    for book in ("CORRECTION", "BEAR"):
        for entry, cover in audit.frozen_profiles(book):
            assert entry.book == book
            assert cover.book == book


def test_failed_discovery_contract_seals_final_source():
    source = Path(audit.__file__).read_text()
    assert 'if selected["passed"]:' in source
    final_branch = source.split('if selected["passed"]:', 1)[1]
    before_status = final_branch.split("exact_eligible =", 1)[0]
    assert "native.simulate(" in before_status
    prereg_branch = source.split("(out / \"PREREGISTRATION.json\").write_text", 1)[0]
    assert "native.simulate(" not in prereg_branch


def test_short_fill_and_benchmark_polarity_regression():
    from tools.research_fill_contract import adverse_fill_price

    assert adverse_fill_price(
        100, position_side="SHORT", opening=True, slippage_rate=.001
    ) == 99.9
    assert adverse_fill_price(
        100, position_side="SHORT", opening=False, slippage_rate=.001
    ) == 100.1
    start, end = 100.0, 120.0
    short_bh = (start - end) / start * 100
    assert short_bh == -20.0
    assert max(short_bh, 0.0) == 0.0
