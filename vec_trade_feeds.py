#!/usr/bin/env python3
"""Vec-side trade feeds for plot mismatch markers.

Two feeds, both compared against the real fills from /history/ (live_trade_history.fetch_trades):

HTML FEED (frozen at publish)
    TRADES from the newest SPREADSHEETS/V15_V16_CELL_BY_CELL
    {SYM}_{SIDE}_*_30d_matrix.html. A 60D/365D board joins only once a fresh
    one appears (mtime newer than the picked 30D) — stale long-window boards
    never contribute old numbers. Frozen the moment the backtest is published.

FORWARD FEED (moves with the npz bars)
    vec_window_events from data/forward_parity/crypto_latest.json (24/7,
    every 15min via tools/forward_live_vs_vector/cron_hourly.sh) plus the
    newest stocks run (RTH). Vector trades on the newest NPZ, round-trip
    tagged like live fills. Freshness equals the last forward tick.

Results are cached per (sym, side) for the life of the process: plots call
per symbol per TF panel, parsing happens once.
"""
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
FWD_CRYPTO = ROOT / "data" / "forward_parity" / "crypto_latest.json"
FWD_RUNS = ROOT / "data" / "forward_parity" / "runs"

sys.path.insert(0, str(ROOT / "tools"))
try:
    from v15_html_parity_vlines import extract_js_array, to_utc
except Exception:
    extract_js_array = None
    to_utc = None

_html_cache = {}
_fwd_index = None


def _to_utc(s):
    if to_utc is not None:
        return to_utc(s)
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pick_html_files(files):
    """Newest 30D board only; a 60D/365D board joins only when it is fresher.

    USER 2026-10-24: stale long-window boards must never contribute old
    numbers. The vec side is the latest 30D test until a new 60D/365D board
    appears (mtime newer than the picked 30D board).
    """
    by_mtime = sorted(files, key=lambda p: p.stat().st_mtime)
    short = [p for p in by_mtime if "_30d_matrix.html" in p.name]
    longw = [p for p in by_mtime if "_30d_matrix.html" not in p.name]
    picked = short[-1:] if short else []
    if picked and longw:
        picked += [p for p in longw if p.stat().st_mtime >= picked[0].stat().st_mtime]
    elif longw:
        picked += longw[-1:]
    return picked


def load_html_trades(sym, side):
    """(entries, exits) datetimes from the picked matrix HTML(s) for sym_side."""
    key = (str(sym).upper(), str(side).upper())
    if key in _html_cache:
        return _html_cache[key]
    entries, exits = [], []
    if extract_js_array is not None and HTML_DIR.is_dir():
        seen = set()
        for hp in _pick_html_files(list(HTML_DIR.glob(f"{key[0]}_{key[1]}_*_matrix.html"))):
            try:
                text = hp.read_text(errors="replace")
                trades = extract_js_array(text, "TRADES")
            except Exception:
                continue
            for t in trades or []:
                try:
                    k = (str(t.get("entry_time")), str(t.get("exit_time")))
                    if k in seen:
                        continue
                    seen.add(k)
                    entries.append(_to_utc(t["entry_time"]))
                    if t.get("exit_time"):
                        exits.append(_to_utc(t["exit_time"]))
                except Exception:
                    continue
    _html_cache[key] = (entries, exits)
    return entries, exits


def _forward_docs():
    """Fresh forward docs, reloaded when the hourly job rewrites them.

    crypto_latest.json (24/7, every 15min) plus the newest stocks run (RTH).
    Each row carries vec_window_events = vector trades on the newest NPZ.
    """
    global _fwd_index
    cands = []
    if FWD_CRYPTO.is_file():
        cands.append(FWD_CRYPTO)
    if FWD_RUNS.is_dir():
        stocks = sorted(FWD_RUNS.glob("stocks_*.json"), key=lambda p: p.stat().st_mtime)
        if stocks:
            cands.append(stocks[-1])
    key = tuple((str(p), p.stat().st_mtime) for p in cands)
    if _fwd_index is None or _fwd_index[0] != key:
        rows = {}
        for p in cands:
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            for r in d.get("rows") or []:
                if r.get("ss"):
                    rows.setdefault(str(r["ss"]).upper(), []).append(r)
        _fwd_index = (key, rows)
    return _fwd_index[1]


_fwd_index = None


_VEC_ENTRY_TOKENS = ("OPEN", "BUY", "ADD", "REOPEN", "AUGMENT")
_VEC_EXIT_TOKENS = ("CLOSE", "EXIT", "COVER", "SELL", "TAKE")


def load_forward_trips(sym, side):
    """(entries, exits) datetimes from the fresh forward job's vec window.

    vec_window_events ([epoch, TYPE, reason]) carry no qty, so there is no
    balance to account: engine OPEN/AUGMENT = entry side, engine CLOSE =
    flat. REDUCE (partial, no boundary) is skipped.
    """
    symside = f"{str(sym).upper()}_{str(side).upper()}"
    entries, exits = [], []
    for r in _forward_docs().get(symside, []):
        for ev in r.get("vec_window_events") or []:
            try:
                ts = datetime.fromtimestamp(float(ev[0]), tz=timezone.utc)
                typ = str(ev[1] or "").upper()
            except Exception:
                continue
            if any(k in typ for k in _VEC_EXIT_TOKENS):
                exits.append(ts)
            elif any(k in typ for k in _VEC_ENTRY_TOKENS):
                entries.append(ts)
    return sorted(entries), sorted(exits)


def vec_trades_for_plot(sym):
    """Merged vec side for one symbol across LONG+SHORT and both feeds.

    Returns (entries, exits) sorted datetime lists. HTML (frozen) and forward
    (fresh) unioned — a live fill matching either counts as explained.
    """
    entries, exits = [], []
    for side in ("LONG", "SHORT"):
        he, hx = load_html_trades(sym, side)
        fe, fx = load_forward_trips(sym, side)
        entries += he + fe
        exits += hx + fx
    return sorted(entries), sorted(exits)


def draw_vec_mismatch(ax_plot, x_left_ts, x_right_ts, vec_entries, vec_exits,
                      fill_entries, fill_exits, x_of, tol_min=30.0):
    """Trade-based discrepancy markers (replaces indicator-signal matching).

    Grey UP arrow under the box = vec trade (HTML frozen or forward) with no
    /history/ fill nearby. Grey DOWN arrow above the box = live fill with no
    vec trade nearby. Entries match entries, exits match exits. Either side
    empty (no vec feed for this symbol) -> (0, 0), nothing drawn.
    """
    import live_trade_history as lth
    ve, vx = sorted(vec_entries), sorted(vec_exits)
    fe, fx = sorted(fill_entries), sorted(fill_exits)
    if not ve and not vx:
        return 0, 0
    tol_s = tol_min * 60.0
    me_v, me_l = lth.greedy_match(ve, fe, tol_s)
    mx_v, mx_l = lth.greedy_match(vx, fx, tol_s)
    vc, va = lth.MISMATCH_VEC_MPL
    lc, la = lth.MISMATCH_LIVE_MPL
    try:
        _lo, _hi = ax_plot.get_ylim()
        _span = _hi - _lo if _hi > _lo else 1.0
    except Exception:
        _lo, _span = 0.0, 1.0
    y_below, y_above = _lo - 0.015 * _span, _lo + 1.015 * _span
    n_vec = n_live = 0
    for i, t in enumerate(ve):
        if i not in me_v and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_below, marker="^", color=vc, alpha=va,
                         ms=lth.MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_vec += 1
    for i, t in enumerate(vx):
        if i not in mx_v and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_below, marker="^", color=vc, alpha=va,
                         ms=lth.MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_vec += 1
    for j, t in enumerate(fe):
        if j not in me_l and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_above, marker="v", color=lc, alpha=la,
                         ms=lth.MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_live += 1
    for j, t in enumerate(fx):
        if j not in mx_l and x_left_ts <= t <= x_right_ts:
            ax_plot.plot(x_of(t), y_above, marker="v", color=lc, alpha=la,
                         ms=lth.MISMATCH_MARKER_SIZE, linestyle="None", zorder=9, clip_on=False)
            n_live += 1
    return n_vec, n_live
