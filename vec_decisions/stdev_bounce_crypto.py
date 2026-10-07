"""stdev_bounce_crypto — vector twin of ez_positions_quick.detect_stdev_bounce (USER 2026-10-06).

LIVE: for each TF in STDEV_BOUNCE_HTF_LIST (default D,4h): pctb=bb_pct_b_<tf>,
rvol=relative_volume_<tf> (fallback relative_volume_15m, default 1.0);
LONG fires when pctb <= STDEV_BOUNCE_PCTB_LONG (0.05) and rvol >= STDEV_BOUNCE_RVOL_MIN (1.2);
SHORT when pctb >= STDEV_BOUNCE_PCTB_SHORT (0.95). First firing TF wins. Stateless.
Score (STDEV_BOUNCE_SCORE 22) feeds live sizing; size is engine-owned (sizing seam, documented).

Master: STDEV_BOUNCE_ENABLED (default False => inert).
"""
from typing import Any, Mapping

import numpy as np


def _cfg(cfg: Any, name: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _lst(v, d):
    if v is None:
        return d
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


def fires(npz, n, is_long, cfg, safe, details=False):
    """Per-bar fire mask. details=True -> dict(mask, htf, pctb, rvol) for live-exact reasons."""
    if not bool(_cfg(cfg, "STDEV_BOUNCE_ENABLED", False)):
        return None
    htf = _lst(_cfg(cfg, "STDEV_BOUNCE_HTF_LIST", None), ["D", "4h"])
    pl = float(_cfg(cfg, "STDEV_BOUNCE_PCTB_LONG", 0.05))
    ps = float(_cfg(cfg, "STDEV_BOUNCE_PCTB_SHORT", 0.95))
    rmin = float(_cfg(cfg, "STDEV_BOUNCE_RVOL_MIN", 1.2))
    P = {tf: safe(npz, f"bb_pct_b_{tf}", n, 0.5) for tf in htf}
    RV = {tf: safe(npz, f"relative_volume_{tf}", n, -1.0) for tf in htf}
    rv15 = safe(npz, "relative_volume_15m", n, 1.0)
    out = np.zeros(n, dtype=bool)
    htf_hit = np.full(n, "", dtype=object)
    pctb_hit = np.zeros(n)
    rvol_hit = np.zeros(n)
    for tf in htf:
        p = np.asarray(P[tf], dtype=float)
        rv = np.asarray(RV[tf], dtype=float)
        rv = np.where(rv < 0, rv15, rv)
        rv = np.where(rv < 0, 1.0, rv)
        if is_long:
            fire = (p <= pl) & (rv >= rmin)
        else:
            fire = (p >= ps) & (rv >= rmin)
        new = fire & (~out)
        out |= fire
        htf_hit[new] = tf
        pctb_hit[new] = p[new]
        rvol_hit[new] = rv[new]
    if not details:
        return out
    return {"mask": out, "htf": htf_hit, "pctb": pctb_hit, "rvol": rvol_hit}
