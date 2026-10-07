"""lane_vec_gates2.py — numpy twins for LIVE_ONLY risk-gate switches (both venues).

Live citations (exact mirror; defaults mirror config.py / config_tradier.py):
  ALL_TF_AGAINST_CLOSE_ENABLED ... ez_manage.py:6799 (entry veto, _batch1_template_live_gate,
    called by check_entry_vetting:2909) + :49438 (process_position close) ;
    tradier dead twin :5637-5649 (never called) ; MIN_TFS=4 both venues.
  BEAR_MARKET_MODE ................ ez_positions_quick.py:3389 (LONG block k_15m>=15) ;
    :3396-3398 score legs are score-only (no bar mask on SHORT).
  FUNDING_GATE_LONG_MAX ........... ez_positions_quick.py:12753-12777 via
    vec_paths/funding_gate.py:37 funding_long_veto (fr>=0.0005, nonzero, MTF bull<=0).
  FUNDING_GATE_SHORT_MIN .......... ez_positions_quick.py:12778-12780 via
    vec_paths/funding_gate.py:49 funding_short_veto (fr<=-0.0005, nonzero, MTF bear<=1).
  FUNDING_GATE_MTF_REQUIRED ....... vec_paths/funding_gate.py:40-46 (MTF-disagree leg).
  HTF4_CONF ....................... ez_manage.py:28076-28134 (crypto, default True) ;
    tradier_manage.py:24600-24610 (stocks, default False). Venue via kw venue=.
  DELTA_HTF_GATE .................. ez_manage.py:25956-25995 (crypto, default 'hh_hl_4h') ;
    tradier_manage.py:12899-12929 (stocks, default '4h'). Delta-signal itself is
    runtime-stateful (delta_tracker) — twin covers the HTF gate only.
  BANDAID_OFF_LOSER_RECOVER_PCT ... ez_manage.py:50441-50443 (origin gain < -0.25).
  BTC_BREAKOUT_ENTRY_ENABLED (P1) .. ez_manage.py:6981-6984 (dc_position_15m gate).
  AUGMENTED_POSITIONS_GUARD_FLOOR_MULT (P1) .. ez_manage.py:27615-27624
    (re-augment block: gain < mult*MIN_GAIN).

Polarity: mask True = the switch's condition FIRES (block/veto/fire). None = switch
off, required data absent, or side-inapplicable (parent ANDs/skips; None never blocks).
Default-inert: at live defaults each twin reproduces live exactly (verified legs below).

SKIPPED (honest stop — no bar predicate in live; not in SUPPORTED):
  P0 BEAR_MARKET_MODE_TRADIER — score-only (tradier:18543) + portfolio balancer
    threshold (tradier:14269); no per-bar predicate.
  P0 HTF_GATE_BYPASS_RZ — reason-string bypass (ez_positions_quick:12690/12702).
  P0 HTF_TREND_VETO_BYPASS_ENABLED — reason-string bypass (ez_manage:25586-25596).
  P0 DC_MOMENT_STRONG_THRESHOLD — score-only (ez_positions_quick:2568-2580) and
    key 0dc_moment absent from frozen NPZ.
  P1 ATR_LONG_WINDOW — computation-window param; live use is generic _thr stub
    (ez:6820) / ATR<=0.5 dead gate (tradier:5651, never called).
  P1 HIGH_GAIN_AUGMENTATION_MIN_SIZE — background-monitor notional gate
    (ez:46502-46506), no entry/exit bar decision.
  P1 GOLDEN_RULE_BASE_USD — sizing-loop param (ez:17502), no bar predicate.
  P1 BTC_DIVERGENCE_EXIT_AGAINST — live DivergenceState always empty
    (ez_positions_quick:2045-2049); gate never fires.
  P1 BTC_HARD_BLOCK_OTHER_ACCOUNTS — account-level; only _thr stub (ez:7016).
  P1 BTC_ROUND_BANDS_EACH_SIDE — level-count param (ez_positions_quick:2066).
  P1 BTC_TECH_EXIT_WT_MIN_TFS — BTC-loop-only exit (btc_loop:560) behind master
    BTC_DEDICATED_ENABLED default False; no general bar decision.
  P1 BTC_ACCEL_RAMP_REQUIRE_POSITIVE — changes accel counting mode
    (btc_loop:112-121), not itself a predicate.
  P1 HTF_TREND_VETO_BYPASS_REASONS — reason-string list (ez:25589).
  P1 INTRADAY_RATIO_* (7) — portfolio-ratio loop, LIVE-ONLY timers
    (tradier:24924-24981; config_tradier:3408).
  P1 EOD_SLIM_RATIO_ENABLED — time-window portfolio trim (tradier:24874).
  P1 MARKET_QUALITY_SCORE_ENABLED — score-bonus only (ez_positions_quick:4579).
  P1 MTF_FILTER_STRONG_BUY_QUICK_BYPASS — reason/action-string (ez:30915-30924).
  P1 OPEN_RATE_BREAKER_ENABLED / OPEN_RATE_MAX — event-rate infra (ez:31077-31079).
  P1 PER_SYM_GATE_FLAT_OPEN_ENFORCE — action gating, no bar data (tradier:26900).
  P1 RECENT_REDUCTION_GUARD_ENABLED / _WINDOW_S — wall-clock guard (ez:32537-32540).
  P1 SENTIMENT_REBAL_REDUCE_DEVIATION_THR / _COOLDOWN_MIN — portfolio rebalance
    (tradier:24136/24179).

NPZ reality (frozen backtest NPZ, 940 keys, probed on AAPL.npz): wt1/wt2_15m/1h/4h/D,
k_15m/k_4h/d_4h/k_D, high/low_4h+prev, high/low_15m+prev, ha_4h (int8 {-1,1}),
dc_position_15m, funding_rate_15m PRESENT; wt1/wt2_3m, k_3m, funding_rate(_3m/_5m),
0dc_moment, close_5m_prev ABSENT. Predicates needing absent keys return None.
ha_4h int mapping: 1=green verified (wt_dc_entry_scorer.py:442-444); -1=red is the
only consistent reading of the observed {-1,1} encoding (string arrays compare
exact 'green'/'red' per live).
"""
from __future__ import annotations

from typing import Any, Mapping
import functools
import math

import numpy as np

SUPPORTED = (
    "ALL_TF_AGAINST_CLOSE_ENABLED",
    "BEAR_MARKET_MODE",
    "FUNDING_GATE_LONG_MAX",
    "FUNDING_GATE_SHORT_MIN",
    "FUNDING_GATE_MTF_REQUIRED",
    "HTF4_CONF",
    "DELTA_HTF_GATE",
    "BANDAID_OFF_LOSER_RECOVER_PCT",
    "BTC_BREAKOUT_ENTRY_ENABLED",
    "AUGMENTED_POSITIONS_GUARD_FLOOR_MULT",
)

_ATF_TFS = ("3m", "15m", "1h", "4h", "D")
_MTF_TFS = ("15m", "1h", "4h", "D")
_FR_KEYS = ("funding_rate", "funding_rate_3m", "funding_rate_5m", "funding_rate_15m")


def _fail_open(func):
    @functools.wraps(func)
    def _wrap(*a, **k):
        try:
            return func(*a, **k)
        except Exception:
            return None

    return _wrap


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


def _s(cfg: Any, name: str, default: str) -> str:
    try:
        v = getattr(cfg, name, default)
        return str(v) if v is not None else default
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


def _col_raw(npz: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    """Raw column read (no float cast; for string/int-coded fields)."""
    if not _have(npz, key):
        return None
    try:
        a = np.asarray(npz[key]).reshape(-1)
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


def _ha_is(ha: np.ndarray, color: str) -> np.ndarray:
    """Live compares ha_4h strings ('green'/'red'); NPZ stores int8 {-1,1}."""
    if ha.dtype.kind in ("U", "S", "O"):
        try:
            low = np.char.lower(ha.astype(str))
        except Exception:
            return np.zeros(ha.shape[0], dtype=bool)
        return low == color
    try:
        v = ha.astype(float)
    except Exception:
        return np.zeros(ha.shape[0], dtype=bool)
    if color == "green":
        return np.isfinite(v) & (v == 1)
    return np.isfinite(v) & (v == -1)


def _funding_col(npz: Mapping[str, Any], n: int) -> np.ndarray | None:
    """Per-bar first-nonzero across live's key order (ez_positions_quick:12761)."""
    cols = []
    for k in _FR_KEYS:
        c = _col(npz, k, n)
        if c is not None:
            cols.append(np.where(np.isfinite(c), c, 0.0))
    if not cols:
        return None
    fr = cols[0]
    for c in cols[1:]:
        fr = np.where(fr != 0.0, fr, c)
    return fr


def _mtf_counts(npz: Mapping[str, Any], n: int) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Bull/bear WT counts over 15m/1h/4h/D (vec_paths/funding_gate._wt_counts)."""
    bull: np.ndarray | None = None
    bear: np.ndarray | None = None
    for tf in _MTF_TFS:
        w1 = _col(npz, f"wt1_{tf}", n)
        w2 = _col(npz, f"wt2_{tf}", n)
        if w1 is None or w2 is None:
            continue
        b = (w1 > w2).astype(float)
        s = (w1 < w2).astype(float)
        bull = b if bull is None else bull + b
        bear = s if bear is None else bear + s
    return bull, bear


# === 1. ALL_TF_AGAINST_CLOSE_ENABLED (ez:6799 entry veto / :49438 close) ===
@_fail_open
def all_tf_against_fire_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "ALL_TF_AGAINST_CLOSE_ENABLED", True):
        return None
    min_tfs = _i(cfg, "ALL_TF_AGAINST_CLOSE_MIN_TFS", 4)
    cnt: np.ndarray | None = None
    for tf in _ATF_TFS:
        w1 = _col(npz, f"wt1_{tf}", n)
        w2 = _col(npz, f"wt2_{tf}", n)
        if w1 is None or w2 is None:
            continue  # live .get default 0 vs 0 → not against → contributes 0
        leg = (w1 < w2) if is_long else (w1 > w2)
        cnt = leg.astype(int) if cnt is None else cnt + leg.astype(int)
    if cnt is None:
        return None
    return cnt >= min_tfs


# === 2. BEAR_MARKET_MODE (ez_positions_quick:3389 LONG block) ===
@_fail_open
def bear_mode_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "BEAR_MARKET_MODE", True):
        return None
    if not is_long:
        return None  # SHORT leg is score-only (:3396-3398), no bar predicate
    k = _col(npz, "k_15m", n)
    if k is None:
        return None
    return np.isfinite(k) & (k >= 15.0)


# === 3. FUNDING_GATE_LONG_MAX (funding_long_veto; LONG-only) ===
@_fail_open
def funding_long_veto_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not is_long:
        return None
    fr = _funding_col(npz, n)
    if fr is None:
        return None
    th = _f(cfg, "FUNDING_GATE_LONG_MAX", 0.0005)
    extreme = (fr != 0.0) & (fr >= th)
    if not _b(cfg, "FUNDING_GATE_MTF_REQUIRED", True):
        return extreme
    bull, _ = _mtf_counts(npz, n)
    if bull is None:
        return extreme  # shared module falls back to naive when no WT keys
    k = _f(cfg, "FUNDING_GATE_MTF_LONG_MAX_BULL_TFS", 0)
    return extreme & (bull <= k)


# === 4. FUNDING_GATE_SHORT_MIN (funding_short_veto; SHORT-only) ===
@_fail_open
def funding_short_veto_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if is_long:
        return None
    fr = _funding_col(npz, n)
    if fr is None:
        return None
    th = _f(cfg, "FUNDING_GATE_SHORT_MIN", -0.0005)
    extreme = (fr != 0.0) & (fr <= th)
    if not _b(cfg, "FUNDING_GATE_MTF_REQUIRED", True):
        return extreme
    _, bear = _mtf_counts(npz, n)
    if bear is None:
        return extreme
    k = _f(cfg, "FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS", 1)
    return extreme & (bear <= k)


# === 5. FUNDING_GATE_MTF_REQUIRED (pure MTF-disagree leg, gate-free) ===
@_fail_open
def funding_mtf_leg_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "FUNDING_GATE_MTF_REQUIRED", True):
        return None
    bull, bear = _mtf_counts(npz, n)
    if is_long:
        if bull is None:
            return None
        return bull <= _f(cfg, "FUNDING_GATE_MTF_LONG_MAX_BULL_TFS", 0)
    if bear is None:
        return None
    return bear <= _f(cfg, "FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS", 1)


# === 6. HTF4_CONF (ez:28076 crypto / tradier:24600 stocks; venue kw) ===
@_fail_open
def htf4_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, venue: str = "crypto") -> np.ndarray | None:
    if not _b(cfg, "HTF4_CONF", True):
        return None
    k4 = _col(npz, "k_4h", n)
    d4 = _col(npz, "d_4h", n)
    h4 = _col(npz, "high_4h", n)
    h4p = _col(npz, "high_4h_prev", n)
    l4 = _col(npz, "low_4h", n)
    l4p = _col(npz, "low_4h_prev", n)
    h15 = _col(npz, "high_15m", n)
    h15p = _col(npz, "high_15m_prev", n)
    l15 = _col(npz, "low_15m", n)
    l15p = _col(npz, "low_15m_prev", n)
    ha = _col_raw(npz, "ha_4h", n)
    if any(c is None for c in (k4, d4, h4, h4p, l4, l4p, h15, h15p, l15, l15p, ha)):
        return None
    assert k4 is not None and d4 is not None and h4 is not None and h4p is not None
    assert l4 is not None and l4p is not None and h15 is not None and h15p is not None
    assert l15 is not None and l15p is not None and ha is not None
    ok = np.isfinite(k4) & np.isfinite(d4)
    green = _ha_is(ha, "green")
    red = _ha_is(ha, "red")
    if str(venue).lower().startswith("stock") or str(venue).lower().startswith("trad"):
        hh4 = (h4 > h4p) & ((l4 > l4p) | green)
        ll4 = ((h4 < h4p) | red) & (l4 < l4p)
        hh15 = (h15 > h15p) & (l15 > l15p)
        ll15 = (h15 < h15p) & (l15 < l15p)
    else:
        pos4 = (h4 > 0) & (h4p > 0) & (l4 > 0) & (l4p > 0)
        pos15 = (h15 > 0) & (h15p > 0) & (l15 > 0) & (l15p > 0)
        hh4 = pos4 & (h4 > h4p) & (l4 > l4p)
        ll4 = pos4 & ((h4 < h4p) | red) & (l4 < l4p)
        hh15 = pos15 & (h15 > h15p) & (l15 > l15p)
        ll15 = pos15 & (h15 < h15p) & (l15 < l15p)
    if is_long:
        blocked = ~(ok & ((k4 > d4) | (hh4 & hh15)))
    else:
        blocked = ~(ok & ((k4 < d4) | (ll4 & ll15)))
    return blocked & np.isfinite(h4) & np.isfinite(l4)


# === 7. DELTA_HTF_GATE (ez:25956 crypto / tradier:12899 stocks; mode-driven) ===
@_fail_open
def delta_htf_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    gate = _s(cfg, "DELTA_HTF_GATE", "none")
    if gate in ("none", "", "off", "false", "False"):
        return np.zeros(n, dtype=bool)
    if gate == "hh_hl_4h":
        h4 = _col(npz, "high_4h", n)
        h4p = _col(npz, "high_4h_prev", n)
        l4 = _col(npz, "low_4h", n)
        l4p = _col(npz, "low_4h_prev", n)
        ha = _col_raw(npz, "ha_4h", n)
        if any(c is None for c in (h4, h4p, l4, l4p, ha)):
            return None
        assert h4 is not None and h4p is not None and l4 is not None and l4p is not None and ha is not None
        hh = (h4 > h4p) & (h4p > 0)
        hl = (l4 > l4p) & (l4p > 0)
        lh = (h4 < h4p) & (h4p > 0)
        ll = (l4 < l4p) & (l4p > 0)
        if is_long:
            struct_ok = (hh & hl) | _ha_is(ha, "green")
        else:
            struct_ok = (lh & ll) | _ha_is(ha, "red")
        return ~struct_ok
    if gate in ("4h", "4h_D", "4h_D_strict"):
        w14 = _col(npz, "wt1_4h", n)
        w24 = _col(npz, "wt2_4h", n)
        if w14 is None or w24 is None:
            return None
        ok4 = (w14 > w24) if is_long else (w14 < w24)
        blocked = ~ok4
        if gate in ("4h_D", "4h_D_strict"):
            w1d = _col(npz, "wt1_D", n)
            w2d = _col(npz, "wt2_D", n)
            if w1d is None or w2d is None:
                return None
            okd = (w1d > w2d) if is_long else (w1d < w2d)
            blocked = blocked | ~okd
        if gate == "4h_D_strict":
            k4 = _col(npz, "k_4h", n)
            kd = _col(npz, "k_D", n)
            if k4 is None or kd is None:
                return None
            if is_long:
                blocked = blocked | (k4 > 80) | (kd > 80)
            else:
                blocked = blocked | (k4 < 20) | (kd < 20)
        return blocked
    return np.zeros(n, dtype=bool)  # unknown mode: live compares == known modes → no block


# === 8. BANDAID_OFF_LOSER_RECOVER_PCT (ez:50441; position-state via gain_arr) ===
@_fail_open
def bandaid_loser_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None) -> np.ndarray | None:
    if gain_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    pct = _f(cfg, "BANDAID_OFF_LOSER_RECOVER_PCT", -0.25)
    return np.isfinite(g) & (g < pct)


# === 9. BTC_BREAKOUT_ENTRY_ENABLED (P1; ez:6981 dc_position_15m gate) ===
@_fail_open
def btc_breakout_block_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "BTC_BREAKOUT_ENTRY_ENABLED", True):
        return None
    dc = _col(npz, "dc_position_15m", n)
    if dc is None:
        return None
    if is_long:
        return np.isfinite(dc) & (dc >= 0.4)
    return np.isfinite(dc) & (dc <= 0.6)


# === 10. AUGMENTED_POSITIONS_GUARD_FLOOR_MULT (P1; ez:27615; position-state) ===
@_fail_open
def augmented_guard_block_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None, augmented_arr: Any = None
) -> np.ndarray | None:
    if gain_arr is None or augmented_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    try:
        aug = np.asarray(augmented_arr, dtype=bool).reshape(-1)
    except Exception:
        return None
    if aug.size < n:
        return None
    aug = aug[:n]
    floor = _f(cfg, "AUGMENTED_POSITIONS_GUARD_FLOOR_MULT", 0.5) * _f(cfg, "MIN_GAIN", 3.0)
    return aug & np.isfinite(g) & (g < floor)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "ALL_TF_AGAINST_CLOSE_ENABLED":
        return all_tf_against_fire_mask(npz, n, is_long, cfg)
    if switch == "BEAR_MARKET_MODE":
        return bear_mode_block_mask(npz, n, is_long, cfg)
    if switch == "FUNDING_GATE_LONG_MAX":
        return funding_long_veto_mask(npz, n, is_long, cfg)
    if switch == "FUNDING_GATE_SHORT_MIN":
        return funding_short_veto_mask(npz, n, is_long, cfg)
    if switch == "FUNDING_GATE_MTF_REQUIRED":
        return funding_mtf_leg_mask(npz, n, is_long, cfg)
    if switch == "HTF4_CONF":
        return htf4_block_mask(npz, n, is_long, cfg, venue=kw.get("venue", "crypto"))
    if switch == "DELTA_HTF_GATE":
        return delta_htf_block_mask(npz, n, is_long, cfg)
    if switch == "BANDAID_OFF_LOSER_RECOVER_PCT":
        return bandaid_loser_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    if switch == "BTC_BREAKOUT_ENTRY_ENABLED":
        return btc_breakout_block_mask(npz, n, is_long, cfg)
    if switch == "AUGMENTED_POSITIONS_GUARD_FLOOR_MULT":
        return augmented_guard_block_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"), augmented_arr=kw.get("augmented_arr"))
    raise KeyError(f"unknown gates2 switch: {switch!r}")
