"""stdev_reject_exit_live — vector twin of ez_positions_quick.py:12033 check_stdev_reject_exit (crypto live; stocks have no live consumer).
LONG : pctb_prev >= ZONE and pctb_now < RETURN and wt_velocity_1h < 0
SHORT: pctb_prev <= 1-ZONE and pctb_now > 1-RETURN and wt_velocity_1h > 0     (pctb = bb_pct_b_{TF}; prev = bb_pct_b_{TF}_prev else previous TF value)
Master STDEV_REJECT_EXIT_ENABLED (live default False); TF STDEV_REJECT_EXIT_TF (D), ZONE 0.80, RETURN 0.65. Position gain is only used for the log line in live."""
import numpy as np


def _a(npz, k, n, d=0.0):
    if k in npz:
        v = np.asarray(npz[k], dtype=float)
        if v.size < n:
            t = np.full(n, d); t[:v.size] = v; v = t
        return np.nan_to_num(v[:n], nan=d)
    return None


def stdev_reject_exit_mask(npz, n, cfg, is_long):
    if not bool(getattr(cfg, "STDEV_REJECT_EXIT_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    tf = str(getattr(cfg, "STDEV_REJECT_EXIT_TF", "D"))
    zone = float(getattr(cfg, "STDEV_REJECT_EXIT_ZONE", 0.80)); ret = float(getattr(cfg, "STDEV_REJECT_EXIT_RETURN", 0.65))
    now = _a(npz, f"bb_pct_b_{tf}", n, 0.5)
    if now is None:
        return np.zeros(n, dtype=bool)
    prev = _a(npz, f"bb_pct_b_{tf}_prev", n, 0.5)
    if prev is None:
        prev = np.roll(now, 1); prev[0] = now[0]
    vel = _a(npz, "wt_velocity_1h", n, 0.0)
    vel = vel if vel is not None else np.zeros(n)
    if is_long:
        return (prev >= zone) & (now < ret) & (vel < 0)
    return (prev <= 1.0 - zone) & (now > 1.0 - ret) & (vel > 0)
