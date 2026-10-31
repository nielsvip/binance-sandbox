import numpy as np
from vec_decisions.asr_input_adapter import paired_npz_view, selected_timeframe

def block_mask(npz, n, is_long, cfg, safe):
    if not bool(getattr(cfg, "ADX_REGIME_FILTER_ENABLED", False)) or bool(getattr(cfg, "REGIME_DETECTION_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    use_ltf = bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    npz = paired_npz_view(npz, use_ltf)
    adx_tf = str(getattr(cfg, "ADX_TF", "1h"))
    focus_tf = selected_timeframe(getattr(cfg, "TF_FOCUS", "3m"), use_ltf)
    adx = safe(npz, f"adx_{adx_tf}", n, 50.0)
    focus_key = f"k_{focus_tf}" if f"k_{focus_tf}" in npz else f"stoch_k_{focus_tf}"
    focus = safe(npz, focus_key, n, 50.0)
    extreme = focus < 20.0 if is_long else focus > 80.0
    return (adx < float(getattr(cfg, "ADX_RANGING_THRESHOLD", 20.0))) & ~extreme
