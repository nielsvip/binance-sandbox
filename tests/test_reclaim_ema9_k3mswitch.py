"""B00 exit-reclaim: ema_9_15m trend leg + k3m momentum leg under USE_1M_3M_SIGNALS_ENABLED."""
import asyncio
import logging
import sys
import types
from types import SimpleNamespace

import ez_manage as em


def _install_stubs(monkeypatch, indicators):
    async def _fake_ii(trade_manager, symbol):
        return dict(indicators)
    monkeypatch.setattr(em, "ii", _fake_ii)
    ezr = types.ModuleType("ez_reentry")
    ezr.is_reentry_eligible = lambda *a, **k: (True, "")
    monkeypatch.setitem(sys.modules, "ez_reentry", ezr)
    pev = types.ModuleType("position_evaluator")
    pev.evaluate_reentry_core = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "position_evaluator", pev)


def _ctx(use_3m):
    pos = SimpleNamespace(positionAmt=0.0, last_reduction_price=100.0, mark_price=110.0)
    tm = SimpleNamespace(positions={"tst:AAAUSDT_LONG": pos})
    cfg = SimpleNamespace(START_POSITION_SIZE=1000.0, MIN_POSITION_SIZE=0.0, HARDCODED_RALLY_REENTRY_ENABLED=False, REENTRY_EXIT_RECLAIM_ENABLED=True, REENTRY_EXIT_RECLAIM_BUFFER_PCT=0.2, USE_1M_3M_SIGNALS_ENABLED=use_3m)
    return {"config": cfg, "logger": logging.getLogger("test"), "position_key": "tst:AAAUSDT_LONG", "symbol": "AAAUSDT", "account_key": "tst", "trade_manager": tm, "position_side": "LONG", "current_price": 110.0}


def _inds(ema=100.0, k3m=60.0, k3m_prev=55.0):
    return {"ema_9_15m": ema, "sma_200_1m": 999999.0, "k_3m": k3m, "k_3m_prev": k3m_prev, "wt1_15m": 10.0, "wt2_15m": 5.0, "wt_velocity_15m": 2.0, "k_15m": 50.0}


def test_reclaim_fires_on_ema9_with_k3m_switch_off(monkeypatch):
    _install_stubs(monkeypatch, _inds(k3m=10.0, k3m_prev=90.0))
    sig = asyncio.run(em.evaluate_reentry(_ctx(False)))
    assert sig is not None and sig.action == "REENTRY" and sig.reason.startswith("B00_PRICE_ABOVE_EXIT")


def test_reclaim_blocked_by_adverse_ema9(monkeypatch):
    _install_stubs(monkeypatch, _inds(ema=120.0))
    assert asyncio.run(em.evaluate_reentry(_ctx(False))) is None


def test_reclaim_k3m_switch_on_adverse_blocks(monkeypatch):
    _install_stubs(monkeypatch, _inds(k3m=10.0, k3m_prev=90.0))
    assert asyncio.run(em.evaluate_reentry(_ctx(True))) is None


def test_reclaim_k3m_switch_on_favorable_fires(monkeypatch):
    _install_stubs(monkeypatch, _inds(k3m=60.0, k3m_prev=55.0))
    sig = asyncio.run(em.evaluate_reentry(_ctx(True)))
    assert sig is not None and sig.reason.startswith("B00_PRICE_ABOVE_EXIT")
