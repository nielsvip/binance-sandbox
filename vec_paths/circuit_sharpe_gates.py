"""CIRCUIT_BREAKER + SHARPE_TRIPLE_GATES — entry-side gate, scalar + vectorized.

LIVE LOGIC MIRRORED FROM:
  - ez_manage.py:14003-14017  (CIRCUIT_BREAKER — path/reason auto-disabled when
    WT/DC wrong at entry; consecutive-loss halt timer)
  - ez_manage.py:13941-13970  (SHARPE_TRIPLE_GATES — combines hour-of-day Sharpe
    floor, regime score floor, volume score floor; all default OFF)

WHY THIS EXISTS:
  Tier-1 vectorized backtests need to filter prospective entries with the same
  gates live applies. Wiring through real `strategy_enhancements.check_*` would
  pull live time / dict-state into the simulator. Instead, this module accepts
  per-bar precomputed feature arrays (hour_of_day_sharpe, regime_score,
  volume_score, last_circuit_open_ts) and returns the SAME (blocked, reason,
  gate_fired) tuple the live code would have produced.

USER CONTRACT (never-fail mandate):
  - REENTRY (positionAmt == 0) and HEDGE actions BYPASS every gate.
  - All gates default OFF — config knobs must be explicitly enabled.
  - Gate order matches live precedence: CIRCUIT > SHARPE_HOUR > REGIME > VOLUME.

REASON CODES (match the live BLOCKED_* return strings shape):
    "OK"
    "DISABLED"
    "BYPASS_REENTRY"
    "BYPASS_HEDGE"
    "BLOCKED_CIRCUIT_BREAKER"
    "BLOCKED_SHARPE_HOUR_FLOOR"
    "BLOCKED_REGIME_FLOOR"
    "BLOCKED_VOLUME_FLOOR"
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

import numpy as np


# ─── Reason codes ───────────────────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DISABLED = "DISABLED"
REASON_BYPASS_REENTRY = "BYPASS_REENTRY"
REASON_BYPASS_HEDGE = "BYPASS_HEDGE"
REASON_CIRCUIT = "BLOCKED_CIRCUIT_BREAKER"
REASON_SHARPE_HOUR = "BLOCKED_SHARPE_HOUR_FLOOR"
REASON_REGIME = "BLOCKED_REGIME_FLOOR"
REASON_VOLUME = "BLOCKED_VOLUME_FLOOR"

GATE_NONE = ""
GATE_CIRCUIT = "CIRCUIT_BREAKER"
GATE_SHARPE_HOUR = "SHARPE_HOUR"
GATE_REGIME = "REGIME"
GATE_VOLUME = "VOLUME"

# Numeric codes for the _vec output (int8)
CODE_OK = 0
CODE_DISABLED = 1
CODE_BYPASS_REENTRY = 2
CODE_BYPASS_HEDGE = 3
CODE_CIRCUIT = 4
CODE_SHARPE_HOUR = 5
CODE_REGIME = 6
CODE_VOLUME = 7

REASON_BY_CODE = {
    CODE_OK: REASON_OK,
    CODE_DISABLED: REASON_DISABLED,
    CODE_BYPASS_REENTRY: REASON_BYPASS_REENTRY,
    CODE_BYPASS_HEDGE: REASON_BYPASS_HEDGE,
    CODE_CIRCUIT: REASON_CIRCUIT,
    CODE_SHARPE_HOUR: REASON_SHARPE_HOUR,
    CODE_REGIME: REASON_REGIME,
    CODE_VOLUME: REASON_VOLUME,
}

# Action sets — bypass per never-fail mandate
REENTRY_ACTIONS = frozenset({"REENTRY", "QUICK_REENTRY"})
HEDGE_ACTIONS = frozenset({"HEDGE", "HEDGE_OPEN", "OBLIGATORY_HEDGE"})

# Default thresholds (all gates OFF by default, in line with live defaults)
DEFAULT_CIRCUIT_ENABLED = False
DEFAULT_SHARPE_HOUR_ENABLED = False
DEFAULT_REGIME_ENABLED = False
DEFAULT_VOLUME_ENABLED = False
DEFAULT_CIRCUIT_COOLDOWN_S = 1800       # 30 min cooldown after a circuit-open
DEFAULT_SHARPE_HOUR_FLOOR = 0.0         # require hour-of-day Sharpe >= 0
DEFAULT_REGIME_FLOOR = 0.0              # require regime score >= 0
DEFAULT_VOLUME_FLOOR = 1.0              # require volume score (e.g. vol/avg) >= 1.0


# ════════════════════════════════════════════════════════════════════════════════
# Config helper
# ════════════════════════════════════════════════════════════════════════════════

def _cfg_get(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _resolve_thresholds(cfg: Any) -> Tuple[bool, bool, bool, bool, float, float, float, float]:
    """Returns (circuit_on, sharpe_on, regime_on, volume_on,
                circuit_cooldown_s, sharpe_floor, regime_floor, volume_floor)."""
    return (
        bool(_cfg_get(cfg, "CIRCUIT_BREAKER_ENABLED", DEFAULT_CIRCUIT_ENABLED)),
        bool(_cfg_get(cfg, "SHARPE_HOUR_GATE_ENABLED", DEFAULT_SHARPE_HOUR_ENABLED)),
        bool(_cfg_get(cfg, "REGIME_GATE_ENABLED", DEFAULT_REGIME_ENABLED)),
        bool(_cfg_get(cfg, "VOLUME_GATE_ENABLED", DEFAULT_VOLUME_ENABLED)),
        float(_cfg_get(cfg, "CIRCUIT_BREAKER_COOLDOWN_S", DEFAULT_CIRCUIT_COOLDOWN_S)),
        float(_cfg_get(cfg, "SHARPE_HOUR_FLOOR", DEFAULT_SHARPE_HOUR_FLOOR)),
        float(_cfg_get(cfg, "REGIME_FLOOR", DEFAULT_REGIME_FLOOR)),
        float(_cfg_get(cfg, "VOLUME_FLOOR", DEFAULT_VOLUME_FLOOR)),
    )


def _bypass_for_action(action: Any, position_amt: float) -> Optional[str]:
    """Return BYPASS code when caller is REENTRY (positionAmt == 0) or HEDGE.
    None otherwise."""
    if action is None:
        return None
    a = str(action).upper()
    if a in HEDGE_ACTIONS or "HEDGE" in a:
        return REASON_BYPASS_HEDGE
    if a in REENTRY_ACTIONS and float(position_amt) == 0.0:
        return REASON_BYPASS_REENTRY
    return None


# ════════════════════════════════════════════════════════════════════════════════
# _core — single-call decision (live or backtest one-shot)
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_circuit_sharpe_gates_core(
    action: Any,
    position_key: str,
    now_ts: float,
    hour_of_day_sharpe: float,
    regime_score: float,
    volume_score: float,
    last_circuit_open_ts: float,
    *,
    position_amt: float = 0.0,
    config: Any = None,
) -> Tuple[bool, str, str]:
    """Decide whether the prospective entry attempt is blocked by the
    CIRCUIT_BREAKER + SHARPE_TRIPLE_GATES stack.

    Args:
        action:               prospective action (OPEN/AUGMENT/REENTRY/HEDGE/...).
        position_key:         e.g. 'BTC_LONG'. Used only for logging continuity.
        now_ts:               epoch seconds of the prospective attempt (UTC).
        hour_of_day_sharpe:   precomputed Sharpe for the current UTC hour.
        regime_score:         precomputed regime score (higher = trendier).
        volume_score:         precomputed volume score (e.g. vol/avg multiplier).
        last_circuit_open_ts: epoch seconds of last circuit-breaker open; 0 if
                              never opened. Cooldown is (now_ts - last) >= S.
        position_amt:         current position quantity (used for REENTRY bypass:
                              REENTRY only bypasses when position_amt == 0).
        config:               config module / dict / None.

    Returns:
        (blocked, reason, gate_fired)
        blocked: True only when a gate fires AND no bypass applies.
        reason:  one of REASON_* (string).
        gate_fired: GATE_* name or empty string when not blocked.

    Gate order (matches live precedence): CIRCUIT > SHARPE_HOUR > REGIME > VOLUME.
    """
    cb_on, sh_on, rg_on, vl_on, cb_cool, sh_floor, rg_floor, vl_floor = _resolve_thresholds(config)

    # Universal disable: all four gates off → DISABLED (fast-path, no logic).
    if not (cb_on or sh_on or rg_on or vl_on):
        return False, REASON_DISABLED, GATE_NONE

    # Bypass mandate: REENTRY (qty==0) and HEDGE never blocked.
    bypass = _bypass_for_action(action, position_amt)
    if bypass is not None:
        return False, bypass, GATE_NONE

    # CIRCUIT_BREAKER — fires if circuit opened within cooldown.
    if cb_on:
        try:
            last = float(last_circuit_open_ts or 0.0)
            now = float(now_ts or 0.0)
        except (TypeError, ValueError):
            last, now = 0.0, 0.0
        if last > 0.0 and (now - last) < cb_cool:
            return True, REASON_CIRCUIT, GATE_CIRCUIT

    # SHARPE_HOUR_FLOOR
    if sh_on:
        try:
            sh = float(hour_of_day_sharpe)
        except (TypeError, ValueError):
            sh = float("nan")
        if not (sh != sh) and sh < sh_floor:  # not NaN AND below floor
            return True, REASON_SHARPE_HOUR, GATE_SHARPE_HOUR

    # REGIME_FLOOR
    if rg_on:
        try:
            rg = float(regime_score)
        except (TypeError, ValueError):
            rg = float("nan")
        if not (rg != rg) and rg < rg_floor:
            return True, REASON_REGIME, GATE_REGIME

    # VOLUME_FLOOR
    if vl_on:
        try:
            vl = float(volume_score)
        except (TypeError, ValueError):
            vl = float("nan")
        if not (vl != vl) and vl < vl_floor:
            return True, REASON_VOLUME, GATE_VOLUME

    return False, REASON_OK, GATE_NONE


# ════════════════════════════════════════════════════════════════════════════════
# _vec — precompute per-bar gate state for whole sim window in O(N)
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_circuit_sharpe_gates_vec(
    actions: np.ndarray,
    now_ts: np.ndarray,
    hour_of_day_sharpe: np.ndarray,
    regime_score: np.ndarray,
    volume_score: np.ndarray,
    last_circuit_open_ts: np.ndarray,
    *,
    position_amt: Optional[np.ndarray] = None,
    config: Any = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized evaluation of the gate stack across N prospective entries.

    Args:
        actions:                 shape (N,) action strings.
        now_ts:                  shape (N,) epoch seconds (int64 or float64).
        hour_of_day_sharpe:      shape (N,) Sharpe values (NaN-tolerant).
        regime_score:            shape (N,) regime score values (NaN-tolerant).
        volume_score:            shape (N,) volume score values (NaN-tolerant).
        last_circuit_open_ts:    shape (N,) epoch sec of last circuit-open per
                                 attempt (0 if never opened).
        position_amt:            shape (N,) position quantity (for REENTRY
                                 bypass). None → treated as 0.0 throughout.
        config:                  config module / dict / None.

    Returns:
        (blocked, reason_code)
        blocked: shape (N,) bool — True when a gate fires AND no bypass applies.
        reason_code: shape (N,) int8 — one of CODE_*.
    """
    cb_on, sh_on, rg_on, vl_on, cb_cool, sh_floor, rg_floor, vl_floor = _resolve_thresholds(config)

    actions_arr = np.asarray(actions, dtype=object)
    n = int(actions_arr.shape[0])
    blocked = np.zeros(n, dtype=bool)
    reason = np.zeros(n, dtype=np.int8)  # CODE_OK = 0
    if n == 0:
        return blocked, reason

    # Universal-disabled fast path.
    if not (cb_on or sh_on or rg_on or vl_on):
        reason[:] = CODE_DISABLED
        return blocked, reason

    now_arr = np.asarray(now_ts, dtype=np.float64)
    sh_arr = np.asarray(hour_of_day_sharpe, dtype=np.float64)
    rg_arr = np.asarray(regime_score, dtype=np.float64)
    vl_arr = np.asarray(volume_score, dtype=np.float64)
    cb_arr = np.asarray(last_circuit_open_ts, dtype=np.float64)
    pa_arr = (np.asarray(position_amt, dtype=np.float64)
              if position_amt is not None else np.zeros(n, dtype=np.float64))

    # Upper-case actions once for membership tests
    action_upper = np.array([str(a).upper() for a in actions_arr], dtype=object)

    # Bypass masks
    is_hedge = np.array(["HEDGE" in a for a in action_upper], dtype=bool)
    is_reentry = np.array([a in REENTRY_ACTIONS for a in action_upper], dtype=bool) & (pa_arr == 0.0)

    # Pending = still need to decide (not yet bypassed)
    pending = ~(is_hedge | is_reentry)

    # Apply bypass codes first (so they appear in `reason`).
    reason[is_hedge] = CODE_BYPASS_HEDGE
    reason[is_reentry & ~is_hedge] = CODE_BYPASS_REENTRY

    # CIRCUIT_BREAKER
    if cb_on:
        cb_fire = pending & (cb_arr > 0.0) & ((now_arr - cb_arr) < cb_cool)
        reason[cb_fire] = CODE_CIRCUIT
        blocked[cb_fire] = True
        pending = pending & ~cb_fire

    # SHARPE_HOUR_FLOOR
    if sh_on:
        sh_valid = ~np.isnan(sh_arr)
        sh_fire = pending & sh_valid & (sh_arr < sh_floor)
        reason[sh_fire] = CODE_SHARPE_HOUR
        blocked[sh_fire] = True
        pending = pending & ~sh_fire

    # REGIME_FLOOR
    if rg_on:
        rg_valid = ~np.isnan(rg_arr)
        rg_fire = pending & rg_valid & (rg_arr < rg_floor)
        reason[rg_fire] = CODE_REGIME
        blocked[rg_fire] = True
        pending = pending & ~rg_fire

    # VOLUME_FLOOR
    if vl_on:
        vl_valid = ~np.isnan(vl_arr)
        vl_fire = pending & vl_valid & (vl_arr < vl_floor)
        reason[vl_fire] = CODE_VOLUME
        blocked[vl_fire] = True

    return blocked, reason
