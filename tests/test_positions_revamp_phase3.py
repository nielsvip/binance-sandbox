"""POSITIONS REVAMP phase 3 (2026-10-06 USER) — mocked brokers only, all files/alarms in tmp.

  B1  tra hard entry ban at the CLIENT guard: no buy / sell_short / option open on tra ever (exits only), independent of
      every config switch; config can only ADD banned accounts. tradier execute_now refuses tra entries up front.
      GAP_MOC restored morning rebuys for banned accounts are dropped / never queued.
  F2  next order on a key refused until {acct}/{long|short}_positions.json shows the broker qty written after the fill;
      a stalled service (> POSITIONS_FILE_SYNC_WAIT_S) -> BROKEN POSITIONS MANAGEMENT alarm + row rewritten from the
      broker; if the rewrite cannot make the file reflect the broker the order is refused.
  E3  every order needs the execute_now token (exits too, raw client, raw POST /orders); AST scan has 0 UNGUARDED sites.
  S4  stall -> CRITICAL "BROKEN POSITIONS MANAGEMENT SCRIPT" + flag file + live-guardian row + macOS notification;
      hang reconcile and local!=broker mismatch rewrite every broker field of that sym_side.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TRADIER_API_LOG_DIR", "/tmp/tradier_api_test_logs")
sys.path.insert(0, str(ROOT))
import order_dedupe_guard as odg  # noqa: E402
import positions_truth as pt  # noqa: E402
import tradier_api  # noqa: E402

from tests.test_positions_revamp_incident import FakeBinance, FakeTradierHTTP, FakeResp, run, _hermetic_positions  # noqa: E402,F401

CFG = SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0)


def _write_pos(tmp_path, acct, key, amt, side="long", meta=True, extra=None):
    d = tmp_path / "positions" / acct
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{side}_positions.json"
    data = json.loads(f.read_text()) if f.exists() else {}
    data[key] = {"positionAmt": amt, "entry_price": 48.68, "opened_at": "2026-10-01T00:00:00Z", "max_gain": 4.2, **(extra or {})}
    f.write_text(json.dumps(data))
    if meta:
        pt.write_meta(f, "test")
    return f


@pytest.fixture
def tra(tmp_path, monkeypatch):
    odg._TRADIER_GUARDS.clear()
    http = FakeTradierHTTP()
    http.quotes["SPY"] = {"bid": 500.0, "ask": 500.1, "last": 500.05}
    http.positions["SPY"] = [10.0, 10 * 480.0]
    tradier_api.TradierAPIClient._global_ban_expires = 0

    async def _connect(self):
        self.session = http

    monkeypatch.setattr(tradier_api.TradierAPIClient, "connect", _connect)
    guard = odg.get_tradier_guard("tra", CFG, ledger_dir=tmp_path / "ledger")
    guard.cfg = CFG

    def make():
        c = tradier_api.TradierAPIClient(account_key="tra", override_config={"api_key": "x", "account_id": "VA0002"})
        c._min_request_interval = 0
        c.session = http
        return c

    _write_pos(tmp_path, "tra", "tra:SPY_LONG", 10.0)
    return http, guard, make


def _blocked(res, code):
    return isinstance(res, dict) and f"ORDER_DEDUPE_BLOCK:{code}" in json.dumps(res.get("errors", ""))


# ───────────── B1 ─────────────
@pytest.mark.parametrize("side", ["buy", "sell_short"])
def test_B1_tra_never_enters_even_via_execute_now(tra, side):
    http, guard, make = tra
    with odg.execute_now_context("tradier", symbol="SPY", position_side="LONG", action="AUGMENT"):
        res = run(make().place_order(account_key="tra", symbol="SPY", side=side, quantity=1, order_type="market"))
    assert _blocked(res, "ACCOUNT_ENTRY_BANNED")
    assert http.posts == []


def test_B1_tra_ban_survives_every_switch(tra):
    http, guard, make = tra
    guard.cfg = SimpleNamespace(ORDER_DEDUPE_GUARD_ENABLED=False, WIRE_EXPOSURE_GATE_ENABLED=False, WIRE_REQUIRE_EXECUTE_NOW_ALL=False, WIRE_FILE_REFLECTION_ENABLED=False, WIRE_ENTRY_BANNED_ACCOUNTS=(), TRA_NO_BUYS=False)
    res = run(make().place_order(account_key="tra", symbol="SPY", side="buy", quantity=1, order_type="market"))
    assert _blocked(res, "ACCOUNT_ENTRY_BANNED") and http.posts == []
    assert odg.entry_banned("tra", SimpleNamespace(WIRE_ENTRY_BANNED_ACCOUNTS=()))
    assert odg.entry_banned("trb", SimpleNamespace(WIRE_ENTRY_BANNED_ACCOUNTS=("trb",)))  # list-configurable (add only)
    assert not odg.entry_banned("trb", None)


def test_B1_tra_exit_still_allowed(tra):
    http, guard, make = tra
    with odg.execute_now_context("tradier", symbol="SPY", position_side="LONG", action="CLOSE"):
        res = run(make().place_order(account_key="tra", symbol="SPY", side="sell", quantity=10, order_type="market"))
    assert res.get("order", {}).get("id") and http.positions["SPY"][0] == 0


def test_B1_tra_option_open_banned(tra):
    http, guard, make = tra
    with odg.execute_now_context("tradier", symbol="SPY", position_side="LONG", action="OPEN"):
        res = run(make().place_option_order("tra", "SPY", "SPY261120C00500000", "buy_to_open", 1, "limit", 1.0))
    assert _blocked(res, "ACCOUNT_ENTRY_BANNED") and http.posts == []


def test_B1_binance_account_ban_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ODG_ENTRY_BANNED_ACCOUNTS", "men")
    broker = FakeBinance()
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path / "l", cfg=CFG)
    client = odg.GuardedBinanceClient(broker, guard)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="ACCOUNT_ENTRY_BANNED"):
            client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="10")
    assert broker.created == []


def test_B1_tradier_execute_now_refuses_tra_entry_up_front():
    import tradier_manage as tm

    dummy = SimpleNamespace()  # never touched: the ban returns before any state is read
    res = run(tm.TradierTradeManager.execute_now(dummy, "tra:SPY_LONG", "tra", "SPY", 0.0, "BUY", "LONG", 1.0, 500.0, "u1", "GAP_MOC morning rebuy", False, action="OPEN"))
    assert res == "BLOCKED_ACCOUNT_ENTRY_BANNED"
    res2 = run(tm.TradierTradeManager.execute_now(dummy, "tra:SPY_LONG", "tra", "SPY", 10.0, "BUY", "LONG", 1.0, 500.0, "u2", "x", False, action="REENTRY"))
    assert res2 == "BLOCKED_ACCOUNT_ENTRY_BANNED"


def test_B1_gap_moc_drops_banned_rebuys():
    src = (ROOT / "tradier_manage.py").read_text()
    i = src.index("_gap_moc_load_pending()  # 2026-09-14")
    assert "_odg.entry_banned(" in src[i:i + 600] and "_GAP_MOC_PENDING_REENTRY.pop(_ban_pk" in src[i:i + 600]
    j = src.index("for pk, info in list(_GAP_MOC_PENDING_REENTRY.items()):")
    assert "_odg.entry_banned(" in src[j:j + 300]


# ───────────── F2 ─────────────
@pytest.fixture
def bn(tmp_path):
    odg._BINANCE_GUARDS.clear()
    odg._UNCONFIRMED.clear()
    broker = FakeBinance()
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path / "l", cfg=SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.3))
    return broker, guard, odg.GuardedBinanceClient(broker, guard)


def _open_then_next(bn, tmp_path, file_amt_after_fill):
    broker, guard, client = bn
    _write_pos(tmp_path, "men", "men:ZRXUSDT_LONG", 0.0)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    if file_amt_after_fill is not None:  # the positions service writes the post-fill qty (with a fresh written_at)
        _write_pos(tmp_path, "men", "men:ZRXUSDT_LONG", file_amt_after_fill)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="REDUCE"):
        client.futures_create_order(symbol="ZRXUSDT", side="SELL", positionSide="LONG", type="MARKET", quantity="50", reduceOnly="true")
    return broker


def test_F2_file_reflects_no_alarm(bn, tmp_path):
    broker = _open_then_next(bn, tmp_path, 100.0)
    assert len(broker.created) == 2
    assert not (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag").exists()


def test_F2_stale_file_alarm_and_rewrite_then_proceed(bn, tmp_path, caplog):
    caplog.set_level(logging.CRITICAL)
    broker = _open_then_next(bn, tmp_path, None)  # service never wrote the fill
    flag = tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag"
    assert flag.exists() and json.loads(flag.read_text())["alarm"] == "BROKEN POSITIONS MANAGEMENT SCRIPT"
    assert any("BROKEN POSITIONS MANAGEMENT SCRIPT" in r.getMessage() for r in caplog.records)
    rows = [json.loads(line) for line in (tmp_path / "guardian.jsonl").read_text().splitlines()]
    assert rows[-1]["kind"] == "BROKEN_POSITIONS_MANAGEMENT" and rows[-1]["severity"] == "CRITICAL"
    f = tmp_path / "positions" / "men" / "long_positions.json"
    row = json.loads(f.read_text())["men:ZRXUSDT_LONG"]
    assert row["positionAmt"] == 100.0 and row["opened_at"] == "2026-10-01T00:00:00Z" and row["max_gain"] == 4.2  # broker fields rewritten, sacred local fields kept
    assert pt.read_written_at(f) is not None
    assert len(broker.created) == 2


def test_F2_unwritable_file_refuses_next_order(bn, tmp_path, monkeypatch):
    broker, guard, client = bn
    _write_pos(tmp_path, "men", "men:ZRXUSDT_LONG", 0.0)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    monkeypatch.setattr(pt, "rewrite_row_from_broker", lambda *a, **k: False)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="REDUCE"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="POSITIONS_FILE_NOT_REFLECTED"):
            client.futures_create_order(symbol="ZRXUSDT", side="SELL", positionSide="LONG", type="MARKET", quantity="50", reduceOnly="true")
    assert len(broker.created) == 1


def test_F2_written_before_fill_is_not_enough(bn, tmp_path):
    broker, guard, client = bn
    f = _write_pos(tmp_path, "men", "men:ZRXUSDT_LONG", 0.0)
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        client.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="100")
    data = json.loads(f.read_text())
    data["men:ZRXUSDT_LONG"]["positionAmt"] = 100.0
    f.write_text(json.dumps(data))
    meta = pt.meta_path(f)
    m = json.loads(meta.read_text())
    m["written_at"] = m["written_at"] - 30  # amount right but stamped BEFORE the fill
    meta.write_text(json.dumps(m))
    ok, ev = pt.file_reflects("binance", "men", "ZRXUSDT", "LONG", 100.0, odg._last_fill_ts(guard, "ZRXUSDT", "LONG"))
    assert not ok


def test_F2_tradier_next_order_needs_file(tra, tmp_path):
    http, guard, make = tra
    http.positions["SPY"] = [10.0, 4800.0]
    _write_pos(tmp_path, "tra", "tra:SPY_LONG", 3.0)  # file wrong vs broker 10
    guard.cfg = SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0)
    with odg.execute_now_context("tradier", symbol="SPY", position_side="LONG", action="REDUCE"):
        res = run(make().place_order(account_key="tra", symbol="SPY", side="sell", quantity=5, order_type="market"))
    assert res.get("order", {}).get("id")  # healed (alarm + rewrite) then sent
    assert (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_tra.flag").exists()


# ───────────── E3 ─────────────
def test_E3_exit_outside_execute_now_refused_both_venues(tra, tmp_path):
    http, guard, make = tra
    res = run(make().place_order(account_key="tra", symbol="SPY", side="sell", quantity=10, order_type="market"))
    assert _blocked(res, "NOT_VIA_EXECUTE_NOW") and http.posts == []
    broker = FakeBinance()
    broker.pos[("ZRXUSDT", "LONG")] = [5.0, 0.3]
    g = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path / "l2", cfg=CFG)
    with pytest.raises(odg.OrderDedupeBlockedBinance, match="NOT_VIA_EXECUTE_NOW"):
        odg.GuardedBinanceClient(broker, g).futures_create_order(symbol="ZRXUSDT", side="SELL", positionSide="LONG", type="MARKET", quantity="5", reduceOnly="true")


def test_E3_raw_request_post_orders_refused(tra):
    http, guard, make = tra
    res = run(make()._request("POST", "/accounts/VA0002/orders", data={"class": "equity", "symbol": "SPY", "side": "buy", "quantity": "1", "type": "market"}))
    assert "RAW_ORDER_POST_OUTSIDE_GUARD" in json.dumps(res) and http.posts == []


def test_E3_ast_scan_zero_unguarded():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "order_callsite_scan.py"), "--root", str(ROOT)], capture_output=True, text=True, timeout=900)
    assert "UNGUARDED (can reach a broker outside execute_now): 0" in r.stdout, r.stdout[-3000:]
    assert r.returncode == 0


# ───────────── S4 ─────────────
def test_S4_macos_notification(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("POSITIONS_ALARM_NOTIFY", "1")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: calls.append(args))
    monkeypatch.setattr(sys, "platform", "darwin")
    pt._ALARM_LAST.clear()
    pt.broken_positions_alarm("binance", "fin", "fin:X_LONG", {"why": "test"})
    assert calls and calls[0][0] == "osascript" and "display notification" in calls[0][2] and "BROKEN POSITIONS MANAGEMENT SCRIPT" in calls[0][2]


def test_S4_hang_reconcile_rewrites_every_broker_row(tmp_path):
    pt._CACHES.clear()
    _write_pos(tmp_path, "men", "men:GALAUSDT_SHORT", 0.0, side="short")
    svc = SimpleNamespace(positions_last_sync=None)
    rows = [{"symbol": "GALAUSDT", "positionSide": "SHORT", "positionAmt": "-828", "entryPrice": "0.002411"}]
    pt.hang_reconcile(svc, "men", rows, "PAU hung", SimpleNamespace(POSITIONS_HANG_EXIT_AFTER=3))
    row = json.loads((tmp_path / "positions" / "men" / "short_positions.json").read_text())["men:GALAUSDT_SHORT"]
    assert row["positionAmt"] == 828.0 and row["entry_price"] == 0.002411
    assert (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag").exists()


def test_S4_pre_order_mismatch_alarm_and_rewrite(tmp_path):
    pt._CACHES.clear()
    _write_pos(tmp_path, "men", "men:ZRXUSDT_LONG", 40.0)

    class C:
        def futures_position_information(self):
            return [{"symbol": "ZRXUSDT", "positionSide": "LONG", "positionAmt": "100", "entryPrice": "0.3"}]

    svc = SimpleNamespace(positions={"men:ZRXUSDT_LONG": SimpleNamespace(positionAmt=40.0)}, positions_last_sync=None)
    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=C())}, positions_service=svc, positions={})
    res = run(pt.ez_pre_order_check(mgr, "men", "ZRXUSDT", "men:ZRXUSDT_LONG", "LONG", "AUGMENT", "x"))
    assert res == "BLOCKED_POSITIONS_BROKER_MISMATCH"
    row = json.loads((tmp_path / "positions" / "men" / "long_positions.json").read_text())["men:ZRXUSDT_LONG"]
    assert row["positionAmt"] == 100.0
    assert (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag").exists()


# ───────────── R5: RENDER men 20:18 — real fill reported UNCONFIRMED_BY_BROKER_NO_PRE_SNAPSHOT ─────────────
def _render_env(tmp_path, monkeypatch, fill_now=True):
    odg._BINANCE_GUARDS.clear()
    odg._UNCONFIRMED.clear()
    pt._CACHES.clear()
    broker = FakeBinance()
    broker.mark["RENDERUSDT"] = 2.15
    broker.fill_mode = "fill" if fill_now else "rest"
    cfg = SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0, POSITIONS_CONFIRM_TIMEOUT_S=0.0, POSITIONS_CONFIRM_POLL_S=0.0, POSITIONS_LATE_CONFIRM_S=2.0, POSITIONS_LATE_CONFIRM_POLL_S=0.05)
    guard = odg.get_binance_guard("men", cfg, ledger_dir=tmp_path / "l")
    guard.cfg = cfg
    client = odg.GuardedBinanceClient(broker, guard)
    allpos = lambda: [r for s in ("RENDERUSDT",) for r in FakeBinance.futures_position_information(broker, s)]  # noqa: E731
    broker.futures_position_information_all = allpos
    orig = FakeBinance.futures_position_information
    monkeypatch.setattr(broker, "futures_position_information", lambda symbol=None: orig(broker, symbol) if symbol else allpos())
    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=client)}, positions_service=None, positions={})
    return broker, guard, client, mgr, cfg


def test_R5_fill_without_pre_snapshot_is_success(tmp_path, monkeypatch):
    broker, guard, client, mgr, cfg = _render_env(tmp_path, monkeypatch)

    async def core(position_key=None, account_key=None, symbol=None, position_side="LONG", quantity=0.0, action=None, reason="", is_full_close=False, side="BUY"):
        mgr.__dict__.get("_ptruth_pre", {}).clear()  # STEP2 never recorded a pre (the live failure)
        client.futures_create_order(symbol="RENDERUSDT", side="BUY", positionSide="LONG", type="LIMIT", quantity="4.3")
        return "SUCCESS"

    async def go():
        with odg.execute_now_context("binance", symbol="RENDERUSDT", position_side="LONG", action="OPEN"):
            return await pt.ez_execute_now_guarded(mgr, core, (), dict(position_key="men:RENDERUSDT_LONG", account_key="men", symbol="RENDERUSDT", position_side="LONG", quantity=4.3, action="OPEN"), config=cfg, ledger_dir=tmp_path / "l")

    assert run(go()) == "SUCCESS"
    assert odg.unconfirmed_lock("binance", "men", "RENDERUSDT", cfg) is None


def test_R5_evaluate_reconciles_post_plus_fill():
    ok, code, _ = pt.evaluate(None, 4.3, "increase", 4.3, 4.3, 1e-9)
    assert ok and code == "RECONCILED_POST_COVERS_FILL"
    ok, code, _ = pt.evaluate(None, 1.0, "increase", 4.3, 4.3, 1e-9)
    assert not ok  # broker holds less than the fill -> genuinely unconfirmed


def test_R5_maker_fill_after_return_releases_lock(tmp_path, monkeypatch):
    broker, guard, client, mgr, cfg = _render_env(tmp_path, monkeypatch, fill_now=False)
    state = {}

    async def core(position_key=None, account_key=None, symbol=None, position_side="LONG", quantity=0.0, action=None, reason="", is_full_close=False, side="BUY"):
        r = client.futures_create_order(symbol="RENDERUSDT", side="BUY", positionSide="LONG", type="LIMIT", quantity="4.3")
        state["oid"] = r["orderId"]
        return "SUCCESS"  # maker still resting when execute_now returns

    async def go():
        with odg.execute_now_context("binance", symbol="RENDERUSDT", position_side="LONG", action="OPEN"):
            res = await pt.ez_execute_now_guarded(mgr, core, (), dict(position_key="men:RENDERUSDT_LONG", account_key="men", symbol="RENDERUSDT", position_side="LONG", quantity=4.3, action="OPEN"), config=cfg, ledger_dir=tmp_path / "l")
        assert res.startswith(pt.UNCONFIRMED_PREFIX)
        assert odg.unconfirmed_lock("binance", "men", "RENDERUSDT", cfg) is not None
        await asyncio.sleep(0.1)
        broker.fill(state["oid"])  # the maker fills later
        await asyncio.wait_for(pt._LATE_TASKS["men:RENDERUSDT_LONG"], timeout=3)
        return res

    run(go())
    assert odg.unconfirmed_lock("binance", "men", "RENDERUSDT", cfg) is None


# ───────────── L6: freshness = per-entry last_updated (USER correction) — against a COPY of a real current file ─────────────
def _real_copy(tmp_path, acct="men", side="long"):
    import shutil

    src = ROOT / acct / f"{side}_positions.json"
    dst = tmp_path / "positions" / acct / f"{side}_positions.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)  # read-only use of the live file; all checks run on the copy
    return dst, json.loads(dst.read_text())


def test_L6_entry_last_updated_is_the_freshness(tmp_path):
    dst, data = _real_copy(tmp_path)
    key, row = next((k, v) for k, v in data.items() if isinstance(v, dict) and v.get("last_updated"))
    e = pt.entry_written_at(row)
    assert e is not None and abs(e - pt._epoch(row["last_updated"])) < 1e-6
    row2 = {k: v for k, v in row.items() if k != "last_updated"}
    if row2.get("mark_price_last_updated"):
        assert pt.entry_written_at(row2) == pt._epoch(row2["mark_price_last_updated"])
    assert pt.entry_written_at({}, 123.0) == 123.0  # then the file meta / sync stamp


def test_L6_fresh_entry_no_broker_poll_no_alarm(tmp_path):
    import datetime as _dt

    pt._CACHES.clear()
    calls = []

    class C:
        def futures_position_information(self):
            calls.append(1)
            return []

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    pos = SimpleNamespace(positionAmt=36.4, last_updated=now_iso)
    svc = SimpleNamespace(positions={"men:KSMUSDT_SHORT": pos}, positions_last_sync=None)  # service stamp missing -> used to be age=inf
    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=C())}, positions_service=svc, positions={})
    assert run(pt.ez_pre_order_check(mgr, "men", "KSMUSDT", "men:KSMUSDT_SHORT", "SHORT", "REDUCE", "x")) is None
    assert calls == []  # fresh entry -> no stale fallback, no alarm
    assert not (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag").exists()


def test_L6_stale_entry_still_polls_broker(tmp_path):
    pt._CACHES.clear()
    calls = []

    class C:
        def futures_position_information(self):
            calls.append(1)
            return [{"symbol": "KSMUSDT", "positionSide": "SHORT", "positionAmt": "-36.4", "entryPrice": "4.93"}]

    pos = SimpleNamespace(positionAmt=36.4, last_updated="2026-10-06T00:00:00Z")
    svc = SimpleNamespace(positions={"men:KSMUSDT_SHORT": pos}, positions_last_sync=None)
    mgr = SimpleNamespace(accounts={"men": SimpleNamespace(client=C())}, positions_service=svc, positions={})
    assert run(pt.ez_pre_order_check(mgr, "men", "KSMUSDT", "men:KSMUSDT_SHORT", "SHORT", "REDUCE", "x")) is None
    assert calls == [1]  # genuinely stale -> broker poll (amounts agree -> no alarm)
    assert not (tmp_path / "alerts" / "BROKEN_POSITIONS_MANAGEMENT_men.flag").exists()


def test_L6_file_gate_uses_row_last_updated_on_real_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_FILE_FILL_RECENT_S", str(10 ** 9))  # treat the test fills as recent whatever the copy's age
    dst, data = _real_copy(tmp_path)
    key, row = next((k, v) for k, v in data.items() if isinstance(v, dict) and v.get("last_updated"))
    acct, rest = key.split(":", 1)
    sym, ps = rest.rsplit("_", 1)
    amt = abs(float(row.get("positionAmt") or 0))
    lu = pt._epoch(row["last_updated"])
    assert not pt.meta_path(dst).exists()  # copy has no meta: row freshness alone must suffice
    ok, ev = pt.file_reflects("binance", acct, sym, ps, amt, lu - 1.0)
    assert ok, ev
    ok2, _ = pt.file_reflects("binance", acct, sym, ps, amt, lu + 60.0)  # a fill after the row was written -> not reflected
    assert not ok2
