"""execution_ledger pins (USER 2026-10-24 churn-brake root-cause fix).

The per-minute brake must count exchange-accepted fills, never raw decision
attempts: self-refused/deduped/lock-skipped retries tripped the halt on ~1
real fill (flz 05:09Z: "14 trades/min").
"""
import json
import os
from datetime import datetime, timedelta, timezone

from vec_decisions import execution_ledger as _el


def test_refused_retries_do_not_count(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_SIM_TS", raising=False)
    now = datetime.now(timezone.utc)
    for i, result in enumerate([
        "MAKER_SUCCESS filled 14.6",  # the one real fill
        "ORDER_DEDUPE_BLOCK_OPEN_ORDER_EXISTS",
        "BLOCK_SKIPPED_LOCK_ACTIVE",
        "BLOCK_SKIPPED_LOCK_ACTIVE",
        "BLOCK_SKIPPED_LOCK_ACTIVE",
        "ORDER_DEDUPE_BLOCK_OPEN_ORDER_EXISTS",
    ]):
        assert _el.append_execution(
            str(tmp_path), "flz", f"flz:SYM{i}_SHORT",
            "OPEN", "SOME_REASON", result,
        ) is True
    cutoff = now - timedelta(seconds=60)
    assert _el.count_recent_accepted(str(tmp_path), "flz", cutoff) == 1


def test_old_fills_age_out(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_SIM_TS", raising=False)
    p = _el.executions_path(str(tmp_path), "flz")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with open(p, "a") as f:
        f.write(json.dumps({"timestamp": old, "result": "MAKER_SUCCESS x"}) + "\n")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert _el.count_recent_accepted(str(tmp_path), "flz", cutoff) == 0


def test_sim_fills_never_land(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_SIM_TS", "1234567890")
    assert _el.append_execution(
        str(tmp_path), "flz", "flz:X_SHORT", "OPEN", "R", "SUCCESS",
    ) is False
    assert not os.path.exists(_el.executions_path(str(tmp_path), "flz"))


def test_missing_file_counts_zero(tmp_path):
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert _el.count_recent_accepted(str(tmp_path), "nobody", cutoff) == 0


def test_classify_routes_around_service_writes():
    assert _el.classify_history_type("OPEN", False) == "OPEN"
    assert _el.classify_history_type("VEC_DRIVEN_OPEN_PURE", False) == "OPEN"
    assert _el.classify_history_type("CLOSE", True) == "CLOSE"
    assert _el.classify_history_type("QUICK_CLOSE", False) == "CLOSE"
    assert _el.classify_history_type("REENTRY", False) == "REENTRY"
    assert _el.classify_history_type("AUGMENT", False) is None
    assert _el.classify_history_type("UAG_LADDER", False) is None
    assert _el.classify_history_type("QUICK_REDUCE_STRONG", False) is None
    assert _el.classify_history_type("HEDGE_OPEN", False) == "OPEN"


def test_open_fill_lands_in_history(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_SIM_TS", raising=False)
    assert _el.append_history_fill(
        str(tmp_path), "flz", "flz:GRAMUSDT_SHORT", "OPEN", 22.3, 1.254, "VEC_OPEN_R") is True
    p = tmp_path / "data" / "history" / "flz" / "GRAMUSDT_SHORT.jsonl"
    rec = json.loads(p.read_text().strip().splitlines()[-1])
    assert rec["type"] == "OPEN" and rec["qty"] == 22.3 and rec["reason"] == "VEC_OPEN_R"


def test_tail_guard_blocks_double_log(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_SIM_TS", raising=False)
    kw = dict(base_path=str(tmp_path), account_key="flz", position_key="flz:X_SHORT",
              hist_type="OPEN", qty=1.0, price=2.0, reason="R")
    assert _el.append_history_fill(**kw) is True
    assert _el.append_history_fill(**kw) is False  # same fill within 120 s


def test_sim_never_writes_history(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_SIM_TS", "123")
    assert _el.append_history_fill(
        str(tmp_path), "flz", "flz:X_SHORT", "OPEN", 1.0, 2.0, "R") is False
