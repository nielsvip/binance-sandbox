"""Semantic validation for parameter-sweep value grids.

Generated ``default * {0.5..1.5}`` grids are only hypotheses.  They are not
allowed to reach a backtest when their type/domain makes them impossible or
non-binding.  Clear domains are repaired explicitly and recorded; ambiguous
units are quarantined for a schema entry rather than silently clamped.
"""
from __future__ import annotations

import math
import re
from typing import Any


_OSCILLATOR = re.compile(
    r"(?:^|_)(?:RSI|STOCH|MFI)(?:_|$)|(?:^|_)K_(?:EXHAUSTED|EXTREME|HIGH|LOW)",
    re.I,
)
_NORMALIZED_PCTB = re.compile(r"(?:^|_)(?:BB_)?PCTB(?:_|$)", re.I)
_COUNT = re.compile(
    r"(?:^|_)(?:COUNT|BARS|TFS|LOOKBACK|PERIOD|WINDOW|MIN_TFS|MAX_TFS)(?:_|$)",
    re.I,
)
_FRACTION = re.compile(r"(?:^|_)(?:FRAC|FRACTION)(?:_|$)", re.I)


def semantic_domain(name: str, default: Any) -> dict[str, Any]:
    upper = name.upper()
    if isinstance(default, bool):
        return {"kind": "boolean", "minimum": None, "maximum": None}
    if (
        _OSCILLATOR.search(upper)
        and isinstance(default, (int, float))
        and not isinstance(default, bool)
    ):
        # A few explicitly normalized indicator implementations use 0..1.
        # Some active gates use >100 as an explicit "disabled" sentinel (for
        # example SATOSHIT_LONG_MFI_MAX_TRADIER=120).  The active typed control
        # must remain executable even though ordinary oscillator readings stop
        # at 100.
        maximum = (
            1.0
            if 0 <= default <= 1
            else max(100.0, float(default))
        )
        return {"kind": "bounded_oscillator", "minimum": 0.0, "maximum": maximum}
    if (
        _NORMALIZED_PCTB.search(upper)
        and isinstance(default, (int, float))
        and not isinstance(default, bool)
    ):
        if isinstance(default, (int, float)) and 0 <= default <= 1:
            return {"kind": "normalized_pctb", "minimum": 0.0, "maximum": 1.0}
        return {
            "kind": "ambiguous_pctb_units",
            "minimum": None,
            "maximum": None,
            "requires_explicit_schema": True,
        }
    if _FRACTION.search(upper):
        return {"kind": "fraction", "minimum": 0.0, "maximum": 1.0}
    if isinstance(default, int) and not isinstance(default, bool) and _COUNT.search(upper):
        return {"kind": "nonnegative_integer_count", "minimum": 0, "maximum": None}
    return {"kind": "unbounded_or_ambiguous", "minimum": None, "maximum": None}


def _same_type_value(value: Any, default: Any) -> tuple[bool, Any, str | None]:
    if isinstance(default, bool):
        if type(value) is not bool:
            return False, value, "boolean grid accepts only JSON booleans"
        return True, value, None
    if isinstance(default, int) and not isinstance(default, bool):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, value, "integer grid accepts only finite numeric values"
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return False, value, "integer grid value is fractional or non-finite"
        return True, int(number), None
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, value, "float grid accepts only finite numeric values"
        number = float(value)
        if not math.isfinite(number):
            return False, value, "float grid value is non-finite"
        return True, number, None
    if isinstance(default, str):
        if not isinstance(value, str):
            return False, value, "categorical string grid accepts only strings"
        return True, value, None
    return False, value, "non-numeric parameter requires an explicit categorical schema"


def validate_test_values(
    name: str,
    default: Any,
    values: list[Any] | tuple[Any, ...] | None,
) -> dict[str, Any]:
    domain = semantic_domain(name, default)
    if not values:
        return {
            "status": "QUARANTINED_EMPTY",
            "domain": domain,
            "valid_values": [],
            "rejected_values": [],
            "reason": "no test values",
        }
    if domain.get("requires_explicit_schema"):
        return {
            "status": "QUARANTINED_AMBIGUOUS_UNITS",
            "domain": domain,
            "valid_values": [],
            "rejected_values": [
                {"value": value, "reason": "explicit pctB unit schema required"}
                for value in values
            ],
            "reason": "ambiguous normalized/raw pctB units; no silent clamp",
        }

    valid: list[Any] = []
    rejected: list[dict[str, Any]] = []
    minimum = domain.get("minimum")
    maximum = domain.get("maximum")
    for raw in values:
        ok, value, reason = _same_type_value(raw, default)
        if not ok:
            rejected.append({"value": raw, "reason": reason})
            continue
        if minimum is not None and value < minimum:
            rejected.append(
                {"value": raw, "reason": f"below semantic minimum {minimum}"}
            )
            continue
        if maximum is not None and value > maximum:
            rejected.append(
                {"value": raw, "reason": f"above semantic maximum {maximum}"}
            )
            continue
        if value not in valid:
            valid.append(value)

    if len(valid) < 2:
        status = "QUARANTINED_NONBINDING"
        reason = "fewer than two distinct semantically valid values"
    elif rejected:
        status = "REPAIRED_EXPLICIT_REJECTIONS"
        reason = "invalid generated values removed and recorded"
    else:
        status = "VALID"
        reason = "all values satisfy the declared semantic domain"
    return {
        "status": status,
        "domain": domain,
        "valid_values": valid,
        "rejected_values": rejected,
        "reason": reason,
    }


def executable_values(validation: dict[str, Any]) -> list[Any] | None:
    if str(validation.get("status", "")).startswith("QUARANTINED"):
        return None
    values = list(validation.get("valid_values") or [])
    return values if len(values) >= 2 else None
