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
    if value.startswith(("ULTIMATE_DC_4H_HARD_STOP_LONG_", "ULTIMATE_DC_4H_HARD_STOP_SHORT_", "QUICK_ULTIMATE_DC_4H_HARD_STOP_LONG_", "QUICK_ULTIMATE_DC_4H_HARD_STOP_SHORT_")):
        return True
    if re.match(r"^EXIT_GUARANTEE_FORCE\[[^\]]+\]\s+ULTIMATE_DC_4H_HARD_STOP_(?:LONG|SHORT)_", value):
        return True
    if value.startswith("ULTIMATE_DC_4H_HARD_STOP AT DC_4H_LOW 0.25PCT CHANNEL") or value.startswith("ULTIMATE_DC_4H_HARD_STOP AT DC_4H_HIGH 0.25PCT CHANNEL"):
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


def vector_exit_event_allowed(event, position, before_position, mandatory_dc4_buffer_pct=0.25, is_long=True, half_fee=0.0, additional_fee_pct=0.0):
    """Accept ordinary simulated exits only when modeled net plus fee buffer stays positive."""
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
    try:
        extra_fee = max(0.0, float(additional_fee_pct)) / 100.0
        if "pnl_dollars" in event:
            pnl = float(event["pnl_dollars"])
            qty = abs(float(event.get("qty") or 0.0))
            price = abs(float(event.get("exit_price") or event.get("price") or 0.0))
            return math.isfinite(pnl) and pnl - qty * price * extra_fee > 0.0
        if kind == "REDUCE" and isinstance(position, dict):
            qty = abs(float(event.get("qty") or 0.0))
            price = abs(float(event.get("price") or 0.0))
            avg_price = abs(float(position.get("avg_price") or 0.0))
            if qty <= 0.0 or price <= 0.0 or avg_price <= 0.0:
                return False
            gross = (price - avg_price) * qty if is_long else (avg_price - price) * qty
            modeled_round_trip_fees = (avg_price + price) * qty * max(0.0, float(half_fee))
            estimated_extra_fees = price * qty * extra_fee
            net = gross - modeled_round_trip_fees - estimated_extra_fees
            return math.isfinite(net) and net > 0.0
    except (TypeError, ValueError):
        return False
    return False
