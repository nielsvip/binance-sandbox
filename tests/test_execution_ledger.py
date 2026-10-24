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
