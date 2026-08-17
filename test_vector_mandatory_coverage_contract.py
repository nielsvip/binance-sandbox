"""Static contract for Bible §0.1's mandatory V8/vector shortlist.

This test intentionally launches no backtest and reads no NPZ.  It prevents a
new or edited registered runner from losing the fail-closed coverage-claim
gate while allowing narrow research receipts to remain honestly partial.
"""
from pathlib import Path

import pytest

from vector_mandatory_coverage import (
    MANDATORY_SHORTLIST,
    REGISTERED_V8_VECTOR_RUNNERS,
    audit_registered_runner_wiring,
    validate_comprehensive_receipt,
)


def test_mandatory_shortlist_is_the_complete_bible_queue():
    assert set(MANDATORY_SHORTLIST) == {
        "STDEV_LADDER_1_TO_10", "DONCHIAN_E02", "DIVERGENCE_RETEST_E05",
        "MTF_ATR_TRAIL", "PEAK_GIVEBACK", "PROTECTIVE_TRAIL",
        "DELAYED_LOWER_TOP_EXIT", "DELAYED_EMERGENCY_EXIT", "DIRECT_WT15_CROSS_EXIT",
        "LR_BAND_HARVEST_VARIANTS", "WT_FORCE_OPEN_FRESH_CROSS",
        "LR_ENTRY_PRIORITY_THRESHOLD", "SMA200_EMA_DISTANCE_SLOPE",
        "OSCILLATOR_STYLE_FILTERS", "BB_STDEV_BREAKOUT_RETEST",
        "MARKET_QUALITY_REGIME_FILTERS", "MTF_ARROW_HTF_ALIGNMENT",
        "DELTA_ENTRY_GATES", "RZ_KZONE_ZONE_ENTRY", "STRUCTURAL_RANGE_SHIFT_EXITS",
        "RZ_TWO_PHASE_R3_HTF_FLIP", "DYNAMIC_SCORE_COUNTER_EXITS", "DELTA_EXITS",
        "FROZEN_BB_DC_STOPS", "MI_EXHAUSTION_EXITS", "RSI_STOCH_CROSS_EXITS",
        "SENTIMENT_GAIN_EROSION_EXITS", "WT_D_BOUNCE_EXITS", "NO_LOSS_STOP_PACKS",
        "WT_EXIT_WITH_BREAKOUT_BOUNCE_REENTRY",
    }
    assert len(REGISTERED_V8_VECTOR_RUNNERS) >= 17


def test_every_registered_v8_vector_runner_has_the_fail_closed_claim_gate():
    root = Path(__file__).resolve().parent
    assert audit_registered_runner_wiring(root) == []


def test_partial_or_disconnected_receipt_cannot_support_comprehensive_claim(tmp_path):
    receipt = tmp_path / "partial.json"
    receipt.write_text('{"schema":"v8-vector-mandatory-coverage-v1","coverage_status":"PARTIAL_NOT_COMPREHENSIVE"}')
    with pytest.raises(ValueError, match="missing V8_VECTOR_MANDATORY_SHORTLIST_V1"):
        validate_comprehensive_receipt(receipt)
