"""POSITIONS REVAMP 2026-10-06 — reproduces the trc IBIT_LONG incident (4 BUY 24 while long) and its crypto analogues.

Mocked brokers only (no network, no live orders). Every scenario must be PREVENTED:
  T1  four BUYs while the broker is long (REENTRY_MONITOR direct, execute_now OPEN, execute_now REENTRY, repeated)
  T2  positions file capped at $2.5k (51.6 vs broker 129): the wire decides from the broker, never from the file
  T3  POST /orders response lost -> tradier_api must NOT re-POST; the key stays frozen until the broker listing reconciles
  T4  chase: limit leg fills after the status read -> the re-place / market fallback is refused (real tradier_manage.place_order)
  C1  crypto: BUY LONG while long without token / OPEN on non-zero / augment below the gain floor
  C2  crypto: maker filled but "unverified" -> MARKET fallback inside the same execute_now is refused
  C3  zero / negative / NaN qty never sent (both venues)
  C4  a raw python-binance Client (no wrapper) is still gated (class-level guard)
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import os  # noqa: E402
os.environ.setdefault("TRADIER_API_LOG_DIR", "/tmp/tradier_api_test_logs")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402
import tradier_api  # noqa: E402

CFG = SimpleNamespace(MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0, POSITIONS_FILE_SYNC_WAIT_S=0.0)


@pytest.fixture(autouse=True)
def _hermetic_positions(tmp_path, monkeypatch):
    """Positions files / alarms / dashboard rows go to tmp (never the live data dirs); no macOS notifications."""
    monkeypatch.setenv("POSITIONS_BASE_DIR", str(tmp_path / "positions"))
    monkeypatch.setenv("POSITIONS_ALERTS_DIR", str(tmp_path / "alerts"))
    monkeypatch.setenv("POSITIONS_GUARDIAN_JSONL", str(tmp_path / "guardian.jsonl"))
    monkeypatch.setenv("POSITIONS_ALARM_NOTIFY", "0")
    yield


# ───────────────────────────── Tradier HTTP-level fake broker ─────────────────────────────
class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._text = payload if isinstance(payload, str) else json.dumps(payload)

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeTradierHTTP:
    """Speaks the Tradier REST shapes the real TradierAPIClient parses. Market orders fill instantly."""

    def __init__(self):
        self.closed = False
        self.positions = {}  # symbol -> [qty, cost_basis]
        self.quotes = {}
        self.orders = []
        self.posts = []
        self.next_id = 39828000
        self.lose_next_post_response = 0  # create the order, then raise TimeoutError (response lost)
        self.fill_limit_on_cancel = False  # limit stays "open" on status read, fills when cancel arrives
        self.limit_status_reads_stale = False

    def _fill(self, o, px):
        o.update(status="filled", exec_quantity=float(o["quantity"]), remaining_quantity=0.0, avg_fill_price=px)
        sign = 1 if o["side"] in ("buy", "buy_to_cover") else -1
        q, c = self.positions.get(o["symbol"], [0.0, 0.0])
        self.positions[o["symbol"]] = [q + sign * o["quantity"], c + sign * o["quantity"] * px]

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        path = url.split("/v1", 1)[1]
        if method == "GET" and path.endswith("/positions"):
            rows = [{"symbol": s, "quantity": q, "cost_basis": c} for s, (q, c) in self.positions.items() if q]
            return FakeResp(200, {"positions": "null"} if not rows else {"positions": {"position": rows}})
        if method == "GET" and path.endswith("/orders"):
            return FakeResp(200, {"orders": "null"} if not self.orders else {"orders": {"order": [dict(o) for o in self.orders]}})
        if method == "GET" and "/orders/" in path:
            oid = int(path.rsplit("/", 1)[1])
            o = next(x for x in self.orders if x["id"] == oid)
            if self.limit_status_reads_stale and o["type"] == "limit":
                return FakeResp(200, {"order": {**o, "status": "open", "exec_quantity": 0.0}})
            return FakeResp(200, {"order": dict(o)})
        if method == "GET" and path.startswith("/markets/quotes"):
            sym = params["symbols"]
            return FakeResp(200, {"quotes": {"quote": {"symbol": sym, **self.quotes[sym]}}})
        if method == "POST" and path.endswith("/orders"):
            self.posts.append(dict(data))
            o = {"id": self.next_id, "symbol": data["symbol"], "side": data["side"], "type": data["type"], "status": "open", "quantity": float(data["quantity"]), "exec_quantity": 0.0, "create_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
            self.next_id += 1
            self.orders.append(o)
            if o["type"] == "market":
                self._fill(o, self.quotes[o["symbol"]]["last"])
            if self.lose_next_post_response:
                self.lose_next_post_response -= 1
                raise asyncio.TimeoutError()
            return FakeResp(200, {"order": {"id": o["id"], "status": "ok"}})
        if method == "DELETE" and "/orders/" in path:
            oid = int(path.rsplit("/", 1)[1])
            o = next(x for x in self.orders if x["id"] == oid)
            if self.fill_limit_on_cancel and o["status"] == "open":
                self._fill(o, self.quotes[o["symbol"]]["last"])
                return FakeResp(400, {"errors": {"error": ["order already filled"]}})
            if o["status"] == "open":
                o["status"] = "canceled"
                return FakeResp(200, {"order": {"id": oid, "status": "ok"}})
            return FakeResp(400, {"errors": {"error": ["order not cancelable"]}})
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def tradier(tmp_path, monkeypatch):
    odg._TRADIER_GUARDS.clear()
    monkeypatch.setenv("ORDER_DEDUPE_LEDGER_DIR", str(tmp_path))
    http = FakeTradierHTTP()
    http.quotes["IBIT"] = {"bid": 48.88, "ask": 48.92, "last": 48.90}
    http.positions["IBIT"] = [57.0, 57.0 * 48.6834]
    tradier_api.TradierAPIClient._global_ban_expires = 0

    async def _connect(self):
        self.session = http

    monkeypatch.setattr(tradier_api.TradierAPIClient, "connect", _connect)
    guard = odg.get_tradier_guard("trc", CFG, ledger_dir=tmp_path)
    guard.cfg = CFG

    def make():
        c = tradier_api.TradierAPIClient(account_key="trc", override_config={"api_key": "x", "account_id": "VA0001"})
        c._min_request_interval = 0
        c.session = http
        return c

    return http, guard, make


def run(coro):
    return asyncio.run(coro)


def _buy(client, qty=24, typ="market", price=None):
    return run(client.place_order(account_key="trc", symbol="IBIT", side="buy", quantity=qty, order_type=typ, price=price))


def _blocked(res, code):
    return isinstance(res, dict) and f"ORDER_DEDUPE_BLOCK:{code}" in json.dumps(res.get("errors", ""))


def test_T1a_reentry_monitor_direct_place_order_refused(tradier):
    http, guard, make = tradier
    res = _buy(make())  # REENTRY_MONITOR 18:43:47 called place_order directly — no execute_now token
    assert _blocked(res, "NOT_VIA_EXECUTE_NOW")
    assert http.posts == []
    assert http.positions["IBIT"][0] == 57


def test_T1b_four_buys_while_long_all_refused(tradier):
    http, guard, make = tradier
    for action in ("REENTRY", "OPEN", "AUGMENT", "REENTRY"):  # the four BUY 24 IBIT orders, now each through execute_now
        with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action=action, reason="incident replay"):
            res = _buy(make())
        guard._backoff.clear()
        assert _blocked(res, "OPEN_ON_NONZERO_POSITION") or _blocked(res, "AUGMENT_GAIN_GATE"), res
    assert http.posts == []
    assert http.positions["IBIT"][0] == 57  # broker never went 57 -> 81 -> 105 -> 129


def test_T1c_open_from_zero_allowed_once_then_refused(tradier):
    http, guard, make = tradier
    http.positions.clear()
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        res = _buy(make())
    assert res["order"]["id"] and http.positions["IBIT"][0] == 24
    guard._backoff.clear()
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        res2 = _buy(make())
    assert _blocked(res2, "OPEN_ON_NONZERO_POSITION")
    assert len(http.posts) == 1


def test_T1d_augment_needs_gain_vs_last_fill_too(tradier):
    http, guard, make = tradier
    http.quotes["IBIT"] = {"bid": 50.30, "ask": 50.34, "last": 50.32}  # +3.3% vs avg 48.68 -> first augment passes
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="AUGMENT"):
        res = _buy(make())
    assert res["order"]["id"]
    run(guard.preflight(make(), "IBIT"))  # broker listing marks it FINAL + avg price
    guard._backoff.clear()
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="AUGMENT"):
        res2 = _buy(make())  # same price again: gain vs the last fill (50.32) is 0% -> refused (no cascade)
    assert _blocked(res2, "AUGMENT_GAIN_GATE") or _blocked(res2, "INTENT_ALREADY_FILLED")
    assert len(http.posts) == 1


def test_T2_capped_file_is_never_used_for_decisions(tradier, tmp_path):
    http, guard, make = tradier
    http.positions["IBIT"] = [129.0, 129.0 * 48.6834]
    capped = {"trc:IBIT_LONG": {"positionAmt": 51.6582, "augment_reason": "BROKER_SYNC_REJECTED_AUG_VALUE_CAP"}}
    (tmp_path / "long_positions.json").write_text(json.dumps(capped))
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="AUGMENT"):
        res = _buy(make())
    assert _blocked(res, "AUGMENT_GAIN_GATE")
    ev = json.loads((tmp_path / "order_dedupe_blocks.jsonl").read_text().splitlines()[-1])["evidence"]
    assert ev["broker_amt"] == 129.0  # broker truth, not the capped 51.6


def test_T3_lost_post_response_is_not_retried_and_key_freezes(tradier):
    http, guard, make = tradier
    http.positions.clear()
    http.lose_next_post_response = 1
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        res = _buy(make())
    assert len(http.posts) == 1, "tradier_api re-POSTed an order after a lost response"
    assert "order" not in res
    rows = guard.ledger.orders("IBIT")
    assert rows[-1]["state"] == "UNKNOWN"
    guard._backoff.clear()
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        res2 = _buy(make())  # next decision: listing adopts the filled order; broker now long -> OPEN refused
    assert "order" not in res2 and len(http.posts) == 1
    assert guard.ledger.orders("IBIT")[0].get("adopted") is True


def test_T3b_gateway_502_after_post_not_retried(tradier, monkeypatch):
    http, guard, make = tradier
    http.positions.clear()
    orig = http.request

    def req(method, url, **kw):
        r = orig(method, url, **kw)
        if method == "POST":
            return FakeResp(502, "Bad Gateway")
        return r

    monkeypatch.setattr(http, "request", req)
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        _buy(make())
    assert len(http.posts) == 1


def _tm_dummy(monkeypatch, make):
    import tradier_manage as tm

    monkeypatch.setattr(tm, "is_regular_trading_hours", lambda: True)
    monkeypatch.setattr(tm, "TradierAPIClient", lambda *a, **k: make())

    async def fast_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    dummy = SimpleNamespace(rejection_cooldowns={}, exceptions=set(), limit_exception_order=2500.0, limit_normal_order=2500.0, limit_exception_total_pos=2500.0, limit_total_pos=2500.0, trade_manager=None, is_symbol_tradeable=lambda *a, **k: True)
    return tm, dummy


def test_T4_chase_fill_after_status_read_market_fallback_refused(tradier, monkeypatch):
    http, guard, make = tradier
    http.positions.clear()
    http.fill_limit_on_cancel = True
    http.limit_status_reads_stale = True
    tm, dummy = _tm_dummy(monkeypatch, make)
    with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
        run(tm.TradierTradeManager.place_order(dummy, "IBIT", "BUY", 24, "market", action="OPEN", position_side="LONG", account_key="trc"))
    assert len(http.posts) == 1, f"chase re-sent after the fill: {http.posts}"
    assert http.positions["IBIT"][0] == 24


def test_T4b_reentry_monitor_no_longer_calls_place_order_directly():
    import ast

    src = Path(__file__).resolve().parents[1].joinpath("tradier_manage.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in ("place_order", "execute_now", "run"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "place_order":
                    pytest.fail(f"direct place_order in {node.name} line {sub.lineno}")


def test_T5_tradier_bad_qty_never_sent(tradier):
    http, guard, make = tradier
    http.positions.clear()
    for q in (0, -5, float("nan"), 0.4):
        with odg.execute_now_context("tradier", symbol="IBIT", position_side="LONG", action="OPEN"):
            res = _buy(make(), qty=q)
        guard._backoff.clear()
        assert _blocked(res, "BAD_QTY"), (q, res)
    assert http.posts == []


# ───────────────────────────── Binance ─────────────────────────────
class FakeBinance:
    def __init__(self):
        self.pos = {}  # (symbol, ps) -> [amt, entry]
        self.mark = {"ZRXUSDT": 0.30}
        self.orders = {}
        self.open_orders = []
        self.created = []
        self.next_id = 500
        self.fill_mode = "fill"  # fill | rest

    def futures_position_information(self, symbol=None):
        out = []
        for ps in ("LONG", "SHORT"):
            amt, entry = self.pos.get((symbol, ps), [0.0, 0.0])
            out.append({"symbol": symbol, "positionSide": ps, "positionAmt": str(amt if ps == "LONG" else -amt), "entryPrice": str(entry), "markPrice": str(self.mark[symbol])})
        return out

    def futures_get_open_orders(self, symbol=None):
        return [o for o in self.open_orders if o["symbol"] == symbol]

    def futures_get_order(self, symbol=None, orderId=None, origClientOrderId=None):
        o = self.orders.get(orderId) if orderId else next(x for x in self.orders.values() if x["clientOrderId"] == origClientOrderId)
        return dict(o)

    def futures_create_order(self, **p):
        self.created.append(dict(p))
        oid = self.next_id
        self.next_id += 1
        o = {"orderId": oid, "clientOrderId": p.get("newClientOrderId"), "symbol": p["symbol"], "side": p["side"], "positionSide": p["positionSide"], "type": p["type"], "status": "NEW", "origQty": p["quantity"], "executedQty": "0", "avgPrice": "0"}
        self.orders[oid] = o
        if self.fill_mode == "fill":
            self.fill(oid)
        else:
            self.open_orders.append(o)
        return dict(o)

    def fill(self, oid):
        o = self.orders[oid]
        o.update(status="FILLED", executedQty=o["origQty"], avgPrice=str(self.mark[o["symbol"]]))
        self.open_orders = [x for x in self.open_orders if x["orderId"] != oid]
        key = (o["symbol"], o["positionSide"])
        amt, entry = self.pos.get(key, [0.0, 0.0])
        q = float(o["origQty"])
        inc = (o["positionSide"] == "LONG") == (o["side"] == "BUY")
        self.pos[key] = [amt + q, (amt * entry + q * self.mark[o["symbol"]]) / (amt + q)] if inc else [max(0.0, amt - q), entry]

    def futures_cancel_order(self, symbol=None, orderId=None, origClientOrderId=None):
        o = self.orders[orderId]
        if o["status"] == "NEW":
            o["status"] = "CANCELED"
        self.open_orders = [x for x in self.open_orders if x["orderId"] != orderId]
        return dict(o)


@pytest.fixture
def binance(tmp_path):
    odg._BINANCE_GUARDS.clear()
    broker = FakeBinance()
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path, cfg=CFG)
    return broker, guard, odg.GuardedBinanceClient(broker, guard)


def _bcreate(client, side="BUY", ps="LONG", qty="100", typ="MARKET"):
    return client.futures_create_order(symbol="ZRXUSDT", side=side, positionSide=ps, type=typ, quantity=qty)


def test_C1_crypto_buy_while_long_refused(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.299]
    with pytest.raises(odg.OrderDedupeBlockedBinance, match="NOT_VIA_EXECUTE_NOW"):
        _bcreate(client)
    guard._backoff.clear()
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="OPEN_ON_NONZERO_POSITION"):
            _bcreate(client)
    guard._backoff.clear()
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="AUGMENT"):
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="AUGMENT_GAIN_GATE"):
            _bcreate(client)  # +0.33% < 3%
    assert broker.created == []


def test_C1b_crypto_exit_only_via_execute_now(binance):
    broker, guard, client = binance
    broker.pos[("ZRXUSDT", "LONG")] = [500.0, 0.299]
    with pytest.raises(odg.OrderDedupeBlockedBinance, match="NOT_VIA_EXECUTE_NOW"):
        _bcreate(client, side="SELL", ps="LONG", qty="500")  # PHASE 3: no path outside execute_now, exits included
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="CLOSE"):
        _bcreate(client, side="SELL", ps="LONG", qty="500")  # exit through execute_now: never blocked by the exposure gate
    assert len(broker.created) == 1


def test_C2_maker_filled_then_market_fallback_refused(binance):
    broker, guard, client = binance
    broker.fill_mode = "rest"
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        r = _bcreate(client, typ="LIMIT")  # maker leg
        broker.fill(r["orderId"])  # fills; WS/position verify lags -> legacy code fires MARKET fallback
        guard._backoff.clear()
        with pytest.raises(odg.OrderDedupeBlockedBinance, match="INTENT_ALREADY_FILLED|INTENT_FILLED_IN_CALL"):
            _bcreate(client)
    assert len(broker.created) == 1
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 100.0


def test_C2b_partial_fill_followup_clamped_to_remainder(binance):
    broker, guard, client = binance
    broker.fill_mode = "rest"
    with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
        r = _bcreate(client, typ="LIMIT", qty="100")
        o = broker.orders[r["orderId"]]
        o.update(status="CANCELED", executedQty="40", avgPrice="0.30")  # partial then cancelled
        broker.open_orders = []
        broker.pos[("ZRXUSDT", "LONG")] = [40.0, 0.30]
        guard._backoff.clear()
        broker.fill_mode = "fill"
        _bcreate(client, qty="100")  # legacy fallback asks full qty again
    assert broker.created[-1]["quantity"] == "60"
    assert broker.pos[("ZRXUSDT", "LONG")][0] == 100.0


def test_C3_crypto_bad_qty_never_sent(binance):
    broker, guard, client = binance
    for q in ("0", "-1", "nan", "0.000"):
        with odg.execute_now_context("binance", symbol="ZRXUSDT", position_side="LONG", action="OPEN"):
            with pytest.raises(odg.OrderDedupeBlockedBinance, match="BAD_QTY"):
                _bcreate(client, qty=q)
        guard._backoff.clear()
    assert broker.created == []


def test_C4_raw_python_binance_client_is_class_guarded(tmp_path, monkeypatch):
    from binance.client import Client

    assert odg.install_binance_class_guard() and getattr(Client.futures_create_order, "__odg_patched__", False)
    monkeypatch.setenv("ORDER_DEDUPE_LEDGER_DIR", str(tmp_path))
    odg._BINANCE_GUARDS.clear()
    c = Client("k" * 64, "s" * 64, ping=False)
    fake = FakeBinance()
    fake.pos[("ZRXUSDT", "LONG")] = [500.0, 0.299]
    sent = []
    monkeypatch.setattr(c, "futures_position_information", fake.futures_position_information, raising=False)
    monkeypatch.setattr(c, "futures_get_open_orders", fake.futures_get_open_orders, raising=False)
    monkeypatch.setattr(c, "_request_futures_api", lambda *a, **k: sent.append((a, k)) or {"orderId": 1, "status": "NEW", "executedQty": "0"}, raising=False)
    with pytest.raises(odg.OrderDedupeBlockedBinance, match="NOT_VIA_EXECUTE_NOW"):
        c.futures_create_order(symbol="ZRXUSDT", side="BUY", positionSide="LONG", type="MARKET", quantity="10")
    with pytest.raises(odg.OrderDedupeBlockedBinance, match="BATCH_ORDERS_NOT_ALLOWED"):
        c.futures_place_batch_order(batchOrders=[])
    assert sent == []


def test_execute_now_decorators_present():
    root = Path(__file__).resolve().parents[1]
    for f in ("ez_manage.py", "tradier_manage.py"):
        src = root.joinpath(f).read_text()
        i = src.index("    async def execute_now(")
        assert "_odg.execute_now_gate(" in src[max(0, i - 400):i], f"{f}: execute_now not wrapped by the wire token decorator"


def test_token_propagates_through_to_thread():
    seen = []
    with odg.execute_now_context("binance", symbol="X", position_side="LONG", action="OPEN"):
        asyncio.run(asyncio.to_thread(lambda: seen.append(odg.current_token())))
    assert seen and seen[0]["symbol"] == "X"
