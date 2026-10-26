"""Final execution-law helpers for estimated-fee-positive exits."""
import math


def mandatory_loss_stop_reason(reason, dc4_buffer_pct=0.25):
    value = str(reason or "").upper()
    if value.startswith("FIXED_STOP_"):
        return "_REF" not in value or "_REFDC_LOW.15M.B0.25" in value or "_REFDC_HIGH.15M.B0.25" in value
    if "ULTIMATE_DC_4H_HARD_STOP" in value:
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
