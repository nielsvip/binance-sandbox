"""Fail-closed c5 evidence gates for side, capacity, and reentry integrity."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


_OPEN_ACTIONS = {
    "OPEN",
    "QUICK_OPEN",
    "AUGMENT",
    "QUICK_AUGMENT",
    "REENTRY",
    "HEDGE_OPEN",
}
_CLOSE_ACTIONS = {
    "CLOSE",
    "REDUCE",
    "QUICK_CLOSE",
    "FULL_CLOSE",
    "PROFIT_TAKE",
    "HEDGE_CLOSE",
    "MTM_FINAL_BAR_NOLIES_RULE2",
}


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _peak_action_notional(trades: Iterable[Mapping[str, Any]], qty_field: str):
    peak = 0.0
    for trade in trades:
        running_qty = 0.0
        for event in sorted(
            trade.get("action_events") or [],
            key=lambda row: _number(row.get("timestamp")),
        ):
            action = str(event.get("action") or "").upper()
            quantity = abs(
                _number(event.get(qty_field, event.get("executed_qty")))
            )
            price = _number(event.get("price"))
            if action in _OPEN_ACTIONS:
                running_qty += quantity
            elif action in _CLOSE_ACTIONS or "CLOSE" in action or "REDUCE" in action:
                running_qty = max(0.0, running_qty - quantity)
            peak = max(peak, running_qty * price)
    return peak


def audit_c5_matrix_safety(
    trades: Iterable[Mapping[str, Any]],
    result: Mapping[str, Any],
    *,
    intended_side: str,
    capacity_usd: float = 16000.0,
    allow_terminal_pending: bool = False,
) -> dict[str, Any]:
    rows = list(trades)
    side = intended_side.upper()
    opposite_rows = [
        row
        for row in rows
        if str(row.get("position_side") or row.get("side") or "").upper()
        not in ("", side)
    ]
    peak_requested = _peak_action_notional(rows, "requested_qty")
    peak_executed = _peak_action_notional(rows, "executed_qty")
    engine_peak = _number(result.get("max_open_notional"))
    violations = int(_number(result.get("reentry_violations")))
    pending = int(_number(result.get("reentry_pending")))
    reclaim = int(_number(result.get("reclaim_pending")))
    reasons = []
    if opposite_rows:
        reasons.append(f"{len(opposite_rows)} opposite-side P&L rows")
    if peak_requested > capacity_usd * 1.000001:
        reasons.append(
            f"requested exposure ${peak_requested:.2f} exceeds ${capacity_usd:.2f}"
        )
    if peak_executed > capacity_usd * 1.000001:
        reasons.append(
            f"executed exposure ${peak_executed:.2f} exceeds ${capacity_usd:.2f}"
        )
    if engine_peak > capacity_usd * 1.000001:
        reasons.append(
            f"engine max_open_notional ${engine_peak:.2f} exceeds ${capacity_usd:.2f}"
        )
    if violations:
        reasons.append(f"{violations} reentry overshoot violations")
    if pending and not allow_terminal_pending:
        reasons.append(f"{pending} reentry obligations pending")
    if reclaim and not allow_terminal_pending:
        reasons.append(f"{reclaim} reclaim obligations pending")
    return {
        "pass": not reasons,
        "intended_side": side,
        "opposite_side_pnl_rows": len(opposite_rows),
        "peak_requested_notional": peak_requested,
        "peak_executed_notional": peak_executed,
        "engine_max_open_notional": engine_peak,
        "capacity_usd": capacity_usd,
        "reentry_violations": violations,
        "reentry_pending": pending,
        "reclaim_pending": reclaim,
        "terminal_right_censored": bool(
            allow_terminal_pending and (pending or reclaim) and not violations
        ),
        "reasons": reasons,
    }
