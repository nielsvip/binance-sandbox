"""Per-trade returns logger — risk-zero additive instrumentation.

Writes ONE JSONL line per CLOSE/REDUCE action to:
    data/per_trade_returns/{account}_{YYYYMMDD}.jsonl

Hard rules:
  * NEVER raises. NEVER blocks the trade.
  * Best-effort only — file errors swallowed silently (logged via stderr if env var set).
  * Atomic append, flush, close on every line.
  * No live-logic dependencies — purely additive.

Usage from live managers (ez_manage.py / tradier_manage.py / scalp_v3_live.py):

    from per_trade_logger import log_close
    log_close(
        account="ang",
        symbol="BTCUSDC",
        side="LONG",
        entry_price=85000.0,
        exit_price=86000.0,
        pnl_pct=1.18,
        fees_pct=0.04,
        hold_minutes=72.5,
        qty=0.001,
        reason="WT_4H_VEL_EXIT",
    )

Caller is responsible for computing pnl_pct (live managers compute it for log lines anyway).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Path constants. BASE_PATH preferred; fall back to module-relative.
try:
    import config  # noqa: F401
    _BASE = Path(getattr(__import__("config"), "BASE_PATH", ""))
    if not _BASE or not _BASE.exists():
        raise ImportError
except Exception:
    _BASE = Path(__file__).resolve().parent

_OUT_DIR = _BASE / "data" / "per_trade_returns"

_lock = threading.Lock()


def _emit_path(account: str) -> Path:
    """Build today's JSONL path. Mkdir lazily; never raises."""
    try:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_acct = "".join(c for c in str(account or "unknown") if c.isalnum() or c in "_-") or "unknown"
    return _OUT_DIR / f"{safe_acct}_{today}.jsonl"


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
) -> bool:
    """Append one JSONL row describing a closed/reduced trade.

    Returns True on successful write, False otherwise. NEVER raises.
    """
    try:
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
