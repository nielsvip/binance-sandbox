"""2026-10-20 augment revival — vec_decisions/augment_core.py + STOCK_AUGMENT_VENUE_GUARD_ENABLED + live hooks.

Mac-runnable (no NPZ). The NPZ proofs (byte-identical ledgers on 4 sym_sides, flip through the switch, 13/13 LADDER acts
identical between the engine and the live core on SHOP_LONG) are tools/augment_core_parity.py on S1.
"""
import asyncio
import dataclasses
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vec_decisions.augment_core as AC  # noqa: E402
import vec_decisions.gain_ladder_augment as GLA  # noqa: E402


def _get(d):
    return lambda k, default=None: d.get(k, default)


def test_live_enabled_defaults_are_today():
    assert AC.live_enabled(_get({}), "tradier") is False
    assert AC.live_enabled(_get({}), "crypto") is False
    assert AC.live_enabled(_get({"STOCK_LIVE_AUGMENT_ONLY": False}), "tradier") is False
    assert AC.live_enabled(_get({"STOCK_AUGMENT_VENUE_GUARD_ENABLED": False, "STOCK_LIVE_AUGMENT_ONLY": True}), "tradier") is False
    assert AC.live_enabled(_get({"STOCK_AUGMENT_VENUE_GUARD_ENABLED": False, "STOCK_LIVE_AUGMENT_ONLY": False}), "tradier") is True
    assert AC.live_enabled(_get({"AUGMENT_CORE_LIVE_ENABLED": True}), "crypto") is True
    assert AC.live_enabled(_get({"STOCK_AUGMENT_VENUE_GUARD_ENABLED": "False"}), "tradier") is True


def test_ladder_matches_engine_predicate():
    class C:
        MIN_GAIN_TO_BUY_AGGRESSIVELY = 3.0

    for px, last, gain, peak in ((103.5, 100.0, 3.5, 3.6), (101.0, 100.0, 1.0, 1.2), (101.6, 100.0, 1.6, 3.2), (99.0, 100.0, -1.0, 0.5)):
        f, r = GLA.gain_ladder_fire(C, True, px, last, gain, peak)
        cf, src, reason, add = AC.decide(_get({"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0}), True, px, 100.0, last, gain, peak, 10.0)
        assert cf == f
        if f:
            assert src == "LADDER" and reason == f"AUGMENT_CORE_LADDER {r}" and add == 5.0


def test_cooldown_and_profit_gate():
    g = _get({"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0, "AUGMENTATION_COOLDOWN_SECONDS": 300.0})
    assert AC.decide(g, True, 104.0, 100.0, 100.0, 4.0, 4.0, 10.0, secs_since_last_aug=100.0)[0] is False
    assert AC.decide(g, True, 104.0, 100.0, 100.0, 4.0, 4.0, 10.0, secs_since_last_aug=400.0)[0] is True
    assert AC.decide(g, False, 96.0, 100.0, 100.0, 4.0, 4.0, 10.0)[0] is True  # short: gain since add 4 % on a falling price


def test_pyramid_through_uag_gate():
    g = _get({"PYRAMID_ENABLED": True, "PYRAMID_SIZE_MULT": 0.25, "MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0})
    ind = {"dc_position_15m": 0.9, "wt_velocity_1h": 5.0}
    f, src, _r, add = AC.decide(g, True, 101.0, 100.0, 100.0, 1.0, 1.0, 8.0, ind)
    assert f is False  # gain since add 1 % < 3 % and no pullback -> UAG gate blocks (as in the vector)
    f, src, _r, add = AC.decide(g, True, 101.6, 100.0, 100.0, 1.6, 3.2, 8.0, ind)
    assert f is True and src in ("LADDER", "PYRAMID")


def test_bounce_fails_closed_without_prev_k():
    g = _get({"BOUNCE_AUGMENT_ENABLED": True})
    ind = {"stoch_k_15m": 15.0, "stoch_d_15m": 12.0, "dc_low_4h": 100.0}
    assert AC.bounce_sig(g, ind, True, 100.5, None) is False
    assert AC.bounce_sig(g, ind, True, 100.5, 10.0) is True


def test_engine_and_config_fields():
    import v12_quick_engine as V

    f = {x.name: x.default for x in dataclasses.fields(V.QuickConfig)}
    assert f["STOCK_AUGMENT_VENUE_GUARD_ENABLED"] is True
    assert f["STOCK_LIVE_AUGMENT_ONLY"] is False
    import config_tradier as CT

    t = CT.TradierConfig()
    assert t.STOCK_AUGMENT_VENUE_GUARD_ENABLED is True and t.STOCK_LIVE_AUGMENT_ONLY is False
    import config as C

    assert C.Config.AUGMENT_CORE_LIVE_ENABLED is False


def test_pilot_guard_switch():
    from tools.opt import v12_pilot as VP

    p = {"mode": "tradier"}
    assert VP._venue_augment_guard(p, {})["STOCK_LIVE_AUGMENT_ONLY"] is True
    assert "STOCK_LIVE_AUGMENT_ONLY" not in VP._venue_augment_guard(p, {"STOCK_AUGMENT_VENUE_GUARD_ENABLED": False})
    assert VP._venue_augment_guard({"mode": "crypto"}, {}) == {}


def _tradier_stub(guard, gain=3.5, last=100.0):
    import tradier_manage as T

    queued = []

    async def fake_queue(order_queue, trade_manager, position_key, action, reason, conviction=50.0, override_qty=None, record_decision=True):
        from live_twins import vec_provenance as vp

        queued.append({"action": action, "reason": reason, "qty": override_qty, "prov": vp.current()})
        return "QUEUED"

    async def cqc(symbol, action, side, qty, ind, pos, mc):
        return qty

    vals = {"STOCK_AUGMENT_VENUE_GUARD_ENABLED": guard, "STOCK_LIVE_AUGMENT_ONLY": False, "MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0, "AUGMENT_MIN_GAIN_PCT": 0.0}
    orig_cfg, orig_q = T._cfg, T.queue_trade_action
    T._cfg = lambda k, d=None, *a, **kw: vals.get(k, d)
    T.queue_trade_action = fake_queue
    try:
        tm = types.SimpleNamespace(strategy=types.SimpleNamespace(calculate_quantity_complex=cqc))
        pos = types.SimpleNamespace(positionAmt=10.0, entry_price=100.0, gain=gain, max_gain=gain, last_augmentation_price=last, last_augmentation_time=None)
        tag = asyncio.run(T._augment_core_try(tm, None, "trb:SHOP_LONG", pos, {"timestamp_15m": 1.0}, {}, "SHOP", "LONG", "trb", 100.0 * (1 + gain / 100.0), True, 30.0, True))
    finally:
        T._cfg, T.queue_trade_action = orig_cfg, orig_q
    return tag, queued


def test_tradier_hook_inert_by_default():
    tag, queued = _tradier_stub(True)
    assert tag is None and queued == []


def test_tradier_hook_fires_through_queue_with_provenance():
    tag, queued = _tradier_stub(False)
    assert tag == "AUGMENT_CORE:LADDER"
    assert len(queued) == 1 and queued[0]["action"] == "AUGMENT" and queued[0]["qty"] == 5.0
    assert queued[0]["reason"].startswith("AUGMENT_CORE_LADDER UAG_LADDER")
    assert queued[0]["prov"]["pk"] == "trb:SHOP_LONG" and queued[0]["prov"]["kind"] == "AUGMENT_CORE" and queued[0]["prov"]["act"] == "AUGMENT:LADDER"


def test_tradier_hook_no_fire_below_trigger():
    tag, queued = _tradier_stub(False, gain=1.0)
    assert tag is None and queued == []


def _ez_stub(enabled, gain=3.5):
    import ez_manage as EZ

    vals = {"AUGMENT_CORE_LIVE_ENABLED": enabled, "MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0, "AUGMENT_MIN_GAIN_PCT": 0.0, "DD_BOUNCE_ENABLED": False, "LR_BAND_LADDER_ENABLED": False, "WT_D_BOUNCE_AUG_ENABLED": False, "TRAILING_AUG_ENABLED_TRADIER": False, "AUGMENT_AT_LOSS_ENABLED": False, "AUGMENT_FALLBACK_REDUCE_ENABLED": False}
    orig = EZ._psym_get
    EZ._psym_get = lambda symbol, side, k, d: vals.get(k, d)
    pos = types.SimpleNamespace(position_side="LONG", positionAmt=10.0, entry_price=100.0, gain=gain, max_gain=gain, last_augmentation_price=100.0, last_augmentation_time=None, last_augment_qty=0.0)

    class TM:
        async def get_position(self, k):
            return pos

    cfg = types.SimpleNamespace(**{k: v for k, v in vals.items()}, ABLATION_DISABLE_HIGH_GAIN_AUGMENT=False, MIN_GAIN=5.0, START_POSITION_SIZE=25.0, AUGMENT_BLOWPAST_ENABLED=False, AUGMENT_WT_CROSS_ENABLED=False, AUGMENT_WT_3TF_ENABLED=False, AUGMENT_HTF_TREND_ENABLED=False)
    ctx = {"position_key": "ang:ADAUSDC_LONG", "config": cfg, "symbol": "ADAUSDC", "trade_manager": TM(), "indicators": {"wt1_15m": 1.0}, "current_price": 100.0 * (1 + gain / 100.0), "is_long": True, "gain": gain, "position_side": "LONG"}
    try:
        return asyncio.run(EZ.evaluate_augmentation(ctx))
    finally:
        EZ._psym_get = orig


def test_ez_hook_inert_by_default():
    sig = _ez_stub(False)
    assert sig is None or "AUGMENT_CORE" not in str(getattr(sig, "reason", ""))


def test_ez_hook_fires_when_enabled():
    sig = _ez_stub(True)
    assert sig is not None and sig.action == "AUGMENT" and str(sig.reason).startswith("AUGMENT_CORE_LADDER UAG_LADDER") and abs(sig.quantity - 5.0) < 1e-9
