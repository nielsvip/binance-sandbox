import os
import asyncio
from types import SimpleNamespace

os.environ.setdefault("TRADIER_API_LOG_DIR", "/tmp/tradier_shared_direct_tests")
os.environ.setdefault("EZ_LOG_DIR", "/tmp/tradier_shared_direct_tests")

import tradier_manage as tm


def _cfg(monkeypatch, **overrides):
    values = {
        "COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED": True,
        "ENTRY_STOCH_HHHL_DIRECT_ENABLED": False,
        "ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED": False,
        "ENTRY_STOCH_PARENT_DIRECT_ENABLED": False,
        "WT_DC_DIRECT_COMPLETED_ENABLED": False,
        "LONG_WAIT_DIRECT_ENABLED": False,
    }
    values.update(overrides)
    monkeypatch.setattr(
        tm, "_cfg", lambda name, default, *_args: values.get(name, default)
    )


def _base(ts=300):
    return {
        "_tick_ts": ts,
        "current_price": 999.0,
        "mark_price": 999.0,
        "close_15m": 999.0,
        "_completed_source_ts_5m": ts - 10,
        "_completed_close_5m": 100.5,
        "_completed_stoch_k_5m": 30.0,
        "_completed_stoch_d_5m": 20.0,
        "_completed_source_ts_15m": ts - 20,
        "_completed_close_15m": 500.0,
        "_completed_dc_low_15m": 100.0,
        "_completed_dc_low_15m_prev": 100.0,
        "_completed_stoch_k_15m": 30.0,
        "_completed_stoch_d_15m": 20.0,
        "_completed_wt1_15m": 2.0,
        "_completed_wt2_15m": 1.0,
        "_completed_source_ts_1h": ts - 30,
        "_completed_stoch_k_1h": 25.0,
        "_completed_stoch_d_1h": 10.0,
        "_completed_source_ts_4h": ts - 40,
        "_completed_stoch_k_4h": 10.0,
    }


def test_bounce_15m_uses_completed_5m_execution_price_not_selected_tf_close(monkeypatch):
    _cfg(
        monkeypatch,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED=True,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME="15m",
        ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE=.01,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY=False,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION="none",
    )
    indicators = _base()
    result = tm._shared_direct_entry_claim(
        SimpleNamespace(), "trb:TEST_LONG", indicators,
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert result is not None
    assert result["family"] == "ENTRY_BOUNCE_DONCHIAN"
    assert result["source_identities"]["5m_price"] == 290
    assert "p0.005000" in result["reason"]


def test_direct_episode_waits_for_first_executable_observation(monkeypatch):
    _cfg(
        monkeypatch,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED=True,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME="15m",
        ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE=.01,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY=False,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION="none",
    )
    manager = SimpleNamespace()
    # The episode begins while orders are unavailable. Its state advances, but
    # the claim remains pending rather than being silently consumed.
    assert tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", _base(300),
        "trb", "TEST", "LONG", allow_claim=False,
    ) is None
    claim = tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", _base(600),
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert claim and claim["family"] == "ENTRY_BOUNCE_DONCHIAN"
    # A contiguous eligible episode can be owned only once.
    assert tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", _base(900),
        "trb", "TEST", "LONG", allow_claim=True,
    ) is None


def test_long_wait_15m_uses_5m_price_and_current_not_prior_channel(monkeypatch):
    _cfg(
        monkeypatch,
        LONG_WAIT_DIRECT_ENABLED=True,
        LONG_WAIT_DIRECT_BOUNCE_TIMEFRAME="15m",
        LONG_WAIT_DIRECT_BOUNCE_DISTANCE=.008,
        LONG_WAIT_DIRECT_DEEP_K4H=35.0,
        LONG_WAIT_DIRECT_TURN_K1H=40.0,
        LONG_WAIT_DIRECT_CONFIRMATION="stoch5",
    )
    manager = SimpleNamespace()
    first = _base(300)
    # Prime the exact prior aligned-row K. No entry is allowed on this row.
    assert tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", first,
        "trb", "TEST", "LONG", allow_claim=True,
    ) is None
    second = _base(600)
    second["_completed_dc_low_15m"] = 100.0
    second["_completed_dc_low_15m_prev"] = 200.0
    result = tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", second,
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert result is not None
    assert result["family"] == "ENTRY_LONG_WAIT_ENABLED"
    assert result["source_identities"]["5m_price"] == 590
    assert "p0.005000" in result["reason"]


def test_direct_adapter_requires_master_and_never_substitutes_generic_values(monkeypatch):
    _cfg(
        monkeypatch,
        COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED=False,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED=True,
    )
    assert tm._shared_direct_entry_claim(
        SimpleNamespace(), "trb:TEST_LONG", _base(),
        "trb", "TEST", "LONG", allow_claim=True,
    ) is None

    _cfg(
        monkeypatch,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED=True,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME="15m",
        ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE=.01,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY=False,
        ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION="none",
    )
    indicators = _base()
    del indicators["_completed_close_5m"]
    manager = SimpleNamespace()
    assert tm._shared_direct_entry_claim(
        manager, "trb:TEST_LONG", indicators,
        "trb", "TEST", "LONG", allow_claim=True,
    ) is None
    last = manager._shared_direct_route_telemetry[
        "trb:TEST_LONG"
    ]["ENTRY_BOUNCE_DONCHIAN"]["last"]
    assert last["data_error"]
    assert "_completed_close_5m" in last["blockers"][0]


def test_hhhl_parent_and_wt_dc_none_cross_claim_through_live_adapter(monkeypatch):
    hhhl = _base(300)
    hhhl.update({
        "_completed_high_1h": 11.0,
        "_completed_high_1h_prev": 10.0,
        "_completed_low_1h": 9.0,
        "_completed_low_1h_prev": 8.0,
        "_completed_stoch_k_1h": 15.0,
        "_completed_stoch_k_1h_prev": 10.0,
    })
    _cfg(
        monkeypatch,
        ENTRY_STOCH_HHHL_DIRECT_ENABLED=True,
        ENTRY_STOCH_HHHL_DIRECT_TFS=("1h",),
        ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS=1,
        ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD=20.0,
    )
    claim = tm._shared_direct_entry_claim(
        SimpleNamespace(), "trb:TEST_LONG", hhhl,
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert claim and claim["reason"].startswith("STOCH_HHHL_DIRECT_")

    parent = _base(300)
    parent.update({
        "_completed_source_ts_1h_prev": 200,
        "_completed_stoch_k_1h": 30.0,
        "_completed_stoch_k_1h_prev": 20.0,
    })
    _cfg(
        monkeypatch,
        ENTRY_STOCH_PARENT_DIRECT_ENABLED=True,
        ENTRY_STOCH_PARENT_DIRECT_FAMILY="ENTRY_1H_TURN_UP",
        ENTRY_STOCH_PARENT_DIRECT_THRESHOLD=40.0,
        ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION="rising-vs-prior",
    )
    claim = tm._shared_direct_entry_claim(
        SimpleNamespace(), "trb:TEST_LONG", parent,
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert claim and claim["reason"].startswith("STOCH_PARENT_DIRECT_")

    wtdc = _base(300)
    for tf in ("D", "4h", "1h"):
        wtdc[f"_completed_source_ts_{tf}"] = {"D": 100, "4h": 200, "1h": 270}[tf]
        wtdc[f"_completed_wt1_{tf}"] = 2.0
        wtdc[f"_completed_wt2_{tf}"] = 1.0
    wtdc.update({
        "_completed_dc_low_1h_prev": 0.1,
        "_completed_dc_high_1h_prev": 100.1,
        "_completed_close_1h": 25.1,
        "_completed_wt_cross_1h": "NONE",
        "_completed_stoch_k_5m": 80.0,
    })
    _cfg(
        monkeypatch,
        WT_DC_DIRECT_COMPLETED_ENABLED=True,
        WT_DC_DIRECT_THRESHOLD=45.0,
        WT_DC_DIRECT_HTF_GATE="none",
        WT_DC_DIRECT_HTF_ALIGN_REQUIRED=0,
        WT_DC_DIRECT_COMBINED_STOCH_GATE=100.0,
    )
    claim = tm._shared_direct_entry_claim(
        SimpleNamespace(), "trb:TEST_LONG", wtdc,
        "trb", "TEST", "LONG", allow_claim=True,
    )
    assert claim and claim["reason"].startswith("WT_DC_DIRECT_score60.0")


def _bottom_b_snapshot(observation, arm, confirm):
    result = {"_tick_ts": observation}
    for tf, source, values in (
        ("1h", arm[0], arm[1:]),
        ("5m", confirm[0], confirm[1:]),
    ):
        result.update({
            f"_completed_source_ts_{tf}": source,
            f"_completed_high_{tf}": values[0],
            f"_completed_low_{tf}": values[1],
            f"_completed_close_{tf}": values[2],
            f"_completed_wt1_{tf}": values[3],
            f"_completed_atr_{tf}": 1.0,
        })
    return result


def test_bottom_b_exits_through_real_evaluate_stop_before_full_recipe_hold(monkeypatch):
    _cfg(
        monkeypatch,
        BOTTOM_B_DELAYED_LOWER_TOP_ENABLED=True,
        BOTTOM_B_DELAYED_LOWER_TOP_ARM_TF="1h",
        BOTTOM_B_DELAYED_LOWER_TOP_CONFIRM_TF="5m",
        BOTTOM_B_DELAYED_LOWER_TOP_REBOUND_ATR=.25,
        BOTTOM_B_DELAYED_LOWER_TOP_PREBREAK_LOOKBACK=2,
        BOTTOM_B_DELAYED_LOWER_TOP_MAX_WAIT_1H=3,
        BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_MODE="PRICE_ONLY",
        BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_BARS=1,
        BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_MODE="PREV_BAR",
        BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_THRESHOLD=0.0,
        BOTTOM_A_PROTECTIVE_TRAIL_ENABLED=False,
        FULL_RECIPE_ONLY_ENABLED=True,
    )
    manager = SimpleNamespace()
    strategy = object.__new__(tm.StockStrategy)
    strategy.trade_manager = manager
    position = SimpleNamespace(
        position_side="LONG", positionAmt=5.0,
        position_key="trb:TEST_LONG",
    )
    tm.current_account.set("trb")
    rows = (
        _bottom_b_snapshot(150, (100, 10, 8, 9, 5), (110, 8, 7, 7.5, 4)),
        _bottom_b_snapshot(250, (200, 11, 9, 10, 6), (210, 8, 7, 7.5, 4)),
        _bottom_b_snapshot(350, (300, 10, 7, 7.5, 4), (310, 8, 7, 7.5, 4)),
        _bottom_b_snapshot(360, (300, 10, 7, 7.5, 4), (320, 9, 7.5, 8, 5)),
        _bottom_b_snapshot(370, (300, 10, 7, 7.5, 4), (330, 8.5, 7, 7.4, 4)),
    )
    async def exercise():
        for indicators in rows[:-1]:
            fired, reason, quantity = await strategy.evaluate_stop(
                "TEST", position, indicators
            )
            assert (fired, quantity) == (False, 0)
            assert reason == "FULL_RECIPE_ONLY_HOLD_EXIT"
        return await strategy.evaluate_stop("TEST", position, rows[-1])

    fired, reason, quantity = asyncio.run(exercise())
    assert fired and quantity == 5.0
    assert reason.startswith("BOTTOM_B_DELAYED_LOWER_TOP__")
    assert "arm_src300" in reason
    assert reason.endswith("MANDATORY_REENTRY")
