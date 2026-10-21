"""vec_decisions/btc_dedicated_entry.py — vectorized BTC dedicated entry override (crypto).

Ports the LIVE BTC-loop entry override to a per-bar allow mask. Live scalar chain:

  ez_positions_quick.py:29364-29378  caller gate: _btc_dedicated_active() only,
    then _btc_dedicated_entry_decision() or fall through to rate()
  ez_positions_quick.py:3348-3356   _btc_dedicated_active() = BTC_DEDICATED_ENABLED
    + symbol in BTC_DEDICATED_SYMBOLS + account in BTC_DEDICATED_ACCOUNTS
  ez_positions_quick.py:3430-3465   _btc_dedicated_entry_decision():
    head-gate config.ENABLE_BTC_DEDICATED_ENTRY is True (config.py:1519, False),
    else None (fall through to rate()); firing -> (100.0, True), else (0.0, False)
  ez_positions_quick.py:3370-3427   _btc_build_features_from_indicators():
    accel legs over tfs ("3m","15m","1h","4h","D") from wt_velocity_{tf} /
    wt_acceleration_{tf} with velocity_prev = velocity - acceleration (:3390);
    divergence = EMPTY DivergenceState (:3399-3403, "no div block/exit");
    red zone from the wt_dc leg (bb_pct_b_4h maps to TOP/BOTTOM/BASELINE at
    :3406-3412, missing -> 0.5 BASELINE — all three count as active, so the
    leg is outcome-invariant and not re-read) + real round-level proximity
    from price (fib_levels_per_tf=None)
  btc_loop.py:394-434 / 437-468    should_enter_btc_long/short():
    opposing-divergence veto (>= BTC_DIVERGENCE_{BEAR,BULL}_MIN_INDS, default 2),
    RZ-as-boost softening (BTC_RZ_AS_BOOST_ENABLED True -> eff_min_tfs =
    BTC_ACCEL_RAMP_MIN_TFS - BTC_RZ_SOFTEN_ACCEL_BY when RZ active), legacy
    NO_RED_ZONE gate when boost off, accel-ramp side + min-TF gate
    (BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP True, default min 5 TFs).

Caller applies: entry_sig = entry_sig & allow_mask (ONE call site in
v12_quick_engine.simulate_one, gated on ENABLE_BTC_DEDICATED_ENTRY + sym in
BTC_DEDICATED_SYMBOLS). Switch default False -> parent never calls -> the
baseline is untouched (BIBLE section 14: default flip delta exactly 0).

NPZ availability (frozen backtest_v8/indicators set: 15m/1h/4h/D + sma_200_1m):
  PRESENT: wt_velocity_{15m,1h,4h,D}, wt_acceleration_{15m,1h,4h,D}
    (backtest_v8_precompute.py per-TF writer; HTF loop covers 15m/1h/4h/D),
    bb_pct_b_4h (NPZ_FIELD_SPEC.md), close (close_{tf}/close).
  ABSENT: wt_velocity_3m / wt_acceleration_3m (no 3m in frozen set). Missing TF
    legs evaluate as zeros -> non-aligned, EXACTLY like live indicators.get()
    defaults (v=0,a=0,vp=0 aligns with neither side). Consequence: at default
    BTC_ACCEL_RAMP_MIN_TFS=5 with RZ soften 1, eff_min=4 = every available TF
    must align; without an RZ-softened read the gate cannot pass on frozen
    NPZs. Reported as unresolved (needs 3m regen), NOT worked around.

Divergence is zeros BY LIVE DESIGN (caller feeds an empty DivergenceState),
so the veto is structurally inert at default thresholds — same as live.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

ACCEL_TFS = ("3m", "15m", "1h", "4h", "D")


def _arr(npz: Mapping[str, Any], key: str, n: int, default: float) -> np.ndarray:
    try:
        v = npz.get(key)
        if v is not None:
            a = np.asarray(v, dtype=float).reshape(-1)
            if a.size >= n:
                out = a[:n].copy()
                out[~np.isfinite(out)] = default
                return out
    except Exception:
        pass
    return np.full(n, float(default), dtype=float)


def _close(npz: Mapping[str, Any], n: int) -> np.ndarray | None:
    for key in (
        "close",
        "close_15m",
        "close_1h",
        "close_4h",
        "close_D",
        "close_3m",
        "close_5m",
    ):
        try:
            v = npz.get(key)
            if v is not None:
                a = np.asarray(v, dtype=float).reshape(-1)
                if (
                    a.size >= n
                    and np.count_nonzero(np.isfinite(a[:n]) & (a[:n] > 0)) > n * 0.5
                ):
                    return a[:n].astype(float)
        except Exception:
            continue
    return None


def allow_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
) -> np.ndarray | None:
    """Per-bar BTC-loop entry allow mask. True where live would fire (100.0, True).

    Fail-open (None) only when close is unusable (live falls through to rate())
    or on exception. Missing indicator keys degrade to live .get() defaults,
    never to invented values.
    """
    try:
        close = _close(npz, n)
        if close is None:
            return None
        pos = np.isfinite(close) & (close > 0)

        require_positive = bool(getattr(cfg, "BTC_ACCEL_RAMP_REQUIRE_POSITIVE", True))
        bull_aligned = np.zeros(n, dtype=np.int64)
        bear_aligned = np.zeros(n, dtype=np.int64)
        for tf in ACCEL_TFS:
            v = _arr(npz, f"wt_velocity_{tf}", n, 0.0)
            a = _arr(npz, f"wt_acceleration_{tf}", n, 0.0)
            v_prev = v - a
            if require_positive:
                bull_aligned += ((v > v_prev) & (v_prev > 0)).astype(np.int64)
                bear_aligned += ((v < v_prev) & (v_prev < 0)).astype(np.int64)
            else:
                bull_aligned += (v > v_prev).astype(np.int64)
                bear_aligned += (v < v_prev).astype(np.int64)
        side_bull = (bull_aligned > bear_aligned) & (bull_aligned > 0)
        side_bear = (bear_aligned > bull_aligned) & (bear_aligned > 0)

        # Live maps bb_pct_b_4h to TOP (>=0.85) / BOTTOM (<=0.15) / BASELINE
        # (missing -> 0.5 -> BASELINE) and treats all three as wt_dc-active
        # (build_red_zone_state: wt_dc_active = zone in
        # ("BASELINE","TOP","BOTTOM")), so the wt_dc leg is active on every
        # bar by construction — same as live.
        wt_dc_active = np.ones(n, dtype=bool)
        primary_inc = float(getattr(cfg, "BTC_ROUND_INC_PRIMARY_USD", 5000.0) or 5000.0)
        secondary_inc = float(
            getattr(cfg, "BTC_ROUND_INC_SECONDARY_USD", 1000.0) or 1000.0
        )
        bands = int(getattr(cfg, "BTC_ROUND_BANDS_EACH_SIDE", 8) or 8)
        prox_pct = float(getattr(cfg, "BTC_RZ_PROXIMITY_PCT", 0.5) or 0.0)
        round_within = np.zeros(n, dtype=bool)
        if prox_pct > 0 and bands > 0:
            tol = prox_pct / 100.0
            with np.errstate(divide="ignore", invalid="ignore"):
                for inc in (primary_inc, secondary_inc):
                    if not np.isfinite(inc) or inc <= 0:
                        continue
                    base = np.floor(close / inc) * inc
                    best = np.full(n, np.inf)
                    for k in range(bands):
                        for lvl in (base - inc * k, base + inc * (k + 1)):
                            d = np.abs(close - lvl) / np.where(pos, close, 1.0)
                            best = np.minimum(
                                best, np.where(pos & np.isfinite(lvl), d, np.inf)
                            )
                    round_within |= best <= tol
        rz_active = (round_within | wt_dc_active) & pos

        if bool(getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True)) and bool(
            getattr(cfg, "BTC_DIVERGENCE_ENABLED", True)
        ):
            opp_min = int(
                getattr(
                    cfg,
                    (
                        "BTC_DIVERGENCE_BEAR_MIN_INDS"
                        if is_long
                        else "BTC_DIVERGENCE_BULL_MIN_INDS"
                    ),
                    2,
                )
                or 2
            )
            opp_inds = 0
            if opp_inds >= opp_min:
                return np.zeros(n, dtype=bool)

        rz_boost_mode = bool(getattr(cfg, "BTC_RZ_AS_BOOST_ENABLED", True))
        soften_by = int(getattr(cfg, "BTC_RZ_SOFTEN_ACCEL_BY", 1) or 0)
        soften = np.where(rz_boost_mode & rz_active, soften_by, 0)
        if not rz_boost_mode and bool(
            getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ", True)
        ):
            base_allow = rz_active.copy()
        else:
            base_allow = np.ones(n, dtype=bool)
        if bool(getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", True)):
            min_tfs = int(getattr(cfg, "BTC_ACCEL_RAMP_MIN_TFS", 5) or 5)
            eff_min = np.maximum(1, min_tfs - soften)
            if is_long:
                gate = side_bull & (bull_aligned >= eff_min)
            else:
                gate = side_bear & (bear_aligned >= eff_min)
            base_allow &= gate
        return (base_allow & pos).astype(bool)
    except Exception:
        return None
