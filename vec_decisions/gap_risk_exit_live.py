"""gap_risk_exit_live — vector twin of tradier_manage.py:19509-19619 GAP_RISK_EXIT (N2, 2026-10-01). ONE predicate, line-by-line live semantics.

Live (per bar while a position is held; _GAP_RISK_STATE keyed by position):
  gap_short = (not long) and open_D > close_D_prev ; gap_long = long and open_D < close_D_prev ; both need open_D and close_D_prev non-zero
  state (re)initialised when (open_D, close_D_prev) differ from the stored gap: retraced=False, ext_high=high_D, ext_low=low_D
  retraced latches True when: short: low_D <= prev_close ; long: high_D >= prev_close        (high_D/low_D = DAILY running extremes)
  ext_high = max(ext_high, high_D), ext_low = min(ext_low, low_D)  (updated BEFORE the exit check, exactly like live)
  only if retraced:
    COND_A (OPEN_RECLAIM or COND_A enabled): short: close > open_D or high_D > open_D ; long: close < open_D or low_D < open_D
        close = close_D (daily running close) else the bar price
    COND_B (STRUCTURE_BREAK or COND_B enabled): short: high_D > 0 and high_D > prev_close and high_D >= open_D and high_D == ext_high
                                                long : low_D > 0 and low_D < open_D and low_D < prev_close and low_D == ext_low
  fire -> full close (live returns (True, reason, qty)); state popped. No gap on a bar -> state popped.
Master: GAP_RISK_EXIT_ENABLED and (SHORT: GAP_RISK_EXIT_SHORT_ENABLED | LONG: GAP_RISK_EXIT_LONG_ENABLED); A/B enables as above.
The vector engine evaluates the mask for every bar of the series (state restarts at each new day-gap), live evaluates only while a position is open:
a position opened after the retrace starts a fresh state in live (retraced False until the next touch) — the mask is therefore an UPPER BOUND on live fires
(documented difference; the engine only consumes the mask on bars where it is in position)."""
import numpy as np


def _arr(npz, key, n, default=0.0):
    if key in npz:
        a = np.asarray(npz[key], dtype=float)
        if a.size < n:
            t = np.full(n, default); t[:a.size] = a; a = t
        return np.nan_to_num(a[:n], nan=0.0, posinf=0.0, neginf=0.0)
    return np.full(n, default)


def gap_risk_exit_live_vec(npz, n, cfg, is_long):
    out = np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "GAP_RISK_EXIT_ENABLED", False)):
        return out
    side_en = bool(getattr(cfg, "GAP_RISK_EXIT_LONG_ENABLED" if is_long else "GAP_RISK_EXIT_SHORT_ENABLED", False))
    if not side_en:
        return out
    a_en = bool(getattr(cfg, "GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED", False)) or bool(getattr(cfg, "GAP_RISK_EXIT_COND_A_ENABLED", False))
    b_en = bool(getattr(cfg, "GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED", False)) or bool(getattr(cfg, "GAP_RISK_EXIT_COND_B_ENABLED", False))
    if not (a_en or b_en):
        return out
    od = _arr(npz, "open_D", n); pc = _arr(npz, "close_D_prev", n)
    hi = _arr(npz, "high_D", n); lo = _arr(npz, "low_D", n)
    cl = _arr(npz, "close_D", n); px = _arr(npz, "close", n)
    close = np.where(cl > 0, cl, px)
    st_open = st_prev = None
    retraced = False; ext_hi = ext_lo = 0.0
    for i in range(n):
        o, p = float(od[i]), float(pc[i])
        if not (o and p):
            st_open = st_prev = None; retraced = False
            continue
        gap = (o < p) if is_long else (o > p)
        if not gap:
            st_open = st_prev = None; retraced = False
            continue
        h, l, c = float(hi[i]), float(lo[i]), float(close[i])
        if st_open != o or st_prev != p:
            st_open, st_prev = o, p; retraced = False; ext_hi, ext_lo = h, l
        if not retraced:
            if (not is_long) and l and l <= p:
                retraced = True
            elif is_long and h and h >= p:
                retraced = True
        if h:
            ext_hi = max(ext_hi or h, h)
        if l:
            ext_lo = min(ext_lo or l, l) if ext_lo else l
        if not retraced:
            continue
        ca = cb = False
        if a_en:
            ca = (c > o or h > o) if not is_long else (c < o or (l and l < o))
        if b_en:
            if (not is_long) and h and h > o:
                cb = h > p and h >= o and h == ext_hi
            elif is_long and l and l < o:
                cb = l < p and l <= o and l == ext_lo
        if ca or cb:
            out[i] = True
            st_open = st_prev = None; retraced = False   # live pops the state on exit
    return out
