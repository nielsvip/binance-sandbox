"""Account-scoped lot validation for vector partial reductions."""
from decimal import Decimal, ROUND_DOWN
import math


CONTEXT_VERSION = 1


def validate_context(context, symbol, side, window_first_ts, window_last_ts):
    if not isinstance(context, dict):
        return False, "CONTEXT_NOT_OBJECT"
    if int(context.get("schema_version") or 0) != CONTEXT_VERSION:
        return False, "CONTEXT_SCHEMA_VERSION"
    if not str(context.get("account_key") or "").strip():
        return False, "CONTEXT_ACCOUNT_MISSING"
    if str(context.get("symbol") or "").upper() != str(symbol or "").upper():
        return False, "CONTEXT_SYMBOL_MISMATCH"
    if str(context.get("side") or "").upper() != str(side or "").upper():
        return False, "CONTEXT_SIDE_MISMATCH"
    if not str(context.get("decision_generation") or "").strip():
        return False, "CONTEXT_DECISION_GENERATION_MISSING"
    if not str(context.get("config_sha256") or "").strip():
        return False, "CONTEXT_CONFIG_HASH_MISSING"
    try:
        first = int(context.get("window_first_ts"))
        last = int(context.get("window_last_ts"))
        observed = int(context.get("observation_bar_ts"))
        held = float(context.get("observed_qty"))
        lot = context.get("market_lot") or {}
        step = float(lot.get("step_size"))
        minimum = float(lot.get("min_qty"))
    except (TypeError, ValueError, AttributeError, OverflowError):
        return False, "CONTEXT_NUMERIC_FIELDS_INVALID"
    if first != int(window_first_ts) or last != int(window_last_ts):
        return False, "CONTEXT_WINDOW_MISMATCH"
    if observed < first or observed > last:
        return False, "CONTEXT_OBSERVATION_OUTSIDE_WINDOW"
    if not math.isfinite(held) or held < 0:
        return False, "CONTEXT_HELD_QTY_INVALID"
    if not math.isfinite(step) or step <= 0 or not math.isfinite(minimum) or minimum <= 0:
        return False, "CONTEXT_MARKET_LOT_INVALID"
    return True, "OK"


def plan_partial_reduce(engine_qty, engine_qty_before, account_qty, step_size, min_qty):
    try:
        request = float(engine_qty)
        engine_held = float(engine_qty_before)
        held = float(account_qty)
        step = float(step_size)
        minimum = float(min_qty)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "reason": "PARTIAL_NUMERIC_FIELDS_INVALID"}
    if not all(math.isfinite(value) for value in (request, engine_held, held, step, minimum)):
        return {"ok": False, "reason": "PARTIAL_NUMERIC_FIELDS_INVALID"}
    if engine_held <= 0 or request <= 0 or request >= engine_held:
        return {"ok": False, "reason": "NOT_ENGINE_PARTIAL"}
    if held <= 0 or step <= 0 or minimum <= 0:
        return {"ok": False, "reason": "ACCOUNT_QTY_OR_LOT_INVALID"}
    fraction = request / engine_held
    raw_qty = held * fraction
    dec_qty, dec_step = Decimal(str(raw_qty)), Decimal(str(step))
    account_reduce_qty = float((dec_qty / dec_step).to_integral_value(rounding=ROUND_DOWN) * dec_step)
    if account_reduce_qty <= 0 or account_reduce_qty + 1e-12 < minimum:
        return {
            "ok": False,
            "reason": "PARTIAL_BELOW_MIN_QTY",
            "requested_fraction": fraction,
            "account_qty_before": held,
            "account_requested_qty": raw_qty,
            "account_lot_qty": account_reduce_qty,
            "min_qty": minimum,
        }
    residual = max(0.0, held - account_reduce_qty)
    if residual > 1e-12 and residual + 1e-12 < minimum:
        account_reduce_qty = held
        residual = 0.0
        reason = "PARTIAL_EXECUTABLE_FULL_DUST_CLOSE"
    else:
        reason = "PARTIAL_EXECUTABLE"
    executable_fraction = account_reduce_qty / held
    return {
        "ok": True,
        "reason": reason,
        "requested_fraction": fraction,
        "executable_fraction": executable_fraction,
        "account_qty_before": held,
        "account_reduce_qty": account_reduce_qty,
        "account_projected_qty_after": residual,
        "engine_qty_before": engine_held,
        "engine_reduce_qty": engine_held * executable_fraction,
    }
