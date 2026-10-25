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

Why not count data/history/ instead: crypto history records AUGMENT/REDUCE fills
only — OPEN/CLOSE are never written there (census 2026-10-24: 0 across all five
crypto accounts; the 05:09 halt burst was OPEN attempts). Stocks history IS
complete (OPEN/CLOSE/REENTRY/AUGMENT). This ledger is the only per-account source
covering every action with its outcome, on both venues.
"""

import json
import os
from datetime import datetime, timezone

_CLOSE_TYPES = frozenset({"REDUCE", "CLOSE", "GHOST_CLOSE"})
_OPEN_TYPES = frozenset({"OPEN", "AUGMENT", "REENTRY"})
_CONSERVATION_WINDOW_S = 172800.0
_CONSERVATION_MAX_LINES = 2000


def _history_rows_back(path, max_lines=_CONSERVATION_MAX_LINES):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (FileNotFoundError, OSError):
        return
    for line in reversed(lines[-max_lines:]):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            yield rec


def conservation_allows(path, trade_type, qty):
    try:
        t = str(trade_type or "").upper()
        if t not in _CLOSE_TYPES:
            return True
        q = float(qty or 0)
        if q <= 0:
            return True
        now = datetime.now(timezone.utc)
        tol = max(1e-9, q * 0.005)
        opened = 0.0
        closed = 0.0
        same_repeat = False
        newest_seen = False
        paths = [str(path)]
        bak = str(path) + ".bak"
        if os.path.exists(bak):
            paths.append(bak)
        for p in paths:
            for rec in _history_rows_back(p):
                try:
                    rts = datetime.fromisoformat(
                        str(rec.get("ts", "")).replace("Z", "+00:00")
                    )
                except Exception:
                    continue
                if (now - rts).total_seconds() > _CONSERVATION_WINDOW_S:
                    continue
                rt = str(
                    rec.get("type", rec.get("action", rec.get("event", ""))) or ""
                ).upper()
                try:
                    rq = float(rec.get("qty", 0) or 0)
                except Exception:
                    continue
                if not newest_seen:
                    newest_seen = True
                    if rt in _OPEN_TYPES:
                        return True
                if rt in _OPEN_TYPES:
                    opened += rq
                elif rt in _CLOSE_TYPES:
                    if rq > 0 and abs(rq - q) < tol:
                        same_repeat = True
                    closed += rq
        if not same_repeat:
            return True
        if opened <= 0:
            return True
        return closed + q <= opened + tol
    except Exception:
        return True


def history_write_allowed(path, trade_type, qty, price):
    try:
        t = str(trade_type or "").upper()
        if t in _CLOSE_TYPES or t in _OPEN_TYPES:
            q = round(float(qty), 6)
            try:
                with open(path, "rb") as f:
                    f.seek(max(0, os.path.getsize(path) - 8192))
                    tail = f.read().decode("utf-8", "replace").splitlines()
                now = datetime.now(timezone.utc)
                seen_close_since = False
                for line in reversed(tail[-16:]):
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        rts = datetime.fromisoformat(
                            str(rec.get("ts", "")).replace("Z", "+00:00")
                        )
                    except Exception:
                        continue
                    if (now - rts).total_seconds() > 900:
                        break
                    rt = str(rec.get("type", "") or "").upper()
                    if rt in _CLOSE_TYPES:
                        seen_close_since = True
                    match = (
                        (rt in _CLOSE_TYPES)
                        if t in _CLOSE_TYPES
                        else (rt in _OPEN_TYPES)
                    )
                    if match and abs(float(rec.get("qty", -1)) - q) < 1e-9:
                        try:
                            if abs(float(rec.get("price", -1)) - float(price)) > max(
                                1e-8, abs(float(price)) * 1e-6
                            ):
                                continue
                        except Exception:
                            continue
                        if t in _OPEN_TYPES and seen_close_since:
                            continue
                        return False
            except (FileNotFoundError, OSError):
                pass
            if t in _CLOSE_TYPES and not conservation_allows(path, trade_type, qty):
                return False
        return True
    except Exception:
        return True


def executions_path(base_path, account_key, day=None):
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(
        str(base_path), "data", "decisions", f"executions_{account_key}_{day}.jsonl"
    )


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


def classify_history_type(action, is_full_close=False):
    """Map an execute_now action to its data/history/{acct}/{SYM_SIDE}.jsonl type.

    Returns None for types the positions service already records (AUGMENT/REDUCE
    fills via ez_positions_service._append_to_history) so two writers can never
    double-log the same fill. Everything else accepted at the funnel gets a type.
    """
    act = str(action or "").upper()
    # Service-owned first: anything the positions service persists as an
    # AUGMENT/REDUCE fill (incl. UAG/HAIKU ladder families) must not be re-logged.
    if "AUGMENT" in act or "REDUCE" in act or "UAG" in act or "HAIKU" in act:
        return None
    if "REENTRY" in act:
        return "REENTRY"
    if "OPEN" in act and "CLOSE" not in act:
        return "OPEN"
    if bool(is_full_close) or "CLOSE" in act:
        return "CLOSE"
    return act[:24] if act else None


def append_history_fill(
    base_path, account_key, position_key, hist_type, qty, price, reason, venue_subdir=""
):
    """Append one accepted fill to data/history/{acct}/{SYM_SIDE}.jsonl.

    USER 2026-10-24: no positionAmt movement may go unrecorded. The funnel calls
    this for every accepted outcome the service does not already record (see
    classify_history_type). venue_subdir routes to a venue history root (stocks:
    "tradier" -> data/tradier/history, exposed under data/history via symlink);
    default "" keeps the crypto root data/history. Tail guard: same
    type+qty+price within 120 s is already on disk (service write or retry) —
    never double-log. Never raises.
    """
    try:
        if os.environ.get("V8_SIM_TS"):
            return False
        parts = str(position_key or "").split(":")
        acct = (
            str(account_key or (parts[0] if parts else "unknown")).strip() or "unknown"
        )
        symside = (parts[1] if len(parts) > 1 else str(position_key or "")).strip()
        if not symside:
            return False
        _mid = (
            ["data"]
            + ([str(venue_subdir)] if str(venue_subdir or "").strip() else [])
            + ["history"]
        )
        path = os.path.join(str(base_path), *_mid, acct, f"{symside}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        q = round(float(qty), 6)
        p = round(float(price), 8)
        now = datetime.now(timezone.utc)
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 4096))
                tail = f.read().decode("utf-8", "replace").splitlines()
            for line in reversed(tail):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                try:
                    rts = datetime.fromisoformat(
                        str(rec.get("ts", "")).replace("Z", "+00:00")
                    )
                except Exception:
                    continue
                if (now - rts).total_seconds() > 120:
                    break
                if (
                    str(rec.get("type", "")).upper() == str(hist_type).upper()
                    and abs(float(rec.get("qty", -1)) - q) < 1e-9
                    and abs(float(rec.get("price", -1)) - p) < 1e-9
                ):
                    return False
        except FileNotFoundError:
            pass
        if not history_write_allowed(path, hist_type, q, p):
            return False
        rec = {
            "ts": now.isoformat(),
            "type": hist_type,
            "qty": q,
            "price": p,
            "value": round(q * p, 2),
            "reason": str(reason or "")[:200],
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
                    if datetime.fromisoformat(ts) >= cutoff and "SUCCESS" in str(
                        rec.get("result", "")
                    ):
                        n += 1
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return n
