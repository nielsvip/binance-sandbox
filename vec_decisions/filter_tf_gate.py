"""vec_decisions/filter_tf_gate.py — SHARED FILTER_TF TF-selector gate.
Live and vector both call this identical function. Each *_FILTER_TF is a TF selector:
 when set to "none"/"" it bypasses; otherwise it requires that the associated filter's
 MTF condition is evaluated on that TF. Currently a no-op selector (returns False = do not block)
 so that wiring parity can be proven without changing live behavior when default "15m".
 As specific filters are implemented, this becomes causal and both paths change together.
"""
from __future__ import annotations
from typing import Any

def filter_tf_gate_blocks(indicators, cfg: Any, filter_name: str) -> bool:
    """Live scalar predicate — identical to vectorized below."""
    tf = str(getattr(cfg, filter_name, "15m") or "15m").lower()
    if tf in ("none", "off", "", "0"):
        return False
    # No blocking yet — selector only proves wiring reaches same predicate
    # Per-filter causal logic will be added here and in filter_tf_gate_vec together
    return False

def filter_tf_gate_vec(npz, n, cfg: Any, filter_name: str):
    import numpy as np
    tf = str(getattr(cfg, filter_name, "15m") or "15m").lower()
    if tf in ("none", "off", "", "0"):
        return np.zeros(n, dtype=bool)
    return np.zeros(n, dtype=bool)
