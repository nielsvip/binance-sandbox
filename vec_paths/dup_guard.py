"""
vec_paths/dup_guard.py — AUGMENT_LOCK + DUPLICATE_OPEN_GUARD exact semantics.

Sources:
  - ez_manage.py:11014-11038 (HARD DUPLICATE OPEN GUARD — gain-based)
  - ez_manage.py:14062-14113 (HARD AUGMENT LOCK — 900s cooldown)
  - CLAUDE.md feedback_wt15m_loss_bypass_augment_lock_20260509:
      "AUGMENT_LOCK 900s now blocks OPEN/AUGMENT/REENTRY equally — no REENTRY bypass.
       Also fires on TRUE OPEN on empty positions."
  - config.py: DUP_GUARD_USE_GAIN_GATE=True, DUP_GUARD_GAIN_MULTIPLIER=0.5, MIN_GAIN=3.0
    → threshold = 3.0 * 0.5 = 1.5%

Public API:
    check_dup_guard_block(pos_state, ts_i, current_gain_pct, cfg) -> Optional[str]

Returns a block-reason string (which equals the return value of execute_now when blocked)
or None when the action is allowed.

Two independent guards:
  1. GAIN_GATE (DUP_GUARD_USE_GAIN_GATE=True, default):
     Block when pos_size > MIN_POSITION_SIZE AND gain <= MIN_GAIN * DUP_GUARD_GAIN_MULTIPLIER.
     Reason: BLOCKED_DUP_GUARD_GAIN_<x>pct_lt_<thr>pct
  2. TIME_COOLDOWN (DUP_GUARD_USE_GAIN_GATE=False fallback):
     Block when last_augment_ts within AUGMENT_LOCK_MIN_SECONDS (900s).
     Reason: BLOCKED_DUPLICATE_OPEN_<pk>

HARD_AUGMENT_LOCK (always active, independent of GAIN_GATE):
  Block when time since last augment < AUGMENT_LOCK_MIN_SECONDS AND gain < MIN_GAIN.
  Allows WT_3M_FORCE_OPEN bypass when configured.
  Reason: BLOCKED_HARD_AUGMENT_LOCK_<age>s

In vec_engine_v1 simulation, pos_state.last_augment_ts tracks when the last augment fired.
The engine calls this before every AUGMENT/OPEN/REENTRY candidate.
"""
from __future__ import annotations

from typing import Any, Optional


def check_dup_guard_block(
    pos_state: Any,
    ts_i: float,
    current_gain_pct: float,
    cfg: Any,
    pos_value_usd: float = 0.0,
    action: str = "AUGMENT",
    reason_hint: str = "",
) -> Optional[str]:
    """Check DUPLICATE_OPEN_GUARD + HARD_AUGMENT_LOCK.

    Args:
        pos_state:          _PositionState for the position being augmented/opened.
        ts_i:               Current bar timestamp (unix seconds).
        current_gain_pct:   Current position gain in percent.
        cfg:                VecConfig (or any object with the guard attributes).
        pos_value_usd:      Current position value in USD (pos_amt * price).
                            Used to determine whether position is at foothold size.
        action:             The action being attempted ('AUGMENT', 'OPEN', 'REENTRY').
        reason_hint:        Reason string — if 'WT_3M_FORCE_OPEN' is present and
                            WT_3M_FORCE_OPEN_BYPASS_GATES=True, HARD_AUGMENT_LOCK is skipped.

    Returns:
        None    — action is allowed.
        str     — block reason string (== what execute_now returns when blocked).
    """
    _lock_min = float(getattr(cfg, 'AUGMENT_LOCK_MIN_SECONDS', 900.0))
    _min_gain = float(getattr(cfg, 'MIN_GAIN', 3.0))
    _min_pos = float(getattr(cfg, 'MIN_POSITION_SIZE', 45.0))

    last_aug_ts = float(getattr(pos_state, 'last_augment_ts', 0.0))
    since_aug = ts_i - last_aug_ts

    # ── GAIN_GATE (default) ────────────────────────────────────────────────────
    if bool(getattr(cfg, 'DUP_GUARD_USE_GAIN_GATE', True)):
        _wt3m_bypass = ('WT_3M_FORCE_OPEN' in reason_hint.upper()
                        and bool(getattr(cfg, 'WT_3M_FORCE_OPEN_BYPASS_GATES', True)))
        if not _wt3m_bypass:
            _dg_mult = float(getattr(cfg, 'DUP_GUARD_GAIN_MULTIPLIER', 0.5))
            _dg_thr = _min_gain * _dg_mult
            if pos_value_usd > _min_pos and current_gain_pct <= _dg_thr:
                return f"BLOCKED_DUP_GUARD_GAIN_{current_gain_pct:.2f}pct_lt_{_dg_thr:.2f}pct"
    else:
        # TIME_COOLDOWN fallback
        _dup_cooldown = float(getattr(cfg, 'DUPLICATE_OPEN_COOLDOWN', 900.0))
        if since_aug < _dup_cooldown:
            return f"BLOCKED_DUPLICATE_OPEN_{since_aug:.0f}s"

    # ── HARD_AUGMENT_LOCK (always runs, independent of GAIN_GATE) ─────────────
    # Per CLAUDE.md 2026-05-09: applies to OPEN/AUGMENT/REENTRY equally.
    _gain_ok = (current_gain_pct >= _min_gain) if (pos_value_usd > _min_pos) else False
    _wt3m_bypass_lock = ('WT_3M_FORCE_OPEN' in reason_hint.upper()
                         and bool(getattr(cfg, 'WT_3M_FORCE_OPEN_BYPASS_GATES', True)))
    if since_aug < _lock_min and not _gain_ok and not _wt3m_bypass_lock:
        return f"BLOCKED_HARD_AUGMENT_LOCK_{since_aug:.0f}s"

    return None
