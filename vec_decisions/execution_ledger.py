"""execution_ledger — exchange-accepted order outcomes for rate brakes.

USER 2026-10-24: the per-minute churn brake counted raw lines in
data/decisions/decisions_<acct>_<date>.jsonl, which records every DECISION
attempt — including self-refused, deduped and lock-skipped retries. Measured:
flz hit "14 trades/min" on ~1 real fill + a burst of ORDER_DEDUPE_BLOCK /
BLOCK_SKIPPED_LOCK_ACTIVE refusals. A halt meant for runaway live order flow
was tripped by the engine arguing with its own locks.

So: execute_trade_action appends one line per COMPLETED execute_now call here,
and the per-minute brake counts only exchange-accepted outcomes ("SUCCESS" in
result — the same acceptance token the WH_TRACE logs use). Hourly entry/total
guards stay on the decisions file (decision-storm detection, unchanged).
Backtest/sim fills never land here (callers gate on V8_SIM_TS).
"""
import json
import os
from datetime import datetime, timezone


def executions_path(base_path, account_key, day=None):
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(str(base_path), "data", "decisions",
                        f"executions_{account_key}_{day}.jsonl")


def append_execution(base_path, account_key, position_key, action, reason, result):
    """One line per completed execute_now. Never raises (logging must not break trading)."""
    try:
        if os.environ.get("V8_SIM_TS"):
            return False
        path = executions_path(base_path, account_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position_key": position_key,
            "account": account_key,
            "action": action,
            "reason": str(reason or "")[:200],
            "result": str(result or "")[:200],
        }
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return True
    except Exception:
        return False


def count_recent_accepted(base_path, account_key, cutoff, day=None):
    """Exchange-accepted executions at/after cutoff (datetime). Never raises."""
    n = 0
    try:
        with open(executions_path(base_path, account_key, day), errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200000))
            if size > 200000:
                f.readline()
            for line in f:
                try:
                    rec = json.loads(line)
                    ts = rec.get("timestamp", "").replace("Z", "+00:00")
                    if datetime.fromisoformat(ts) >= cutoff and "SUCCESS" in str(rec.get("result", "")):
                        n += 1
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return n
