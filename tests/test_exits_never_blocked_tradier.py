"""EXITS NEVER COMPROMISED — stocks (tradier) half (USER 2026-10-14).

Drives the exit_guarantee wrapper with the REAL tradier hooks (_xg_tr_force -> execute_now FORCE_CLOSE_MARKET, _xg_tr_confirmed ->
per_trade_logger / VIGILANCE / STALE-SET) on a fake manager whose execute_now core returns each tradier refusal code, plus the
TradierDedupeGuard wire exit path on a fake broker, plus source-level checks of every exempted mechanical guard.
Never writes into the live data/ dir (tmp dirs, EXIT_GUARANTEE_BASE)."""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import exit_guarantee as xg
import order_dedupe_guard as odg
import per_trade_logger as ptl

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "tradier_manage.py").read_text()


@pytest.fixture(scope="module")
def tm():
    import tradier_manage

    return tradier_manage


@pytest.fixture
def env(tmp_path, monkeypatch, tm):
    monkeypatch.setenv("EXIT_GUARANTEE_BASE", str(tmp_path))
    monkeypatch.setattr(ptl, "_OUT_DIR", tmp_path / "per_trade_returns")
    monkeypatch.setattr(ptl, "_ATTEMPT_DIR", tmp_path / "per_trade_attempts")
    monkeypatch.setattr(ptl, "_IDEM_DIR", tmp_path / "per_trade_returns" / "_idem")
    ptl._idem_cache.clear()
    ptl._idem_mtime.clear()
    xg._reset_for_tests()

    async def _nosleep(*a, **k):
        return None

    monkeypatch.setattr(xg.asyncio, "sleep", _nosleep)
    rec = {"vig": [], "ssh": []}
    monkeypatch.setattr(tm, "vigilance_record_close", lambda *a, **k: rec["vig"].append((a, k)) or False)
    monkeypatch.setattr(tm._stale_set_halt, "record_close", lambda *a, **k: rec["ssh"].append((a, k)) or None)
    rec["tmp"] = tmp_path
    return rec


CFG = SimpleNamespace(EXIT_GUARANTEE_ENABLED=True, EXIT_GUARANTEE_FORCE_ENABLED=True, EXIT_GUARANTEE_REDUCE_RETRY_SEC="0,0", EXIT_GUARANTEE_STUCK_CHECK_SEC="0,0", EXIT_GUARANTEE_NOTIFY=False, BASE_PATH=None)


def _make(tm, results, broker_amt=10.0):
    """Fake tradier manager: execute_now = exit_guarantee wrapper(core). core pops `results`; FORCE action -> confirmed flat."""
    calls = []
    state = {"amt": broker_amt}

    async def core(self, position_key, account_key, symbol, original_position_amt, side, position_side, quantity, old_price, unique_id, reason, is_full_close, action=None, decision_recorded=False):
        calls.append({"action": action, "reason": reason, "qty": quantity, "full": is_full_close})
        if str(action).upper() == xg.FORCE_ACTION:
            state["amt"] = 0.0
            return "SUCCESS_FORCE_CLOSE_CONFIRMED|oid=777|px=101.5|qty=10|entry=100.0"
        xg.stash(entry_price=100.0, opened_at="2026-10-14T13:31:00+00:00", gain=1.0, qty=float(quantity), price=101.0, real_amt=10.0, full_by_qty=bool(is_full_close), symbol=symbol, position_side=position_side, account=account_key, reason=reason)
        r = results.pop(0) if results else "SUCCESS"
        if str(r).startswith("SUCCESS"):
            state["amt"] = max(0.0, state["amt"] - float(quantity))
        return r

    async def broker(mgr, account_key, symbol, position_side):
        return {"amt": state["amt"], "entry": 100.0}

    hooks = xg.Hooks(force=tm._xg_tr_force, broker=broker, confirmed=tm._xg_tr_confirmed, cfg=lambda: CFG, logger=lambda: tm.logger)

    class FakeTM:
        position_manager = None
        execute_now = xg.wrap_execute_now("tradier", lambda: hooks)(core)

    return FakeTM(), calls, state


def _close(mgr, action="CLOSE", full=True, qty=10.0, reason="WT_CROSS_EXIT_1h_bear |VEC_EXACT"):
    return asyncio.run(mgr.execute_now(position_key="trb:AAPL_LONG", account_key="trb", symbol="AAPL", original_position_amt=10.0, side="SELL", position_side="LONG", quantity=qty, old_price=101.0, unique_id="T1", reason=reason, is_full_close=full, action=action))


def _ledger_rows(tmp):
    rows = []
    for p in (tmp / "per_trade_returns").glob("*.jsonl"):
        rows += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows


# every refusal tradier execute_now / place_order can return for an exit (mechanical or broker-transient)
TRADIER_REFUSALS = [
    "BLOCKED_BROKER_SYNC_STALE", "BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION", "BLOCKED_EXCHANGE_OPEN_ORDER_EXISTS", "GHOST_CLEARED_COOLDOWN",
    "REDIS_EXECUTION_LOCK_EXISTS", "LOCK_ACQUISITION_ERROR", "REDUCTION_COOLDOWN", "BLOCKED_RECENT_REDUCTION_GUARD_12s_lt_60s",
    "BLOCKED_EXIT_LH_LL_REQUIRED", "BLOCKED_STALE_MARK_PRICE_age99.0s", "BLOCKED_EMERGENCY_BRAKE_MAX_ENTRIES", "BLOCKED_EMERGENCY_BRAKE_SYMBOL_CHURN",
    "BLOCKED_QUARANTINED(FOO)", "NO_VALID_PRICE", "INVALID_QUANTITY", "ORDER_DEDUPE_BLOCK_BROKER_UNREACHABLE_ORDERS", "ORDER_DEDUPE_BLOCK_OPEN_ORDER_EXISTS",
    "ORDER_REJECTED:ORDER_DEDUPE_BLOCK:EXIT_LEG_ORDER_WORKING", "ORDER_REJECTED:WIRE_SANCTION_REFUSED", "ORDER_REJECTED:CHASE_CANCEL_UNCONFIRMED",
    "ORDER_REJECTED:POSITIONS_BROKER_UNREACHABLE", "ORDER_API_ERROR", "EXCEPTION_timeout", "UNCONFIRMED_BY_BROKER_POST_ORDER", "LOSS_PROTECTION_BLOCK", "NOLOSS_BLOCK",
]


@pytest.mark.parametrize("code", TRADIER_REFUSALS)
def test_full_close_refusal_escalates_to_force_and_logs_once(tm, env, code):
    mgr, calls, state = _make(tm, [code])
    res = _close(mgr)
    assert res.startswith("SUCCESS_FORCE_CLOSE_CONFIRMED"), (code, res)
    assert [c["action"] for c in calls] == ["CLOSE", xg.FORCE_ACTION]
    assert state["amt"] == 0.0
    rows = _ledger_rows(env["tmp"])
    assert len(rows) == 1 and rows[0]["confirmed"] and rows[0]["full_close"] and rows[0]["fill_id"] == "777"
    assert rows[0]["idem_key"].endswith("|FULL") and rows[0]["exit_price"] == 101.5 and rows[0]["entry_price"] == 100.0
    assert len(env["vig"]) == 1 and len(env["ssh"]) == 1 and env["ssh"][0][1].get("lifecycle")


def test_repeated_close_attempts_one_ledger_row(tm, env):
    mgr, calls, _ = _make(tm, ["SUCCESS", "SUCCESS", "SUCCESS"])
    for _ in range(3):
        _close(mgr)
    rows = _ledger_rows(env["tmp"])
    assert len(rows) == 1, rows  # same lifecycle FULL close re-confirmed -> written once
    att = list((env["tmp"] / "per_trade_attempts").glob("*.jsonl"))
    assert att and "DUPLICATE_SUPPRESSED" in att[0].read_text()


@pytest.mark.parametrize("code", ["BLOCKED_SERVER_HEARTBEAT_ACTIVE", "BLOCK WRONG ACCOUNT KEY"])
def test_policy_refusal_not_forced(tm, env, code):
    mgr, calls, _ = _make(tm, [code])
    assert _close(mgr) == code
    assert [c["action"] for c in calls] == ["CLOSE"]
    assert _ledger_rows(env["tmp"]) == []


def test_native_noloss_is_policy_vec_close_is_forced(tm, env):
    mgr, calls, _ = _make(tm, ["NOLOSS_BLOCK"])
    assert _close(mgr, reason="NATIVE_TP_SOMETHING") == "NOLOSS_BLOCK"
    assert [c["action"] for c in calls] == ["CLOSE"]
    mgr2, calls2, _ = _make(tm, ["NOLOSS_BLOCK"])
    assert _close(mgr2, reason="FIXED_STOP_LONG_px1_lvl2").startswith("SUCCESS_FORCE")


@pytest.mark.parametrize("code", ["REDUCTION_COOLDOWN", "ORDER_API_ERROR", "BLOCKED_STALE_MARK_PRICE_age99.0s", "ORDER_DEDUPE_BLOCK_BROKER_UNREACHABLE_ORDERS"])
def test_partial_reduce_refusal_is_retried(tm, env, code):
    mgr, calls, state = _make(tm, [code, "SUCCESS"])

    async def run():
        r = await mgr.execute_now(position_key="trb:AAPL_LONG", account_key="trb", symbol="AAPL", original_position_amt=10.0, side="SELL", position_side="LONG", quantity=4.0, old_price=101.0, unique_id="T2", reason="REDUCE_HALF |VEC_EXACT", is_full_close=False, action="REDUCE")
        t = xg._WATCHERS.get("trb:AAPL_LONG")
        assert t is not None
        await t
        return r

    assert asyncio.run(run()) == code
    assert [c["action"] for c in calls] == ["REDUCE", "REDUCE"]
    assert state["amt"] == 6.0
    rows = _ledger_rows(env["tmp"])
    assert len(rows) == 1 and not rows[0]["full_close"]


def test_classify_tradier_strings():
    for code in TRADIER_REFUSALS:
        assert xg.classify(code, "x |VEC_EXACT", True) == "REFUSED", code
    assert xg.classify("SUCCESS", "", True) == "OK"
    assert xg.classify("BLOCKED_SERVER_HEARTBEAT_ACTIVE", "", True) == "POLICY"
    assert xg.classify("FORCE_NOTHING_OPEN", "", True) == "NOOP"


# ───────────────────────── TradierDedupeGuard wire exit path ─────────────────────────
class FakeTradierInner:
    def __init__(self, held=10.0, orders=None, list_fails=False):
        self._current_id = "ACC1"
        self.held, self.orders, self.list_fails, self.placed = held, list(orders or []), list_fails, []

    async def _request(self, method, url, **kw):
        if url.endswith("/orders") and method == "GET":
            if self.list_fails:
                raise RuntimeError("gateway 502")
            return {"orders": {"order": self.orders} if self.orders else "null"}
        return {}

    async def get_account_positions(self, account_key=None):
        return [{"symbol": "AAPL", "quantity": self.held, "cost_basis": 100.0 * self.held}] if self.held else []

    async def get_quote(self, symbol):
        return {"last": 101.0, "bid": 100.9, "ask": 101.1}

    async def place_order(self, account_key, symbol, side, quantity, order_type="market", price=None, stop=None, duration="day"):
        self.placed.append({"side": side, "quantity": quantity, "type": order_type})
        return {"order": {"id": 900 + len(self.placed), "status": "ok"}}


def _guard(tmp_path):
    odg._TRADIER_GUARDS.clear()
    return odg.TradierDedupeGuard("trb", ledger_dir=tmp_path, cfg=SimpleNamespace(POSITIONS_FILE_SYNC_WAIT_S=0.0))


def _place(g, inner, action, side="sell", qty=10.0, exit_priority=True):
    async def run():
        with odg.execute_now_context("tradier", symbol="AAPL", position_side="LONG", action=action, reason="t |VEC_EXACT") as tok:
            if exit_priority:
                tok["exit_priority"] = True
            return await g.guarded_place(inner, "trb", "AAPL", side, qty, "market")

    return asyncio.run(run())


def test_wire_exit_unreachable_listing_still_places(tmp_path):
    inner = FakeTradierInner(list_fails=True)
    res = _place(_guard(tmp_path), inner, "CLOSE")
    assert res["order"]["id"] and inner.placed[0]["quantity"] == 10.0


def test_wire_exit_protective_stop_never_blocks_and_qty_clamped(tmp_path):
    inner = FakeTradierInner(held=7.0, orders=[{"id": 5, "symbol": "AAPL", "side": "sell", "type": "stop", "status": "open"}])
    res = _place(_guard(tmp_path), inner, "CLOSE", qty=10.0)
    assert res["order"]["id"] and inner.placed[0]["quantity"] == 7.0


def test_wire_exit_working_order_blocks_full_but_not_force(tmp_path):
    wo = [{"id": 6, "symbol": "AAPL", "side": "sell", "type": "limit", "status": "open"}]
    inner = FakeTradierInner(orders=wo)
    res = _place(_guard(tmp_path), inner, "CLOSE")
    assert "EXIT_LEG_ORDER_WORKING" in json.dumps(res) and not inner.placed
    inner2 = FakeTradierInner(orders=wo)
    res2 = _place(_guard(tmp_path / "f"), inner2, xg.FORCE_ACTION, exit_priority=False)
    assert res2["order"]["id"] and inner2.placed


def test_wire_exit_broker_flat_nothing_to_close(tmp_path):
    inner = FakeTradierInner(held=0.0)
    res = _place(_guard(tmp_path), inner, "CLOSE")
    assert "NOTHING_TO_CLOSE" in json.dumps(res) and not inner.placed


def test_wire_entry_unchanged_legacy_path(tmp_path):
    assert odg.tradier_exit_priority_mode("AAPL", "buy") is None
    assert odg.tradier_exit_priority_mode("AAPL", "sell", "stop") is None


# ───────────────────────── source-level: exempted mechanical guards ─────────────────────────
def _block(anchor, span=1600):
    i = SRC.index(anchor)
    return SRC[max(0, i - span):i + 200]


def test_source_decorator_order_and_force_branch():
    i = SRC.index("    async def execute_now(\n        self,\n        position_key: str,")
    head = SRC[i - 700:i]
    assert head.index("_odg.execute_now_gate(") < head.index("@_xg.wrap_execute_now(\"tradier\"") < head.index("@_ptruth.tradier_confirmed")
    body = SRC[i:i + 2500]
    assert "_xg.FORCE_ACTION" in body and "_exit_force_market_stocks" in body and "_xg_full = _xg.is_full_close(action, is_full_close)" in body


@pytest.mark.parametrize("anchor,needle", [
    ('return "BLOCKED_BROKER_SYNC_STALE"', "_age > 90 and _xg_exit"),
    ('return "BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION"', "_wait < 90 and _xg_full"),
    ('return "BLOCKED_EXCHANGE_OPEN_ORDER_EXISTS"', "if _xg_full:"),
    ('return "REDUCTION_COOLDOWN"', "and not _xg_full"),
    ('return "BLOCKED_EMERGENCY_BRAKE_MAX_ENTRIES"', "_live_eb_blocked and not _vx_ex and not _xg_full"),
    ('return f"BLOCKED_QUARANTINED({_live_q_name})"', "_live_q_blocked and not _xg_full"),
    ('return f"ORDER_DEDUPE_BLOCK_{_odg_dec.code}"', "not _odg_dec.allowed and _xg_full"),
])
def test_source_mechanical_guard_exempts_full_close(anchor, needle):
    assert needle in _block(anchor), anchor


def test_source_lock_error_never_strands_exit():
    assert 'if not _xg_exit:\n                        return "LOCK_ACQUISITION_ERROR"' in SRC


def test_source_presubmit_ledger_behind_guarantee():
    i = SRC.index("def _ptl_log_close(**_ptl_kw)")
    assert "if _xg.active():" in SRC[i:i + 600] and "_ptl_log_attempt(" in SRC[i:i + 600]
    assert "and current_price and not _xg.active():  # 2026-10-14: with the exit guarantee active the confirmed hook feeds VIGILANCE" in SRC
    assert "and current_price and not _xg.active():  # 2026-10-14: confirmed hook records once per lifecycle" in SRC


def test_source_place_order_backoff_and_full_close_mapping():
    assert re.search(r'if symbol in self\.rejection_cooldowns and str\(action or ""\)\.upper\(\) not in \{"CLOSE", "REDUCE"', SRC)
    i = SRC.index("            _EXIT_ACTIONS = {")
    assert '"FULL_CLOSE"' in SRC[i:i + 400]


def test_hooks_defined_before_main():
    assert SRC.index("_XG_TR_HOOKS = _xg.Hooks(") < SRC.index('if __name__ == "__main__":')
