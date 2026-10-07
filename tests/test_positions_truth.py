"""POSITIONS_TRUTH — mocked broker only; no network, no live orders."""
import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402
import positions_truth as pt  # noqa: E402

CFG = SimpleNamespace(POSITIONS_CONFIRM_TIMEOUT_S=0.3, POSITIONS_CONFIRM_POLL_S=0.01, POSITIONS_BROKER_CACHE_S=1.0, POSITIONS_MAX_AGE_S=1.0)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeBinance:
    def __init__(self):
        self.amts = {}  # (symbol, side) -> abs amt
        self.position_calls = 0
        self.fail = False
        self.orders = {}

    def futures_position_information(self):
        self.position_calls += 1
        if self.fail:
            raise ConnectionError("broker down")
        rows = []
        for (sym, side), amt in self.amts.items():
            rows.append({"symbol": sym, "positionSide": side, "positionAmt": str(amt if side == "LONG" else -amt), "entryPrice": "1.0"})
        return rows

    def futures_get_order(self, symbol=None, orderId=None, origClientOrderId=None):
        return dict(self.orders[origClientOrderId or orderId])


class FakeService:
    def __init__(self):
        self.positions = {}
        self.positions_last_sync = None
        self.resyncs = 0

    async def fetch_positions(self, account_key):
        self.resyncs += 1
        return {}


class FakeMgr:
    def __init__(self, client, account="men"):
        self.positions_service = FakeService()
        self.positions = {}
        self.accounts = {account: SimpleNamespace(client=client)}


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    pt._CACHES.clear()
    pt._TRADIER_PRE.clear()
    odg._BINANCE_GUARDS.clear()
    odg._TRADIER_GUARDS.clear()
    monkeypatch.setenv("ORDER_DEDUPE_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setattr(pt, "_ban_remaining_binance", lambda: 0.0)
    yield


def _pos(amt):
    return SimpleNamespace(positionAmt=amt)


# ── (a)+(b) stale -> broker fallback / fresh -> no extra calls ─────────────────────────
def test_stale_local_falls_back_to_broker(caplog):
    fb = FakeBinance()
    fb.amts[("GALAUSDT", "SHORT")] = 100.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:GALAUSDT_SHORT"] = _pos(-100.0)
    mgr.positions_service.positions_last_sync = time.time() - 5.0  # 5 s old > 1 s
    with caplog.at_level("WARNING"):
        res = run(pt.ez_pre_order_check(mgr, "men", "GALAUSDT", "men:GALAUSDT_SHORT", "SHORT", "REDUCE", config=CFG))
    assert res is None
    assert fb.position_calls == 1
    assert mgr._ptruth_pre["men:GALAUSDT_SHORT"]["source"] == "broker"
    assert mgr._ptruth_pre["men:GALAUSDT_SHORT"]["pre"] == 100.0
    assert "POSITIONS_STALE_FALLBACK" in caplog.text


def test_fresh_local_makes_no_broker_call():
    fb = FakeBinance()
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:GALAUSDT_SHORT"] = _pos(-100.0)
    mgr.positions_service.positions_last_sync = time.time() - 0.2
    res = run(pt.ez_pre_order_check(mgr, "men", "GALAUSDT", "men:GALAUSDT_SHORT", "SHORT", "OPEN", config=CFG))
    assert res is None
    assert fb.position_calls == 0
    assert mgr._ptruth_pre["men:GALAUSDT_SHORT"] == {"pre": 100.0, "local": 100.0, "source": "local", "ts": mgr._ptruth_pre["men:GALAUSDT_SHORT"]["ts"]}


def test_broker_snapshot_cached_and_single_flight():
    fb = FakeBinance()
    bp = pt.BrokerPositions("binance", "men", pt.binance_fetcher(fb), config=CFG)

    async def go():
        return await asyncio.gather(*[bp.snapshot() for _ in range(5)])

    snaps = run(go())
    assert fb.position_calls == 1 and all(s is snaps[0] for s in snaps)
    run(bp.snapshot())
    assert fb.position_calls == 1  # within cache window
    run(bp.snapshot(force=True))
    assert fb.position_calls == 2


def test_ip_ban_refuses_poll_entries_blocked_exits_proceed(monkeypatch):
    fb = FakeBinance()
    monkeypatch.setattr(pt, "_ban_remaining_binance", lambda: 300.0)
    mgr = FakeMgr(fb)
    mgr.positions_service.positions_last_sync = None
    assert run(pt.ez_pre_order_check(mgr, "men", "XUSDT", "men:XUSDT_LONG", "LONG", "OPEN", config=CFG)) == "BLOCKED_POSITIONS_BROKER_UNREACHABLE"
    assert run(pt.ez_pre_order_check(mgr, "men", "XUSDT", "men:XUSDT_LONG", "LONG", "CLOSE", config=CFG)) is None
    assert fb.position_calls == 0


def test_broker_unreachable_blocks_entry():
    fb = FakeBinance()
    fb.fail = True
    mgr = FakeMgr(fb)
    assert run(pt.ez_pre_order_check(mgr, "men", "XUSDT", "men:XUSDT_LONG", "LONG", "AUGMENT", config=CFG)) == "BLOCKED_POSITIONS_BROKER_UNREACHABLE"


# ── restart with stale files -> broker truth wins ─────────────────────────────────────
def test_restart_with_stale_files_broker_truth_wins(tmp_path):
    f = tmp_path / "long_positions.json"
    f.write_text("{}")
    pt.write_meta(f, writer="test", now=time.time() - 600)
    assert pt.is_stale(pt.read_written_at(f), CFG)
    fb = FakeBinance()  # broker: flat
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:SOLUSDC_LONG"] = _pos(3.0)  # loaded from stale disk at restart
    mgr.positions_service.positions_last_sync = None  # no broker sync since restart
    assert run(pt.ez_pre_order_check(mgr, "men", "SOLUSDC", "men:SOLUSDC_LONG", "LONG", "AUGMENT", config=CFG)) == "BLOCKED_POSITIONS_BROKER_MISMATCH"
    assert run(pt.ez_pre_order_check(mgr, "men", "SOLUSDC", "men:SOLUSDC_LONG", "LONG", "CLOSE", config=CFG)) == "SKIP_BROKER_FLAT"
    fb.amts[("SOLUSDC", "LONG")] = 5.0  # broker holds MORE than the stale file: exit proceeds on broker truth
    pt._CACHES.clear()
    assert run(pt.ez_pre_order_check(mgr, "men", "SOLUSDC", "men:SOLUSDC_LONG", "LONG", "REDUCE", config=CFG)) is None
    assert mgr._ptruth_pre["men:SOLUSDC_LONG"]["pre"] == 5.0
    assert mgr.positions_service.resyncs >= 1


def test_meta_contract(tmp_path):
    f = tmp_path / "short_positions.json"
    f.write_text("{}")
    assert pt.read_written_at(f) is None and pt.is_stale(pt.read_written_at(f), CFG)  # no meta = stale
    pt.write_meta(f, writer="test")
    assert not pt.is_stale(pt.read_written_at(f), CFG)
    os.utime(f, (time.time() - 100, time.time() - 100))  # data older than meta -> data mtime caps freshness
    assert pt.is_stale(pt.read_written_at(f), CFG)
    assert pt.is_positions_file(f) and not pt.is_positions_file(tmp_path / "tracker.json")


# ── (c) zero qty refused / SUCCESS only when broker confirms ──────────────────────────
def _core_factory(mgr, fb, effect, result="SUCCESS", calls=None, precheck=True):
    async def core(position_key=None, account_key=None, symbol=None, original_positionAmt=0.0, side="BUY", position_side="LONG", quantity=0.0, old_price=0.0, unique_id=None, reason="", is_full_close=False, action=None, is_hedge=False, hedge_for=None, url_variant=""):
        if calls is not None:
            calls.append(quantity)
        if precheck:  # real core: STEP2 hook, only reached past the gates and never for sandbox accounts
            blk = await pt.ez_pre_order_check(mgr, account_key, symbol, position_key, position_side, action, reason, config=CFG)
            if blk:
                return blk
        effect(fb, symbol, position_side, quantity)
        return result

    return core


def test_zero_qty_refused_never_sent():
    fb = FakeBinance()
    mgr = FakeMgr(fb)
    calls = []
    core = _core_factory(mgr, fb, lambda *a: None, calls=calls)
    for q in (0.0, -1.0, float("nan"), None):
        res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:XUSDT_LONG", "account_key": "men", "symbol": "XUSDT", "position_side": "LONG", "side": "SELL", "quantity": q, "action": "REDUCE", "reason": "VEC_EXACT_REDUCE_TO_FLAT"}, config=CFG))
        assert res == "BLOCKED_ZERO_QTY"
    assert calls == [] and fb.position_calls == 0


def test_replay_reduce_to_flat_unconfirmed_is_not_success():
    """The replay: REDUCE_TO_FLAT reported SUCCESS while the live position never went flat."""
    fb = FakeBinance()
    fb.amts[("ZRXUSDT", "SHORT")] = 50.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:ZRXUSDT_SHORT"] = _pos(-50.0)
    core = _core_factory(mgr, fb, lambda *a: None)  # order "accepted" but nothing happened at the broker
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:ZRXUSDT_SHORT", "account_key": "men", "symbol": "ZRXUSDT", "position_side": "SHORT", "side": "BUY", "quantity": 50.0, "action": "REDUCE", "reason": "VEC_EXACT_REDUCE_TO_FLAT"}, config=CFG))
    assert res == "UNCONFIRMED_BY_BROKER_NOT_FLAT"
    assert "SUCCESS" not in res


def test_close_confirmed_flat_keeps_success():
    fb = FakeBinance()
    fb.amts[("ZRXUSDT", "SHORT")] = 50.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:ZRXUSDT_SHORT"] = _pos(-50.0)

    def eff(fb_, sym, side, q):
        fb_.amts[(sym, side)] = 0.0

    core = _core_factory(mgr, fb, eff)
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:ZRXUSDT_SHORT", "account_key": "men", "symbol": "ZRXUSDT", "position_side": "SHORT", "side": "BUY", "quantity": 50.0, "action": "CLOSE", "is_full_close": True}, config=CFG))
    assert res == "SUCCESS"


def test_reduce_partial_without_ledger_is_not_success():
    fb = FakeBinance()
    fb.amts[("XUSDT", "LONG")] = 10.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:XUSDT_LONG"] = _pos(10.0)

    def eff(fb_, sym, side, q):
        fb_.amts[(sym, side)] = 8.0  # asked 4, broker moved 2 and no broker-confirmed fill record

    core = _core_factory(mgr, fb, eff)
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:XUSDT_LONG", "account_key": "men", "symbol": "XUSDT", "position_side": "LONG", "side": "SELL", "quantity": 4.0, "action": "REDUCE"}, config=CFG))
    assert res == "UNCONFIRMED_BY_BROKER_PARTIAL_UNVERIFIED"


def test_reduce_confirmed_by_ledger_filled_qty(tmp_path):
    fb = FakeBinance()
    fb.amts[("XUSDT", "LONG")] = 10.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:XUSDT_LONG"] = _pos(10.0)
    guard = odg.get_binance_guard("men", ledger_dir=tmp_path / "ledger")

    def eff(fb_, sym, side, q):
        # maker partially filled 2 of 4 — broker order status says executedQty=2, position moved by exactly 2
        cid = "odgtest1"
        guard.ledger.mutate(sym, lambda orders: orders.append({"client_order_id": cid, "order_id": None, "side": "SELL", "position_side": "LONG", "qty": q, "submitted_at": time.time(), "state": "SUBMITTED", "executed_qty": 0.0, "final": False}))
        fb_.orders[cid] = {"orderId": 7, "clientOrderId": cid, "status": "CANCELED", "executedQty": "2"}
        fb_.amts[(sym, side)] = 8.0

    core = _core_factory(mgr, fb, eff, result="SUCCESS")
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:XUSDT_LONG", "account_key": "men", "symbol": "XUSDT", "position_side": "LONG", "side": "SELL", "quantity": 4.0, "action": "REDUCE"}, config=CFG, ledger_dir=tmp_path / "ledger"))
    assert res == "SUCCESS"


def test_reduce_with_zero_broker_fill_not_success(tmp_path):
    fb = FakeBinance()
    fb.amts[("XUSDT", "LONG")] = 10.0
    mgr = FakeMgr(fb)
    mgr.positions_service.positions["men:XUSDT_LONG"] = _pos(10.0)
    guard = odg.get_binance_guard("men", ledger_dir=tmp_path / "ledger")

    def eff(fb_, sym, side, q):
        guard.ledger.mutate(sym, lambda orders: orders.append({"client_order_id": "c0", "side": "SELL", "position_side": "LONG", "qty": q, "submitted_at": time.time(), "state": "FINAL", "status": "EXPIRED", "executed_qty": 0.0, "final": True}))

    core = _core_factory(mgr, fb, eff, result="SUCCESS_VIA_FALLBACK")
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:XUSDT_LONG", "account_key": "men", "symbol": "XUSDT", "position_side": "LONG", "side": "SELL", "quantity": 4.0, "action": "REDUCE"}, config=CFG, ledger_dir=tmp_path / "ledger"))
    assert res == "UNCONFIRMED_BY_BROKER_NO_FILL_CONFIRMED"


def test_open_confirmed_and_blocked_results_untouched():
    fb = FakeBinance()
    mgr = FakeMgr(fb)

    def eff(fb_, sym, side, q):
        fb_.amts[(sym, side)] = q

    core = _core_factory(mgr, fb, eff)
    kw = {"position_key": "men:XUSDT_LONG", "account_key": "men", "symbol": "XUSDT", "position_side": "LONG", "side": "BUY", "quantity": 3.0, "action": "OPEN"}
    assert run(pt.ez_execute_now_guarded(mgr, core, (), dict(kw), config=CFG)) == "SUCCESS"
    blocked = _core_factory(mgr, fb, eff, result="BLOCKED_NO_365D_CONFIRM", precheck=False)
    assert run(pt.ez_execute_now_guarded(mgr, blocked, (), dict(kw), config=CFG)) == "BLOCKED_NO_365D_CONFIRM"


def test_sandbox_results_not_confirmed():
    fb = FakeBinance()
    mgr = FakeMgr(fb)
    core = _core_factory(mgr, fb, lambda *a: None, result="SUCCESS_SANDBOX", precheck=False)
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "sbx:X_LONG", "account_key": "sbx", "symbol": "X", "position_side": "LONG", "quantity": 1.0, "action": "OPEN"}, config=CFG, is_sandbox=lambda a: a == "sbx"))
    assert res == "SUCCESS_SANDBOX"


def test_master_switch_off_is_legacy():
    fb = FakeBinance()
    mgr = FakeMgr(fb)
    core = _core_factory(mgr, fb, lambda *a: None)
    off = SimpleNamespace(**vars(CFG), POSITIONS_TRUTH_ENABLED=False)
    res = run(pt.ez_execute_now_guarded(mgr, core, (), {"position_key": "men:X_LONG", "account_key": "men", "symbol": "X", "position_side": "LONG", "quantity": 1.0, "action": "OPEN"}, config=off))
    assert res == "SUCCESS"


# ── tradier ──────────────────────────────────────────────────────────────────────────
class FakeTradier:
    holdings = {}
    calls = 0

    def __init__(self, account_key=None):
        self.account_key = account_key
        self._current_id = "ACC"

    async def connect(self):
        return True

    async def close(self):
        return None

    async def get_account_positions(self, account_key=None):
        FakeTradier.calls += 1
        return [{"symbol": s, "quantity": q, "cost_basis": abs(q) * 10.0} for s, q in FakeTradier.holdings.items() if q]


def test_tradier_mismatch_blocks_entry_allows_exit():
    # IBIT trc 2026-10-06: broker 129 shares, positions file clamped to 51.6156
    assert pt.tradier_pre_order("trc", "IBIT", "LONG", "AUGMENT", 129.0, 51.6156, config=CFG) == "POSITIONS_BROKER_MISMATCH"
    assert pt.tradier_pre_order("trc", "IBIT", "LONG", "CLOSE", 129.0, 51.6156, config=CFG) is None
    assert pt._TRADIER_PRE[("trc", "IBIT", "LONG")]["pre"] == 129.0


def _tcore(effect, result="SUCCESS"):
    async def core(position_key, account_key, symbol, original_position_amt, side, position_side, quantity, old_price, unique_id, reason, is_full_close, action=None, decision_recorded=False):
        if quantity <= 0:
            return "INVALID_QUANTITY"
        pt.tradier_pre_order(account_key, symbol, position_side, action, FakeTradier.holdings.get(symbol, 0.0), FakeTradier.holdings.get(symbol, 0.0), config=CFG)
        effect(symbol, quantity)
        return result

    return core


def test_tradier_close_unconfirmed_not_success():
    FakeTradier.holdings = {"UEC": -25.0}
    mgr = SimpleNamespace(position_manager=SimpleNamespace(get_position=lambda k: None))
    core = _tcore(lambda s, q: None)  # order id returned, nothing filled
    res = run(pt.tradier_execute_now_guarded(mgr, core, ("trc:UEC_SHORT", "trc", "UEC", 25.0, "buy_to_cover", "SHORT", 25.0, 10.0, "u1", "WT_CROSSOVER", True), {"action": "CLOSE"}, client_factory=lambda a: FakeTradier(a), config=CFG))
    assert res == "UNCONFIRMED_BY_BROKER_NOT_FLAT"


def test_tradier_open_confirmed():
    FakeTradier.holdings = {}
    mgr = SimpleNamespace(position_manager=SimpleNamespace(get_position=lambda k: None))

    def eff(s, q):
        FakeTradier.holdings[s] = q

    res = run(pt.tradier_execute_now_guarded(mgr, _tcore(eff), ("trc:PLTR_LONG", "trc", "PLTR", 0.0, "buy", "LONG", 39.0, 180.0, "u2", "CLENOW_ENTRY", False), {"action": "OPEN"}, client_factory=lambda a: FakeTradier(a), config=CFG))
    assert res == "SUCCESS"


def test_tradier_zero_qty_refused():
    mgr = SimpleNamespace(position_manager=SimpleNamespace(get_position=lambda k: None))
    calls = []
    res = run(pt.tradier_execute_now_guarded(mgr, _tcore(lambda s, q: calls.append(q)), ("trb:X_LONG", "trb", "X", 5.0, "sell", "LONG", 0, 1.0, "u", "R", False), {"action": "REDUCE"}, client_factory=lambda a: FakeTradier(a), config=CFG))
    assert res == "BLOCKED_ZERO_QTY" and calls == []


def test_evaluate_table():
    assert pt.evaluate(10, 0, "close", 10, None, 1e-9)[0]
    assert not pt.evaluate(10, 1, "close", 10, None, 1e-9)[0]
    assert pt.evaluate(10, 6, "reduce", 4, None, 1e-9)[:2] == (True, "MATCH_REQUESTED")
    assert pt.evaluate(10, 10, "reduce", 4, None, 1e-9)[:2] == (False, "NO_CHANGE")
    assert pt.evaluate(10, 13, "increase", 5, 3.0, 1e-9)[:2] == (True, "MATCH_FILLED")
    assert pt.evaluate(10, 12, "reduce", 4, None, 1e-9)[1] == "NO_CHANGE"
    assert pt.evaluate(None, 5, "increase", 5, None, 1e-9)[1] == "NO_PRE_SNAPSHOT"
    assert pt.evaluate(10, None, "reduce", 4, None, 1e-9)[1] == "BROKER_UNREACHABLE"
    assert pt.classify_kind("REDUCE", False, "VEC_EXACT_REDUCE_TO_FLAT") == "close"
    assert pt.classify_kind("REDUCE") == "reduce" and pt.classify_kind("OPEN") == "increase"
    assert pt.classify_kind(None, False, "", "SELL", "LONG") == "reduce"


def test_ez_decorator_keeps_source_and_refuses_zero_qty():
    import inspect

    class Mgr(FakeMgr):
        @pt.ez_confirmed
        async def execute_now(self, position_key=None, account_key=None, symbol=None, original_positionAmt=0.0, side="BUY", position_side="LONG", quantity=0.0, reason="", is_full_close=False, action=None):
            "CORE_MARKER"
            return "SUCCESS"

    m = Mgr(FakeBinance())
    assert "CORE_MARKER" in inspect.getsource(Mgr.execute_now)  # source-string tests still see the core
    assert run(m.execute_now("men:X_LONG", "men", "X", 0.0, "SELL", "LONG", 0.0, action="REDUCE")) == "BLOCKED_ZERO_QTY"


def test_offloop_serialized_does_not_block_loop():
    async def go():
        ticks = []

        async def ticker():
            for _ in range(5):
                ticks.append(time.time())
                await asyncio.sleep(0.02)

        t = asyncio.ensure_future(ticker())
        await pt.offloop_serialized(time.sleep, 0.2)
        await t
        return ticks

    ticks = run(go())
    assert len(ticks) == 5 and ticks[-1] - ticks[0] < 0.2
