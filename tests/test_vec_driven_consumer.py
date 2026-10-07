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


def test_config_defaults_off():
    c = C.Config()
    assert c.VEC_DRIVEN_ENABLED is False
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


def test_consumed_store_persists(tmp_path):
    s = VD.ConsumedStore(tmp_path / "c.json")
    s.mark("a")
    s.mark_reconcile("X_LONG", 123)
    s2 = VD.ConsumedStore(tmp_path / "c.json")
    assert s2.has("a") and s2.reconcile_done("X_LONG", 123) and not s2.has("b")


def test_open_caps_defaults_only_one_per_bar():
    c = VD.OpenCaps()
    t = 1_000_000 * 900.0
    for i in range(50):
        assert c.check("men", f"S{i}_LONG", 49, t + i)[0]
        c.record("men", f"S{i}_LONG", t + i)
    assert c.check("men", "S0_LONG", 0, t + 60) == (False, "ONE_OPEN_PER_SYM_SIDE_PER_BAR")
    assert c.check("men", "S0_LONG", 0, t + 900)[0]


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


def _run_exec(**kw):
    self = SimpleNamespace()
    args = dict(position_key="men:BTCUSDT_LONG", account_key="men", symbol="BTCUSDT", position_side="LONG", action="OPEN", reason="X", quantity=1.0)
    args.update(kw)
    return asyncio.run(EZ.MultiAccountTradeManager.execute_now(self, **args))


def test_native_suppressed_when_vec_driven_live(monkeypatch):
    _mode_live(monkeypatch)
    for action, reason in (("OPEN", "WT_DC_ENTRY"), ("CLOSE", "EXIT_VELOCITY_WT_1h"), ("AUGMENT", "GOLDEN_RULE_LONG"), ("REDUCE", "DC_BREACH_REDUCE")):
        assert _run_exec(action=action, reason=reason) == "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED"


def test_native_emergency_exit_allowed(monkeypatch):
    _mode_live(monkeypatch)

    def _stop(*a, **k):
        raise _Stop()
    monkeypatch.setattr(EZ, "_broker_sync_freshest_ts", _stop)
    with pytest.raises(_Stop):
        _run_exec(action="CLOSE", reason="LIQUIDATION_PROTECT", is_full_close=True)
    assert _run_exec(action="OPEN", reason="EMERGENCY_MARGIN_OPEN") == "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED"


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
    src = inspect.getsource(EZ.MultiAccountTradeManager.execute_now)
    must = [
        "if not _ok365 and _vd_exempt:",
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

class _FakeTM:
    _vec_driven_live_amt = EZ.MultiAccountTradeManager._vec_driven_live_amt
    _vec_driven_send = EZ.MultiAccountTradeManager._vec_driven_send
    _vec_driven_consumer_loop = EZ.MultiAccountTradeManager._vec_driven_consumer_loop

    def __init__(self, base, amts):
        self.config = SimpleNamespace(BASE_PATH=base, START_POSITION_SIZE=28.0)
        self.accounts = {"men": {}}
        self.min_qty = {}
        self.order_queue = object()
        self.amts = amts

    async def get_position(self, pk, max_age_s=None):
        return SimpleNamespace(positionAmt=self.amts.get(pk, 0.0), mark_price=10.0, entry_price=10.0, gain=0.0)


def _setup(tmp_path, monkeypatch, registry, amts=None, cycles=1, expires=None):
    base = tmp_path
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


def test_reconcile_once_per_bar(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    bar = time.time() - 30
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": bar, "reason": "WT"}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_GRACE_S", 0.0, raising=False)
    _run_loop(tm, monkeypatch, cycles=3)
    assert len(calls) == 1 and calls[0]["action"] == "OPEN" and "RECONCILE" in calls[0]["reason"]
    base2, vl2, tm2, calls2 = _setup(tmp_path / "b", monkeypatch, REG_LIVE, amts={"men:BTCUSDT_LONG": 5.0})
    (vl2 / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "FLAT", "bar_ts": time.time() - 30}))
    _run_loop(tm2, monkeypatch, cycles=2)
    assert [c["action"] for c in calls2] == ["CLOSE"]


def test_reconcile_no_late_open(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time() - 3600}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_GRACE_S", 0.0, raising=False)
    _run_loop(tm, monkeypatch, cycles=2)
    assert calls == []


def test_stale_intent_not_executed(tmp_path, monkeypatch):
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


def test_one_open_per_sym_side_per_bar(tmp_path, monkeypatch):
    base, vl, tm, calls = _setup(tmp_path, monkeypatch, REG_LIVE)
    now = time.time()
    _intent(vl, "a1", "BTCUSDT_LONG", "OPEN", now - 5)
    _intent(vl, "a2", "BTCUSDT_LONG", "OPEN", now - 4)
    _run_loop(tm, monkeypatch, cycles=1)
    assert len(calls) == 1


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
