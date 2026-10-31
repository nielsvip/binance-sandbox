"""Causal ASR no-double-augment wait gate from AdvancedSignalRater.rate."""
from collections.abc import Mapping

import numpy as np


def asr_no_double_augment_gate(state, cfg, n):
    """Mask the native <24m and gain<MIN_GAIN WAIT, requiring lifecycle facts."""
    if not isinstance(state, Mapping):
        raise ValueError("ASR lifecycle gate requires an explicit state mapping")
    n = int(n)
    if "position_exists" not in state:
        raise ValueError("ASR lifecycle state requires position_exists")
    exists = np.broadcast_to(np.asarray(state["position_exists"], dtype=bool), (n,))
    last_aug = state.get("last_augmentation_present")
    if last_aug is None:
        if np.any(exists):
            raise ValueError("held positions require last_augmentation_present")
        last_aug = np.zeros(n, dtype=bool)
    last_aug = np.broadcast_to(np.asarray(last_aug, dtype=bool), (n,))
    active = exists & last_aug
    if np.any(active):
        missing = [key for key in ("minutes_since_augmentation", "gain") if key not in state]
        if missing:
            raise ValueError(f"recent augmentation state requires {','.join(missing)}")
        minutes = np.asarray(state["minutes_since_augmentation"], dtype=float).reshape(-1)
        gain = np.asarray(state["gain"], dtype=float).reshape(-1)
        if minutes.size != n or gain.size != n:
            raise ValueError("ASR lifecycle time/gain arrays must have length n")
        minimum_gain = float(cfg.get("MIN_GAIN", 0.0) if isinstance(cfg, Mapping) else cfg.MIN_GAIN)
        recent = active & (minutes < 24.0)
        blocked = recent & (gain < minimum_gain)
    else:
        recent = np.zeros(n, dtype=bool)
        blocked = np.zeros(n, dtype=bool)
    return {"blocked": blocked, "position_has_recent_augmentation": recent}
