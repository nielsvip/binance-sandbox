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
MISMATCH_VEC_MPL = ("#f87171", 0.14)
MISMATCH_LIVE_MPL = ("#4ade80", 0.14)
MISMATCH_LW = 0.8


def boundary_events(events):
    return [e for e in (events or []) if e.get("phase") in ("entry", "exit")]


def arrow_alpha(event):
    return ENTRY_ALPHA if event.get("phase", "entry") == "entry" else EXIT_ALPHA


def draw_mismatch_lines(ax_plot, x_left_ts, x_right_ts, sig_buy, sig_sell, fill_buy, fill_sell, x_of, tol_min=30.0):
    """Faint discrepancy vlines: reddish = signal without fill, greenish = fill without signal.

    x_of maps an event timestamp to the axes x-coordinate (plot_idx on stocks,
    raw timestamps on crypto). Returns (n_vec_only, n_live_only).
    """
    tol_s = tol_min * 60.0
    sb, ss = sorted(sig_buy), sorted(sig_sell)
    fb, fs = sorted(fill_buy), sorted(fill_sell)
    mb_v, mb_l = greedy_match(sb, fb, tol_s)
    ms_v, ms_l = greedy_match(ss, fs, tol_s)
    vc, va = MISMATCH_VEC_MPL
    lc, la = MISMATCH_LIVE_MPL
    n_vec = n_live = 0
    for i, t in enumerate(sb):
        if i not in mb_v and x_left_ts <= t <= x_right_ts:
            ax_plot.axvline(x=x_of(t), color=vc, alpha=va, lw=MISMATCH_LW, zorder=6)
            n_vec += 1
    for i, t in enumerate(ss):
        if i not in ms_v and x_left_ts <= t <= x_right_ts:
            ax_plot.axvline(x=x_of(t), color=vc, alpha=va, lw=MISMATCH_LW, zorder=6)
            n_vec += 1
    for j, t in enumerate(fb):
        if j not in mb_l and x_left_ts <= t <= x_right_ts:
            ax_plot.axvline(x=x_of(t), color=lc, alpha=la, lw=MISMATCH_LW, zorder=6)
            n_live += 1
    for j, t in enumerate(fs):
        if j not in ms_l and x_left_ts <= t <= x_right_ts:
            ax_plot.axvline(x=x_of(t), color=lc, alpha=la, lw=MISMATCH_LW, zorder=6)
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


ENTRY_ALPHA = 0.95
EXIT_ALPHA = 0.45


def _qty(rec):
    try:
        return float(rec.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


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
                         "type": typ, "qty": _qty(rec), "side": side,
                         "reason": str(rec.get("reason") or "")[:80]})
        events = []
        for side, recs in by_side.items():
            recs.sort(key=lambda e: e["timestamp"])
            events.extend(mark_boundaries(recs))
        if events:
            seen = set()
            uniq = []
            for ev in events:
                key = (ev["timestamp"].isoformat(), ev["type"], ev["direction"])
                if key not in seen:
                    seen.add(key)
                    uniq.append(ev)
            uniq.sort(key=lambda e: e["timestamp"])
            out[acct] = uniq
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
