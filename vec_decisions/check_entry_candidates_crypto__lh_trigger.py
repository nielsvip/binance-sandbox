"""ENTRY_LH_TRIGGER (crypto+stocks entry) — STRUCT: SHORT on lower-high prints far below the 1h top
(LONG mirror); DIVERG: SHORT on stoch rollover from overbought far below the 1h top (LONG mirror).
Pure scalars, zero state, zero series.

USER 2026-10-09 INV-0001 (AGLDUSDT_SHORT autopsy: -15.97% slide from the 0.2292 top, first entry
201 bars late, 6.5% covered). v1-v3 rejected (71% overfire / unreplicable series / useless gate).
v4 STRUCT: 1h DC band as top reference (AGLDUSDT 39k bars: regress 2% -> 34.8%, 5% -> 14.4%).
v5 DIVERG (USER "not just LH but divergence"): regress-gated stoch rollover — was overbought last
bar, bearish-aligned now — catches tops where momentum fades before price prints lower (AGLD: 168
diverg bars, +40 net over STRUCT at regress 5%). 80/20 are FIXED textbook constants, deliberately
not knobs (no over-optimization surface).

SHORT STRUCT: (dc_high_1h > 0) & (high_15m < dc_high_1h*(1-REGRESS_PCT/100)) & (high_1h < high_1h_prev)
SHORT DIVERG: same regress gate & (k_1h < d_1h) & (k_1h_prev >= 80)
LONG mirrors on lows (dc_low_1h, low_15m, low_1h > low_1h_prev; k_1h > d_1h; k_1h_prev <= 20).
FIRE = ENABLED & (STRUCT | (DIVERG_ENABLED & DIVERG)). Missing/zero input -> NO fire
(NEUTRAL-on-missing, mirrors lh_hl_filter). Default OFF = inert.

LIVE SITES (applied 2026-10-09 after unlock; all keys ride the live indicators dict in both venues):
  crypto: ez_positions_quick.py::check_entry_candidates_for_account worker immediately before VOL
          (both not-should_trade gated = FIRST-writer-wins; trade parity via vec OR-mode).
  stocks: tradier_manage.py should_enter_short / should_enter_long, beside the WT_15M_BOUNCE
          HL/HH scalar-_prev pattern.
  backtest_v12_engine needs NO separate code: it calls the live entry functions on frozen NPZ
  (guarded by _assert_live_path), so it inherits the replica verbatim.
This module is the SHARED predicate (scalar + vec twins, same comparisons); both live venues
import it — parity by construction, never by parallel reimplementation.
"""

import numpy as np

DIVERG_OB = 80.0
DIVERG_OS = 20.0


def _lh_trigger_params(config):
    return (
        float(getattr(config, "ENTRY_LH_TRIGGER_REGRESS_PCT", 5.0)) / 100.0,
        bool(getattr(config, "ENTRY_LH_TRIGGER_DIVERG_ENABLED", False)),
    )


def _lh_trigger_fire(
    h15, l15, dch, dcl, h1, h1p, l1, l1p, k1, d1, k1p, is_long, regress, diverg_on
):
    """PURE per-bar trigger (floats). Returns (struct_fire, diverg_fire). Shared by scalar+vec."""
    if is_long:
        gate = dcl > 0 and l15 > 0 and l15 > dcl * (1.0 + regress)
        struct = gate and l1 > 0 and l1p > 0 and l1 > l1p
        diverg = bool(diverg_on and gate and k1 > d1 and k1p <= DIVERG_OS)
        return bool(struct), diverg
    gate = dch > 0 and h15 > 0 and h15 < dch * (1.0 - regress)
    struct = gate and h1 > 0 and h1p > 0 and h1 < h1p
    diverg = bool(diverg_on and gate and k1 < d1 and k1p >= DIVERG_OB)
    return bool(struct), diverg


def check_lh_trigger_entry(config, indicators: dict, is_long: bool):
    """LIVE/scalar path. Returns (fire, reason). Static reasons (STRUCT vs DIVERG attributed)."""
    if not bool(getattr(config, "ENTRY_LH_TRIGGER_ENABLED", False)):
        return False, ""
    regress, diverg_on = _lh_trigger_params(config)
    ind = indicators or {}
    h15 = float(ind.get("high_15m", 0) or 0)
    l15 = float(ind.get("low_15m", 0) or 0)
    dch = float(ind.get("dc_high_1h", 0) or 0)
    dcl = float(ind.get("dc_low_1h", 0) or 0)
    h1 = float(ind.get("high_1h", 0) or 0)
    h1p = float(ind.get("high_1h_prev", 0) or 0)
    l1 = float(ind.get("low_1h", 0) or 0)
    l1p = float(ind.get("low_1h_prev", 0) or 0)
    k1 = float(ind.get("k_1h", 50) or 50)
    d1 = float(ind.get("d_1h", 50) or 50)
    k1p = float(ind.get("k_1h_prev", 50) or 50)
    struct, diverg = _lh_trigger_fire(
        h15, l15, dch, dcl, h1, h1p, l1, l1p, k1, d1, k1p, is_long, regress, diverg_on
    )
    side = "LONG" if is_long else "SHORT"
    if struct:
        return True, f"LH_TRIGGER_ENTRY_{side}"
    if diverg:
        return True, f"LH_TRIGGER_DIVERG_{side}"
    return False, ""


def check_lh_trigger_entry_vec(
    config,
    high_15m,
    low_15m,
    dc_high_1h,
    dc_low_1h,
    high_1h,
    high_1h_prev,
    low_1h,
    low_1h_prev,
    k_1h,
    d_1h,
    k_1h_prev,
    is_long,
):
    """VECTORIZED fire mask. SAME comparisons as _lh_trigger_fire. Returns bool ndarray."""
    h15 = np.asarray(high_15m, dtype=float)
    l15 = np.asarray(low_15m, dtype=float)
    dch = np.asarray(dc_high_1h, dtype=float)
    dcl = np.asarray(dc_low_1h, dtype=float)
    h1 = np.asarray(high_1h, dtype=float)
    h1p = np.asarray(high_1h_prev, dtype=float)
    l1 = np.asarray(low_1h, dtype=float)
    l1p = np.asarray(low_1h_prev, dtype=float)
    k1 = np.asarray(k_1h, dtype=float)
    d1 = np.asarray(d_1h, dtype=float)
    k1p = np.asarray(k_1h_prev, dtype=float)
    regress, diverg_on = _lh_trigger_params(config)
    if is_long:
        gate = (dcl > 0) & (l15 > 0) & (l15 > dcl * (1.0 + regress))
        struct = gate & (l1 > 0) & (l1p > 0) & (l1 > l1p)
        if not diverg_on:
            return struct
        return struct | (gate & (k1 > d1) & (k1p <= DIVERG_OS))
    gate = (dch > 0) & (h15 > 0) & (h15 < dch * (1.0 - regress))
    struct = gate & (h1 > 0) & (h1p > 0) & (h1 < h1p)
    if not diverg_on:
        return struct
    return struct | (gate & (k1 < d1) & (k1p >= DIVERG_OB))
