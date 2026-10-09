"""ENTRY_LH_TRIGGER (crypto+stocks entry) — fire SHORT on lower-high prints far below the 1h top
(mirror: LONG on higher-low prints far above the 1h bottom). Pure scalars, zero state, zero series.

USER 2026-10-09 INV-0001 (AGLDUSDT_SHORT autopsy: -15.97% slide from the 0.2292 top, first entry
201 bars late, 6.5% covered). v1 used forward-filled HTF high<high_prev prints — rejected (fired
71%: an incomplete 1h bar is trivially below the prior completed one). v2 used base-TF rolling
windows — rejected (live entry workers carry SCALARS only, no trailing klines series at the call
site, so no identical live replica exists). v3 used the daily DC top — rejected (price sits below
the daily top 95% of the time: regress gate useless). v4 uses the 1h DC band as the top reference:
1h top hugs price, so REGRESS_PCT is a real rarity knob (AGLDUSDT 39k bars: 2% -> 34.8%, 5% ->
14.4%, 8% -> 5.4%, 10% -> 3.3%), and the 1h lower-print check is self-consistent (no base/HTF
pending-bar mismatch by construction — the print IS the 1h bar).

SHORT (all strict, all >0-guarded):
  FIRE = (dc_high_1h > 0) & (high_15m < dc_high_1h*(1-REGRESS_PCT/100)) & (high_1h < high_1h_prev)
LONG mirror on lows (dc_low_1h, low_15m, low_1h > low_1h_prev).
Missing/zero input -> NO fire (NEUTRAL-on-missing, mirrors lh_hl_filter). Default OFF = inert.

LIVE SITES (replica spec, NOT YET APPLIED — needs unlock; all four fields already ride the live
indicators dict in both venues):
  crypto: ez_positions_quick.py entry worker between the DC gate (~15943) and the VOL worker
          (~16343) — LAST-writer-wins, so vec FIRST-wins places this block after VOL_SPIKE_REVERSAL.
  stocks: tradier_manage.py should_enter_short (~30241) / should_enter_long (~29849), beside the
          WT_15M_BOUNCE HL/HH filter (~1340, precedent for scalar _prev LH comparisons).
  backtest_v12_engine needs NO separate code: it calls the live entry functions on frozen NPZ
  (guarded by _assert_live_path), so it inherits the replica verbatim.
This module is the SHARED predicate (scalar + vec twins, same comparisons); live imports it once
unlocked — parity by construction, never by parallel reimplementation.
"""

import numpy as np


def _lh_trigger_params(config):
    return float(getattr(config, "ENTRY_LH_TRIGGER_REGRESS_PCT", 5.0)) / 100.0


def _lh_trigger_fire(h15, l15, dch, dcl, h1, h1p, l1, l1p, is_long, regress):
    """PURE per-bar trigger (floats). Returns bool. Shared by scalar+vec."""
    if is_long:
        return bool(
            dcl > 0
            and l1 > 0
            and l1p > 0
            and l15 > 0
            and l15 > dcl * (1.0 + regress)
            and l1 > l1p
        )
    return bool(
        dch > 0
        and h1 > 0
        and h1p > 0
        and h15 > 0
        and h15 < dch * (1.0 - regress)
        and h1 < h1p
    )


def check_lh_trigger_entry(config, indicators: dict, is_long: bool):
    """LIVE/scalar path. indicators carries high_15m/low_15m/high_1h/high_1h_prev/low_1h/
    low_1h_prev/dc_high_1h/dc_low_1h in BOTH venues. Returns (fire, reason). Static reason.
    """
    if not bool(getattr(config, "ENTRY_LH_TRIGGER_ENABLED", False)):
        return False, ""
    regress = _lh_trigger_params(config)
    ind = indicators or {}
    h15 = float(ind.get("high_15m", 0) or 0)
    l15 = float(ind.get("low_15m", 0) or 0)
    dch = float(ind.get("dc_high_1h", 0) or 0)
    dcl = float(ind.get("dc_low_1h", 0) or 0)
    h1 = float(ind.get("high_1h", 0) or 0)
    h1p = float(ind.get("high_1h_prev", 0) or 0)
    l1 = float(ind.get("low_1h", 0) or 0)
    l1p = float(ind.get("low_1h_prev", 0) or 0)
    if _lh_trigger_fire(h15, l15, dch, dcl, h1, h1p, l1, l1p, is_long, regress):
        return True, f"LH_TRIGGER_ENTRY_{'LONG' if is_long else 'SHORT'}"
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
    is_long,
):
    """VECTORIZED fire mask. SAME comparisons as _lh_trigger_fire (strict, >0-guarded).
    Returns bool ndarray."""
    h15 = np.asarray(high_15m, dtype=float)
    l15 = np.asarray(low_15m, dtype=float)
    dch = np.asarray(dc_high_1h, dtype=float)
    dcl = np.asarray(dc_low_1h, dtype=float)
    h1 = np.asarray(high_1h, dtype=float)
    h1p = np.asarray(high_1h_prev, dtype=float)
    l1 = np.asarray(low_1h, dtype=float)
    l1p = np.asarray(low_1h_prev, dtype=float)
    regress = _lh_trigger_params(config)
    if is_long:
        return (
            (dcl > 0)
            & (l1 > 0)
            & (l1p > 0)
            & (l15 > 0)
            & (l15 > dcl * (1.0 + regress))
            & (l1 > l1p)
        )
    return (
        (dch > 0)
        & (h1 > 0)
        & (h1p > 0)
        & (h15 > 0)
        & (h15 < dch * (1.0 - regress))
        & (h1 < h1p)
    )
