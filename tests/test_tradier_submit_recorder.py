"""tests/test_tradier_submit_recorder.py — direct-dispatch recording regression.

2026-10-05 (USER immediate-repair): Oct 5 Monday stocks submitted via direct-dispatch
paths (GAP_FILL/GAP_MOC/SECTOR_HEDGE) — queue success=True in
~/logs/tradier_manage_trb.log + _trc.log — yet data/decisions/ has NO
decisions_trb/trc_20261005.jsonl. Root cause: record_decision_context was only
called on the process_position strategy-path tail (tradier_manage.py:14766);
every other queue_trade_action / execute_trade_action caller bypassed it.

Fix: queue_trade_action records on success (record_decision=True default;
strategy tail passes False + claim-skip, no dupes); execute_trade_action records
on SUCCESS unless decision_recorded (handle_order passes True). History stays
fill-time in execute (no phantom fills at queue time).

Hermetic: tmp dirs only (chdir for relative data/decisions, stub DATA_DIR for
history). Live data/ snapshot-guarded.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tradier_manage as tm

REPO = Path(__file__).resolve().parent.parent

# Strategy-path schema (utils.record_decision_context) — queue/execute records must match.
DECISION_TOP_KEYS = {"timestamp", "position_key", "account_key", "action",
                     "reason_text", "tech_score", "market_context", "indicators", "trade"}
DECISION_IND_KEYS = {"current_price", "stoch_k_5m", "stoch_d_5m", "stoch_k_15m",
                     "stoch_d_15m", "stoch_k_1h", "stoch_d_1h", "rsi_5m", "rsi_15m",
                     "ha_5m", "sentiment_rank", "sentiment_score", "volatility_atr",
                     "relative_volume"}
DECISION_TRADE_KEYS = {"qty", "price", "value", "entry_price", "gain_pct",
                       "position_amt", "action_type", "side"}
HISTORY_KEYS = {"ts", "type", "qty", "price", "value", "reason", "indicators"}


def _utc_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _live_snapshot() -> dict:
    snap = {}
    for d in (REPO / "data" / "decisions", REPO / "data" / "tradier" / "history" / "trb",
              REPO / "data" / "tradier" / "history" / "trc"):
        if d.is_dir():
            snap[str(d)] = sorted(p.name for p in d.iterdir())
    return snap


@pytest.fixture(autouse=True)
def _live_data_untouched():
    before = _live_snapshot()
    yield
    assert _live_snapshot() == before, "test touched live data/ — must use tmp dirs only"


def _stub_position_manager():
    pm = types.SimpleNamespace()
    pm.positions = {}
    pm.get_position = lambda pk: None
    return pm


def _stub_trade_manager():
    async def _gcp(symbol):
        return (100.0, "2026-10-05T00:00:00+00:00")

    mgr = types.SimpleNamespace(
        redis_manager=None,
        position_manager=_stub_position_manager(),
        get_indicators=lambda symbol: {},
        get_current_price=_gcp,
        dedupe_lock=asyncio.Lock(),
        order_deduplication={},
    )
    return mgr


@pytest.fixture()
def _queue_env(tmp_path, monkeypatch):
    """Hermetic queue env: cwd=tmp, market open, parity off."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm, "is_regular_trading_hours", lambda: True)
    # NOTE: STRICT_VEC_PARITY gate reads getattr(config_tradier module, ..., False);
    # the module has no such attr so the gate is off here (same as live import).
    mgr = _stub_trade_manager()
    oq = tm.OrderQueue(mgr)
    return oq, mgr, tmp_path


def _decision_lines(tmp_path, acct="trb"):
    p = tmp_path / "data" / "decisions" / f"decisions_{acct}_{_utc_ymd()}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _assert_decision_schema(rec: dict):
    assert DECISION_TOP_KEYS <= set(rec), f"top keys: {sorted(rec)}"
    assert DECISION_IND_KEYS <= set(rec["indicators"]), f"ind keys: {sorted(rec['indicators'])}"
    assert DECISION_TRADE_KEYS <= set(rec["trade"]), f"trade keys: {sorted(rec['trade'])}"
    assert set(rec["market_context"]) == {"bias", "is_tech"}


def test_direct_dispatch_open_appends_decision(_queue_env):
    """Replay Oct 5 GAP_FILL direct submit: queue SUCCESS must append 1 decision line."""
    oq, mgr, tmp_path = _queue_env
    res = asyncio.run(tm.queue_trade_action(
        oq, mgr, "trb:TSTREC_LONG", "OPEN", "GAP_FILL_SELL gap=1.06%", 90.0,
        override_qty=2))
    assert res == "SUCCESS"
    recs = _decision_lines(tmp_path)
    assert len(recs) == 1, f"expected exactly 1 decision line, got {len(recs)}"
    rec = recs[0]
    _assert_decision_schema(rec)
    assert rec["position_key"] == "trb:TSTREC_LONG"
    assert rec["account_key"] == "trb"
    assert rec["action"] == "OPEN"
    assert "GAP_FILL_SELL" in rec["reason_text"]
    assert rec["tech_score"] == 90.0
    # qty equals the actually-queued qty (live sizing/scaling may adjust override)
    _queued = oq._orders.get_nowait()
    assert rec["trade"]["qty"] == _queued["quantity"]
    assert rec["trade"]["price"] == 100.0
    assert rec["trade"]["action_type"] == "OPEN"
    assert rec["trade"]["side"] == "BUY"  # LONG open
    # claim left for the strategy-tail dedupe skip
    assert "trb:TSTREC_LONG" in (getattr(mgr, "_qta_decision_ts", {}) or {})


def test_refused_submit_appends_nothing(_queue_env):
    """QUEUE_DEDUPE refusal (same key+action <60s) must not append."""
    oq, mgr, tmp_path = _queue_env
    pk = "trb:DEDUP_LONG"
    r1 = asyncio.run(tm.queue_trade_action(oq, mgr, pk, "OPEN", "GAP_FILL_SELL gap=1%", 90.0, override_qty=2))
    assert r1 == "SUCCESS"
    assert len(_decision_lines(tmp_path)) == 1
    r2 = asyncio.run(tm.queue_trade_action(oq, mgr, pk, "OPEN", "GAP_FILL_SELL gap=1%", 90.0, override_qty=2))
    assert r2 is False or (isinstance(r2, str) and "REFUSED" in r2), f"unexpected: {r2!r}"
    assert len(_decision_lines(tmp_path)) == 1


def test_record_decision_false_appends_nothing(_queue_env):
    """Strategy-path tail passes record_decision=False (it records richly itself)."""
    oq, mgr, tmp_path = _queue_env
    res = asyncio.run(tm.queue_trade_action(
        oq, mgr, "trb:TAILONLY_LONG", "OPEN", "WT_DC_ENTRY_78 test", 80.0,
        override_qty=2, record_decision=False))
    assert res == "SUCCESS"
    assert _decision_lines(tmp_path) == []


def test_execute_path_helper_same_schema(_queue_env):
    """Direct-execute (PPL/ratio/scalp/daytrade) records with identical schema."""
    oq, mgr, tmp_path = _queue_env
    ok = asyncio.run(tm._record_submit_decision(
        mgr, "trb:SIMX_LONG", "CLOSE", "GAP_MOC VV exit", 5, 50.0, "SELL", 50.0))
    assert ok is True
    recs = _decision_lines(tmp_path)
    assert len(recs) == 1
    _assert_decision_schema(recs[0])
    assert recs[0]["action"] == "CLOSE"
    assert recs[0]["trade"]["qty"] == 5
    assert recs[0]["trade"]["price"] == 50.0


def test_history_writer_schema_tmp(tmp_path):
    """History writer appends same-schema fill lines (fill-time, unchanged writer)."""
    stub_self = types.SimpleNamespace(config=types.SimpleNamespace(DATA_DIR=tmp_path / "data"))
    asyncio.run(tm.TradierTradeManager._append_to_history(
        stub_self, "trb:SIMX_LONG", "OPEN", 2, 100.0, "GAP_FILL_SELL gap=1.06%"))
    p = tmp_path / "data" / "history" / "trb" / "SIMX_LONG.jsonl"
    assert p.exists()
    rec = json.loads(p.read_text().splitlines()[0])
    assert HISTORY_KEYS <= set(rec), f"history keys: {sorted(rec)}"
    assert rec["type"] == "OPEN" and rec["qty"] == 2 and rec["price"] == 100.0


def test_wiring_present_in_source():
    """Belt & braces (repo pattern): both chokepoints wired, no order logic touched."""
    src = (REPO / "tradier_manage.py").read_text()
    # queue chokepoint
    assert "async def queue_trade_action(" in src and "record_decision: bool = True" in src
    assert "await _record_submit_decision(trade_manager, position_key, action, final_reason" in src
    # strategy tail owns its record (2 sites) + claim-skip
    assert src.count("record_decision=False") >= 2
    assert "_qta_decision_ts" in src and "time.time() - _qta_claim_ts < 5.0" in src
    # execute chokepoint: success records unless queue already did
    assert "decision_recorded=False" in src
    assert "if not decision_recorded:" in src
    assert "override_qty=order.get('override_qty'),decision_recorded=True" in src
    # history still fill-time only
    assert "await self._append_to_history(position_key, action, quantity, current_price, reason)" in src
