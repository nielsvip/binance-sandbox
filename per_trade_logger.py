"""Per-trade returns logger — risk-zero additive instrumentation.

Writes ONE JSONL line per CONFIRMED close/reduce fill to:
    data/per_trade_returns/{account}_{YYYYMMDD}.jsonl
and every retry / unconfirmed attempt to:
    data/per_trade_attempts/{account}_{YYYYMMDD}.jsonl

2026-10-14 (USER "ledger relogging ok, but make sure at least EXITS will never be compromised again"): the live managers
used to call log_close BEFORE the order was sent, once per attempt — a close retried every minute became 600+ "trades"
(LTCUSDC_SHORT men 661 rows for one lifecycle) and one losing close could count as two consecutive losers in the
loss guards. Now:
  * live managers call log_close only from the exit-guarantee confirmed hook (broker-confirmed fill), with
    idem_key = account | position lifecycle | fill id (or FULL for the lifecycle's full close);
  * a repeated idem_key is NEVER written to the trade ledger again (persisted index, survives restarts) — it goes to the
    attempts log as DUPLICATE_SUPPRESSED;
  * log_attempt() records unconfirmed attempts separately (diagnostics only, never read by the loss guards).
Readers that need distinct trades use per_trade_ledger (dedupe-on-read also covers the legacy rows written before this).

Hard rules:
  * NEVER raises. NEVER blocks the trade.
  * Best-effort only — file errors swallowed silently (logged via stderr if env var set).
  * Atomic append, flush, close on every line.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# Path constants. BASE_PATH preferred; fall back to module-relative.
try:
    import config  # noqa: F401
    _BASE = Path(getattr(__import__("config"), "BASE_PATH", ""))
    if not _BASE or not _BASE.exists():
        raise ImportError
except Exception:
    _BASE = Path(__file__).resolve().parent

_OUT_DIR = _BASE / "data" / "per_trade_returns"
_ATTEMPT_DIR = _BASE / "data" / "per_trade_attempts"
_IDEM_DIR = _OUT_DIR / "_idem"
_IDEM_KEEP_S = 45 * 86400.0

_lock = threading.Lock()
_idem_cache: Dict[str, Dict[str, float]] = {}
_idem_mtime: Dict[str, float] = {}


def _safe_acct(account) -> str:
    return "".join(c for c in str(account or "unknown") if c.isalnum() or c in "_-") or "unknown"


def _emit_path(account: str, base: Optional[Path] = None) -> Path:
    """Build today's JSONL path. Mkdir lazily; never raises."""
    d = base or _OUT_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return d / f"{_safe_acct(account)}_{today}.jsonl"


def _idem_path(account: str) -> Path:
    return _IDEM_DIR / f"{_safe_acct(account)}.jsonl"


def _idem_load(account: str) -> Dict[str, float]:
    """Persisted idempotency index (append-only jsonl {k, ts}); reloaded when another process appended."""
    acct = _safe_acct(account)
    p = _idem_path(acct)
    try:
        mt = p.stat().st_mtime
    except Exception:
        mt = 0.0
    if acct in _idem_cache and _idem_mtime.get(acct) == mt:
        return _idem_cache[acct]
    keys: Dict[str, float] = {}
    cutoff = time.time() - _IDEM_KEEP_S
    try:
        with open(p) as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                    if float(e.get("ts", 0)) >= cutoff:
                        keys[str(e.get("k"))] = float(e.get("ts", 0))
                except Exception:
                    continue
    except Exception:
        pass
    _idem_cache[acct] = keys
    _idem_mtime[acct] = mt
    return keys


def seen(account: str, idem_key: str) -> bool:
    try:
        with _lock:
            return str(idem_key) in _idem_load(account)
    except Exception:
        return False


def _idem_claim(account: str, idem_key: str) -> bool:
    """True = first time this key is claimed (caller writes the trade row). flock-guarded across processes."""
    acct = _safe_acct(account)
    p = _idem_path(acct)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p) + ".lock", "a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                _idem_mtime.pop(acct, None)
                keys = _idem_load(acct)
                if str(idem_key) in keys:
                    return False
                ts = time.time()
                with open(p, "a") as fh:
                    fh.write(json.dumps({"k": str(idem_key), "ts": ts}) + "\n")
                keys[str(idem_key)] = ts
                try:
                    _idem_mtime[acct] = p.stat().st_mtime
                except Exception:
                    pass
                return True
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except Exception:
        return True  # index unavailable: never lose a confirmed trade row


def log_attempt(*, account: Optional[str], symbol: Optional[str], side: Optional[str], action: Optional[str] = None, reason: Optional[str] = None, qty: Optional[float] = None, price: Optional[float] = None, stage: str = "ATTEMPT", result: Optional[str] = None, idem_key: Optional[str] = None, lifecycle: Optional[str] = None, extra: Optional[dict] = None) -> bool:
    """Diagnostics only: one line per close attempt / retry / suppressed duplicate. NEVER read by loss guards."""
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "account": str(account or ""), "symbol": str(symbol or ""), "side": str(side or "").upper(), "action": str(action or ""), "reason": str(reason or "")[:200], "qty": float(qty) if qty is not None else 0.0, "price": float(price) if price is not None else 0.0, "stage": stage, "result": str(result or "")[:200], "idem_key": idem_key or "", "lifecycle": lifecycle or ""}
        if extra:
            rec.update({k: v for k, v in extra.items() if k not in rec})
        line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
        path = _emit_path(account or "unknown", _ATTEMPT_DIR)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        return True
    except Exception:
        return False


def log_close(
    *,
    account: Optional[str],
    symbol: Optional[str],
    side: Optional[str],
    entry_price: Optional[float],
    exit_price: Optional[float],
    pnl_pct: Optional[float],
    fees_pct: Optional[float] = 0.0,
    hold_minutes: Optional[float] = None,
    qty: Optional[float] = None,
    reason: Optional[str] = None,
    idem_key: Optional[str] = None,
    lifecycle: Optional[str] = None,
    fill_id: Optional[str] = None,
    full_close: Optional[bool] = None,
    confirmed: Optional[bool] = None,
    notional_usd: Optional[float] = None,
) -> bool:
    """Append one JSONL row describing a closed/reduced trade.

    idem_key given and already logged -> NOT written (attempts log gets DUPLICATE_SUPPRESSED), returns False.
    Returns True on successful write, False otherwise. NEVER raises.
    """
    try:
        if idem_key and not _idem_claim(account or "unknown", idem_key):
            log_attempt(account=account, symbol=symbol, side=side, reason=reason, qty=qty, price=exit_price, stage="DUPLICATE_SUPPRESSED", idem_key=idem_key, lifecycle=lifecycle)
            return False
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account": str(account) if account is not None else "",
            "symbol": str(symbol) if symbol is not None else "",
            "side": str(side or "").upper(),
            "entry_price": float(entry_price) if entry_price is not None else 0.0,
            "exit_price": float(exit_price) if exit_price is not None else 0.0,
            "pnl_pct": float(pnl_pct) if pnl_pct is not None else 0.0,
            "fees_pct": float(fees_pct) if fees_pct is not None else 0.0,
            "hold_minutes": float(hold_minutes) if hold_minutes is not None else 0.0,
            "qty": float(qty) if qty is not None else 0.0,
            "reason": str(reason or ""),
        }
        if idem_key:
            rec["idem_key"] = str(idem_key)
        if lifecycle:
            rec["lifecycle"] = str(lifecycle)
        if fill_id:
            rec["fill_id"] = str(fill_id)
        if full_close is not None:
            rec["full_close"] = bool(full_close)
        if confirmed is not None:
            rec["confirmed"] = bool(confirmed)
        if notional_usd is not None:
            rec["notional_usd"] = round(float(notional_usd), 4)
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        path = _emit_path(account or "unknown")
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        return True
    except Exception as exc:
        if os.environ.get("PER_TRADE_LOGGER_DEBUG"):
            try:
                sys.stderr.write(f"[per_trade_logger] WRITE_FAIL {exc!r}\n")
            except Exception:
                pass
        return False
