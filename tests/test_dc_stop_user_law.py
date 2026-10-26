"""Pins the current stop law: frozen DC15 and anytime DC4h only."""
from pathlib import Path
from types import SimpleNamespace as NS
import asyncio

ROOT = Path(__file__).resolve().parents[1]


def test_tradier_rolling_1h_15m_stops_are_retired_but_dc15_order_stays_pinned():
    src = (ROOT / "tradier_manage.py").read_text()
    start = src.index("async def process_position(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    block_start = body.index("UNCONDITIONAL DC STOP BOTH SIDES")
    block_end = body.index("NON-TRADEABLE", block_start)
    dc_block = body[block_start:block_end]
    assert "DC_HARD_STOP_1H_ENABLED" not in dc_block
    assert "DC_HARD_STOP_15M_ENABLED" not in dc_block
    assert "dc_low_1h" not in dc_block and "dc_high_1h" not in dc_block
    assert "dc_low_15m" not in dc_block and "dc_high_15m" not in dc_block
    assert "_uh_tol = _udcs.DEFAULT_BUFFER_PCT" in dc_block
    assert 'EXCHANGE_RESTING_STOPS_TR = {"DC_HARD_STOP_15M_ENABLED"}' in src
    cfg = (ROOT / "config_tradier.py").read_text()
    assert "DC_HARD_STOP_1H_ENABLED: bool = False" in cfg
    assert "DC_HARD_STOP_15M_ENABLED: bool = False" in cfg


def test_tradier_dc4h_stop_uses_exact_quarter_percent_threshold(monkeypatch):
    import tradier_manage as TM

    calls = []

    async def queue(*args, **kwargs):
        calls.append((args, kwargs))
        return "QUEUED"

    monkeypatch.setattr(TM, "queue_trade_action", queue)
    monkeypatch.setattr(TM, "dc_hardstop_cooldown_record", lambda *a, **k: None)
    monkeypatch.setattr(
        TM,
        "_cfg",
        lambda key, default=None, *a, **k: False if key == "VIGILANCE_GUARD_ENABLED" else default,
    )
    TM._PRE_TWIN_STOP_FIRED_TS_TRADIER.clear()
    ind = {"dc_low_4h": 100.0, "dc_high_4h": 120.0}
    tm = NS(strategy=NS(parse_market_data=lambda raw: dict(raw)))
    position = NS(positionAmt=1.0, gain=-1.0)

    no_touch = asyncio.run(
        TM._pre_twin_hard_stops("trb", "trb:TEST_LONG_A", "TEST", "LONG", None, tm, ind, position, 99.80)
    )
    assert no_touch is False and calls == []
    TM._PRE_TWIN_STOP_FIRED_TS_TRADIER.clear()
    touch = asyncio.run(
        TM._pre_twin_hard_stops("trb", "trb:TEST_LONG_B", "TEST", "LONG", None, tm, ind, position, 99.75)
    )
    assert touch is True and len(calls) == 1
    assert "ULTIMATE_DC_4h_HARD_STOP_LONG" in calls[0][0][4]


def test_final_exit_law_fee_floor_and_mandatory_reason_allowlist():
    from vec_decisions.net_exit_gate import estimated_net_gain_positive, mandatory_loss_stop_reason

    assert estimated_net_gain_positive(0.081, 0.08)
    assert not estimated_net_gain_positive(0.08, 0.08)
    assert not estimated_net_gain_positive(None, 0.08)
    assert mandatory_loss_stop_reason("FIXED_STOP_LONG_px100_lvl99_g-1.0")
    assert mandatory_loss_stop_reason("FIXED_STOP_SHORT_REFdc_high.15m.b0.25")
    assert not mandatory_loss_stop_reason("FIXED_STOP_LONG_REFbb_low.1h.b0.25")
    assert mandatory_loss_stop_reason("ULTIMATE_DC_4H_HARD_STOP_LONG_px99_lvl100")
    assert mandatory_loss_stop_reason("UNCOND_DC_LOW_4H_UNCOND_px99_lvl100", 0.25)
    assert mandatory_loss_stop_reason("UNCOND_DC_HIGH_4H_UNCOND_px101_lvl100", 0.25)
    assert not mandatory_loss_stop_reason("UNCOND_DC_LOW_4H_UNCOND_px99_lvl100", 0.5)
    assert not mandatory_loss_stop_reason("VIGILANCE_DC4_15M_HARD_STOP")
    assert not mandatory_loss_stop_reason("ARBITRARY_FIXED_STOP_BYPASS")
    assert not mandatory_loss_stop_reason("PREFIX_ULTIMATE_DC_4H_HARD_STOP_LONG_fake")
    assert not mandatory_loss_stop_reason("FIXED_STOP_LONG_px1_lvl1_g-1_REFdc_low.1h.")
    assert not mandatory_loss_stop_reason("FIXED_STOP_SHORT_px1_lvl1_g-1_REFdc_high.15m.b0.35")
    assert not mandatory_loss_stop_reason("DC_HARD_STOP_1H")
    assert not mandatory_loss_stop_reason("VEC_LAST_CLOSE")


def test_loss_close_wrapper_and_vigilance_paths_are_strictly_pinned():
    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    vec = (ROOT / "v12_quick_engine.py").read_text()
    assert "[EXECUTE_NOW_NET_GAIN_BLOCK]" in ez
    assert "[EXECUTE_NOW_NET_GAIN_BLOCK_TR]" in tr
    assert "if False and bool(getattr(_cfg, \"VIGILANCE_GUARD_ENABLED\", True))" in ez
    assert "False\n                and bool(_cfg_auto(\"VIGILANCE_GUARD_ENABLED\", True))" in tr
    assert "_vig_enabled = False" in vec
