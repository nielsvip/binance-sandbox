"""UNCOND_DC_HARD_STOP_BUFFER_PCT (USER 2026-10-22, pv3 C2): the buffer of the unconditional DC hard stop.

LONG closes at px <= dc_low_TF * (1 - buf/100), SHORT at px >= dc_high_TF * (1 + buf/100), TF = 4h always, 1h / 15m by
DC_HARD_STOP_1H_ENABLED / DC_HARD_STOP_15M_ENABLED. Default 0.25 = the historical 0.9975 / 1.0025 literals byte for byte.
It moves the SOFTWARE trigger only: no value disables a leg, and the exchange-resting DC_HARD_STOP_15M order
(ez_manage EXCHANGE_RESTING_STOPS, USER 2026-10-16 "can NEVER be switched") keeps its own placement rule.
Shared by v12_quick_engine (vector) and tradier_manage (stocks live); crypto live executes the vector's UNCOND_DC closes.
"""
DEFAULT_BUFFER_PCT = 0.25
_MIN, _MAX = 0.0, 2.0


def buffer_pct_value(raw) -> float:
    try:
        b = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_BUFFER_PCT
    if isinstance(raw, bool) or not _MIN <= b <= _MAX:
        return DEFAULT_BUFFER_PCT
    return b


def buffer_pct(cfg) -> float:
    return buffer_pct_value(getattr(cfg, "UNCOND_DC_HARD_STOP_BUFFER_PCT", DEFAULT_BUFFER_PCT))


def buffer_factors(cfg) -> tuple:
    """(long factor, short factor). The default returns the historical literals exactly (no float re-derivation)."""
    b = buffer_pct(cfg)
    if b == DEFAULT_BUFFER_PCT:
        return 0.9975, 1.0025
    return 1.0 - b / 100.0, 1.0 + b / 100.0
