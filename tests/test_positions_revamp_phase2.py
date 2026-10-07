"""POSITIONS REVAMP phase 2 (2026-10-06) — each failure reproduced against the real code paths, mocked brokers only.

  P1  ez cancel_order_with_confirmation: FILLED / NEW / -2011 / -2013 are NOT "canceled" (was True -> market fallback double-fill)
  P2  tradier chase: cancel never confirmed -> no re-place / no market; partial+canceled -> only the remainder re-placed
  P3  tradier second positions writer: broker qty preserved, no row dropped, no memory-only row added, atomic
  P4  broker-confirmed SUCCESS: core says SUCCESS, broker unchanged -> UNCONFIRMED + key locked for new exposure at the wire
  P5  hang watchdog reconciles (broker snapshot seeded, local marked stale) instead of os._exit on the first hang
  P6  OPEN on a leftover crypto position -> AUGMENT (retagged token passes the wire gain gate; un-retagged OPEN is refused)
  P7  ez reduce/entry fallbacks consult a FRESH broker read; maker-success + unverified reduce never reaches send_webhook
  P8  bootstrap timeout > inner positionRisk timeout
"""
import ast
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import order_dedupe_guard as odg  # noqa: E402
import positions_truth as pt  # noqa: E402
from binance.exceptions import BinanceAPIException  # noqa: E402

from tests.test_positions_revamp_incident import FakeBinance, FakeTradierHTTP, tradier, run, _tm_dummy, _hermetic_positions  # noqa: E402,F401


def _bexc(code):
    return BinanceAPIException(None, 400, json.dumps({"code": code, "msg": "x"}))


@pytest.fixture
def fast_sleep(monkeypatch):
    async def _s(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _s)


# ───────────── P1 ─────────────
class CancelClient:
    def __init__(self, cancel, statuses):
        self.cancel, self.statuses, self.gets = cancel, list(statuses), 0

    def futures_cancel_order(self, symbol=None, orderId=None):
        if isinstance(self.cancel, Exception):
            raise self.cancel
        return {"status": self.cancel}

    def futures_get_order(self, symbol=None, orderId=None):
        self.gets += 1
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(st, Exception):
            raise st
        return {"status": st}


@pytest.mark.parametrize("cancel,statuses,expected", [
    ("FILLED", ["FILLED"], False),  # filled is NOT canceled (old code: True -> MARKET fallback = double fill)
    ("NEW", ["NEW", "NEW"], False),  # old code accepted NEW as canceled
    ("NEW", ["NEW", "CANCELED"], True),
    ("CANCELED", ["CANCELED"], True),
    (_bexc(-2011), ["FILLED"], False),  # old code: -2011 -> True
    (_bexc(-2013), [_bexc(-2013)], False),  # old code: not found -> True; now re-query, never assume
    (_bexc(-2011), ["CANCELED"], True),
])
def test_P1_ez_cancel_confirmation(cancel, statuses, expected, fast_sleep):
    import ez_manage

    cli = CancelClient(cancel, statuses)
    dummy = SimpleNamespace(accounts={"men": SimpleNamespace(client=cli)}, stop_manager=None)
    got = run(ez_manage.MultiAccountTradeManager.cancel_order_with_confirmation(dummy, "men:ZRXUSDT_LONG", 123, 1.0))
    assert got is expected


# ───────────── P2 ─────────────
def test_P2_chase_cancel_unconfirmed_no_replace(tradier, monkeypatch):
    http, guard, make = tradier
    http.positions.clear()
    orig = http.request

    def req(method, url, **kw):  # cancel accepted but the order keeps reading "open" (status never final)
        if method == "DELETE":
            from tests.test_positions_revamp_incident import FakeResp
            return FakeResp(200, {"order": {"status": "ok"}})
        return orig(method, url, **kw)

    monkeypatch.setattr(http, "request", req)
    tm, dummy = _tm_dummy(monkeypatch, make)
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        res = run(tm.TradierTradeManager.place_order(dummy, "IBIT", "BUY", 24, "market", action="OPEN", position_side="LONG", account_key="trc"))
    assert "CHASE_CANCEL_UNCONFIRMED" in json.dumps(res)
    assert len(http.posts) == 1  # no re-place, no market fallback


def test_P2b_partial_then_canceled_replaces_only_remainder(tradier, monkeypatch):
    http, guard, make = tradier
    http.positions.clear()
    orig = http.request
    state = {"n": 0}

    def req(method, url, **kw):
        if method == "DELETE":
            oid = int(url.rsplit("/", 1)[1])
            o = next(x for x in http.orders if x["id"] == oid)
            if state["n"] == 0:  # first leg: 10 of 24 filled, rest canceled
                o.update(status="canceled", exec_quantity=10.0, avg_fill_price=48.9)
                q, c = http.positions.get("IBIT", [0.0, 0.0])
                http.positions["IBIT"] = [q + 10, c + 489.0]
            state["n"] += 1
            from tests.test_positions_revamp_incident import FakeResp
            return FakeResp(200, {"order": {"status": "ok"}})
        return orig(method, url, **kw)

    monkeypatch.setattr(http, "request", req)
    http.fill_limit_on_cancel = False
    tm, dummy = _tm_dummy(monkeypatch, make)
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        run(tm.TradierTradeManager.place_order(dummy, "IBIT", "BUY", 24, "market", action="OPEN", position_side="LONG", account_key="trc"))
    assert float(http.posts[0]["quantity"]) == 24
    assert all(float(p["quantity"]) <= 14 for p in http.posts[1:])
    assert http.positions["IBIT"][0] <= 24


# ───────────── P3 ─────────────
def test_P3_second_writer_preserves_broker_qty(tmp_path, monkeypatch):
    import tradier_manage as tm

    acct = tmp_path / "trc"
    acct.mkdir()
    disk = {"trc:IBIT_LONG": {"positionAmt": 129.0, "entry_price": 48.68, "symbol": "IBIT"}, "trc:NVDA_LONG": {"positionAmt": 5.0, "symbol": "NVDA"}}
    (acct / "long_positions.json").write_text(json.dumps(disk))
    monkeypatch.setattr(tm.config, "get_account_config", lambda a: {"account_dir": acct})
    mem = {
        "trc:IBIT_LONG": SimpleNamespace(positionAmt=51.6582, max_gain=4.2, to_dict=lambda: {"positionAmt": 51.6582, "max_gain": 4.2, "symbol": "IBIT"}),
        "trc:GHOST_LONG": SimpleNamespace(positionAmt=10.0, to_dict=lambda: {"positionAmt": 10.0, "symbol": "GHOST"}),
    }
    reader = SimpleNamespace(positions=mem)
    run(tm.PositionReader.save_all_positions(reader))
    out = json.loads((acct / "long_positions.json").read_text())
    assert out["trc:IBIT_LONG"]["positionAmt"] == 129.0  # broker qty, not the optimistic/capped 51.6
    assert out["trc:IBIT_LONG"]["max_gain"] == 4.2  # memory bookkeeping still saved
    assert "trc:NVDA_LONG" in out  # never dropped
    assert "trc:GHOST_LONG" not in out  # memory-only (broker does not hold it) never added
    assert not list(acct.glob("*.tmp.*"))


# ───────────── P4 ─────────────
def test_P4_unconfirmed_success_locks_key(tmp_path, monkeypatch):
    odg._BINANCE_GUARDS.clear()
    odg._UNCONFIRMED.clear()
    pt._CACHES.clear()
    broker = FakeBinance()
    broker.pos[("ZRXUSDT", "LONG")] = [0.0, 0.0]
    guard = odg.get_binance_guard("men", SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0), ledger_dir=tmp_path)
    client = odg.GuardedBinanceClient(broker, guard)
    broker.futures_position_information_all = broker.futures_position_information
    monkeypatch.setattr(broker, "futures_position_information", lambda symbol=None: FakeBinance.futures_position_information(broker, symbol or "ZRXUSDT"))
    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=client)}, positions_service=None, positions={})
    cfg = SimpleNamespace(POSITIONS_CONFIRM_TIMEOUT_S=0.0, POSITIONS_CONFIRM_POLL_S=0.0, MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0)

    async def core(position_key=None, account_key=None, symbol=None, position_side="LONG", quantity=0.0, action=None, reason="", is_full_close=False, side="BUY"):
        mgr.__dict__.setdefault("_ptruth_pre", {})[position_key] = {"pre": 0.0}
        return "SUCCESS"  # e.g. MARKET accepted (NEW, executedQty 0) — nothing filled

    res = run(pt.ez_execute_now_guarded(mgr, core, (), dict(position_key="men:ZRXUSDT_LONG", account_key="men", symbol="ZRXUSDT", position_side="LONG", quantity=100.0, action="OPEN"), config=cfg, ledger_dir=tmp_path))
    assert res.startswith(pt.UNCONFIRMED_PREFIX) and "SUCCESS" not in res
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="KEY_UNCONFIRMED_LOCK"):
            client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="REDUCE"):
        client.futures_create_order(symbol="ZRXUSDT", side="SELL", positionSide="LONG", type="MARKET", quantity="1", reduceOnly="true")  # exits still pass
    odg._UNCONFIRMED.clear()


# ───────────── P5 ─────────────
def test_P5_hang_reconcile_instead_of_exit():
    pt._CACHES.clear()
    svc = SimpleNamespace(positions_last_sync=None)
    rows = [{"symbol": "GALAUSDT", "positionSide": "SHORT", "positionAmt": "-828", "entryPrice": "0.002411"}]
    assert pt.hang_reconcile(svc, "men", rows, "test hang", SimpleNamespace(POSITIONS_HANG_EXIT_AFTER=3)) is False
    assert svc.positions_last_sync.timestamp() == 0  # local now STALE -> every order-bound read polls the broker
    assert pt._CACHES[("binance", "men")].last.amount("GALAUSDT", "SHORT") == 828.0  # broker truth seeded
    assert pt.hang_reconcile(svc, "men", rows, "test hang", SimpleNamespace(POSITIONS_HANG_EXIT_AFTER=3)) is False
    assert pt.hang_reconcile(svc, "men", rows, "test hang", SimpleNamespace(POSITIONS_HANG_EXIT_AFTER=3)) is True
    pt.hang_ok(svc, "men")
    assert pt.hang_reconcile(svc, "men", rows, "test hang", SimpleNamespace(POSITIONS_HANG_EXIT_AFTER=3)) is False


def test_P5b_service_exits_only_after_reconcile_gives_up():
    src = (ROOT / "ez_positions_service.py").read_text()
    lines = src.splitlines()
    for code in ("os._exit(42)", "os._exit(43)", "os._exit(44)"):
        hits = [n for n, l in enumerate(lines) if l.strip() == code]
        assert hits, code
        for n in hits:
            assert any("hang_reconcile(" in l for l in lines[max(0, n - 6):n]), f"{code} at line {n + 1} reachable without hang_reconcile"


# ───────────── P6 ─────────────
def test_P6_open_on_leftover_retag_passes_wire_only_after_gain_gate(tmp_path):
    odg._BINANCE_GUARDS.clear()
    odg._UNCONFIRMED.clear()
    broker = FakeBinance()
    broker.pos[("ZRXUSDT", "LONG")] = [3.0, 0.28]  # leftover; mark 0.30 = +7.1%
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path, cfg=SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0))
    client = odg.GuardedBinanceClient(broker, guard)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="OPEN_ON_NONZERO_POSITION"):
            client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    guard._backoff.clear()
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        odg.retag_action("AUGMENT")  # what execute_now does after its OPEN_ON_LEFTOVER gain gate passed
        client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    assert len(broker.created) == 1


def test_P6b_execute_now_converts_before_uag_gate():
    src = (ROOT / "ez_manage.py").read_text()
    i_conv = src.index("[OPEN_ON_LEFTOVER_TO_AUGMENT]")
    i_uag = src.index("# UNIVERSAL_AUGMENT_GAIN_GATE — 2026-05-26 USER MANDATE")
    assert i_conv < i_uag
    blk = src[src.index("# POSITIONS REVAMP 2026-10-06: OPEN on a leftover BROKER position"):i_uag]
    assert 'action = "AUGMENT"' in blk and "_odg.retag_action(\"AUGMENT\")" in blk and "BLOCKED_OPEN_ON_LEFTOVER_GAIN_GATE" in blk


# ───────────── P7 ─────────────
def test_P7_broker_moved_uses_fresh_broker_read():
    pt._CACHES.clear()
    rows = {"v": [{"symbol": "ZRXUSDT", "positionSide": "LONG", "positionAmt": "100", "entryPrice": "0.3"}]}

    class C:
        def futures_position_information(self):
            return rows["v"]

    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=C())})
    assert run(pt.ez_broker_moved(mgr, "men", "ZRXUSDT", "LONG", 100.0, "reduce")) is False
    rows["v"] = [{"symbol": "ZRXUSDT", "positionSide": "LONG", "positionAmt": "40", "entryPrice": "0.3"}]
    assert run(pt.ez_broker_moved(mgr, "men", "ZRXUSDT", "LONG", 100.0, "reduce")) is True  # forced refresh, not the cached 100
    assert run(pt.ez_broker_moved(mgr, "men", "ZRXUSDT", "LONG", 0.0, "increase")) is True


def test_P7b_maker_success_unverified_reduce_never_reaches_market():
    src = (ROOT / "ez_manage.py").read_text()
    i = src.index("[MAKER_REDUCE_UNCONFIRMED]")
    tail = src[i:i + 600]
    assert 'return "UNCONFIRMED_BY_BROKER_MAKER_REDUCE"' in tail
    assert tail.index("return") < tail.index("# Maker failed — fall through to webhook")


# ───────────── P8 ─────────────
def test_P8_bootstrap_timeout_75():
    src = (ROOT / "ez_manage.py").read_text()
    assert 'getattr(config, "POSITIONS_BOOTSTRAP_TIMEOUT_S", 75.0)' in src
