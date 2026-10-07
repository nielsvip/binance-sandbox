"""
vec_paths/exit_to_reduce_adapter.py — Convert CLOSE-mode exits to fractional REDUCEs.

═══════════════════════════════════════════════════════════════════════════════
RATIONALE — THE #1 VEC-VS-LIVE PARITY GAP (2026-05-26 audit)
═══════════════════════════════════════════════════════════════════════════════
Live (ez_manage.py + tradier_manage.py + ez_positions_quick.py) NEVER emits a
full-position CLOSE event during normal trading. Audit of 1,395 sym-side
history files across 5 crypto accounts (flz/fin/men/inf/ang) — 71,322 total
events — shows:

    AUGMENT  = 43,004 events  (60.3%)
    REDUCE   = 28,318 events  (39.7%)
    CLOSE    =      0 events  ( 0.0%)

═══════════════════════════════════════════════════════════════════════════════
BATCH 2 (2026-05-26 22:00) — USER-CLARIFIED ARCHITECTURE
See: data/_diagnostic/REDUCE_VS_CLOSE_ARCHITECTURE.md
═══════════════════════════════════════════════════════════════════════════════
User clarified: every live REDUCE on the main Finandy webhook is effectively a
FULL CLOSE (state.qty → 0). The "tiny stat residual" policy was killed long ago.
Only PPL step 1 (routed to `<acct>_WEBHOOK_URL2`) is genuinely partial (50%).

`/history/` writes `type="REDUCE"` as a LABEL of the order action, NOT a
description of position state after the order. Zero CLOSE events in /history/
is an artifact of the event-label convention, not a partial-close strategy.

BATCH 1 default (frac=1/RATIO_MULTIPLIER=25%) was WRONG. Arm B blew trades up
to 172k (4× baseline) because every exit only trimmed 25%, leaving 75% of the
position open to fire again on the next bar.

CORRECT default (this module):
    * Default frac = 1.0 (FULL CLOSE on state, REDUCE on event label)
    * PPL step 1 reason routing → frac = 0.5 (the genuine partial path)
    * Optional VEC_REDUCE_CASCADE_COOLDOWN_S as a defense-in-depth (default 0.0)

The vec engine (`v8_vec_sweep.py`) has ~24 distinct CLOSE-emitting sites
(R1, RZ_CASCADE, MICRO_SCALP, QUALITY_TOP_EXIT, BTC_LOOP_EXIT, NLK, WT_VEL,
etc.). Each fires CLOSE → state.qty = 0 → next bar OPENs again. With the
corrected adapter (frac=1.0 default), the behavior is bit-equivalent in P&L
terms to the legacy CLOSE — only the event label flips REDUCE for /history/
parity.

═══════════════════════════════════════════════════════════════════════════════
ENABLEMENT — opt-in via config knob, ROLLBACK = single flag flip
═══════════════════════════════════════════════════════════════════════════════
Activated via `config.VEC_LIVE_REDUCE_PARITY_ENABLED`. Default = False, which
preserves current vec behaviour bit-exactly. Set True via override JSON or
`config.py` patch to enable the adapter.

Per-site override: each call site passes `force_close=True` for the genuine
end-of-sim MtM CLOSE (`MTM_FINAL_BAR_NOLIES_RULE2`). The MtM CLOSE is the
ONE place where vec MUST close the position; everywhere else is fair game.

═══════════════════════════════════════════════════════════════════════════════
KNOBS (BATCH 2)
═══════════════════════════════════════════════════════════════════════════════
    VEC_LIVE_REDUCE_PARITY_ENABLED: bool = False       # master switch
    VEC_LIVE_REDUCE_DEFAULT_FRAC:    float = 1.0       # frac for non-PPL exits (full close)
    VEC_LIVE_REDUCE_PPL_STEP1_FRAC:  float = 0.5       # frac for PPL step 1
    VEC_LIVE_REDUCE_PPL_REASONS:     tuple = (
        "PARTIAL_PROFIT_LOCK_STEP1", "PPL_STEP1",
    )                                                  # reason substrings that route to STEP1_FRAC
    VEC_REDUCE_CASCADE_COOLDOWN_S:   float = 0.0       # seconds; refuse REDUCE if (ts - last_reduce_ts) < this
                                                       # default 0.0 = inert when frac=1.0 (no cascade)
    VEC_LIVE_REDUCE_PARITY_KEEP_DUST: bool = False     # legacy (still honored when frac<1.0)
    VEC_LIVE_REDUCE_PARITY_FRAC:     float = 0.0       # legacy explicit override (still respected if > 0)

═══════════════════════════════════════════════════════════════════════════════
PUBLIC API
═══════════════════════════════════════════════════════════════════════════════
    exit_to_reduce(
        state,           # _PositionState from v8_vec_sweep
        pos,             # mirror Position obj
        events,          # list[TradeEvent] to append the event to
        trade_returns,   # list[float] of per-trade pnl
        ts,              # float bar timestamp
        mark,            # float current price
        gain,            # float current gain %
        reason,          # str exit reason (preserved verbatim, also drives PPL routing)
        is_long,         # bool
        cfg,             # VecConfig / SweepConfig
        force_close=False,  # if True, ignore adapter and emit CLOSE
        TradeEvent=None,    # the dataclass to use for event construction
        clear_hedge=True,   # whether to reset hedge state on a CLOSE
    ) -> bool

Returns True if a full CLOSE was emitted (state fully zeroed), False if a
fractional REDUCE was emitted (position still partially open).

When frac == 1.0 (default), the adapter still returns True (full close) so
callers `continue` the per-bar loop — same control flow as legacy CLOSE.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from typing import Any, List, Tuple


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _is_adapter_enabled(cfg: Any) -> bool:
    """Master flag. Default False = byte-exact preservation of vec behaviour."""
    return bool(getattr(cfg, "VEC_LIVE_REDUCE_PARITY_ENABLED", False))


def _ppl_reasons(cfg: Any) -> Tuple[str, ...]:
    raw = getattr(cfg, "VEC_LIVE_REDUCE_PPL_REASONS",
                  ("PARTIAL_PROFIT_LOCK_STEP1", "PPL_STEP1"))
    try:
        return tuple(str(r) for r in raw if r)
    except Exception:
        return ("PARTIAL_PROFIT_LOCK_STEP1", "PPL_STEP1")


def _reason_is_ppl_step1(reason: str, cfg: Any) -> bool:
    if not reason:
        return False
    reasons = _ppl_reasons(cfg)
    r = str(reason)
    return any(p in r for p in reasons)


def _exit_to_reduce_frac(cfg: Any, reason: str = "") -> float:
    """Return the fractional reduce amount in (0, 1].

    Dispatch (first match wins):
      1. PPL step 1 reason match → VEC_LIVE_REDUCE_PPL_STEP1_FRAC (default 0.5)
      2. Explicit legacy override VEC_LIVE_REDUCE_PARITY_FRAC (if > 0, ≤ 1)
      3. VEC_LIVE_REDUCE_DEFAULT_FRAC (default 1.0 — full close)
    """
    if _reason_is_ppl_step1(reason, cfg):
        v = _sf(getattr(cfg, "VEC_LIVE_REDUCE_PPL_STEP1_FRAC", 0.5), 0.5)
        if 0.0 < v <= 1.0:
            return v
        return 0.5

    legacy = getattr(cfg, "VEC_LIVE_REDUCE_PARITY_FRAC", None)
    if legacy is not None:
        try:
            lv = float(legacy)
            if 0.0 < lv <= 1.0:
                return lv
        except (TypeError, ValueError):
            pass

    default = _sf(getattr(cfg, "VEC_LIVE_REDUCE_DEFAULT_FRAC", 1.0), 1.0)
    if 0.0 < default <= 1.0:
        return default
    return 1.0


def _reduce_kills_position(cfg: Any) -> bool:
    """When a partial REDUCE would leave the position below MIN_POSITION_SIZE,
    live drops the residual to 0 via the daemon trim. Mirror by full-closing.

    Disable by setting VEC_LIVE_REDUCE_PARITY_KEEP_DUST = True. Default False.
    Moot when frac == 1.0 (no residual to keep).
    """
    return not bool(getattr(cfg, "VEC_LIVE_REDUCE_PARITY_KEEP_DUST", False))


def _cascade_cooldown_s(cfg: Any) -> float:
    """Defense-in-depth: refuse a REDUCE if (ts - state.last_reduce_ts) < this.
    Default 0.0 = inert (no cooldown). Set positive to mirror live's
    `_recent_reduces` Redis floor (~15-60s). Inert when frac==1.0 since no
    cascade is possible.
    """
    return _sf(getattr(cfg, "VEC_REDUCE_CASCADE_COOLDOWN_S", 0.0), 0.0)


def _emit_close(state, pos, events, trade_returns, ts, mark, gain, reason,
                clear_hedge, TradeEvent):
    """Internal: emit full CLOSE event and reset all per-position state.
    Used by both force_close path and dust-residual path. Matches the
    state-reset block used at every legacy CLOSE site in v8_vec_sweep.py.
    """
    ev = TradeEvent(
        ts=ts, type="CLOSE", qty=state.qty, price=mark,
        value=state.qty * mark, reason=reason, pnl_pct=gain,
    )
    events.append(ev)
    trade_returns.append(gain)
    state.qty = 0.0
    state.entry_price = 0.0
    state.initial_qty = 0.0
    state.opened_at = 0.0
    state.augmented_count = 0
    state.max_gain = 0.0
    state.last_reduce_ts = ts
    # 2026-05-26 BATCH 4 — track whether last reduce/close was a loss so the
    # refined WT_CROSSUNDER cooldown can bypass for urgent loss-cuts. Defensive
    # — setattr only when field exists (older SymState compat).
    try:
        state.last_reduce_was_loss = bool(gain < 0.0)
    except Exception:
        pass
    # 2026-05-27 BATCH 5 — stamp last_close_* for reentry signal use.
    try:
        state.last_close_price = float(mark)
        state.last_close_ts = float(ts)
        state.last_close_reason = str(reason)
    except Exception:
        pass
    if clear_hedge:
        state.hedge_active = False
        state.hedge_qty = 0.0
        state.hedge_entry_price = 0.0
        state.hedge_completed_ts = ts
    state.r1_stop_price = 0.0
    if pos is not None:
        try:
            pos.reset_ppl()
        except Exception:
            pass
        try:
            pos.gain_pct = 0.0
        except Exception:
            pass


def exit_to_reduce(
    state: Any,
    pos: Any,
    events: List[Any],
    trade_returns: List[float],
    ts: float,
    mark: float,
    gain: float,
    reason: str,
    is_long: bool,
    cfg: Any,
    force_close: bool = False,
    TradeEvent: Any = None,
    clear_hedge: bool = True,
) -> bool:
    """Emit either a fractional REDUCE (parity mode) or a full CLOSE (legacy mode).

    Returns:
        True  if a full CLOSE was emitted (qty zeroed, ppl cleared, last_reduce_ts updated).
              ALSO returns True when frac==1.0 because state.qty ends at 0 — same
              control flow as legacy CLOSE.
        False if a fractional REDUCE was emitted (qty reduced by frac, position still open).

    Caller MUST use the return value to decide whether to `continue` the
    per-bar loop (True → close → continue) or fall through (False → partial reduce,
    next exit/augment block may run on the residual).

    Default behaviour matches the current vec engine: full CLOSE (frac=1.0).
    """
    if TradeEvent is None:
        raise RuntimeError("exit_to_reduce: TradeEvent class must be passed in")

    # Force-close path = MtM final-bar, or adapter is disabled.
    if force_close or not _is_adapter_enabled(cfg):
        _emit_close(state, pos, events, trade_returns, ts, mark, gain, reason,
                    clear_hedge, TradeEvent)
        return True

    # ─── ADAPTER ON ─────────────────────────────────────────────────────────────
    # Defense-in-depth cascade cooldown. Inert when frac==1.0 (no cascade
    # possible), but mirrors live's _recent_reduces Redis floor when set.
    cooldown_s = _cascade_cooldown_s(cfg)
    if cooldown_s > 0.0:
        last_red = _sf(getattr(state, "last_reduce_ts", 0.0), 0.0)
        if last_red > 0.0 and (ts - last_red) < cooldown_s:
            # Refuse — skip this exit, position keeps running.
            return False

    frac = _exit_to_reduce_frac(cfg, reason=reason)

    # frac >= 1.0 → emit REDUCE-labeled event but zero the position (full close
    # in P&L terms). Matches user-clarified architecture.
    if frac >= 1.0 - 1e-9:
        ev = TradeEvent(
            ts=ts, type="REDUCE", qty=state.qty, price=mark,
            value=state.qty * mark, reason=reason, pnl_pct=gain,
        )
        events.append(ev)
        trade_returns.append(gain)
        state.qty = 0.0
        state.entry_price = 0.0
        state.initial_qty = 0.0
        state.opened_at = 0.0
        state.augmented_count = 0
        state.max_gain = 0.0
        state.last_reduce_ts = ts
    # 2026-05-26 BATCH 4 — track whether last reduce/close was a loss so the
    # refined WT_CROSSUNDER cooldown can bypass for urgent loss-cuts. Defensive
    # — setattr only when field exists (older SymState compat).
    try:
        state.last_reduce_was_loss = bool(gain < 0.0)
    except Exception:
        pass
    # 2026-05-27 BATCH 5 — stamp last_close_* for reentry signal use.
    try:
        state.last_close_price = float(mark)
        state.last_close_ts = float(ts)
        state.last_close_reason = str(reason)
    except Exception:
        pass
        if clear_hedge:
            state.hedge_active = False
            state.hedge_qty = 0.0
            state.hedge_entry_price = 0.0
            state.hedge_completed_ts = ts
        state.r1_stop_price = 0.0
        if pos is not None:
            try:
                pos.reset_ppl()
            except Exception:
                pass
            try:
                pos.gain_pct = 0.0
            except Exception:
                pass
        return True

    # ─── PARTIAL REDUCE PATH (frac < 1.0, primarily PPL step 1) ───────────────
    reduce_qty = state.qty * frac
    if reduce_qty <= 0.0:
        return False

    residual_qty = state.qty - reduce_qty
    residual_value = residual_qty * mark
    min_pos_value = _sf(getattr(cfg, "MIN_POSITION_SIZE", 45.0), 45.0)

    # If residual is below MIN_POSITION_SIZE, live's daemon trim flattens it
    # within bars; mirror by treating this as a full close (label = REDUCE).
    if _reduce_kills_position(cfg) and residual_value < min_pos_value:
        ev = TradeEvent(
            ts=ts, type="REDUCE", qty=state.qty, price=mark,
            value=state.qty * mark, reason=f"{reason}_VIA_REDUCE_DUST", pnl_pct=gain,
        )
        events.append(ev)
        trade_returns.append(gain)
        state.qty = 0.0
        state.entry_price = 0.0
        state.initial_qty = 0.0
        state.opened_at = 0.0
        state.augmented_count = 0
        state.max_gain = 0.0
        state.last_reduce_ts = ts
    # 2026-05-26 BATCH 4 — track whether last reduce/close was a loss so the
    # refined WT_CROSSUNDER cooldown can bypass for urgent loss-cuts. Defensive
    # — setattr only when field exists (older SymState compat).
    try:
        state.last_reduce_was_loss = bool(gain < 0.0)
    except Exception:
        pass
        if clear_hedge:
            state.hedge_active = False
            state.hedge_qty = 0.0
            state.hedge_entry_price = 0.0
            state.hedge_completed_ts = ts
        state.r1_stop_price = 0.0
        if pos is not None:
            try:
                pos.reset_ppl()
            except Exception:
                pass
            try:
                pos.gain_pct = 0.0
            except Exception:
                pass
        return True

    # Genuine partial reduce: emit REDUCE event, decrement qty, preserve entry_price.
    ev = TradeEvent(
        ts=ts, type="REDUCE", qty=reduce_qty, price=mark,
        value=reduce_qty * mark, reason=reason, pnl_pct=gain,
    )
    events.append(ev)
    trade_returns.append(gain * frac)
    state.qty = residual_qty
    state.last_reduce_ts = ts
    # 2026-05-26 BATCH 4 — track whether last reduce/close was a loss so the
    # refined WT_CROSSUNDER cooldown can bypass for urgent loss-cuts. Defensive
    # — setattr only when field exists (older SymState compat).
    try:
        state.last_reduce_was_loss = bool(gain < 0.0)
    except Exception:
        pass
    if pos is not None:
        try:
            pos.gain_pct = gain
        except Exception:
            pass
    return False


# ─── Lightweight scalar reasoner used by validators ────────────────────────────
def predict_event_type(
    cfg: Any,
    reason: str,
    is_mtm_final_bar: bool = False,
) -> str:
    """For validator parity checks: given a config and a vec exit reason, predict
    whether vec WOULD emit REDUCE or CLOSE. Mirrors the runtime decision in
    exit_to_reduce() without needing position state.

    With adapter ON, every exit emits REDUCE label (frac>=1.0 → REDUCE label
    even on full close). With adapter OFF, every exit emits CLOSE."""
    if is_mtm_final_bar:
        return "CLOSE"
    if not _is_adapter_enabled(cfg):
        return "CLOSE"
    return "REDUCE"
