"""
vec_paths/hedge_scan_gates.py — 7-gate hedge-scan cluster (live parity module).

THIS IS THE BIGGEST GAP between live and backtest_v8_engine — ~150 LOC of
scalar gate logic in ez_positions_quick.py:5016-5272 that runs every 100-500ms
in production and decides whether a losing position spawns a same-symbol hedge.

Gate ordering MUST match live EXACTLY. The order below is taken from
ez_positions_quick.py:5016-5272 as of 2026-05-12:

    Gate 1: HEDGE_SYMBOL_LOCK         (line 5016) — symbol-level dedup; locked
                                                    while same-sym hedge side qty>0.
    Gate 2: HEDGE_SCAN_DEBOUNCE       (line 5038) — per-position debounce
                                                    (SCAN_HEDGE_DEBOUNCE_SECONDS, default 60s).
    Gate 3: HEDGE_COMPLETED_LOCKOUT   (line 5042) — 1h-style lockout after hedge fire
                                                    (HEDGE_COMPLETED_LOCKOUT_SECONDS=60).
    Gate 4: TRACKER_HEDGE_CONSULTATION(line 5047) — block if exit_candidates says
                                                    is_hedge / hedge_for / promoted_from_hedge,
                                                    or active_hedges already has it.
    Gate 5: HEDGE_DETERIORATING_GAIN  (line 5096) — gain must be ACTIVELY dropping
                                                    (cur < prev - delta_pp). BYPASSED when
                                                    USER_TRIGGER active.
    Gate 6: HEDGE_NEWBORN_GRACE       (line 5140) — 10-min grace + DC breach exception.
    Gate 7: HEDGE_WT_TRIGGER          (line 5206) — WT cascade:
                                                    1) 3m AND (15m OR 1h) [default 2026-05-11]
                                                    2) 3m AND 1h
                                                    3) 3m alone
                                                    4) legacy: 15m OR (3m AND 1h)
    Fall-through: HEDGE_FAILED_FALLBACK_CLOSE (line 5271) — if hedge can't open.

USER CONTRACT (2026-05-09 mandate): hedge must NEVER FAIL when gain<0.
Live currently has these strip defaults (see CLAUDE.md and memory entries):
    HEDGE_DETERIORATING_GAIN_ENABLED=False
    HEDGE_NEWBORN_GRACE_MINUTES=0.0 (2026-05-12 mandate)
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H=True (2026-05-11 default)
    HEDGE_COMPLETED_LOCKOUT_SECONDS=60 (was 3600)

The trigger cascade MUST be respected in the order documented above. Any change
to gate order requires synchronized edit of:
    - ez_positions_quick.py:5016-5272 (live scan loop)
    - ez_manage.py:14365-14401 (OBLIGATORY_HEDGE in execute_now)
    - vec_paths/hedge_scan_gates.py (this file)
    - vec_paths/hedge_engine.py (legacy single-fn wrapper)

DECISION OUTPUT
---------------
evaluate_hedge_scan_gates_core() returns 4-tuple:
    (should_fire: bool, hedge_qty: float, reason: str, blocked_by_gate: str | None)

blocked_by_gate names the FIRST gate that rejected (one of):
    "HEDGE_SYMBOL_LOCK", "HEDGE_SCAN_DEBOUNCE", "HEDGE_COMPLETED_LOCKOUT",
    "TRACKER_HEDGE_CONSULTATION", "HEDGE_DETERIORATING_GAIN",
    "HEDGE_NEWBORN_GRACE", "HEDGE_WT_TRIGGER",
    "POSITION_NOT_OPEN", "MODE_NOT_CRYPTO", "HEDGE_MODE_DISABLED",
    "GAIN_NOT_LOSS", "OPPOSITE_SIDE_OPEN", "HEDGE_ALREADY_ACTIVE",
    "NOTIONAL_TOO_SMALL"
or None on fire.

evaluate_hedge_scan_gates_vec() — per-bar arrays for backtest_v8_engine.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple, Any

import numpy as np

if TYPE_CHECKING:
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState


# ─────────────────────────────────────────────────────────────────────────────
# Constants (must mirror live defaults)
# ─────────────────────────────────────────────────────────────────────────────

# Live defaults (from config.py / ez_positions_quick.py — see CLAUDE.md):
DEFAULT_SCAN_HEDGE_DEBOUNCE_SECONDS = 60.0
DEFAULT_HEDGE_COMPLETED_LOCKOUT_SECONDS = 60.0
DEFAULT_HEDGE_NEWBORN_GRACE_MINUTES = 0.0           # was 10.0 pre-2026-05-12
DEFAULT_HEDGE_NEWBORN_DC_BREACH_ALLOWED = True
DEFAULT_HEDGE_DETERIORATING_GAIN_ENABLED = False    # strip default
DEFAULT_HEDGE_DETERIORATING_GAIN_DELTA_PP = 0.10
DEFAULT_OBLIGATORY_HEDGE_MIN_LOSS_PCT = -0.25
DEFAULT_HEDGE_SAME_SYMBOL_PCT = 1.0                 # live=1.0; vec_engine cfg defaults 0.5
DEFAULT_HEDGE_MODE = True
DEFAULT_HEDGE_ALL_POSITIONS = False
DEFAULT_MIN_NOTIONAL_USD = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper — WT against (mirrors live string)
# ─────────────────────────────────────────────────────────────────────────────

def _wt_against(wt1: float, wt2: float, is_long: bool) -> bool:
    """LONG: wt1 < wt2 means WT bearish (against LONG).
    SHORT: wt1 > wt2 means WT bullish (against SHORT)."""
    if is_long:
        return wt1 < wt2
    return wt1 > wt2


# ─────────────────────────────────────────────────────────────────────────────
# Core (scalar) — one position, one bar
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_hedge_scan_gates_core(
    position_key: str,
    account_key: str,
    pos: Any,
    indicators: Dict[str, float],
    tracker_state: Dict[str, Any],
    cfg: Any,
    now_ts: float,
    mode: str = "crypto",
) -> Tuple[bool, float, str, Optional[str]]:
    """Evaluate the 7-gate hedge-scan cluster for one losing position at one bar.

    Mirrors ez_positions_quick.py:5002-5272 EXACTLY (gate order + early returns).

    Args:
        position_key:   "<account>:<SYMBOL>_<SIDE>" — e.g. "ang:BTCUSDC_LONG".
        account_key:    Account name (e.g. "ang", "inf").
        pos:            Position-like object with attrs:
                            positionAmt, entry_price, mark_price, gain (or pnl_pct),
                            opened_at, prev_gain, augment_reason.
                        Also accepts a plain dict with the same keys.
        indicators:     Dict of {wt1_3m, wt2_3m, wt1_15m, wt2_15m, wt1_1h, wt2_1h,
                                  dc_low_3m, dc_high_3m}.
        tracker_state:  Dict containing:
                            "active_hedges": list[dict]            (Gate 4)
                            "exit_candidates": dict[pk -> dict]    (Gate 4)
                            "_hedge_completed": dict[pk -> ts]     (Gate 3)
                            "_scan_hedge_debounce": dict[pk -> ts] (Gate 2)
                            "_symbol_hedge_active": dict[str->ts]  (Gate 1)
                            "positions": dict[pk -> pos_obj]       (Gate 1 + opposite-side check)
                            "scan_hedge_debounce_secs": float      (override)
        cfg:            Config object (config.py-like) with all HEDGE_* knobs.
        now_ts:         Current wall-clock seconds (time.time()).
        mode:           "crypto" or "tradier".

    Returns:
        (should_fire, hedge_qty, reason, blocked_by_gate)
            should_fire   — True if all gates passed.
            hedge_qty     — qty to fire (loser.qty * HEDGE_SAME_SYMBOL_PCT) on fire,
                            else 0.0.
            reason        — reason string for /history/ logging on fire,
                            else short reject reason.
            blocked_by_gate — name of first failing gate, else None on fire.
    """
    # ── 0) Position basics ──────────────────────────────────────────────────
    def _attr(o, k, dflt=0):
        if isinstance(o, dict):
            return o.get(k, dflt)
        return getattr(o, k, dflt)

    pos_amt = float(_attr(pos, "positionAmt", 0) or 0)
    qty = abs(pos_amt)
    entry_price = float(_attr(pos, "entry_price", 0) or 0)
    mark_price = float(_attr(pos, "mark_price", 0) or 0)

    is_long = position_key.endswith("_LONG")
    side = "LONG" if is_long else "SHORT"
    hedge_side = "SHORT" if is_long else "LONG"
    try:
        symbol = position_key.split(":", 1)[1].replace("_LONG", "").replace("_SHORT", "")
    except (IndexError, AttributeError):
        return (False, 0.0, "BAD_POSITION_KEY", "POSITION_NOT_OPEN")

    # Mode + master-switch guards — these run BEFORE the loop in live
    # (the for-loop iterates only over positions of the account; mode/HEDGE_MODE
    # gating happens at the caller level). We treat them as preflight rejects.
    if mode != "crypto":
        return (False, 0.0, "stocks_no_auto_hedge", "MODE_NOT_CRYPTO")
    if not bool(getattr(cfg, "HEDGE_MODE", DEFAULT_HEDGE_MODE)):
        return (False, 0.0, "hedge_mode_disabled", "HEDGE_MODE_DISABLED")

    # ─────────────────────────────────────────────────────────────────────
    # GATE 1: HEDGE_SYMBOL_LOCK (live line 5016-5036) — FIRST gate in live.
    # Runs before qty/notional/gain checks (live evaluates these lower).
    # Live behaviour: a symbol whose HEDGE SIDE has qty>0 cannot spawn another
    # hedge for the same loser. If the hedge side is FLAT (qty=0), the lock
    # is auto-cleared and we proceed.
    # ─────────────────────────────────────────────────────────────────────
    sym_lock_key = f"{account_key}:{symbol}"
    symbol_hedge_active = tracker_state.get("_symbol_hedge_active", {}) or {}
    positions_all = tracker_state.get("positions", {}) or {}
    hedge_side_pk = f"{account_key}:{symbol}_{hedge_side}"
    hedge_side_pos = positions_all.get(hedge_side_pk)
    hedge_side_qty = abs(float(_attr(hedge_side_pos, "positionAmt", 0) or 0)) if hedge_side_pos else 0.0

    if sym_lock_key in symbol_hedge_active:
        if hedge_side_qty > 0:
            return (False, 0.0,
                    f"hedge_side_{hedge_side}_qty={hedge_side_qty:.4f}_still_open",
                    "HEDGE_SYMBOL_LOCK")
        # else: lock would be auto-cleared in live — we treat it as cleared and proceed.

    # ─────────────────────────────────────────────────────────────────────
    # GATE 2: HEDGE_SCAN_DEBOUNCE (live line 5038-5041)
    # ─────────────────────────────────────────────────────────────────────
    scan_debounce_map = tracker_state.get("_scan_hedge_debounce", {}) or {}
    debounce_secs = float(tracker_state.get("scan_hedge_debounce_secs",
                                            DEFAULT_SCAN_HEDGE_DEBOUNCE_SECONDS))
    last_fire = float(scan_debounce_map.get(position_key, 0) or 0)
    if last_fire > 0 and (now_ts - last_fire) < debounce_secs:
        return (False, 0.0,
                f"debounce_{now_ts - last_fire:.0f}s_lt_{debounce_secs:.0f}s",
                "HEDGE_SCAN_DEBOUNCE")

    # ─────────────────────────────────────────────────────────────────────
    # GATE 3: HEDGE_COMPLETED_LOCKOUT (live line 5042-5046)
    # ─────────────────────────────────────────────────────────────────────
    hedge_completed_map = tracker_state.get("_hedge_completed", {}) or {}
    lockout_secs = float(getattr(cfg, "HEDGE_COMPLETED_LOCKOUT_SECONDS",
                                 DEFAULT_HEDGE_COMPLETED_LOCKOUT_SECONDS))
    hc_ts = float(hedge_completed_map.get(position_key, 0) or 0)
    if hc_ts > 0 and (now_ts - hc_ts) < lockout_secs:
        return (False, 0.0,
                f"completed_lockout_{now_ts - hc_ts:.0f}s_lt_{lockout_secs:.0f}s",
                "HEDGE_COMPLETED_LOCKOUT")

    # ─────────────────────────────────────────────────────────────────────
    # GATE 4: TRACKER_HEDGE_CONSULTATION (live line 5047-5085)
    # Block if tracker says this is a hedge / promoted hedge / has live hedge child.
    # ─────────────────────────────────────────────────────────────────────
    exit_candidates = tracker_state.get("exit_candidates", {}) or {}
    active_hedges = tracker_state.get("active_hedges", []) or []
    tracker_data = exit_candidates.get(position_key)
    if isinstance(tracker_data, dict):
        if tracker_data.get("is_hedge") or tracker_data.get("hedge_for"):
            return (False, 0.0, "tracker_is_hedge", "TRACKER_HEDGE_CONSULTATION")
        if tracker_data.get("promoted_from_hedge"):
            return (False, 0.0, "promoted_from_hedge", "TRACKER_HEDGE_CONSULTATION")
    # Check active_hedges — position might be the hedge itself, OR have a live hedge child
    is_hedge_in_tracker = any(
        isinstance(h, dict) and h.get("position_key") == position_key and h.get("is_hedge", False)
        for h in active_hedges
    )
    if is_hedge_in_tracker:
        return (False, 0.0, "is_hedge_in_active_hedges", "TRACKER_HEDGE_CONSULTATION")
    # Live "stale-tracker auto-clean" — if matching hedge entries have qty=0,
    # treat them as stale. Live count of "live" entries (qty>0) blocks.
    matching_hedges = [
        h for h in active_hedges
        if isinstance(h, dict) and h.get("losing_position_key") == position_key
    ]
    live_count = 0
    for h in matching_hedges:
        hk = h.get("position_key")
        if not hk:
            continue
        hp = positions_all.get(hk)
        hq = abs(float(_attr(hp, "positionAmt", 0) or 0)) if hp else 0.0
        if hq > 0:
            live_count += 1
    if live_count > 0:
        return (False, 0.0,
                f"tracker_live_hedge_children={live_count}",
                "TRACKER_HEDGE_CONSULTATION")

    # ─────────────────────────────────────────────────────────────────────
    # Preflight rejects between Gate 4 and Gate 5 — mirrors live
    # ez_positions_quick.py:5086-5095 ordering exactly.
    # ─────────────────────────────────────────────────────────────────────
    if qty <= 0 or entry_price <= 0 or mark_price <= 0:
        return (False, 0.0, "position_not_open", "POSITION_NOT_OPEN")
    notional = qty * mark_price
    if notional < DEFAULT_MIN_NOTIONAL_USD:
        return (False, 0.0, f"notional_{notional:.2f}_lt_5", "NOTIONAL_TOO_SMALL")
    if is_long:
        pnl_pct = (mark_price - entry_price) / entry_price * 100.0
    else:
        pnl_pct = (entry_price - mark_price) / entry_price * 100.0
    _hedge_all = bool(getattr(cfg, "HEDGE_ALL_POSITIONS", DEFAULT_HEDGE_ALL_POSITIONS))
    _oh_min_loss = float(getattr(cfg, "OBLIGATORY_HEDGE_MIN_LOSS_PCT", DEFAULT_OBLIGATORY_HEDGE_MIN_LOSS_PCT))
    if not _hedge_all and pnl_pct >= _oh_min_loss:
        return (False, 0.0, f"gain_{pnl_pct:.2f}_above_loss_thr_{_oh_min_loss:.2f}", "GAIN_NOT_LOSS")

    # ─────────────────────────────────────────────────────────────────────
    # Pre-compute WT signals (used by Gates 5 and 7)
    # ─────────────────────────────────────────────────────────────────────
    w1_3m = float(indicators.get("wt1_3m", 0) or 0)
    w2_3m = float(indicators.get("wt2_3m", 0) or 0)
    w1_15m = float(indicators.get("wt1_15m", 0) or 0)
    w2_15m = float(indicators.get("wt2_15m", 0) or 0)
    w1_1h = float(indicators.get("wt1_1h", 0) or 0)
    w2_1h = float(indicators.get("wt2_1h", 0) or 0)

    _3m_against = _wt_against(w1_3m, w2_3m, is_long)
    _15m_against = _wt_against(w1_15m, w2_15m, is_long)
    _1h_against = _wt_against(w1_1h, w2_1h, is_long)

    # User-trigger predicate (live line 5126-5130) — used to BYPASS deteriorating-gain gate.
    _use_15m_or_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True))
    if _use_15m_or_1h:
        _user_trigger_active = _3m_against and (_15m_against or _1h_against)
    else:
        _user_trigger_active = (
            bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", True))
            and _3m_against and _1h_against
        )

    # ─────────────────────────────────────────────────────────────────────
    # GATE 5: HEDGE_DETERIORATING_GAIN (live line 5096-5138)
    # Strip default OFF; when ON, requires cur < prev - delta_pp UNLESS
    # _user_trigger_active bypasses.
    # ─────────────────────────────────────────────────────────────────────
    if bool(getattr(cfg, "HEDGE_DETERIORATING_GAIN_ENABLED",
                    DEFAULT_HEDGE_DETERIORATING_GAIN_ENABLED)) and not _user_trigger_active:
        # Live: safe_fetch_float(getattr(pos, 'prev_gain', pnl_pct), pnl_pct).
        # 0.0 is a VALID prev_gain (just-opened position); only None / missing
        # falls back to pnl_pct. Do NOT use `or pnl_pct` — 0 is falsy and would
        # silently flip "is deteriorating" comparisons on every brand-new position.
        _pg_raw = _attr(pos, "prev_gain", None)
        prev_gain = pnl_pct if _pg_raw is None else float(_pg_raw)
        delta_pp = float(getattr(cfg, "HEDGE_DETERIORATING_GAIN_DELTA_PP",
                                 DEFAULT_HEDGE_DETERIORATING_GAIN_DELTA_PP))
        if pnl_pct >= prev_gain - delta_pp:
            return (False, 0.0,
                    f"not_deteriorating_cur={pnl_pct:.2f}_prev={prev_gain:.2f}_drop_lt_{delta_pp:.2f}",
                    "HEDGE_DETERIORATING_GAIN")

    # ─────────────────────────────────────────────────────────────────────
    # GATE 6: HEDGE_NEWBORN_GRACE (live line 5140-5172)
    # Skip if age < grace_min minutes UNLESS DC breach.
    # ─────────────────────────────────────────────────────────────────────
    grace_min = float(getattr(cfg, "HEDGE_NEWBORN_GRACE_MINUTES",
                              DEFAULT_HEDGE_NEWBORN_GRACE_MINUTES))
    if grace_min > 0:
        opened_at = _attr(pos, "opened_at", None) or _attr(pos, "last_augmentation_time", None)
        age_s = 999999.0
        if opened_at is not None:
            try:
                if isinstance(opened_at, (int, float)):
                    age_s = now_ts - float(opened_at)
                else:
                    # ISO/datetime string fallback (live path supports this)
                    from datetime import datetime, timezone
                    if isinstance(opened_at, datetime):
                        _dt_parsed = opened_at
                    else:
                        _dt_parsed = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    if _dt_parsed.tzinfo is None:
                        _dt_parsed = _dt_parsed.replace(tzinfo=timezone.utc)
                    age_s = (datetime.now(timezone.utc) - _dt_parsed).total_seconds()
            except Exception:
                age_s = 999999.0
        if age_s < grace_min * 60.0:
            breach_ok = bool(getattr(cfg, "HEDGE_NEWBORN_DC_BREACH_ALLOWED",
                                     DEFAULT_HEDGE_NEWBORN_DC_BREACH_ALLOWED))
            dc_breached = False
            if breach_ok:
                dc_low_3m = float(indicators.get("dc_low_3m", 0) or 0)
                dc_high_3m = float(indicators.get("dc_high_3m", 0) or 0)
                if is_long and dc_low_3m > 0 and mark_price < dc_low_3m:
                    dc_breached = True
                elif (not is_long) and dc_high_3m > 0 and mark_price > dc_high_3m:
                    dc_breached = True
            if not dc_breached:
                return (False, 0.0,
                        f"newborn_age_{age_s:.0f}s_lt_{grace_min*60:.0f}s_no_dc_breach",
                        "HEDGE_NEWBORN_GRACE")

    # ─────────────────────────────────────────────────────────────────────
    # GATE 7: HEDGE_WT_TRIGGER (live line 5206-5223) — CASCADE
    # MUST match live order EXACTLY:
    #   1) HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H  (default True)
    #   2) HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H
    #   3) HEDGE_TRIGGER_USE_WT_3M_ALONE
    #   4) legacy: 15m OR (3m AND 1h)
    # ─────────────────────────────────────────────────────────────────────
    _require_3m_and_15m_or_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True))
    _require_3m_and_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", True))
    _use_3m_alone = bool(getattr(cfg, "HEDGE_TRIGGER_USE_WT_3M_ALONE", True))

    if _require_3m_and_15m_or_1h:
        wt_trigger = _3m_against and (_15m_against or _1h_against)
        trigger_label = "3m_AND_(15m_OR_1h)"
    elif _require_3m_and_1h:
        wt_trigger = _3m_against and _1h_against
        trigger_label = "3m_AND_1h"
    elif _use_3m_alone:
        wt_trigger = _3m_against
        trigger_label = "3m_alone"
    else:
        wt_trigger = _15m_against or (_3m_against and _1h_against)
        trigger_label = "15m_OR_(3m_AND_1h)"

    if not wt_trigger:
        return (False, 0.0,
                f"wt_no_trigger_mode={trigger_label}_3m={_3m_against}_15m={_15m_against}_1h={_1h_against}",
                "HEDGE_WT_TRIGGER")

    # ─────────────────────────────────────────────────────────────────────
    # Post-WT preflights — mirrors live ez_positions_quick.py:5224-5230
    # (the `_same_already` check after the WT trigger log).
    # Also: backtest-only hedge_active flag protects against re-hedging the
    # same position twice; equivalent to live's tracker child block but cleaner
    # in vec_engine where _PositionState owns its state.
    # ─────────────────────────────────────────────────────────────────────
    if hedge_side_qty > 0:
        return (False, 0.0,
                f"opposite_{hedge_side}_already_open_qty={hedge_side_qty:.4f}",
                "OPPOSITE_SIDE_OPEN")
    if bool(_attr(pos, "hedge_active", False)):
        return (False, 0.0, "position_already_hedge", "HEDGE_ALREADY_ACTIVE")

    # ─────────────────────────────────────────────────────────────────────
    # ALL 7 GATES PASSED — compute hedge qty and return fire.
    # ─────────────────────────────────────────────────────────────────────
    hss_pct = float(getattr(cfg, "HEDGE_SAME_SYMBOL_PCT", DEFAULT_HEDGE_SAME_SYMBOL_PCT))
    # Cap to [0, HEDGE_OVERSIZE_RATIO] to mirror VecConfig safety (2.0 default).
    cap = float(getattr(cfg, "HEDGE_OVERSIZE_RATIO", 2.0))
    hss_pct = max(0.0, min(hss_pct, cap))
    hedge_qty = qty * hss_pct

    # Live final notional check (line 5238)
    if hedge_qty * mark_price < DEFAULT_MIN_NOTIONAL_USD:
        return (False, 0.0,
                f"hedge_notional_{hedge_qty*mark_price:.2f}_lt_5",
                "NOTIONAL_TOO_SMALL")

    reason = (
        f"HEDGE_PROTECT_{side}_LOSS_wt_{trigger_label}"
        f"_g{pnl_pct:.2f}pct"
        f"_3m={_3m_against}_15m={_15m_against}_1h={_1h_against}"
    )
    return (True, hedge_qty, reason, None)


# ─────────────────────────────────────────────────────────────────────────────
# Vec (per-bar arrays) — backtest_v8_engine integration
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_hedge_scan_gates_vec(
    *,
    is_long: bool,
    open_arr: np.ndarray,
    entry_price_arr: np.ndarray,
    mark_price_arr: np.ndarray,
    qty_arr: np.ndarray,
    prev_gain_arr: np.ndarray,
    hedge_active_arr: np.ndarray,
    opposite_open_arr: np.ndarray,
    opened_at_arr: np.ndarray,
    last_fire_arr: np.ndarray,
    hedge_completed_arr: np.ndarray,
    symbol_hedge_active_arr: np.ndarray,
    tracker_is_hedge_arr: np.ndarray,
    wt1_3m_arr: np.ndarray,
    wt2_3m_arr: np.ndarray,
    wt1_15m_arr: np.ndarray,
    wt2_15m_arr: np.ndarray,
    wt1_1h_arr: np.ndarray,
    wt2_1h_arr: np.ndarray,
    dc_low_3m_arr: np.ndarray,
    dc_high_3m_arr: np.ndarray,
    now_ts_arr: np.ndarray,
    cfg: Any,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar vectorized hedge-scan-gate evaluator.

    Returns 3 arrays of length N (bar count):
        should_fire      — bool[N]
        hedge_qty        — float[N]
        blocked_by_gate  — int8[N]  (0=fire, 1=SYMBOL_LOCK ... 7=WT_TRIGGER,
                                     -1=GAIN_NOT_LOSS, -2=POSITION_NOT_OPEN,
                                     -3=NOTIONAL_TOO_SMALL, -4=HEDGE_ALREADY_ACTIVE,
                                     -5=OPPOSITE_OPEN, -6=MODE/HEDGE_MODE_DISABLED)

    Order of gate evaluation matches scalar exactly. We mask down progressively:
    a bar fails the first gate it would hit in the scalar path.

    Gate codes:
        0 = FIRE
        1 = HEDGE_SYMBOL_LOCK
        2 = HEDGE_SCAN_DEBOUNCE
        3 = HEDGE_COMPLETED_LOCKOUT
        4 = TRACKER_HEDGE_CONSULTATION
        5 = HEDGE_DETERIORATING_GAIN
        6 = HEDGE_NEWBORN_GRACE
        7 = HEDGE_WT_TRIGGER
    """
    n = len(open_arr)
    blocked = np.zeros(n, dtype=np.int8)        # 0 = fire
    fire = np.zeros(n, dtype=bool)
    hedge_qty_out = np.zeros(n, dtype=np.float64)

    # ── Pre-flight master switch ─────────────────────────────────────
    if not bool(getattr(cfg, "HEDGE_MODE", DEFAULT_HEDGE_MODE)):
        blocked[:] = -6
        return fire, hedge_qty_out, blocked

    # All bars start as 'still valid candidates'. As gates reject, valid is
    # whittled down. Order MUST mirror scalar exactly.
    valid = np.ones(n, dtype=bool)

    # ─── Gate 1: HEDGE_SYMBOL_LOCK (FIRST gate in live, line 5016) ───
    # Live: locked symbol blocks only when hedge_side_qty > 0. The fixture
    # supplies opposite_open_arr as proxy for hedge_side_qty>0.
    sym_locked = symbol_hedge_active_arr.astype(bool) & opposite_open_arr.astype(bool)
    g1 = valid & sym_locked
    blocked[g1] = 1
    valid = valid & ~sym_locked

    # ─── Gate 2: HEDGE_SCAN_DEBOUNCE (live line 5038) ────────────────
    debounce_secs = DEFAULT_SCAN_HEDGE_DEBOUNCE_SECONDS
    in_debounce = (last_fire_arr > 0) & ((now_ts_arr - last_fire_arr) < debounce_secs)
    g2 = valid & in_debounce
    blocked[g2] = 2
    valid = valid & ~in_debounce

    # ─── Gate 3: HEDGE_COMPLETED_LOCKOUT (live line 5042) ────────────
    lockout_secs = float(getattr(cfg, "HEDGE_COMPLETED_LOCKOUT_SECONDS",
                                 DEFAULT_HEDGE_COMPLETED_LOCKOUT_SECONDS))
    in_lockout = (hedge_completed_arr > 0) & ((now_ts_arr - hedge_completed_arr) < lockout_secs)
    g3 = valid & in_lockout
    blocked[g3] = 3
    valid = valid & ~in_lockout

    # ─── Gate 4: TRACKER_HEDGE_CONSULTATION (live line 5047) ─────────
    g4 = valid & tracker_is_hedge_arr.astype(bool)
    blocked[g4] = 4
    valid = valid & ~tracker_is_hedge_arr.astype(bool)

    # ─── Preflight qty/notional/gain (live line 5086-5095) ───────────
    pos_ok = (open_arr & (entry_price_arr > 0) & (mark_price_arr > 0) & (qty_arr > 0))
    bad_pos = valid & ~pos_ok
    blocked[bad_pos] = -2
    valid = valid & pos_ok

    notional = qty_arr * mark_price_arr
    notional_ok = notional >= DEFAULT_MIN_NOTIONAL_USD
    bad_notional = valid & ~notional_ok
    blocked[bad_notional] = -3
    valid = valid & notional_ok

    safe_entry = np.where(entry_price_arr == 0, 1.0, entry_price_arr)
    if is_long:
        pnl_pct = (mark_price_arr - entry_price_arr) / safe_entry * 100.0
    else:
        pnl_pct = (entry_price_arr - mark_price_arr) / safe_entry * 100.0

    _oh_min_loss = float(getattr(cfg, "OBLIGATORY_HEDGE_MIN_LOSS_PCT", DEFAULT_OBLIGATORY_HEDGE_MIN_LOSS_PCT))
    _hedge_all = bool(getattr(cfg, "HEDGE_ALL_POSITIONS", DEFAULT_HEDGE_ALL_POSITIONS))
    if _hedge_all:
        gain_ok = np.ones(n, dtype=bool)
    else:
        gain_ok = pnl_pct < _oh_min_loss
    bad_gain = valid & ~gain_ok
    blocked[bad_gain] = -1
    valid = valid & gain_ok

    # WT signal computation (used by both gate 5 user-trigger bypass + gate 7)
    if is_long:
        _3m_ag = wt1_3m_arr < wt2_3m_arr
        _15m_ag = wt1_15m_arr < wt2_15m_arr
        _1h_ag = wt1_1h_arr < wt2_1h_arr
    else:
        _3m_ag = wt1_3m_arr > wt2_3m_arr
        _15m_ag = wt1_15m_arr > wt2_15m_arr
        _1h_ag = wt1_1h_arr > wt2_1h_arr

    _use_15m_or_1h_pre = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True))
    if _use_15m_or_1h_pre:
        user_trigger_active = _3m_ag & (_15m_ag | _1h_ag)
    elif bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", True)):
        user_trigger_active = _3m_ag & _1h_ag
    else:
        user_trigger_active = np.zeros(n, dtype=bool)

    # ─── Gate 5: HEDGE_DETERIORATING_GAIN (live line 5096) ───────────
    if bool(getattr(cfg, "HEDGE_DETERIORATING_GAIN_ENABLED",
                    DEFAULT_HEDGE_DETERIORATING_GAIN_ENABLED)):
        delta_pp = float(getattr(cfg, "HEDGE_DETERIORATING_GAIN_DELTA_PP",
                                 DEFAULT_HEDGE_DETERIORATING_GAIN_DELTA_PP))
        not_deteriorating = (pnl_pct >= (prev_gain_arr - delta_pp)) & ~user_trigger_active
        g5 = valid & not_deteriorating
        blocked[g5] = 5
        valid = valid & ~not_deteriorating

    # ─── Gate 6: HEDGE_NEWBORN_GRACE (live line 5140) ────────────────
    grace_min = float(getattr(cfg, "HEDGE_NEWBORN_GRACE_MINUTES",
                              DEFAULT_HEDGE_NEWBORN_GRACE_MINUTES))
    if grace_min > 0:
        age_s = now_ts_arr - opened_at_arr
        age_unknown = opened_at_arr <= 0
        age_s = np.where(age_unknown, 999999.0, age_s)
        in_grace = age_s < (grace_min * 60.0)
        if bool(getattr(cfg, "HEDGE_NEWBORN_DC_BREACH_ALLOWED",
                        DEFAULT_HEDGE_NEWBORN_DC_BREACH_ALLOWED)):
            if is_long:
                dc_breach = (dc_low_3m_arr > 0) & (mark_price_arr < dc_low_3m_arr)
            else:
                dc_breach = (dc_high_3m_arr > 0) & (mark_price_arr > dc_high_3m_arr)
            grace_block = in_grace & ~dc_breach
        else:
            grace_block = in_grace
        g6 = valid & grace_block
        blocked[g6] = 6
        valid = valid & ~grace_block

    # ─── Gate 7: HEDGE_WT_TRIGGER (cascade — live line 5206-5223) ────
    _require_3m_and_15m_or_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True))
    _require_3m_and_1h = bool(getattr(cfg, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", True))
    _use_3m_alone = bool(getattr(cfg, "HEDGE_TRIGGER_USE_WT_3M_ALONE", True))

    if _require_3m_and_15m_or_1h:
        wt_trigger = _3m_ag & (_15m_ag | _1h_ag)
    elif _require_3m_and_1h:
        wt_trigger = _3m_ag & _1h_ag
    elif _use_3m_alone:
        wt_trigger = _3m_ag
    else:
        wt_trigger = _15m_ag | (_3m_ag & _1h_ag)

    g7 = valid & ~wt_trigger
    blocked[g7] = 7
    valid = valid & wt_trigger

    # ─── Post-WT: opposite-side already open + hedge_active flag ─────
    opp_open = opposite_open_arr.astype(bool)
    bad_opp = valid & opp_open
    blocked[bad_opp] = -5
    valid = valid & ~opp_open

    bad_already = valid & hedge_active_arr.astype(bool)
    blocked[bad_already] = -4
    valid = valid & ~hedge_active_arr.astype(bool)

    # ─── FIRE ────────────────────────────────────────────────────────
    hss_pct = float(getattr(cfg, "HEDGE_SAME_SYMBOL_PCT", DEFAULT_HEDGE_SAME_SYMBOL_PCT))
    cap = float(getattr(cfg, "HEDGE_OVERSIZE_RATIO", 2.0))
    hss_pct = max(0.0, min(hss_pct, cap))
    hedge_qty_candidate = qty_arr * hss_pct
    final_notional_ok = (hedge_qty_candidate * mark_price_arr) >= DEFAULT_MIN_NOTIONAL_USD
    fire_idx = valid & final_notional_ok
    fail_notional = valid & ~final_notional_ok
    blocked[fail_notional] = -3

    fire[fire_idx] = True
    hedge_qty_out[fire_idx] = hedge_qty_candidate[fire_idx]
    blocked[fire_idx] = 0

    return fire, hedge_qty_out, blocked


# Convenience name map used by tests + integration
GATE_NAMES = {
    0: "FIRE",
    1: "HEDGE_SYMBOL_LOCK",
    2: "HEDGE_SCAN_DEBOUNCE",
    3: "HEDGE_COMPLETED_LOCKOUT",
    4: "TRACKER_HEDGE_CONSULTATION",
    5: "HEDGE_DETERIORATING_GAIN",
    6: "HEDGE_NEWBORN_GRACE",
    7: "HEDGE_WT_TRIGGER",
    -1: "GAIN_NOT_LOSS",
    -2: "POSITION_NOT_OPEN",
    -3: "NOTIONAL_TOO_SMALL",
    -4: "HEDGE_ALREADY_ACTIVE",
    -5: "OPPOSITE_SIDE_OPEN",
    -6: "HEDGE_MODE_DISABLED",
}
