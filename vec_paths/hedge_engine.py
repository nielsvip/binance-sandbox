"""
vec_paths/hedge_engine.py — Vectorized hedge engine paths for vec_engine_v1.

Models 4 hedge-related paths found in the live trading system:

  Path 1: scan_and_hedge_losers
    Source: ez_positions_quick.py:5002 — opens same-symbol opposite-side hedge
    when position is in loss AND WT against on configured TFs.
    Live reason in /history/: HEDGE_PROTECT_{LONG,SHORT}_LOSS, QUICK_HEDGE_PROTECT_*
    NOTE: The current live system (2026-05-10 strip) uses wt1_3m alone as the trigger.
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H=False (CLAUDE.md 2026-05-10 mandate).

  Path 2: OBLIGATORY_HEDGE
    Source: ez_manage.py:14339 — fires INSIDE the UNIVERSAL_NOLOSS_GATE when a
    loss-exit is attempted. If gain <= OBLIGATORY_HEDGE_MIN_LOSS_PCT AND WT against,
    forces a same-symbol hedge BEFORE allowing the close to proceed. If hedge
    fails, falls through to HEDGE_FAILED close.
    Live reasons: OBLIGATORY_HEDGE, OBLIGATORY_HEDGE_TASK_START/END

  Path 3: HEDGE_FAILED fallback close
    Source: ez_positions_quick.py:5271 + ez_manage.py:14405.
    When the hedge cannot be opened (blacklist, no quote, already hedged), the
    losing position is closed at a loss using a reason containing 'HEDGE_FAILED'.
    This IS one of the 3 sanctioned loss-exit paths (R1/R2/HEDGE_FAILED).
    Live reasons in /history/: HEDGE_FAILED_FALLBACK_CLOSE_g{pct}%_*

  Path 4: compute_hedge_size
    Source: ez_positions_quick.py:5235-5236 — hedge qty = loser_qty * HEDGE_SAME_SYMBOL_PCT.
    Default: 1.0 (100% of losing position). Config.py has HEDGE_SAME_SYMBOL_PCT=1.0.

LIVE EVIDENCE (from /history/ scan 2026-05-12):
  - HEDGE_PROTECT_SHORT_LOSS: 1,301 AUGMENT events in ang/inf/men (primary same-sym hedge open)
  - HEDGE_PROTECT_LONG_LOSS: 1,202 AUGMENT events (primary same-sym hedge on short losers)
  - QUICK_HEDGE_PROTECT_*: 5,322 events (older naming — pre-April 2026)
  - HEDGE_FAILED_FALLBACK_CLOSE: 5 events (recent, post 2026-05-09 mandate)
  - HEDGE_SAME_SYM_LAST_RESORT: ~1,200 AUGMENT events (fallback path)

DESIGN NOTES:
  - Stocks (tradier) have NO auto-same-symbol hedge (CLAUDE.md: "Stocks no auto-hedge").
  - Only crypto accounts inf/men/fin/ang/flz hedge via scan_and_hedge_losers.
  - HEDGE_FAILED close is one of the 3 sanctioned loss-exits; it BYPASSES UNIVERSAL_NOLOSS_GATE
    via the bypass reasons list in config.py UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS.
  - In vec simulation: hedge = open opposite-side _PositionState with hedge_active=True,
    sized at HEDGE_SAME_SYMBOL_PCT of loser qty.
  - Hedge positions are NOT tracked in returns_by_sym separately — they affect the opposing
    side's PnL implicitly (hedge short gains while long loses, vice versa). The
    _PositionState.hedge_active flag prevents double-hedging.

All defaults match 2026-05-10 strip values from CLAUDE.md:
  HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H=False
  HEDGE_DETERIORATING_GAIN_ENABLED=False
  HEDGE_STRICT_WT_ALL_TFS_ENABLED=False
  HEDGE_CLOSE_MODE='wt_3m'
  OBLIGATORY_HEDGE_ENABLED=True (ezm default)
  OBLIGATORY_HEDGE_WT_TFS_REQUIRED=1 (wt_3m alone after strip)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

# Re-export the 7-gate hedge-scan cluster so callers can do
#     from vec_paths.hedge_engine import evaluate_hedge_scan_gates_core
# without learning a second module path. The implementation lives in
# vec_paths/hedge_scan_gates.py and is the authoritative parity surface for
# ez_positions_quick.py:5016-5272 (live scan loop).
from .hedge_scan_gates import (
    evaluate_hedge_scan_gates_core,
    evaluate_hedge_scan_gates_vec,
    GATE_NAMES as HEDGE_SCAN_GATE_NAMES,
)

if TYPE_CHECKING:
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState


# ─────────────────────────────────────────────────────────────────────────────
# Helper: check if WT is against a position on a given TF
# ─────────────────────────────────────────────────────────────────────────────

def _wt_against(store: "_NPZStore", bar_idx: int, side: str, tf: str) -> bool:
    """Return True if wt1_{tf} < wt2_{tf} for LONG (price falling = against LONG).
    For SHORT: wt1 > wt2 means WT is bullish = against SHORT position.
    """
    wt1 = store.f(f"wt1_{tf}", bar_idx, 0.0)
    wt2 = store.f(f"wt2_{tf}", bar_idx, 0.0)
    if side == "LONG":
        return wt1 < wt2
    else:
        return wt1 > wt2


# ─────────────────────────────────────────────────────────────────────────────
# Path 4: compute_hedge_size
# ─────────────────────────────────────────────────────────────────────────────

def compute_hedge_size(pos_state: "_PositionState", cfg: "VecConfig") -> float:
    """Compute hedge position size as fraction of the losing position.

    Source: ez_positions_quick.py:5235-5236
        _hss_pct = config.HEDGE_SAME_SYMBOL_PCT (default 1.0 = 100% of loser qty)
        hedge_qty = qty * _hss_pct

    Returns scalar to multiply against loser's qty. Default 0.5 per VecConfig
    (vec_engine_v1.py line 220: HEDGE_SAME_SYMBOL_PCT: float = 0.5).
    Live config.py has 1.0 — vec default is more conservative for backtest.
    """
    pct = float(getattr(cfg, "HEDGE_SAME_SYMBOL_PCT", 0.5))
    return max(0.0, min(pct, 2.0)) * pos_state.qty


# ─────────────────────────────────────────────────────────────────────────────
# Path 1: scan_and_hedge_losers (WT-based same-symbol hedge open)
# ─────────────────────────────────────────────────────────────────────────────

def check_scan_hedge_losers(
    store: "_NPZStore",
    bar_idx: int,
    pos_state: "_PositionState",
    all_pos_states: Optional[Dict[str, "_PositionState"]] = None,
    mode: str = "crypto",
    cfg: "Optional[VecConfig]" = None,
) -> Optional[Dict]:
    """Evaluate whether a losing position should trigger a same-symbol hedge.

    Source: ez_positions_quick.py:5002 scan_and_hedge_losers().

    Trigger conditions (2026-05-10 strip — HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H=False):
      - HEDGE_MODE enabled (cfg.HEDGE_MODE=True)
      - mode == "crypto" (stocks have NO auto-hedge per CLAUDE.md)
      - Position is open AND in loss (gain < OBLIGATORY_HEDGE_MIN_LOSS_PCT default -0.25%)
      - Opposite-side position NOT already open (no double-hedge)
      - hedge_active flag NOT set (prevents re-hedging same position)
      - WT trigger: wt1_3m against position (post-strip: 3m alone is sufficient)
        - HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H=False → 3m alone triggers
        - HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H can also be consulted but
          the May-10 strip set 3m alone as the live default.

    Args:
        store:          NPZ indicator store for this symbol.
        bar_idx:        Current bar index.
        pos_state:      The LOSING position's _PositionState (must be open, in loss).
        all_pos_states: Dict of {side -> _PositionState} for this symbol. Used to
                        check if opposite side is already open (no double-hedge).
        mode:           "crypto" or "tradier".
        cfg:            VecConfig instance.

    Returns:
        Dict with keys:
          side         - hedge side ('SHORT' if pos is LONG, 'LONG' if pos is SHORT)
          size_qty     - hedge qty (loser.qty * HEDGE_SAME_SYMBOL_PCT)
          reason       - reason string for logging
        or None if conditions not met.
    """
    if cfg is None:
        return None
    if not pos_state.open:
        return None
    if mode != "crypto":
        return None
    if not getattr(cfg, "HEDGE_MODE", True):
        return None
    if getattr(pos_state, "hedge_active", False):
        return None

    gain = pos_state.gain_pct
    min_loss = float(getattr(cfg, "OBLIGATORY_HEDGE_MIN_LOSS_PCT", -0.25))
    if gain > min_loss:
        return None

    side = pos_state.side
    hedge_side = "SHORT" if side == "LONG" else "LONG"

    if all_pos_states is not None:
        opp = all_pos_states.get(hedge_side)
        if opp is not None and opp.open:
            return None

    _3m_against = _wt_against(store, bar_idx, side, "3m")
    require_3m_and_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", False))
    require_3m_and_15m_or_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", False))
    use_3m_alone = bool(getattr(cfg, "HEDGE_TRIGGER_USE_WT_3M_ALONE", True))

    if require_3m_and_15m_or_1h:
        _15m_against = _wt_against(store, bar_idx, side, "15m")
        _1h_against = _wt_against(store, bar_idx, side, "1h")
        wt_trigger = _3m_against and (_15m_against or _1h_against)
        trigger_label = "3m_AND_(15m_OR_1h)"
    elif require_3m_and_1h:
        _1h_against = _wt_against(store, bar_idx, side, "1h")
        wt_trigger = _3m_against and _1h_against
        trigger_label = "3m_AND_1h"
    elif use_3m_alone:
        wt_trigger = _3m_against
        trigger_label = "3m_alone"
    else:
        _15m_against = _wt_against(store, bar_idx, side, "15m")
        _1h_against = _wt_against(store, bar_idx, side, "1h")
        wt_trigger = _15m_against or (_3m_against and _1h_against)
        trigger_label = "15m_OR_3m_AND_1h"

    if not wt_trigger:
        return None

    hedge_qty = compute_hedge_size(pos_state, cfg)
    return {
        "side": hedge_side,
        "size_qty": hedge_qty,
        "reason": f"HEDGE_PROTECT_{side}_LOSS_wt_{trigger_label}_g{gain:.2f}pct",
        "trigger_label": trigger_label,
        "gain": gain,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path 2: OBLIGATORY_HEDGE (fires inside UNIVERSAL_NOLOSS_GATE)
# ─────────────────────────────────────────────────────────────────────────────

def check_obligatory_hedge(
    store: "_NPZStore",
    bar_idx: int,
    pos_state: "_PositionState",
    mode: str = "crypto",
    cfg: "Optional[VecConfig]" = None,
) -> Optional[Dict]:
    """Evaluate OBLIGATORY_HEDGE trigger (inside UNIVERSAL_NOLOSS_GATE).

    Source: ez_manage.py:14339 — fired when a loss-exit attempt is blocked by
    UNIVERSAL_NOLOSS_GATE and the WT signal confirms reversal.

    2026-05-10 strip: _oh_user_trigger = _oh_3m_against alone (wt1_3m independent of 1h).
    Original: required OBLIGATORY_HEDGE_WT_TFS_REQUIRED (default 2) TFs against.
    After strip: 1 TF (3m alone) is sufficient.

    This path fires when:
      - OBLIGATORY_HEDGE_ENABLED=True (default)
      - gain <= OBLIGATORY_HEDGE_MIN_LOSS_PCT (-0.25% default)
      - wt1_3m against position (OR wt_against_count >= OBLIGATORY_HEDGE_WT_TFS_REQUIRED)
      - NOT already hedged (hedge_active=False)

    In vec sim context: this runs AFTER the WT-exit path is blocked by NOLOSS gate,
    providing a recovery hedge before the HEDGE_FAILED fallback close.

    Returns same dict shape as check_scan_hedge_losers, or None.
    """
    if cfg is None:
        return None
    if not pos_state.open:
        return None
    if mode != "crypto":
        return None
    if not bool(getattr(cfg, "OBLIGATORY_HEDGE_ENABLED", True)):
        return None
    if getattr(pos_state, "hedge_active", False):
        return None

    gain = pos_state.gain_pct
    min_loss = float(getattr(cfg, "OBLIGATORY_HEDGE_MIN_LOSS_PCT", -0.25))
    if gain > min_loss:
        return None

    side = pos_state.side
    hedge_side = "SHORT" if side == "LONG" else "LONG"

    _oh_use = {
        "3m": bool(getattr(cfg, "OBLIGATORY_HEDGE_WT_USE_3M", True)),
        "1m": bool(getattr(cfg, "OBLIGATORY_HEDGE_WT_USE_1M", False)),
        "15m": bool(getattr(cfg, "OBLIGATORY_HEDGE_WT_USE_15M", False)),
        "1h": bool(getattr(cfg, "OBLIGATORY_HEDGE_WT_USE_1H", True)),
    }
    _oh_req = int(getattr(cfg, "OBLIGATORY_HEDGE_WT_TFS_REQUIRED", 2))

    _oh_wt_against = 0
    _oh_3m_against = False
    for tf in ("1m", "3m", "15m", "1h"):
        if not _oh_use.get(tf, False):
            continue
        against = _wt_against(store, bar_idx, side, tf)
        _oh_wt_against += int(against)
        if tf == "3m":
            _oh_3m_against = against

    _oh_user_trigger = _oh_3m_against
    if not (_oh_wt_against >= _oh_req or _oh_user_trigger):
        return None

    hedge_qty = compute_hedge_size(pos_state, cfg)
    return {
        "side": hedge_side,
        "size_qty": hedge_qty,
        "reason": f"OBLIGATORY_HEDGE_g{gain:.2f}pct_wt_against={_oh_wt_against}_3m={_oh_3m_against}",
        "gain": gain,
        "wt_against_count": _oh_wt_against,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path 3: HEDGE_FAILED fallback close
# ─────────────────────────────────────────────────────────────────────────────

def check_hedge_failed_fallback(
    store: "_NPZStore",
    bar_idx: int,
    pos_state: "_PositionState",
    mode: str = "crypto",
    cfg: "Optional[VecConfig]" = None,
    hedge_attempt_result: bool = False,
) -> Optional[Dict]:
    """Determine if a HEDGE_FAILED fallback close should fire.

    Source: ez_positions_quick.py:5271 + ez_manage.py:14405.

    When scan_and_hedge_losers or OBLIGATORY_HEDGE fires but the hedge order
    CANNOT be opened (no quote, blacklist, already hedged), the losing position
    is closed immediately using a reason that contains 'HEDGE_FAILED'.

    Per CLAUDE.md EXIT RULES: HEDGE_FAILED is one of the 3 sanctioned loss-exit paths
    that can bypass UNIVERSAL_NOLOSS_GATE. The bypass reason list in config.py includes:
      'HEDGE_FAILED', 'HEDGE_FAILED_FALLBACK_CLOSE'

    In vec backtest sim: we model hedge_attempt_result as True (hedge always succeeds
    in a backtest — no order routing to fail). This function is provided for completeness
    and for validation against live /history/ events where hedge DID fail.

    Args:
        store:                  NPZ store (for wt signal at time of failure).
        bar_idx:                Current bar index.
        pos_state:              The losing position state.
        mode:                   "crypto" or "tradier".
        cfg:                    VecConfig.
        hedge_attempt_result:   True if hedge opened (no fallback), False if failed.

    Returns:
        Dict with:
          reason       - reason string containing 'HEDGE_FAILED' (for NOLOSS bypass)
          close_gain   - the gain at close (usually negative)
          is_loss_exit - True (this IS a loss exit, sanctioned by HEDGE_FAILED bypass)
        or None if hedge_attempt_result=True or conditions not met.
    """
    if hedge_attempt_result:
        return None
    if cfg is None:
        return None
    if not pos_state.open:
        return None
    if mode != "crypto":
        return None
    if not bool(getattr(cfg, "HEDGE_FAILED_FALLBACK_CLOSE_ENABLED", True)):
        return None

    gain = pos_state.gain_pct
    _3m_against = _wt_against(store, bar_idx, pos_state.side, "3m")
    _1h_against = _wt_against(store, bar_idx, pos_state.side, "1h")

    return {
        "reason": f"HEDGE_FAILED_FALLBACK_CLOSE_g{gain:.2f}%_wt3m={_3m_against}_wt1h={_1h_against}",
        "close_gain": gain,
        "is_loss_exit": True,
        "noloss_bypass": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Hedge close: check if hedge position should be closed (WT reversed back)
# ─────────────────────────────────────────────────────────────────────────────

def check_hedge_close(
    store: "_NPZStore",
    bar_idx: int,
    hedge_pos_state: "_PositionState",
    mode: str = "crypto",
    cfg: "Optional[VecConfig]" = None,
) -> Optional[Dict]:
    """Check if an open hedge position should be closed.

    Source: config.py HEDGE_CLOSE_MODE='wt_3m' (2026-05-10 user mandate).
    The hedge closes when wt1_3m reverses back IN FAVOR of the original loser
    (i.e., WT is no longer against the original loser = WT is now AGAINST the hedge).

    The hedge is SHORT if original was LONG, so:
      - Close hedge SHORT when wt1_3m > wt2_3m (WT bullish = against the SHORT hedge)
    And vice versa for hedge LONG (original SHORT loser).

    Args:
        store:           NPZ store.
        bar_idx:         Current bar index.
        hedge_pos_state: The HEDGE position's _PositionState (must be open, hedge_active=True).
        mode:            "crypto" or "tradier".
        cfg:             VecConfig.

    Returns:
        Dict with reason string, or None if no close signal.
    """
    if cfg is None:
        return None
    if not hedge_pos_state.open:
        return None
    if not getattr(hedge_pos_state, "hedge_active", False):
        return None
    if mode != "crypto":
        return None

    close_mode = str(getattr(cfg, "HEDGE_CLOSE_MODE", "wt_3m"))
    hedge_side = hedge_pos_state.side
    gain = hedge_pos_state.gain_pct

    if close_mode == "wt_3m":
        should_close = _wt_against(store, bar_idx, hedge_side, "3m")
    elif close_mode in ("wt_3m_and_1h", "wt_3m1h"):
        should_close = _wt_against(store, bar_idx, hedge_side, "3m") and \
                       _wt_against(store, bar_idx, hedge_side, "1h")
    elif close_mode == "wt_15m":
        should_close = _wt_against(store, bar_idx, hedge_side, "15m")
    else:
        should_close = _wt_against(store, bar_idx, hedge_side, "3m")

    if not should_close:
        return None

    wt1_3m = store.f("wt1_3m", bar_idx, 0.0)
    wt2_3m = store.f("wt2_3m", bar_idx, 0.0)
    return {
        "reason": f"HEDGE_CLOSE_{close_mode.upper()}_wt1={wt1_3m:.1f}_wt2={wt2_3m:.1f}_gain={gain:.2f}%",
        "close_gain": gain,
        "mode": close_mode,
    }
