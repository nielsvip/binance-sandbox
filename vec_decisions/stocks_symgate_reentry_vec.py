"""STOCKS_SYMGATE_REENTRY — vector twin of the stock symgate reentry rule.

LIVE SOURCE: tradier_manage.StockStrategy.would_exit_trigger_now
(tradier_manage.py:51929). Returns blocked when the MIRROR exit rubric fires
for the side:
  leg 1 — wt_dc_score_exit >= WT_DC_EXIT_THRESHOLD (live-effective 30);
  leg 2 — DELTA exit_long / exit_short flags;
  leg 3 — DELTA zone TOP / TOP_TOP (long) / BOTTOM / BOTTOM_BOTTOM (short);
  leg 4 — DELTA speed floor bull / bear_speed < REENTRY_SYMGATE_SPEED_MIN
          (0.5), waived when no 5m/15m WT data at all (STALE FAIL-OPEN).
Live head-gate: config_tradier.ENABLE_TR_SYMGATE_REENTRY (default False;
locked, parent-owned) + REENTRY_SYMGATE_ENABLED (default False). Live bypass:
price reclaim (cur beyond exit in the favourable direction) skips symgate
entirely — mirrored at the call site via reclaimed(), not in this mask.
ENTRY-side consults (:52825/:53480) are NOT modeled here (reentry scope).

VEC LEGS (frozen NPZ: 15m/1h/4h/D + sma_200_1m; no 3m/5m/1m-WT):
  SCORE — stocks_wtdc_scorer_exit.score_array (exact N-of-5 twin) >=
          WT_DC_EXIT_THRESHOLD from the sweep-effective cfg.
  ZONE  — TOP / BOTTOM predicate mirroring wt_dc_delta zone detection
          (_near_top / _near_bottom at 1h + rz thresholds + zscore OB/OS).
  SPEED — causal per-bar bull / bear speeds with the LIVE STOCK tracker
          weights {1h: 2, 4h: 3, D: 2} over the same WT / DC field classes;
          a series is skipped when its key is absent (live skips None prev);
          prev is the `{key}_prev` array when present else the causal
          shift-by-1 (bar 0 contributes nothing, as live first-call). Live
          update() applies no smoothing, neither do we.
FIDELITY NOTES (honest gaps, scalar-parity lane owns them):
  1. DELTA exit_long / exit_short flags are NOT modeled: stateful two-phase +
     200-bar accel window + max-speed tracking; a vec proxy would be a guess,
     not a twin (bible §19). Fail-open direction: vec allows what only the
     flag leg would block live.
  2. Live symgate scores with scorer module defaults (cfg=None: K75); vec
     scores with sweep-effective cfg (stock baseline K85, same as the
     engine exit-path twin). Pass EXIT_SCORER_K_EXTREME=75 for the K75 read.
  3. Live monitor polls sporadically (stored prev may be bars stale); vec
     evaluates every 15m bar causally — the backtest-canonical reading.
CALL SITE (one): v12_quick_engine common fire veto stack — veto ANY
post-close reentry open (B-blocks, ladder, rally, churn) when blocked and not
reclaimed. Fresh entries and augments are out of scope (live ENTRY-side
consults are separate switches, not modeled here).
"""

from __future__ import annotations

import numpy as np

STOCK_TF_WEIGHTS = (("1h", 2.0), ("4h", 3.0), ("D", 2.0))
_WT_FIELDS = (
    "wt1",
    "wt2",
    "wt_score",
    "wt_velocity",
    "wt_acceleration",
    "wt_percentile",
    "wt_zscore",
)
_DC_PREV_FIELDS = ("dc_basis", "dc_high", "dc_low")
_DC_SELF_FIELDS = ("dc_position", "dc_width")
_INT_FIELDS = ("wt_bullish", "wt_cross_bull", "wt_cross_bear", "wt_momentum_state")
_STALE_KEYS = ("wt1_5m", "wt2_5m", "wt1_15m", "wt2_15m")


def _cg(cfg, name, default):
    try:
        if isinstance(cfg, dict):
            v = cfg.get(name, default)
        else:
            v = getattr(cfg, name, default)
    except Exception:
        return default
    return default if v is None else v


def _f(cfg, name, default):
    try:
        v = float(_cg(cfg, name, default))
        return v if v == v else float(default)
    except (TypeError, ValueError):
        return float(default)


def _col(npz, key, n, default=None):
    try:
        a = np.asarray(npz.get(key), dtype=np.float64).reshape(-1)
    except Exception:
        a = None
    if a is None or a.size < n:
        if default is None:
            return None
        return np.full(n, float(default), dtype=np.float64)
    return a[:n].astype(np.float64, copy=True)


def _delta(a, pa):
    if pa is not None:
        m = np.isfinite(a) & np.isfinite(pa)
        return np.where(m, a - pa, 0.0), m
    n = a.shape[0]
    m = np.zeros(n, dtype=bool)
    m[1:] = np.isfinite(a[1:]) & np.isfinite(a[:-1])
    d = np.zeros(n, dtype=np.float64)
    d[1:] = np.where(m[1:], a[1:] - a[:-1], 0.0)
    return d, m


def speed_arrays(npz, n):
    """Causal per-bar bull/bear speeds with live stock weights. No smoothing."""
    bull = np.zeros(n, dtype=np.float64)
    bear = np.zeros(n, dtype=np.float64)
    for tf, w in STOCK_TF_WEIGHTS:
        b = np.zeros(n, dtype=np.float64)
        e = np.zeros(n, dtype=np.float64)
        c = np.zeros(n, dtype=np.float64)
        for f in _WT_FIELDS:
            a = _col(npz, f"{f}_{tf}", n)
            if a is None:
                continue
            d, m = _delta(a, _col(npz, f"{f}_prev_{tf}", n))
            b += np.where(d > 0.0, d, 0.0)
            e += np.where(d < 0.0, -d, 0.0)
            c += m.astype(np.float64)
        for f in _DC_PREV_FIELDS:
            a = _col(npz, f"{f}_{tf}", n)
            pa = _col(npz, f"{f}_{tf}_prev", n)
            if a is None or pa is None:
                continue
            d, m = _delta(a, pa)
            b += np.where(d > 0.0, d, 0.0)
            e += np.where(d < 0.0, -d, 0.0)
            c += m.astype(np.float64)
        for f in _DC_SELF_FIELDS + _INT_FIELDS:
            a = _col(npz, f"{f}_{tf}", n)
            if a is None:
                continue
            d, m = _delta(a, None)
            b += np.where(d > 0.0, d, 0.0)
            e += np.where(d < 0.0, -d, 0.0)
            c += m.astype(np.float64)
        bull += np.divide(b, c, out=np.zeros(n), where=c > 0.0) * w
        bear += np.divide(e, c, out=np.zeros(n), where=c > 0.0) * w
    return bull, bear


def score_blocked(npz, n, is_long, cfg):
    """True where the mirror WT-DC exit score would fire (live leg 1)."""
    from vec_decisions import stocks_wtdc_scorer_exit as _sx

    return _sx.score_array(npz, n, is_long, cfg) >= _f(
        cfg, "WT_DC_EXIT_THRESHOLD", 25.0
    )


def _price(npz, n):
    for k in ("close", "close_15m", "close_1h"):
        a = _col(npz, k, n)
        if a is not None:
            return a
    return None


def zone_blocked(npz, n, is_long, cfg):
    """True in the DELTA TOP (long) / BOTTOM (short) zone (live leg 3)."""
    z = np.zeros(n, dtype=bool)
    px = _price(npz, n)
    if px is None:
        return z
    bbu = _col(npz, "bb_upper_1h", n, 0.0)
    bbl = _col(npz, "bb_lower_1h", n, 0.0)
    dch = _col(npz, "dc_high_1h", n, 0.0)
    dcl = _col(npz, "dc_low_1h", n, 0.0)
    bbp = _col(npz, "bb_pct_b_1h", n, 0.5)
    dcp = _col(npz, "dc_position_1h", n, 0.5)
    zs1 = _col(npz, "wt_zscore_1h", n, 0.0)
    zs4 = _col(npz, "wt_zscore_4h", n, 0.0)
    top_bb = _f(cfg, "RZ_TOP_BB_THRESHOLD", 0.85)
    bot_bb = _f(cfg, "RZ_BOT_BB_THRESHOLD", 0.15)
    zs_on = bool(_cg(cfg, "RZ_ZSCORE_ZONE_ENABLED", True))
    ob = zs_on & ((zs4 > 2.0) | ((zs1 > 2.0) & (zs4 > 1.5)))
    os = zs_on & ((zs4 < -2.0) | ((zs1 < -2.0) & (zs4 < -1.5)))
    if is_long:
        return (
            ((px > bbu) & (bbu > 0.0))
            | ((px > dch) & (dch > 0.0))
            | (bbp > top_bb)
            | (dcp > top_bb)
            | ob
        )
    return (
        ((px < bbl) & (bbl > 0.0))
        | ((px < dcl) & (dcl > 0.0))
        | (bbp < bot_bb)
        | (dcp < bot_bb)
        | os
    )


def _no_stf_data(npz):
    for k in _STALE_KEYS:
        try:
            a = np.asarray(npz.get(k), dtype=np.float64).reshape(-1)
        except Exception:
            continue
        if np.any(np.isfinite(a) & (a != 0.0)):
            return False
    return True


def precompute(npz, n, is_long, cfg):
    """Per-bar symgate block mask + leg detail. Pure predicates, no trading."""
    s = np.asarray(score_blocked(npz, n, is_long, cfg), dtype=bool).reshape(-1)[:n]
    z = np.asarray(zone_blocked(npz, n, is_long, cfg), dtype=bool).reshape(-1)[:n]
    bull, bear = speed_arrays(npz, n)
    smin = _f(cfg, "REENTRY_SYMGATE_SPEED_MIN", 0.5)
    slow = (bull < smin) if is_long else (bear < smin)
    if _no_stf_data(npz):
        slow = slow & ~((bull == 0.0) & (bear == 0.0))
    return {
        "blocked": (s | z | slow).astype(bool),
        "score": s.astype(bool),
        "zone": z.astype(bool),
        "slow": slow.astype(bool),
        "bull": bull,
        "bear": bear,
    }


def reclaimed(is_long, px, exit_px):
    """Live GUARANTEED reclaim bypass (no buffer): price back past the exit."""
    try:
        px, ex = float(px), float(exit_px)
    except (TypeError, ValueError):
        return False
    if px <= 0.0 or ex <= 0.0:
        return False
    return px >= ex if is_long else px <= ex
