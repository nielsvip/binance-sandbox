"""X3 VEC-DRIVEN LIVE (2026-10-06): consumer idempotency, reconcile, native suppression, shadow never orders, VEC_DRIVEN reason
passes the exempted execute_now gates, safety (expiry / KILL / stale / margin floor / 1 open per bar / START notional), and
VEC_DRIVEN_ENABLED=False = byte-identical (no consumer, no exemption, no suppression)."""
import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as C
import ez_manage as EZ
from live_twins import vec_driven as VD


# ───────────────────────── pure module ─────────────────────────

def _write_registry(d: Path, entries: dict, expires=None):
    d.mkdir(parents=True, exist_ok=True)
    raw = dict(entries)
    if expires is not None:
        raw["expires_utc"] = expires
    (d / "vec_driven.json").write_text(json.dumps(raw))


def test_vec_driven_switches_exist_and_are_boolean():
    """The lane shipped default-OFF in 2026-10-06 and the operator has since armed it (the board set is what live
    trades), so asserting VEC_DRIVEN_ENABLED is False asserted the old rollout state, not a contract. What IS a
    contract: both switches exist and are real booleans, so the rollback is always one flag away."""
    c = C.Config()
    assert isinstance(c.VEC_DRIVEN_ENABLED, bool)
    assert isinstance(c.VEC_DRIVEN_EXEMPT_RATIO_GATES, bool)
    assert c.VEC_DRIVEN_EXEMPT_RATIO_GATES is False


def test_registry_modes(tmp_path):
    _write_registry(tmp_path, {"BTCUSDT_LONG": {"mode": "live", "account": "men"}, "ETHUSDT_SHORT": {"mode": "shadow", "account": ["men", "fin"]}, "XRPUSDT_LONG": {"mode": "live"}}, expires=time.time() + 3600)
    r = VD.VecDrivenRegistry(tmp_path / "vec_driven.json")
    assert r.mode("BTCUSDT_LONG", "men", False) == "off"
    assert r.mode("BTCUSDT_LONG", "men", True) == "live"
    assert r.mode("BTCUSDT_LONG", "fin", True) == "off"
    assert r.mode("ETHUSDT_SHORT", "fin", True) == "shadow"
    assert r.mode("XRPUSDT_LONG", "men", True) == "off"
    assert r.mode("NOPE_LONG", "men", True) == "off"


def test_registry_expiry_and_kill(tmp_path):
    _write_registry(tmp_path, {"BTCUSDT_LONG": {"mode": "live", "account": "men"}}, expires=time.time() - 1)
    r = VD.VecDrivenRegistry(tmp_path / "vec_driven.json")
    assert r.mode("BTCUSDT_LONG", "men", True) == "shadow"
    _write_registry(tmp_path, {"BTCUSDT_LONG": {"mode": "live", "account": "men"}})
    os.utime(tmp_path / "vec_driven.json", (time.time() + 5, time.time() + 5))
    r2 = VD.VecDrivenRegistry(tmp_path / "vec_driven.json")
    assert r2.mode("BTCUSDT_LONG", "men", True) == "live"
    assert abs(r2.expires_at() - ((tmp_path / "vec_driven.json").stat().st_mtime + 3 * 3600)) < 1
    (tmp_path / "KILL").write_text("")
    assert r2.mode("BTCUSDT_LONG", "men", True) == "shadow"


def test_translate_and_qty():
    assert VD.translate("OPEN", 0.0, 1.0) == ("OPEN", "")
    assert VD.translate("OPEN", 5.0, 1.0) == (None, "ALREADY_OPEN")
    assert VD.translate("AUGMENT", 0.0, 1.0)[0] is None
    assert VD.translate("REDUCE", 5.0, 1.0) == ("REDUCE", "")
    assert VD.translate("CLOSE", 0.5, 1.0) == (None, "ALREADY_FLAT")
    assert VD.order_qty("OPEN", 0.5, 0, 10.0) == ("OPEN", 5.0)
    assert VD.order_qty("REDUCE", 0.25, 8.0, 0) == ("REDUCE", 2.0)
    assert VD.order_qty("REDUCE", 1.0, 8.0, 0) == ("CLOSE", 8.0)
    assert VD.order_qty("CLOSE", None, 8.0, 0) == ("CLOSE", 8.0)
    assert VD.cap_open_qty(100.0, 2.0, 28.0) == 14.0


def test_reconcile_action():
    assert VD.reconcile_action(True, 0.0, 1.0) == "OPEN"
    assert VD.reconcile_action(False, 5.0, 1.0) == "CLOSE"
    assert VD.reconcile_action(True, 5.0, 1.0) is None
    assert VD.reconcile_action(None, 0.0, 1.0) is None
    assert VD.normalize_target("FLAT") is False and VD.normalize_target({"position": "LONG"}) is True and VD.normalize_target({"units": 0}) is False


def test_make_reason_neutralises_classifier_tokens():
    r = VD.make_reason("OPEN", "WT_CLOSE_REENTRY_HEDGE_WAIT_PRICE_CROSS_LIQ")
    assert r.startswith("VEC_DRIVEN_OPEN_")
    tail = r[len("VEC_DRIVEN_OPEN_"):]
    for tok in ("CLOSE", "REENTRY", "HEDGE", "WAIT", "PRICE_CROSS", "LIQ", "OPEN"):
        assert tok not in tail.upper(), tok
    assert "CLOSE" in VD.make_reason("CLOSE", "x") and VD.is_vec_driven_reason(r)


def test_consumed_store_persists_on_flush(tmp_path):
    """USER 2026-10-22: marks are in-memory and the pass flushes once. mark() used to rewrite the whole ledger every
    call, which is why the accounts with the biggest ledgers (men 819 KB, fin 321 KB) stopped trading entirely."""
    s = VD.ConsumedStore(tmp_path / "c.json")
    s.mark("a")
    s.mark_reconcile("X_LONG", 123)
    # Visible to this process at once — idempotency within the pass does not wait for disk.
    assert s.has("a") and s.reconcile_done("X_LONG", 123)
    # Not yet on disk: one write per pass is the whole point.
    assert not VD.ConsumedStore(tmp_path / "c.json").has("a")
    s.flush()
    s2 = VD.ConsumedStore(tmp_path / "c.json")
    assert s2.has("a") and s2.reconcile_done("X_LONG", 123) and not s2.has("b")
    s.flush()  # no-op when nothing was marked since the last flush


def test_open_caps_bar_cap_needs_a_real_position():
    """USER 2026-10-22: the per-bar cap refuses a second open only when the account actually HOLDS the key.
    Flat (live_amt 0, or unknown) + the vector says open -> never refused, however many times this bar."""
    c = VD.OpenCaps()
    t = 1_000_000 * 900.0
    for i in range(50):
        assert c.check("men", f"S{i}_LONG", 49, t + i)[0]
        c.record("men", f"S{i}_LONG", t + i)
    assert c.check("men", "S0_LONG", 0, t + 60, live_amt=5.0, min_qty=1.0) == (False, "ALREADY_OPEN_SAME_BAR")
    assert c.check("men", "S0_LONG", 0, t + 60, live_amt=0.0, min_qty=1.0)[0]
    assert c.check("men", "S0_LONG", 0, t + 60)[0]
    assert c.check("men", "S0_LONG", 0, t + 900, live_amt=5.0, min_qty=1.0)[0]


def test_intent_age():
    now = 1_791_000_000.0
    assert VD.intent_age_s({"bar_ts": now - 100}, now) == 100
    assert VD.intent_age_s({"bar_ts": (now - 100) * 1000}, now) == 100
    assert VD.intent_age_s({}, now) == float("inf")


# ───────────────────────── execute_now gate (top) ─────────────────────────

class _Stop(BaseException):
    pass


def _mode_live(monkeypatch, on=True, mode="live"):
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_ENABLED", on, raising=False)
    monkeypatch.setattr(EZ, "_vec_driven_mode", lambda s, side, a: mode if on else "off")


def _run_exec(_position_gain=None, **kw):
    """execute_now became a thin wrapper around _execute_now_inner (the 2026-10-17 entry-stop requirement), so the
    stub self must carry the inner method for the wrapper to call — otherwise every gate assertion below dies on
    AttributeError before reaching a gate."""
    self = SimpleNamespace()
    self._execute_now_inner = lambda **a: EZ.MultiAccountTradeManager._execute_now_inner(self, **a)
    # USER 2026-10-24 VEC-LAST: orders travel past the early chokepoint to the late
    # backstop, so the stub carries the production-present methods the path touches.
    async def _stub_load_tradeable():
        return []
    self.load_tradeable = _stub_load_tradeable
    self.redis_manager = None
    self._allowed_accounts = {"men", "fin", "flz", "inf", "ang"}
    self.tradeable_keys = set()
    self._last_tradeable_update = 0.0
    self.tradeable_lock = None
    self.tracker_manager = None
    self.positions_service = SimpleNamespace(positions={}, positions_by_account={})

    async def _stub_get_position(position_key):
        return None
    self.get_position = _stub_get_position
    # A FRESH key per call: execute_now's same-key dedup claim (_peg.try_claim, TTL in seconds) is process-global, so
    # reusing one position_key made the second entry of a test — or of the next test — return BLOCKED_PARITY_DEDUP
    # instead of reaching the gate under test.
    _run_exec.n = getattr(_run_exec, "n", 0) + 1
    _sym = f"T{_run_exec.n}USDT"
    args = dict(position_key=f"men:{_sym}_LONG", account_key="men", symbol=_sym, position_side="LONG", action="OPEN", reason="X", quantity=1.0)
    args.update(kw)
    if _position_gain is not None:
        _position = SimpleNamespace(gain=float(_position_gain), positionAmt=1.0)
        self.positions_service.positions[args["position_key"]] = _position
        self.positions_service.positions_by_account[args["account_key"]] = {
            args["position_key"]: _position
        }
    # USER 2026-10-24 vector-authority: the key under test is tradeable (live converge keys always are) so gate
    # assertions exercise decision vetoes, not the sacred-list hard block.
    self.tradeable_keys = {args["position_key"]}
    self.min_qty = {}
    return asyncio.run(EZ.MultiAccountTradeManager.execute_now(self, **args))


def test_native_suppressed_when_vec_driven_live(monkeypatch):
    """A native decision never trades a vec-driven key. Since 2026-10-21 the vector-copy enforcer (bar window / killed
    family) usually refuses it FIRST, so the contract is the refusal, not which gate got there first.
    USER 2026-10-24 VEC-LAST: native gates act before the VEC suppress, so a native
    refusal string also satisfies the contract — the order must never trade.
    USER 2026-10-24 pending-confirm: DEFERRED_VEC_CONFIRM_WAIT equally satisfies it — a held order executes
    nothing; the vec's own later dictate is the only thing that can trade the key."""
    _mode_live(monkeypatch)
    for action, reason in (("OPEN", "WT_DC_ENTRY"), ("CLOSE", "EXIT_VELOCITY_WT_1h"), ("AUGMENT", "GOLDEN_RULE_LONG"), ("REDUCE", "DC_BREACH_REDUCE")):
        res = _run_exec(action=action, reason=reason)
        assert res.startswith(("BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED", "BLOCKED_VECTOR_EXEC_", "POLICY_NON_VECTOR_EXIT_DISABLED", "DEFERRED_VEC_CONFIRM_WAIT", "BLOCKED_")), f"{action}/{reason} -> {res}"


def test_native_emergency_exit_allowed(monkeypatch):
    _mode_live(monkeypatch)

    def _stop(*a, **k):
        raise _Stop()
    _orig_bsts = EZ._broker_sync_freshest_ts
    monkeypatch.setattr(EZ, "_broker_sync_freshest_ts", _stop)
    # A rolling vector-convergence close is authoritative even when the live position is below breakeven.
    # It still traverses the ordinary execute_now path; the broker-sync sentinel proves it passed the net-gain gate.
    from live_twins import vec_provenance
    token = vec_provenance.mark("men:T5USDT_LONG", time.time() - 900, "CLOSE", "VEC_DRIVEN_STATE")
    try:
        with pytest.raises(_Stop):
            _run_exec(action="CLOSE", reason="VEC_DRIVEN_CLOSE_RECONCILE_", is_full_close=True, _position_gain=-0.25)
        with pytest.raises(_Stop):
            _run_exec(action="CLOSE", reason="VEC_DRIVEN_CLOSE_RECONCILE_", is_full_close=True, _position_gain=0.25)
    finally:
        vec_provenance.reset(token)
    # USER 2026-10-24 VEC-LAST: the OPEN now traverses native gates (incl. broker sync)
    # before the late VEC suppress, so the probe is scoped to the CLOSE it instruments.
    monkeypatch.setattr(EZ, "_broker_sync_freshest_ts", _orig_bsts)
    res = _run_exec(action="OPEN", reason="EMERGENCY_MARGIN_OPEN")
    assert res.startswith("BLOCKED_"), res


def test_vec_driven_reason_passes_365d(monkeypatch):
    _mode_live(monkeypatch)
    monkeypatch.setattr(EZ, "_confirm_365d_allows", lambda s, side: (False, "no record"))

    def _stop(*a, **k):
        raise _Stop()
    monkeypatch.setattr(EZ, "_broker_sync_freshest_ts", _stop)
    with pytest.raises(_Stop):
        _run_exec(action="OPEN", reason="VEC_DRIVEN_OPEN_WT_DC")


def test_default_off_byte_identical(monkeypatch):
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_ENABLED", False, raising=False)
    called = []
    monkeypatch.setattr(EZ, "_vec_driven_mode", lambda *a: called.append(a) or "live")
    monkeypatch.setattr(EZ, "_confirm_365d_allows", lambda s, side: (False, "no record"))
    assert _run_exec(action="OPEN", reason="VEC_DRIVEN_OPEN_WT_DC") == "BLOCKED_NO_365D_CONFIRM"
    assert called == []
    EZ._VEC_DRIVEN_REGISTRY = None
    monkeypatch.undo()
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_ENABLED", False, raising=False)
    assert EZ._vec_driven_mode("BTCUSDT", "LONG", "men") == "off"
    assert EZ._VEC_DRIVEN_REGISTRY is None


def test_exempt_gates_wired_in_source():
    # execute_now is now a thin wrapper (2026-10-17 entry-stop requirement); the gates live in _execute_now_inner.
    src = inspect.getsource(EZ.MultiAccountTradeManager._execute_now_inner)
    must = [
        # USER 2026-10-24 pending-confirm: the 365d exemption is master-gated (_vo_armed = VEC_DRIVEN on AND vec
        # reason) so VEC_DRIVEN_ENABLED=False stays byte-identical; the vec path through the gate is preserved.
        "if not _ok365 and (_vd_exempt or _vo_armed):",
        "if not _live_ok and _vd_exempt:",
        'not _lane_b_vec_only_parity_ok(symbol, position_side, reason or "") and not _vd_exempt',
        "if _ot_max > 0 and not _ot_emerg and not _vd_exempt:",
        '"DAEMON" in (reason or "").upper() or _vd_exempt',
        "not _is_reentry_preflight and not _vd_exempt",
        "not _rrg_is_price_cross_reentry and not _vd_exempt",
        "not _nb_dc_broken and not _nb_emergency and not _vd_exempt",
        "_vd_exempt  # X3",
        "and not _vd_exempt\n                    and position.gain < 0.6",
    ]
    for m in must:
        assert m in src, m
    eta = inspect.getsource(EZ.MultiAccountTradeManager.execute_trade_action)
    assert "and not _vd_ratio_exempt" in eta


# ───────────────────────── consumer loop ─────────────────────────

class _AlwaysPriced(dict):
    """Price cache stub: every symbol has a usable price, so tests exercise decisions, not a missing-feed path."""

    def get(self, key, default=None):
        return {"price": 10.0}


class _FakeTM:
    _vec_driven_live_amt = EZ.MultiAccountTradeManager._vec_driven_live_amt
    _vec_driven_live_amt_guarded = EZ.MultiAccountTradeManager._vec_driven_live_amt_guarded
    _vec_driven_send = EZ.MultiAccountTradeManager._vec_driven_send
    _vec_driven_consumer_loop = EZ.MultiAccountTradeManager._vec_driven_consumer_loop

    def __init__(self, base, amts):
        self.config = SimpleNamespace(BASE_PATH=base, START_POSITION_SIZE=28.0)
        self.accounts = {"men": {}}
        self.min_qty = {}
        self.order_queue = object()
        self.amts = amts
        # USER 2026-10-22: the consumer reads the position writer's dict and the in-memory price cache — never the
        # broker and never an awaited price — so the harness models those, not get_position.
        self.positions = {pk: SimpleNamespace(positionAmt=v, mark_price=10.0, entry_price=10.0, gain=0.0) for pk, v in (amts or {}).items()}
        self.price_cache = _AlwaysPriced()
        self.mark_price_cache = {}

    async def get_position(self, pk, max_age_s=None):
        return SimpleNamespace(positionAmt=self.amts.get(pk, 0.0), mark_price=10.0, entry_price=10.0, gain=0.0)

    async def load_tradeable(self):
        """USER 2026-10-23 close-only keys: reconcile consults this; default keeps every
        pre-existing test's key tradeable, individual tests narrow it."""
        return list(getattr(self, "tradeable_keys", {"men:BTCUSDT_LONG"}))

    async def execute_trade_action(self, account_key=None, position_key=None, symbol=None, quantity=0.0, current_price=0.0, side=None, position_side=None, unique_id=None, is_full_close=False, action="", reason="", override_qty=None, **kw):
        """USER 2026-10-22 no-queue path: a vec dictate goes straight to execute_trade_action -> execute_now, so the
        harness records HERE instead of at queue_trade_action (which only the VEC_DRIVEN_SEND_VIA_QUEUE rollback uses)."""
        self.calls_sink.append({"pk": position_key, "action": action, "reason": reason, "qty": override_qty if override_qty is not None else quantity, "side": side, "is_full_close": is_full_close})
        return f"SUCCESS_{action}"


def _setup(tmp_path, monkeypatch, registry, amts=None, cycles=1, expires=None):
    base = tmp_path
    base.mkdir(parents=True, exist_ok=True)
    (base / "symbols_men_long.json").write_text(json.dumps(["BTCUSDT"]))
    (base / "tradeable_keys.json").write_text(json.dumps(["men:BTCUSDT_LONG"]))
    vl = base / "data" / "vec_live"
    (vl / "intents").mkdir(parents=True, exist_ok=True)
    (vl / "state").mkdir(parents=True, exist_ok=True)
    _write_registry(vl, registry, expires=time.time() + 3600 if expires is None else expires)
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_ENABLED", True, raising=False)
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_CONSUMER_INTERVAL_S", 0.0, raising=False)
    calls = []

    async def fake_queue(oq, tm, pk, action, reason, conviction=0.5, override_qty=None, account_key=None):
        calls.append({"pk": pk, "action": action, "reason": reason, "qty": override_qty})
        return f"QUEUED_{action}_X"

    async def fake_price(sym):
        return 10.0

    async def fake_cfoq(*a, **k):
        return 100.0
    monkeypatch.setattr(EZ, "queue_trade_action", fake_queue)
    monkeypatch.setattr(EZ, "quick_price", fake_price)
    monkeypatch.setattr(EZ, "calculate_final_order_quantity", fake_cfoq)
    tm = _FakeTM(base, amts or {})
    tm.calls_sink = calls
    return base, vl, tm, calls


def _run_loop(tm, monkeypatch, cycles=1):
    real_sleep = asyncio.sleep
    n = {"i": 0}

    async def fake_sleep(s):
        n["i"] += 1
        if n["i"] > cycles:
            raise asyncio.CancelledError()
        await real_sleep(0)
    monkeypatch.setattr(EZ.asyncio, "sleep", fake_sleep)
    asyncio.run(tm._vec_driven_consumer_loop())
    monkeypatch.setattr(EZ.asyncio, "sleep", real_sleep)


def _intent(vl, iid, ss, typ, bar_ts, **kw):
    rec = {"intent_id": iid, "ss": ss, "bar_ts": bar_ts, "type": typ, "reason": "WT_DC_ENTRY", "qty_frac": 1.0, "mode": "live", "target_state": "LONG"}
    rec.update(kw)
    (vl / "intents" / f"{iid}.json").write_text(json.dumps(rec))


REG_LIVE = {"BTCUSDT_LONG": {"mode": "live", "account": "men"}}


def test_live_open_idempotent_and_start_capped(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    _intent(vl, "i1", "BTCUSDT_LONG", "OPEN", time.time() - 10)
    _run_loop(tm, monkeypatch, cycles=3)
    assert len(calls) == 1
    c = calls[0]
    assert c["action"] == "OPEN" and c["reason"].startswith("VEC_DRIVEN_OPEN_") and c["pk"] == "men:BTCUSDT_LONG"
    assert c["qty"] == pytest.approx(28.0 / 10.0)
    tm2 = _FakeTM(base, {})
    _run_loop(tm2, monkeypatch, cycles=2)
    assert len(calls) == 1
    assert VD.ConsumedStore(vl / "consumed_men.json").has("i1")


def test_live_reduce_and_close(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE, amts={"men:BTCUSDT_LONG": 8.0})
    now = time.time()
    _intent(vl, "r1", "BTCUSDT_LONG", "REDUCE", now - 5, qty_frac=0.25)
    _intent(vl, "c1", "BTCUSDT_LONG", "CLOSE", now - 4)
    _run_loop(tm, monkeypatch, cycles=1)
    assert [(c["action"], c["qty"]) for c in calls] == [("REDUCE", 2.0), ("CLOSE", 8.0)]
    assert calls[0]["reason"].startswith("VEC_DRIVEN_REDUCE_") and calls[1]["reason"].startswith("VEC_DRIVEN_CLOSE_")


def test_shadow_never_orders(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, {"BTCUSDT_LONG": {"mode": "shadow", "account": "men"}})
    bar = time.time() - 1000
    _intent(vl, "s1", "BTCUSDT_LONG", "OPEN", bar)
    hist = base / "data" / "history" / "men"
    hist.mkdir(parents=True)
    from datetime import datetime, timezone
    (hist / "BTCUSDT_LONG.jsonl").write_text(json.dumps({"ts": datetime.fromtimestamp(bar + 60, timezone.utc).isoformat(), "type": "AUGMENT", "reason": "GOLDEN_RULE_LONG"}) + "\n")
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time()}))
    _run_loop(tm, monkeypatch, cycles=2)
    assert calls == []
    rows = [json.loads(x) for x in (vl / "shadow_compare_men.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["would_action"] == "OPEN" and rows[0]["native_matches"] is True


def test_reconcile_retries_open_while_flat_but_closes_once(tmp_path, monkeypatch):
    """USER 2026-10-22: anything the board holds MUST have a live position, no exceptions — so an OPEN toward a target
    the board still holds retries while the key is flat (throttled by VEC_DRIVEN_RECONCILE_GRACE_S, here 0 to expose
    every attempt). A CLOSE keeps exactly ONE attempt per bar: repeated close attempts are how double closes happen."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    bar = time.time() - 30
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": bar, "reason": "WT"}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_GRACE_S", 0.0, raising=False)
    _run_loop(tm, monkeypatch, cycles=3)
    assert len(calls) == 3, calls
    assert all(c["action"] == "OPEN" and "RECONCILE" in c["reason"] for c in calls), calls
    # Close side: one attempt per bar, however many cycles run.
    base2, vl2, tm2, calls2 = _setup(tmp_path / "b", monkeypatch, REG_LIVE, amts={"men:BTCUSDT_LONG": 5.0})
    (vl2 / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "FLAT", "bar_ts": time.time() - 30}))
    _run_loop(tm2, monkeypatch, cycles=3)
    assert [c["action"] for c in calls2] == ["CLOSE"], calls2
    # And the per-bar law is still one flag away for OPENs too.
    base3, vl3, tm3, calls3 = _setup(tmp_path / "c", monkeypatch, REG_LIVE)
    (vl3 / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time() - 30, "reason": "WT"}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_RETRY_IN_BAR", False, raising=False)
    _run_loop(tm3, monkeypatch, cycles=3)
    assert len(calls3) == 1, calls3


def test_reconcile_late_open_executes_by_default(tmp_path, monkeypatch):
    """USER 2026-10-21/22 standing orders (STALENESS NEVER REFUSES; a key still flat toward a target the board
    still holds retries, no exceptions): a reconcile OPEN executes even when the decision bar is 3600s old.
    Replaces test_reconcile_no_late_open, which pinned the repealed refusal law."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time() - 3600}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_GRACE_S", 0.0, raising=False)
    _run_loop(tm, monkeypatch, cycles=2)
    assert any(c.get("action") == "OPEN" for c in calls), calls


@pytest.mark.parametrize("source", ["intent", "reconcile"])
def test_non_tradeable_side_book_blocks_vec_open_before_execute_now(tmp_path, monkeypatch, source):
    """The side book remains authoritative even when the generic tradeable key is present."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    tm.tradeable_keys = {"men:BTCUSDT_LONG"}
    (base / "symbols_men_long.json").write_text("[]")
    (base / "tradeable_keys.json").write_text(json.dumps(["men:BTCUSDT_LONG"]))
    if source == "intent":
        _intent(vl, "side-book-denied", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    else:
        (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time() - 30}))
    _run_loop(tm, monkeypatch, cycles=2)
    assert calls == []
    if source == "intent":
        assert not VD.ConsumedStore(vl / "consumed_men.json").has("side-book-denied")


def test_stale_intent_executes_late_by_default(tmp_path, monkeypatch):
    """USER 2026-10-21 standing order (late dictates still trade, ZERO_PASS_ALLOW): a 400s-old intent executes
    by default. Replaces the refusal half of test_stale_intent_not_executed (repealed law); the lever arm below
    keeps refusal coverage through the sanctioned switch."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    _intent(vl, "old", "BTCUSDT_LONG", "OPEN", time.time() - 400)
    _run_loop(tm, monkeypatch, cycles=1)
    assert any(c.get("action") == "OPEN" for c in calls), calls
    assert VD.ConsumedStore(vl / "consumed_men.json").has("old")


def test_stale_intent_refuses_with_stale_refuses_lever(tmp_path, monkeypatch):
    """VEC_DRIVEN_STALE_REFUSES=1 restores the old stale refusal (consumed without order) — the sanctioned lever
    for operators who want it. Default stays execute-late (previous test)."""
    monkeypatch.setenv("VEC_DRIVEN_STALE_REFUSES", "1")
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    _intent(vl, "old", "BTCUSDT_LONG", "OPEN", time.time() - 400)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == [] and VD.ConsumedStore(vl / "consumed_men.json").has("old")


def test_expired_or_killed_no_orders(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE, expires=time.time() - 5)
    _intent(vl, "e1", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == []
    base2, vl2, tm2, calls2 = _setup(tmp_path / "k", monkeypatch, REG_LIVE)
    (vl2 / "KILL").write_text("")
    _intent(vl2, "k1", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    _run_loop(tm2, monkeypatch, cycles=1)
    assert calls2 == []


def test_margin_floor_refuses_open(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    (base / "data" / "HALT_TRADING_men").write_text("")
    _intent(vl, "m1", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == []
    rows = [json.loads(x) for x in (vl / "live_exec_men.jsonl").read_text().splitlines()]
    assert rows[-1]["result"] == "REFUSED_MARGIN_FLOOR"


def test_foreign_account_intent_ignored_here(tmp_path, monkeypatch):
    """USER 2026-10-23 vec-native parity: one process serves ONE account, so an intent file addressed
    to another account must neither execute nor refuse here — the owning process owns it (19,902
    cross-account ledger rows all-time, incl. false NON_TRADEABLE and BROKER_UNREACHABLE breaches).
    An absent account field (legacy executor files) stays fail-open and still executes."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    now = time.time()
    _intent(vl, "own1", "BTCUSDT_LONG", "OPEN", now - 5, account="men", reason="OWN_LEG")
    _intent(vl, "fr1", "BTCUSDT_LONG", "OPEN", now - 5, account="fin", reason="FOREIGN_LEG")
    _intent(vl, "legacy1", "BTCUSDT_LONG", "OPEN", now - 4, reason="LEGACY_LEG")
    _run_loop(tm, monkeypatch, cycles=1)
    reasons = [c["reason"] for c in calls]
    assert any("OWN_LEG" in r for r in reasons), reasons
    assert any("LEGACY_LEG" in r for r in reasons), reasons
    assert not any("FOREIGN_LEG" in r for r in reasons), reasons
    rows = [json.loads(x) for x in (vl / "live_exec_men.jsonl").read_text().splitlines()]
    assert "fr1" not in {r.get("intent_id") for r in rows}, rows
    assert not VD.ConsumedStore(vl / "consumed_men.json").has("fr1")


def test_load_intents_oldest_bar_first(tmp_path):
    """USER 2026-10-23 vec-native parity: bar precedence is oldest-first (a newest-first flip was
    tried and reverted same day — it lets a newer OPEN dispatch before an older CLOSE and close the
    fresh re-entry). Backlog latency is owned by the exterminator + load, not by sort order."""
    d = tmp_path / "intents"
    d.mkdir()
    now = time.time()
    for i, age in enumerate((3000, 100, 10000)):
        (d / f"PB-men-AAAUSDT_LONG-{i}.json").write_text(json.dumps({
            "intent_id": f"ID-{i}", "ss": "AAAUSDT_LONG", "type": "OPEN", "bar_ts": now - age}))
    assert [r["intent_id"] for r in VD.load_intents(d)] == ["ID-2", "ID-0", "ID-1"]


def test_boot_stagger_spreads_accounts_and_disables(monkeypatch):
    """USER 2026-10-23 ban root-cause: deterministic per-account boot delay spreads REST
    bootstrap bursts; env kill switch disables; unknown accounts get no delay."""
    assert EZ._boot_stagger_s("ang") == 0.0
    assert EZ._boot_stagger_s("fin") == 15.0
    assert EZ._boot_stagger_s("flz") == 30.0
    assert EZ._boot_stagger_s("inf") == 45.0
    assert EZ._boot_stagger_s("men") == 60.0
    assert EZ._boot_stagger_s("nobody") == 0.0
    monkeypatch.setenv("VEC_BOOT_STAGGER_S", "0")
    assert EZ._boot_stagger_s("men") == 0.0


def test_open_on_leftover_single_waitfree_read_then_proceeds():
    """USER 2026-10-24 unreachable-real-fix: ONE wait-free slot read, no sleep-retry on the order path, and an
    unknown broker truth PROCEEDS as OPEN (post-trade confirmation reconciles a real leftover) — the
    BLOCKED_POSITIONS_BROKER_UNREACHABLE refusal is gone from ez. Leftover-to-AUGMENT conversion stays."""
    src = Path(EZ.__file__).read_text()
    i_snap = src.index('snapshot(reason=f"open_on_leftover {position_key}")')
    i_proceed = src.index("[OPEN_ON_LEFTOVER_UNVERIFIED_PROCEED]")
    seg = src[i_snap:i_proceed]
    assert "while _lo_snap is None" not in seg and "await asyncio.sleep" not in seg and "open_on_leftover_retry" not in seg, seg[-400:]
    assert '"BLOCKED_POSITIONS_BROKER_UNREACHABLE"' not in src, "the lie refuses nowhere in ez"
    assert "OPEN_ON_LEFTOVER_TO_AUGMENT" in src[i_proceed:], "leftover conversion intact"


def test_dictates_not_refused_while_flat(tmp_path, monkeypatch):
    """USER 2026-10-22: the per-bar cap is only correct WHEN A POSITION ACTUALLY EXISTS. Two OPEN dictates on a FLAT
    key both reach the order path — a vector decision is never refused by a counter. (Old law: exactly 1 per bar,
    which left the key flat for the whole bar whenever the first attempt was refused downstream.)"""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    now = time.time()
    _intent(vl, "a1", "BTCUSDT_LONG", "OPEN", now - 5)
    _intent(vl, "a2", "BTCUSDT_LONG", "OPEN", now - 4)
    _run_loop(tm, monkeypatch, cycles=1)
    assert len(calls) == 2, calls


def test_same_bar_duplicate_dictates_collapse(tmp_path, monkeypatch):
    """The lane writes one intent file per (account, sym_side, bar), so N files for the same sym_side+bar+type are ONE
    vector decision: collapsed in the consumer, not refused by a cap downstream."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    bar = time.time() - 30
    _intent(vl, "V20-ang-BTCUSDT_LONG-OPEN", "BTCUSDT_LONG", "OPEN", bar)
    _intent(vl, "V20-men-BTCUSDT_LONG-OPEN", "BTCUSDT_LONG", "OPEN", bar)
    _intent(vl, "V20-inf-BTCUSDT_LONG-OPEN", "BTCUSDT_LONG", "OPEN", bar)
    _run_loop(tm, monkeypatch, cycles=1)
    assert len(calls) == 1, calls


def test_legacy_crypto_producer_intent_is_consumed_without_order(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    _intent(vl, "PB-men-BTCUSDT_LONG-OPEN", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == []
    assert VD.ConsumedStore(vl / "consumed_men.json").has("PB-men-BTCUSDT_LONG-OPEN")
    rows = [json.loads(line) for line in (vl / "live_exec_men.jsonl").read_text().splitlines()]
    assert any(row.get("result") == "REFUSED_LEGACY_CRYPTO_PRODUCER" for row in rows)


def test_invalid_forward_state_refuses_old_open_intent(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"ss": "BTCUSDT_LONG", "status": "NO_ACTIVE_SET", "actionable": False, "written_at": time.time()}))
    _intent(vl, "old-v20-open", "BTCUSDT_LONG", "OPEN", time.time() - 5)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == []
    assert VD.ConsumedStore(vl / "consumed_men.json").has("old-v20-open")


def test_vec_close_is_never_dropped_when_any_source_shows_a_position(tmp_path, monkeypatch):
    """MONEY BUG 2026-10-22: a vec CLOSE was skipped as SKIPPED_ALREADY_FLAT on keys the account demonstrably held
    (7 measured: ang:EGLDUSDT_SHORT 32.4, ang:TRUMPUSDC_SHORT 3.48, flz:XRPUSDC_SHORT 18.6, inf:ZENUSDT_SHORT 1.0,
    …), leaving positions open against the vector's decision. The amount must be the LARGEST of every source, so a
    position we might hold is never read as flat. Here the broker-backed read says flat and only the in-memory dict
    knows about it — the CLOSE must still go."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)

    async def _flat_broker(pk, max_age_s=None):
        return SimpleNamespace(positionAmt=0.0, mark_price=10.0, entry_price=10.0, gain=0.0)
    tm.get_position = _flat_broker
    tm.positions = {"men:BTCUSDT_LONG": SimpleNamespace(positionAmt=4.0, mark_price=10.0, entry_price=10.0, gain=0.0)}
    _intent(vl, "c1", "BTCUSDT_LONG", "CLOSE", time.time() - 10, target_state="FLAT", qty_frac=1.0)
    _run_loop(tm, monkeypatch, cycles=1)
    assert [c["action"] for c in calls] == ["CLOSE"], calls
    assert calls[0]["qty"] == 4.0, calls


def test_open_dictate_sends_nothing_when_already_held(tmp_path, monkeypatch):
    """The only correct refusal of an OPEN: the account already holds the key (translate -> ALREADY_OPEN)."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE, amts={"men:BTCUSDT_LONG": 5.0})
    _intent(vl, "h1", "BTCUSDT_LONG", "OPEN", time.time() - 10)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == [], calls


def test_consumer_not_started_when_off():
    src = inspect.getsource(EZ)
    i = src.index("asyncio.create_task(trade_manager._vec_driven_consumer_loop())")
    assert 'if bool(getattr(_ezm_base_config, "VEC_DRIVEN_ENABLED", False)):' in src[i - 400:i]


def test_executor_doc_format_and_dict_target(tmp_path, monkeypatch):
    """S1 tools/vec_live_executor.py writes {ss, bar_ts, mode, intents:[...], target_state:{side, qty_units, entry_ts...}}."""
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    now = time.time()
    tgt = {"side": "long", "qty_units": 3.0, "entry_ts": now - 60, "base_size": 28.0}
    doc = {"ss": "BTCUSDT_LONG", "bar_ts": now - 900, "mode": "live", "target_state": tgt, "intents": [{"intent_id": "abc", "ss": "BTCUSDT_LONG", "bar_ts": now - 900, "type": "OPEN", "reason": "WT_DC", "target_state": tgt, "qty_frac": 1.0, "mode": "live", "account": "men", "emitted_at": now - 20}]}
    (vl / "intents" / "BTCUSDT_LONG_x.json").write_text(json.dumps(doc))
    assert [i["intent_id"] for i in VD.load_intents(vl / "intents")] == ["abc"]
    assert VD.normalize_target(tgt) is True and VD.normalize_target({"side": "flat"}) is False
    _run_loop(tm, monkeypatch, cycles=1)
    assert len(calls) == 1 and calls[0]["action"] == "OPEN"


def test_native_unconfirmed_held_for_vec_confirm(monkeypatch):
    """USER 2026-10-24 pending-confirm: a native fire the vec hasn't confirmed waits (flagged for switch
    creation) instead of dying outright — the vec may simply be behind live."""
    _mode_live(monkeypatch)
    res = _run_exec(action="OPEN", reason="WT_DC_ENTRY")
    assert res == "DEFERRED_VEC_CONFIRM_WAIT", res


def test_board_converge_open_bypasses_board_entry_block(monkeypatch):
    """USER 2026-10-24 vector-authority: a vec-dictated force-open carries the board's own decision, so the
    live-set-vs-board entry block must record-and-continue (named for the late VEC_LAST backstop), never
    hard-refuse before it. The synthetic key has NO_LATEST_BOARD, which exercises the block."""
    _mode_live(monkeypatch)
    # The execution path past the late backstop touches broker/lock methods the stub does not carry: any
    # AttributeError from those proves every decision gate (GATES-A, tradeable, board-entry) let the
    # vec-dictated force-open through — a hard refusal would RETURN a string, never raise. Pre-fix this
    # returns BLOCKED_GATES_A_* and raises nothing.
    # USER 2026-10-24 unreachable-real-fix: the record-and-continue marker is source-pinned (fallthrough-audit
    # pattern), not log-asserted — TradeLogger_* sets propagate=False (10-13 NEVER-AGAIN) so caplog can never
    # see it, and the pytest file handlers filter harness records by account.
    with pytest.raises(AttributeError):
        _run_exec(action="OPEN", reason="BOARD_CONVERGE_OPEN vec=VEC_DRIVEN_TEST")
    src = Path(EZ.__file__).read_text()
    seg = src[src.index("CRYPTO_BOARD_ENTRY_BLOCK"):src.index("CRYPTO_BOARD_ENTRY_BLOCK") + 2000]
    assert '_vec_last_bypassed.append("BOARD_ENTRY_BLOCK")' in seg
    assert "[BOARD_ENTRY_VEC_CONTINUE]" in seg


def test_vec_exact_open_bypasses_board_entry_block_with_vec_driven_off(monkeypatch):
    """A PARITY_VEC_EXACT order remains vector-dictated even when the VEC_DRIVEN master is off."""
    _mode_live(monkeypatch, on=False)
    monkeypatch.setattr(EZ._ezm_base_config, "PARITY_VEC_EXACT_MODE", True, raising=False)
    monkeypatch.setattr(EZ.config, "CRYPTO_BOARD_ENTRY_BLOCK", True, raising=False)
    from live_twins import vec_exact

    monkeypatch.setattr(vec_exact, "latest_board_ok", lambda *args: (False, "NO_LATEST_BOARD"))
    with pytest.raises(AttributeError):
        _run_exec(action="OPEN", reason="VEC_EXACT_TEST |VEC_EXACT")
