import pytest
import numpy as np
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
        StructuralWtParams(
            rebound_atr=0.5,
            prebreak_lookback=3,
            max_wait_1h=12,
        )
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


def test_v2_atr_arm_rejects_weak_break_then_arms_on_qualified_break():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(
            arm_tf="1h",
            confirm_tf="15m",
            prebreak_lookback=3,
            arm_break_mode="ATR",
            arm_break_threshold=1.0,
        )
    )
    for bar in (
        _bar(1, 110, 100, 108, 60),
        _bar(2, 112, 102, 110, 65),
        _bar(3, 114, 104, 112, 70),
    ):
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=bar,
            role="ARM",
        )
    # This breaks the previous low but not the previous close by a full ATR.
    book.update(
        symbol="MU",
        position_side="LONG",
        active=True,
        bar=_bar(4, 113, 103, 111, 50),
        role="ARM",
    )
    assert book.state_snapshot("MU", "LONG")["phase"] == "TREND"
    book.update(
        symbol="MU",
        position_side="LONG",
        active=True,
        bar=_bar(5, 111, 101, 108.5, 35),
        role="ARM",
    )
    assert book.state_snapshot("MU", "LONG")["phase"] == "WAIT_REBOUND"


def test_short_is_exact_mirror_and_state_is_side_isolated():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(
            rebound_atr=0.5,
            prebreak_lookback=3,
            max_wait_1h=12,
        )
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
        _bar(16, 116, 99, 115, -35, timeframe="4h"),
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


def test_same_timeframe_arm_and_confirm_use_distinct_roles():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(
            arm_tf="1h",
            confirm_tf="1h",
            rebound_atr=0.5,
            prebreak_lookback=3,
            max_wait_1h=12,
        )
    )
    history = [
        _bar(1, 110, 100, 108, 60),
        _bar(2, 112, 102, 110, 65),
        _bar(3, 114, 104, 112, 70),
    ]
    for bar in history:
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=bar,
            role="ARM",
        )
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=bar,
            role="CONFIRM",
        )
    arm = _bar(4, 111, 99, 100, 35)
    assert (
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=arm,
            role="ARM",
        )
        is None
    )
    assert (
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=arm,
            role="CONFIRM",
        )
        is None
    )
    rebound = _bar(5, 109, 100, 108, 55)
    adverse = _bar(6, 107, 99, 104, 50)
    assert (
        book.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=rebound,
            role="CONFIRM",
        )
        is None
    )
    signal = book.update(
        symbol="MU",
        position_side="LONG",
        active=True,
        bar=adverse,
        role="CONFIRM",
    )
    assert signal is not None
    assert signal.retest_price == 109
    assert signal.retest_wt1 == 55


def test_flat_state_cancels_old_arm_but_keeps_completed_history():
    book = StructuralWtRetestExitBook(
        StructuralWtParams(prebreak_lookback=3)
    )
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
    assert book.state_snapshot("MU", "LONG")["phase"] == "WAIT_REBOUND"
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


def test_exact_state_machine_matches_authoritative_vector_event_timestamp():
    from tools.vec_structural_wt_rebound_experiment import (
        structural_wt_rebound_signal,
    )
    from tools.vec_top_exit_campaign import HTFData

    arm_source = np.array([400, 800, 1200, 1600], dtype=np.int64)
    arm_high = np.array([110, 112, 114, 111], dtype=float)
    arm_low = np.array([100, 102, 104, 99], dtype=float)
    arm_close = np.array([108, 110, 112, 100], dtype=float)
    arm_wt = np.array([60, 65, 70, 35], dtype=float)
    arm_atr = np.full(4, 2.0)
    arm_h = HTFData(
        tf="4h",
        event_index=np.arange(4),
        source_ts=arm_source,
        open=arm_close,
        high=arm_high,
        low=arm_low,
        close=arm_close,
        rsi=np.full(4, 50.0),
        atr=arm_atr,
    )

    trigger_source = np.arange(100, 2000, 100, dtype=np.int64)
    trigger_high = np.full(19, 104.0)
    trigger_low = np.full(19, 101.0)
    trigger_close = np.full(19, 103.0)
    trigger_wt = np.full(19, 40.0)
    # Same-source 1h bar initializes damage; the next bar rebounds; only the
    # distinct later adverse price/WT bar may emit the exit.
    trigger_high[15:18] = [105, 109, 107]
    trigger_low[15:18] = [98, 100, 99]
    trigger_close[15:18] = [103, 108, 104]
    trigger_wt[15:18] = [40, 55, 50]
    trigger_h = HTFData(
        tf="1h",
        event_index=np.arange(19),
        source_ts=trigger_source,
        open=trigger_close,
        high=trigger_high,
        low=trigger_low,
        close=trigger_close,
        rsi=np.full(19, 50.0),
        atr=np.full(19, 2.0),
    )
    vec_event, _, _ = structural_wt_rebound_signal(
        arm_h,
        trigger_h,
        arm_wt,
        trigger_wt,
        1,
        rebound_atr=0.5,
        prebreak_lookback=3,
        max_wait_1h=12,
    )
    assert np.flatnonzero(vec_event).tolist() == [17]

    exact = StructuralWtRetestExitBook(
        StructuralWtParams(
            rebound_atr=0.5,
            prebreak_lookback=3,
            max_wait_1h=12,
        )
    )
    arm_cursor = 0
    exact_signal_ts = []
    for j, source_ts in enumerate(trigger_source):
        while (
            arm_cursor < len(arm_source)
            and arm_source[arm_cursor] <= source_ts
        ):
            exact.update(
                symbol="MU",
                position_side="LONG",
                active=True,
                bar=CompletedBar(
                    "4h",
                    int(arm_source[arm_cursor]),
                    int(source_ts),
                    float(arm_high[arm_cursor]),
                    float(arm_low[arm_cursor]),
                    float(arm_close[arm_cursor]),
                    float(arm_wt[arm_cursor]),
                    float(arm_atr[arm_cursor]),
                ),
            )
            arm_cursor += 1
        signal = exact.update(
            symbol="MU",
            position_side="LONG",
            active=True,
            bar=CompletedBar(
                "1h",
                int(source_ts),
                int(source_ts),
                float(trigger_high[j]),
                float(trigger_low[j]),
                float(trigger_close[j]),
                float(trigger_wt[j]),
                2.0,
            ),
        )
        if signal is not None:
            exact_signal_ts.append(signal.source_ts)
    assert exact_signal_ts == trigger_source[np.flatnonzero(vec_event)].tolist()
