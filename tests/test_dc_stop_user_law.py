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


def test_vector_close_and_reduce_gate_uses_net_dollars_and_state_delta():
    from vec_decisions.net_exit_gate import vector_exit_event_allowed

    before = {"realized": 0.0, "fees": 0.04, "qty": 2.0}
    close_loss = {"type": "CLOSE", "reason": "ALL_TF_AGAINST_CLOSE", "pnl_dollars": -0.01}
    close_gain = {"type": "CLOSE", "reason": "TAKE_PROFIT", "pnl_dollars": 0.01}
    assert not vector_exit_event_allowed(close_loss, before, before)
    assert not vector_exit_event_allowed({"type": "CLOSE", "reason": "TAKE_PROFIT", "pnl_dollars": 0.05, "qty": 1.0, "price": 100.0}, before, before, additional_fee_pct=0.08)
    assert vector_exit_event_allowed({"type": "CLOSE", "reason": "TAKE_PROFIT", "pnl_dollars": 0.09, "qty": 1.0, "price": 100.0}, before, before, additional_fee_pct=0.08)
    assert vector_exit_event_allowed(close_gain, before, before)
    reduce = {"type": "REDUCE", "reason": "REDUCE_SIGNAL", "qty": 1.0, "price": 101.0}
    assert not vector_exit_event_allowed({**reduce, "price": 100.05}, {"avg_price": 100.0}, before, is_long=True, half_fee=0.0004, additional_fee_pct=0.04)
    assert vector_exit_event_allowed(reduce, {"avg_price": 100.0}, before, is_long=True, half_fee=0.0004, additional_fee_pct=0.04)
    short_reduce = {**reduce, "price": 99.0}
    assert vector_exit_event_allowed(short_reduce, {"avg_price": 100.0}, before, is_long=False, half_fee=0.0004, additional_fee_pct=0.04)
    assert vector_exit_event_allowed({"type": "CLOSE", "reason": "ULTIMATE_DC_4h_HARD_STOP at dc_4h_low 0.25pct channel", "pnl_dollars": -1.0}, before, before)
    assert vector_exit_event_allowed({"type": "CLOSE", "reason": "FIXED_STOP", "mandatory_stop_kind": "DC15_FROZEN_DEFAULT", "pnl_dollars": -1.0}, before, before)


def test_v12_loss_signal_holds_position_until_positive_net_or_mandatory_stop():
    import numpy as np
    import v12_quick_engine as vq

    n = 100
    close = np.linspace(100.0, 99.5, n)
    timestamps = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {"close": close, "close_3m": close, "close_15m": close, "close_5m": close, "timestamps": timestamps, "timestamp_3m": timestamps, "timestamp_15m": timestamps, "low_3m": close, "high_3m": close, "open_3m": close, "dc_low_4h": np.full(n, 80.0), "dc_high_4h": np.full(n, 120.0), "dc_low_15m": np.full(n, 90.0), "dc_high_15m": np.full(n, 130.0)}
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.full(n, -1.0)
        npz[f"wt2_{tf}"] = np.zeros(n)
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.ALL_TF_AGAINST_FORCE_CLOSE_ENABLED = True
    cfg.ALL_TF_AGAINST_FORCE_CLOSE_MIN_TFS = 3
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.08
    result = vq.simulate_one(npz, "GATE_LONG", True, cfg, force_initial_seed=True)
    closes = [t for t in result["ledger"] if t.get("type") == "CLOSE" and not t.get("unrealised")]
    assert not closes
    assert any(t.get("reason") == "FINAL_MTM" and t.get("unrealised") for t in result["ledger"])
