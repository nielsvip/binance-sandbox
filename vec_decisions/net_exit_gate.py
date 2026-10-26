"""Final execution-law helpers for estimated-fee-positive exits."""
import math
import re


def mandatory_loss_stop_reason(reason, dc4_buffer_pct=0.25):
    value = str(reason or "").upper()
    if value.startswith(("FIXED_STOP_LONG_", "FIXED_STOP_SHORT_")):
        # Default stop reasons omit _REF entirely; explicit specs must name the frozen 15m DC channel.
        # Do not let old BB/1h/4h or alternative-buffer software stops bypass the fee-positive exit law.
        if "_REF" not in value:
            return True
        return bool(re.search(r"_REFDC_(?:LOW|HIGH)\.15M(?:\.B0\.25)?$", value))
    if value.startswith(("ULTIMATE_DC_4H_HARD_STOP_LONG_", "ULTIMATE_DC_4H_HARD_STOP_SHORT_")):
        return True
    if value.startswith("ULTIMATE_DC_4H_HARD_STOP AT DC_LOW_4H 0.25PCT CHANNEL") or value.startswith("ULTIMATE_DC_4H_HARD_STOP AT DC_HIGH_4H 0.25PCT CHANNEL"):
        return True
    if ("UNCOND_DC_LOW_4H_UNCOND" in value or "UNCOND_DC_HIGH_4H_UNCOND" in value) and math.isclose(float(dc4_buffer_pct), 0.25, abs_tol=1e-9):
        return True
    return False


def estimated_net_gain_positive(gross_gain_pct, estimated_fee_pct):
    try:
        gross = float(gross_gain_pct)
        fee = max(0.0, float(estimated_fee_pct))
    except (TypeError, ValueError):
        return False
    return math.isfinite(gross) and gross - fee > 0.0


def vector_exit_event_allowed(event, position, before_position, mandatory_dc4_buffer_pct=0.25):
    """Accept a simulated close/reduce only when its current-leg net is positive or it is a mandatory stop."""
    if not isinstance(event, dict):
        return False
    kind = str(event.get("type") or "").upper()
    if kind not in ("CLOSE", "REDUCE") or event.get("unrealised"):
        return True
    reason = event.get("exit_reason") or event.get("reason")
    if event.get("mandatory_stop_kind") == "DC15_FROZEN_DEFAULT" and str(reason or "") == "FIXED_STOP":
        return True
    if mandatory_loss_stop_reason(reason, mandatory_dc4_buffer_pct):
        return True
    if "pnl_dollars" in event:
        try:
            return math.isfinite(float(event["pnl_dollars"])) and float(event["pnl_dollars"]) > 0.0
        except (TypeError, ValueError):
            return False
    if kind == "REDUCE" and isinstance(position, dict) and isinstance(before_position, dict):
        try:
            realized_delta = float(position.get("realized", 0.0)) - float(before_position.get("realized", 0.0))
            fee_delta = float(position.get("fees", 0.0)) - float(before_position.get("fees", 0.0))
            return math.isfinite(realized_delta - fee_delta) and realized_delta - fee_delta > 0.0
        except (TypeError, ValueError):
            return False
    return False
