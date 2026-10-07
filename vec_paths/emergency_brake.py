"""EMERGENCY_BRAKE — per-hour rate limiter, scalar + vectorized.

LIVE LOGIC MIRRORED FROM: ez_manage.py:14114-14166
  - Reads data/decisions/decisions_<acct>_<YYYYMMDD>.jsonl
  - Counts within rolling 60-minute window:
      * total decisions (`_hour_total`)
      * entry-class decisions (OPEN/AUGMENT/REENTRY/QUICK_OPEN/QUICK_AUGMENT)
      * per-position-key churn count (`_sym_counts`)
  - Throttles when ANY of:
      * entries-in-window > EMERGENCY_BRAKE_MAX_TRADES_PER_HOUR  (live default 500)
      * total-in-window   > EMERGENCY_BRAKE_TOTAL_TRADES_PER_HOUR (live default 1000)
      * position_key churn > EMERGENCY_BRAKE_SYMBOL_CHURN_THRESHOLD (live default 50)
  - Profitable closes bypass; losing closes still throttled.

WHY THIS EXISTS:
  2026-03-29 runaway hedge loop fired 15,378 rogue orders in one day. The brake
  catches that. It is INTENTIONALLY NOT in the REENTRY/HEDGE-never-fail bypass
  scope — runaway-brakes stay enforced even when other gates are loosened.

REASON CODES returned to the caller (match live BLOCKED_* strings):
    "OK"
    "DISABLED"
    "EMERGENCY_BRAKE_MAX_ENTRIES"      (entries/hour cap)
    "EMERGENCY_BRAKE_MAX_TRADES"       (total trades/hour cap)
    "EMERGENCY_BRAKE_SYMBOL_CHURN"     (per-position-key churn cap)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


# ─── Reason-code constants ─────────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DISABLED = "DISABLED"
REASON_MAX_ENTRIES = "EMERGENCY_BRAKE_MAX_ENTRIES"
REASON_MAX_TRADES = "EMERGENCY_BRAKE_MAX_TRADES"
REASON_SYMBOL_CHURN = "EMERGENCY_BRAKE_SYMBOL_CHURN"

# Numeric codes for the _vec output (np.int8 array)
CODE_OK = 0
CODE_DISABLED = 1
CODE_MAX_ENTRIES = 2
CODE_MAX_TRADES = 3
CODE_SYMBOL_CHURN = 4

REASON_BY_CODE: Dict[int, str] = {
    CODE_OK: REASON_OK,
    CODE_DISABLED: REASON_DISABLED,
    CODE_MAX_ENTRIES: REASON_MAX_ENTRIES,
    CODE_MAX_TRADES: REASON_MAX_TRADES,
    CODE_SYMBOL_CHURN: REASON_SYMBOL_CHURN,
}

# ─── Default thresholds (match live hardcoded values) ──────────────────────────
DEFAULT_ENABLED = True
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_MAX_TRADES_PER_HOUR = 500       # entry-class events/hr (live: > 500)
DEFAULT_TOTAL_TRADES_PER_HOUR = 1000    # total decisions/hr  (live: > 1000)
DEFAULT_SYMBOL_CHURN_THRESHOLD = 50     # per-pk events/hr    (live: > 50)

# Entry-class actions (match live `_da in (...)` block)
ENTRY_ACTIONS = frozenset({"OPEN", "AUGMENT", "REENTRY", "QUICK_OPEN", "QUICK_AUGMENT"})
CLOSE_ACTIONS = frozenset({"CLOSE", "QUICK_CLOSE", "REDUCE", "PARTIAL_CLOSE", "STRONG_REDUCE"})


# ════════════════════════════════════════════════════════════════════════════════
# Config helper — read knobs off a config module / dict / None
# ════════════════════════════════════════════════════════════════════════════════

def _cfg_get(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _resolve_thresholds(cfg: Any) -> Tuple[bool, int, int, int, int]:
    """Returns (enabled, window_minutes, max_entries, max_total, churn)."""
    enabled = bool(_cfg_get(cfg, "EMERGENCY_BRAKE_ENABLED", DEFAULT_ENABLED))
    window_m = int(_cfg_get(cfg, "EMERGENCY_BRAKE_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES))
    max_entries = int(_cfg_get(cfg, "EMERGENCY_BRAKE_MAX_TRADES_PER_HOUR", DEFAULT_MAX_TRADES_PER_HOUR))
    max_total = int(_cfg_get(cfg, "EMERGENCY_BRAKE_TOTAL_TRADES_PER_HOUR", DEFAULT_TOTAL_TRADES_PER_HOUR))
    churn = int(_cfg_get(cfg, "EMERGENCY_BRAKE_SYMBOL_CHURN_THRESHOLD", DEFAULT_SYMBOL_CHURN_THRESHOLD))
    return enabled, window_m, max_entries, max_total, churn


# ════════════════════════════════════════════════════════════════════════════════
# Decision-event loader — mirrors live tail-500KB read
# ════════════════════════════════════════════════════════════════════════════════

def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def load_decision_events(
    decisions_dir_path: str,
    account_key: str,
    current_ts: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    tail_bytes: int = 500_000,
) -> List[Dict[str, Any]]:
    """Load decision events from JSONL within the rolling window ending at current_ts.
    Mirrors live tail-read behavior (~500KB tail) but accepts a smaller file fully.
    Returns [{'timestamp': dt, 'action': str, 'position_key': str}, ...]."""
    cutoff = current_ts - timedelta(minutes=window_minutes)
    date_str = current_ts.strftime("%Y%m%d")
    dfile = Path(decisions_dir_path) / f"decisions_{account_key}_{date_str}.jsonl"
    events: List[Dict[str, Any]] = []
    try:
        with open(dfile, errors="replace") as df:
            df.seek(0, 2)
            fsize = df.tell()
            df.seek(max(0, fsize - tail_bytes))
            if fsize > tail_bytes:
                df.readline()  # drop partial leading line
            for line in df:
                try:
                    dd = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(dd.get("timestamp", ""))
                if ts is None or ts < cutoff or ts > current_ts:
                    continue
                events.append({
                    "timestamp": ts,
                    "action": str(dd.get("action", "")).upper(),
                    "position_key": str(dd.get("position_key", "")),
                })
    except FileNotFoundError:
        pass
    return events


# ════════════════════════════════════════════════════════════════════════════════
# _core — single-call decision (live or backtest one-shot)
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_emergency_brake_core(
    account_key: str,
    current_ts: datetime,
    position_key: str,
    action: str,
    *,
    is_profitable_close: bool = False,
    config: Any = None,
    decisions_dir_path: Optional[str] = None,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str]:
    """Decide whether the next order attempt is throttled.

    Args:
        account_key:        e.g. 'ang','inf','trb' — used to pick decisions file.
        current_ts:         tz-aware UTC datetime of the prospective attempt.
        position_key:       e.g. 'BTC_LONG' — used for per-pk churn check.
        action:             prospective action (OPEN/AUGMENT/CLOSE/...).
        is_profitable_close: True when action is CLOSE-class AND gain > 0.1%.
                            Profitable closes bypass entirely (live semantics).
        config:             config module / dict / None.
        decisions_dir_path: directory containing decisions_<acct>_<YYYYMMDD>.jsonl.
        events:             pre-loaded list of decision events (used by _vec).
                            Overrides decisions_dir_path when provided.

    Returns:
        (throttled, reason_code)
        throttled is True only when the brake fires.
    """
    enabled, window_m, max_entries, max_total, churn_thr = _resolve_thresholds(config)
    if not enabled:
        return False, REASON_DISABLED

    # Profitable closes ALWAYS execute (live semantics).
    if is_profitable_close:
        return False, REASON_OK

    if events is None:
        if decisions_dir_path is None:
            # No data source = can't throttle. Treat as OK (fail-open, same as live
            # FileNotFoundError branch).
            return False, REASON_OK
        events = load_decision_events(decisions_dir_path, account_key, current_ts, window_m)

    # Filter to the rolling window (events list may be pre-window-filtered or not)
    cutoff = current_ts - timedelta(minutes=window_m)
    hour_total = 0
    hour_entries = 0
    sym_counts: Dict[str, int] = {}
    for ev in events:
        ts = ev.get("timestamp")
        if ts is None or ts < cutoff or ts > current_ts:
            continue
        hour_total += 1
        if ev.get("action", "") in ENTRY_ACTIONS:
            hour_entries += 1
        pk = ev.get("position_key", "")
        sym_counts[pk] = sym_counts.get(pk, 0) + 1

    # Live order: entries cap, then total cap, then per-pk churn.
    if hour_entries > max_entries:
        return True, REASON_MAX_ENTRIES
    if hour_total > max_total:
        return True, REASON_MAX_TRADES
    if sym_counts.get(position_key, 0) > churn_thr:
        return True, REASON_SYMBOL_CHURN

    return False, REASON_OK


# ════════════════════════════════════════════════════════════════════════════════
# _vec — precompute per-bar throttle state for whole sim window in O(N log N)
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_emergency_brake_vec(
    entry_timestamps: np.ndarray,
    symbols: np.ndarray,
    entry_attempted: Optional[np.ndarray] = None,
    actions: Optional[np.ndarray] = None,
    *,
    config: Any = None,
    window_minutes: Optional[int] = None,
    max_trades_per_hour: Optional[int] = None,
    total_trades_per_hour: Optional[int] = None,
    symbol_churn_threshold: Optional[int] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized precompute of the brake state for an entire sim window.

    Args:
        entry_timestamps: shape (N,) int64 epoch seconds (or numpy datetime64[s]).
                          Treated as the timestamps of recorded decision events,
                          ordered ascending. The output is the brake state EVALUATED
                          AT EACH of these timestamps — i.e. for an attempt at
                          entry_timestamps[i] looking back over the prior window.
                          MUST be sorted ascending (assertion).
        symbols:          shape (N,) position_key strings (e.g. 'BTC_LONG').
        entry_attempted:  shape (N,) bool — True when the event is entry-class
                          (OPEN/AUGMENT/REENTRY/QUICK_*). If None, derived from
                          `actions` array. Required for the entries-per-hour cap.
        actions:          shape (N,) action strings. Only used to derive
                          entry_attempted when that's not supplied.

    Returns:
        (throttled, reason_code)
        Both shape (N,) — throttled is bool, reason_code is int8 (see CODE_*).
        Element i is the brake decision when an attempt is made AT entry_timestamps[i].
    """
    enabled, window_m, max_entries, max_total, churn_thr = _resolve_thresholds(config)
    # Allow keyword overrides
    if window_minutes is not None:
        window_m = int(window_minutes)
    if max_trades_per_hour is not None:
        max_entries = int(max_trades_per_hour)
    if total_trades_per_hour is not None:
        max_total = int(total_trades_per_hour)
    if symbol_churn_threshold is not None:
        churn_thr = int(symbol_churn_threshold)

    n = int(entry_timestamps.shape[0])
    throttled = np.zeros(n, dtype=bool)
    reason = np.zeros(n, dtype=np.int8)  # CODE_OK = 0

    if not enabled:
        reason[:] = CODE_DISABLED
        return throttled, reason
    if n == 0:
        return throttled, reason

    # Normalize timestamps to int64 epoch seconds
    if entry_timestamps.dtype.kind == "M":
        ts = entry_timestamps.astype("datetime64[s]").astype(np.int64)
    else:
        ts = entry_timestamps.astype(np.int64, copy=False)

    # Assert sorted (cheap O(N))
    if n > 1 and not (ts[1:] >= ts[:-1]).all():
        order = np.argsort(ts, kind="stable")
        ts = ts[order]
        symbols = np.asarray(symbols)[order]
        if entry_attempted is not None:
            entry_attempted = np.asarray(entry_attempted)[order]
        if actions is not None:
            actions = np.asarray(actions)[order]

    if entry_attempted is None:
        if actions is None:
            entry_attempted = np.zeros(n, dtype=bool)
        else:
            entry_attempted = np.isin(np.asarray(actions), tuple(ENTRY_ACTIONS))
    entry_attempted = entry_attempted.astype(bool, copy=False)

    window_s = int(window_m) * 60

    # left_idx[i] = first index j such that ts[j] > ts[i] - window_s, i.e. start of
    # the rolling window for the attempt at ts[i] (events strictly within the past
    # `window_s` seconds, INCLUDING ts[i] itself — mirrors live behavior where the
    # current event isn't yet written so the count is from prior events. We compute
    # both flavors and the test pins the convention.)
    # Convention: window is (ts[i] - window_s, ts[i]] — inclusive of current event.
    cutoffs = ts - window_s
    left_idx = np.searchsorted(ts, cutoffs, side="right")
    # Total trades in window ending at i (inclusive): i - left_idx[i] + 1
    counts_total = np.arange(n, dtype=np.int64) - left_idx + 1

    # Cumulative entries → entries-in-window via diff
    cum_entries = np.concatenate(([0], np.cumsum(entry_attempted.astype(np.int64))))
    counts_entries = cum_entries[np.arange(1, n + 1)] - cum_entries[left_idx]

    # Per-position-key churn — group by symbol then position the cumulative
    # within-group cumcount, then use np.searchsorted on group timestamps.
    sym_arr = np.asarray(symbols)
    counts_churn = np.zeros(n, dtype=np.int64)
    # Build inverse map once via np.unique
    uniq, inv = np.unique(sym_arr, return_inverse=True)
    for g in range(len(uniq)):
        mask = inv == g
        if not mask.any():
            continue
        idx_g = np.flatnonzero(mask)
        ts_g = ts[idx_g]
        # For each global i in this group, the count of group events with
        # ts_g[j] in (ts[i]-window_s, ts[i]]. Since ts_g is ascending,
        # use searchsorted on ts_g.
        # Position of i within ts_g is its rank: that's just where idx_g==i,
        # but we have a vector — use the fact that ts[idx_g] is ts_g.
        left_g = np.searchsorted(ts_g, ts[idx_g] - window_s, side="right")
        right_g = np.arange(len(idx_g), dtype=np.int64) + 1
        counts_g = right_g - left_g
        counts_churn[idx_g] = counts_g

    # Apply caps in live order: entries, total, churn. > thr (strictly greater) to
    # match the live `if _cache['entries'] > 500` semantics.
    entries_fire = counts_entries > max_entries
    total_fire = counts_total > max_total
    churn_fire = counts_churn > churn_thr

    # Live precedence: entries beats total beats churn.
    reason = np.where(entries_fire, CODE_MAX_ENTRIES,
              np.where(total_fire, CODE_MAX_TRADES,
              np.where(churn_fire, CODE_SYMBOL_CHURN, CODE_OK))).astype(np.int8)
    throttled = reason != CODE_OK
    return throttled, reason


# ════════════════════════════════════════════════════════════════════════════════
# Integration helper for backtest_v8_engine — call once at sim start, then look up
# ════════════════════════════════════════════════════════════════════════════════

class BrakeLookup:
    """O(1) lookup of brake state by (account_key, bar_index) after a one-shot
    precompute. Backtest engines build this once at sim-start, then ask
    `is_throttled(account, ts, position_key)` per simulated attempt.

    Internally indexes by sorted timestamps + per-position-key counters identical
    to the live tail-read formula.
    """

    __slots__ = ("_ts", "_sym", "_entry", "_throttled", "_reason", "_idx_by_acct")

    def __init__(self, account_events: Dict[str, List[Dict[str, Any]]], config: Any = None):
        """Precompute one brake-state vector per account.

        account_events: {account_key: [{'timestamp': datetime, 'action': str,
                                         'position_key': str}, ...] sorted asc.}
        """
        self._idx_by_acct: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        for acct, evs in account_events.items():
            if not evs:
                continue
            ts_list = []
            sym_list = []
            entry_list = []
            for ev in evs:
                t = ev["timestamp"]
                if hasattr(t, "timestamp"):
                    ts_list.append(int(t.timestamp()))
                else:
                    ts_list.append(int(t))
                sym_list.append(ev.get("position_key", ""))
                entry_list.append(ev.get("action", "") in ENTRY_ACTIONS)
            ts_arr = np.array(ts_list, dtype=np.int64)
            sym_arr = np.array(sym_list, dtype=object)
            entry_arr = np.array(entry_list, dtype=bool)
            throttled, reason = evaluate_emergency_brake_vec(
                ts_arr, sym_arr, entry_attempted=entry_arr, config=config,
            )
            self._idx_by_acct[acct] = (ts_arr, sym_arr, entry_arr, throttled, reason)

    def is_throttled(
        self,
        account_key: str,
        current_ts: datetime,
        position_key: str,
        action: str = "OPEN",
        is_profitable_close: bool = False,
        config: Any = None,
    ) -> Tuple[bool, str]:
        """Per-attempt lookup. Recomputes the brake at current_ts from cached events
        (cheap: bisect into existing arrays). Mirrors evaluate_emergency_brake_core
        but reuses the precomputed event arrays."""
        enabled, window_m, max_entries, max_total, churn_thr = _resolve_thresholds(config)
        if not enabled:
            return False, REASON_DISABLED
        if is_profitable_close:
            return False, REASON_OK
        idx = self._idx_by_acct.get(account_key)
        if idx is None:
            return False, REASON_OK
        ts_arr, sym_arr, entry_arr, _throttled, _reason = idx
        cur_s = int(current_ts.timestamp()) if hasattr(current_ts, "timestamp") else int(current_ts)
        window_s = int(window_m) * 60
        left = int(np.searchsorted(ts_arr, cur_s - window_s, side="right"))
        right = int(np.searchsorted(ts_arr, cur_s, side="right"))
        hour_total = right - left
        if hour_total <= 0:
            return False, REASON_OK
        hour_entries = int(entry_arr[left:right].sum())
        sym_count = int((sym_arr[left:right] == position_key).sum())
        if hour_entries > max_entries:
            return True, REASON_MAX_ENTRIES
        if hour_total > max_total:
            return True, REASON_MAX_TRADES
        if sym_count > churn_thr:
            return True, REASON_SYMBOL_CHURN
        return False, REASON_OK
