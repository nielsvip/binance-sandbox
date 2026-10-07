"""
vec_paths/peak_giveback_be_erosion.py — PEAK_GIVEBACK + BE_EROSION exit gates.

Sources:
  - PEAK_GIVEBACK_PROTECTION: tradier_manage.py:5028-5068
    (also referenced from ez_manage.py comment at 13526; not wired in crypto live loop
     except via OPUS_VOMIT.py X1/X2 scaffold. We implement both crypto + tradier variants.)
  - BE_EROSION / BREAKEVEN_EROSION: referenced in OPUS_VOMIT X2, config.py "PEAK_GIVEBACK"
    section (PEAK_GIVEBACK_HARD_ZERO_ENABLED — the breakeven branch of PEAK_GIVEBACK).

Public API:
    check_peak_giveback_exit(store, bar_idx, pos_state, mode, cfg) -> Optional[str]
    check_be_erosion_exit(store, bar_idx, pos_state, mode, cfg) -> Optional[str]

Each returns None (no action) or a reason string (CLOSE signal).

PEAK_GIVEBACK logic (from tradier_manage.py:5035-5066):
  Conditions for full close:
    1. PEAK_GIVEBACK_PROTECTION_ENABLED = True
    2. hold_time >= TRADIER_MIN_HOLD_MINUTES (default 240 min = 4h; crypto uses MIN_HOLD_MINUTES_CRYPTO)
    3. cycle_peak_gain >= PEAK_GIVEBACK_MIN_PEAK_PCT (default 0.3%)
    4. hold_time >= BREAKEVEN_GRACE_MINUTES (default 15 min)
    5. If PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN=True: gain <= PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT (default -0.5%)
    6a. Hard-zero branch (PEAK_GIVEBACK_HARD_ZERO_ENABLED=False in live 2026-04-27):
        gain < 0.08% (near zero) → PEAK_GIVEBACK_GAIN_EROSION_STOP
    6b. Drop branch (PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=False per USER 2026-05-11 after
        "was closing breakouts on first retest"):
        gain < cycle_peak_gain - PEAK_GIVEBACK_DROP_PCT → PEAK_GIVEBACK_GAIN_EROSION_STOP

  Default config.py (as of 2026-05-11):
    PEAK_GIVEBACK_PROTECTION_ENABLED = True
    PEAK_GIVEBACK_MIN_PEAK_PCT = 0.5
    PEAK_GIVEBACK_DROP_PCT = 0.5
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED = False   ← USER disabled 2026-05-11
    PEAK_GIVEBACK_HARD_ZERO_ENABLED = False       ← OFF since 2026-04-27
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN = True
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT = -0.5
    BREAKEVEN_GRACE_MINUTES = 15.0

BE_EROSION (breakeven erosion, OPUS_VOMIT X2):
  Simplified version: if position was profitable (max_gain_pct >= thr) and current gain
  has dropped below -BE_EROSION_FLOOR_PCT (a configurable near-zero floor), close.
  This is the "hard zero" variant. Default OFF.
"""
from __future__ import annotations

from typing import Any, Optional


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def check_peak_giveback_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[str]:
    """PEAK_GIVEBACK_PROTECTION exit check (full close).

    Mirrors tradier_manage.py:5028-5066 + config.py:1366-1374.

    Args:
        store:      NPZStore (bar indicators).
        bar_idx:    Current bar index.
        pos_state:  _PositionState — needs .gain_pct, .max_gain_pct, .entry_ts.
        mode:       'crypto' | 'tradier'.
        cfg:        Config object with PEAK_GIVEBACK_* fields.

    Returns:
        None           — no action.
        reason string  — full-close signal (PEAK_GIVEBACK_GAIN_EROSION_STOP_...).
    """
    if not bool(getattr(cfg, 'PEAK_GIVEBACK_PROTECTION_ENABLED', True)):
        return None

    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    # cycle_peak_gain: resets on open, tracks intra-position peak.
    # In vec_engine _PositionState.max_gain_pct serves this role.
    max_g = float(getattr(pos_state, 'max_gain_pct', 0.0))

    # hold time from entry_ts — current bar timestamp
    entry_ts = float(getattr(pos_state, 'entry_ts', 0.0))
    bar_ts = float(store.f('timestamps', bar_idx, 0.0) if hasattr(store, 'f') else 0.0)
    # Fallback: read from store.timestamps
    if bar_ts == 0.0:
        try:
            bar_ts = float(store.timestamps[bar_idx])
        except Exception:
            pass
    hold_min = (bar_ts - entry_ts) / 60.0 if entry_ts > 0 and bar_ts > 0 else 0.0

    # MIN_HOLD gate
    if mode == 'tradier':
        peak_min_hold = float(getattr(cfg, 'TRADIER_MIN_HOLD_MINUTES',
                                      getattr(cfg, 'MIN_HOLD_MINUTES_TRADIER', 240.0)))
    else:
        peak_min_hold = float(getattr(cfg, 'MIN_HOLD_MINUTES_CRYPTO', 30.0))

    if hold_min < peak_min_hold:
        # Below min-hold: block peak_giveback (per user rule 2026-04-27)
        # NOTE: DC_LOW4 structural stop inside this branch is complex; skip in vec
        return None

    # Peak gate
    pgp_min_peak = float(getattr(cfg, 'PEAK_GIVEBACK_MIN_PEAK_PCT', 0.3))
    be_grace = float(getattr(cfg, 'BREAKEVEN_GRACE_MINUTES', 15.0))

    if max_g < pgp_min_peak:
        return None
    if hold_min < be_grace:
        return None

    # REQUIRE_NEGATIVE_GAIN gate (protect manually-bought positions that pulled back to flat)
    pgp_require_neg = bool(getattr(cfg, 'PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN', True))
    pgp_neg_floor = float(getattr(cfg, 'PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT', -0.5))
    if pgp_require_neg and gain > pgp_neg_floor:
        return None

    # Hard-zero branch (PEAK_GIVEBACK_HARD_ZERO_ENABLED)
    pgp_hard_zero = bool(getattr(cfg, 'PEAK_GIVEBACK_HARD_ZERO_ENABLED', False))
    if pgp_hard_zero and gain < 0.08:
        return f"PEAK_GIVEBACK_GAIN_EROSION_STOP_peak{max_g:.2f}%_cur{gain:.2f}%"

    # Drop branch (PEAK_GIVEBACK_DROP_TRIGGER_ENABLED — OFF per USER 2026-05-11)
    pgp_drop_trigger = bool(getattr(cfg, 'PEAK_GIVEBACK_DROP_TRIGGER_ENABLED', False))
    if pgp_drop_trigger:
        pgp_drop = float(getattr(cfg, 'PEAK_GIVEBACK_DROP_PCT', 0.5))
        if gain < max_g - pgp_drop:
            return f"PEAK_GIVEBACK_GAIN_EROSION_STOP_peak{max_g:.2f}%_drop{pgp_drop:.1f}%_cur{gain:.2f}%"

    return None


def check_be_erosion_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[str]:
    """BE_EROSION (breakeven erosion) exit check.

    Source: OPUS_VOMIT X2 / position_evaluator scaffold.
    Fires when: position was profitable (max_gain >= BE_EROSION_MIN_PEAK_PCT)
    AND current gain fell below -BE_EROSION_FLOOR_PCT (i.e. actually at a loss
    after fees, not just near zero).

    Default OFF (BE_EROSION_ENABLED=False). When ON, bypasses NOLOSS gate
    (same intent as PEAK_GIVEBACK hard-zero branch).

    Config:
      BE_EROSION_ENABLED       = False
      BE_EROSION_MIN_PEAK_PCT  = 0.5   # must have been ≥ 0.5% profitable
      BE_EROSION_FLOOR_PCT     = 0.0   # fire when gain < -floor (0 = any loss)
      BE_EROSION_HOLD_MIN_MIN  = 15.0  # min hold in minutes before gate activates
    """
    if not bool(getattr(cfg, 'BE_EROSION_ENABLED', False)):
        return None

    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    max_g = float(getattr(pos_state, 'max_gain_pct', 0.0))

    min_peak = float(getattr(cfg, 'BE_EROSION_MIN_PEAK_PCT', 0.5))
    floor = float(getattr(cfg, 'BE_EROSION_FLOOR_PCT', 0.0))
    hold_min_req = float(getattr(cfg, 'BE_EROSION_HOLD_MIN_MIN', 15.0))

    if max_g < min_peak:
        return None

    # hold time gate
    entry_ts = float(getattr(pos_state, 'entry_ts', 0.0))
    bar_ts = 0.0
    try:
        bar_ts = float(store.timestamps[bar_idx])
    except Exception:
        pass
    hold_min = (bar_ts - entry_ts) / 60.0 if entry_ts > 0 and bar_ts > 0 else 0.0
    if hold_min < hold_min_req:
        return None

    if gain < -floor:
        return f"BE_EROSION_STOP_peak{max_g:.2f}%_cur{gain:.2f}%_floor{floor:.2f}%"

    return None
