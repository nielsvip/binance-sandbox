"""lane_vec_wave5.py — numpy twins for the last LIVE_ONLY switches with bar semantics (5 switches).

Live citations (exact defaults mirror config.py / config_tradier.py):
  PER_SYM_GATE_FLAT_OPEN_ENFORCE .. tradier_manage.py:27250 (extends the
    PER_SYM_LIVE_GATE to _opens_exposure actions when True; live read fallback
    True, config_tradier.py:99 default False — temp OFF per 2026-09-28 USER
    order). Flat-open def :27051-27052 (|amt|<0.0001, non-reduce/non-exit/
    non-HEDGE_CLOSE). Block conditions :27321-27332 (DISABLED tag, NO_TRADES,
    trades==0 & no sharpe, gain<=0, gain_vs_bh<=0, gain<=bh). Venue: trb/trc/tra
    only (:27251); ez_manage never reads it (stocks-only twin).
  BTC_TECH_EXIT_WT_MIN_TFS ......... btc_loop.py:560-561 (BOUNCE exit leg:
    wt_against_count >= min -> WT_AGAINST_NOF5). TF set + polarity:
    btc_loop_strategy.pine:415-427 (3m/15m/1h/4h/D; LONG w1<w2, SHORT w1>w2;
    exit use :570-571). Default 3 (config.py:4256). Master BTC_DEDICATED_ENABLED
    (default False, btc_loop.py:495) is parent-owned — twin is gate-free.
  GOLDEN_RULE_BASE_USD ............. backtest_v12_engine.py:7059 (base read,
    default 5.0 = config.py:1097). Causal rule: trigger (15m DC/BB break +
    3m WT cross-state :7099-7132) -> target_usd = base*mult with cascade mult
    1h->1.5 / 4h->2.0 / D->3.0 (:7143-7158; mult defaults :7068-7071). Price
    probe order :7075 (close_5m/close/current_price); WT probe :7079-7080
    (5m then 3m). NOTE: live _golden_rule_loop (ez_manage.py:17584 base read)
    now runs breakout/retest mults 0.1/5.0 (:17726-17731) — the twin ports the
    BACKTEST cascade per coordinator order; base_usd is linear in both.
  MOVER_DETECTION_ENABLED ........... ez_positions_quick.py:4933 (scan_movers
    master gate). Default True (config.py:2737). Bar predicate MOVER_THRESHOLD
    already twinned (lane_vec_mopup3.py) — this twin is the missing ENABLED arm.
  VOL_SPIKE_ENABLED ................. ez_positions_quick.py:11816 (detect_volume_
    spike master gate; detector :11818-11839 already twinned by the
    vol_spike_reversal lane). Default True (config.py:2552).

Design (mirrors lane_vec_scalp_v3.py / lane_vec_entryq2.py / lane_vec_mopup3.py):
  - ENABLED masters -> armed mask (all-True) or None when off.
  - BTC WT leg -> pure sub-condition mask, gate-free (parent ANDs with the BTC
    master). Mirrors wrong_side_wt_mask (lane_vec_entryq2.py:419-438) exactly:
    per-TF (0,0) pairs skipped, available pairs counted, count >= req.
  - GOLDEN_RULE_BASE_USD -> float scale array (mopup3 contract
    lane_vec_mopup3.py:406-418): (base/5.0)*cascade_mult where the GR trigger
    fires, 1.0 elsewhere; None when the WT pair / price column is absent or
    base<=0. Cascade mults + dc/bb flags arrive via kw as bare values (foreign
    rows — never re-read here). Vote-signal path (default off :7116-7134), HTF
    veto (default off :7135-7142) and tradier integer-lot rounding (:7160-7163)
    are out of scope, documented.
  - PER_SYM flat-open leg -> BLOCK mask (True = gate FIRES = block this
    exposure-open). None when the switch is off (live default), when flat_amt
    state is absent, or on any error. Per-sym stats arrive via kw as resolved
    scalars (live resolution chains :27312-27317 are caller-side); flat epsilon
    0.0001 mirrors :27051 exactly. Caller must query only for exposure-opening
    actions on the stocks venue (action-type legs :27052 are engine context).
Polarity: mask True = condition FIRES (block/exit-trigger/armed). None = switch
off, required data absent, or side-inapplicable. Default-inert: at live defaults
each twin reproduces live exactly. Fail-open: unexpected error -> None.

NPZ reality (frozen backtest NPZ, probed GALAUSDT 1165 keys + 1000000MOGUSDT):
  PRESENT: wt1/wt2_15m/1h/4h/D, dc_high/low_15m/1h/4h/D, bb_upper/lower_15m/1h/
    4h/D, close, open/high/low/close_15m, k/d_1h/15m, relative_volume_15m.
  ABSENT: all 3m/5m WT keys (wt1/wt2_3m, wt1_5m/wt2_5m), k_3m, close_5m,
    relative_volume_3m. Twins needing absent keys return None on real NPZ and a
    real result when the caller supplies the key (probe proves both).

SKIPPED (grep-hunt 2026-10-05 — verified in live code; not in SUPPORTED):
  HTF1_CONF — simplified k/d live rule already twinned (stocks ema_alignment
    lane -> tradier:29310-29338/29338/29670-29698); full HH/HL rule dead both
    venues (crypto explicitly disabled ez:28109-28110; stocks zero callers).
  HTF4_CONF / VOL_SPIKE thresholds / MOVER_THRESHOLD / HLR_PTS+SZ / SATOSHIT fam
    — already twinned (gates2 / vol_spike_reversal / mopup3 / wirec_hlr_family /
    5 satoshit lanes).
  AUGMENT_WT_CROSS/3TF/HTF_TREND — audit-stub reads only (tradier:6810-6821).
  BREAKOUT_TF_SIZE_* — reason-string parsing (tradier:23804-23826).
  DC_WIDTH_SIZING — ranking-runtime state (ez_positions_quick:1082-1096).
  MIN_PERC_FROM_SMA_1/15 — additive-SP ladder in stateful sizing (ez:26529-549).
  REENTRY_SYMGATE — meta-gate to full exit engine (tradier:28679) / delta_tracker
    runtime (ez_positions_quick:16531-16564). ENTRY_SYMGATE same delta engine.
  BREAKEVEN_EXIT_AFTER_BARS fam — master default False + position-state exit.
  CONVICTION_SIZING / TIER_* / SYMBOL_PERF_* — ledger state (ez:8093-8097).
  GOLDEN_PULLBACK fam — exploding-ledger gate (ez:8032-8034), no frozen mirror.
  RSI2/EMA200_STOCHRSI/TRIPLE_CONF/MOMENTUM_FADE/EMA_PULLBACK/STDEV_BOUNCE/
    BB_RSI_STOCH fams — masters default False (dead).
  HEDGE_TRIGGER_REQUIRE_WT_* / HEDGE_WT_VEL / HEDGE_DC_REJECT / OBLIGATORY_HEDGE_
    WT_* — hedge-path only (default False/0; 3m keys absent).
  BTC_DIVERGENCE_MIN_INDS — custom-detector count (btc_loop:568-570).
  R1_USE_DC_4BAR — field-mode switch, no predicate. RED_ZONE_GATE — off + redis.
  R_S*/R_Z*/RE_* — disabled research (defaults off/0).
  Score-only (no bar predicate): LIVE_ENTRY_ENGINE_MIN_SCORE/BOOST_SCORE,
    STDEV_BREAKOUT_SCORE/RETEST_SCORE, MOVER_SCORE_BONUS/LINEARITY/VOL_MIN,
    TR_* boycotts, RP_*, DC_MOMENT_*, MTS_ENTRY_QUALITY_*/MTS_BOTTOM_MIN (mopup3
    kw), MARKET_QUALITY_SCORE.
  Infra/ledger/portfolio: LS_RATIO_*, OPEN_RATE_*, LEADERBOARD_FILTER, PERSIST_*,
    file/path/interval/account/heartbeat/cooldown switches.
  OVERRIDE NOTE: sibling lanes skipped the 3 mandated targets as "no bar
    predicate" (gates2 P1 notes; mopup3 SKIPPED). Coordinator explicitly ordered
    these twins with the specified designs (block-via-kw / gate-free WT leg /
    backtest-cascade scale) — parent direction supersedes sibling skip notes.
"""

from __future__ import annotations

from typing import Any, Mapping
import functools
import math

import numpy as np

SUPPORTED = (
    "PER_SYM_GATE_FLAT_OPEN_ENFORCE",
    "BTC_TECH_EXIT_WT_MIN_TFS",
    "GOLDEN_RULE_BASE_USD",
    "MOVER_DETECTION_ENABLED",
    "VOL_SPIKE_ENABLED",
)

_GR_BASE_DEFAULT = 5.0  # config.py:1097 GOLDEN_RULE_BASE_USD live default
_BTC_TFS = ("3m", "15m", "1h", "4h", "D")  # pine:418-427 + WRONG_SIDE_WT mirror


def _fail_open(fn):
    """Fail-open: any unexpected error -> None (parent skips the leg)."""

    @functools.wraps(fn)
    def _w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None

    return _w


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
        return int(float(getattr(cfg, name, default)))
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


def _opt_f(v: Any) -> float | None:
    """Optional scalar float; None stays None (live `is None` checks matter)."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _opt_i(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# === 1. PER_SYM_GATE_FLAT_OPEN_ENFORCE (tradier:27250) — flat-open BLOCK mask ===
# True = gate FIRES = block this exposure-open. None when off (live default
# False), when flat_amt state is absent, or on any error. Stocks venue only.
@_fail_open
def per_sym_flat_open_block_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    flat_amt_arr: Any = None,
    gain_pct: Any = None,
    bh_pct: Any = None,
    gain_vs_bh: Any = None,
    trades: Any = None,
    wsharpe: Any = None,
    tag: Any = "",
    sample_tag: Any = "",
) -> np.ndarray | None:
    if not _b(cfg, "PER_SYM_GATE_FLAT_OPEN_ENFORCE", False):
        return None
    if flat_amt_arr is None:
        return None
    amt = _as_float_arr(flat_amt_arr, n)
    if amt is None:
        return None
    flat = np.isfinite(amt) & (np.abs(amt) < 0.0001)  # live :27051 exact epsilon
    g = _opt_f(gain_pct)
    bh = _opt_f(bh_pct)
    gv = _opt_f(gain_vs_bh)
    tr = _opt_i(trades)
    w = _opt_f(wsharpe)
    # Live block conditions, tradier_manage.py:27321-27332, mirrored exactly.
    if (
        "DISABLED" in str(tag)
        or str(sample_tag) == "NO_TRADES"
        or (tr == 0 and (w is None or w == 0))
    ):
        blocked = True
    elif g is not None and g <= 0:
        blocked = True
    elif gv is not None and gv <= 0:
        blocked = True
    elif g is not None and bh is not None and g <= bh:
        blocked = True
    else:
        blocked = False
    if not blocked:
        return np.zeros(n, dtype=bool)
    return flat


# === 2. BTC_TECH_EXIT_WT_MIN_TFS (btc_loop:560-561; pine:415-427) — WT-against leg ===
# Gate-free pure mask (BTC_DEDICATED master is parent-owned). Mirrors
# wrong_side_wt_mask (lane_vec_entryq2.py:419-438).
@_fail_open
def btc_tech_exit_wt_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    req = _i(cfg, "BTC_TECH_EXIT_WT_MIN_TFS", 3)
    against = np.zeros(n, dtype=int)
    pairs = 0
    for _tf in _BTC_TFS:
        w1 = _col(npz, f"wt1_{_tf}", n)
        w2 = _col(npz, f"wt2_{_tf}", n)
        if w1 is None or w2 is None:
            continue
        pairs += 1
        ok = np.isfinite(w1) & np.isfinite(w2) & ~((w1 == 0) & (w2 == 0))
        if is_long:
            against += (ok & (w1 < w2)).astype(int)
        else:
            against += (ok & (w1 > w2)).astype(int)
    if pairs == 0:
        return None
    return against >= req


def _gr_wt_pair(npz: Mapping[str, Any], n: int) -> tuple | None:
    """WT cross-state pair for the GR trigger. Backtest prefers 5m (:7079-7080). USER 2026-10-11: 15m fallback — NPZs never carry 3m/5m WT by policy (speed), and 15m cross-state is just as valid with less noise. Backtest-only twin (no live callers); live wiring follows only via proven-profit promotion."""
    for tf in ("5m", "3m", "15m"):
        w1 = _col(npz, f"wt1_{tf}", n)
        w2 = _col(npz, f"wt2_{tf}", n)
        if w1 is not None and w2 is not None:
            return w1, w2
    return None


def _gr_price(npz: Mapping[str, Any], n: int) -> np.ndarray | None:
    """GR price probe order mirrors backtest :7075 (close_5m/close/current_price)."""
    for key in ("close_5m", "close", "current_price"):
        px = _col(npz, key, n)
        if px is not None:
            return px
    return None


def _gr_break_leg(
    npz: Mapping[str, Any],
    n: int,
    px: np.ndarray,
    key: str,
    enabled: bool,
    is_long: bool,
    is_high: bool,
) -> np.ndarray:
    """One DC/BB break leg. Absent key -> leg off (backtest-exact 0-default)."""
    if not enabled:
        return np.zeros(n, dtype=bool)
    lv = _col(npz, key, n)
    if lv is None:
        return np.zeros(n, dtype=bool)
    pos = np.isfinite(px) & (px > 0) & np.isfinite(lv) & (lv > 0)
    if is_long:
        return pos & (px > lv) if is_high else pos & (px < lv)
    return pos & (px < lv) if not is_high else pos & (px > lv)


# === 3. GOLDEN_RULE_BASE_USD (backtest:7059) — GR sizing scale ===
# (base/5.0)*cascade_mult where the GR trigger fires, 1.0 elsewhere.
@_fail_open
def golden_rule_base_scale(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    mult_15m: float = 1.0,
    mult_1h: float = 1.5,
    mult_4h: float = 2.0,
    mult_d: float = 3.0,
    dc_en: bool = True,
    bb_en: bool = True,
) -> np.ndarray | None:
    base = _f(cfg, "GOLDEN_RULE_BASE_USD", _GR_BASE_DEFAULT)
    if not math.isfinite(base) or base <= 0:
        return None
    pair = _gr_wt_pair(npz, n)
    px = _gr_price(npz, n)
    if pair is None or px is None:
        return None
    w1, w2 = pair
    wf = np.isfinite(w1) & np.isfinite(w2)
    if is_long:
        wtdir = wf & (w1 > w2)  # backtest :7100
        dc15 = _gr_break_leg(npz, n, px, "dc_high_15m", bool(dc_en), True, True)
        bb15 = _gr_break_leg(npz, n, px, "bb_upper_15m", bool(bb_en), True, True)
    else:
        wtdir = wf & (w1 < w2)  # backtest :7105
        dc15 = _gr_break_leg(npz, n, px, "dc_low_15m", bool(dc_en), False, False)
        bb15 = _gr_break_leg(npz, n, px, "bb_lower_15m", bool(bb_en), False, False)
    trigger = wtdir & (dc15 | bb15)  # backtest :7131 (vote path default-off)
    casc = np.full(n, float(mult_15m))  # backtest :7143
    if is_long:
        up1h = _gr_break_leg(
            npz, n, px, "dc_high_1h", bool(dc_en), True, True
        ) | _gr_break_leg(npz, n, px, "bb_upper_1h", bool(bb_en), True, True)
        up4h = _gr_break_leg(
            npz, n, px, "dc_high_4h", bool(dc_en), True, True
        ) | _gr_break_leg(npz, n, px, "bb_upper_4h", bool(bb_en), True, True)
        upD = _gr_break_leg(
            npz, n, px, "dc_high_D", bool(dc_en), True, True
        ) | _gr_break_leg(npz, n, px, "bb_upper_D", bool(bb_en), True, True)
    else:
        up1h = _gr_break_leg(
            npz, n, px, "dc_low_1h", bool(dc_en), False, False
        ) | _gr_break_leg(npz, n, px, "bb_lower_1h", bool(bb_en), False, False)
        up4h = _gr_break_leg(
            npz, n, px, "dc_low_4h", bool(dc_en), False, False
        ) | _gr_break_leg(npz, n, px, "bb_lower_4h", bool(bb_en), False, False)
        upD = _gr_break_leg(
            npz, n, px, "dc_low_D", bool(dc_en), False, False
        ) | _gr_break_leg(npz, n, px, "bb_lower_D", bool(bb_en), False, False)
    casc = np.where(up1h, float(mult_1h), casc)  # backtest :7145/:7152
    casc = np.where(up4h, float(mult_4h), casc)  # backtest :7147/:7154
    casc = np.where(upD, float(mult_d), casc)  # backtest :7149/:7156
    return np.where(trigger, (base / _GR_BASE_DEFAULT) * casc, 1.0)


# === 4. MOVER_DETECTION_ENABLED (ez_positions_quick:4933) — scan master arm ===
@_fail_open
def mover_detection_armed(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    if not _b(cfg, "MOVER_DETECTION_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === 5. VOL_SPIKE_ENABLED (ez_positions_quick:11816) — detector master arm ===
@_fail_open
def vol_spike_armed(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    if not _b(cfg, "VOL_SPIKE_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(
    switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any
) -> np.ndarray | None:
    if switch == "PER_SYM_GATE_FLAT_OPEN_ENFORCE":
        return per_sym_flat_open_block_mask(
            npz,
            n,
            is_long,
            cfg,
            flat_amt_arr=kw.get("flat_amt_arr"),
            gain_pct=kw.get("gain_pct"),
            bh_pct=kw.get("bh_pct"),
            gain_vs_bh=kw.get("gain_vs_bh"),
            trades=kw.get("trades"),
            wsharpe=kw.get("wsharpe"),
            tag=kw.get("tag", ""),
            sample_tag=kw.get("sample_tag", ""),
        )
    if switch == "BTC_TECH_EXIT_WT_MIN_TFS":
        return btc_tech_exit_wt_mask(npz, n, is_long, cfg)
    if switch == "GOLDEN_RULE_BASE_USD":
        return golden_rule_base_scale(
            npz,
            n,
            is_long,
            cfg,
            mult_15m=kw.get("mult_15m", 1.0),
            mult_1h=kw.get("mult_1h", 1.5),
            mult_4h=kw.get("mult_4h", 2.0),
            mult_d=kw.get("mult_d", 3.0),
            dc_en=kw.get("dc_en", True),
            bb_en=kw.get("bb_en", True),
        )
    if switch == "MOVER_DETECTION_ENABLED":
        return mover_detection_armed(npz, n, is_long, cfg)
    if switch == "VOL_SPIKE_ENABLED":
        return vol_spike_armed(npz, n, is_long, cfg)
    raise KeyError(f"unknown wave5 switch: {switch!r}")
