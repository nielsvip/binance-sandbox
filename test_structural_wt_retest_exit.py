import pytest
from pathlib import Path

from vec_paths.structural_wt_retest_exit import (
    CompletedBar,
    StructuralWtParams,
    StructuralWtRetestExitBook,
    next_rth_fill_price,
)


def _bar(
    n,
    high,
    low,
    close,
    wt1,
    atr=2.0,
    observed_offset=60,
    timeframe="1h",
):
    return CompletedBar(
        timeframe=timeframe,
        source_ts=n * 100,
        observed_ts=n * 100 + observed_offset,
        high=high,
        low=low,
        close=close,
        wt1=wt1,
        atr=atr,
    )


def test_long_damage_arms_but_only_distinct_lower_price_and_wt_top_exits():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(damage_atr=0.5, rebound_atr=0.5)
    )
    confirm_warmup = [
        _bar(1, 110, 100, 108, 60),
        _bar(2, 112, 102, 110, 65),
        _bar(3, 114, 104, 112, 70),
    ]
    for bar in confirm_warmup:
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
    arm_bars = [
        _bar(4, 110, 100, 108, 60, timeframe="4h"),
        _bar(8, 112, 102, 110, 65, timeframe="4h"),
        _bar(12, 114, 104, 112, 70, timeframe="4h"),
        _bar(16, 111, 99, 100, 35, timeframe="4h"),
    ]
    arm_signals = [
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
        for bar in arm_bars
    ]
    assert all(signal is None for signal in arm_signals)
    confirm_bars = [
        _bar(17, 105, 98, 103, 40),
        _bar(18, 109, 100, 108, 55),
        _bar(19, 107, 99, 104, 50),
    ]
    signals = [
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
        for bar in confirm_bars
    ]
    assert signals[0] is None
    assert signals[1] is None
    signal = signals[2]
    assert signal is not None
    assert signal.position_side == "LONG"
    assert signal.price_anchor == 114
    assert signal.wt_anchor == 70
    assert signal.retest_price == 109
    assert signal.retest_wt1 == 55
    assert "MANDATORY_REENTRY" in signal.reason


def test_short_is_exact_mirror_and_state_is_side_isolated():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(damage_atr=0.5, rebound_atr=0.5)
    )
    confirm_warmup = [
        _bar(1, 110, 100, 102, -60),
        _bar(2, 112, 98, 100, -65),
        _bar(3, 114, 96, 98, -70),
    ]
    for bar in confirm_warmup:
        book.update(symbol="MU", position_side="SHORT", active=True, bar=bar)
    arm_bars = [
        _bar(4, 110, 100, 102, -60, timeframe="4h"),
        _bar(8, 112, 98, 100, -65, timeframe="4h"),
        _bar(12, 114, 96, 98, -70, timeframe="4h"),
        _bar(16, 115, 99, 112, -35, timeframe="4h"),
    ]
    for bar in arm_bars:
        book.update(symbol="MU", position_side="SHORT", active=True, bar=bar)
    bars = [
        _bar(17, 116, 103, 108, -40),
        _bar(18, 114, 100, 102, -55),
        _bar(19, 115, 102, 108, -50),
    ]
    short_signals = [
        book.update(symbol="MU", position_side="SHORT", active=True, bar=bar)
        for bar in bars
    ]
    assert short_signals[-1] is not None
    assert short_signals[-1].position_side == "SHORT"
    assert short_signals[-1].retest_price == 100
    assert short_signals[-1].retest_wt1 == -55
    # The LONG state has never been armed by SHORT observations.
    assert book.state_snapshot("MU", "LONG")["phase"] == "TREND"


def test_flat_state_cancels_old_arm_but_keeps_completed_history():
    book = StructuralWtRetestExitBook()
    for bar in (
        _bar(1, 110, 100, 108, 60),
        _bar(2, 112, 102, 110, 65),
        _bar(3, 114, 104, 112, 70),
    ):
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
    for bar in (
        _bar(4, 110, 100, 108, 60, timeframe="4h"),
        _bar(8, 112, 102, 110, 65, timeframe="4h"),
        _bar(12, 114, 104, 112, 70, timeframe="4h"),
        _bar(16, 111, 99, 100, 35, timeframe="4h"),
    ):
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
    assert book.state_snapshot("MU", "LONG")["phase"] == "ARMED"
    book.update(
        symbol="MU",
        position_side="LONG",
        active=False,
        bar=_bar(17, 105, 98, 103, 40),
    )
    assert book.state_snapshot("MU", "LONG")["phase"] == "TREND"


def test_future_htf_source_and_same_timestamp_reprocessing_are_fail_closed():
    book = StructuralWtRetestExitBook()
    with pytest.raises(ValueError, match="future"):
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=CompletedBar("1h", 200, 100, 10, 9, 9.5, 1, 1),
        )
    bar = _bar(1, 10, 9, 9.5, 1)
    assert (
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
        is None
    )
    assert (
        book.update(symbol="MU", position_side="LONG", active=True, bar=bar)
        is None
    )


def test_fill_is_adverse_next_rth_open_and_side_specific():
    assert next_rth_fill_price(100, "LONG", 2) == pytest.approx(99.98)
    assert next_rth_fill_price(100, "SHORT", 2) == pytest.approx(100.02)


def test_exact_engine_route_uses_same_parameterized_4h_arm_1h_confirm_core():
    source = Path("backtest_v8_engine.py").read_text()
    assert "--research-struct-wt-exit" in source
    assert "StructuralWtRetestExitBook" in source
    assert "_struct_wt_params_t.arm_tf" in source
    assert "_struct_wt_params_t.confirm_tf" in source
    assert "research_fill_rth_latency" in source
    assert "V8_RESEARCH_STRUCT_WT_RETEST_EXIT" in source
