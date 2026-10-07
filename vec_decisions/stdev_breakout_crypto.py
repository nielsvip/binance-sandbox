"""stdev_breakout_crypto — vector twin of ez_positions_quick.detect_stdev_breakout (crypto live, lines 11909-11990) (N1/004).
Phase 1: no active breakout -> for each HTF in STDEV_BREAKOUT_HTF_LIST (default D,4h): pctb=bb_pct_b_<htf>, rvol=relative_volume_<htf> (fallback 1h then 15m);
         LONG fires when pctb > STDEV_BREAKOUT_PCTB_LONG(1.125) and rvol >= STDEV_BREAKOUT_RVOL_MIN(1.2); SHORT when pctb < STDEV_BREAKOUT_PCTB_SHORT(-0.125). -> BREAKOUT entry, state active.
Phase 2 (active): bars_since += 1 each call, expires after STDEV_BREAKOUT_MAX_AGE_BARS (50); at most STDEV_BREAKOUT_MAX_RETESTS (3) retests; for each tf in STDEV_BREAKOUT_RETEST_TF_LIST (1h,15m):
         LONG retest = RETEST_PCTB_MIN(0.85) <= pctb_tf <= RETEST_PCTB_MAX(1.05) and k_tf > d_tf and k_tf < 65 ; SHORT retest = (1-max) <= pctb <= (1-min) and k_tf < d_tf and k_tf > 35.
APPROXIMATIONS (documented, not hidden): (1) live advances `bars_since` once PER CALL of the rater (every few seconds), the twin once per 15m BAR, so the age/retest budget is
in 15m bars; (2) live shares the state between the LONG and SHORT slot of a symbol, the twin runs per side; (3) live's size_mult (1.5 on retests) and score are not modelled (size-normalised gain).
Master: STDEV_BREAKOUT_ENABLED (default False => inert). Returns bool[n] 'entry fires on this bar' (BREAKOUT or RETEST). Crypto only (stocks live path should_enter_* is dead code)."""
import numpy as np


def _lst(v, d):
    if v is None:
        return d
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


def fires(npz, n, is_long, cfg, safe):
    if not bool(getattr(cfg, "STDEV_BREAKOUT_ENABLED", False)):
        return None
    htf = _lst(getattr(cfg, "STDEV_BREAKOUT_HTF_LIST", None), ["D", "4h"])
    rtf = _lst(getattr(cfg, "STDEV_BREAKOUT_RETEST_TF_LIST", None), ["1h", "15m"])
    pl = float(getattr(cfg, "STDEV_BREAKOUT_PCTB_LONG", 1.125)); ps = float(getattr(cfg, "STDEV_BREAKOUT_PCTB_SHORT", -0.125))
    rmin = float(getattr(cfg, "STDEV_BREAKOUT_RVOL_MIN", 1.2)); maxr = int(getattr(cfg, "STDEV_BREAKOUT_MAX_RETESTS", 3)); maxa = int(getattr(cfg, "STDEV_BREAKOUT_MAX_AGE_BARS", 50))
    rp_min = float(getattr(cfg, "STDEV_BREAKOUT_RETEST_PCTB_MIN", 0.85)); rp_max = float(getattr(cfg, "STDEV_BREAKOUT_RETEST_PCTB_MAX", 1.05))
    P = {tf: safe(npz, f"bb_pct_b_{tf}", n, 0.5) for tf in set(htf + rtf)}
    RV = {tf: safe(npz, f"relative_volume_{tf}", n, 0.0) for tf in htf}
    rv1, rv15 = safe(npz, "relative_volume_1h", n, 0.0), safe(npz, "relative_volume_15m", n, 0.0)
    K = {tf: (safe(npz, f"stoch_k_{tf}", n, 50.0), safe(npz, f"stoch_d_{tf}", n, 50.0)) for tf in rtf}
    out = np.zeros(n, dtype=bool)
    active = False; since = 0; retests = 0
    for i in range(n):
        if not active:
            for tf in htf:
                p = float(P[tf][i]); rv = float(RV[tf][i])
                if rv <= 0:
                    rv = float(rv1[i])
                if rv <= 0:
                    rv = float(rv15[i])
                if (is_long and p > pl and rv >= rmin) or ((not is_long) and p < ps and rv >= rmin):
                    active = True; since = 0; retests = 0; out[i] = True
                    break
            continue
        since += 1
        if since > maxa:
            active = False
            continue
        if retests >= maxr:
            continue
        for tf in rtf:
            p = float(P[tf][i]); k, d = float(K[tf][0][i]), float(K[tf][1][i])
            if is_long:
                ok = (rp_min <= p <= rp_max) and k > d and k < 65
            else:
                ok = ((1.0 - rp_max) <= p <= (1.0 - rp_min)) and k < d and k > 35
            if ok:
                retests += 1; out[i] = True
                break
    return out
