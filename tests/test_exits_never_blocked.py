"""EXITS NEVER BLOCKED (USER 2026-10-14 "make sure at least EXITS will never be compromised again").

Drives every guard that could refuse/delay a crypto close and asserts the close passes (FULL close -> FORCE_CLOSE_MARKET
from exchange truth) or is retried (partial REDUCE), plus the ledger fix (one row per confirmed fill / lifecycle).
Sections:
  A  classify(): every guard result code -> OK / NOOP / POLICY / VERIFY / REFUSED
  B  wrapper: each mechanical refusal of a FULL close escalates to FORCE inside execute_now; ledger written once
  C  wire (order_dedupe_guard exit path, FakeBinance): unreachable broker, other-leg order, own working order, clamp, flat
  D  ez_manage source pins: the in-body bypasses exist, pre-send ledger logging is gone behind the wrapper
  E  ez _exit_force_market on a FakeBinance: cancels own leg orders, market close, broker-flat verified, unreachable fallback
  F  ledger: idempotent logger, distinct-trade reader (retry storm -> 1 trade), stale-set + vigilance count lifecycles once
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import exit_guarantee as xg  # noqa: E402
import order_dedupe_guard as odg  # noqa: E402
import per_trade_ledger as ptl  # noqa: E402
import per_trade_logger as plog  # noqa: E402
import stale_set_halt as ssh  # noqa: E402
from tests.test_positions_revamp_incident import FakeBinance  # noqa: E402

CFG = SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0, EXIT_GUARANTEE_REDUCE_RETRY_SEC="0,0,0", EXIT_GUARANTEE_STUCK_CHECK_SEC="0,0", EXIT_GUARANTEE_NOTIFY=False)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("EXIT_GUARANTEE_BASE", str(tmp_path))
    monkeypatch.setattr(plog, "_OUT_DIR", tmp_path / "data" / "per_trade_returns")
    monkeypatch.setattr(plog, "_ATTEMPT_DIR", tmp_path / "data" / "per_trade_attempts")
    monkeypatch.setattr(plog, "_IDEM_DIR", tmp_path / "data" / "per_trade_returns" / "_idem")
    plog._idem_cache.clear()
    plog._idem_mtime.clear()
    xg._reset_for_tests()
    yield


def run(coro):
    return asyncio.run(coro)


# ───────────────────────────── A: classification table (the guard table) ─────────────────────────────
VEC = "WT_CROSS_EXIT_1h_bear |VEC_EXACT"
NATIVE = "EXIT_VELOCITY_WT_4h_vel-21.7-against-long_g1.15"
MECHANICAL_REFUSALS = [  # each of these could refuse a close before 2026-10-14; a FULL close now escalates to FORCE
    "BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION",
    "BLOCKED_EXCHANGE_OPEN_ORDER_EXISTS",
    "BLOCKED_HARD_REDUCE_LOCK_11s",
    "ORDER_DEDUPE_BLOCK_BROKER_UNREACHABLE_OPEN_ORDERS",
    "ORDER_DEDUPE_BLOCK_OPEN_ORDER_EXISTS",
    "ORDER_DEDUPE_BLOCK_LAST_ORDER_NOT_FINAL",
    "ORDER_DEDUPE_BLOCK_LAST_ORDER_STATUS_UNKNOWN",
    "ORDER_DEDUPE_BLOCK_PREFLIGHT_TIMEOUT_OR_ERROR",
    "BLOCK_SKIPPED_LOCK_ACTIVE",
    "BLOCK_SKIPPED_POST_FILL_COOLDOWN",
    "BLOCK_DEBOUNCE_ACTIVE_10s",
    "BLOCKED_STALE_MARK_PRICE_age31.0s",
    "BLOCK_STALE_DATA_MISMATCH",
    "BLOCKED_EMERGENCY_BRAKE_10_PER_MIN_CHURN",
    "BLOCKED_EMERGENCY_BRAKE_SYMBOL_CHURN",
    "BLOCKED_QUARANTINED(X)",
    "CIRCUIT_BREAKER_BLOCKED",
    "BLOCKED_MAKER_SUPPRESS_WEBHOOK",
    "UNCONFIRMED_BY_BROKER_MAKER_REDUCE",
    "UNCONFIRMED_BY_BROKER_NO_MOVE",
    "FAILED_WEBHOOK",
    "FAILED_PLACEMENT_AND_FALLBACK",
    "FAILED_VERIFICATION_AND_FALLBACK",
    "BLOCKED_DISPATCH_TIMEOUT",
    "ERROR_Timeout",
    "BLOCKED_POSITIONS_BROKER_UNREACHABLE",
    "BLOCKED: men:X_LONG $3.1 over MIN QTY",
    "",
    None,
]
STRATEGY_REFUSALS = ["BLOCKED_BY_UNIVERSAL_NOLOSS_GATE", "BLOCKED_NOLOSS_MIN_-0.20pct", "BLOCKED_BY_HEDGE_MODE", "BLOCKED_NEWBORN_PROTECT_120s", "BLOCKED_EXIT_LH_LL_REQUIRED", "BLOCKED_WT_EXIT_MIN_TFS_LONG", "BLOCKED_LOW_GAIN_DRAIN_PROTECTION"]
POLICY = ["BLOCKED_PARITY_NATIVE_NATIVE_EXIT_men:X_LONG", "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED", "BLOCKED_VEC_PARITY_CLOSE", "SKIPPED_SCALP_REDUCE_DISABLED", "BLOCKED_SERVER_HEARTBEAT_ACTIVE", "BLOCK WRONG ACCOUNT KEY", "men:X_LONG WAIT MEANS WAIT"]
VERIFY = ["BLOCK_POS_ALREADY_CLOSED", "SKIP_REDUCE_EMPTY_POSITION", "SKIP_BROKER_FLAT", "TIMEOUT_ALREADY_FLAT"]


@pytest.mark.parametrize("code", MECHANICAL_REFUSALS)
def test_A1_mechanical_refusal_of_full_close_is_escalated(code):
    assert xg.classify(code, VEC, True) == "REFUSED"
    assert xg.classify(code, NATIVE, True) == "REFUSED"
    assert xg.classify(code, VEC, False) in ("REFUSED", "NOOP")  # partial: REFUSED -> retried


@pytest.mark.parametrize("code", STRATEGY_REFUSALS)
def test_A2_strategy_refusal_only_policy_for_unauthorized_native(code):
    assert xg.classify(code, VEC, True) == "REFUSED"  # vec-decided close: live follows the vector incl. losses
    assert xg.classify(code, "FIXED_STOP_LONG_px1_lvl1_g-1.2", True) == "REFUSED"
    assert xg.classify(code, "VEC_DRIVEN_CLOSE x", True) == "REFUSED"
    assert xg.classify(code, NATIVE, True) == "POLICY"


@pytest.mark.parametrize("code", POLICY)
def test_A3_policy_never_escalated(code):
    assert xg.classify(code, VEC, True) == "POLICY"


@pytest.mark.parametrize("code", VERIFY)
def test_A4_local_flat_verified_on_full_close(code):
    assert xg.classify(code, VEC, True) == "VERIFY"
    assert xg.classify(code, VEC, False) == "NOOP"


def test_A5_success_and_noop():
    for c in ("SUCCESS", "SUCCESS_VIA_FALLBACK", "SUCCESS_FORCE_CLOSE_CONFIRMED|oid=1|px=2|qty=3|entry=1"):
        assert xg.classify(c, VEC, True) == "OK"
    for c in ("SUCCESS_SANDBOX", "FORCE_NOTHING_OPEN", "NO QUANTITY LEFT TO REDUCE 1", "SKIPPED_FORCE_PYTEST"):
        assert xg.classify(c, VEC, True) == "NOOP"


def test_A6_exit_classification():
    assert xg.is_exit_action("CLOSE") and xg.is_full_close("CLOSE")
    assert xg.is_exit_action("REDUCE") and not xg.is_full_close("REDUCE")
    assert xg.is_full_close("REDUCE", True)
    for a in ("OPEN", "AUGMENT", "REENTRY", "REVERSE_REDUCE", "PLACE_STOP", "CANCEL_STOP", "QUICK_OPEN"):
        assert not xg.is_exit_action(a)
    assert xg.is_exit_action(xg.FORCE_ACTION) and xg.is_full_close(xg.FORCE_ACTION)


# ───────────────────────────── B: wrapper on a fake manager ─────────────────────────────
class FakeMgr:
    """Stand-in for MultiAccountTradeManager: core execute_now returns the scripted refusal; FORCE closes the fake broker."""

    def __init__(self, codes, broker_amt=10.0):
        self.codes = list(codes)
        self.calls = []
        self.broker = {"amt": broker_amt, "entry": 1.0}
        self.force_calls = 0

    @odg.execute_now_gate("binance")
    @xg.wrap_execute_now("ez", lambda: HOOKS)
    async def execute_now(self, position_key=None, account_key=None, symbol=None, original_positionAmt=0.0, side="SELL", position_side="LONG", quantity=0.0, old_price=0.0, unique_id=None, reason="", is_full_close=False, action=None, is_hedge=False, hedge_for=None, url_variant="", stop_price=0.0, stop_order_id=""):
        self.calls.append(action)
        if action == xg.FORCE_ACTION:
            self.force_calls += 1
            if self.broker["amt"] <= 0:
                return "FORCE_NOTHING_OPEN"
            q = self.broker["amt"]
            self.broker["amt"] = 0.0
            return f"SUCCESS_FORCE_CLOSE_CONFIRMED|oid=77|px=1.1|qty={q}|entry=1.0"
        xg.stash(entry_price=1.0, opened_at="2026-10-14T00:00:00+00:00", gain=-0.5, qty=quantity, price=1.1, real_amt=self.broker["amt"], symbol=symbol, position_side=position_side, account=account_key)
        code = self.codes.pop(0) if self.codes else "SUCCESS"
        if code == "SUCCESS" and action != "CLOSE":
            self.broker["amt"] = max(0.0, self.broker["amt"] - quantity)
        elif code == "SUCCESS":
            self.broker["amt"] = 0.0
        return code


LEDGER = []


async def _h_force(mgr, a, c, why):
    return await mgr.execute_now(position_key=a.get("position_key"), account_key=a.get("account_key"), symbol=a.get("symbol"), position_side=a.get("position_side"), reason="XG", is_full_close=True, action=xg.FORCE_ACTION)


async def _h_broker(mgr, acct, sym, ps):
    return dict(mgr.broker)


async def _h_confirmed(mgr, a, c, res):
    life = xg.lifecycle_id(a.get("account_key"), a.get("symbol"), a.get("position_side"), c.get("opened_at"), c.get("entry_price"))
    full = bool(c.get("full") or c.get("force"))
    ok = plog.log_close(account=a.get("account_key"), symbol=a.get("symbol"), side=a.get("position_side"), entry_price=1.0, exit_price=1.1, pnl_pct=10.0, qty=c.get("qty") or 1.0, reason=a.get("reason"), idem_key=f"{life}|FULL" if full else f"{life}|{c.get('t0')}", lifecycle=life, confirmed=True, full_close=full)
    LEDGER.append((res, ok))


HOOKS = xg.Hooks(force=_h_force, broker=_h_broker, confirmed=_h_confirmed, cfg=lambda: CFG)


def _close(m, action="CLOSE", qty=10.0, reason=VEC, full=None):
    return run(m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="SELL", quantity=qty, reason=reason, is_full_close=(action == "CLOSE") if full is None else full, action=action))


@pytest.mark.parametrize("code", [c for c in MECHANICAL_REFUSALS if c])
def test_B1_every_mechanical_refusal_of_a_full_close_still_closes(code):
    LEDGER.clear()
    m = FakeMgr([code])
    res = _close(m)
    assert res.startswith("SUCCESS_FORCE_CLOSE_CONFIRMED"), (code, res)
    assert m.broker["amt"] == 0.0 and m.force_calls == 1
    assert len([x for x in LEDGER if x[1]]) == 1  # confirmed close logged exactly once


@pytest.mark.parametrize("code", STRATEGY_REFUSALS)
def test_B2_vec_close_refused_by_strategy_gate_still_closes(code):
    m = FakeMgr([code])
    assert _close(m).startswith("SUCCESS_FORCE_CLOSE_CONFIRMED")
    m2 = FakeMgr([code])
    assert _close(m2, reason=NATIVE) == code and m2.force_calls == 0  # native non-authorised close: policy stays


@pytest.mark.parametrize("code", POLICY)
def test_B3_policy_refusal_not_forced(code):
    m = FakeMgr([code])
    assert _close(m) == code and m.force_calls == 0 and m.broker["amt"] == 10.0


def test_B4_local_flat_but_broker_holds_is_closed():
    m = FakeMgr(["SKIP_BROKER_FLAT"])  # stale snapshot said flat (ang:EDUUSDT_LONG / NEARUSDC 25h)
    assert _close(m).startswith("SUCCESS_FORCE_CLOSE_CONFIRMED") and m.broker["amt"] == 0.0


def test_B5_local_flat_and_broker_flat_is_noop():
    m = FakeMgr(["BLOCK_POS_ALREADY_CLOSED"], broker_amt=0.0)
    assert _close(m) == "BLOCK_POS_ALREADY_CLOSED" and m.force_calls == 1


def test_B6_retry_storm_logs_one_trade():
    LEDGER.clear()
    m = FakeMgr(["SUCCESS"])
    _close(m)
    for _ in range(5):  # the decision loop re-fires the same close every minute
        m.codes = ["SUCCESS"]
        _close(m)
    assert len([x for x in LEDGER if x[1]]) == 1
    rows = (plog._OUT_DIR).glob("men_*.jsonl")
    assert sum(len(p.read_text().splitlines()) for p in rows) == 1


def test_B7_partial_reduce_refused_is_retried_until_done():
    m = FakeMgr(["BLOCKED_HARD_REDUCE_LOCK_11s", "ORDER_DEDUPE_BLOCK_OPEN_ORDER_EXISTS", "SUCCESS"])

    async def go():
        r = await m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="SELL", quantity=4.0, reason=VEC, is_full_close=False, action="REDUCE")
        await asyncio.gather(*[t for t in list(xg._WATCHERS.values())])
        return r

    assert run(go()) == "BLOCKED_HARD_REDUCE_LOCK_11s"
    assert m.calls.count("REDUCE") == 3 and m.broker["amt"] == 6.0 and m.force_calls == 0


def test_B8_partial_reduce_retry_stops_when_broker_shows_it_happened():
    m = FakeMgr(["UNCONFIRMED_BY_BROKER_NO_MOVE"])

    async def go():
        r = await m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="SELL", quantity=4.0, reason=VEC, is_full_close=False, action="REDUCE")
        m.broker["amt"] = 6.0  # late fill
        await asyncio.gather(*[t for t in list(xg._WATCHERS.values())])
        return r

    run(go())
    assert m.calls.count("REDUCE") == 1  # no double reduce


def test_B9_refusal_while_another_exit_call_in_flight_is_not_forced_twice():
    m = FakeMgr([])
    xg._INFLIGHT["men:XTZUSDT_LONG"] = 1  # e.g. the maker chase of the first call still running
    m.codes = ["BLOCK_SKIPPED_LOCK_ACTIVE"]

    async def go():
        r = await m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="SELL", quantity=10.0, reason=VEC, is_full_close=True, action="CLOSE")
        assert m.force_calls == 0
        xg._INFLIGHT.pop("men:XTZUSDT_LONG", None)  # owner finishes but never closed -> watcher forces
        await asyncio.gather(*[t for t in list(xg._WATCHERS.values())])
        return r

    assert run(go()) == "BLOCK_SKIPPED_LOCK_ACTIVE"
    assert m.force_calls == 1 and m.broker["amt"] == 0.0


def test_B10_force_failure_arms_watcher_then_alerts(tmp_path):
    class Stuck(FakeMgr):
        pass

    m = Stuck(["FAILED_WEBHOOK"])

    async def stuck_force(mgr, a, c, why):
        mgr.force_calls += 1
        return "FAILED_FORCE_CLOSE_STILL_OPEN|amt=10"

    hooks = xg.Hooks(force=stuck_force, broker=_h_broker, confirmed=_h_confirmed, cfg=lambda: CFG)
    global HOOKS
    old = HOOKS
    HOOKS = hooks
    try:
        async def go():
            r = await m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="SELL", quantity=10.0, reason=VEC, is_full_close=True, action="CLOSE")
            await asyncio.gather(*[t for t in list(xg._WATCHERS.values())])
            return r

        assert run(go()) == "FAILED_WEBHOOK"
    finally:
        HOOKS = old
    assert m.force_calls == 3  # immediate + 2 stuck checks
    alerts = (tmp_path / "data" / "alerts" / "exit_never_blocked.jsonl").read_text().splitlines()
    assert alerts and "still not done" in alerts[-1]
    ev = [json.loads(x)["event"] for p in (tmp_path / "data" / "safety" / "exit_events").glob("ez_*.jsonl") for x in p.read_text().splitlines()]
    assert "ESCALATE" in ev and "ALERT" in ev and "RESULT" in ev


def test_B11_entries_untouched():
    m = FakeMgr(["BLOCKED_VIGILANCE"])
    r = run(m.execute_now(position_key="men:XTZUSDT_LONG", account_key="men", symbol="XTZUSDT", position_side="LONG", side="BUY", quantity=10.0, reason=VEC, is_full_close=False, action="OPEN"))
    assert r == "BLOCKED_VIGILANCE" and m.force_calls == 0


# ───────────────────────────── C: wire exit path (order_dedupe_guard) ─────────────────────────────
@pytest.fixture
def binance(tmp_path):
    odg._BINANCE_GUARDS.clear()
    broker = FakeBinance()
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path / "odg", cfg=CFG)
    return broker, guard, odg.GuardedBinanceClient(broker, guard)


def _sell(client, qty="500", ps="LONG"):
    return client.futures_create_order(symbol="ZRXUSDT", side="SELL" if ps == "LONG" else "BUY", positionSide=ps, type="MARKET", quantity=qty)


def _full_ctx(action="CLOSE"):
    ctx = odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action=action, reason=VEC)
    return ctx


def test_C1_open_orders_unreachable_full_close_proceeds(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]

    def boom(symbol=None):
        raise ConnectionError("BROKER_UNREACHABLE")

    broker.futures_get_open_orders = boom
    with _full_ctx() as tok:
        tok["exit_priority"] = True
        _sell(client)
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_C1b_legacy_path_would_have_refused(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    broker.futures_get_open_orders = lambda symbol=None: (_ for _ in ()).throw(ConnectionError("x"))
    with _full_ctx():  # no exit_priority stamp = what every close looked like before (SNXUSDT 10-13)
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="BROKER_UNREACHABLE_OPEN_ORDERS"):
            _sell(client)


def test_C2_order_on_other_leg_never_blocks(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    broker.open_orders.append({"orderId": 9, "clientOrderId": "x", "symbol": "ZRXUSDT", "side": "SELL", "positionSide": "SHORT", "type": "LIMIT", "status": "NEW", "origQty": "10", "executedQty": "0"})
    with _full_ctx() as tok:
        tok["exit_priority"] = True
        _sell(client)
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_C3_working_order_same_leg_full_blocks_force_proceeds(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    broker.open_orders.append({"orderId": 9, "clientOrderId": "x", "symbol": "ZRXUSDT", "side": "SELL", "positionSide": "LONG", "type": "LIMIT", "status": "NEW", "origQty": "500", "executedQty": "0"})
    with _full_ctx() as tok:
        tok["exit_priority"] = True
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="EXIT_LEG_ORDER_WORKING"):
            _sell(client)
    with _full_ctx(xg.FORCE_ACTION):
        _sell(client)
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_C4_qty_clamped_to_fresh_broker_and_flat_is_nothing_to_close(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [120.0, 0.3]
    with _full_ctx(xg.FORCE_ACTION):
        _sell(client, qty="500")
    assert broker.created[-1]["quantity"] == "120"
    with _full_ctx(xg.FORCE_ACTION):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="NOTHING_TO_CLOSE"):
            _sell(client, qty="500")


def test_C5_nonfinal_ledger_row_and_unreflected_file_never_block_force(binance, monkeypatch):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    guard.ledger.mutate("ZRXUSDT", lambda o: o.append({"client_order_id": "old1", "order_id": None, "side": "SELL", "position_side": "LONG", "qty": 500, "submitted_at": 0, "state": "UNKNOWN", "final": False}))
    import positions_truth as pt
    monkeypatch.setattr(pt, "file_reflects", lambda *a, **k: (False, {"why": "stale file"}))
    with _full_ctx(xg.FORCE_ACTION):
        _sell(client)
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_C6_positionrisk_unreachable_force_still_sent(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    real = broker.futures_position_information
    broker.futures_position_information = lambda symbol=None: (_ for _ in ()).throw(TimeoutError("x"))
    with _full_ctx(xg.FORCE_ACTION):
        _sell(client)
    broker.futures_position_information = real
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_C7_entries_keep_the_legacy_guard(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN") as tok:
        tok["exit_priority"] = True  # a stamp on an INCREASING order is ignored
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="OPEN_ON_NONZERO_POSITION|POSITIONS_FILE_NOT_REFLECTED"):
            client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="10")


# ───────────────────────────── D: ez_manage source pins ─────────────────────────────
SRC = (ROOT / "ez_manage.py").read_text()


def test_D1_decorator_order():
    i = SRC.index("    async def execute_now(")
    head = SRC[max(0, i - 600):i]
    assert head.index("_odg.execute_now_gate(") < head.index("_xg.wrap_execute_now(") < head.index("_ptruth.ez_confirmed")


def test_D2_in_body_bypasses_present():
    assert "if _fx_act0 == _xg.FORCE_ACTION:" in SRC
    assert "if _wait < 90 and _xg_full:" in SRC  # BROKER_SYNC_PENDING_CONFIRMATION (90 s duplicate)
    assert "'OPEN') and _xg_full:" in SRC  # EXCHANGE_OPEN_ORDER_BLOCK
    assert "not _xg_full and not _fixed_stop_reason(reason)" in SRC  # HARD_REDUCE_LOCK (XTZUSDT FIXED_STOP)
    assert "[EXIT_PREFLIGHT_BYPASS]" in SRC  # ORDER_DEDUPE preflight BROKER_UNREACHABLE (SNXUSDT)
    assert "exit_force=True" in SRC and "def _sanction_execution(position_key, action=\"\", side=\"\", quantity=0.0, reason=\"\", exit_force=False)" in SRC


def test_D3_pre_send_ledger_logging_is_behind_the_wrapper():
    i = SRC.index("_xg_active = _xg.stash(")
    blk = SRC[i:i + 9000]
    j = blk.index("            if not _xg_active:")
    assert blk.index("_ptl_log_close(") > j and blk.index("ez_vigilance_record_close(") > j and blk.index("_stale_set_halt.record_close(") > j


def test_D4_confirmed_hook_is_the_ledger_writer():
    i = SRC.index("async def _xg_ez_confirmed(")
    blk = SRC[i:i + 6000]
    assert "idem_key=_idem" in blk and "confirmed=True" in blk and "lifecycle=_life" in blk


def test_D5_vx_timeout_leg_goes_through_execute_now_force():
    src = (ROOT / "tools" / "vx_tracked_exec.py").read_text()
    assert "action=_xg.FORCE_ACTION" in src and "trade_manager.execute_now(" in src


# ───────────────────────────── E: ez _exit_force_market on a FakeBinance ─────────────────────────────
@pytest.fixture(scope="module")
def ezm():
    return pytest.importorskip("ez_manage")


def _mgr(ezm, broker, guard, monkeypatch):
    m = object.__new__(ezm.MultiAccountTradeManager)
    m.__dict__["accounts"] = {"men": SimpleNamespace(client=odg.GuardedBinanceClient(broker, guard))}
    m.__dict__["_xg_allow_force_in_tests"] = True
    m.__dict__["get_symbol_config"] = lambda s: {"step_size": 1.0}
    monkeypatch.setattr(ezm, "is_sandbox_account", lambda *a, **k: False)
    monkeypatch.setattr(ezm, "current_env", {"env": "server"})
    monkeypatch.setattr(ezm.asyncio, "sleep", _nosleep)
    return m


async def _nosleep(*a, **k):
    return None


def test_E1_force_cancels_own_leg_order_and_closes(ezm, binance, monkeypatch):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    broker.open_orders.append({"orderId": 501, "clientOrderId": "mk", "symbol": "ZRXUSDT", "side": "SELL", "positionSide": "LONG", "type": "LIMIT", "status": "NEW", "origQty": "500", "executedQty": "0"})
    broker.orders[501] = dict(broker.open_orders[0])
    broker.open_orders.append({"orderId": 502, "clientOrderId": "sh", "symbol": "ZRXUSDT", "side": "SELL", "positionSide": "SHORT", "type": "LIMIT", "status": "NEW", "origQty": "5", "executedQty": "0"})
    broker.orders[502] = dict(broker.open_orders[1])
    m = _mgr(ezm, broker, guard, monkeypatch)

    async def go():
        with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action=xg.FORCE_ACTION):
            return await m._exit_force_market(position_key="men:ZRXUSDT_LONG", account_key="men", symbol="ZRXUSDT", position_side="LONG", reason=VEC)

    r = run(go())
    assert r.startswith("SUCCESS_FORCE_CLOSE_CONFIRMED"), r
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0
    assert broker.orders[501]["status"] == "CANCELED" and broker.orders[502]["status"] == "NEW"  # other leg untouched
    assert broker.created[-1]["type"] == "MARKET" and broker.created[-1]["positionSide"] == "LONG" and float(broker.created[-1]["quantity"]) == 500.0


def test_E2_force_with_broker_flat_is_nothing_open(ezm, binance, monkeypatch):
    broker, guard, client = binance
    m = _mgr(ezm, broker, guard, monkeypatch)

    async def go():
        with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action=xg.FORCE_ACTION):
            return await m._exit_force_market(position_key="men:ZRXUSDT_LONG", account_key="men", symbol="ZRXUSDT", position_side="LONG")

    assert run(go()) == "FORCE_NOTHING_OPEN" and broker.created == []


def test_E3_positionrisk_unreachable_falls_back_to_last_known_and_closes(ezm, binance, monkeypatch):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.3]
    real = broker.futures_position_information
    n = {"k": 0}

    def flaky(symbol=None):  # unreachable for the whole first read (4 tries), then back
        n["k"] += 1
        if n["k"] <= 4:
            raise TimeoutError("BROKER_UNREACHABLE")
        return real(symbol)

    broker.futures_position_information = flaky
    m = _mgr(ezm, broker, guard, monkeypatch)

    async def gp(pk):
        return SimpleNamespace(positionAmt=500.0)

    m.__dict__["get_position"] = gp
    monkeypatch.setattr(ezm._ptruth, "_CACHES", {}, raising=False)

    async def go():
        with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action=xg.FORCE_ACTION):
            return await m._exit_force_market(position_key="men:ZRXUSDT_LONG", account_key="men", symbol="ZRXUSDT", position_side="LONG")

    r = run(go())
    assert r.startswith("SUCCESS_FORCE_CLOSE_CONFIRMED"), r
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 0.0


def test_E4_force_branch_of_execute_now_reaches_the_helper(ezm):
    import inspect
    body = inspect.getsource(ezm.MultiAccountTradeManager.execute_now)
    assert body.index("_xg.FORCE_ACTION") < body.index("EXIT-ENGINE PARITY INSTRUMENTATION")  # before every gate


# ───────────────────────────── F: ledger ─────────────────────────────
def test_F1_logger_idempotent_and_attempts_separate():
    for _ in range(3):
        plog.log_close(account="men", symbol="LTCUSDC", side="SHORT", entry_price=44.0, exit_price=47.0, pnl_pct=-7.0, qty=0.4, reason="x", idem_key="men|LTCUSDC|SHORT|2026-10-07T20:46|FULL")
    rows = [json.loads(x) for p in plog._OUT_DIR.glob("men_*.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["idem_key"].endswith("|FULL")
    att = [json.loads(x) for p in plog._ATTEMPT_DIR.glob("men_*.jsonl") for x in p.read_text().splitlines()]
    assert [a["stage"] for a in att] == ["DUPLICATE_SUPPRESSED", "DUPLICATE_SUPPRESSED"]
    plog._idem_cache.clear()  # restart: persisted index still dedupes
    plog._idem_mtime.clear()
    assert plog.log_close(account="men", symbol="LTCUSDC", side="SHORT", entry_price=44.0, exit_price=47.0, pnl_pct=-7.0, idem_key="men|LTCUSDC|SHORT|2026-10-07T20:46|FULL") is False


def _storm(n=661, qty=0.417, px=47.0, pnl=-1.0, opened="2026-10-07T20:46:30+00:00", acct="men", sym="LTCUSDC"):
    from datetime import datetime, timedelta
    o = datetime.fromisoformat(opened)
    out = []
    for i in range(n):
        ts = o + timedelta(minutes=10 + i)
        out.append({"ts": ts.isoformat(), "account": acct, "symbol": sym, "side": "SHORT", "entry_price": 0.0, "exit_price": px, "pnl_pct": pnl, "fees_pct": 0.04, "hold_minutes": (ts - o).total_seconds() / 60.0, "qty": qty, "reason": "CYCLE_TP_TIERED_T0"})
    return out


def test_F2_retry_storm_is_one_trade_and_dust_is_not_a_loss():
    rows = _storm() + _storm(n=708, qty=10.0, px=0.3, opened="2026-10-08T03:00:00+00:00", sym="MANAUSDT")
    d = ptl.distinct_rows(rows)
    finals = ptl.final_closes(d)
    assert len({r["lifecycle"] for r in d}) == 2
    assert len(finals) == 1 and finals[0]["symbol"] == "LTCUSDC" and finals[0]["n_raw"] == 661
    assert [r for r in d if r["symbol"] == "MANAUSDT"][0]["dust"] is True


def test_F3_two_lifecycles_two_trades():
    rows = _storm(n=5) + _storm(n=5, opened="2026-10-08T01:00:00+00:00")
    assert len(ptl.final_closes(ptl.distinct_rows(rows))) == 2


def test_F4_stale_set_one_losing_close_retried_is_not_two_losers(tmp_path):
    cfg = SimpleNamespace(BASE_PATH=str(tmp_path), STALE_SET_HALT_ENABLED=False)
    for _ in range(3):
        ssh.record_close("ez", "men", "LTCUSDC", "SHORT", -2.0, "x", cfg=cfg, lifecycle="men|LTCUSDC|SHORT|2026-10-07T20:46", notional_usd=19.6)
    ssh.record_close("ez", "men", "MANAUSDT", "SHORT", -1.0, "dust", cfg=cfg, lifecycle="men|MANAUSDT|SHORT|a", notional_usd=3.0)
    closes = ssh._hook_closes("ez", cfg)
    assert len(closes["LTCUSDC_SHORT"]) == 1
    hit, _ = ssh.trailing_losses(closes["LTCUSDC_SHORT"], 0, 2)
    assert not hit
    hit2, _ = ssh.trailing_losses(closes["MANAUSDT_SHORT"] * 2, 0, 2)
    assert not hit2  # dust never counts


def test_F5_stale_set_legacy_hook_rows_of_one_retry_burst_collapse():
    rows = [{"ts": f"2026-10-14T00:{m:02d}:00Z", "account": "men", "sym_side": "X_LONG", "net_pct": -1.0} for m in (1, 2, 3, 5)]
    assert len(ssh.dedupe_hook_rows(rows)) == 1


def test_F6_vigilance_counts_a_lifecycle_once(ezm, tmp_path, monkeypatch):
    monkeypatch.setattr(ezm, "_ez_vigilance_file", lambda: tmp_path / "vig.json")
    monkeypatch.setattr(ezm, "config", SimpleNamespace(VIGILANCE_GUARD_ENABLED=True, VIGILANCE_CONSEC_LOSSES=2, EXIT_DUST_USD=5.0, BASE_PATH=str(tmp_path)))  # live config has the guard OFF; the counting must still be right when ON
    ezm._EZ_VIGILANCE_STATE.update({"loaded_at": 0.0, "blocks": {}, "streaks": {}})
    for _ in range(4):
        ezm.ez_vigilance_record_close("XTZUSDT", "LONG", -1.2, "FIXED_STOP", exit_price=0.32, lifecycle="men|XTZUSDT|LONG|2026-10-13T20:00", notional_usd=50.0)
    assert ezm._EZ_VIGILANCE_STATE["streaks"]["XTZUSDT_LONG"]["consec_losses"] == 1
    assert "XTZUSDT_LONG" not in ezm._EZ_VIGILANCE_STATE["blocks"]
    ezm.ez_vigilance_record_close("XTZUSDT", "LONG", -1.0, "dust", lifecycle="men|XTZUSDT|LONG|b", notional_usd=3.0)
    assert ezm._EZ_VIGILANCE_STATE["streaks"]["XTZUSDT_LONG"]["consec_losses"] == 1
    ezm.ez_vigilance_record_close("XTZUSDT", "LONG", -1.0, "real 2nd", lifecycle="men|XTZUSDT|LONG|c", notional_usd=50.0)
    assert "XTZUSDT_LONG" in ezm._EZ_VIGILANCE_STATE["blocks"]  # two DISTINCT losing closes still block
