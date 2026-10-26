"""15m ANG timing mask shared by sweep and live vector twin."""
import numpy as np
from tools.ang_timing_core import FAILED, long_state, short_state


def failed_mask_from_npz(npz, is_long, cfg):
    close = np.asarray(npz["close_15m"], dtype=float)
    high = np.asarray(npz["high_15m"], dtype=float)
    low = np.asarray(npz["low_15m"], dtype=float)
    if len(close) != len(high) or len(close) != len(low):
        raise ValueError("15m OHLC lengths disagree")
    lookback = int(getattr(cfg, "ANG_TIMING_LOOKBACK", 20))
    hold_bars = int(getattr(cfg, "ANG_TIMING_HOLD_BARS", 12))
    window = lookback + hold_bars + 2
    kwargs = {
        "lookback": lookback,
        "fresh_bars": int(getattr(cfg, "ANG_TIMING_FRESH_BARS", 3)),
        "tight_atr": float(getattr(cfg, "ANG_TIMING_TIGHT_ATR", 0.5)),
        "hold_bars": hold_bars,
        "hold_tol_atr": float(getattr(cfg, "ANG_TIMING_HOLD_TOL_ATR", 0.25)),
    }
    predicate = long_state if is_long else short_state
    failed = np.zeros(len(close), dtype=bool)
    for i in range(window - 1, len(close)):
        state, _, _, _ = predicate(
            close[i - window + 1 : i + 1],
            high[i - window + 1 : i + 1],
            low[i - window + 1 : i + 1],
            **kwargs,
        )
        failed[i] = state == FAILED
    return failed
