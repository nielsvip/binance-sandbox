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
  REENTRY2_DIR_FAV_ENABLED .......... ez_positions_quick.py:17237 (BC_152 AND leg)
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

NPZ reality (frozen backtest NPZ): k_3m, wt1_3m/wt2_3m, wt_bullish_3m,
wt_cross_bars_ago_3m ABSENT; k_15m, sma_200_1h, close, bar_atr_rank_*,
wt_bullish_15m/1h/4h/D, wt_cross_bars_ago_15m/1h PRESENT.
Predicates needing absent keys return None on real NPZ and a real mask when
the caller supplies the key.

SKIPPED (no bar semantics — honest stop, do not fabricate):
  REENTRY_POST_CONSOL_MULT / REENTRY_K15M_PARTIAL_MULT / REENTRY_WT15M_SIZE_MULT
    sizing multipliers, no bar predicate (parent sizes live-side).
  BTC_GUARANTEED_REENTRY_MAX_AGE_BARS / BTC_GUARANTEED_REENTRY_MIN_GAP_BARS
    bar-age bookkeeping on position state; live hits are synthetic close<=thr.
  DAEMON_REENTRY_STALE_EXIT_ENABLED
    live R1b path parses exit from augment_reason string; no bar predicate.
  DELTA_REENTRY_Z_THRESHOLD
    needs delta_tracker speed_z, absent from NPZ (ez_manage.py:208 comment).
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


# === 14. REENTRY2_DIR_FAV_ENABLED (EPQ :17237) — BC_152 armed gate ===
def reentry2_dir_fav_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        if not _b(cfg, "REENTRY2_DIR_FAV_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
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
        return reentry2_dir_fav_armed(npz, n, is_long, cfg)
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
    raise KeyError(f"unknown reentry2 switch: {switch!r}")
