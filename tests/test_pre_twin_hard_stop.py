"""2026-10-08 USER FIX a+b+c (ARUSDT/NEARUSDC/AIAUSDT/RENDERUSDT stranded 40h): hard stops can never be suppressed by twin ownership/starvation."""
import asyncio
import pathlib
import sys
from types import SimpleNamespace as NS

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from live_twins import vec_exact as VX  # noqa: E402


def _prep(close, lo4, hi4, wt_against=True, tf="4h"):
    n = 3
    d = {
        "close": np.full(n, close),
        "dc_low_4h": np.full(n, lo4),
        "dc_high_4h": np.full(n, hi4),
        "dc_low_D": np.full(n, lo4),
        "dc_high_D": np.full(n, hi4),
    }
    for t in ("1h", "15m", "4h"):
        d[f"wt1_{t}"] = np.full(n, -5.0 if wt_against else 5.0)
        d[f"wt2_{t}"] = np.full(n, 5.0 if wt_against else -5.0)
    return d


def test_bar_stop_long_breach_fires():
    a = VX.bar_hard_stop_action(_prep(4.27, 4.36, 5.0), 2, 1790000000.0, "LONG")
    assert a is not None and a["type"] == "CLOSE" and a["to_flat"] is True
    assert "ULTIMATE_DC_4h_HARD_STOP_LONG" in a["reason"]
    assert a["vec_price"] == 4.27


def test_bar_stop_long_no_breach_none():
    assert VX.bar_hard_stop_action(_prep(4.40, 4.36, 5.0), 2, 1790000000.0, "LONG") is None


def test_bar_stop_veto_holds():
    assert VX.bar_hard_stop_action(_prep(4.27, 4.36, 5.0, wt_against=False), 2, 1790000000.0, "LONG") is None


def test_bar_stop_short_mirror():
    d = _prep(5.10, 4.36, 5.0)
    for t in ("1h", "15m", "4h"):
        d[f"wt1_{t}"] = np.full(3, 5.0)
        d[f"wt2_{t}"] = np.full(3, -5.0)
    a = VX.bar_hard_stop_action(d, 2, 1790000000.0, "SHORT")
    assert a is not None and a["type"] == "CLOSE"
    assert "ULTIMATE_DC_4h_HARD_STOP_SHORT" in a["reason"]


def test_bar_stop_missing_arrays_fail_open():
    assert VX.bar_hard_stop_action({}, 0, 1790000000.0, "LONG") is None
    assert VX.bar_hard_stop_action({"close": np.array([1.0])}, 5, 1790000000.0, "LONG") is None


def test_bar_stop_kill_switch():
    import live_twins.vec_exact as V
    V.set_cfg(NS(VEC_EXACT_BAR_HARD_STOP_ENABLED=False))
    try:
        assert V.bar_hard_stop_action(_prep(4.27, 4.36, 5.0), 2, 1790000000.0, "LONG") is None
    finally:
        V.set_cfg(None)


def _stub_tm(pos, calls):
    async def _get_position(pk):
        return pos

    async def _execute_now(**kw):
        calls.append(kw)
        return "OK"

    return NS(
        positions={"inf:ARUSDT_LONG": pos},
        processing_keys=set(),
        config=NS(VIGILANCE_GUARD_ENABLED=True, VIGILANCE_DC4_STOP_TF="15m", VIGILANCE_DC4_BREACH_TOLERANCE_PCT=0.25, BOTTOM_EXIT_HTF_WT_VETO_ENABLED=True),
        get_position=_get_position,
        execute_now=_execute_now,
    )


def test_pre_twin_probe_fires_on_dc_breach():
    import ez_manage as E
    E._PRE_TWIN_STOP_FIRED_TS.clear()
    pos = NS(positionAmt=16.4, gain=-7.0, mark_price=4.27)
    calls = []
    ind = {"dc_low_4h": 4.36, "dc_high_4h": 5.0, "wt1_1h": -5.0, "wt2_1h": 5.0, "wt1_15m": -5.0, "wt2_15m": 5.0, "wt1_4h": -5.0, "wt2_4h": 5.0, "dc_low4_15m": 4.20}
    fired = asyncio.run(E._pp_unconditional_hard_stops(_stub_tm(pos, calls), "inf", "inf:ARUSDT_LONG", _ind_override=ind, _px_override=4.27))
    assert fired is True
    assert len(calls) == 1
    assert "ULTIMATE_DC_4h_HARD_STOP_LONG" in calls[0]["reason"]
    assert calls[0]["is_full_close"] is True and calls[0]["side"] == "SELL"


def test_pre_twin_probe_veto_and_flat():
    import ez_manage as E
    E._PRE_TWIN_STOP_FIRED_TS.clear()
    pos = NS(positionAmt=16.4, gain=-7.0, mark_price=4.27)
    calls = []
    ind = {"dc_low_4h": 4.36, "dc_high_4h": 5.0, "wt1_1h": 5.0, "wt2_1h": -5.0, "wt1_15m": 5.0, "wt2_15m": -5.0, "wt1_4h": 5.0, "wt2_4h": -5.0, "dc_low4_15m": 4.20}
    assert asyncio.run(E._pp_unconditional_hard_stops(_stub_tm(pos, calls), "inf", "inf:ARUSDT_LONG", _ind_override=ind, _px_override=4.27)) is False
    assert calls == []
    flat = NS(positionAmt=0.0, gain=0.0, mark_price=4.27)
    assert asyncio.run(E._pp_unconditional_hard_stops(_stub_tm(flat, calls), "inf", "inf:ARUSDT_LONG", _ind_override=ind, _px_override=4.27)) is False


def test_pre_twin_probe_cooldown_blocks_refire():
    import ez_manage as E
    E._PRE_TWIN_STOP_FIRED_TS.clear()
    pos = NS(positionAmt=16.4, gain=-7.0, mark_price=4.27)
    calls = []
    ind = {"dc_low_4h": 4.36, "dc_high_4h": 5.0, "wt1_1h": -5.0, "wt2_1h": 5.0, "wt1_15m": -5.0, "wt2_15m": 5.0, "wt1_4h": -5.0, "wt2_4h": 5.0, "dc_low4_15m": 4.20}
    tm = _stub_tm(pos, calls)
    assert asyncio.run(E._pp_unconditional_hard_stops(tm, "inf", "inf:ARUSDT_LONG", _ind_override=ind, _px_override=4.27)) is True
    assert asyncio.run(E._pp_unconditional_hard_stops(tm, "inf", "inf:ARUSDT_LONG", _ind_override=ind, _px_override=4.27)) is False
    assert len(calls) == 1


def test_stocks_bar_stop_event():
    import tradier_vec_exact as TVE
    npz = {
        "close": np.array([100.0, 99.0, 98.0]),
        "dc_low_4h": np.array([99.0, 99.0, 99.0]),
        "dc_high_4h": np.array([101.0, 101.0, 101.0]),
        "wt1_1h": np.array([0.0, 0.0, -5.0]), "wt2_1h": np.array([0.0, 0.0, 5.0]),
        "wt1_15m": np.array([0.0, 0.0, -5.0]), "wt2_15m": np.array([0.0, 0.0, 5.0]),
        "wt1_4h": np.array([0.0, 0.0, -5.0]), "wt2_4h": np.array([0.0, 0.0, 5.0]),
    }
    e = TVE.bar_hard_stop_event(npz, "LONG", {})
    assert e is not None and e["type"] == "CLOSE"
    assert "ULTIMATE_DC_4h_HARD_STOP_LONG" in e["reason"]
    npz["close"] = np.array([100.0, 99.5, 99.5])
    assert TVE.bar_hard_stop_event(npz, "LONG", {}) is None
    npz["close"] = np.array([100.0, 99.0, 98.0])
    npz["wt1_1h"] = np.array([0.0, 0.0, 5.0])
    npz["wt2_1h"] = np.array([0.0, 0.0, -5.0])
    assert TVE.bar_hard_stop_event(npz, "LONG", {}) is None
    assert TVE.bar_hard_stop_event(npz, "LONG", {"VEC_EXACT_BAR_HARD_STOP_ENABLED": False}) is None
    assert TVE.bar_hard_stop_event({}, "LONG", {}) is None


def test_stocks_pre_twin_probe_fires():
    import tradier_manage as TM
    TM._PRE_TWIN_STOP_FIRED_TS_TRADIER.clear()
    pos = NS(positionAmt=10.0, gain=-3.0)
    calls = []

    async def _queue(order_queue, trade_manager, position_key, action, reason, conviction=100.0, override_qty=None, record_decision=True):
        calls.append({"action": action, "reason": reason, "override_qty": override_qty})
        return "QUEUED"

    _orig_q, _orig_c = TM.queue_trade_action, TM.dc_hardstop_cooldown_record
    TM.queue_trade_action, TM.dc_hardstop_cooldown_record = _queue, (lambda *a, **k: None)
    try:
        ind = {"dc_low_4h": 99.0, "dc_high_4h": 101.0, "wt1_1h": -5.0, "wt2_1h": 5.0, "wt1_15m": -5.0, "wt2_15m": 5.0, "wt1_4h": -5.0, "wt2_4h": 5.0, "dc_low4_15m": 97.0}
        tm = NS(strategy=NS(parse_market_data=lambda raw: dict(ind)))
        fired = asyncio.run(TM._pre_twin_hard_stops("trb", "trb:AAPL_LONG", "AAPL", "LONG", None, tm, {"ts": 1}, pos, 98.0))
        assert fired is True
        assert len(calls) == 1 and calls[0]["action"] == "CLOSE"
        assert "ULTIMATE_DC_4h_HARD_STOP_LONG" in calls[0]["reason"]
        assert calls[0]["override_qty"] == 999999
        assert asyncio.run(TM._pre_twin_hard_stops("trb", "trb:AAPL_LONG", "AAPL", "LONG", None, tm, {"ts": 1}, pos, 98.0)) is False
        assert len(calls) == 1
    finally:
        TM.queue_trade_action, TM.dc_hardstop_cooldown_record = _orig_q, _orig_c


def test_stocks_delegation_order_and_stale_wired():
    src = (ROOT / "tradier_manage.py").read_text()
    i_probe = src.index("if has_position and await _pre_twin_hard_stops(")
    i_deleg = src.index('_vx_res = await _vec_exact_process(')
    assert i_probe < i_deleg, "stocks pre-twin probe must run BEFORE the VEC_EXACT delegation"
    assert 'if _vx_res != "VEC_EXACT_STALE":' in src
    assert "VEC_EXACT_STALE_FALLBACK" in src
    tsrc = (ROOT / "tradier_vec_exact.py").read_text()
    assert "bar_hard_stop_event(npz, side, overrides)" in tsrc
    cfg = (ROOT / "config_tradier.py").read_text()
    assert "VEC_EXACT_STALE_FALLBACK_BARS: float = 4.0" in cfg
    assert "VEC_EXACT_BAR_HARD_STOP_ENABLED: bool = True" in cfg


def test_delegation_order_and_stale_fallback_wired():
    src = (ROOT / "ez_manage.py").read_text()
    i_probe = src.index("if await _pp_unconditional_hard_stops(trade_manager, account_key, position_key):")
    i_deleg = src.index("if await _vec_exact_process_position(account_key, position_key, trade_manager):")
    assert i_probe < i_deleg, "pre-twin probe must run BEFORE the VEC_EXACT delegation"
    assert "VEC_EXACT_STALE_FALLBACK" in src
    assert "VEC_EXACT_STALE_FALLBACK_BARS" in src
    cfg = (ROOT / "config.py").read_text()
    assert "VEC_EXACT_STALE_FALLBACK_BARS: float = 4.0" in cfg
    assert "VEC_EXACT_BAR_HARD_STOP_ENABLED: bool = True" in cfg
