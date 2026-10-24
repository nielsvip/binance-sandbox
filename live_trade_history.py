#!/usr/bin/env python3
"""live_trade_history — shared reader for per-account trade ledgers.

Both venues append {ts, type, qty, price, value, reason} JSON lines to
  {DATA_DIR}/history/{acct}/{SYM}_{SIDE}.jsonl
with a byte-identical mirror under {BASE}/data/history/{acct}/. This module
reads one root (tradier first, data/history as fallback) and dedupes, so
callers never double-count the mirror. No venue imports: ez_rankings and
tradier_rankings both use it without import cycles.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ACCOUNT_ARROW_ORDER = ["trb", "ang", "inf", "flz", "men", "fin"]
ACCOUNT_ARROW_COLORS = {"ang": "#00e676", "inf": "#2196f3", "flz": "#ff1744", "men": "#ff9100", "fin": "#cfd8dc"}
TRB_BUY_COLOR = "#00e676"
TRB_SELL_COLOR = "#ff1744"
VEC_ONLY_COLOR = "rgba(248,113,113,0.55)"
LIVE_ONLY_COLOR = "rgba(74,222,128,0.55)"
MISMATCH_VEC_MPL = ("#bdbdbd", 0.90)
MISMATCH_LIVE_MPL = ("#757575", 0.90)
MISMATCH_LW = 1.0
WIN_COLOR = "#00e676"
LOSS_COLOR = "#ff1744"
UNKNOWN_COLOR = "#9e9e9e"


def boundary_events(events):
    return [e for e in (events or []) if e.get("phase") in ("entry", "exit")]


def arrow_alpha(event):
    return ENTRY_ALPHA if event.get("phase", "entry") == "entry" else EXIT_ALPHA


OUTCOME_LW = 1.5
OUTCOME_ALPHA = 0.20


def draw_outcome_lines(ax_plot, x_left_ts, x_right_ts, events, x_of):
    """Win/loss vertical bars at the EXIT of each closed round trip only:
    green = winning exit, red = losing exit. Entries never get a bar even
    when their trip's outcome is known, and open trips get none.

    events: boundary dicts with timestamp + phase + win (from
    annotate_round_trips). x_of maps a timestamp to the axes x-coordinate.
    Returns (n_win, n_loss).
    """
    n_win = n_loss = 0
    for e in events or []:
        if e.get("phase") != "exit":
            continue
        w = e.get("win")
        if w is None:
            continue
        t = e.get("timestamp")
        if t is None or not (x_left_ts <= t <= x_right_ts):
            continue
        if w is True:
            ax_plot.axvline(x=x_of(t), color=WIN_COLOR, alpha=OUTCOME_ALPHA, lw=OUTCOME_LW, zorder=6)
            n_win += 1
        elif w is False:
            ax_plot.axvline(x=x_of(t), color=LOSS_COLOR, alpha=OUTCOME_ALPHA, lw=OUTCOME_LW, zorder=6)
            n_loss += 1
    return n_win, n_loss


MISMATCH_MARKER_SIZE = 7


def draw_mismatch_lines(ax_plot, x_left_ts, x_right_ts, sig_buy, sig_sell, fill_buy, fill_sell, x_of, tol_min=30.0):
    """Discrepancy markers outside the panel, no vertical lines: grey UP
    arrow tucked under the box = vector signal with no live fill, grey DOWN
    arrow above the box = live fill with no vector signal.

    x_of maps an event timestamp to the axes x-coordinate (plot_idx on stocks,
    raw timestamps on crypto). Returns (n_vec_only, n_live_only).
    """
    tol_s = tol_min * 60.0
    sb, ss = sorted(sig_buy), sorted(sig_sell)
    fb, fs = sorted(fill_buy), sorted(fill_sell)
    if not sb and not ss:
        return 0, 0
    mb_v, mb_l = greedy_match(sb, fb, tol_s)
    ms_v, ms_l = greedy_match(ss, fs, tol_s)
    vc, va = MISMATCH_VEC_MPL
    lc, la = MISMATCH_LIVE_MPL
    try:
        _lo, _hi = ax_plot.get_ylim()
        _span = _hi - _lo if _hi > _lo else 1.0
    except Exception:
        _lo, _span = 0.0, 1.0
    y_below, y_above = _lo - 0.015 * _span, _lo + 1.015 * _span
    n_vec = n_live = 0
    for i, t in enumerate(sb):
        if i not in mb_v and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_below, marker="^", color=vc, alpha=va,
                         ms=MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_vec += 1
    for i, t in enumerate(ss):
        if i not in ms_v and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_below, marker="^", color=vc, alpha=va,
                         ms=MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_vec += 1
    for j, t in enumerate(fb):
        if j not in mb_l and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_above, marker="v", color=lc, alpha=la,
                         ms=MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_live += 1
    for j, t in enumerate(fs):
        if j not in ms_l and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_above, marker="v", color=lc, alpha=la,
                         ms=MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_live += 1
    return n_vec, n_live

_BUY_TOKENS = ("OPEN", "AUGMENT", "ADD", "REOPEN", "BUY")
_SELL_TOKENS = ("REDUCE", "CLOSE", "SELL", "EXIT", "COVER", "TAKE")


def event_direction(event_type, side):
    t = str(event_type or "").upper()
    if any(k in t for k in _SELL_TOKENS):
        base = "sell"
    elif any(k in t for k in _BUY_TOKENS):
        base = "buy"
    else:
        base = "buy" if side == "LONG" else "sell"
    if side == "SHORT":
        base = "sell" if base == "buy" else "buy"
    return base


def account_color(acct, direction):
    if acct == "trb":
        return TRB_BUY_COLOR if direction == "buy" else TRB_SELL_COLOR
    return ACCOUNT_ARROW_COLORS[acct]


ENTRY_ALPHA = 1.0
EXIT_ALPHA = 0.65
ARROW_SIZE = 55


def _qty(rec):
    try:
        return float(rec.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def _px(rec):
    try:
        return float(rec.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def annotate_round_trips(events, side):
    """FIFO win/loss per closed round trip, in place.

    events: time-sorted, single side, phase-tagged, with qty+price. Entries
    accumulate cost; a boundary exit closes everything open since the last flat
    and stamps win (pnl>0, side-signed, qty-weighted entry) on the exit and its
    entries. Unmatched exits and still-open entries keep win=None (grey).
    """
    sign = -1.0 if side == "SHORT" else 1.0
    open_evts, open_q, open_cost = [], 0.0, 0.0
    for e in events:
        e["win"] = None
        ph = e.get("phase")
        q = abs(_qty(e))
        px = _px(e)
        if ph == "entry":
            open_evts.append(e)
            open_q += q
            open_cost += px * q
        elif ph == "exit":
            if open_evts and open_q > 0 and px > 0:
                avg = open_cost / open_q
                win = ((px - avg) / avg * sign) > 0 if avg > 0 else None
                e["win"] = win
                for o in open_evts:
                    o["win"] = win
            open_evts, open_q, open_cost = [], 0.0, 0.0
    return events


def mark_boundaries(records):
    """Tag ledger records with round-trip phase via cumulative qty.

    records: [{ts, type, qty}]. qty is a DELTA (added on OPEN/AUGMENT/BUY,
    removed on REDUCE/CLOSE/...). Returns same list with phase entry/exit/adapt:
    entry = balance leaves ~0, exit = balance returns to ~0, adapt = mid-position
    size changes (skipped by overlays). Unknown types change nothing. A typed
    event with missing qty still counts as a unit move so OPENs never vanish.
    """
    peak = max([abs(_qty(r)) for r in records] + [1.0])
    eps = 1e-6 * peak
    bal = 0.0
    out = []
    for r in records:
        typ = str(r.get("type") or "").upper()
        q = abs(_qty(r))
        if any(k in typ for k in _BUY_TOKENS):
            nb = bal + (q if q > eps else eps * 10.0)
        elif any(k in typ for k in _SELL_TOKENS):
            nb = bal - (q if q > eps else eps * 10.0)
        else:
            nb = bal
        nb = max(0.0, nb)
        if bal <= eps and nb > eps:
            phase = "entry"
        elif bal > eps and nb <= eps:
            phase = "exit"
        else:
            phase = "adapt"
        bal = nb
        d = dict(r)
        d["phase"] = phase
        out.append(d)
    return out


def fetch_trades(symbol, accounts, hist_dirs):
    import json
    out = {}
    sym = str(symbol or "").upper()
    if not sym:
        return out
    for acct in accounts:
        by_side = {}
        for hdir in hist_dirs:
            adir = Path(hdir) / acct
            if not adir.is_dir():
                continue
            for side in ("LONG", "SHORT"):
                fpath = adir / f"{sym}_{side}.jsonl"
                if not fpath.is_file():
                    continue
                try:
                    lines = fpath.read_text().splitlines()[-400:]
                except Exception:
                    continue
                for line in lines:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        ts = pd.to_datetime(rec.get("ts"), utc=True)
                    except Exception:
                        continue
                    if pd.isna(ts):
                        continue
                    typ = str(rec.get("type") or "")
                    by_side.setdefault(side, []).append(
                        {"timestamp": ts, "direction": event_direction(typ, side),
                         "type": typ, "qty": _qty(rec), "price": _px(rec), "side": side,
                         "reason": str(rec.get("reason") or "")[:80]})
        open_set, trusted = _load_open_set(hist_dirs)
        events = []
        for side, recs in by_side.items():
            recs.sort(key=lambda e: e["timestamp"])
            recs = _dedupe_mirror_lines(recs)
            recs = _merge_order_fragments(recs)
            recs = _collapse_echo_fills(recs)
            recs = _collapse_stuck_relogs(recs)
            marked = mark_boundaries(recs)
            marked = _cancel_same_bar_pairs(marked)
            marked = _net_bucket_filter(marked)
            marked = annotate_round_trips(marked, side)
            if trusted and (acct, sym, side) not in open_set:
                _mark_stale_trailing(marked)
            events.extend(marked)
        if events:
            events.sort(key=lambda e: e["timestamp"])
            out[acct] = events
    return out


def _dedupe_mirror_lines(recs):
    """Drop cross-root mirror copies BEFORE balance accounting.

    data/history and data/tradier/history carry the same fills in different
    JSON formatting, so byte comparison never matches. Key on content
    instead; keep the first occurrence so a later distinct record survives.
    """
    seen, uniq = set(), []
    for r in recs:
        key = (r["timestamp"].isoformat(), r["side"], r["type"], r["qty"], r["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def _fill_class(typ):
    t = str(typ or "").upper()
    if any(k in t for k in _SELL_TOKENS):
        return "exit"
    if any(k in t for k in _BUY_TOKENS):
        return "entry"
    return "other"


def _is_sync_echo(reason):
    r = str(reason or "").upper()
    return "BROKER_SYNC" in r or "BROKER SYNC" in r or "BROKER_RECONCILED" in r


def _merge_order_fragments(recs):
    """Merge partial-fill fragments sharing one broker order number.

    The broker logs one logical fill as several same-second records
    (e.g. REDUCE 41 + REDUCE 140, one order=... number) and the strategy
    logs the same fill again under its own reason. Summing fragments first
    turns them into the single fill they are: summed qty, earliest ts,
    qty-weighted price, strategy reason preferred. Without this, fragments
    masquerade as separate entries/exits inside one 15m bar.
    """
    import re as _re
    groups, order_of, singles = {}, [], []
    for r in recs:
        m = _re.search(r"order=(\d+)", str(r.get("reason") or ""))
        if not m:
            singles.append(r)
            continue
        groups.setdefault(m.group(1), []).append(r)
        if m.group(1) not in order_of:
            order_of.append(m.group(1))
    merged = []
    for oid in order_of:
        g = sorted(groups[oid], key=lambda e: e["timestamp"])
        if len(g) == 1:
            merged.append(g[0])
            continue
        qty = sum(abs(float(r.get("qty") or 0)) for r in g)
        px = sum(abs(float(r.get("qty") or 0)) * float(r.get("price") or 0) for r in g)
        strat = [r for r in g if not _is_sync_echo(r.get("reason"))]
        base = dict(strat[0] if strat else g[0])
        base["timestamp"] = g[0]["timestamp"]
        base["qty"] = qty
        base["price"] = (px / qty) if qty > 0 else 0.0
        merged.append(base)
    out = sorted(singles + merged, key=lambda e: e["timestamp"])
    return out


def _collapse_echo_fills(recs, window_s=90.0):
    """Collapse strategy/broker double-logs of the same fill.

    Every bot fill is logged twice: once by the strategy (real reason) and
    once by the broker sync (BROKER_SYNC_OPEN_FROM_FLAT_STUB / Broker sync),
    seconds apart with the same side, qty and direction class. Counting both
    inflates the position balance so round trips never close (e.g. CDW showed
    one eternal entry). Same side + same class + same qty within 90s keeps
    one record, preferring the strategy reason. Genuine distinct fills are
    >=1 15m bar apart by construction, so same-qty repeats inside 90s are
    echoes, not trades.
    """
    out = []
    for r in recs:
        cls = _fill_class(r.get("type"))
        dup = None
        if cls in ("entry", "exit"):
            for o in reversed(out):
                if (o["timestamp"] - r["timestamp"]).total_seconds() < -window_s:
                    break
                if (o.get("side") == r.get("side") and _fill_class(o.get("type")) == cls
                        and abs(float(o.get("qty") or 0) - float(r.get("qty") or 0)) < 1e-9):
                    dup = o
                    break
        if dup is None:
            out.append(r)
        elif _is_sync_echo(r.get("reason")) and not _is_sync_echo(dup.get("reason")):
            pass
        elif _is_sync_echo(dup.get("reason")) and not _is_sync_echo(r.get("reason")):
            out[out.index(dup)] = r
        # else: identical echoes, keep the first
    return out


def _cancel_same_bar_pairs(recs, bar_min=15):
    """Cancel entry+exit pairs netting to zero inside one bar.

    We only trade 15m bars and up, so an entry and an exit of equal size in
    the same 15m bucket is not a trade (daemon whipsaw / sync churn) — it
    nets to zero and must not draw arrows, bars or mismatch fills. Unequal
    remainders (real flips: exit of one trip plus entry of the next) survive.
    """
    from datetime import timezone
    buckets, order = {}, []
    for r in recs:
        ts = r["timestamp"].astimezone(timezone.utc)
        key = (ts.replace(minute=(ts.minute // bar_min) * bar_min, second=0, microsecond=0),
               r.get("side"), r.get("phase"))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(r)
    drop = set()
    seen_buckets = set((k[0], k[1]) for k in order)
    for ts_b, side in seen_buckets:
        entries = [r for r in buckets.get((ts_b, side, "entry"), [])]
        exits = [r for r in buckets.get((ts_b, side, "exit"), [])]
        for x in exits:
            for e in entries:
                if id(e) not in drop and abs(float(e.get("qty") or 0) - float(x.get("qty") or 0)) < 1e-9:
                    drop.add(id(e))
                    drop.add(id(x))
                    break
    return [r for r in recs if id(r) not in drop]


def _load_open_set(hist_dirs):
    """Live broker-flat truth: (acct, sym, side) triples currently holding.

    Reads {root}/{acct}/{long,short}_positions.json next to each history root
    (root = parent of data/). Only accounts whose files are fresher than 24h
    count as trusted; anything else (missing/stale files) keeps ledger entries
    untouched rather than risk hiding a real position.
    """
    import json
    import time
    from pathlib import Path as _P
    open_set, trusted = set(), False
    for hdir in hist_dirs or []:
        try:
            proot = _P(hdir).parent.parent
            adirs = [d for d in _P(hdir).iterdir() if d.is_dir()]
        except Exception:
            continue
        for adir in adirs:
            acct = adir.name
            fresh = False
            for side, fname in (("LONG", "long_positions.json"), ("SHORT", "short_positions.json")):
                fp = proot / acct / fname
                try:
                    if not fp.is_file() or time.time() - fp.stat().st_mtime > 86400:
                        continue
                    fresh = True
                    data = json.loads(fp.read_text())
                except Exception:
                    continue
                if isinstance(data, dict):
                    for k, v in data.items():
                        try:
                            amt = float((v or {}).get("positionAmt") or 0)
                        except (TypeError, ValueError):
                            continue
                        if abs(amt) <= 1e-9:
                            continue
                        import re as _re
                        parts = str(k).split(":")
                        sym = (parts[1] if len(parts) > 1 else parts[0]).upper()
                        sym = _re.sub(r"_(LONG|SHORT)$", "", sym)
                        open_set.add((acct, sym, side))
            if fresh:
                trusted = True
    return open_set, trusted


def _mark_stale_trailing(marked, max_age_s=1800.0):
    """Demote trailing unclosed entries when the broker is flat.

    The ledger's last word is an entry with no close and the live position
    file says flat: the trip ended off-ledger (or the entry is a phantom).
    Showing it as an entry arrow implies an open position that does not
    exist. Phase becomes "stale": kept for audit, excluded from arrows,
    bars and mismatch fills. Entries fresher than max_age_s keep the benefit
    of the doubt (position file may lag a just-opened position).
    """
    import time
    now = time.time()
    last_exit = -1
    for i, e in enumerate(marked):
        if e.get("phase") == "exit":
            last_exit = i
    for i in range(last_exit + 1, len(marked)):
        e = marked[i]
        if e.get("phase") != "entry" or e.get("win") is not None:
            continue
        try:
            age = now - e["timestamp"].timestamp()
        except Exception:
            continue
        if age > max_age_s:
            e["phase"] = "stale"


def _net_bucket_filter(recs, bar_min=15, wash_pct=0.01):
    """One phase per 15m bucket per side: net the signed qty.

    We only trade 15m bars and up, so a bucket holding both entries and
    exits is intra-bar churn, never two bar-close decisions. Net entries
    (+) against exits (-): a wash (|net| <= 1% of bucket volume, e.g. close
    186 + reopen 185) drops both; a take with trims (entry 5708, trim 516)
    keeps the entries; a reduction keeps the exits. Entries-only and
    exits-only buckets pass through untouched. Adapts never participate.
    """
    from datetime import timezone
    buckets = {}
    for r in recs:
        if r.get("phase") not in ("entry", "exit"):
            continue
        ts = r["timestamp"].astimezone(timezone.utc)
        key = (ts.replace(minute=(ts.minute // bar_min) * bar_min, second=0, microsecond=0),
               r.get("side"))
        buckets.setdefault(key, []).append(r)
    drop = set()
    for members in buckets.values():
        kinds = set(r.get("phase") for r in members)
        if len(kinds) < 2:
            continue
        net = sum(float(r.get("qty") or 0) * (1.0 if r.get("phase") == "entry" else -1.0)
                  for r in members)
        vol = sum(abs(float(r.get("qty") or 0)) for r in members)
        if abs(net) <= max(1e-9, wash_pct * vol):
            for r in members:
                drop.add(id(r))
        elif net > 0:
            for r in members:
                if r.get("phase") == "exit":
                    drop.add(id(r))
        else:
            for r in members:
                if r.get("phase") == "entry":
                    drop.add(id(r))
    return [r for r in recs if id(r) not in drop]


def _collapse_stuck_relogs(recs):
    """Collapse runs of identical consecutive state re-logs.

    The strategy loggers re-emit the same (side, type, qty, price) record
    every ~2 min while a state persists (e.g. 150x AUGMENT 21.0 @ same
    price). Each copy would otherwise inflate the position balance and no
    round trip could ever close. Genuine consecutive fills at exactly the
    same price are rare; keep the first of a run.
    """
    out = []
    for r in recs:
        if out and (out[-1]["side"], out[-1]["type"], out[-1]["qty"], out[-1]["price"]) == \
                (r["side"], r["type"], r["qty"], r["price"]):
            continue
        out.append(r)
    return out


def wilder_rsi(close, period=14):
    c = pd.to_numeric(close, errors="coerce")
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rsi = pd.Series(50.0, index=c.index)
    up_only = (avg_loss == 0) & (avg_gain > 0)
    rsi[up_only] = 100.0
    rest = ~((avg_gain == 0) & (avg_loss == 0)) & ~up_only
    rsi[rest] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))[rest]
    return rsi.fillna(50.0).clip(0.0, 100.0)


def stoch_kd(close, window=14, smooth_k=3, smooth_d=3):
    c = pd.to_numeric(close, errors="coerce")
    try:
        from ta.momentum import StochRSIIndicator
        k = StochRSIIndicator(close=c, window=window, smooth1=smooth_k, smooth2=smooth_d, fillna=True).stochrsi() * 100.0
        k = pd.Series(k, index=c.index).clip(0.0, 100.0)
    except Exception:
        k = pd.Series(50.0, index=c.index)
    d = k.rolling(smooth_d, min_periods=1).mean()
    return k.fillna(50.0).clip(0.0, 100.0), d.fillna(50.0).clip(0.0, 100.0)


def _num_x(x_vals):
    import matplotlib.dates as mdates
    xa = np.asarray(x_vals)
    try:
        if np.issubdtype(xa.dtype, np.datetime64):
            return mdates.date2num(xa)
    except TypeError:
        pass
    if len(xa) and hasattr(xa.flat[0], "strftime"):
        return mdates.date2num(pd.to_datetime(xa, utc=True))
    return xa.astype(float)


def draw_rsi_strip(ax_plot, x_vals, rsi_vals):
    r = np.asarray(rsi_vals, dtype=float)
    x = _num_x(x_vals)
    if len(x) < 2 or len(x) != len(r):
        return
    rsi_ax = ax_plot.inset_axes([0.0, 0.07, 1.0, 0.06])
    rsi_ax.set_facecolor("#1e2427")
    rsi_ax.patch.set_alpha(0.85)
    up = r >= 50.0
    rsi_ax.plot(x, np.where(up, r, np.nan), color="#5b9dff", lw=0.9, zorder=3)
    rsi_ax.plot(x, np.where(~up, r, np.nan), color="#ff6b6b", lw=0.9, zorder=3)
    rsi_ax.axhline(50.0, color="#8a8a8a", lw=0.5, alpha=0.6, zorder=2)
    rsi_ax.set_xlim(ax_plot.get_xlim())
    rsi_ax.set_ylim(0.0, 100.0)
    rsi_ax.set_xticks([])
    rsi_ax.set_yticks([])
    for spine in rsi_ax.spines.values():
        spine.set_color("#5a5a5a")
    rsi_ax.text(0.01, 0.85, "RSI", transform=rsi_ax.transAxes, fontsize=6, color="#9e9e9e", va="top", ha="left", zorder=4)
    rsi_ax.set_zorder(6)


def draw_stoch_strip(ax_plot, x_vals, k_vals, d_vals):
    k = np.asarray(k_vals, dtype=float)
    d = np.asarray(d_vals, dtype=float)
    x = _num_x(x_vals)
    if len(x) < 2 or len(x) != len(k) or len(x) != len(d):
        return
    st_ax = ax_plot.inset_axes([0.0, 0.0, 1.0, 0.07])
    st_ax.set_facecolor("#1e2427")
    st_ax.patch.set_alpha(0.85)
    up = k >= d
    st_ax.plot(x, np.where(up, k, np.nan), color="#5b9dff", lw=0.9, zorder=3)
    st_ax.plot(x, np.where(~up, k, np.nan), color="#ff5252", lw=0.9, zorder=3)
    st_ax.plot(x, d, color="#e0e0e0", lw=0.6, alpha=0.8, zorder=3)
    st_ax.axhline(80.0, color="#8a8a8a", lw=0.5, alpha=0.5, linestyle=":", zorder=2)
    st_ax.axhline(20.0, color="#8a8a8a", lw=0.5, alpha=0.5, linestyle=":", zorder=2)
    st_ax.set_xlim(ax_plot.get_xlim())
    st_ax.set_ylim(0.0, 100.0)
    st_ax.set_xticks([])
    st_ax.set_yticks([])
    for spine in st_ax.spines.values():
        spine.set_color("#5a5a5a")
    st_ax.text(0.01, 0.82, "STOCH", transform=st_ax.transAxes, fontsize=5, color="#9e9e9e", va="top", ha="left", zorder=4)
    st_ax.set_zorder(6)


def greedy_match(vec_ts, live_ts, tol_s):
    matched_v, matched_l = set(), set()
    for i, vt in enumerate(vec_ts):
        best, best_dt = None, tol_s + 1
        for j, lt in enumerate(live_ts):
            if j in matched_l:
                continue
            try:
                dt = abs((lt - vt).total_seconds())
            except Exception:
                continue
            if dt <= tol_s and dt < best_dt:
                best, best_dt = j, dt
        if best is not None:
            matched_v.add(i)
            matched_l.add(best)
    return matched_v, matched_l
