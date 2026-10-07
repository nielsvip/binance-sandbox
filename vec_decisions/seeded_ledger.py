"""SEEDED-LEDGER initial state for v12_quick_engine.simulate_one (lane w2-carryin).

Problem (vec cold-start): simulate_one always starts flat (pos=None, trades=[],
has_closed_before=False) at window ts0. Live never cold-starts: at any ts0 it
holds open positions (E4 carry-in closes) and remembers pre-window exits (E2
mandatory price-cross reentry levels). The vec therefore misses in-window
closes of carry-in positions and cannot reenter off pre-window levels.

This module builds the seeded initial state from a live open-position
snapshot. It is called ONCE per simulate_one (init hunk) plus once for
metrics exclusion; both calls are gated by SEEDED_LEDGER_ENABLED (default
False = cold start, bitwise identical to today).

Snapshot input (SEEDED_LEDGER_SNAPSHOT_JSON, dict or JSON string). TWO
accepted shapes:

  (a) V8_SEED-compatible (live tracker format, shared with the scalar twin
      backtest_v12_engine V8_SEED_POSITIONS_FILE / V12_F1 decisions seed):
        {"account": str, "positions": {position_key: live_Position_dict}}
      Live Position fields consumed (tradier_positions.TradierPosition /
      ez crypto Position / exchange API mapped onto the same keys):
        symbol, position_side ("LONG"/"SHORT"), positionAmt (signed),
        entry_price, opened_at|entry_time (ISO or unix), cycle_peak_gain,
        max_gain, last_reduction_price, last_reduction_time,
        reduction_reason, bars_held_at_ts0 (optional exact override).
      Exchange mapping (done by the Monday extractor, not here): Tradier
      API quantity/cost_basis/date_acquired -> positionAmt/entry_price/
      opened_at; Binance positionAmt/entryPrice -> same keys.

  (b) Minimal explicit (synthetic seeds, tracker-replay output):
        {"window_ts0": unix, "seeds": {SYM_SIDE: {"open": {...},
                                                  "last_exit": {...}}}}
        open: {side, qty, entry_price, entry_ts, bars_held_at_ts0,
               peak_pnl_pct, source, note}
        last_exit: {exit_price, exit_ts, exit_reason, source, note}

Semantics (all documented approximations are one-directional/conservative):
  * Seeded OPEN -> pos dict with the exact keys _open() produces, plus
    entry_bar = -bars_held_at_ts0 (held time INCLUDES pre-window holding;
    no ts[entry_bar] indexing exists anywhere, so negative bars are safe),
    entry_reason SEEDED_CARRY_IN_*. P&L is trade-lifetime (vs live entry),
    matching live broker statements for in-window closes; the ts0 mark and
    pre-window unrealized ride in the OPEN event so window-attributed P&L
    stays derivable. Seeded fills do NOT increment the overtrade day
    counter (pre-window fills are not in-window fills).
  * Seeded EXIT (flat at ts0 only) -> a metrics-neutral CLOSE row with
    seeded_prewindow=True, pnl 0, deployed 0, qty 0, bar_entry=bar_exit=0.
    Reentry levels (exit_price/reason) are EXACT; age is measured from
    window start (bar_exit=0), which UNDERSTATES true age -> churn/tier2
    gates stay conservative, never aggressive. The row is excluded from
    every metric (metrics_rows) but kept in the ledger for reason trace.
  * cd0 = 0 always: a pre-window live exit is not "our" close; live
    reentry gating (churn/confirmation/hardcool) still applies per bar.
  * Fail-closed: side mismatch, non-positive qty/price, entry_ts/exit_ts
    after ts0 (future leak), or malformed JSON -> that seed is IGNORED
    with a SEED_IGNORED trace line. Never crash, never fabricate.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

SNAPSHOT_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# timestamp parsing (live tracker ISO strings or unix numbers)
# --------------------------------------------------------------------------

def _parse_ts(v):
    """-> unix float or 0.0. Accepts unix numbers, numeric strings, ISO."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else 0.0
    if isinstance(v, datetime):
        try:
            d = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            return float(d.timestamp())
        except Exception:
            return 0.0
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        try:
            return float(s)
        except Exception:
            pass
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return float(d.timestamp())
        except Exception:
            return 0.0
    return 0.0


def _num(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


# --------------------------------------------------------------------------
# snapshot parsing -> normalized seed
# --------------------------------------------------------------------------

def _match_v8_position(positions, sym, side):
    """Find the live position dict for sym/side in a V8_SEED positions map."""
    if not isinstance(positions, dict):
        return None, ""
    want_sym = str(sym).upper()
    for pk, pd in positions.items():
        if not isinstance(pd, dict):
            continue
        psym = str(pd.get("symbol", "")).upper()
        pside = str(pd.get("position_side", "")).upper()
        if psym == want_sym and pside == side:
            return pd, str(pk)
        # fallback: position_key "{account}:{SYM}_{SIDE}"
        try:
            tail = str(pk).split(":", 1)[-1]
            if tail.upper() == f"{want_sym}_{side}":
                return pd, str(pk)
        except Exception:
            pass
    return None, ""


def parse_snapshot(doc, sym, is_long, ts0, bmin=15.0):
    """Normalize a snapshot doc to (seed_open, seed_exit, trace).

    seed_open: dict(qty, entry_price, entry_ts, bars_held, peak, source,
                   note) or None.
    seed_exit: dict(exit_price, exit_ts, exit_reason, source, note) or None.
    trace: list[str] reason lines (SEED_* / SEED_IGNORED_*).
    """
    trace = []
    side = "LONG" if is_long else "SHORT"
    symside = f"{str(sym).upper()}_{side}"
    if isinstance(doc, str):
        s = doc.strip()
        if not s:
            return None, None, trace
        try:
            doc = json.loads(s)
        except Exception as e:
            trace.append(f"SEED_IGNORED_{symside} bad_json={type(e).__name__}")
            return None, None, trace
    if not isinstance(doc, dict):
        trace.append(f"SEED_IGNORED_{symside} not_a_mapping")
        return None, None, trace

    seed_open = None
    seed_exit = None

    # --- shape (b): explicit minimal seeds ---
    seeds = doc.get("seeds")
    if isinstance(seeds, dict) and isinstance(seeds.get(symside), dict):
        s = seeds[symside]
        o = s.get("open") if isinstance(s.get("open"), dict) else None
        if o is not None:
            if str(o.get("side", side)).upper() != side:
                trace.append(f"SEED_IGNORED_{symside} open_side_mismatch")
            else:
                qty = _num(o.get("qty"))
                ep = _num(o.get("entry_price"))
                ets = _parse_ts(o.get("entry_ts"))
                if qty <= 0 or ep <= 0:
                    trace.append(f"SEED_IGNORED_{symside} open_bad_qty_px")
                elif ets > ts0:
                    trace.append(f"SEED_IGNORED_{symside} open_future_entry_ts")
                else:
                    try:
                        bars = int(o.get("bars_held_at_ts0", 0) or 0)
                    except Exception:
                        bars = 0
                    seed_open = {
                        "qty": qty, "entry_price": ep, "entry_ts": ets,
                        "bars_held": max(0, bars),
                        "peak": _num(o.get("peak_pnl_pct")),
                        "source": str(o.get("source", "explicit")),
                        "note": str(o.get("note", "")),
                    }
        x = s.get("last_exit") if isinstance(s.get("last_exit"), dict) else None
        if x is not None:
            xp = _num(x.get("exit_price"))
            xts = _parse_ts(x.get("exit_ts"))
            if xp <= 0:
                trace.append(f"SEED_IGNORED_{symside} exit_bad_px")
            elif xts > ts0:
                trace.append(f"SEED_IGNORED_{symside} exit_future_ts")
            else:
                seed_exit = {
                    "exit_price": xp, "exit_ts": xts,
                    "exit_reason": str(x.get("exit_reason", "SEED_PREWINDOW_EXIT")),
                    "source": str(x.get("source", "explicit")),
                    "note": str(x.get("note", "")),
                }

    # --- shape (a): V8_SEED live-tracker positions map ---
    if seed_open is None and seed_exit is None and isinstance(doc.get("positions"), dict):
        pd, pk = _match_v8_position(doc.get("positions"), sym, side)
        if pd is None:
            trace.append(f"SEED_NONE_{symside} no_live_position")
        else:
            amt = _num(pd.get("positionAmt"))
            ep = _num(pd.get("entry_price"))
            if abs(amt) > 0 and ep > 0:
                ets = (_parse_ts(pd.get("opened_at"))
                       or _parse_ts(pd.get("entry_time"))
                       or _parse_ts(pd.get("last_updated")))
                if ets > ts0:
                    trace.append(f"SEED_IGNORED_{symside} live_future_open_ts")
                else:
                    try:
                        bars = int(pd.get("bars_held_at_ts0", 0) or 0)
                    except Exception:
                        bars = 0
                    if bars <= 0 and ets > 0 and bmin > 0:
                        bars = int((ts0 - ets) / (bmin * 60.0))
                    peak = _num(pd.get("cycle_peak_gain"))
                    if peak == 0.0:
                        peak = _num(pd.get("max_gain"))
                    seed_open = {
                        "qty": abs(amt), "entry_price": ep, "entry_ts": ets,
                        "bars_held": max(0, bars), "peak": peak,
                        "source": f"live:{pk}",
                        "note": str(pd.get("last_signal", "")),
                    }
            elif abs(amt) > 0:
                trace.append(f"SEED_IGNORED_{symside} live_bad_entry_px")
            if seed_open is None:
                # flat at ts0: last reduction doubles as the pre-window exit
                # level (live reentry fires off last-reduction/exit levels).
                rp = _num(pd.get("last_reduction_price"))
                rts = _parse_ts(pd.get("last_reduction_time"))
                if rp > 0 and rts > ts0:
                    trace.append(f"SEED_IGNORED_{symside} live_future_reduce_ts")
                elif rp > 0:
                    seed_exit = {
                        "exit_price": rp, "exit_ts": rts,
                        "exit_reason": "SEED_PREWINDOW_EXIT",
                        "source": f"live:{pk}",
                        "note": str(pd.get("reduction_reason", "")),
                    }
                else:
                    trace.append(f"SEED_NONE_{symside} live_flat_no_history")

    if seed_open is not None:
        o = seed_open
        trace.append(
            f"SEED_OPEN_{symside} qty={o['qty']:.4f} entry={o['entry_price']:.4f} "
            f"bars_held={o['bars_held']} src={o['source']}")
    if seed_exit is not None:
        x = seed_exit
        trace.append(
            f"SEED_EXIT_{symside} px={x['exit_price']:.4f} src={x['source']}")
    return seed_open, seed_exit, trace


# --------------------------------------------------------------------------
# state builders (same dict shapes simulate_one._open / CLOSE rows produce)
# --------------------------------------------------------------------------

def build_seeded_open(seed_open, ts0, mark0, half_fee):
    """-> (pos, open_event). pos has the exact keys _open() produces."""
    qty = float(seed_open["qty"])
    ep = float(seed_open["entry_price"])
    bars = int(seed_open.get("bars_held", 0) or 0)
    peak = float(seed_open.get("peak", 0.0) or 0.0)
    src = str(seed_open.get("source", "explicit"))
    reason = (f"SEEDED_CARRY_IN_{src}_e{ep:.4f}_tb{bars}")[:120]
    deployed = abs(qty * ep)
    pos = {
        "qty": qty, "avg_price": ep, "entry_price": ep, "entry_qty": qty,
        "deployed": deployed, "realized": 0.0,
        "entry_bar": -max(0, bars), "peak_pnl_pct": peak,
        "fees": deployed * half_fee, "entry_reason": reason,
    }
    unreal = 0.0
    if mark0 > 0 and ep > 0:
        unreal = (mark0 - ep) / ep * 100.0
    ev = {
        "type": "OPEN", "ts": float(ts0), "price": float(ep),
        "qty": float(qty), "pos_deployed": deployed, "bar": 0,
        "reason": reason, "seeded_carry_in": True,
        "seed_source": src, "entry_ts": float(seed_open.get("entry_ts", 0.0) or 0.0),
        "mark_at_ts0": float(mark0), "unrealized_at_ts0_pct": float(unreal),
    }
    return pos, ev


def build_seeded_exit_row(seed_exit):
    """-> metrics-neutral CLOSE row (excluded via metrics_rows)."""
    xp = float(seed_exit["exit_price"])
    reason = str(seed_exit.get("exit_reason") or "SEED_PREWINDOW_EXIT")
    if "TARGET" in reason and "dc_" in reason.lower():
        reason = "SEED_PREWINDOW_EXIT"
    return {
        "pnl_dollars": 0.0, "pnl_pct": 0.0, "deployed": 0.0,
        "reason": reason, "type": "CLOSE",
        "ts": float(seed_exit.get("exit_ts", 0.0) or 0.0),
        "price": xp, "bar_entry": 0, "bar_exit": 0,
        "entry_price": xp, "exit_price": xp, "qty": 0.0,
        "entry_reason": "SEED_PREWINDOW", "exit_reason": reason,
        "bars_held": 0, "seeded_prewindow": True,
        "seed_source": str(seed_exit.get("source", "explicit")),
    }


# --------------------------------------------------------------------------
# engine entry points (the two staged call sites)
# --------------------------------------------------------------------------

def apply_seed(cfg, sym, is_long, ts, close, half_fee, bmin):
    """Build seeded initial state. -> (pos, trades, has_closed, cd, events,
    trace) or None when disabled/empty/unmatched (caller keeps cold start).
    """
    try:
        if not bool(getattr(cfg, "SEEDED_LEDGER_ENABLED", False)):
            return None
        doc = getattr(cfg, "SEEDED_LEDGER_SNAPSHOT_JSON", "")
        if isinstance(doc, str) and not doc.strip():
            return None
        if doc is None or doc == "":
            return None
    except Exception:
        return None
    try:
        ts0 = float(ts[0])
    except Exception:
        return None
    try:
        mark0 = float(close[0])
    except Exception:
        mark0 = 0.0
    try:
        bmin_f = float(bmin or 15.0)
    except Exception:
        bmin_f = 15.0
    seed_open, seed_exit, trace = parse_snapshot(doc, sym, is_long, ts0, bmin_f)
    if seed_open is None and seed_exit is None:
        return None
    pos = None
    trades = []
    events = []
    if seed_open is not None:
        pos, ev = build_seeded_open(seed_open, ts0, mark0, float(half_fee or 0.0))
        events.append(ev)
    if seed_exit is not None and pos is None:
        # exit seed applies when flat at ts0 (E2); a carry-in open owns
        # the reentry state once it closes in-window.
        trades.append(build_seeded_exit_row(seed_exit))
    elif seed_exit is not None:
        trace.append("SEED_EXIT_HELD open_carry_in_owns_reentry_state")
    has_closed = len(trades) > 0
    return pos, trades, has_closed, 0, events, trace


def metrics_rows(trades):
    """trades minus pre-window seed rows (metrics only; ledger untouched)."""
    try:
        return [t for t in (trades or []) if not t.get("seeded_prewindow")]
    except Exception:
        return list(trades or [])