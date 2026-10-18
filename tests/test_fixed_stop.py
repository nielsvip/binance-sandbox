"""FIXED_STOP — non-negotiable frozen-at-entry stop (USER 2026-10-12).

LONG:  software level frozen at entry bar dc_low_15m * 0.9975  (SHORT: vv).
Exchange STOP_MARKET backstop sits 0.05% beyond the software level so the
regular (maker) exit is tried first and taker fees are the last resort.
No switch, no config field, no template row — runs in EVERY vec calc,
every live-trading bar, and the live backtest (same code paths).
"""
import dataclasses as dc
import numpy as np
import vec_decisions.fixed_stop as fx


def test_freeze_math_long_short():
    assert fx.freeze_software_level(True, 100.0, 0.0, 101.0) == 99.75
    assert fx.freeze_software_level(False, 0.0, 100.0, 99.0) == 100.25


def test_freeze_fallback_entry_based():
    assert fx.freeze_software_level(True, 0.0, 0.0, 100.0) == 99.75
    assert fx.freeze_software_level(False, 0.0, 0.0, 100.0) == 100.25
    assert fx.freeze_software_level(True, -1.0, 0.0, 0.0) == 0.0


def test_exchange_level_extra_distance():
    assert abs(fx.exchange_level(True, 99.75) - 99.75 * 0.9995) < 1e-12
    assert abs(fx.exchange_level(False, 100.25) - 100.25 * 1.0005) < 1e-12


def test_crypto_exchange_level_is_channel_minus_40_bps():
    """USER 2026-10-18: the Binance STOP_MARKET sits 0.40% beyond the frozen entry-bar dc_15m, not 0.2999%."""
    assert abs(fx.crypto_exchange_level(True, fx.freeze_software_level(True, 100.0, 0.0, 100.0)) - 99.60) < 1e-9
    assert abs(fx.crypto_exchange_level(False, fx.freeze_software_level(False, 0.0, 100.0, 100.0)) - 100.40) < 1e-9
    assert fx.crypto_exchange_level(True, 99.75) < fx.exchange_level(True, 99.75)
    assert fx.crypto_exchange_level(False, 100.25) > fx.exchange_level(False, 100.25)
    assert fx.crypto_exchange_level(True, 0.0) == 0.0


def test_breach_sides_and_touch():
    assert fx.breached(True, 99.75, 99.75) is True
    assert fx.breached(True, 99.76, 99.75) is False
    assert fx.breached(False, 100.25, 100.25) is True
    assert fx.breached(False, 100.24, 100.25) is False
    assert fx.breached(True, 50.0, 0.0) is False


def test_tick_rounding_safe_side():
    assert fx.round_to_tick(True, 99.756, 0.01) == 99.75
    assert fx.round_to_tick(False, 100.254, 0.01) == 100.26
    assert fx.round_to_tick(True, 99.75, 0.0) == 99.75


def test_stop_params_builder():
    p = fx.build_stop_params(True, 99.75, 0.01)
    assert p["side"] == "SELL" and p["closePosition"] is True
    assert p["workingType"] == "MARKET" or p["workingType"] == "MARK_PRICE"
    assert abs(p["stopPrice"] - 99.60) < 0.011  # dc_low_15m 100.0 -> -0.40%
    q = fx.build_stop_params(False, 100.25, 0.01)
    assert q["side"] == "BUY" and q["closePosition"] is True
    assert abs(q["stopPrice"] - 100.40) < 0.011  # dc_high_15m 100.0 -> +0.40%


def _mk(n, close, dl15, dh15):
    ts = np.arange(1700000000, 1700000000 + n * 180, 180)
    z = np.zeros(n)
    return {
        "close_3m": close, "close_15m": close, "close_5m": close, "close": close,
        "dc_low_15m": dl15, "dc_high_15m": dh15,
        "dc_low_15m_prev": dl15, "dc_high_15m_prev": dh15,
        "dc_low_4h": np.full(n, 50.0), "dc_high_4h": np.full(n, 150.0),
        "dc_low_1h": np.full(n, 50.0), "dc_high_1h": np.full(n, 150.0),
        "timestamps": ts, "timestamp_3m": ts, "timestamp_15m": ts,
        "wt1_3m": z, "wt2_3m": z, "wt1_15m": z, "wt2_15m": z,
        "wt1_1h": z, "wt2_1h": z, "wt1_4h": z, "wt2_4h": z, "wt1_D": z, "wt2_D": z,
    }


def _cfg():
    import v12_quick_engine as vq
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"; cfg.BASE_TF = "3m"; cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0; cfg.MIN_HOLD_BARS = 100; cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False; cfg.STOP_LOSS_ENABLED = False
    return cfg


def test_v12_long_frozen_not_trailing():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(101.0, 94.0, n)
    dl = np.full(n, 95.0); dl[0] = 100.0
    dh = np.full(n, 150.0)
    r = vq.simulate_one(_mk(n, close, dl, dh), "FX_LONG", True, _cfg(), force_initial_seed=True)
    reasons = [t.get("exit_reason") for t in r["ledger"]]
    assert "FIXED_STOP" in reasons, reasons[:5]
    first = next(t for t in r["ledger"] if t.get("exit_reason") == "FIXED_STOP")
    cross = int(np.where(close <= 100.0 * 0.9975)[0][0])
    assert first["bar_exit"] == cross
    assert first["bar_exit"] < int(np.where(close <= 95.0 * 0.9975)[0][0])


def test_v12_short_frozen_not_trailing():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(99.0, 106.0, n)
    dh = np.full(n, 105.0); dh[0] = 100.0
    dl = np.full(n, 50.0)
    r = vq.simulate_one(_mk(n, close, dl, dh), "FX_SHORT", False, _cfg(), force_initial_seed=True)
    reasons = [t.get("exit_reason") for t in r["ledger"]]
    assert "FIXED_STOP" in reasons, reasons[:5]
    first = next(t for t in r["ledger"] if t.get("exit_reason") == "FIXED_STOP")
    cross = int(np.where(close >= 100.0 * 1.0025)[0][0])
    assert first["bar_exit"] == cross


def test_v12_no_fire_above_frozen():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(101.0, 100.0, n)
    dl = np.full(n, 100.0); dh = np.full(n, 150.0)
    r = vq.simulate_one(_mk(n, close, dl, dh), "FX_HOLD", True, _cfg(), force_initial_seed=True)
    assert "FIXED_STOP" not in [t.get("exit_reason") for t in r["ledger"]]


def test_v12_unconditional_despite_trailing_switch_off():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(101.0, 94.0, n)
    dl = np.full(n, 95.0); dl[0] = 100.0
    dh = np.full(n, 150.0)
    cfg = _cfg()
    cfg.DC_HARD_STOP_15M_ENABLED = False
    cfg.DC_HARD_STOP_1H_ENABLED = False
    r = vq.simulate_one(_mk(n, close, dl, dh), "FX_UNCOND", True, cfg, force_initial_seed=True)
    assert "FIXED_STOP" in [t.get("exit_reason") for t in r["ledger"]]


def test_no_config_or_quickconfig_fields():
    import config as C, config_tradier as CT, v12_quick_engine as V
    for cls in (C.Config, CT.TradierConfig, V.QuickConfig):
        names = [f.name for f in dc.fields(cls())]
        assert not [x for x in names if "FIXED_STOP" in x], names


def test_parity_gate_exempts_fixed_stop():
    import parity_exact_gate as _peg
    from live_twins import vec_driven as _vdm
    ok, code = _peg.allows("FIXED_STOP_LONG_px1.0_lvl1.0_g-2.0", "CLOSE", True)
    assert ok and code == "EMERGENCY_EXIT", (ok, code)
    assert _vdm.is_emergency_reason("FIXED_STOP_SHORT_px1.0_lvl1.0_g-2.0") is True


def test_live_reason_wired_all_managers():
    import pathlib
    for fname in ["ez_manage.py", "ez_positions_service.py", "tradier_manage.py", "v12_quick_engine.py"]:
        assert "FIXED_STOP" in pathlib.Path(fname).read_text(), fname


def test_position_fields_roundtrip():
    from ez_positions_service import Position as SP
    p = SP(symbol="ALGOUSDT", position_side="LONG", entry_price=0.13, mark_price=0.12, positionAmt=10.0, initial_quantity=10.0, gain=-8.0, max_gain=0.0, prev_gain=0.0, max_quantity=10.0, last_augmentation_amount=0.0, last_augmentation_price=0.0, last_augmentation_time=None, last_reduction_amount=0.0, last_reduction_price=0.0, last_reduction_time=None, max_positionSize=10.0, opened_at=None, last_updated=None, last_signal="", fixed_stop_price=0.128, exchange_stop_order_id="12345")
    d = p.to_dict()
    p2 = SP.from_dict(d)
    assert p2.fixed_stop_price == 0.128 and p2.exchange_stop_order_id == "12345"
    from ez_manage import Position as EP
    q = EP(symbol="ALGOUSDT", position_side="LONG", entry_price=0.13, mark_price=0.12, positionAmt=10.0, initial_quantity=10.0, gain=-8.0, max_gain=0.0, prev_gain=0.0, max_quantity=10.0, last_augmentation_amount=0.0, last_augmentation_price=0.0, last_augmentation_time=None, last_reduction_amount=0.0, last_reduction_price=0.0, last_reduction_time=None, max_positionSize=10.0, opened_at=None, last_updated=None, last_signal="", fixed_stop_price=0.128, exchange_stop_order_id="12345")
    q2 = EP.from_dict(q.to_dict())
    assert q2.fixed_stop_price == 0.128 and q2.exchange_stop_order_id == "12345"


def _fake_stop_manager(find_result, exec_calls):
    import types
    from ez_manage import MultiAccountTradeManager as _MM
    fake = types.SimpleNamespace()
    fake.managed_stop_registry = {}
    async def _find(account_key, symbol, side_u, want_oid):
        exec_calls.setdefault("find", []).append((symbol, want_oid))
        return find_result
    async def _exec(**kw):
        exec_calls.setdefault("exec", []).append(kw)
        if kw.get("action") == "PLACE_STOP":
            return "SUCCESS_STOP_PLACED_999"
        return "SUCCESS_STOP_CANCELLED_X"
    fake._fixed_stop_find_algo = _find
    fake.execute_now = _exec
    fake.get_client = lambda account_key: object()
    fake.get_symbol_config = lambda symbol: {"tick_size": 0.5}
    fake.ensure_fixed_stop_backstop = _MM.ensure_fixed_stop_backstop.__get__(fake)
    return fake


def _fake_stop_position():
    import types
    return types.SimpleNamespace(positionAmt=1.0, opened_at="OPEN123", entry_price=100.0, exchange_stop_order_id="OLD999")


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_reverify_gone_oid_replaces():
    calls = {}
    m = _fake_stop_manager(False, calls)
    pk = "men:LTCUSDC_LONG"
    m.managed_stop_registry[pk] = {"orderId": "OLD999", "stopPrice": 60.0, "entry_sig": "OPEN123", "symbol": "LTCUSDC", "side": "LONG"}
    out = _run(m.ensure_fixed_stop_backstop(account_key="men", symbol="LTCUSDC", position_key=pk, position_side="LONG", position=_fake_stop_position(), soft_level=60.0, current_price=61.6))
    assert str(out).startswith("SUCCESS_STOP_PLACED"), out
    assert m.managed_stop_registry[pk]["orderId"] == "999"
    assert calls["find"] == [("LTCUSDC", "OLD999")]


def test_reverify_present_oid_no_replace():
    calls = {}
    m = _fake_stop_manager(True, calls)
    pk = "men:LTCUSDC_LONG"
    m.managed_stop_registry[pk] = {"orderId": "OLD999", "stopPrice": 60.0, "entry_sig": "OPEN123", "symbol": "LTCUSDC", "side": "LONG"}
    out = _run(m.ensure_fixed_stop_backstop(account_key="men", symbol="LTCUSDC", position_key=pk, position_side="LONG", position=_fake_stop_position(), soft_level=60.0, current_price=61.6))
    assert out == "OK_ALREADY_PLACED", out
    assert "exec" not in calls
    assert m.managed_stop_registry[pk]["orderId"] == "OLD999"


def test_reverify_ttl_skips_broker_call():
    import time
    calls = {}
    m = _fake_stop_manager(True, calls)
    pk = "men:LTCUSDC_LONG"
    m.managed_stop_registry[pk] = {"orderId": "OLD999", "stopPrice": 60.0, "entry_sig": "OPEN123", "symbol": "LTCUSDC", "side": "LONG"}
    m._fixed_stop_verify_ts = {pk: time.time()}
    out = _run(m.ensure_fixed_stop_backstop(account_key="men", symbol="LTCUSDC", position_key=pk, position_side="LONG", position=_fake_stop_position(), soft_level=60.0, current_price=61.6))
    assert out == "OK_ALREADY_PLACED", out
    assert "find" not in calls


def test_reverify_transport_failure_fail_closed():
    import time
    calls = {}
    m = _fake_stop_manager(None, calls)
    pk = "men:LTCUSDC_LONG"
    m.managed_stop_registry[pk] = {"orderId": "OLD999", "stopPrice": 60.0, "entry_sig": "OPEN123", "symbol": "LTCUSDC", "side": "LONG"}
    out = _run(m.ensure_fixed_stop_backstop(account_key="men", symbol="LTCUSDC", position_key=pk, position_side="LONG", position=_fake_stop_position(), soft_level=60.0, current_price=61.6))
    assert out == "OK_ALREADY_PLACED", out
    assert "exec" not in calls
    assert m._fixed_stop_verify_ts[pk] < time.time()
