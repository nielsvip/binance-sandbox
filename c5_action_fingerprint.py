"""C5 exact-action fingerprint.

The c4 fingerprint intentionally remains available in ``param_results_store``.
New c5 evidence uses this stronger fingerprint so sizing, partial realization,
cash-flow, close-reason, and exposure differences cannot masquerade as inert
knobs merely because entry/exit timestamps and percentage P&L match.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping


C5_ACTION_FINGERPRINT_VERSION = "exact-actions-v2"

_FLOAT_FIELDS = (
    "entry_price",
    "exit_price",
    "pnl_pct",
    "pnl_usd",
    "requested_open_qty",
    "executed_open_qty",
    "requested_close_qty",
    "executed_close_qty",
    "gross_cash_flow",
    "net_cash_flow",
    "partial_cash_flow",
    "exposure_qty_seconds",
    "exposure_notional_seconds",
)


def _finite_float(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _canonical_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": int(_finite_float(event.get("timestamp"))),
        "action": str(event.get("action") or "").upper(),
        "side": str(event.get("side") or "").upper(),
        "position_side": str(event.get("position_side") or "").upper(),
        "reason": str(event.get("reason") or ""),
        "requested_qty": round(
            _finite_float(event.get("requested_qty")), 8
        ),
        "executed_qty": round(
            _finite_float(
                event.get("executed_qty", event.get("quantity"))
            ),
            8,
        ),
        "price": round(_finite_float(event.get("price")), 8),
        "cash_flow": round(_finite_float(event.get("cash_flow")), 8),
        "is_full_close": bool(event.get("is_full_close")),
    }


def canonical_trade_action(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable c5 identity-bearing subset of one realized round."""
    out: dict[str, Any] = {
        "symbol": str(trade.get("symbol") or "").upper(),
        "side": str(trade.get("side") or "").upper(),
        "entry_ts": int(_finite_float(trade.get("entry_ts"))),
        "exit_ts": int(_finite_float(trade.get("exit_ts"))),
        "entry_reason": str(trade.get("entry_reason") or ""),
        "exit_reason": str(trade.get("exit_reason") or ""),
        "partial_close_count": int(
            _finite_float(trade.get("partial_close_count"))
        ),
    }
    for field in _FLOAT_FIELDS:
        out[field] = round(_finite_float(trade.get(field)), 8)
    events = trade.get("action_events") or []
    out["action_events"] = sorted(
        (_canonical_event(event) for event in events),
        key=lambda event: (
            event["timestamp"],
            event["action"],
            event["side"],
            event["requested_qty"],
            event["executed_qty"],
            event["price"],
            event["reason"],
        ),
    )
    return out


def exact_action_fingerprint(trades: Iterable[Mapping[str, Any]]) -> str:
    """Hash all execution/accounting fields required by the c5 contract."""
    canonical = sorted(
        (canonical_trade_action(trade) for trade in trades),
        key=lambda trade: (
            trade["entry_ts"],
            trade["exit_ts"],
            trade["symbol"],
            trade["side"],
            trade["entry_reason"],
            trade["exit_reason"],
        ),
    )
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{len(canonical)}:{C5_ACTION_FINGERPRINT_VERSION}:{digest}"
