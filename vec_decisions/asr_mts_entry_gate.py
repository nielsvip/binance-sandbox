"""Native MTS bottom-score/entry-quality gate with generic and Tradier thresholds."""

import numpy as np


def mts_entry_gate_scalar(bottom, quality, is_long, tradier_enabled, generic_enabled, cfg):
    """Return the native WAIT reason or None, preserving Tradier precedence."""
    gate = generic_enabled or tradier_enabled
    if not gate:
        return None
    if tradier_enabled and not generic_enabled:
        bottom_min = float(getattr(cfg, "MTS_BOTTOM_MIN_TRADIER", 5.0))
        quality_min = float(getattr(cfg, "MTS_ENTRY_QUALITY_MIN_TRADIER", 0.0))
        if bottom < bottom_min or quality < quality_min:
            return f"MTS_GATE_TRADIER(b={bottom:.0f}<{bottom_min},eq={quality:.0f}<{quality_min},{'L' if is_long else 'S'})"
        return None
    if generic_enabled:
        if is_long:
            bottom_min = getattr(cfg, "MTS_BOTTOM_MIN", 10.0)
            quality_min = getattr(cfg, "MTS_ENTRY_QUALITY_MIN", 5.0)
        else:
            bottom_min = getattr(cfg, "MTS_BOTTOM_MIN_SHORT", 5.0)
            quality_min = getattr(cfg, "MTS_ENTRY_QUALITY_MIN_SHORT", 0.0)
        if bottom < bottom_min or quality < quality_min:
            return f"MTS_GATE(b={bottom:.0f}<{bottom_min},eq={quality:.0f}<{quality_min},{'L' if is_long else 'S'})"
    return None


def mts_entry_gate_array(bottom, quality, is_long, tradier_enabled, generic_enabled, cfg):
    """Return per-bar block mask and selected threshold masks."""
    bottom, quality = np.broadcast_arrays(np.asarray(bottom, dtype=np.float64), np.asarray(quality, dtype=np.float64))
    if tradier_enabled and not generic_enabled:
        bottom_min = float(getattr(cfg, "MTS_BOTTOM_MIN_TRADIER", 5.0))
        quality_min = float(getattr(cfg, "MTS_ENTRY_QUALITY_MIN_TRADIER", 0.0))
        block = (bottom < bottom_min) | (quality < quality_min)
        return block, {"tradier_bottom": bottom < bottom_min, "tradier_quality": quality < quality_min}
    if generic_enabled:
        if is_long:
            bottom_min = getattr(cfg, "MTS_BOTTOM_MIN", 10.0)
            quality_min = getattr(cfg, "MTS_ENTRY_QUALITY_MIN", 5.0)
        else:
            bottom_min = getattr(cfg, "MTS_BOTTOM_MIN_SHORT", 5.0)
            quality_min = getattr(cfg, "MTS_ENTRY_QUALITY_MIN_SHORT", 0.0)
        block = (bottom < bottom_min) | (quality < quality_min)
        return block, {"generic_bottom": bottom < bottom_min, "generic_quality": quality < quality_min}
    return np.zeros(bottom.shape, dtype=bool), {}
