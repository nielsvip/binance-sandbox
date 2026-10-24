"""USER 2026-10-24: isolated harness for crypto ratio_rebalance_loop through its
public entry point on real skew input, using the REAL engine method +
REAL firing legs, RATIO_REBALANCE_ENABLED True then False.

- Enabled + LONG-heavy book + bullish breadth -> RATIO_REDUCE_* fires.
- Enabled + LONG-heavy book + flat breadth (+OW leg off) -> RATIO_CLOSE_* fires.
- Disabled -> immediate early return, zero queue calls, single pass.

One pass per case: instant-sleep raises a private BaseException on its second
call (survives every `except Exception` in the loop), so each case observes
exactly one loop iteration.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ez_manage as EZ

ACCT = "ang"
PK = f"{ACCT}:ZZZUSDC_LONG"


class _StopAfterPass(BaseException):
    pass


def _pos(gain=2.0):
    return types.SimpleNamespace(
        positionAmt=1.0,
        gain=gain,
        opened_at=time.time(),
        symbol="ZZZUSDC",
        is_hedge=False,
        mark_price=100.0,
        entry_price=98.0,
    )


def _service(skew_long=9000.0, skew_short=1000.0):
    svc = types.SimpleNamespace()

    def _ratio(acct):
        if acct == ACCT:
            return {"long_value": skew_long, "short_value": skew_short}
        return {"long_value": 50.0, "short_value": 50.0}

    svc.get_long_short_ratio = _ratio
    return svc


def _mgr(snap):
    return types.SimpleNamespace(
        order_queue=[],
        positions_service=_service(),
        positions_by_account={ACCT: {PK: _pos()}},
        hedge_engine=None,
        tracker_manager=None,
        indicators_snapshot=dict(snap),
        redis_manager=None,
        hot_data_cache={},
    )


def _install_sleep(monkeypatch):
    calls = {"n": 0}

    async def _fast(d):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _StopAfterPass()
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast)


def _install_queue(monkeypatch, fired):
    async def _fake_q(q, mgr, pkey, action, reason, *a, **k):
        fired.append((action, str(reason), pkey))
        return "QUEUED"

    monkeypatch.setattr(EZ, "queue_trade_action", _fake_q)


def _install_ii(monkeypatch, btc):
    async def _fake_ii(mgr=None, sym=None):
        return dict(btc)

    monkeypatch.setattr(EZ, "ii", _fake_ii)


BTC = {
    "k_1h": 60.0, "d_1h": 50.0, "k_4h": 60.0, "d_4h": 50.0,
    "current_price": 100.0, "0market_sentiment_score": 50.0,
    "0market_sentiment_score_ema": 50.0,
}


@pytest.mark.asyncio
async def test_ratio_gate_false_blocks_single_pass(monkeypatch):
    # NOTE: Config is a dataclass — class-level patching cannot reach the live
    # engine instance's __dict__. Patch the instance the loop actually reads
    # (EZ.config proxy forwards setattr to it), mirroring production reality:
    # flipping the class default only takes effect on engine recycle.
    fired = []
    _install_sleep(monkeypatch)
    _install_queue(monkeypatch, fired)
    _install_ii(monkeypatch, BTC)
    monkeypatch.setattr(EZ.config, "RATIO_REBALANCE_ENABLED", False, raising=False)
    snap = {"ZZZUSDC": {"current_price": 100.0, "ema_20_15m": 90.0}}
    t0 = time.time()
    # Gate returns BEFORE the first sleep: no StopAfterPass, clean immediate return.
    await EZ.MultiAccountTradeManager.ratio_rebalance_loop(_mgr(snap))
    assert time.time() - t0 < 5.0
    assert fired == []


@pytest.mark.asyncio
async def test_ratio_reduce_leg_fires_on_skew(monkeypatch):
    fired = []
    _install_sleep(monkeypatch)
    _install_queue(monkeypatch, fired)
    _install_ii(monkeypatch, BTC)
    monkeypatch.setattr(EZ.config, "RATIO_REBALANCE_ENABLED", True, raising=False)
    snap = {"ZZZUSDC": {"current_price": 100.0, "ema_20_15m": 90.0}}
    with pytest.raises(_StopAfterPass):
        await EZ.MultiAccountTradeManager.ratio_rebalance_loop(_mgr(snap))
    reduces = [f for f in fired if f[1].startswith("RATIO_REDUCE_")]
    closes = [f for f in fired if f[1].startswith("RATIO_CLOSE_")]
    assert len(reduces) >= 1
    assert closes == []
    assert all(a == "REDUCE" for a, _, _ in reduces)


@pytest.mark.asyncio
async def test_ratio_close_leg_fires_on_skew(monkeypatch):
    fired = []
    _install_sleep(monkeypatch)
    _install_queue(monkeypatch, fired)
    _install_ii(monkeypatch, BTC)
    monkeypatch.setattr(EZ.config, "RATIO_REBALANCE_ENABLED", True, raising=False)
    monkeypatch.setattr(EZ.config, "RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY", False, raising=False)
    snap = {"ZZZUSDC": {"current_price": 90.0, "ema_20_15m": 100.0}}
    with pytest.raises(_StopAfterPass):
        await EZ.MultiAccountTradeManager.ratio_rebalance_loop(_mgr(snap))
    plain = [f for f in fired if f[1].startswith("RATIO_CLOSE_") and "RATIO_CLOSE_OW_" not in f[1]]
    reduces = [f for f in fired if f[1].startswith("RATIO_REDUCE_")]
    assert len(plain) >= 1
    assert reduces == []
