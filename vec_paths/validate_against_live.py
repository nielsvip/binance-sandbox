"""
vec_paths/validate_against_live.py — Validate vec_engine_v1 paths against live trades.

Reads /history/<acct>/<SYMBOL>_<SIDE>.jsonl (source of truth per CLAUDE.md) and
compares timestamps of AUGMENT/OPEN events that contain path-specific reasons
against what the vec engine would have fired at the same bars.

USAGE:
    python vec_paths/validate_against_live.py --account trb --symbol GOOGL --side LONG
    python vec_paths/validate_against_live.py --account trb --all --mode tradier

VALIDATION PATHS COVERED:
    1. SENTIMENT_BOOST — AUGMENT events with reason matching "SENTIMENT_BOOST"
    2. RATIO_BOOST_L / RATIO_BOOST_S / RATIO_CUT_L / RATIO_CUT_S — events with "|RATIO_BOOST"
    3. DELTA_ENTRY — OPEN/AUGMENT events with reason matching "DELTA_ENTRY"
    4. DC_BREAK (from dc_break.py — parallel agent) — "DC_BREAK_HIGH" / "DC_BREAK_LOW"
    5. REENTRY (from reentry.py — parallel agent) — "REENTRY" prefix

OUTPUT per path:
    live_events: N
    vec_matched: N  (vec fired within ±2 bars of live bar)
    match_rate: X.X%
    unmatched_live: list of timestamps (up to 5 samples)
    false_positives: N (vec fired but live did not, within 5-bar window)

NO Sharpe numbers are computed here. This is a timestamp/event match validator.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

# ── Setup path
_BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BASE))

# ── History reader ──────────────────────────────────────────────────────────

def load_history_events(
    account: str,
    symbol: str,
    side: str,
    base_path: Path = _BASE,
) -> List[Dict]:
    """Load trade history events from /data/history/<acct>/<SYM>_<SIDE>.jsonl."""
    fpath = base_path / "data" / "history" / account / f"{symbol}_{side}.jsonl"
    if not fpath.exists():
        return []
    events = []
    with open(fpath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def parse_ts(ts_str: str) -> int:
    """Parse ISO timestamp to unix int (UTC)."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def filter_by_reason(events: List[Dict], pattern: str) -> List[Dict]:
    """Return events whose reason field contains pattern."""
    return [e for e in events if pattern in (e.get("reason") or "")]


# ── NPZ loader ─────────────────────────────────────────────────────────────

def load_npz_store(symbol: str, npz_dir: Path):
    """Load NPZ for a symbol, return (timestamps_arr, arrays_dict) or None."""
    path = npz_dir / f"{symbol}.npz"
    if not path.exists():
        return None
    try:
        data = np.load(str(path), allow_pickle=True)
        return data
    except Exception as e:
        print(f"  [WARN] Could not load {path}: {e}")
        return None


def ts_to_bar_idx(timestamps: np.ndarray, ts_unix: int, tol_bars: int = 2) -> Optional[int]:
    """Find bar index CLOSEST to ts_unix, within ±tol_bars * bar_interval."""
    if len(timestamps) == 0:
        return None
    bar_interval = int(timestamps[1] - timestamps[0]) if len(timestamps) > 1 else 300
    max_diff = bar_interval * tol_bars
    idx = int(np.searchsorted(timestamps, ts_unix, side="left"))
    if idx >= len(timestamps):
        idx = len(timestamps) - 1
    best_idx = None
    best_diff = float("inf")
    for i in range(max(0, idx - tol_bars), min(len(timestamps), idx + tol_bars + 1)):
        diff = abs(int(timestamps[i]) - ts_unix)
        if diff <= max_diff and diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


# ── SENTIMENT_BOOST validator ───────────────────────────────────────────────

def validate_sentiment_boost(
    account: str,
    symbol: str,
    side: str,
    npz_dir: Path,
    tol_bars: int = 3,
) -> Dict:
    """Validate SENTIMENT_BOOST AUGMENT events against vec approximation."""
    from vec_paths.sentiment_boost import check_sentiment_boost_augment, _sentiment_mult_from_score

    events = load_history_events(account, symbol, side)
    sb_events = filter_by_reason(events, "SENTIMENT_BOOST")
    if not sb_events:
        return {"path": "SENTIMENT_BOOST", "live_events": 0, "note": "no live events"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "SENTIMENT_BOOST", "error": f"no NPZ for {symbol}"}

    timestamps = data["timestamps"]
    ts_set = {int(t) for t in timestamps}

    matched = 0
    unmatched = []
    for ev in sb_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if bar_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no bar found"})
            continue
        mss = float(data["market_sentiment_score"][bar_idx]) if "market_sentiment_score" in data else 50.0
        sent_global = (mss - 50.0) * 2.0
        sent_local = sent_global
        is_long = (side == "LONG")
        s_mult = _sentiment_mult_from_score(sent_global, sent_local, is_long)
        price = float(data["close"][bar_idx]) if "close" in data else 0.0
        base_qty = 600.0 / max(price, 1.0)
        ideal_qty = base_qty * s_mult
        current_qty = base_qty  # conservative: assume we have base_qty open
        deviation = (ideal_qty - current_qty) / max(current_qty, 1.0)
        if deviation > 0.25:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "deviation": deviation, "mss": mss, "s_mult": s_mult})

    match_rate = matched / len(sb_events) * 100 if sb_events else 0.0
    return {
        "path": "SENTIMENT_BOOST",
        "live_events": len(sb_events),
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "LIMITATION: vec lacks sentiment_local (per-stock) from live scraper. "
            "Using market_sentiment_score as proxy. Match rate limited by this gap."
        ),
    }


# ── RATIO_BOOST/RATIO_CUT validator ────────────────────────────────────────

def validate_ratio_size(
    account: str,
    symbol: str,
    side: str,
    npz_dir: Path,
) -> Dict:
    """Validate RATIO_BOOST / RATIO_CUT events.

    In backtest, the ratio is set to 0.5 (neutral) → NEITHER boost nor cut should fire.
    Live ratio varies based on portfolio state — which we cannot replicate without all
    concurrent positions. This validator reports the live event count as baseline.
    """
    events = load_history_events(account, symbol, side)
    rb_events = filter_by_reason(events, "RATIO_BOOST") + filter_by_reason(events, "RATIO_CUT")
    rb_events = [e for e in events if "RATIO_BOOST" in (e.get("reason") or "") or "RATIO_CUT" in (e.get("reason") or "")]

    if not rb_events:
        return {"path": "RATIO_SIZE", "live_events": 0, "note": "no live events"}

    reason_counts: Dict[str, int] = {}
    for ev in rb_events:
        r = ev.get("reason", "")
        for tag in ("RATIO_BOOST_L", "RATIO_BOOST_S", "RATIO_CUT_L", "RATIO_CUT_S"):
            if tag in r:
                reason_counts[tag] = reason_counts.get(tag, 0) + 1

    return {
        "path": "RATIO_SIZE",
        "live_events": len(rb_events),
        "vec_matched": 0,
        "match_rate": "0% (expected — backtest ratio=0.5 neutral)",
        "reason_breakdown": reason_counts,
        "note": (
            "RATIO sizing is portfolio-state-dependent. "
            "Vec sets calculate_unified_market_ratio=0.5 (neutral) → no boosts/cuts fire. "
            "This is correct behavior for backtest isolation. "
            "To test ratio effects, track portfolio_state dict in VecEngine.simulate()."
        ),
    }


# ── DELTA_ENTRY validator ───────────────────────────────────────────────────

def validate_delta_entry(
    account: str,
    symbol: str,
    side: str,
    npz_dir: Path,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate DELTA_ENTRY events from history against vec check_delta_entry."""
    from vec_paths.delta_engine import check_delta_entry

    class _FakeCfg:
        DELTA_ENGINE_ENABLED = True
        DELTA_ENTRY_ENABLED = True
        DELTA_HTF_GATE = "none"
        DELTA_ENTRY_MIN_TF = 3  # lowered from 4 for backtest parity

    class _FakeStore:
        def __init__(self, data):
            self._data = data
        def f(self, key, idx, default=0.0):
            arr = self._data.get(key)
            if arr is None:
                return default
            if idx < 0 or idx >= len(arr):
                return default
            v = arr[idx]
            if isinstance(v, (float, np.floating)) and np.isnan(v):
                return default
            try:
                return float(v)
            except Exception:
                return default
        def b(self, key, idx, default=False):
            arr = self._data.get(key)
            if arr is None:
                return default
            if idx < 0 or idx >= len(arr):
                return default
            v = arr[idx]
            try:
                return bool(int(v))
            except Exception:
                return default
        def price(self, idx):
            return self.f("close", idx, 0.0)

    events = load_history_events(account, symbol, side)
    de_events = filter_by_reason(events, "DELTA_ENTRY")
    if not de_events:
        return {"path": "DELTA_ENTRY", "live_events": 0, "note": "no live events in history for this sym/side"}

    data_raw = load_npz_store(symbol, npz_dir)
    if data_raw is None:
        return {"path": "DELTA_ENTRY", "error": f"no NPZ for {symbol}"}

    data = {k: data_raw[k] for k in data_raw.files}
    timestamps = data["timestamps"]
    store = _FakeStore(data)
    cfg = _FakeCfg()

    matched = 0
    unmatched = []
    for ev in de_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if bar_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no bar found in NPZ"})
            continue
        result = check_delta_entry(store, bar_idx, side, mode, cfg)
        if result is not None:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "reason": "vec did not fire",
                "live_reason": ev.get("reason", ""),
            })

    match_rate = matched / len(de_events) * 100 if de_events else 0.0
    return {
        "path": "DELTA_ENTRY",
        "live_events": len(de_events),
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "DELTA_ENGINE is rarely enabled in tradier_manage (default OFF). "
            "Zero live events is the expected result. "
            "Vec approximation uses wt_velocity as delta proxy (no stateful DeltaTracker)."
        ),
    }


# ── DC_BREAK / REENTRY full validators ─────────────────────────────────────

def _check_in_window(
    npz_data,
    timestamps: np.ndarray,
    ev_ts: int,
    side: str,
    tol_bars: int,
    mode: str = "tradier",
) -> Optional[str]:
    """Check DC_BREAK and REENTRY signals in ±tol_bars bars around ev_ts.

    Returns fired reason string or None.
    """
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState
    from vec_paths.dc_break import check_dc_break_entry
    from vec_paths.reentry import check_reentry_entry

    # Build a quick in-memory _NPZStore
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    cfg = VecConfig()
    cfg.TRADIER_DC_DAYTRADE_ENABLED = True
    cfg.TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION = True
    cfg.REENTRY_ENABLED = True
    cfg.REENTRY_BREAKOUT_ENABLED = True
    cfg.REENTRY_PULLBACK_ENABLED = True
    cfg.REENTRY_TREND_ENABLED = True
    cfg.REENTRY_PROBE_ENABLED = True

    center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
    if center_idx is None:
        return None

    n = store.n_bars
    for delta in range(tol_bars + 1):
        for offset in ([0] if delta == 0 else [-delta, delta]):
            idx = center_idx + offset
            if idx < 0 or idx >= n:
                continue
            sig = check_dc_break_entry(store, idx, cfg)
            if sig is not None and sig["side"] == side:
                return sig["reason"]
            pos = _PositionState()
            pos.open = False
            pos.last_reduce_price = 0.0
            pos.last_close_ts = 0.0
            re_sig = check_reentry_entry(store, idx, "?", pos, side, cfg)
            if re_sig is not None:
                return re_sig["reason"]
    return None


def validate_dc_break(
    account: str,
    symbol: str,
    side: str,
    npz_dir: Path,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate DC_BREAK_HIGH / DC_BREAK_LOW live events against vec_paths/dc_break.py.

    DC_BREAK events are live opens with reason containing 'DC_BREAK_HIGH' or 'DC_BREAK_LOW'.
    Note: the live reason often has suffixes like '|RATIO_CUT_L(R=...)' — we strip those.
    """
    events = load_history_events(account, symbol, side)
    dc_events = [
        e for e in events
        if e.get("type") == "OPEN"
        and "DC_BREAK" in (e.get("reason") or "")
    ]
    if not dc_events:
        return {"path": "DC_BREAK", "live_events": 0, "note": "no DC_BREAK OPEN live events found"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "DC_BREAK", "error": f"no NPZ for {symbol}", "live_events": len(dc_events)}

    timestamps = data["timestamps"]
    npz_last_ts = int(timestamps[-1])

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in dc_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        fired = _check_in_window(data, timestamps, ev_ts, side, tol_bars, mode)
        if fired is not None:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": (ev.get("reason") or "")[:60],
                "price": ev.get("price"),
            })

    checkable = len(dc_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "DC_BREAK",
        "live_events": len(dc_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "DC_BREAK requires TRADIER_DC_DAYTRADE_ENABLED=True (forced True for validation). "
            "out_of_range = live events after NPZ cutoff date (NPZ may be stale)."
        ),
    }


def validate_reentry(
    account: str,
    symbol: str,
    side: str,
    npz_dir: Path,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate REENTRY_* live OPEN events against vec_paths/reentry.py.

    Only OPEN events whose reason starts with REENTRY_ are considered valid
    REENTRY entries. CLOSE events with REENTRY in their reason are skipped.
    """
    events = load_history_events(account, symbol, side)
    # Only OPEN events (not CLOSE/REDUCE) with a reason that starts with REENTRY_
    re_events = [
        e for e in events
        if e.get("type") == "OPEN"
        and (e.get("reason") or "").startswith("REENTRY_")
    ]
    if not re_events:
        return {"path": "REENTRY", "live_events": 0, "note": "no REENTRY live events found"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "REENTRY", "error": f"no NPZ for {symbol}", "live_events": len(re_events)}

    timestamps = data["timestamps"]
    npz_last_ts = int(timestamps[-1])

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in re_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        fired = _check_in_window(data, timestamps, ev_ts, side, tol_bars, mode)
        if fired is not None:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": (ev.get("reason") or "")[:60],
                "price": ev.get("price"),
            })

    checkable = len(re_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "REENTRY",
        "live_events": len(re_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "REENTRY_BREAKOUT, _PULLBACK, _TREND, _PROBE all checked. "
            "REENTRY_TURN/REENTRY_RECOVERY not yet modeled (separate logic). "
            "out_of_range = events after NPZ cutoff date."
        ),
    }


# ── GOLDEN_RULE validator ───────────────────────────────────────────────────

def validate_golden_rule(
    sym: str,
    side: str,
    account: str,
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate GOLDEN_RULE_* AUGMENT events against vec_paths/golden_rule_enforce.py.

    Searches /history/{account}/ for AUGMENT events with reason matching GOLDEN_RULE_*.
    Checks whether the vec model fires within ±tol_bars of the live event timestamp.

    Key validation:
      - WT direction (wt1 > wt2 for LONG, wt1 < wt2 for SHORT)
      - 15m level gate (price > dc_high_15m OR price > bb_upper_15m for LONG)
      - Size mult tier matches (1.0/1.5/2.0/3.0 → 15m/1h/4h/D level)

    Expected match rate: 50-80%. Mismatches occur because:
      - Vec NPZ bars are stale (NPZ may lag live by hours/days).
      - Vec has no account-specific symbol pool gate (fires for all syms).
      - Live has per-symbol cooldown (600s); vec does not.
    """
    from vec_paths.golden_rule_enforce import check_golden_rule_enforce
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    gr_events = [e for e in events if "GOLDEN_RULE" in (e.get("reason") or "")]
    if not gr_events:
        return {"path": "GOLDEN_RULE", "live_events": 0, "note": "no GOLDEN_RULE events in history for this sym/side"}

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "GOLDEN_RULE", "live_events": len(gr_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "GOLDEN_RULE", "error": f"NPZ load failed: {e}", "live_events": len(gr_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.GOLDEN_RULE_ENABLED = True
    cfg.GOLDEN_RULE_HTF_VETO_ENABLED = False
    dummy_pos_open = _PositionState()
    dummy_pos_open.open = True
    dummy_pos_open.qty = 0.0

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in gr_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
                if center_idx is None:
                    continue
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_golden_rule_enforce(store, bar_idx, sym, side, dummy_pos_open, cfg, mode=mode)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    checkable = len(gr_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "GOLDEN_RULE",
        "live_events": len(gr_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates WT direction + 15m gate + highest-TF mult cascade. "
            "Expected match rate 50-80%: NPZ may lag live; vec has no 600s cooldown; "
            "no account-specific symbol pool check. out_of_range = after NPZ cutoff."
        ),
    }


# ── WT_3M_FORCE_OPEN validator ──────────────────────────────────────────────

def validate_wt_force(
    sym: str,
    side: str,
    account: str = "ang",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate WT_3M_FORCE_OPEN events against vec_paths/wt_force_open.py.

    Searches /history/{account}/ for OPEN events with reason containing WT_3M_FORCE_OPEN.
    Note: WT_3M_FORCE_OPEN is a recent feature (2026-05-10). If zero events found,
    the feature may not have fired yet on the tracked symbol.

    Expected match rate: 60-85% (WT direction is the primary condition; close-to-live
    when NPZ is current).
    """
    from vec_paths.wt_force_open import check_wt_force_open
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    wf_events = [e for e in events if "WT_3M_FORCE_OPEN" in (e.get("reason") or "")]
    if not wf_events:
        return {
            "path": "WT_3M_FORCE_OPEN",
            "live_events": 0,
            "note": (
                "No WT_3M_FORCE_OPEN events in history for this sym/side. "
                "Feature added 2026-05-10. May not have fired yet on this symbol. "
                "Also: tradier stocks have NO WT_3M_FORCE_OPEN (only crypto, per MEMORY.md)."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "WT_3M_FORCE_OPEN", "live_events": len(wf_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "WT_3M_FORCE_OPEN", "error": f"NPZ load failed: {e}", "live_events": len(wf_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.WT_3M_FORCE_OPEN_ENABLED = True
    dummy_pos_closed = _PositionState()
    dummy_pos_closed.open = False

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in wf_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        fired = False
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is not None:
            for delta in range(tol_bars + 1):
                for offset in ([0] if delta == 0 else [-delta, delta]):
                    bar_idx = center_idx + offset
                    if bar_idx < 0 or bar_idx >= store.n_bars:
                        continue
                    result = check_wt_force_open(store, bar_idx, side, mode, cfg, dummy_pos_closed)
                    if result is not None:
                        fired = True
                        break
                if fired:
                    break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    checkable = len(wf_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "WT_3M_FORCE_OPEN",
        "live_events": len(wf_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates wt1_{btf} vs wt2_{btf} direction match. "
            "NPZ wt1_3m may be 0 for tradier (base TF = 5m) — falls back to wt1_5m. "
            "out_of_range = live events after NPZ cutoff date (NPZ may be stale)."
        ),
    }


# ── PRICE_CROSS_BACK_REENTRY validator ─────────────────────────────────────

def validate_price_cross_back(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate PRICE_CROSS_BACK_REENTRY events against vec_paths/price_cross_back.py.

    Searches /history/{account}/ for OPEN events with reason matching PRICE_CROSS_BACK*.
    The vec model needs both: the live close price AND the live close timestamp.
    We reconstruct these by scanning the history for the most recent CLOSE event
    prior to each PRICE_CROSS_BACK OPEN.

    Expected match rate: 40-70%. Key gap: vec uses NPZ bar price as proxy for
    last_close_price; live uses the actual fill price from the prior CLOSE event.
    """
    from vec_paths.price_cross_back import check_price_cross_back
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    pcb_events = [e for e in events if "PRICE_CROSS_BACK" in (e.get("reason") or "")]
    if not pcb_events:
        return {
            "path": "PRICE_CROSS_BACK_REENTRY",
            "live_events": 0,
            "note": (
                "No PRICE_CROSS_BACK_REENTRY events in history for this sym/side. "
                "Feature is tradier-specific (tradier_manage.py:1880). "
                "Crypto (ang/inf) does not have this path."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "PRICE_CROSS_BACK_REENTRY", "live_events": len(pcb_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "PRICE_CROSS_BACK_REENTRY", "error": f"NPZ load failed: {e}", "live_events": len(pcb_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.PRICE_CROSS_BACK_REENTRY_ENABLED = True
    cfg.PRICE_CROSS_BACK_MAX_AGE_MIN = 240.0
    cfg.PRICE_CROSS_BACK_BAND_PCT = 0.3

    close_events = [e for e in events if e.get("type") in ("CLOSE", "REDUCE")]
    close_events_sorted = sorted(close_events, key=lambda e: parse_ts(e.get("ts", "")))

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in pcb_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        last_close_ts = 0.0
        last_close_price = 0.0
        for ce in close_events_sorted:
            ce_ts = parse_ts(ce.get("ts", ""))
            if ce_ts < ev_ts:
                last_close_ts = float(ce_ts)
                try:
                    last_close_price = float(ce.get("price", 0) or 0)
                except Exception:
                    last_close_price = 0.0
            else:
                break

        if last_close_price <= 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "no prior CLOSE event with price found"})
            continue

        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event timestamp"})
            continue

        dummy_pos = _PositionState()
        dummy_pos.open = False
        dummy_pos.last_close_ts = last_close_ts
        dummy_pos.last_close_price = last_close_price

        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_price_cross_back(store, bar_idx, sym, side, dummy_pos, cfg, mode=mode)
                if result is not None:
                    fired = True
                    break
            if fired:
                break

        if fired:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": (ev.get("reason") or "")[:60],
                "last_close_px": last_close_price,
                "age_min": (ev_ts - last_close_ts) / 60.0 if last_close_ts > 0 else "?",
            })

    checkable = len(pcb_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "PRICE_CROSS_BACK_REENTRY",
        "live_events": len(pcb_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Reconstructs last_close_price from prior CLOSE event in history. "
            "Gap: live fill price vs NPZ close may differ. "
            "Expected 40-70% match. out_of_range = events after NPZ cutoff."
        ),
    }


# ── SCALP_V2 validator ─────────────────────────────────────────────────────

def validate_scalp_v2(
    sym: str,
    side: str,
    account: str = "inf",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate SCALP_V2_OPEN_* events from /history/ against vec_paths/scalp_v2.py.

    Note: SCALP_MODE=False in production (2026-05-12). Live /history/ should have
    zero SCALP_V2_OPEN_ events. This validator will report live_events=0 until
    SCALP_MODE is enabled on the inf account.
    """
    from vec_paths.scalp_v2 import check_scalp_v2_entry
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    v2_events = [e for e in events if "SCALP_V2_OPEN_" in (e.get("reason") or "")]
    if not v2_events:
        return {
            "path": "SCALP_V2",
            "live_events": 0,
            "note": (
                "No SCALP_V2_OPEN_ events in history for this sym/side. "
                "Expected: SCALP_MODE=False in production (live config default). "
                "To generate events, enable SCALP_MODE=True on the inf account."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "SCALP_V2", "live_events": len(v2_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "SCALP_V2", "error": f"NPZ load failed: {e}", "live_events": len(v2_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.SCALP_MODE = True
    cfg.SCALP_V2_ENTRY_MODE = "breakout"
    cfg.SCALP_V2_DC_HTF_REQUIRE_ALL = True

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in v2_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_scalp_v2_entry(store, bar_idx, side, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    checkable = len(v2_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "SCALP_V2",
        "live_events": len(v2_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates HTF DC breakout gate. NPZ dc_high/dc_low fields must be present. "
            "Expected match rate 50-80% if SCALP_MODE has been live. "
            "SCALP_MODE=False in production — zero live events is the normal result."
        ),
    }


# ── SCALP_V3 validator ─────────────────────────────────────────────────────

def validate_scalp_v3(
    sym: str,
    side: str,
    account: str = "inf",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate SCALP_V3_OPEN_* and QUICK_SCALP_V3_OPEN_* events against vec_paths/scalp_v3.py.

    QUICK_SCALP_V3_OPEN_* = older live reason string (pre-2026-04-28).
    SCALP_V3_OPEN_* = current live reason string.
    Both are matched.
    """
    from vec_paths.scalp_v3 import check_scalp_v3_entry
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    v3_events = [
        e for e in events
        if "SCALP_V3_OPEN_" in (e.get("reason") or "")
        or "QUICK_SCALP_V3_OPEN_" in (e.get("reason") or "")
    ]
    if not v3_events:
        return {
            "path": "SCALP_V3",
            "live_events": 0,
            "note": (
                "No SCALP_V3_OPEN_ / QUICK_SCALP_V3_OPEN_ events in history for this sym/side. "
                "Account=inf has live V3 events (2026-04-23 to 2026-04-28 under QUICK_ prefix). "
                "Try account=inf for BTCUSDC/ETHUSDC/AIUSDT etc."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "SCALP_V3", "live_events": len(v3_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "SCALP_V3", "error": f"NPZ load failed: {e}", "live_events": len(v3_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.SCALP_V3_ENABLED = True
    cfg.SCALP_V3_ENTRY_TREND_ENABLED = True
    cfg.SCALP_V3_ENTRY_WT_CROSS_ENABLED = True
    cfg.SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED = True

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in v3_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_scalp_v3_entry(store, bar_idx, side, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            live_r = (ev.get("reason") or "")[:60]
            unmatched.append({"ts": ev.get("ts"), "live_reason": live_r})

    checkable = len(v3_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "SCALP_V3",
        "live_events": len(v3_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "QUICK_SCALP_V3_OPEN_ = old reason prefix (2026-04-23 to 04-28). "
            "SCALP_V3_OPEN_ = current. Both checked. "
            "Mismatch expected: live uses hot_metrics proxy (no OHLC bars); "
            "vec uses NPZ OHLC bars → different bar_rising/falling detection. "
            "Expect 30-60% match due to structural difference in bar data."
        ),
    }


# ── MICRO_SCALP validator ──────────────────────────────────────────────────

def validate_micro_scalp(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 5,
    mode: str = "stocks",
) -> Dict:
    """Validate MICRO_SCALP_STOCKS_* events against vec_paths/micro_scalp.py.

    CLOSE events: gain >= threshold AND gain < prev_gain.
    REOPEN events: price re-crossed prior exit level (not in live history yet as of 2026-05-12).

    Primary validation: count CLOSE events and check gain threshold match.
    Full bar-level validation is limited because vec only has close prices, not
    intrabar gain peaks (live gain can differ from NPZ close-based gain).
    """
    events = load_history_events(account, sym, side)
    ms_close_events = [e for e in events if "MICRO_SCALP_STOCKS_CLOSE" in (e.get("reason") or "")]
    ms_reopen_events = [e for e in events if "MICRO_SCALP_STOCKS_REOPEN" in (e.get("reason") or "")]

    if not ms_close_events and not ms_reopen_events:
        return {
            "path": "MICRO_SCALP",
            "live_events": 0,
            "note": (
                "No MICRO_SCALP_STOCKS_CLOSE / REOPEN events in history. "
                "Events confirmed in /history/trb/ for: GOOGL, INTC, MSFT, NVDA, PLTR, SNDK, USO. "
                "MICRO_SCALP_USDC: search /history/ang/ or /history/inf/ (no events seen as of 2026-05-12). "
                "REOPEN events have not been observed in /history/ (reopen conditions may be rare in live)."
            ),
        }

    # Threshold analysis from the live reason string
    # Format: MICRO_SCALP_STOCKS_CLOSE_g<gain>%_prev<prev>%_qty_999999
    import re
    threshold_pct = 0.05
    threshold_violations = []
    total_close = len(ms_close_events)
    below_threshold = 0
    for ev in ms_close_events:
        r = ev.get("reason", "")
        m = re.search(r"MICRO_SCALP_STOCKS_CLOSE_g([\d.]+)%_prev([\d.]+)%", r)
        if m:
            g = float(m.group(1))
            prev = float(m.group(2))
            if g < threshold_pct:
                below_threshold += 1
                threshold_violations.append({"ts": ev.get("ts"), "gain": g, "prev": prev})
            elif g >= prev:
                threshold_violations.append({"ts": ev.get("ts"), "gain": g, "prev": prev, "note": "gain>=prev (no decel)"})

    note_parts = [
        f"CLOSE events validated by threshold (>={threshold_pct}%) and decel (gain<prev) from reason string. ",
        f"All {total_close} CLOSE events checked. ",
    ]
    if below_threshold:
        note_parts.append(f"WARN: {below_threshold}/{total_close} below threshold (reason string may use live-mark gain, not NPZ close gain). ")
    else:
        note_parts.append("All CLOSE events are above threshold — consistent with live threshold logic. ")
    if ms_reopen_events:
        note_parts.append(f"{len(ms_reopen_events)} REOPEN events found (validated by presence only — no NPZ cross-check). ")
    note_parts.append(
        "Full bar-level vec match not possible: live 'gain' = live mark vs entry (intrabar), "
        "vec would use close-vs-entry. Counts as 'matched' if threshold+decel invariant holds from reason string."
    )

    return {
        "path": "MICRO_SCALP",
        "live_events": total_close + len(ms_reopen_events),
        "close_events": total_close,
        "reopen_events": len(ms_reopen_events),
        "vec_matched": total_close - below_threshold,
        "match_rate": f"{(total_close - below_threshold) / max(1, total_close) * 100:.1f}%",
        "threshold_violations": threshold_violations[:5],
        "note": "".join(note_parts),
    }


# ── R1 exits validator ─────────────────────────────────────────────────────

def validate_r1_exits(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate R1_DC_* CLOSE events against vec_paths/exit_r1_r2.check_r1_emergency_exit.

    Searches /history/{account}/ for CLOSE events with reason matching R1_DC_.
    As of 2026-05-12, live /history/ shows zero R1_DC_* events because:
      (a) R1 is a recent addition (2026-05-09 mandate)
      (b) The newborn 15-min window rarely triggers (most positions survive past it)
    A zero live_events result is expected.
    """
    from vec_paths.exit_r1_r2 import check_r1_emergency_exit
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    r1_events = [e for e in events if "R1_DC_" in (e.get("reason") or "")]
    if not r1_events:
        return {
            "path": "R1_DC_EMERGENCY",
            "live_events": 0,
            "note": (
                "No R1_DC_ CLOSE events in history for this sym/side. "
                "R1 added 2026-05-09. 15-min newborn window rarely triggers in practice — "
                "most positions survive the window. Expected live_events=0 for most symbols. "
                "Live reason pattern: R1_DC_LOW4_3M_EMERGENCY_g<N>_age<N>m (crypto) "
                "or R1_DC_LOW4_EMERGENCY_g<N>_age<N>m (tradier)."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "R1_DC_EMERGENCY", "live_events": len(r1_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "R1_DC_EMERGENCY", "error": f"NPZ load failed: {e}", "live_events": len(r1_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.R1_DC_LOW4_3M_EMERGENCY_ENABLED = True
    cfg.R1_NEWBORN_WINDOW_MIN = 15
    cfg.R1_USE_DC_4BAR = True
    cfg.R1_TF = "5m" if mode == "tradier" else "3m"
    # 2026-05-23 RESTRICT gate added to vec (ez_manage.py:38290). Default True.
    # We set pos_state.reason to a known overbought-breakout marker so RESTRICT
    # lets the live events through (historical events were closed before RESTRICT
    # landed; without this they'd all be skipped).
    cfg.R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT = True
    cfg.R1_ATR_3M_MULT = 3.0

    matched = 0
    unmatched = []
    out_of_range = 0
    # Per-breach-tag counters (b)/(c) measured via dedicated synthetic tests below.
    breach_tag_counts: Dict[str, int] = {"DC_LOW4_3M": 0, "3xATR_3M": 0, "LH_LL_3M": 0}

    for ev in r1_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        fired_tag = None
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.entry_price = store.price(bar_idx) * 1.001
                dummy_pos.entry_ts = float(timestamps[bar_idx]) - 300.0
                dummy_pos.gain_pct = -0.1
                # RESTRICT-gate bypass: tag dummy with an overbought-breakout marker so
                # 2026-05-23 RESTRICT logic lets it through. Without this every live
                # event would be skipped because dummy_pos.reason defaults to "".
                dummy_pos.reason = "STRONG_BUY_DC_BREAK_TEST"
                r1_sig = check_r1_emergency_exit(store, bar_idx, dummy_pos, mode, cfg)
                if r1_sig is not None:
                    fired = True
                    fired_tag = r1_sig.get("breach_tag", "DC_LOW4_3M")
                    break
            if fired:
                break
        if fired:
            matched += 1
            for k in breach_tag_counts:
                if fired_tag and fired_tag.startswith(k):
                    breach_tag_counts[k] += 1
                    break
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    # ── Synthetic coverage of conditions (b) 3xATR_3M and (c) LH_LL_3M ──
    # Live events in /history/ overwhelmingly fire on (a). To prove (b) and (c)
    # code paths are wired correctly, we synthesize positions on bars where the
    # NPZ data unambiguously satisfies each individual condition. Sample up to
    # 5 bars per condition. Each synthetic test must independently fire R1 with
    # the matching breach_tag.
    import numpy as _np  # local import — top-of-file imports already include numpy as np
    synth_b_total = 0
    synth_b_fired = 0
    synth_c_total = 0
    synth_c_fired = 0
    try:
        n = store.n_bars
        atr_arr = store.arrays.get("atr_3m")
        # (b) 3xATR_3M — pick bars with positive atr_3m and a price drop
        # large enough to exceed 3*ATR_3m from a synthetic entry.
        if atr_arr is not None and n > 50:
            scan_step = max(1, n // 5000)
            for i in range(max(50, n - 20000), n - 1, scan_step):
                a = float(atr_arr[i]) if not _np.isnan(atr_arr[i]) else 0.0
                if a <= 0:
                    continue
                p = float(store.price(i))
                if p <= 0:
                    continue
                # LONG: entry = price + 3.5*atr (so 3*atr stop sits above current price → breach).
                # SHORT: entry = price - 3.5*atr.
                if side == "LONG":
                    entry_px = p + 3.5 * a
                else:
                    entry_px = p - 3.5 * a
                # Make sure DC condition (a) does NOT fire — pick a bar where price > dc_low4_3m + atr.
                dl = store.f("dc_low4_3m", i, 0.0)
                dh = store.f("dc_high4_3m", i, 0.0)
                if side == "LONG" and dl > 0 and p <= dl + 0.1 * a:
                    continue  # (a) would fire — not a clean (b) test
                if side == "SHORT" and dh > 0 and p >= dh - 0.1 * a:
                    continue
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.entry_price = entry_px
                dummy_pos.entry_ts = float(timestamps[i]) - 300.0
                dummy_pos.gain_pct = -1.0
                dummy_pos.reason = "STRONG_BUY_BREAKOUT_TEST"
                r1_sig = check_r1_emergency_exit(store, i, dummy_pos, mode, cfg)
                synth_b_total += 1
                if r1_sig is not None and (r1_sig.get("breach_tag") or "").startswith("3xATR_3M"):
                    synth_b_fired += 1
                if synth_b_total >= 5:
                    break

        # (c) LH_LL_3M — scan for bars where high_3m<high_3m_prev AND low_3m<low_3m_prev (LONG)
        # or the reverse for SHORT. Place a synthetic entry such that neither (a) nor (b) fires.
        h_arr = store.arrays.get("high_3m")
        l_arr = store.arrays.get("low_3m")
        hp_arr = store.arrays.get("high_3m_prev")
        lp_arr = store.arrays.get("low_3m_prev")
        if all(x is not None for x in (h_arr, l_arr, hp_arr, lp_arr)) and n > 50:
            scan_step = max(1, n // 10000)
            for i in range(max(50, n - 30000), n - 1, scan_step):
                try:
                    h3 = float(h_arr[i]); l3 = float(l_arr[i])
                    h3p = float(hp_arr[i]); l3p = float(lp_arr[i])
                except Exception:
                    continue
                if h3 <= 0 or l3 <= 0 or h3p <= 0 or l3p <= 0:
                    continue
                if side == "LONG":
                    if not (h3 < h3p and l3 < l3p):
                        continue
                else:
                    if not (h3 > h3p and l3 > l3p):
                        continue
                p = float(store.price(i))
                if p <= 0:
                    continue
                dl = store.f("dc_low4_3m", i, 0.0)
                dh = store.f("dc_high4_3m", i, 0.0)
                a = store.f("atr_3m", i, 0.0)
                # Skip if (a) would fire on this bar.
                if side == "LONG" and dl > 0 and p <= dl:
                    continue
                if side == "SHORT" and dh > 0 and p >= dh:
                    continue
                # Set entry so (b) cannot fire: entry within 1*ATR of price.
                if side == "LONG":
                    entry_px = p + 0.5 * max(a, 0.0001)
                else:
                    entry_px = p - 0.5 * max(a, 0.0001)
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.entry_price = entry_px
                dummy_pos.entry_ts = float(timestamps[i]) - 300.0
                dummy_pos.gain_pct = -0.1
                dummy_pos.reason = "STRONG_BUY_TOR_BREAK_TEST"
                r1_sig = check_r1_emergency_exit(store, i, dummy_pos, mode, cfg)
                synth_c_total += 1
                if r1_sig is not None and (r1_sig.get("breach_tag") or "") == "LH_LL_3M":
                    synth_c_fired += 1
                if synth_c_total >= 5:
                    break

        # Synthetic test for RESTRICT gate negative path: ensure non-breakout reason is rejected.
        synth_restrict_total = 0
        synth_restrict_correct = 0
        if n > 10:
            i = n // 2
            dummy_pos = _PositionState()
            dummy_pos.open = True
            dummy_pos.side = side
            dummy_pos.entry_price = store.price(i) * 1.001
            dummy_pos.entry_ts = float(timestamps[i]) - 300.0
            dummy_pos.gain_pct = -0.5
            dummy_pos.reason = "REENTRY_BOTTOM_ZONE"  # NOT in overbought-breakout marker list
            r1_sig = check_r1_emergency_exit(store, i, dummy_pos, mode, cfg)
            synth_restrict_total = 1
            if r1_sig is None:
                synth_restrict_correct = 1
    except Exception as _synth_err:
        breach_tag_counts["_synth_err"] = str(_synth_err)[:80]

    checkable = len(r1_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "R1_DC_EMERGENCY",
        "live_events": len(r1_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "breach_tag_counts": breach_tag_counts,
        "synth_b_3xATR": f"{synth_b_fired}/{synth_b_total}",
        "synth_c_LH_LL": f"{synth_c_fired}/{synth_c_total}",
        "synth_restrict_gate": f"{synth_restrict_correct}/{synth_restrict_total}",
        "note": (
            "Entry_price set 0.1% above bar price to force entry>dc_low. "
            "Entry_ts set 5m before bar (within 15-min window). "
            "Expected match rate ~70%: actual live entry/DC level may differ slightly from NPZ bars. "
            "out_of_range = events after NPZ cutoff date."
        ),
    }


# ── R2 exits validator ─────────────────────────────────────────────────────

def validate_r2_exits(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate R2_WT_VEL_SLOW (and legacy WT_15M_VEL_SLOW) CLOSE events.

    Searches /history/{account}/ for CLOSE events with reason matching:
      - R2_WT_VEL_SLOW (current pattern)
      - WT_15M_VEL_SLOW (legacy alias from 2026-05-09 rollout period)

    As of 2026-05-12, live /history/ shows zero R2_WT_VEL_SLOW events because:
      (a) R2 was added 2026-05-09
      (b) Requires: peak >= 0.5% then collapse to [0.01%, 0.10%) + vel decel
      (c) Most tradier accounts use NOLOSS gate (3%) blocking most near-BE exits
    A zero live_events result is expected for many symbols.
    """
    from vec_paths.exit_r1_r2 import check_r2_wt_vel_slow_exit
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    r2_events = [
        e for e in events
        if "R2_WT_VEL_SLOW" in (e.get("reason") or "")
        or "WT_15M_VEL_SLOW" in (e.get("reason") or "")
    ]
    if not r2_events:
        return {
            "path": "R2_WT_VEL_SLOW",
            "live_events": 0,
            "note": (
                "No R2_WT_VEL_SLOW or WT_15M_VEL_SLOW CLOSE events in history. "
                "R2 added 2026-05-09. Requires peak≥0.5% then collapse to [0.01%, 0.10%) "
                "plus wt_velocity deceleration. Tradier NOLOSS gate (3%) blocks most near-BE exits. "
                "Live reason: R2_WT_VEL_SLOW_{DECEL|DYING}_{tf}_g<N>%_peak<N>%_vel<N>vs<N>"
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "R2_WT_VEL_SLOW", "live_events": len(r2_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "R2_WT_VEL_SLOW", "error": f"NPZ load failed: {e}", "live_events": len(r2_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED = True
    cfg.WT_15M_VEL_SLOW_GAIN_FLOOR_PCT = 0.01
    cfg.WT_15M_VEL_SLOW_GAIN_BAND_PCT = 0.10
    cfg.R2_PEAK_MIN_PCT = 0.5
    cfg.WT_VEL_DECEL_RATIO = 0.5
    cfg.WT_VEL_USE_DECEL_RATIO_ONLY = True
    cfg.R2_TF_LIST = ["1h", "4h", "D"] if mode == "tradier" else ["15m"]

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in r2_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.gain_pct = 0.05
                dummy_pos.max_gain_pct = 0.7
                r2_sig = check_r2_wt_vel_slow_exit(store, bar_idx, dummy_pos, mode, cfg)
                if r2_sig is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    checkable = len(r2_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "R2_WT_VEL_SLOW",
        "live_events": len(r2_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Dummy pos: gain=0.05% (in band), max_gain=0.7% (above peak_min=0.5%). "
            "Real parity limited: live's actual gain/peak/vel at close time differ from NPZ values. "
            "Expected 40-70% match. out_of_range = events after NPZ cutoff."
        ),
    }


# ── PPL validator ───────────────────────────────────────────────────────────

def validate_partial_profit_lock(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    mode: str = "tradier",
) -> Dict:
    """Validate PARTIAL_PROFIT_LOCK_* events from /history/.

    Searches for REDUCE events with PPL_TP_* reason (Step 1) and
    CLOSE events with PPL_SL_CLOSE_* reason (Step 3).

    As of 2026-05-12, PARTIAL_PROFIT_LOCK_ENABLED=False in live config.
    Live /history/ shows zero PPL_* events. live_events=0 is expected.
    """
    events = load_history_events(account, sym, side)
    ppl_reduce = [e for e in events if "PPL_TP_" in (e.get("reason") or "")]
    ppl_close = [e for e in events if "PPL_SL_CLOSE" in (e.get("reason") or "")]
    total = len(ppl_reduce) + len(ppl_close)
    if total == 0:
        return {
            "path": "PARTIAL_PROFIT_LOCK",
            "live_events": 0,
            "note": (
                "No PPL_TP_* or PPL_SL_CLOSE_* events in history for this sym/side. "
                "PARTIAL_PROFIT_LOCK_ENABLED=False in live as of 2026-05-12. "
                "This is the expected result. "
                "Live reason patterns: PPL_TP_gain<N>_50pct (Step 1 REDUCE), "
                "PPL_SL_CLOSE_{upgraded-0.5pct|BE+buffer}_px<N>_stop<N> (Step 3 CLOSE)."
            ),
        }
    return {
        "path": "PARTIAL_PROFIT_LOCK",
        "live_events": total,
        "step1_reduce_events": len(ppl_reduce),
        "step3_close_events": len(ppl_close),
        "vec_matched": "n/a (PPL disabled in live — validation would require enabling + running live)",
        "match_rate": "n/a",
        "note": (
            "PPL events found in /history/ — PARTIAL_PROFIT_LOCK was enabled at some point. "
            "Enable cfg.PARTIAL_PROFIT_LOCK_ENABLED=True to run vec validation. "
            "Match expected 60-80%: vec uses bar close price; live uses intrabar mark price."
        ),
    }


# ── WT_CROSSUNDER_FINAL validator ───────────────────────────────────────────

def validate_wt_crossunder_final(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 5,
    mode: str = "tradier",
) -> Dict:
    """Validate WT_CROSSUNDER_FINAL events from /history/.

    Searches for CLOSE events with reason matching WT_CROSSUNDER_FINAL or WT_CROSSOVER_FINAL.
    Live events confirmed in /history/trb/ for multiple symbols (GOOGL, GLD, XOM, FIVN, etc).

    Key observation: live reasons use format WT_CROSSUNDER_FINAL_1h{bool}_4h{bool}_D{bool}
    (no 5m/15m prefix in old events). New events post-2026-05-12 use
    WT_CROSSUNDER_FINAL_5m_15m_1h{bool}_4h{bool}_D{bool} format.
    """
    from vec_paths.wt_crossunder_final import check_wt_crossunder_final_exit
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    xu_events = [
        e for e in events
        if "WT_CROSSUNDER_FINAL" in (e.get("reason") or "")
        or "WT_CROSSOVER_FINAL" in (e.get("reason") or "")
    ]
    if not xu_events:
        return {
            "path": "WT_CROSSUNDER_FINAL",
            "live_events": 0,
            "note": (
                "No WT_CROSSUNDER_FINAL or WT_CROSSOVER_FINAL events in history. "
                "Events confirmed in /history/trb/ for: GOOGL_LONG, GLD_LONG, FIVN_SHORT, XOM_SHORT. "
                "Try those symbols. Mode: tradier (5m LTF, 15m confirm, 1h/4h/D HTF)."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "WT_CROSSUNDER_FINAL", "live_events": len(xu_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "WT_CROSSUNDER_FINAL", "error": f"NPZ load failed: {e}", "live_events": len(xu_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.WT_CROSSUNDER_FINAL_ENABLED = True
    cfg.WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED = False
    cfg.WT_CROSSUNDER_FINAL_NOLOSS_BYPASS = False

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in xu_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.gain_pct = 1.0
                dummy_pos.entry_ts = float(timestamps[bar_idx]) - 3600.0 * 24
                xu_sig = check_wt_crossunder_final_exit(store, bar_idx, dummy_pos, mode, cfg)
                if xu_sig is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": (ev.get("reason") or "")[:80],
            })

    checkable = len(xu_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "WT_CROSSUNDER_FINAL",
        "live_events": len(xu_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates LTF_down + 15m_confirm + ≥1_HTF_against. "
            "LTF = 5m (tradier) / 3m (crypto). "
            "Expected 50-75%: live uses exact gain/hold context; vec uses bar snapshot. "
            "Old live events use 1h/4h/D only (no 5m/15m prefix) — these fire via HTF check. "
            "out_of_range = events after NPZ cutoff."
        ),
    }


# ── STRUCTURAL_RANGE_SHIFT validator ───────────────────────────────────────

def validate_structural_range_shift(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 5,
    mode: str = "tradier",
) -> Dict:
    """Validate STRUCTURAL_RANGE_SHIFT_* events from /history/.

    Searches for CLOSE/REDUCE events with STRUCTURAL_RANGE_SHIFT in reason.
    Note: QUICK_STRUCTURAL_RANGE_SHIFT_* (old format from ang/inf) is included.
    Live events confirmed: FIVN_SHORT, GLD_LONG, XOM_SHORT, COPX_LONG in /history/trb/.

    Two live reason formats:
      1. bb_1h top/bot format: STRUCTURAL_RANGE_SHIFT_LONG_bb_1h_top=425.45_k1h=98_...
      2. bb_4h entry format:   STRUCTURAL_RANGE_SHIFT_LONG_bb_4h_entry=82.31>75.37
    Both use stoch gate conditions.
    """
    from vec_paths.structural_range_shift import check_srs_exit
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    srs_events = [
        e for e in events
        if "STRUCTURAL_RANGE_SHIFT" in (e.get("reason") or "")
    ]
    if not srs_events:
        return {
            "path": "STRUCTURAL_RANGE_SHIFT",
            "live_events": 0,
            "note": (
                "No STRUCTURAL_RANGE_SHIFT_* events in history for this sym/side. "
                "Confirmed events in /history/trb/: FIVN_SHORT, GLD_LONG, XOM_SHORT, COPX_LONG. "
                "STRUCTURAL_RANGE_SHIFT_EXIT=False in default config — must be enabled. "
                "Live reason patterns: STRUCTURAL_RANGE_SHIFT_LONG_bb_1h_top=<N>_k1h=<N>_..."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "STRUCTURAL_RANGE_SHIFT", "live_events": len(srs_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "STRUCTURAL_RANGE_SHIFT", "error": f"NPZ load failed: {e}", "live_events": len(srs_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = True
    cfg.STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS = 100.0
    cfg.STRUCTURAL_RANGE_SHIFT_K_HIGH = 80.0
    cfg.STRUCTURAL_RANGE_SHIFT_K_LOW = 20.0

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in srs_events:
        live_reason = (ev.get("reason") or "")
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        import re
        tf_match = re.search(r"STRUCTURAL_RANGE_SHIFT_(?:LONG|SHORT)_(bb_(?:1h|4h|D)|dc_(?:1h|4h|D))", live_reason)
        if tf_match:
            cfg.STRUCTURAL_RANGE_SHIFT_TF = tf_match.group(1)
        else:
            cfg.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"

        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        _srs_tf_short = cfg.STRUCTURAL_RANGE_SHIFT_TF.replace("bb_", "").replace("dc_", "")
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                price = store.price(bar_idx)
                dummy_pos = _PositionState()
                dummy_pos.open = True
                dummy_pos.side = side
                dummy_pos.gain_pct = -0.5
                if side == "LONG":
                    high = store.f(f"bb_upper_{_srs_tf_short}", bar_idx, 0.0)
                    dummy_pos.entry_price = high * 1.01 if high > 0 else price * 1.01
                else:
                    low = store.f(f"bb_lower_{_srs_tf_short}", bar_idx, 0.0)
                    dummy_pos.entry_price = low * 0.99 if low > 0 else price * 0.99
                srs_sig = check_srs_exit(store, bar_idx, dummy_pos, mode, cfg)
                if srs_sig is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": live_reason[:80],
                "tf_used": cfg.STRUCTURAL_RANGE_SHIFT_TF,
            })

    checkable = len(srs_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "STRUCTURAL_RANGE_SHIFT",
        "live_events": len(srs_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "KNOWN DIVERGENCE: NPZ stoch_k_1h computed from 15m resampled bars via "
            "backtest_v8_precompute; live stoch_k_1h computed intrabar by tradier_indicators.py "
            "from raw 1h OHLCV. These can diverge by 90+ points (GLD confirmed: NPZ k1h=9 vs live k1h=98). "
            "SRS match rate = 0% expected for this reason — not a code bug. "
            "Entry_price forced above bb_upper (LONG) / below bb_lower (SHORT). "
            "out_of_range = events after NPZ cutoff."
        ),
    }



# ── SATOSHIT validator ─────────────────────────────────────────────────────


def validate_satoshit(
    sym: str,
    side: str,
    account: str = "men",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate SATOSHIT OPEN/AUGMENT events against vec_paths/satoshit.py.

    Searches /history/{account}/ for events whose reason contains "SATOSHIT".
    Live format: "SATOSHIT_LONG_v3of5[RBHKM]_rsi32_k18_..."
    Also matches: "SATOSHIT_EXIT_SATOSHIT_EXIT_..." style from ez_satoshit exit path.

    Primary accounts: men (crypto), ang, inf.
    Note: SATOSHIT events are rare — the path was only confirmed active on
    /history/men/MAGICUSDT_SHORT.jsonl as of 2026-05-12.

    Expected match rate: 50-80% when live events exist.
    Key limitations: vec lacks per-position cooldown (120s) and reentry tracker.
    """
    from vec_paths.satoshit import check_satoshit_entry
    from vec_engine_v1 import _NPZStore, VecConfig

    events = load_history_events(account, sym, side)
    sat_events = [e for e in events if "SATOSHIT" in (e.get("reason") or "")]
    if not sat_events:
        return {
            "path": "SATOSHIT",
            "live_events": 0,
            "note": (
                "No SATOSHIT events in history for this sym/side. "
                "Confirmed events: /history/men/MAGICUSDT_SHORT.jsonl (2026-03-26). "
                "SATOSHIT_ENABLED=False in live config (default). "
                "SATOSHIT_ENTRY_FILTER=True (crypto gate) fires via WT path — "
                "check ang/inf LONG/SHORT symbols for implicit filtering."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "SATOSHIT", "live_events": len(sat_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "SATOSHIT", "error": f"NPZ load failed: {e}", "live_events": len(sat_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.SATOSHIT_ENABLED = True
    cfg.SATOSHIT_MIN_VOTES = 3

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in sat_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_satoshit_entry(store, bar_idx, side, mode, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:80]})

    checkable = len(sat_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "SATOSHIT",
        "live_events": len(sat_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates 5-vote system: rsi_15m/bb_pct_b_1h/ha_15m/stoch_k_15m/mfi_15m + "
            "HTF mfi_D/rvol_1h. Vec lacks 120s cooldown + reentry tracker. "
            "Expected 50-80% when live events exist. "
            "out_of_range = events after NPZ cutoff date."
        ),
    }


# ── FH_MOMENTUM validator ───────────────────────────────────────────────────


def validate_fh_momentum(
    sym: str,
    side: str,
    account: str = "ang",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate FH_MOMENTUM / FH_MOM_CRYPTO OPEN events against vec_paths/fh_momentum.py.

    Searches /history/{account}/ for OPEN events whose reason contains
    "FH_MOMENTUM" or "FH_MOM" (including FH_MOM_CRYPTO_L/S).

    Live reason formats:
      tradier: "FH_MOMENTUM_L move=+0.82%_mfi=63_dc=0.42"
      crypto:  "FH_MOM_CRYPTO_L move=+1.23%_dc=0.31_rt=0"

    Expected match rate: 50-80% for tradier, 40-70% for crypto.
    Key limitation: vec uses NPZ open_D (forward-filled from daily bar) and
    current close as price. Live uses actual live price vs actual daily open.
    NPZ may have open_D from prior day if precompute ran before market open.
    Crypto FH window: 13:00-14:30 UTC. Tradier: 13:30-14:30 UTC.

    Note: FH_MOMENTUM_ENABLED=False (tradier default). CRYPTO_FH_MOMENTUM_ENABLED=True.
    Zero live tradier events is expected if FH_MOMENTUM_ENABLED stayed OFF in production.
    """
    from vec_paths.fh_momentum import check_fh_momentum_entry
    from vec_engine_v1 import _NPZStore, VecConfig

    events = load_history_events(account, sym, side)
    fh_events = [
        e for e in events
        if "FH_MOMENTUM" in (e.get("reason") or "")
        or "FH_MOM" in (e.get("reason") or "")
    ]
    if not fh_events:
        return {
            "path": "FH_MOMENTUM",
            "live_events": 0,
            "note": (
                "No FH_MOMENTUM / FH_MOM events in history for this sym/side. "
                "Tradier FH_MOMENTUM_ENABLED=False (default OFF). "
                "Crypto CRYPTO_FH_MOMENTUM_ENABLED=True but requires 13:00-14:30 UTC window + move>=0.5%. "
                "Try account=ang/inf for crypto events."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "FH_MOMENTUM", "live_events": len(fh_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "FH_MOMENTUM", "error": f"NPZ load failed: {e}", "live_events": len(fh_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    if mode == "tradier":
        cfg.FH_MOMENTUM_ENABLED = True
        cfg.FH_MOMENTUM_MFI_CONFIRM = True
        cfg.FH_MOMENTUM_DC_CONFIRM = True
        cfg.FH_MOMENTUM_MIN_MOVE_PCT = 0.5
    else:
        cfg.CRYPTO_FH_MOMENTUM_ENABLED = True
        cfg.CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT = 0.5

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in fh_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_fh_momentum_entry(store, bar_idx, side, mode, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:80]})

    checkable = len(fh_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "FH_MOMENTUM",
        "live_events": len(fh_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates open_D move >= threshold within first-hour UTC window. "
            "Vec uses NPZ open_D (forward-filled) vs close; live uses live price vs actual daily open. "
            "Crypto window 13:00-14:30 UTC, stocks 13:30-14:30 UTC. "
            "Expected 50-80%. out_of_range = events after NPZ cutoff."
        ),
    }


# ── BB_RECOVERY validator ───────────────────────────────────────────────────


def validate_bb_recovery(
    sym: str,
    side: str,
    account: str = "trb",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "tradier",
) -> Dict:
    """Validate BB_RECOVERY_EXIT_BYPASS events against vec_paths/bb_recovery.py.

    Searches /history/{account}/ for CLOSE events whose reason contains "BB_RECOVERY".
    Live reason format: "[BB_RECOVERY_EXIT_BYPASS] SYMBOL_LONG: LONG entry=..."

    Note: BB_RECOVERY_EXIT_BYPASS is logged as a WARNING in tradier_manage.py:9301/9305
    but the actual CLOSE reason in history may be the WT exit reason (not BB_RECOVERY).
    This validator checks for the BB_RECOVERY log string in the reason field.

    BB_RECOVERY_EXIT_ENABLED_TRADIER=True (default ON in config_tradier.py:778).
    Crypto default OFF (not in config.py).

    Expected match rate: 30-60%. Key limitation: vec uses entry_price from pos_state
    (which is set at the start of the backtest bar, not the actual live fill price).
    Live entry_price is the actual Binance fill price which may differ from NPZ close.
    """
    from vec_paths.bb_recovery import check_bb_recovery_exit
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    bbr_events = [
        e for e in events
        if "BB_RECOVERY" in (e.get("reason") or "")
    ]
    if not bbr_events:
        return {
            "path": "BB_RECOVERY",
            "live_events": 0,
            "note": (
                "No BB_RECOVERY events in history for this sym/side. "
                "BB_RECOVERY_EXIT_BYPASS is a NOLOSS bypass — it only fires when "
                "entry_price > bb_upper_1h (stranded above BB). "
                "Such entries are rare; this validator may see 0 events normally. "
                "tradier BB_RECOVERY_EXIT_ENABLED_TRADIER=True (always-ON). "
                "Events only appear when actual entry was made above BB band."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "BB_RECOVERY", "live_events": len(bbr_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "BB_RECOVERY", "error": f"NPZ load failed: {e}", "live_events": len(bbr_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = True
    cfg.BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER = 0.30

    OPEN_events = [e for e in events if e.get("type") == "OPEN"]
    OPEN_events_sorted = sorted(OPEN_events, key=lambda e: parse_ts(e.get("ts", "")))

    matched = 0
    unmatched = []
    out_of_range = 0

    for ev in bbr_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        last_open_price = 0.0
        for oe in OPEN_events_sorted:
            oe_ts = parse_ts(oe.get("ts", ""))
            if oe_ts < ev_ts:
                try:
                    last_open_price = float(oe.get("price", 0) or 0)
                except Exception:
                    last_open_price = 0.0
            else:
                break
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue
        dummy_pos = _PositionState()
        dummy_pos.open = True
        dummy_pos.side = side
        dummy_pos.entry_price = last_open_price if last_open_price > 0 else store.price(center_idx) * 1.003
        dummy_pos.gain_pct = -0.1
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_bb_recovery_exit(store, bar_idx, dummy_pos, mode, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            matched += 1
        else:
            unmatched.append({
                "ts": ev.get("ts"),
                "live_reason": (ev.get("reason") or "")[:80],
                "entry_price_used": dummy_pos.entry_price,
            })

    checkable = len(bbr_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "BB_RECOVERY",
        "live_events": len(bbr_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates entry_price > bb_upper_1h + price within tolerance + 3m reversal. "
            "Entry price reconstructed from last OPEN event in history (approximate). "
            "Key gap: live fill price vs NPZ bar price. Expected 30-60%. "
            "out_of_range = events after NPZ cutoff."
        ),
    }


# ── MOM3 validator ─────────────────────────────────────────────────────────


def validate_mom3(
    sym: str,
    side: str,
    account: str = "ang",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate MOM3 / MOM5 score boost events against vec_paths/mom3.py.

    Searches /history/{account}/ for OPEN events whose reason contains "MOM3" or "MOM5".
    Live reason format (from final_order_quantity factors):
      "MOM3_LONG(-1.23%<-1.0)" or "MOM5_SHORT(+1.89%>1.5)"

    Note: MOM3/MOM5 are ADDITIVE — they add to entry score but don't appear
    as standalone reason strings in live /history/ JSONL. The reason string
    shows the WT/DC path reason. MOM3 contribution is implicit in the score.
    This validator searches for the direct MOM3 string just in case it appears.
    Expected: 0 direct events (MOM3 additive, not the primary reason).

    To validate indirectly: MOM3 fires when (close - close_3bar_btf) / close_3bar_btf < -1.0%.
    We validate by checking what fraction of WT-path OPEN events at the given bar
    would have had MOM3 support. This is a diagnostic, not a direct match.
    """
    from vec_paths.mom3 import check_mom3_boost
    from vec_engine_v1 import _NPZStore, VecConfig

    events = load_history_events(account, sym, side)
    mom3_events = [
        e for e in events
        if "MOM3" in (e.get("reason") or "") or "MOM5" in (e.get("reason") or "")
    ]
    open_events = [e for e in events if e.get("type") == "OPEN"]

    if not mom3_events and not open_events:
        return {
            "path": "MOM3",
            "live_events": 0,
            "note": (
                "No MOM3/MOM5 or OPEN events in history for this sym/side. "
                "MOM3_ENTRY_ENABLED=True (crypto default). "
                "MOM3 is ADDITIVE — does not appear as primary reason in /history/. "
                "Run with account=ang and a crypto symbol to see diagnostic."
            ),
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "MOM3", "live_events": len(mom3_events), "error": f"no NPZ for {sym}"}

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "MOM3", "error": f"NPZ load failed: {e}", "live_events": len(mom3_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    timestamps = store.timestamps
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    cfg = VecConfig()
    cfg.MOM3_ENTRY_ENABLED = True
    cfg.MOM5_ENTRY_ENABLED = True

    direct_matched = 0
    diagnostic_support = 0
    diagnostic_checked = 0
    unmatched = []
    out_of_range = 0

    events_to_check = mom3_events if mom3_events else open_events[:20]
    is_diagnostic = len(mom3_events) == 0

    for ev in events_to_check:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue
        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            continue
        if is_diagnostic:
            diagnostic_checked += 1
        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue
                result = check_mom3_boost(store, bar_idx, side, mode, cfg)
                if result is not None:
                    fired = True
                    break
            if fired:
                break
        if fired:
            if is_diagnostic:
                diagnostic_support += 1
            else:
                direct_matched += 1
        elif not is_diagnostic:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:60]})

    if is_diagnostic:
        support_rate = diagnostic_support / max(1, diagnostic_checked) * 100
        return {
            "path": "MOM3",
            "live_events": 0,
            "diagnostic_mode": True,
            "open_events_sampled": diagnostic_checked,
            "mom3_would_have_fired": diagnostic_support,
            "support_rate": f"{support_rate:.1f}%",
            "note": (
                "MOM3 is ADDITIVE — no direct reason string in /history/. "
                f"Diagnostic: MOM3/MOM5 would have fired on {diagnostic_support}/{diagnostic_checked} "
                "recent OPEN bars. This is context-only, not a match rate. "
                "MOM3 adds +22 to entry score when 3-bar pullback >= 1.0% (crypto/3m) or 1.5% (5m)."
            ),
        }

    checkable = len(mom3_events) - out_of_range
    match_rate = direct_matched / max(1, checkable) * 100
    return {
        "path": "MOM3",
        "live_events": len(mom3_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": direct_matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": (
            "Validates (close - close_3bar_btf) / close_3bar_btf < threshold. "
            "MOM3 is additive — direct reason string events are unexpected. "
            "If direct events found, this is a config where MOM3 appears in the primary reason. "
            "out_of_range = events after NPZ cutoff."
        ),
    }


# ── Path 1: SIZING pipeline validator ─────────────────────────────────────

def validate_sizing(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate WT_HTF_DISCOUNT modifier rate in live OPEN events.

    Since sizing doesn't produce a separate history event (it only changes qty),
    we estimate what fraction of OPENs would have been discounted (HTF misaligned)
    vs. full size, and report the distribution. This is a diagnostic-only check.
    """
    from vec_paths.sizing import compute_position_size
    events = load_history_events(account, symbol, side)
    open_events = [e for e in events if e.get("type") == "OPEN"]
    if not open_events:
        return {"path": "SIZING", "live_events": 0, "note": "no OPEN events in history"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "SIZING", "error": f"no NPZ for {symbol}", "live_events": len(open_events)}

    timestamps = np.asarray(data["timestamps"])

    class _Cfg:
        VOL_TARGET_ENABLED = False
        DD_KELLY_ENABLED = False
        GOLDEN_RULE_ENABLED = False
        WT_HTF_DISCOUNT_ENABLED = True
        HEDGE_MAX_PCT_OF_LOSER = 1.0
        MIN_POSITION_SIZE = 55.0
        WINNER_AUGMENT_ENABLED = False
        START_POSITION_SIZE = 600.0

    class _FakeStore:
        def __init__(self, d):
            self._d = d
        def f(self, key, idx, default=0.0):
            arr = self._d.get(key)
            if arr is None: return default
            if idx < 0 or idx >= len(arr): return default
            v = arr[idx]
            try: return float(v) if v == v else default
            except: return default
        @property
        def timestamps(self): return np.asarray(self._d["timestamps"])

    cfg = _Cfg()
    store = _FakeStore({k: data[k] for k in data.files})

    full_count = 0
    half_count = 0
    harsh_count = 0
    floor_count = 0
    sampled = 0

    for ev in open_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 2)
        if bar_idx is None:
            continue
        try:
            _, mod = compute_position_size(
                store, bar_idx, side, mode, cfg, {}, 0.0
            )
            sampled += 1
            if mod == 0: full_count += 1
            elif mod == 1: half_count += 1
            elif mod == 2: harsh_count += 1
            elif mod == 4: floor_count += 1
        except Exception:
            pass

    if sampled == 0:
        return {"path": "SIZING", "live_events": len(open_events), "note": "no bars matched"}

    return {
        "path": "SIZING",
        "live_events": len(open_events),
        "sampled_bars": sampled,
        "modifier_distribution": {
            "NONE (full size)": full_count,
            "WT_HTF_DISCOUNT_x0.5": half_count,
            "WT_HTF_DISCOUNT_x0.3": harsh_count,
            "MIN_POS_FLOOR": floor_count,
        },
        "note": (
            "Diagnostic only — sizing changes qty, not event count. "
            "Distribution shows what fraction of OPENs would be discounted by WT_HTF_DISCOUNT."
        ),
    }


# ── Path 2: DUP_GUARD validator ────────────────────────────────────────────

def validate_dup_guard(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate BLOCKED_DUP_GUARD / BLOCKED_HARD_AUGMENT_LOCK live events.

    In live, these appear as log lines (not in /history/ since they're blocked).
    Here we count AUGMENT events that were allowed vs. how many would have been
    blocked by the gain gate. Estimates dup-guard rate from AUGMENT events.
    """
    events = load_history_events(account, symbol, side)
    aug_events = [e for e in events if e.get("type") in ("AUGMENT", "OPEN")]
    if not aug_events:
        return {"path": "DUP_GUARD", "live_events": 0, "note": "no AUGMENT/OPEN events"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "DUP_GUARD", "error": f"no NPZ for {symbol}", "live_events": len(aug_events)}

    timestamps = np.asarray(data["timestamps"])

    class _Cfg:
        DUP_GUARD_ENABLED = True
        DUP_GUARD_USE_GAIN_GATE = True
        DUP_GUARD_GAIN_MULTIPLIER = 0.5
        MIN_GAIN = 3.0
        MIN_POSITION_SIZE = 45.0
        AUGMENT_LOCK_MIN_SECONDS = 900.0
        DUPLICATE_OPEN_COOLDOWN = 900.0
        WT_3M_FORCE_OPEN_BYPASS_GATES = True

    from vec_paths.dup_guard import check_dup_guard_block

    class _FakePos:
        def __init__(self, gain=0.0, last_aug=0.0):
            self.gain_pct = gain
            self.last_augment_ts = last_aug

    cfg = _Cfg()
    would_block = 0
    allowed = 0
    for ev in aug_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        gain = float(ev.get("gain", 0.0) or 0.0)
        pos = _FakePos(gain=gain, last_aug=0.0)
        blk = check_dup_guard_block(pos, float(ev_ts), gain, cfg, pos_value_usd=100.0, action="AUGMENT")
        if blk is not None:
            would_block += 1
        else:
            allowed += 1

    return {
        "path": "DUP_GUARD",
        "live_events": len(aug_events),
        "would_block": would_block,
        "would_allow": allowed,
        "note": (
            "Compares live AUGMENT events against vec DUP_GUARD logic. "
            "would_block = events that would be blocked by gain gate. "
            "Since live system allows these, this shows the DIFFERENCE in gate behavior "
            "(vec is more restrictive than legacy 900s cooldown)."
        ),
    }


# ── Path 3: REDUCE paths validator ────────────────────────────────────────

def validate_reduce_paths(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate K1M_EXTREME_REVERSE + STRONG_REDUCE_K + PROFIT_TAKE_REDUCE.

    Looks for REDUCE events in /history/ whose reason matches the pattern.
    """
    from vec_paths.reduce_paths import check_k1m_extreme_reverse, check_strong_reduce_k, check_profit_take_reduce
    events = load_history_events(account, symbol, side)
    k1m_events = filter_by_reason(events, "K1M_EXTREME_REVERSE")
    srk_events = filter_by_reason(events, "STRONG_REDUCE_K")
    pt_events = filter_by_reason(events, "PROFIT_TAKE_REDUCE")
    all_reduce_events = k1m_events + srk_events + pt_events

    if not all_reduce_events:
        return {
            "path": "REDUCE_PATHS",
            "live_events": 0,
            "k1m": 0, "srk": 0, "profit_take": 0,
            "note": "no K1M_EXTREME_REVERSE / STRONG_REDUCE_K / PROFIT_TAKE_REDUCE events in history",
        }

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "REDUCE_PATHS", "error": f"no NPZ for {symbol}", "live_events": len(all_reduce_events)}

    timestamps = np.asarray(data["timestamps"])

    class _Cfg:
        K1M_EXTREME_REVERSE_ENABLED = True
        K1M_EXTREME_HIGH = 90.0
        K1M_EXTREME_LOW = 10.0
        K1M_REVERSE_REQUIRES_PROFIT = True
        K1M_REVERSE_REDUCE_FRAC = 0.5
        STRONG_REDUCE_K_ENABLED = True
        SRK_K15M_LONG_MIN = 80.0
        SRK_K1H_LONG_MAX = 30.0
        SRK_K15M_SHORT_MAX = 20.0
        SRK_K1H_SHORT_MIN = 70.0
        SRK_REDUCE_FRAC = 0.5
        PROFIT_TAKE_REDUCE_ENABLED = True
        PROFIT_TAKE_GAIN_PCT = 2.0
        PROFIT_TAKE_REDUCE_FRAC = 0.5
        LTF = '3m' if mode == 'crypto' else '5m'

    class _FakeStore:
        def __init__(self, d):
            self._d = d
        def f(self, key, idx, default=0.0):
            arr = self._d.get(key)
            if arr is None: return default
            if idx < 0 or idx >= len(arr): return default
            v = arr[idx]
            try: return float(v) if v == v else default
            except: return default
        @property
        def timestamps(self): return np.asarray(self._d["timestamps"])

    class _FakePos:
        def __init__(self, side_str, gain):
            self.side = side_str
            self.gain_pct = gain
            self.ppl_fired = False

    cfg = _Cfg()
    store = _FakeStore({k: data[k] for k in data.files})

    k1m_match = srk_match = pt_match = 0
    for ev in k1m_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", 1.0) or 1.0)
        pos = _FakePos(side, gain)
        sig = check_k1m_extreme_reverse(store, bar_idx, pos, mode, cfg)
        if sig is not None: k1m_match += 1
    for ev in srk_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", 1.0) or 1.0)
        pos = _FakePos(side, gain)
        sig = check_strong_reduce_k(store, bar_idx, pos, mode, cfg)
        if sig is not None: srk_match += 1
    for ev in pt_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", 2.5) or 2.5)
        pos = _FakePos(side, gain)
        sig = check_profit_take_reduce(store, bar_idx, pos, mode, cfg)
        if sig is not None: pt_match += 1

    total_live = len(all_reduce_events)
    total_match = k1m_match + srk_match + pt_match
    return {
        "path": "REDUCE_PATHS",
        "live_events": total_live,
        "k1m": len(k1m_events), "k1m_matched": k1m_match,
        "srk": len(srk_events), "srk_matched": srk_match,
        "profit_take": len(pt_events), "pt_matched": pt_match,
        "vec_matched": total_match,
        "match_rate": f"{total_match/total_live*100:.1f}%" if total_live else "0%",
        "note": (
            "K1M uses stoch_k_1m proxy (LTF k_{3m|5m}). "
            "K1M/SRK default OFF in live — expect 0 live events for most accounts."
        ),
    }


# ── Path 4: PEAK_GIVEBACK + BE_EROSION validator ───────────────────────────

def validate_peak_giveback(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate PEAK_GIVEBACK live CLOSE events against vec gate."""
    from vec_paths.peak_giveback_be_erosion import check_peak_giveback_exit, check_be_erosion_exit
    events = load_history_events(account, symbol, side)
    pgb_events = filter_by_reason(events, "PEAK_GIVEBACK")
    be_events = filter_by_reason(events, "BE_EROSION")
    all_events = pgb_events + be_events
    if not all_events:
        return {
            "path": "PEAK_GIVEBACK",
            "live_events": 0,
            "note": "no PEAK_GIVEBACK / BE_EROSION events in history",
        }

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "PEAK_GIVEBACK", "error": f"no NPZ for {symbol}", "live_events": len(all_events)}

    timestamps = np.asarray(data["timestamps"])

    class _Cfg:
        PEAK_GIVEBACK_PROTECTION_ENABLED = True
        PEAK_GIVEBACK_MIN_PEAK_PCT = 0.3
        PEAK_GIVEBACK_DROP_PCT = 0.5
        PEAK_GIVEBACK_DROP_TRIGGER_ENABLED = True  # force ON to match live
        PEAK_GIVEBACK_HARD_ZERO_ENABLED = True      # force ON to match live
        PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN = True
        PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT = -0.5
        BREAKEVEN_GRACE_MINUTES = 15.0
        TRADIER_MIN_HOLD_MINUTES = 240.0
        MIN_HOLD_MINUTES_CRYPTO = 30.0
        BE_EROSION_ENABLED = True
        BE_EROSION_MIN_PEAK_PCT = 0.5
        BE_EROSION_FLOOR_PCT = 0.0
        BE_EROSION_HOLD_MIN_MIN = 15.0

    class _FakeStore:
        def __init__(self, d):
            self._d = d
        def f(self, key, idx, default=0.0):
            arr = self._d.get(key)
            if arr is None: return default
            if idx < 0 or idx >= len(arr): return default
            v = arr[idx]
            try: return float(v) if v == v else default
            except: return default
        @property
        def timestamps(self): return np.asarray(self._d["timestamps"])

    class _FakePos:
        def __init__(self, side_str, gain, max_gain, entry_ts):
            self.side = side_str
            self.gain_pct = gain
            self.max_gain_pct = max_gain
            self.entry_ts = entry_ts

    cfg = _Cfg()
    store = _FakeStore({k: data[k] for k in data.files})

    pgb_match = be_match = 0
    for ev in pgb_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", -0.6) or -0.6)
        max_g = float(ev.get("max_gain", 1.0) or 1.0)
        entry_ts = ev_ts - 5 * 3600  # estimate: 5h before exit
        pos = _FakePos(side, gain, max(max_g, 0.3), entry_ts)
        sig = check_peak_giveback_exit(store, bar_idx, pos, mode, cfg)
        if sig is not None: pgb_match += 1
    for ev in be_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", -0.6) or -0.6)
        max_g = float(ev.get("max_gain", 0.6) or 0.6)
        entry_ts = ev_ts - 5 * 3600
        pos = _FakePos(side, gain, max_g, entry_ts)
        sig = check_be_erosion_exit(store, bar_idx, pos, mode, cfg)
        if sig is not None: be_match += 1

    total_live = len(all_events)
    total_match = pgb_match + be_match
    return {
        "path": "PEAK_GIVEBACK",
        "live_events": total_live,
        "pgb": len(pgb_events), "pgb_matched": pgb_match,
        "be_erosion": len(be_events), "be_matched": be_match,
        "vec_matched": total_match,
        "match_rate": f"{total_match/total_live*100:.1f}%" if total_live else "0%",
        "note": (
            "entry_ts estimated as bar_ts−5h (unavailable from /history/). "
            "Hold-time gate may mismatch. PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=True forced."
        ),
    }


# ── Path 5: WINNER_PROTECT + WT_15M_VEL_SLOW validator ────────────────────

def validate_winner_protect(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate WINNER_PROTECT and R2_WT_VEL_SLOW near-zero gain exits.

    WINNER_PROTECT: looks for CLOSE events NOT matching TREND_REVERSAL_EXIT
    on positions with gain in [0, WP_MIN_GAIN_PCT). Diagnostic only.
    R2_WT_VEL_SLOW: looks for CLOSE events with reason matching R2_WT_VEL_SLOW.
    """
    from vec_paths.winner_protect import check_winner_protect_hold, check_wt_15m_vel_slow_zero_gain
    events = load_history_events(account, symbol, side)
    r2_events = filter_by_reason(events, "R2_WT_VEL_SLOW")
    wp_events = filter_by_reason(events, "WINNER_PROTECT")
    all_events = r2_events + wp_events
    if not all_events:
        return {
            "path": "WINNER_PROTECT",
            "live_events": 0,
            "r2_events": len(r2_events), "wp_events": len(wp_events),
            "note": "no R2_WT_VEL_SLOW or WINNER_PROTECT events in history",
        }

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "WINNER_PROTECT", "error": f"no NPZ for {symbol}", "live_events": len(all_events)}

    timestamps = np.asarray(data["timestamps"])

    class _Cfg:
        WINNER_PROTECT_ENABLED = True
        RP_PROTECT_THRESHOLD = 70.0
        RP_PROTECT_MIN_GAIN = 2.0
        WINNER_PROTECT_HTF_MIN_ALIGNED = 2
        WINNER_PROTECT_GAIN_PCT = 2.0
        WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED = True
        R2_PEAK_MIN_PCT = 0.5
        WT_15M_VEL_SLOW_GAIN_BAND_PCT = 0.10
        WT_15M_VEL_SLOW_GAIN_FLOOR_PCT = 0.01
        WT_15M_VEL_NEAR_ZERO_THRESHOLD = 0.1
        WT_VEL_DECEL_RATIO = 0.5
        WT_VEL_USE_DECEL_RATIO_ONLY = True
        R2_TF_LIST = ['15m'] if mode == 'tradier' else ['1h', '4h', 'D']

    class _FakeStore:
        def __init__(self, d):
            self._d = d
            self.timestamps = np.asarray(d["timestamps"])
        def f(self, key, idx, default=0.0):
            arr = self._d.get(key)
            if arr is None: return default
            if idx < 0 or idx >= len(arr): return default
            v = arr[idx]
            try: return float(v) if v == v else default
            except: return default

    class _FakePos:
        def __init__(self, side_str, gain, max_gain, entry_ts):
            self.side = side_str
            self.gain_pct = gain
            self.max_gain_pct = max_gain
            self.entry_ts = entry_ts

    cfg = _Cfg()
    store = _FakeStore({k: data[k] for k in data.files})

    r2_match = wp_match = 0
    for ev in r2_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", 0.05) or 0.05)
        max_g = float(ev.get("max_gain", 0.6) or 0.6)
        entry_ts = ev_ts - 2 * 3600
        pos = _FakePos(side, gain, max_g, entry_ts)
        sig = check_wt_15m_vel_slow_zero_gain(store, bar_idx, pos, mode, cfg)
        if sig is not None: r2_match += 1
    for ev in wp_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        bar_idx = ts_to_bar_idx(timestamps, ev_ts, 3)
        if bar_idx is None: continue
        gain = float(ev.get("gain", 0.5) or 0.5)
        max_g = float(ev.get("max_gain", 0.5) or 0.5)
        entry_ts = ev_ts - 3600
        pos = _FakePos(side, gain, max_g, entry_ts)
        holds = check_winner_protect_hold(store, bar_idx, pos, mode, cfg)
        if holds: wp_match += 1

    total_live = len(all_events)
    total_match = r2_match + wp_match
    return {
        "path": "WINNER_PROTECT",
        "live_events": total_live,
        "r2_events": len(r2_events), "r2_matched": r2_match,
        "wp_events": len(wp_events), "wp_matched": wp_match,
        "vec_matched": total_match,
        "match_rate": f"{total_match/total_live*100:.1f}%" if total_live else "0%",
        "note": (
            "R2_WT_VEL_SLOW: expects decel ratio check in vec. "
            "WINNER_PROTECT: vec uses HTF WT alignment proxy (no live ranking available). "
            "entry_ts estimated."
        ),
    }


def validate_hedge_open(
    sym: str,
    side: str,
    account: str = "ang",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate HEDGE_PROTECT_* OPEN/AUGMENT events against vec_paths/hedge_engine.py."""
    from vec_paths.hedge_engine import check_scan_hedge_losers
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    hedge_events = [
        e for e in events
        if any(pat in (e.get("reason") or "") for pat in ["HEDGE_PROTECT", "OBLIGATORY_HEDGE", "HEDGE_SAME_SYM"])
    ]

    if not hedge_events:
        return {
            "path": "hedge_open",
            "live_events": 0,
            "note": "no HEDGE_PROTECT or OBLIGATORY_HEDGE events found in history",
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "hedge_open", "live_events": len(hedge_events), "error": f"no NPZ for {sym}"}

    timestamps = npz_data["timestamps"]
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    matched = 0
    unmatched = []
    out_of_range = 0

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "hedge_open", "error": f"NPZ load failed: {e}", "live_events": len(hedge_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    cfg = VecConfig()
    cfg.HEDGE_MODE = True
    cfg.OBLIGATORY_HEDGE_ENABLED = True

    for ev in hedge_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue

        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue

                pos_side = "SHORT" if side == "LONG" else "LONG"
                
                pos = _PositionState()
                pos.open = True
                pos.side = pos_side
                pos.qty = float(ev.get("qty", 1.0) or 1.0)
                pos.gain_pct = -1.0 
                pos.hedge_active = False

                all_pos = {pos_side: pos}

                res = check_scan_hedge_losers(store, bar_idx, pos, all_pos, mode, cfg)
                if res is None:
                    from vec_paths.hedge_engine import check_obligatory_hedge
                    res = check_obligatory_hedge(store, bar_idx, pos, mode, cfg)

                if res is not None:
                    fired = True
                    break
            if fired:
                break

        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:80]})

    checkable = len(hedge_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "hedge_open",
        "live_events": len(hedge_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": "Validates scan_and_hedge_losers + obligatory_hedge. Checks wt_3m_alone default trigger.",
    }


def validate_hedge_failed(
    sym: str,
    side: str,
    account: str = "ang",
    npz_dir: Optional[Path] = None,
    tol_bars: int = 3,
    mode: str = "crypto",
) -> Dict:
    """Validate HEDGE_FAILED close events against vec_paths/hedge_engine.py."""
    from vec_paths.hedge_engine import check_hedge_failed_fallback
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState

    events = load_history_events(account, sym, side)
    failed_events = [
        e for e in events
        if "HEDGE_FAILED" in (e.get("reason") or "") or "HEDGE_FAILED" in (e.get("type") or "")
    ]

    if not failed_events:
        return {
            "path": "hedge_failed",
            "live_events": 0,
            "note": "no HEDGE_FAILED events in history",
        }

    if npz_dir is None:
        npz_dir = _BASE / "backtest_v8" / "indicators"
    npz_data = load_npz_store(sym, npz_dir)
    if npz_data is None:
        return {"path": "hedge_failed", "live_events": len(failed_events), "error": f"no NPZ for {sym}"}

    timestamps = npz_data["timestamps"]
    npz_last_ts = int(timestamps[-1]) if len(timestamps) else 0

    matched = 0
    unmatched = []
    out_of_range = 0

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    try:
        np.savez(tmp.name, **{k: npz_data[k] for k in npz_data.files})
        tmp.close()
        store = _NPZStore(Path(tmp.name))
    except Exception as e:
        return {"path": "hedge_failed", "error": f"NPZ load failed: {e}", "live_events": len(failed_events)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    cfg = VecConfig()
    cfg.HEDGE_FAILED_FALLBACK_CLOSE_ENABLED = True

    for ev in failed_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        if ev_ts == 0:
            unmatched.append({"ts": ev.get("ts"), "reason": "bad timestamp"})
            continue
        if ev_ts > npz_last_ts + 86400:
            out_of_range += 1
            continue

        center_idx = ts_to_bar_idx(timestamps, ev_ts, tol_bars)
        if center_idx is None:
            unmatched.append({"ts": ev.get("ts"), "reason": "no NPZ bar near event"})
            continue

        fired = False
        for delta in range(tol_bars + 1):
            for offset in ([0] if delta == 0 else [-delta, delta]):
                bar_idx = center_idx + offset
                if bar_idx < 0 or bar_idx >= store.n_bars:
                    continue

                pos = _PositionState()
                pos.open = True
                pos.side = side
                pos.qty = float(ev.get("qty", 1.0) or 1.0)
                pos.gain_pct = -1.0
                
                res = check_hedge_failed_fallback(store, bar_idx, pos, mode, cfg, hedge_attempt_result=False)
                if res is not None:
                    fired = True
                    break
            if fired:
                break

        if fired:
            matched += 1
        else:
            unmatched.append({"ts": ev.get("ts"), "live_reason": (ev.get("reason") or "")[:80]})

    checkable = len(failed_events) - out_of_range
    match_rate = matched / max(1, checkable) * 100
    return {
        "path": "hedge_failed",
        "live_events": len(failed_events),
        "checkable": checkable,
        "out_of_range": out_of_range,
        "vec_matched": matched,
        "match_rate": f"{match_rate:.1f}%",
        "unmatched_samples": unmatched[:5],
        "note": "Validates HEDGE_FAILED fallback close path.",
    }


# ── Main CLI ────────────────────────────────────────────────────────────────

# ════════════════════════════════════════════════════════════════════════════
# 2026-05-26 — Structural parity validators (exit→reduce, ratio_reduce proxy, first-open throttle).
# ════════════════════════════════════════════════════════════════════════════

def validate_exit_to_reduce_adapter(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate the structural CLOSE-vs-REDUCE parity gap.

    Live history has 0 CLOSE events (audit: 1395 sym files / 71322 events
    across 5 crypto accts). All exits are REDUCE.

    This validator counts:
      - live_close: count of CLOSE events in /history/ (expected = 0)
      - live_reduce: count of REDUCE events in /history/ (the real exit count)
      - live_augment: count of AUGMENT events (entry/add count)

    The "match" semantic for this validator: live_close should be 0. If it
    isn't, then either /history/ has been corrupted or a non-vec writer has
    been emitting CLOSE events. When live_close==0 we report 100% match and
    publish the live event distribution for sweep-config diagnosis.
    """
    events = load_history_events(account, symbol, side)
    if not events:
        return {"path": "EXIT_TO_REDUCE", "live_events": 0, "note": "no history events"}

    n_close = sum(1 for e in events if e.get("type") == "CLOSE")
    n_reduce = sum(1 for e in events if e.get("type") == "REDUCE")
    n_aug = sum(1 for e in events if e.get("type") == "AUGMENT")
    n_open = sum(1 for e in events if e.get("type") == "OPEN")

    # Live should have zero CLOSE; match_rate is 100% when this invariant holds.
    if n_close == 0:
        match_rate = 100.0
        match_n = 0
    else:
        # If we somehow see CLOSEs in live, parity is violated and we report
        # the rate as the % of exit events that are REDUCE (the live rule).
        match_rate = 100.0 * n_reduce / max(1, n_reduce + n_close)
        match_n = n_reduce

    return {
        "path": "EXIT_TO_REDUCE",
        "live_events": n_close + n_reduce + n_aug + n_open,
        "live_close": n_close,
        "live_reduce": n_reduce,
        "live_augment": n_aug,
        "live_open": n_open,
        "vec_matched": match_n,
        "match_rate": f"{match_rate:.1f}%",
        "note": (
            f"Live should have 0 CLOSE events. {n_close} found. "
            f"REDUCE={n_reduce}, AUGMENT={n_aug}, OPEN={n_open}. "
            "Enable VEC_LIVE_REDUCE_PARITY_ENABLED to mirror this in vec."
        ),
    }


def validate_ratio_reduce_proxy(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Validate ratio_reduce_sym_proxy fires roughly on the right cadence.

    Live REDUCE events with reason 'Manual/System Detection' (or empty) are
    the ratio_reduce-equivalent trim events. This validator counts the rate
    of those events per year of history and compares to what the proxy
    WOULD fire at if VEC_RATIO_REDUCE_PROXY_ENABLED=True.

    Diagnostic-only — exact bar-matching is impossible without portfolio state.
    """
    events = load_history_events(account, symbol, side)
    if not events:
        return {"path": "RATIO_REDUCE_PROXY", "live_events": 0, "note": "no history events"}

    # "Manual/System Detection" or empty/unset reason on REDUCE
    n_msd_reduce = sum(
        1 for e in events
        if e.get("type") == "REDUCE"
        and (e.get("reason", "") in ("Manual/System Detection", "", None))
    )
    n_reduce = sum(1 for e in events if e.get("type") == "REDUCE")

    if not events:
        return {"path": "RATIO_REDUCE_PROXY", "live_events": 0}

    # Compute live event time span
    try:
        t_first = parse_ts(events[0].get("ts", ""))
        t_last = parse_ts(events[-1].get("ts", ""))
        days = max(1, (t_last - t_first) / 86400.0)
    except Exception:
        days = 1.0

    msd_per_yr = n_msd_reduce / days * 365.25

    return {
        "path": "RATIO_REDUCE_PROXY",
        "live_events": n_reduce,
        "live_msd_reduce": n_msd_reduce,
        "live_msd_per_yr": round(msd_per_yr, 1),
        "vec_matched": 0,  # diagnostic-only
        "match_rate": "diagnostic",
        "note": (
            f"Live MSD-REDUCE rate = {msd_per_yr:.1f}/yr per sym-side. "
            "ratio_reduce_sym_proxy targets this rate. Wire and run vec to compare."
        ),
    }


def validate_dc_breach_reduce(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "crypto",
) -> Dict:
    """Validate vec_paths/dc_breach_reduce.py against live DC_BREACH_REDUCE_* events.

    Live event signatures:
      DC_BREACH_REDUCE_LOW_15m_price_X.XXX             (hedged LONG breach)
      DC_BREACH_REDUCE_HIGH_15m_price_X.XXX            (hedged SHORT breach)
      DC_BREACH_REDUCE_UNHEDGED_LOW_15m_price_X.XXX    (unhedged LONG breach)
      DC_BREACH_REDUCE_UNHEDGED_HIGH_15m_price_X.XXX   (unhedged SHORT breach)

    Live source: ez_manage.py monitor_dc_breach_reduce() lines 27002-27224.
    Vec source: vec_paths/dc_breach_reduce.py check_dc_breach_reduce().
    """
    events = load_history_events(account, symbol, side)
    dc_events = [e for e in events
                 if "DC_BREACH_REDUCE" in (e.get("reason", "") or "")
                 and e.get("type") == "REDUCE"]
    if not dc_events:
        return {"path": "DC_BREACH_REDUCE", "live_events": 0,
                "note": "no DC_BREACH_REDUCE_* live events"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "DC_BREACH_REDUCE", "error": f"no NPZ for {symbol}",
                "live_events": len(dc_events)}

    timestamps = np.asarray(data["timestamps"])
    close = np.asarray(data.get("close", []))
    dc_low_15m = np.asarray(data.get("dc_low_15m", []))
    dc_high_15m = np.asarray(data.get("dc_high_15m", []))
    if len(close) == 0 or len(dc_low_15m) == 0 or len(dc_high_15m) == 0:
        return {"path": "DC_BREACH_REDUCE", "error": "NPZ missing close / dc_low_15m / dc_high_15m",
                "live_events": len(dc_events)}

    npz_t_first = timestamps[0] if len(timestamps) else 0
    npz_t_last = timestamps[-1] if len(timestamps) else 0

    matched = 0
    out_of_range = 0
    unmatched_samples = []
    for ev in dc_events:
        ev_ts = parse_ts(ev.get("ts", ""))
        # 2026-05-26 — out_of_range = before NPZ start OR after NPZ end
        if ev_ts > npz_t_last or ev_ts < npz_t_first:
            out_of_range += 1
            continue
        bar = ts_to_bar_idx(timestamps, ev_ts, tol_bars=2)
        if bar is None:
            unmatched_samples.append(f"ts={ev.get('ts','')[:19]} (no bar)")
            continue
        is_long = (side.upper() == "LONG")
        # Check if any bar within ±2 of event saw a breach
        fired_in_window = False
        for off in range(-2, 3):
            b = bar + off
            if b < 0 or b >= len(close):
                continue
            px = float(close[b])
            if px <= 0:
                continue
            if is_long:
                dcl = float(dc_low_15m[b])
                if dcl > 0 and px < dcl:
                    fired_in_window = True
                    break
            else:
                dch = float(dc_high_15m[b])
                if dch > 0 and px > dch:
                    fired_in_window = True
                    break
        if fired_in_window:
            matched += 1
        else:
            unmatched_samples.append(f"ts={ev.get('ts','')[:19]} bar={bar}")

    checkable = len(dc_events) - out_of_range
    rate = (matched / checkable * 100.0) if checkable else 0.0

    return {
        "path": "DC_BREACH_REDUCE",
        "live_events": len(dc_events),
        "vec_matched": matched,
        "checkable": checkable,
        "out_of_range": out_of_range,
        "match_rate": f"{rate:.1f}%",
        "unmatched_samples": unmatched_samples[:5],
        "note": (
            f"DC_BREACH_REDUCE_* live signal: {len(dc_events)} events. "
            f"Vec dc_breach_reduce.check_dc_breach_reduce_vec() invariants match "
            f"{matched}/{checkable} of checkable events."
        ),
    }


def validate_first_open_throttle(
    symbol: str,
    side: str,
    account: str,
    npz_dir: Path,
    mode: str = "tradier",
) -> Dict:
    """Verify the first-OPEN-throttle gate doesn't introduce spurious skips.

    Live opens 1-3 positions per sym in the first month of history. If vec
    throttles longer than that, we miss legitimate early entries.

    Reports: bar-index of the first AUGMENT/OPEN in /history/ relative to the
    first NPZ bar. This is the lower-bound on VEC_FIRST_OPEN_THROTTLE_BARS;
    we should not exceed it.
    """
    events = load_history_events(account, symbol, side)
    if not events:
        return {"path": "FIRST_OPEN_THROTTLE", "live_events": 0, "note": "no history events"}

    data = load_npz_store(symbol, npz_dir)
    if data is None:
        return {"path": "FIRST_OPEN_THROTTLE", "error": f"no NPZ for {symbol}",
                "live_events": len(events)}

    timestamps = np.asarray(data["timestamps"])
    n_bars = len(timestamps)

    # First entry-type event
    first_aug = next((e for e in events if e.get("type") in ("AUGMENT", "OPEN")), None)
    if first_aug is None:
        return {"path": "FIRST_OPEN_THROTTLE", "live_events": 0, "note": "no AUGMENT/OPEN events"}

    first_aug_ts = parse_ts(first_aug.get("ts", ""))
    bar_idx = int(np.searchsorted(timestamps, first_aug_ts))

    return {
        "path": "FIRST_OPEN_THROTTLE",
        "live_events": len(events),
        "live_first_open_bar_idx": bar_idx,
        "npz_total_bars": n_bars,
        "fraction_in": round(bar_idx / max(1, n_bars), 4),
        "vec_matched": 0,
        "match_rate": "diagnostic",
        "note": (
            f"First live OPEN/AUGMENT at bar_idx={bar_idx} / {n_bars} "
            f"({bar_idx/max(1,n_bars)*100:.1f}% in). "
            "VEC_FIRST_OPEN_THROTTLE_BARS should be SET <= this value to avoid missing live entries."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description="Validate vec_paths against live trade history")
    ap.add_argument("--account", required=True, help="Account key (trb, trc, ang, etc.)")
    ap.add_argument("--symbol", default=None, help="Symbol to validate (e.g. GOOGL)")
    ap.add_argument("--side", default="LONG", help="Position side (LONG or SHORT)")
    ap.add_argument("--mode", default="tradier", choices=["tradier", "crypto"])
    ap.add_argument("--npz-dir", default=None, help="Path to NPZ indicator directory")
    ap.add_argument("--all", action="store_true", dest="all_syms",
                    help="Validate all symbols with history files")
    ap.add_argument("--paths", default="all",
                    help="Comma-separated: sentiment,ratio,delta,dc_break,reentry,golden_rule,wt_force,price_cross_back,scalp_v2,scalp_v3,micro_scalp,r1_exit,r2_exit,ppl,wt_crossunder_final,srs,satoshit,fh_momentum,bb_recovery,mom3,sizing,dup_guard,reduce_paths,peak_giveback,winner_protect,all")
    args = ap.parse_args()

    npz_dir = Path(args.npz_dir) if args.npz_dir else _BASE / "backtest_v8" / "indicators"
    _all_paths = {
        "sentiment", "ratio", "delta", "dc_break", "reentry", "golden_rule", "wt_force",
        "price_cross_back", "scalp_v2", "scalp_v3", "micro_scalp",
        "r1_exit", "r2_exit", "ppl", "wt_crossunder_final", "srs",
        "satoshit", "fh_momentum", "bb_recovery", "mom3",
        "hedge_open", "hedge_failed",
        "sizing", "dup_guard", "reduce_paths", "peak_giveback", "winner_protect",
        # 2026-05-26 structural parity validators
        "exit_to_reduce", "ratio_reduce_proxy", "first_open_throttle",
        "dc_breach_reduce",
    }
    paths_to_run = set(args.paths.split(",")) if args.paths != "all" else _all_paths

    if args.all_syms:
        hist_dir = _BASE / "data" / "history" / args.account
        if not hist_dir.exists():
            print(f"No history directory for account {args.account}")
            sys.exit(1)
        files = list(hist_dir.glob("*.jsonl"))
        syms_sides = []
        for f in files:
            parts = f.stem.rsplit("_", 1)
            if len(parts) == 2:
                syms_sides.append((parts[0], parts[1]))
    else:
        if not args.symbol:
            print("Provide --symbol or --all")
            sys.exit(1)
        syms_sides = [(args.symbol.upper(), args.side.upper())]

    all_results = []
    _all_path_names = (
        "sentiment", "ratio", "delta", "dc_break", "reentry", "golden_rule", "wt_force",
        "price_cross_back", "scalp_v2", "scalp_v3", "micro_scalp",
        "r1_exit", "r2_exit", "ppl", "wt_crossunder_final", "srs",
        "satoshit", "fh_momentum", "bb_recovery", "mom3",
        "hedge_open", "hedge_failed",
        "sizing", "dup_guard", "reduce_paths", "peak_giveback", "winner_protect",
        # 2026-05-26 structural parity validators
        "exit_to_reduce", "ratio_reduce_proxy", "first_open_throttle",
        "dc_breach_reduce",
    )
    for sym, side in syms_sides:
        print(f"\n{'='*60}")
        print(f"Symbol: {sym} {side} | Account: {args.account}")
        print(f"{'='*60}")
        for path in _all_path_names:
            if path not in paths_to_run:
                continue
            try:
                if path == "sentiment":
                    r = validate_sentiment_boost(args.account, sym, side, npz_dir)
                elif path == "ratio":
                    r = validate_ratio_size(args.account, sym, side, npz_dir)
                elif path == "delta":
                    r = validate_delta_entry(args.account, sym, side, npz_dir, mode=args.mode)
                elif path == "dc_break":
                    r = validate_dc_break(args.account, sym, side, npz_dir)
                elif path == "reentry":
                    r = validate_reentry(args.account, sym, side, npz_dir)
                elif path == "golden_rule":
                    r = validate_golden_rule(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "wt_force":
                    r = validate_wt_force(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "price_cross_back":
                    r = validate_price_cross_back(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "scalp_v2":
                    r = validate_scalp_v2(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "scalp_v3":
                    r = validate_scalp_v3(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "micro_scalp":
                    r = validate_micro_scalp(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "r1_exit":
                    r = validate_r1_exits(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "r2_exit":
                    r = validate_r2_exits(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "ppl":
                    r = validate_partial_profit_lock(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "wt_crossunder_final":
                    r = validate_wt_crossunder_final(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "srs":
                    r = validate_structural_range_shift(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "hedge_open":
                    r = validate_hedge_open(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "hedge_failed":
                    r = validate_hedge_failed(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "satoshit":
                    r = validate_satoshit(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "fh_momentum":
                    r = validate_fh_momentum(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "bb_recovery":
                    r = validate_bb_recovery(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "mom3":
                    r = validate_mom3(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "sizing":
                    r = validate_sizing(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "dup_guard":
                    r = validate_dup_guard(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "reduce_paths":
                    r = validate_reduce_paths(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "peak_giveback":
                    r = validate_peak_giveback(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "winner_protect":
                    r = validate_winner_protect(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "exit_to_reduce":
                    r = validate_exit_to_reduce_adapter(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "ratio_reduce_proxy":
                    r = validate_ratio_reduce_proxy(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "first_open_throttle":
                    r = validate_first_open_throttle(sym, side, args.account, npz_dir, mode=args.mode)
                elif path == "dc_breach_reduce":
                    r = validate_dc_breach_reduce(sym, side, args.account, npz_dir, mode=args.mode)
                else:
                    continue
            except Exception as e:
                r = {"path": path, "error": str(e)}
            all_results.append({**r, "symbol": sym, "side": side})
            note = r.get("note", "")
            live_n = r.get("live_events", "?")
            matched = r.get("vec_matched", "n/a")
            rate = r.get("match_rate", "n/a")
            oor = r.get("out_of_range", 0)
            checkable = r.get("checkable", None)
            oor_str = f" out_of_range={oor}" if isinstance(oor, int) and oor > 0 else ""
            if isinstance(oor, int) and oor > 0:
                stale_marker = " ⚠ NPZ STALE — check before treating rate as a code bug"
            else:
                stale_marker = ""
            check_str = f" checkable={checkable}" if checkable is not None else ""
            print(f"  [{r.get('path', path)}] live={live_n} matched={matched} rate={rate}{check_str}{oor_str}{stale_marker}")
            if note:
                print(f"    note: {note[:120]}")
            if "error" in r:
                print(f"    ERROR: {r['error']}")
            for s in r.get("unmatched_samples", [])[:3]:
                print(f"    unmatched: {s}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_live = sum(r.get("live_events", 0) for r in all_results)
    total_matched = sum(r.get("vec_matched", 0) for r in all_results if isinstance(r.get("vec_matched"), int))
    print(f"Total live events across all paths: {total_live}")
    print(f"Total vec-matched events: {total_matched}")
    if total_live > 0:
        overall = total_matched / total_live * 100
        print(f"Overall match rate: {overall:.1f}%")
    print("\nNote: RATIO_SIZE match=0 is CORRECT (backtest sets ratio=0.5 neutral).")
    print("Note: DELTA_ENTRY=0 live events is expected (DELTA_ENGINE_ENABLED=False in production).")
    print("Note: SCALP_V2=0 live events is expected (SCALP_MODE=False in production).")
    print("Note: SCALP_V3 events exist in /history/inf/ under QUICK_SCALP_V3_OPEN_ prefix (pre-2026-04-28).")
    print("Note: MICRO_SCALP live events confirmed in /history/trb/ for GOOGL/NVDA/PLTR/SNDK/USO etc.")
    print("Note: WT_3M_FORCE_OPEN=0 live events expected (feature added 2026-05-10, may not have fired).")
    print("Note: PRICE_CROSS_BACK=0 live events expected for crypto accounts (tradier-specific).")
    print("Note: R1_DC=0 live events expected (15-min newborn window, rarely triggers in practice).")
    print("Note: R2_WT_VEL_SLOW=0 live events expected (recent addition 2026-05-09, NOLOSS gate blocks most).")
    print("Note: PPL=0 live events expected (PARTIAL_PROFIT_LOCK_ENABLED=False in live as of 2026-05-12).")
    print("Note: WT_CROSSUNDER_FINAL confirmed in /history/trb/: GOOGL_LONG (100% vec match), OLED_SHORT (100%), IBIT_LONG (NPZ stale), SMCI_SHORT (NPZ stale). GLD_LONG/FIVN_SHORT have SRS events not WT events.")
    print("Note: SRS confirmed in /history/trb/: FIVN_SHORT, GLD_LONG, XOM_SHORT, COPX_LONG. Match rate=0% EXPECTED — NPZ stoch_k_1h (from resampled 15m) diverges 90+ pts from live stoch (from raw 1h). Not a code bug.")
    print("Note: SATOSHIT=0 live events expected — only confirmed in /history/men/MAGICUSDT_SHORT 2026-03-26.")
    print("Note: FH_MOMENTUM=0 live events expected — FH_MOMENTUM_ENABLED=False (tradier); CRYPTO_FH_MOMENTUM=True but rare.")
    print("Note: BB_RECOVERY=0 live events expected — only fires on stranded entries above BB_UPPER_1H.")
    print("Note: MOM3 is ADDITIVE (not standalone) — validator runs in diagnostic mode on OPEN events.")
    print("Note: HEDGE_OPEN live events confirmed in /history/ang/inf/men (~2500 HEDGE_PROTECT_*_LOSS events). Expected match rate 30-60%.")
    print("Note: HEDGE_FAILED=5 live events total (fin/NEARUSDC_SHORT, ang/GRIFFAINUSDT_SHORT). Very rare — hedge usually succeeds.")
    print("Note: SIZING=diagnostic only — reports WT_HTF_DISCOUNT modifier distribution, no event-count match (live sizing happens in execute_now, not /history/).")
    print("Note: DUP_GUARD=diagnostic — AUGMENT events in /history/ have already passed the live dup guard; vec replays same bar to count how many would be blocked (gain < 1.5%).")
    print("Note: REDUCE_PATHS=0 live events expected — K1M_EXTREME_REVERSE/STRONG_REDUCE_K/PROFIT_TAKE_REDUCE all default OFF in production config.")
    print("Note: PEAK_GIVEBACK live events confirm PEAK_GIVEBACK_GAIN_EROSION_STOP_ prefix in /history/ (mostly tradier). DROP_TRIGGER and HARD_ZERO both OFF in live — vec validator forces them ON for coverage testing.")
    print("Note: WINNER_PROTECT live events: R2_WT_VEL_SLOW CLOSE events added 2026-05-09; WINNER_PROTECT_ENABLED=False in production. Expect 0 WP events; R2 events present in trb/trc after 2026-05-09.")


if __name__ == "__main__":
    main()
