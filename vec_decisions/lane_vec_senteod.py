"""lane_vec_senteod.py — numpy twins for the SENTIMENT_REBAL stocks LIVE_ONLY pair.

SUPPORTED (2 switches, both read live in tradier_manage.periodic_sentiment_rebalancing):
  SENTIMENT_REBAL_REDUCE_DEVIATION_THR .. tradier_manage.py:24585 (default 0.50, config_tradier.py:4219)
  SENTIMENT_REBAL_COOLDOWN_MIN .......... tradier_manage.py:24542 (default 240.0, config_tradier.py:4221)

EOD VERDICT — STOP, NOT WIRED (do not add EOD_SLIM_RATIO_ENABLED here):
  The user belief ("EOD_SLIM_RATIO is the daily recalc of 30D gap up/down counts")
  is FALSE. Live EOD_SLIM_RATIO_ENABLED (tradier_manage.py:25280, default False,
  config_tradier.py:962) gates last_hour_balancing_loop: on weekdays 15:15-15:58 ET,
  target_long_pct = calculate_unified_market_ratio() (:24752 — live market_snapshot
  breadth price-vs-ema15 fused 50/50 with scraper sentiment 0market_sentiment_score,
  amplified by RATIO_MULTIPLIER_TRADIER, damped by movement intensity), actual =
  get_current_portfolio_balance() (:24833 — live dollar L/S exposure); if
  |actual - target| > 0.10 it trims 30% of the worst-gain position on the overweight
  side (:25289-:25320). No gap counts, no 30D window, no recalc anywhere in that path
  (the _GAP_INVENTORY machinery at :205 is separate gap-risk code, never read by the
  EOD loop). Both inputs (cross-symbol snapshot, live portfolio dollars) are
  non-vectorizable portfolio state, so per the stop rule this file wires NO EOD twin
  and get() raises KeyError for it. Master kill switch SENTIMENT_REBALANCER_ENABLED
  (default False, :24513) is likewise out of scope — this lane twins the two
  threshold/cooldown switches only, for user-ordered backtesting.

L/S-RATIO GAP ANALYSIS (which legs are exact vs approximated vs None):
  Live reduce leg (:24584-:24586): deviation_pct = (ideal_qty - current_qty) /
  current_qty, fires when deviation_pct < -REDUCE_THR. ideal_qty comes from
  calculate_quantity_complex(..., "REBALANCE", ...) (:24581 → :18978 → :19090), a
  ~15-leg sizing chain (knife HA penalty, time-of-day focus, K-align, golden-angles
  DC/WT, sentiment_mult, DC positioning, volume, MFI, divergence, WT bonuses, market
  bias, ...). Reproducing ideal_qty in vec would be reimplemented-logic sizing —
  forbidden — and needs keys absent from frozen NPZ, so:
  - EXACT: the deviation comparison itself (dev < -thr) given deviation_pct, and the
    cooldown comparison (both ages >= cooldown_s) given ages — pure arithmetic,
    identical to live including the missing-timestamp → 9999s rule (:24546-:24551).
  - APPROXIMATED: nothing. No partial ideal_qty legs are faked from NPZ.
  - None (fail-open): deviation/cooldown whenever the caller cannot supply position
    state (ideal/current qty or last reduce/augment ages); NPZ-only evaluation always
    returns None because no NPZ key carries deviation or L/S-ratio state.
  The sibling live gates on the same path ($100 min notional :24588, gain >= 0.3
  :24591, LTF-contradiction 2/5 :24598-:24608, WT 3-of-4 TFs against :24611-:24621,
  attempt cooldown :24562, min-hold :24567) belong to OTHER switches, not these two —
  the parent ANDs their own twins; this file twins the two named switches gate-free.

NPZ reality (backtest_v8/indicators/AAPL.npz, 940 keys, probed 2026-10-05):
  market_sentiment_score PRESENT but CONSTANT 50.0 (nunique=1) = neutral placeholder,
  not scraper sentiment; 0market_sentiment_score / 0market_sentiment_local /
  0sentiment_strength / 0sentiment_rank ALL ABSENT; long_pct / ls_ratio /
  unified_market_ratio / breadth ALL ABSENT. Nothing usable — masks take position
  state as caller-supplied arrays (same pattern as scalp_v3 gain/max_gain/augmented).

Design (mirrors lane_vec_scalp_v3.py):
  - Threshold/cooldown switches -> pure sub-condition masks, gate-free.
  - None ONLY when required position-state input is absent — never fabricated.
  - Default-inert: at live defaults the masks equal the live comparisons exactly.
  - None means UNKNOWN: the caller must fail open (treat as no-restriction), never
    fail closed.
"""

from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "SENTIMENT_REBAL_REDUCE_DEVIATION_THR",
    "SENTIMENT_REBAL_COOLDOWN_MIN",
)


def _f(cfg: Any, name: str, default: float) -> float:
    try:
        v = float(getattr(cfg, name, default))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _b(cfg: Any, name: str, default: bool) -> bool:
    try:
        return bool(getattr(cfg, name, default))
    except Exception:
        return default


def _have(npz: Mapping[str, Any], key: str) -> bool:
    try:
        return key in npz
    except Exception:
        return False


def _col(npz: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    """Strict column read: None when the key is absent (no fabrication)."""
    if not _have(npz, key):
        return None
    try:
        a = np.asarray(npz[key], dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _as_bool_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return np.isfinite(a[:n])


def _as_float_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


# === 1. SENTIMENT_REBAL_REDUCE_DEVIATION_THR (live :24584-:24586) ===
# Fires when deviation_pct = (ideal_qty - current_qty) / current_qty < -thr.
# Side-independent leg (side enters upstream via ideal_qty sizing); is_long kept
# for dispatcher uniformity. deviation is position state, not NPZ — caller passes
# deviation_pct_arr directly, or ideal_qty_arr + current_qty_arr to derive it.
def sentiment_rebal_reduce_deviation_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    deviation_pct_arr: Any = None,
    ideal_qty_arr: Any = None,
    current_qty_arr: Any = None,
) -> np.ndarray | None:
    del npz, is_long
    thr = _f(cfg, "SENTIMENT_REBAL_REDUCE_DEVIATION_THR", 0.50)
    if deviation_pct_arr is not None:
        dev = _as_float_arr(deviation_pct_arr, n)
        if dev is None:
            return None
    elif ideal_qty_arr is not None and current_qty_arr is not None:
        ideal = _as_float_arr(ideal_qty_arr, n)
        cur = _as_float_arr(current_qty_arr, n)
        if ideal is None or cur is None:
            return None
        with np.errstate(divide="ignore", invalid="ignore"):
            dev = np.where(cur != 0, (ideal - cur) / cur, np.nan)
    else:
        return None
    return np.isfinite(dev) & (dev < -thr)


# === 2. SENTIMENT_REBAL_COOLDOWN_MIN (live :24542-:24553) ===
# Live skips the position when age(last_reduction) < cd OR age(last_aug) < cd.
# Twin returns the elapsed/allowed mask: True = cooldown satisfied on BOTH legs.
# Ages are seconds since the event; NaN = unknown/missing timestamp -> 9999.0s
# exactly like live _age_s (:24546-:24551). LIVE QUIRK, mirrored: at the 240min
# default (14400s) a never-touched position (9999s = 166.7min) is BLOCKED.
def sentiment_rebal_cooldown_elapsed_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    last_reduce_age_s: Any = None,
    last_aug_age_s: Any = None,
) -> np.ndarray | None:
    del npz, is_long
    if last_reduce_age_s is None or last_aug_age_s is None:
        return None
    age_red = _as_float_arr(last_reduce_age_s, n)
    age_aug = _as_float_arr(last_aug_age_s, n)
    if age_red is None or age_aug is None:
        return None
    age_red = np.where(np.isfinite(age_red), age_red, 9999.0)
    age_aug = np.where(np.isfinite(age_aug), age_aug, 9999.0)
    cd_s = _f(cfg, "SENTIMENT_REBAL_COOLDOWN_MIN", 240.0) * 60.0
    return (age_red >= cd_s) & (age_aug >= cd_s)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(
    switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any
) -> np.ndarray | None:
    if switch == "SENTIMENT_REBAL_REDUCE_DEVIATION_THR":
        return sentiment_rebal_reduce_deviation_mask(
            npz,
            n,
            is_long,
            cfg,
            deviation_pct_arr=kw.get("deviation_pct_arr"),
            ideal_qty_arr=kw.get("ideal_qty_arr"),
            current_qty_arr=kw.get("current_qty_arr"),
        )
    if switch == "SENTIMENT_REBAL_COOLDOWN_MIN":
        return sentiment_rebal_cooldown_elapsed_mask(
            npz,
            n,
            is_long,
            cfg,
            last_reduce_age_s=kw.get("last_reduce_age_s"),
            last_aug_age_s=kw.get("last_aug_age_s"),
        )
    if switch == "EOD_SLIM_RATIO_ENABLED":
        raise KeyError(
            "EOD_SLIM_RATIO_ENABLED deliberately unwired: live is last-hour portfolio L/S trim, not the 30D gap recalc (see module docstring)"
        )
    raise KeyError(f"unknown senteod switch: {switch!r}")
