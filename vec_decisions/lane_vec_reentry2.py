"""lane_vec_reentry2.py — numpy twins for the REENTRY LIVE_ONLY family (21 switches).

Live citations (real engine semantics; dead-wire B1 veto stubs NOT mirrored):
  BOUNCE_REENTRY_ENABLED ............ ez_positions_quick.py:3245/:3251 (master _bounce_enabled gate)
  BOUNCE_REENTRY_K_RESET_LONG ....... ez_positions_quick.py:3252/:3256 (LONG k_3m < 35 latch)
  BOUNCE_REENTRY_K_RESET_SHORT ...... ez_positions_quick.py:3253/:3258 (SHORT k_3m > 65 latch)
  MANDATORY_REENTRY_K_HIGH_BLOCK .... ez_positions_quick.py:3276/:3280 (LONG k_3m >= 80)
  MANDATORY_REENTRY_K_LOW_BLOCK ..... ez_positions_quick.py:3277/:3280 (SHORT k_3m <= 20)
  MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME ez_positions_quick.py:3275/:3279 (K-extreme gate)
  MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS ez_positions_quick.py:3278/:3321 (0.3pct + 3m + HTF)
  REENTRY_EXIT_RECLAIM_ENABLED ...... ez_manage.py:39475 (B00 master gate)
  REENTRY_EXIT_RECLAIM_BUFFER_PCT ... ez_manage.py:39478/:39479 (exit +/- buf%)
  REENTRY_POST_CONSOL_ENABLED ....... ez_manage.py:43568 (_compose_reentry_mult gate)
  REENTRY_POST_CONSOL_ATR_THRESHOLD . ez_manage.py:43570/:43575 (bar_atr_rank < thr count)
  REENTRY_POST_CONSOL_TFS_REQUIRED .. ez_manage.py:43572/:43580 (compressed >= req)
  REENTRY_PRICE_IMPROVE_PCT ......... ez_positions_quick.py:17224/:17228 (EPQ dip-vs-exit leg)
  REENTRY2_DIR_FAV_ENABLED .......... ez_positions_quick.py:32641/:32649 (EPQ k/d/wt AND-leg, triple-gated)
  REENTRY_CROSS_FRESHNESS_ENABLED ... ez_positions_quick.py:16979 (wt_cross_bars_ago < 5)
  REENTRY_EXHAUSTED_PARTIAL_ENABLED . ez_positions_quick.py:15811/:15816 (k_3m >95/<5 partial)
  BTC_GUARANTEED_REENTRY_ENABLED .... ez_manage.py:6999 (side of sma_200_1h filter)
  DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED ez_manage.py:57183 (SHORT wt1_3m<wt2_3m + k_3m>60)
  DIRECTION_FAVORABLE_REENTRY_ENABLED ez_positions_quick.py:17237 (AND leg, default OFF)
  DELTA_REENTRY_MIN_TF .............. tradier_manage.py:22633 + ez_manage.py:200 (WT-aligned TF count + 4h)
  OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED ez_reentry.py:306/:321 (3m align + HTF count)

Design (mirrors lane_vec_scalp_v3.py):
  - ENABLED switches -> armed mask (all-True) or None when off (BIBLE 39).
  - Threshold switches -> pure sub-condition mask, gate-free (parent ANDs with
    the armed mask). None ONLY when the required NPZ key / position state is
    absent — never fabricated.
  - Side-inapplicable thresholds -> None (live never reads them on that side).
  - Position-state legs (exit price) arrive via kw (exit_px_arr); absent -> None.
Defaults mirror live config.py exactly (True/35/65/80/20/0.2/0.15/2/0.08/5/2/1;
False for ALLOW_WT0/CROSS_FRESHNESS/XUNDER/DIR_FAV).

NPZ reality (frozen backtest NPZ): k_3m, d_3m, wt1_3m/wt2_3m, wt_bullish_3m,
wt_cross_bars_ago_3m, speed_z ABSENT; k_15m, d_15m, sma_200_1h, close,
bar_atr_rank_*, wt1_15m/wt2_15m, wt_bullish_15m/1h/4h/D,
wt_cross_bars_ago_15m/1h, wt_trough_structure_15m/wt_peak_structure_15m PRESENT.
Predicates needing absent keys return None on real NPZ and a real mask when
the caller supplies the key.

GAP batch2 2026-10-21: DELTA_REENTRY_Z_THRESHOLD wired as delta_z_pass_mask
  (None unless DELTA_REENTRY_FILTER_ENABLED and speed_z present);
  REENTRY2_DIR_FAV_ENABLED repointed from the all-ones stub to the real
  reentry2_dir_fav_mask (EPQ 32649 AND-leg, triple-gated);
  REENTRY_RALLY_K15M_MAX wired as rally_k15m_block_mask (None at cap>=100),
  consumed ONLY in the EPQ-gated reentry path (REENTRY_EPQ_MODEL_ENABLED).
SKIPPED (no bar semantics — honest stop, do not fabricate):
  REENTRY_POST_CONSOL_MULT / REENTRY_K15M_PARTIAL_MULT / REENTRY_WT15M_SIZE_MULT
    sizing multipliers, no bar predicate (parent sizes live-side).
  BTC_GUARANTEED_REENTRY_MAX_AGE_BARS / BTC_GUARANTEED_REENTRY_MIN_GAP_BARS
    bar-age bookkeeping on position state; live hits are synthetic close<=thr.
  DAEMON_REENTRY_STALE_EXIT_ENABLED
    live R1b path parses exit from augment_reason string; no bar predicate.
  OBLIGATORY_REENTRY_SCORE_TIER3
    score constant added when Tier3 fires, not a predicate.
  ABLATION_DISABLE_PERIODIC_REENTRY
    ablation kill-switch for a daemon path; no bar predicate (also venue-split
    default: config.py True vs config_tradier.py False).
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "BOUNCE_REENTRY_ENABLED",
    "BOUNCE_REENTRY_K_RESET_LONG",
    "BOUNCE_REENTRY_K_RESET_SHORT",
    "MANDATORY_REENTRY_K_HIGH_BLOCK",
    "MANDATORY_REENTRY_K_LOW_BLOCK",
    "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME",
    "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS",
    "REENTRY_EXIT_RECLAIM_ENABLED",
    "REENTRY_EXIT_RECLAIM_BUFFER_PCT",
    "REENTRY_POST_CONSOL_ENABLED",
    "REENTRY_POST_CONSOL_ATR_THRESHOLD",
    "REENTRY_POST_CONSOL_TFS_REQUIRED",
    "REENTRY_PRICE_IMPROVE_PCT",
    "REENTRY2_DIR_FAV_ENABLED",
    "REENTRY_CROSS_FRESHNESS_ENABLED",
    "REENTRY_EXHAUSTED_PARTIAL_ENABLED",
    "BTC_GUARANTEED_REENTRY_ENABLED",
    "DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED",
    "DIRECTION_FAVORABLE_REENTRY_ENABLED",
    "DELTA_REENTRY_MIN_TF",
    "OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED",
    "DELTA_REENTRY_Z_THRESHOLD",
    "REENTRY_RALLY_K15M_MAX",
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


def _i(cfg: Any, name: str, default: int) -> int:
    try:
        return int(getattr(cfg, name, default))
    except (TypeError, ValueError):
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


def _as_float_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _bull(npz: Mapping[str, Any], tf: str, n: int) -> np.ndarray | None:
    """wt_bullish_<tf>, or derived from the wt1/wt2 pair when both present."""
    try:
        if _have(npz, f"wt_bullish_{tf}"):
            a = np.asarray(npz[f"wt_bullish_{tf}"]).reshape(-1)
            if a.size < n:
                return None
            return a[:n].astype(bool)
        w1 = _col(npz, f"wt1_{tf}", n)
        w2 = _col(npz, f"wt2_{tf}", n)
        if w1 is None or w2 is None:
            return None
        return np.isfinite(w1) & np.isfinite(w2) & (w1 > w2)
    except Exception:
        return None


# === 1. BOUNCE_REENTRY_ENABLED (EPQ :3245/:3251) — master armed gate ===
def bounce_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "BOUNCE_REENTRY_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


# === 2/3. BOUNCE K-reset latches (EPQ :3252-:3259; k_3m strict, no 15m sub) ===
def bounce_k_reset_long_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not is_long:
            return None
        k = _col(npz, "k_3m", n)
        if k is None:
            return None
        return np.isfinite(k) & (k < _i(cfg, "BOUNCE_REENTRY_K_RESET_LONG", 35))
    except Exception:
        return None


def bounce_k_reset_short_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if is_long:
            return None
        k = _col(npz, "k_3m", n)
        if k is None:
            return None
        return np.isfinite(k) & (k > _i(cfg, "BOUNCE_REENTRY_K_RESET_SHORT", 65))
    except Exception:
        return None


# === 4/5. MANDATORY K-extreme block legs (EPQ :3276-:3280) ===
def mandatory_k_high_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not is_long:
            return None
        k = _col(npz, "k_3m", n)
        if k is None:
            return None
        return np.isfinite(k) & (k >= _f(cfg, "MANDATORY_REENTRY_K_HIGH_BLOCK", 80.0))
    except Exception:
        return None


def mandatory_k_low_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if is_long:
            return None
        k = _col(npz, "k_3m", n)
        if k is None:
            return None
        return np.isfinite(k) & (k <= _f(cfg, "MANDATORY_REENTRY_K_LOW_BLOCK", 20.0))
    except Exception:
        return None


# === 6. MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME (EPQ :3275) — armed gate ===
def mandatory_require_kx_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


# === 7. MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS (EPQ :3278/:3321) ===
# allow_wt0 AND |px/exit-1| > 0.003 AND 3m aligned AND HTF(1h/4h/D) >= 1.
def mandatory_wt0_strong_cross_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, exit_px_arr: Any = None) -> np.ndarray | None:
    try:
        if not _b(cfg, "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS", False):
            return None
        if exit_px_arr is None:
            return None
        px = _col(npz, "close", n)
        ex = _as_float_arr(exit_px_arr, n)
        if px is None or ex is None:
            return None
        b3 = _bull(npz, "3m", n)
        if b3 is None:
            return None
        m3 = b3 if is_long else ~b3
        htf = 0
        for tf in ("1h", "4h", "D"):
            b = _bull(npz, tf, n)
            if b is None:
                return None
            htf = htf + (b if is_long else ~b)
        with np.errstate(divide="ignore", invalid="ignore"):
            strong = np.isfinite(px) & (ex > 0) & (np.abs(px / ex - 1.0) > 0.003)
        return strong & m3 & (htf >= 1)
    except Exception:
        return None


# === 8. REENTRY_EXIT_RECLAIM_ENABLED (ez :39475) — B00 armed gate ===
def reclaim_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "REENTRY_EXIT_RECLAIM_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


# === 9. REENTRY_EXIT_RECLAIM_BUFFER_PCT (ez :39478) — price-vs-exit leg ===
def reclaim_buffer_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, exit_px_arr: Any = None) -> np.ndarray | None:
    try:
        if exit_px_arr is None:
            return None
        px = _col(npz, "close", n)
        ex = _as_float_arr(exit_px_arr, n)
        if px is None or ex is None:
            return None
        buf = _f(cfg, "REENTRY_EXIT_RECLAIM_BUFFER_PCT", 0.2) / 100.0
        ok = np.isfinite(px) & (ex > 0)
        if is_long:
            return ok & (px >= ex * (1.0 + buf))
        return ok & (px <= ex * (1.0 - buf))
    except Exception:
        return None


def reclaim_ready_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    """2026-10-13 USER "re-enter when a closed 15m bar reclaims the stop level … make sure it is guaranteed": the per-bar
    confirmation legs of live B00 PRICE_ABOVE_EXIT (ez_manage evaluate_reentry ~41531 / tradier_manage H2 ~39858), exact
    twin: close on the trend side of ema_9_15m (ema<=0 passes) AND 15m WT turning (LONG wt1>wt2, wt_velocity>0, k_15m<80;
    SHORT mirrored). k_3m leg bypassed exactly like live with USE_1M_3M_SIGNALS_ENABLED=False. The stateful price leg
    (close vs last exit x (1 +/- REENTRY_EXIT_RECLAIM_BUFFER_PCT)) is applied in simulate_one against the ledger exit price.
    None = switch off or a field missing (no fabrication)."""
    try:
        if not _b(cfg, "REENTRY_EXIT_RECLAIM_ENABLED", True):
            return None
        px = _col(npz, "close", n)
        ema9 = _col(npz, "ema_9_15m", n)
        w1 = _col(npz, "wt1_15m", n)
        w2 = _col(npz, "wt2_15m", n)
        vel = _col(npz, "wt_velocity_15m", n)
        k15 = _col(npz, "k_15m", n)
        if any(a is None for a in (px, ema9, w1, w2, vel, k15)):
            return None
        e = np.nan_to_num(ema9, nan=0.0)
        if is_long:
            sma_ok = (e <= 0) | (px > e)
            wt_ok = (w1 > w2) & (vel > 0) & (np.nan_to_num(k15, nan=50.0) < 80)
        else:
            sma_ok = (e <= 0) | (px < e)
            wt_ok = (w1 < w2) & (vel < 0) & (np.nan_to_num(k15, nan=50.0) > 20)
        return np.isfinite(px) & sma_ok & wt_ok
    except Exception:
        return None


# === 10. REENTRY_POST_CONSOL_ENABLED (ez :43568) — armed gate ===
def post_consol_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "REENTRY_POST_CONSOL_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


def _post_consol_compressed_count(npz: Mapping[str, Any], n: int, cfg: Any) -> np.ndarray | None:
    try:
        thr = _f(cfg, "REENTRY_POST_CONSOL_ATR_THRESHOLD", 0.15)
        cnt = np.zeros(n, dtype=int)
        for tf in ("1h", "4h", "D"):
            a = _col(npz, f"bar_atr_rank_{tf}", n)
            if a is None:
                return None
            cnt = cnt + (np.isfinite(a) & (a < thr))
        return cnt
    except Exception:
        return None


# === 11. REENTRY_POST_CONSOL_ATR_THRESHOLD (ez :43570/:43580) ===
def post_consol_atr_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        cnt = _post_consol_compressed_count(npz, n, cfg)
        if cnt is None:
            return None
        return cnt >= _i(cfg, "REENTRY_POST_CONSOL_TFS_REQUIRED", 2)
    except Exception:
        return None


# === 12. REENTRY_POST_CONSOL_TFS_REQUIRED (ez :43572/:43580) ===
def post_consol_tfs_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        cnt = _post_consol_compressed_count(npz, n, cfg)
        if cnt is None:
            return None
        return cnt >= _i(cfg, "REENTRY_POST_CONSOL_TFS_REQUIRED", 2)
    except Exception:
        return None


# === 13. REENTRY_PRICE_IMPROVE_PCT (EPQ :17224/:17228; live default 0.08) ===
# LONG: px <= exit*(1-pct/100) ; SHORT: px >= exit*(1+pct/100).
def price_improve_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, exit_px_arr: Any = None) -> np.ndarray | None:
    try:
        if exit_px_arr is None:
            return None
        px = _col(npz, "close", n)
        ex = _as_float_arr(exit_px_arr, n)
        if px is None or ex is None:
            return None
        pct = _f(cfg, "REENTRY_PRICE_IMPROVE_PCT", 0.08)
        ok = np.isfinite(px) & (ex > 0)
        if is_long:
            return ok & (px <= ex * (1.0 - pct / 100.0))
        return ok & (px >= ex * (1.0 + pct / 100.0))
    except Exception:
        return None


# === 14. REENTRY2_DIR_FAV_ENABLED (EPQ :32641-32649 AND-leg) ===
# LONG: k_3m>d_3m & k_15m>d_15m & k_3m<85 ; SHORT mirrored (& k_3m>15).
# WT confirm LONG wt1_15m>wt2_15m & wt1_3m>wt2_3m (SHORT mirrored).
# Triple-gated exactly like live: LEGACY_DIRECTION_FAVORABLE (def False) &
# DIRECTION_FAVORABLE_REENTRY_ENABLED (def False) & REENTRY2_DIR_FAV_ENABLED
# (def True). 3m keys absent from frozen NPZ -> None there (no fabrication).
# The 120m window + notional legs are position state: applied at the call site.
def reentry2_dir_fav_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "LEGACY_DIRECTION_FAVORABLE", False):
            return None
        if not _b(cfg, "DIRECTION_FAVORABLE_REENTRY_ENABLED", False):
            return None
        if not _b(cfg, "REENTRY2_DIR_FAV_ENABLED", True):
            return None
        k3 = _col(npz, "k_3m", n)
        d3 = _col(npz, "d_3m", n)
        k15 = _col(npz, "k_15m", n)
        d15 = _col(npz, "d_15m", n)
        w1_15 = _col(npz, "wt1_15m", n)
        w2_15 = _col(npz, "wt2_15m", n)
        w1_3 = _col(npz, "wt1_3m", n)
        w2_3 = _col(npz, "wt2_3m", n)
        if any(a is None for a in (k3, d3, k15, d15, w1_15, w2_15, w1_3, w2_3)):
            return None
        if is_long:
            dk = np.isfinite(k3) & np.isfinite(d3) & (k3 > d3) & (k3 < 85.0)
            dk = dk & np.isfinite(k15) & np.isfinite(d15) & (k15 > d15)
            wt = (w1_15 > w2_15) & (w1_3 > w2_3)
        else:
            dk = np.isfinite(k3) & np.isfinite(d3) & (k3 < d3) & (k3 > 15.0)
            dk = dk & np.isfinite(k15) & np.isfinite(d15) & (k15 < d15)
            wt = (w1_15 < w2_15) & (w1_3 < w2_3)
        return dk & wt
    except Exception:
        return None


# === 15. REENTRY_CROSS_FRESHNESS_ENABLED (EPQ :16979) ===
# fresh = any wt_cross_bars_ago_{3m,15m,1h} in [0, max_bars); live fail-open.
def cross_freshness_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "REENTRY_CROSS_FRESHNESS_ENABLED", False):
            return None
        max_bars = _i(cfg, "REENTRY_CROSS_MAX_BARS_AGO", 5)
        fresh = np.zeros(n, dtype=bool)
        seen = False
        for tf in ("3m", "15m", "1h"):
            a = _col(npz, f"wt_cross_bars_ago_{tf}", n)
            if a is None:
                continue
            seen = True
            fresh = fresh | (np.isfinite(a) & (a >= 0) & (a < max_bars))
        if not seen:
            return None
        return fresh
    except Exception:
        return None


# === 16. REENTRY_EXHAUSTED_PARTIAL_ENABLED (EPQ :15811/:15816) ===
# armed + exhausted: LONG k_3m > 95 ; SHORT k_3m < 5.
def exhausted_partial_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "REENTRY_EXHAUSTED_PARTIAL_ENABLED", True):
            return None
        k = _col(npz, "k_3m", n)
        if k is None:
            return None
        if is_long:
            return np.isfinite(k) & (k > 95)
        return np.isfinite(k) & (k < 5)
    except Exception:
        return None


# === 17. BTC_GUARANTEED_REENTRY_ENABLED (ez :6999) — side of sma_200_1h ===
def btc_guaranteed_sma_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "BTC_GUARANTEED_REENTRY_ENABLED", True):
            return None
        c = _col(npz, "close", n)
        sma = _col(npz, "sma_200_1h", n)
        if c is None or sma is None:
            return None
        ok = np.isfinite(c) & np.isfinite(sma) & (sma > 0)
        if is_long:
            return ok & (c > sma)
        return ok & (c < sma)
    except Exception:
        return None


# === 18. DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED (ez :57183) — SHORT only ===
# SHORT: wt1_3m < wt2_3m AND k_3m > 60.
def daemon_short_xunder_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if is_long:
            return None
        if not _b(cfg, "DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED", False):
            return None
        w1 = _col(npz, "wt1_3m", n)
        w2 = _col(npz, "wt2_3m", n)
        k = _col(npz, "k_3m", n)
        if w1 is None or w2 is None or k is None:
            return None
        return np.isfinite(w1) & np.isfinite(w2) & np.isfinite(k) & (w1 < w2) & (k > 60.0)
    except Exception:
        return None


# === 19. DIRECTION_FAVORABLE_REENTRY_ENABLED (EPQ :17237) — armed, default OFF ===
def direction_favorable_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "DIRECTION_FAVORABLE_REENTRY_ENABLED", False):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


# === 20. DELTA_REENTRY_MIN_TF (tradier :22633; ez :200-:213) ===
# aligned(3m/15m/1h/4h) >= min_tf AND 4h aligned; None when known < 2.
def delta_min_tf_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        req = _i(cfg, "DELTA_REENTRY_MIN_TF", 2)
        aligned = np.zeros(n, dtype=int)
        known = 0
        h4_ok = None
        for tf in ("3m", "15m", "1h", "4h"):
            b = _bull(npz, tf, n)
            if b is None:
                continue
            known += 1
            al = b if is_long else ~b
            aligned = aligned + al
            if tf == "4h":
                h4_ok = al
        if known < 2 or h4_ok is None:
            return None
        return (aligned >= req) & h4_ok
    except Exception:
        return None


# === 21. OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED (ez_reentry :306/:321) ===
# wt_3m aligned AND htf_count(3m/15m/1h/4h/D) >= req.
def obligatory_tier2_htf_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        req = _i(cfg, "OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED", 1)
        b3 = _bull(npz, "3m", n)
        if b3 is None:
            return None
        m3 = b3 if is_long else ~b3
        cnt = np.zeros(n, dtype=int)
        for tf in ("3m", "15m", "1h", "4h", "D"):
            b = _bull(npz, tf, n)
            if b is None:
                return None
            cnt = cnt + (b if is_long else ~b)
        return m3 & (cnt >= req)
    except Exception:
        return None


# === 22. DELTA_REENTRY_Z_THRESHOLD (ez_manage.py:221/:244-245 check_reentry_delta_tolerant) ===
# Live blocks reentry when speed z < thr; reachable only when
# DELTA_REENTRY_FILTER_ENABLED (default False -> early allow). speed_z is the
# delta_tracker output column, absent from frozen NPZ -> None there.
# Allow-side mask for the F1 OR convention: True = passes the z gate
# (live consumes the block side: z < thr -> DELTA_REENTRY_BLOCKED).
def delta_z_pass_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "DELTA_REENTRY_FILTER_ENABLED", False):
            return None
        z = _col(npz, "speed_z", n)
        if z is None:
            return None
        return np.isfinite(z) & (z >= _f(cfg, "DELTA_REENTRY_Z_THRESHOLD", 1.0))
    except Exception:
        return None


# === 23. REENTRY_RALLY_K15M_MAX (ez_positions_quick.py:31293 _epq_rally_k15m_blocked; :29682 inline) ===
# Block mask: LONG k_15m >= cap ; SHORT k_15m <= 100-cap ; cap >= 100 disables.
# k_15m present in frozen NPZ. Consumed ONLY in the EPQ-gated reentry path
# (REENTRY_EPQ_MODEL_ENABLED, default False) so QuickConfig default runs stay inert.
def rally_k15m_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        cap = _f(cfg, "REENTRY_RALLY_K15M_MAX", 100.0)
        if cap >= 100.0:
            return None
        k15 = _col(npz, "k_15m", n)
        if k15 is None:
            return None
        ok = np.isfinite(k15)
        if is_long:
            return ok & (k15 >= cap)
        return ok & (k15 <= (100.0 - cap))
    except Exception:
        return None


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "BOUNCE_REENTRY_ENABLED":
        return bounce_armed(npz, n, is_long, cfg)
    if switch == "BOUNCE_REENTRY_K_RESET_LONG":
        return bounce_k_reset_long_mask(npz, n, is_long, cfg)
    if switch == "BOUNCE_REENTRY_K_RESET_SHORT":
        return bounce_k_reset_short_mask(npz, n, is_long, cfg)
    if switch == "MANDATORY_REENTRY_K_HIGH_BLOCK":
        return mandatory_k_high_block_mask(npz, n, is_long, cfg)
    if switch == "MANDATORY_REENTRY_K_LOW_BLOCK":
        return mandatory_k_low_block_mask(npz, n, is_long, cfg)
    if switch == "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME":
        return mandatory_require_kx_armed(npz, n, is_long, cfg)
    if switch == "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS":
        return mandatory_wt0_strong_cross_mask(npz, n, is_long, cfg, exit_px_arr=kw.get("exit_px_arr"))
    if switch == "REENTRY_EXIT_RECLAIM_ENABLED":
        return reclaim_armed(npz, n, is_long, cfg)
    if switch == "REENTRY_EXIT_RECLAIM_BUFFER_PCT":
        return reclaim_buffer_mask(npz, n, is_long, cfg, exit_px_arr=kw.get("exit_px_arr"))
    if switch == "REENTRY_POST_CONSOL_ENABLED":
        return post_consol_armed(npz, n, is_long, cfg)
    if switch == "REENTRY_POST_CONSOL_ATR_THRESHOLD":
        return post_consol_atr_mask(npz, n, is_long, cfg)
    if switch == "REENTRY_POST_CONSOL_TFS_REQUIRED":
        return post_consol_tfs_mask(npz, n, is_long, cfg)
    if switch == "REENTRY_PRICE_IMPROVE_PCT":
        return price_improve_mask(npz, n, is_long, cfg, exit_px_arr=kw.get("exit_px_arr"))
    if switch == "REENTRY2_DIR_FAV_ENABLED":
        return reentry2_dir_fav_mask(npz, n, is_long, cfg)
    if switch == "REENTRY_CROSS_FRESHNESS_ENABLED":
        return cross_freshness_mask(npz, n, is_long, cfg)
    if switch == "REENTRY_EXHAUSTED_PARTIAL_ENABLED":
        return exhausted_partial_mask(npz, n, is_long, cfg)
    if switch == "BTC_GUARANTEED_REENTRY_ENABLED":
        return btc_guaranteed_sma_mask(npz, n, is_long, cfg)
    if switch == "DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED":
        return daemon_short_xunder_mask(npz, n, is_long, cfg)
    if switch == "DIRECTION_FAVORABLE_REENTRY_ENABLED":
        return direction_favorable_armed(npz, n, is_long, cfg)
    if switch == "DELTA_REENTRY_MIN_TF":
        return delta_min_tf_mask(npz, n, is_long, cfg)
    if switch == "OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED":
        return obligatory_tier2_htf_mask(npz, n, is_long, cfg)
    if switch == "DELTA_REENTRY_Z_THRESHOLD":
        return delta_z_pass_mask(npz, n, is_long, cfg)
    if switch == "REENTRY_RALLY_K15M_MAX":
        return rally_k15m_block_mask(npz, n, is_long, cfg)
    raise KeyError(f"unknown reentry2 switch: {switch!r}")
