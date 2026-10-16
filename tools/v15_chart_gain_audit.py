#!/usr/bin/env python3
"""v15_chart_gain_audit — re-run every published chart's own arithmetic and prove the trades are tradeable.

USER 2026-10-16, verbatim: *"build a small script that reruns all the exising charts like
APO_SHORT_bh14p03_gain17p_t19_30d_matrix_manifest.json (in same folder) and makes sure the gain numbers are the
actual sum of the entries and exits it is showing on the chart, also revise the xlsx/json/log it is built from are
never forward looking but real tradeable trades"*.

WHY IT CAN RUN ANYWHERE (including the Mac, which holds 21 of 1091 NPZ): every published chart EMBEDS its own
evidence — `const CLOSE` (the exact execution bars the engine simulated), `const TRADES` (the realised closes the
pictograms are drawn from) and `const EVENTS` (OPEN/AUGMENT/REDUCE). So the headline gain can be recomputed from
the very entries and exits the chart draws, with no NPZ and no engine. `--rerun` additionally re-executes
`tools.opt.hires_chart.generate_hires` on the published set and diffs fresh-vs-published, and that half needs the
NPZ, so it is S1/S2 only and skips honestly elsewhere.

THE GAIN DEFINITION IT REPRODUCES (never re-implemented — read off the live code):
    v12_quick_engine.py:22590   gain_pct = sum(pnl_dollars) / peak_capital * 100
    tools/opt/evaluate_v12.py:523  _honest_gain_pct — same sum over the SPIKE-FILTERED ledger
    tools/opt/evaluate_v12.py:336  _peak_concurrent — event-level signed replay (OPEN sets, AUGMENT adds,
                                   REDUCE subtracts, any realised CLOSE resets), fallback = bar sweep-line
    tools/opt/evaluate_v12.py:410  _spike_filtered — |pnl_pct| > 60 is a bad tick, never alpha
Both denominators are computed here and the header is accepted if it matches EITHER, because the chart header is
written from the vector metrics (sweep-line peak) while the embedded TRADES come from the ledger path
(event-replay peak) — when those two forks disagree the chart is printing a number its own pictograms do not add
up to, which is exactly what this audit exists to catch.

FORWARD-LOOKING / TRADEABILITY (the "real tradeable trades" half):
  LA_SAME_BAR_ROUNDTRIP entry and exit on one bar — intrabar omniscience, not a tradeable round trip
  LA_BAR_ORDER          exit bar at or before the entry bar
  LA_OFF_SERIES_FILL    a fill on no bar close (a stop/limit level filled intrabar) — UNPROVEN until --npz
  LA_FILL_OUTSIDE_BAR   (--npz) that level is outside the bar's own [low, high]: unfillable, fabricated.
                        This is the ONLY verdict on an off-close fill. A level coinciding with some other bar's
                        close is recorded as context and never called lookahead — a price repeats by chance.
  LA_OVERLAP            two trades open at once on a strictly single-position engine
  LA_UNREALISED_MTM     a FINAL_MTM row counted in gain — an OPEN position marked to market, never sold
  LA_WINDOW_AFTER_PUB   the window's last bar is dated after the manifest's own published_utc
  LA_SPIKE_TRADE        a |pnl_pct| > 60 bad-tick trade that survived into the published chart
  LA_ZERO_COST          INFO, always: these gains are gross — the cost ladder is printed, never silently assumed

SOURCES CROSS-CHECKED per chart (the xlsx/json/log half): the filename gain+trades, the _manifest.json metrics,
the xlsx `*_BASELINE_METRICS` tab and greedy E column, the matching `*_v14_progress.json`, and the manifest's own
`accumulation_check`.

READ-ONLY. It never re-stamps, renames or rewrites a published xlsx/manifest/html (2026-10-13: re-stamping
published sheets is illegal). It writes one report under data/reports/ and prints a worklist.

USAGE
    python3 tools/v15_chart_gain_audit.py                                  # audit every chart in CELL_BY_CELL
    python3 tools/v15_chart_gain_audit.py --symside APO_SHORT --verbose    # one sym_side, every finding
    python3 tools/v15_chart_gain_audit.py --npz                            # + intrabar fill verification
    python3 tools/v15_chart_gain_audit.py --rerun --symside APO_SHORT      # S1/S2: regenerate and diff
    python3 tools/v15_chart_gain_audit.py --fail-on ERROR                  # exit 1 when any chart FAILs
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
OUT_DIR = ROOT / "data" / "reports"
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
PROGRESS_DIRS = [ROOT / "data" / "reports" / "lifecycle_pilot", ROOT / "data" / "reports" / "lifecycle_pilot_s2"]
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")
MAX_TRADE_PCT = 60.0
PRICE_TOL = 1e-6
SEV_ORDER = {"ERROR": 2, "WARN": 1, "INFO": 0}
COST_LADDER_BPS = (2.0, 5.0, 10.0)


def log(msg):
    print(msg, flush=True)


def _r(v, nd=4):
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _overrides_md5(ov):
    return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()


def parse_js_const(text, name):
    """Pull `const NAME = <json>;` out of the generated chart without executing anything."""
    key = "const %s =" % name
    i = text.find(key)
    if i < 0:
        return None
    j = text.index("=", i) + 1
    k = text.find(";\n", j)
    if k < 0:
        return None
    try:
        return json.loads(text[j:k].strip())
    except ValueError:
        return None


_HDR_PATTERNS = {
    "gain_pct": r"gain\s+(-?[\d.]+)%",
    "bh_pct": r"BH\s+(-?[\d.]+)%",
    "delta_vs_bh": r"Δ\s+(-?[\d.]+)%",
    "trades": r"trades\s+(\d+)",
    "pool_sharpe": r"pool_sharpe=([+\-]?[\d.]+)",
    "max_dd_pct": r"DD\s+(-?[\d.]+)%",
    "max_dd_mae_pct": r"DDmtm\s+(-?[\d.]+)%",
    "tim_pct": r"TIM\s+(-?[\d.]+)%",
    "wr_pct": r"WR\s+(-?[\d.]+)%",
    "bars": r"window\s+(\d+)\s+bars",
    "peak_capital": r"peak\s+\$(-?[\d.]+)",
}


def parse_header(text):
    i = text.find('<div class="metrics">')
    seg = text[i : text.find("</div>", i)] if i >= 0 else text[:4000]
    out = {}
    for key, pat in _HDR_PATTERNS.items():
        m = re.search(pat, seg)
        out[key] = (int(m.group(1)) if key in ("trades", "bars") else float(m.group(1))) if m else None
    m = re.search(r"fork-fixed metrics=(\d+)→closes=(\d+)", seg)
    out["fork_note"] = {"metrics": int(m.group(1)), "closes": int(m.group(2))} if m else None
    m = re.search(r"ledger (\d+) trades \+ (\d+) opens / (\d+) augment / (\d+) reduce", seg)
    out["ledger_counts"] = {"trades": int(m.group(1)), "opens": int(m.group(2)), "augments": int(m.group(3)), "reduces": int(m.group(4))} if m else None
    return out


def peak_concurrent(ledger):
    """Faithful copy of tools/opt/evaluate_v12._peak_concurrent (event replay, sweep-line fallback)."""
    rows = [t for t in (ledger or []) if isinstance(t, dict)]
    if not rows:
        return 0.0
    evs = []
    for t in rows:
        ty = str(t.get("type") or "").upper()
        try:
            ts = float(t.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ty in ("OPEN", "AUGMENT", "REDUCE"):
            try:
                p, q = float(t.get("price") or 0.0), float(t.get("qty") or 0.0)
            except (TypeError, ValueError):
                continue
            if p <= 0 or q == 0 or q != q or p != p:
                continue
            evs.append((ts, 0 if ty == "OPEN" else 1, ty, abs(q), p))
        elif "pnl_dollars" in t:
            evs.append((ts, 2, "CLOSE", 0.0, 0.0))
    if any(e[2] in ("OPEN", "AUGMENT", "REDUCE") for e in evs):
        evs.sort(key=lambda e: (e[0], e[1]))
        qty = peak = 0.0
        for _ts, _rk, ty, q, p in evs:
            if ty == "OPEN":
                qty = q
            elif ty == "AUGMENT":
                qty += q
            elif ty == "REDUCE":
                qty = max(0.0, qty - min(q, qty))
            else:
                qty = 0.0
                continue
            peak = max(peak, qty * p)
        return float(peak)
    return peak_sweepline(rows)


def peak_sweepline(trades):
    """Faithful copy of v12_quick_engine.py:22561 — the vector path's peak, which the header is written from."""
    ev = []
    for t in trades or []:
        try:
            d = float(t.get("deployed") or 0.0)
        except (TypeError, ValueError):
            continue
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if d <= 0 or be is None or bx is None:
            continue
        ev.append((int(be), d))
        ev.append((int(bx), -d))
    if not ev:
        return 0.0
    ev.sort(key=lambda x: (x[0], -x[1]))
    cur = peak = 0.0
    for _b, delta in ev:
        cur += delta
        peak = max(peak, cur)
    return float(peak)


def max_dd_pct(trades, base):
    """Faithful copy of the engine's realised peak-to-trough on the gain denominator (exit-bar order)."""
    if base <= 0:
        return 0.0
    rows = sorted(trades, key=lambda t: (int(t.get("bar_exit") or 0), int(t.get("bar_entry") or 0)))
    equity = peak = float(base)
    worst = 0.0
    for t in rows:
        equity += float(t.get("pnl_dollars") or 0.0)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return min(100.0, worst)


def recompute(trades, events, close, is_long):
    """Everything the chart claims, rebuilt from the entries and exits the chart itself draws."""
    pnl = sum(float(t.get("pnl_dollars") or 0.0) for t in trades)
    peak_ev = peak_concurrent(list(trades) + list(events))
    peak_sw = peak_sweepline(trades)
    out = {
        "trades": len(trades),
        "pnl_dollars": _r(pnl, 2),
        "peak_event_replay": _r(peak_ev, 2),
        "peak_sweepline": _r(peak_sw, 2),
        "gain_pct_event_peak": _r(pnl / peak_ev * 100.0) if peak_ev > 0 else None,
        "gain_pct_sweep_peak": _r(pnl / peak_sw * 100.0) if peak_sw > 0 else None,
        "wins": sum(1 for t in trades if float(t.get("pnl_dollars") or 0.0) > 0),
    }
    out["wr_pct"] = _r(100.0 * out["wins"] / len(trades), 2) if trades else None
    base = peak_ev if peak_ev > 0 else peak_sw
    out["max_dd_pct"] = _r(max_dd_pct(trades, base), 2)
    bars = set()
    for t in trades:
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if be is None or bx is None:
            continue
        bars.update(range(int(be), int(bx)))
    out["tim_pct"] = _r(100.0 * len(bars) / len(close), 2) if close else None
    if close and len(close) >= 2 and close[0] > 0:
        raw = (close[-1] - close[0]) / close[0] * 100.0
        out["bh_pct"] = _r(raw if is_long else max(-100.0, -raw), 4)
    else:
        out["bh_pct"] = None
    out["cost_ladder"] = {}
    for bps in COST_LADDER_BPS:
        cost = sum(float(t.get("deployed") or 0.0) * 2.0 * bps / 10000.0 for t in trades)
        out["cost_ladder"][f"{bps:g}bps"] = _r((pnl - cost) / base * 100.0) if base > 0 else None
    try:
        from metrics_guard import pool_sharpe_from_ledger

        out["pool_sharpe"] = _r(pool_sharpe_from_ledger(trades))
    except Exception as exc:
        out["pool_sharpe"] = None
        out["pool_sharpe_why"] = f"{type(exc).__name__}: {str(exc)[:80]}"
    return out


def _add(findings, sev, fid, msg, **extra):
    findings.append({"sev": sev, "id": fid, "msg": msg, **extra})


def check_gain(findings, hdr, rec, man, fname, tol):
    """The headline must be the sum of the pictograms — against either peak denominator the code uses."""
    cands = [v for v in (rec["gain_pct_event_peak"], rec["gain_pct_sweep_peak"]) if v is not None]
    if not cands:
        _add(findings, "ERROR", "GAIN_UNCOMPUTABLE", "no positive capital base in the embedded ledger — gain cannot be reproduced at all")
        return
    for label, claimed in (("header", hdr.get("gain_pct")), ("manifest", (man.get("metrics") or {}).get("gain_pct") if man else None)):
        if claimed is None:
            continue
        best = min(abs(claimed - c) for c in cands)
        if best > tol:
            _add(findings, "ERROR", f"GAIN_{label.upper()}_VS_LEDGER", f"{label} prints gain {claimed:+.4f}% but its own entries/exits sum to {cands[0]:+.4f}% (event-peak) / {cands[-1]:+.4f}% (sweep-peak) — drift {best:.4f}", claimed=claimed, recomputed=cands)
    if fname and fname.get("gain") is not None:
        if fname.get("fmt") == "old":
            ok = any(abs(c - fname["gain"]) <= max(tol, 0.005) for c in cands)
            slot = "cents slot"
        else:
            want = int(abs(fname["gain"])) * (-1 if fname["gain"] < 0 else 1)
            ok = any(int(abs(c)) * (-1 if c < 0 else 1) == want for c in cands)
            slot = "integer slot"
        if not ok:
            _add(findings, "ERROR", "GAIN_FILENAME_VS_LEDGER", f"filename says gain {fname['gain']:+g}% ({slot}) but the ledger sums to {cands[0]:+.2f}%", claimed=fname["gain"], recomputed=cands)
    if rec["gain_pct_event_peak"] is not None and rec["gain_pct_sweep_peak"] is not None:
        if abs(rec["gain_pct_event_peak"] - rec["gain_pct_sweep_peak"]) > tol:
            _add(findings, "WARN", "GAIN_PEAK_BASE_FORK", f"the two capital bases the codebase uses disagree: event-replay peak ${rec['peak_event_replay']} -> {rec['gain_pct_event_peak']:+.4f}% vs sweep-line peak ${rec['peak_sweepline']} -> {rec['gain_pct_sweep_peak']:+.4f}%")


def cost_stats(trades, events, is_long):
    """Per-row residual between the price move and the booked P&L — i.e. the cost model the chart actually applied.

    Measured 2026-10-16: crypto boards deduct a 4.0 bps round turn (v12_quick_engine:22672,
    CRYPTO_ROUND_TRIP_COMMISSION_PCT 0.04 / 200 per leg); tradier/stock boards deduct nothing at all. So the row
    check cannot demand pnl == qty x (exit - entry) — it demands that every row charges the SAME cost the rest of
    the chart charges, and reports what that cost is. Rows that scaled mid-life (an AUGMENT/REDUCE event inside
    their span) have no closed-form single-leg economics and are counted, never guessed at.
    """
    ev_bars = sorted(int(e["bar"]) for e in events if str(e.get("type") or "").upper() in ("AUGMENT", "REDUCE") and isinstance(e.get("bar"), int))
    rows, multi, unusable = [], 0, 0
    for t in trades:
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if not isinstance(be, int) or not isinstance(bx, int):
            unusable += 1
            continue
        if any(be < b <= bx for b in ev_bars):
            multi += 1
            continue
        if str(t.get("exit_reason") or "").upper().startswith("FINAL_MTM"):
            continue
        try:
            qty, ep, xp, pnl = float(t["qty"]), float(t["entry_price"]), float(t["exit_price"]), float(t["pnl_dollars"])
        except (KeyError, TypeError, ValueError):
            unusable += 1
            continue
        dep = float(t.get("deployed") or 0.0) or qty * ep
        if dep <= 0 or ep <= 0:
            unusable += 1
            continue
        # REDUCE_TO_FLAT / profit-lock closes carry qty = REMAINING size (0 = flat), so the chart never states how
        # much was actually sold on that row — metrics_guard:69 counts them as genuine closes all the same. They
        # get the sign test, never the closed-form one, because guessing their size would be the lie this audit
        # exists to catch.
        move = (xp - ep) if is_long else (ep - xp)
        sized = qty > 0 and abs(qty * ep - dep) <= 0.01 * dep
        rows.append({"trade": t, "sized": sized, "move": move, "gross": qty * move, "resid": qty * move - pnl, "bps": (qty * move - pnl) / dep * 1e4, "dep": dep, "pnl": pnl, "ep": ep, "xp": xp})
    bps = sorted(r["bps"] for r in rows if r["sized"])
    return rows, (bps[len(bps) // 2] if bps else None), multi, unusable


def check_rows(findings, trades, rows, median_bps, multi, unusable):
    """Every dollar in the headline must come out of qty x (exit - entry), minus the chart's own uniform cost."""
    if unusable:
        _add(findings, "WARN", "ROW_UNUSABLE", f"{unusable}/{len(trades)} trade row(s) carry no usable qty/price/bar fields — their contribution to the headline cannot be verified")
    if multi:
        _add(findings, "INFO", "ROW_MULTI_LEG", f"{multi}/{len(trades)} trade(s) scaled mid-life (AUGMENT/REDUCE inside their span) — single-leg arithmetic does not apply, they are verified only in the aggregate sum")
    if not rows:
        return
    sized = [r for r in rows if r["sized"]]
    unsized = [r for r in rows if not r["sized"]]
    free, outliers, worst = [], 0, None
    for r in sized:
        t = r["trade"]
        if r["bps"] < -0.5:
            free.append({"bps": _r(r["bps"], 2), "pnl": _r(r["pnl"], 4), "gross_from_prices": _r(r["gross"], 4), "exit_reason": t.get("exit_reason")})
        if median_bps is not None and abs(r["bps"] - median_bps) > 1.0 and abs(r["resid"] - median_bps / 1e4 * r["dep"]) > max(0.01, abs(r["pnl"]) * 1e-4):
            outliers += 1
            if worst is None or abs(r["bps"] - median_bps) > worst[0]:
                worst = (abs(r["bps"] - median_bps), {"entry": t.get("entry_price"), "exit": t.get("exit_price"), "qty": t.get("qty"), "pnl": _r(r["pnl"], 4), "gross_from_prices": _r(r["gross"], 4), "row_cost_bps": _r(r["bps"], 2), "chart_cost_bps": _r(median_bps, 2), "exit_reason": t.get("exit_reason")})
    if free:
        _add(findings, "ERROR", "ROW_PNL_FREE_MONEY", f"{len(free)}/{len(sized)} sized trade(s) book MORE P&L than their own entry/exit prices can produce", sample=free[:5])
    if outliers:
        _add(findings, "ERROR", "ROW_PNL_ARITHMETIC", f"{outliers}/{len(sized)} sized trade(s) charge a cost that is not the {median_bps:.2f} bps the rest of the chart charges — their P&L is not qty x (exit - entry) minus the same fee", worst=worst[1] if worst else None)
    contra = [r for r in unsized if abs(r["pnl"]) > 0.01 and abs(r["move"]) > 0 and (r["pnl"] > 0) != (r["move"] > 0)]
    if contra:
        _add(findings, "ERROR", "ROW_PNL_SIGN_CONTRADICTS_PRICES", f"{len(contra)}/{len(unsized)} reduce-to-flat row(s) book P&L of the OPPOSITE sign to the price move the chart draws for them", sample=[{"entry": _r(r["ep"], 8), "exit": _r(r["xp"], 8), "pnl": _r(r["pnl"], 4), "price_move_favours": "profit" if r["move"] > 0 else "loss", "exit_reason": r["trade"].get("exit_reason")} for r in contra[:5]])
    if unsized:
        _add(findings, "WARN", "ROW_CLOSED_SIZE_UNSTATED", f"{len(unsized)}/{len(rows)} row(s) carry a qty that is the REMAINING size, not the size closed (qty x entry_price != deployed), so the chart does not show how much was sold — their P&L cannot be reproduced from the pictograms, only trusted", dollars=_r(sum(r["pnl"] for r in unsized), 2))
    bad_pct = sum(1 for r in rows if abs(float(r["trade"].get("pnl_pct") or 0.0) - r["pnl"] / r["dep"] * 100.0) > 0.01)
    if bad_pct:
        _add(findings, "WARN", "ROW_PNL_PCT", f"{bad_pct}/{len(rows)} trades: pnl_pct is not pnl_dollars/deployed")
    bad_dep = sum(1 for r in rows if float(r["trade"].get("deployed") or 0.0) > 0 and float(r["trade"]["deployed"]) + 0.01 < float(r["trade"]["qty"]) * float(r["trade"]["entry_price"]) * 0.999)
    if bad_dep:
        _add(findings, "WARN", "ROW_DEPLOYED", f"{bad_dep}/{len(rows)} trades deploy less capital than qty x entry_price")


def check_lookahead(findings, trades, events, close, labels, npz, file_utc=None):
    """Real tradeable trades: every fill on a bar that existed, after the bar that signalled it."""
    n = len(close)
    where = {}
    for i, c in enumerate(close):
        where.setdefault(c, i)
    future = []
    stale = []
    off_series = []
    same_bar = 0
    bad_order = 0
    out_of_range = 0
    mtm = []
    spikes = 0
    for t in trades:
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if be is None or bx is None or not isinstance(be, int) or not isinstance(bx, int):
            out_of_range += 1
            continue
        if be < 0 or bx < 0 or be >= n or bx >= n:
            out_of_range += 1
            continue
        is_mtm = str(t.get("exit_reason") or "").upper().startswith("FINAL_MTM")
        if bx == be and not is_mtm:
            same_bar += 1
        elif bx < be:
            bad_order += 1
        for side, bar, px in (("entry", be, t.get("entry_price")), ("exit", bx, t.get("exit_price"))):
            if px is None:
                continue
            px = float(px)
            if abs(close[bar] - px) <= max(PRICE_TOL, abs(px) * 1e-9):
                continue
            hit = where.get(px)
            row = {"side": side, "bar": bar, "price": px, "bar_close": close[bar], "qty": t.get("qty"), "reason": t.get("exit_reason") if side == "exit" else t.get("entry_reason")}
            # A stop/limit level is a price, so it coincides with SOME other bar's close often enough that an
            # exact match proves nothing either way — it is recorded as context, never as a verdict. The only
            # thing that settles an off-close fill is whether it lies inside its own bar's [low, high]: --npz.
            if hit is not None:
                row["also_equals_bar"] = hit
                row["coincidence"] = "later bar" if hit > bar else "earlier bar"
                (future if hit > bar else stale).append(row)
            off_series.append(row)
        if is_mtm:
            mtm.append({"bar_exit": bx, "pnl_dollars": _r(t.get("pnl_dollars"), 2)})
        if abs(float(t.get("pnl_pct") or 0.0)) > MAX_TRADE_PCT:
            spikes += 1
    if same_bar:
        _add(findings, "ERROR", "LA_SAME_BAR_ROUNDTRIP", f"{same_bar} trade(s) enter and exit on the same bar — intrabar omniscience, not a tradeable round trip")
    if bad_order:
        _add(findings, "ERROR", "LA_BAR_ORDER", f"{bad_order} trade(s) exit before they enter")
    if out_of_range:
        _add(findings, "ERROR", "LA_BAR_RANGE", f"{out_of_range} trade(s) reference a bar outside the {n}-bar window the chart draws")
    if off_series:
        sev = "WARN" if npz is None else "INFO"
        extra = f" ({len(future)} also equal a LATER bar's close, {len(stale)} an earlier one — coincidence, not proof)" if (future or stale) else ""
        _add(findings, sev, "LA_OFF_SERIES_FILL", f"{len(off_series)} fill(s) at a price that is on no bar close — a stop/limit level filled intrabar{extra}. UNPROVEN either way until the bar's own [low,high] is read: run --npz on S1/S2", sample=off_series[:5])
    if mtm:
        paper = sum(abs(float(r["pnl_dollars"] or 0.0)) for r in mtm)
        sev = "WARN" if paper > 0.01 else "INFO"
        _add(findings, sev, "LA_UNREALISED_MTM", f"{len(mtm)} FINAL_MTM row(s) in the gain — an OPEN position marked to market at the window edge, never sold ({paper:.2f} paper dollars)", rows=mtm[:5])
    if spikes:
        _add(findings, "ERROR", "LA_SPIKE_TRADE", f"{spikes} trade(s) with |pnl_pct| > {MAX_TRADE_PCT:g} survived into the published chart — the spike filter did not run on this ledger")
    rows = sorted([t for t in trades if isinstance(t.get("bar_entry"), int) and isinstance(t.get("bar_exit"), int)], key=lambda t: (t["bar_entry"], t["bar_exit"]))
    overlap = sum(1 for a, b in zip(rows, rows[1:]) if b["bar_entry"] < a["bar_exit"])
    if overlap:
        _add(findings, "WARN", "LA_OVERLAP", f"{overlap} trade pair(s) are open at the same time on a strictly single-position engine")
    if labels and len(labels) == n:
        if file_utc and labels[-1][:16] > file_utc[:16]:
            _add(findings, "ERROR", "LA_BAR_AFTER_FILE_WRITTEN", f"the chart's last bar is dated {labels[-1]} UTC but the file was written {file_utc} UTC — it was scored on bars that did not exist yet")
        bad_ts = 0
        for t in trades:
            lt, bx = t.get("exit_time"), t.get("bar_exit")
            if lt and isinstance(bx, int) and 0 <= bx < n and labels[bx] != lt:
                bad_ts += 1
        if bad_ts:
            _add(findings, "WARN", "LA_TS_BAR_MISMATCH", f"{bad_ts} trade(s) carry an exit_time that is not the label of their exit bar")


def _align_offset(c_all, close, labels, ts_all):
    """Where the chart's window sits inside today's NPZ — by its first bar's UTC label, else by price match."""
    import calendar
    import numpy as np

    want = np.asarray(close, dtype=float)
    n = len(want)
    cands = []
    if labels and ts_all is not None and len(ts_all) == len(c_all):
        try:
            t0 = calendar.timegm(time.strptime(labels[0], "%Y-%m-%d %H:%M"))
            secs = ts_all / 1000.0 if float(ts_all[-1]) > 1e11 else ts_all
            cands.append(int(np.searchsorted(secs, t0)))
        except (ValueError, IndexError):
            pass
    cands.append(len(c_all) - n)
    first = want[0]
    hits = np.flatnonzero(np.abs(c_all[: len(c_all) - n + 1] - first) <= 1e-9)
    cands += [int(h) for h in hits[:8]]
    for off in cands:
        if 0 <= off <= len(c_all) - n and float(np.nanmax(np.abs(c_all[off : off + n] - want))) <= 1e-4:
            return off
    return None


def check_npz_fills(findings, trades, close, labels, symside, window_bars):
    """Clear (or condemn) every off-series fill against the bar's own high/low. Needs the NPZ — S1/S2."""
    import numpy as np

    sym = symside.rsplit("_", 1)[0]
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists():
        _add(findings, "INFO", "NPZ_ABSENT", f"{p.name} not on this host — intrabar fill verification skipped (run on S1/S2)")
        return
    try:
        d = np.load(p)
        keys = set(d.files)
        hk = "high_15m" if "high_15m" in keys else "high"
        lk = "low_15m" if "low_15m" in keys else "low"
        ck = "close_15m" if "close_15m" in keys else "close"
        high, low, c_all = np.asarray(d[hk], dtype=float), np.asarray(d[lk], dtype=float), np.asarray(d[ck], dtype=float)
    except Exception as exc:
        _add(findings, "INFO", "NPZ_UNREADABLE", f"{p.name}: {type(exc).__name__} {str(exc)[:80]}")
        return
    if len(c_all) < window_bars:
        _add(findings, "INFO", "NPZ_TOO_SHORT", f"{p.name} holds {len(c_all)} bars, the chart draws {window_bars} — cannot align")
        return
    off = _align_offset(c_all, close, labels, np.asarray(d["timestamps"], dtype=float) if "timestamps" in keys else None)
    if off is None:
        _add(findings, "INFO", "NPZ_WINDOW_UNALIGNED", f"{p.name} holds no run of bars matching the chart's CLOSE — the NPZ moved since publication, intrabar check skipped")
        return
    bad = []
    for t in trades:
        for side, bar, px in (("entry", t.get("bar_entry"), t.get("entry_price")), ("exit", t.get("bar_exit"), t.get("exit_price"))):
            if not isinstance(bar, int) or px is None or not (0 <= bar < window_bars):
                continue
            px = float(px)
            lo, hi = low[off + bar], high[off + bar]
            if px < lo - max(1e-6, abs(px) * 1e-6) or px > hi + max(1e-6, abs(px) * 1e-6):
                bad.append({"side": side, "bar": bar, "price": px, "bar_low": float(lo), "bar_high": float(hi), "reason": t.get("exit_reason") if side == "exit" else t.get("entry_reason")})
    if bad:
        _add(findings, "ERROR", "LA_FILL_OUTSIDE_BAR", f"{len(bad)} fill(s) priced outside their own bar's [low, high] — that trade could not have been executed", sample=bad[:5])
    else:
        _add(findings, "INFO", "NPZ_FILLS_OK", f"all fills lie inside their bar's [low, high] on {p.name}")


def check_sources(findings, html, man, hdr, rec, fname, symside, no_xlsx):
    """The xlsx/json/log the chart is built from must say the same thing the chart says."""
    if man is None:
        _add(findings, "WARN", "SRC_NO_MANIFEST", f"no {html.stem}_manifest.json beside the chart — the publish left no provenance")
    xlsx = html.with_suffix(".xlsx")
    if not xlsx.exists():
        _add(findings, "WARN", "SRC_NO_XLSX", f"no {xlsx.name} beside the chart")
    elif man and man.get("file_md5"):
        got = _md5_file(xlsx)
        if got != man["file_md5"]:
            _add(findings, "ERROR", "SRC_XLSX_MD5", f"{xlsx.name} md5 {got} != manifest file_md5 {man['file_md5']} — the board was rewritten after it was published")
    if man:
        mm = man.get("metrics") or {}
        if mm.get("trades") is not None and int(mm["trades"]) != rec["trades"]:
            _add(findings, "ERROR", "SRC_MANIFEST_TRADES", f"manifest says {int(mm['trades'])} trades, the chart draws {rec['trades']}")
        ac = man.get("accumulation_check") or {}
        if ac and ac.get("ok") is False:
            _add(findings, "WARN", "SRC_ACCUM_FAILED", f"manifest accumulation_check failed: expected {ac.get('expected')} measured {ac.get('measured')} drift {ac.get('drift')}")
        pub = str(man.get("published_utc") or "")
        last = (hdr.get("last_label") or "")[:10]
        if pub[:10] and last and last > pub[:10]:
            _add(findings, "WARN", "SRC_CHART_WINDOW_AFTER_MANIFEST", f"the chart's last bar is {last} but the manifest was published {pub[:10]} — the html was regenerated on a LATER window than the board it is named for, so these pictograms are not the trades the manifest scored")
    if fname:
        if fname.get("trades") is not None and int(fname["trades"]) != rec["trades"]:
            _add(findings, "ERROR", "SRC_FILENAME_TRADES", f"filename claims t{fname['trades']} but the chart draws {rec['trades']} trades")
    if hdr.get("trades") is not None and int(hdr["trades"]) != rec["trades"]:
        _add(findings, "ERROR", "SRC_HEADER_TRADES", f"header prints {hdr['trades']} trades, the embedded ledger holds {rec['trades']}")
    if not no_xlsx and xlsx.exists():
        _check_xlsx(findings, xlsx, symside, rec, man)
    _check_progress(findings, symside, man, rec)


def _check_xlsx(findings, xlsx, symside, rec, man):
    try:
        import openpyxl

        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    except Exception as exc:
        _add(findings, "WARN", "SRC_XLSX_UNREADABLE", f"{xlsx.name}: {type(exc).__name__} {str(exc)[:100]}")
        return
    try:
        tab = next((t for t in wb.sheetnames if t.endswith("_BASELINE_METRICS")), None)
        if tab is None:
            _add(findings, "WARN", "SRC_XLSX_NO_BASELINE_TAB", f"{xlsx.name} has no *_BASELINE_METRICS tab")
        else:
            vals = {}
            for row in wb[tab].iter_rows(min_row=1, max_row=24, max_col=2, values_only=True):
                if row and isinstance(row[0], str):
                    vals[row[0].strip()] = row[1]
            g, tr = vals.get("gain_pct"), vals.get("trades")
            if isinstance(g, str) or g is None:
                _add(findings, "ERROR", "SRC_XLSX_E2_NOT_NUMERIC", f"{xlsx.name}!{tab}.gain_pct is {g!r}, not a number")
            else:
                claimed = (man.get("metrics") or {}).get("gain_pct") if man else None
                ref = claimed if claimed is not None else rec["gain_pct_event_peak"]
                if ref is not None and abs(float(g) - float(ref)) > 0.01:
                    _add(findings, "WARN", "SRC_XLSX_BASELINE_STALE", f"{xlsx.name}!{tab} still carries gain {float(g):+.4f}% / {tr} trades while the published chart says {float(ref):+.4f}% / {rec['trades']} — the board tab was never refreshed by the publish that renamed it")
        best = None
        for name in wb.sheetnames:
            if name not in SWITCH_SHEETS:
                continue
            ws = wb[name]
            for row in ws.iter_rows(min_row=3, max_col=7, values_only=True):
                e = row[4] if len(row) > 4 else None
                g = row[6] if len(row) > 6 else None
                if isinstance(e, (int, float)):
                    cand = float(e) + (float(g) if isinstance(g, (int, float)) and g > 0 else 0.0)
                    best = cand if best is None or cand > best else best
        if best is not None:
            ref = (man.get("metrics") or {}).get("gain_pct") if man else rec["gain_pct_event_peak"]
            if ref is not None and best - float(ref) > 1.0:
                _add(findings, "WARN", "SRC_XLSX_GREEDY_ABOVE_FINAL", f"{xlsx.name} greedy column tops out at {best:+.2f}% but the published gain is {float(ref):+.2f}% — E/G cells and the headline do not tell the same story")
    finally:
        wb.close()


def _check_progress(findings, symside, man, rec):
    if not man or not man.get("overrides_md5"):
        return
    want = str(man["overrides_md5"])
    cands = []
    for d in list(PROGRESS_DIRS) + sorted(Path.home().glob("v15_*/progress")):
        if Path(d).is_dir():
            cands += sorted(Path(d).glob(f"{symside}_v14_progress.json"))
    for p in cands:
        try:
            j = json.loads(p.read_text())
        except Exception:
            continue
        ov = j.get("cumulative_overrides") or j.get("hustler_overrides") or {}
        if not isinstance(ov, dict) or _overrides_md5(ov) != want:
            continue
        fg = j.get("final_gain")
        ref = (man.get("metrics") or {}).get("gain_pct")
        if fg is not None and ref is not None and abs(float(fg) - float(ref)) > 0.01:
            _add(findings, "WARN", "SRC_PROGRESS_GAIN", f"{p.name} final_gain {float(fg):+.4f}% != manifest gain {float(ref):+.4f}% for the same override set")
        if j.get("trades") is not None and int(j["trades"]) != rec["trades"]:
            _add(findings, "WARN", "SRC_PROGRESS_TRADES", f"{p.name} records {j['trades']} trades, the chart draws {rec['trades']}")
        return
    _add(findings, "INFO", "SRC_PROGRESS_NOT_FOUND", f"no {symside}_v14_progress.json whose md5(cumulative_overrides) == manifest overrides_md5 — the set that produced this board cannot be recovered on this host")


def audit_one(html, args, parse_name):
    t0 = time.time()
    rec = {"chart": html.name, "symside": None, "verdict": "PASS", "findings": []}
    try:
        text = html.read_text(errors="replace")
    except Exception as exc:
        rec["verdict"] = "UNREADABLE"
        _add(rec["findings"], "ERROR", "CHART_UNREADABLE", f"{type(exc).__name__}: {str(exc)[:120]}")
        return rec
    fname = parse_name(html.name)
    symside = (fname or {}).get("symside") or html.name.split("_bh")[0]
    rec["symside"] = symside
    rec["filename_claims"] = {"gain": (fname or {}).get("gain"), "trades": (fname or {}).get("trades"), "bh": (fname or {}).get("bh")}
    close = parse_js_const(text, "CLOSE")
    trades = parse_js_const(text, "TRADES")
    events = parse_js_const(text, "EVENTS") or []
    labels = parse_js_const(text, "LABELS") or []
    if close is None or trades is None:
        rec["verdict"] = "UNPARSEABLE"
        _add(rec["findings"], "ERROR", "CHART_NO_LEDGER", "the chart embeds no CLOSE/TRADES arrays — nothing to verify it against")
        return rec
    # The trade population is whatever `metrics_guard.realised_closes` selects — ANY row carrying pnl_dollars,
    # type label irrelevant (metrics_guard.py:64, E2 unified semantic 2026-10-12). Charts generated before that
    # date split by type label instead, so their genuine REDUCE-to-flat closes (HLR_TOP, PPL_TP) were filed into
    # EVENTS and never drawn — the header counted them, the pictograms did not. Measured 2026-10-16: that single
    # bug accounts for 75 of the 78 charts whose headline did not match the trades drawn in the same file. So the
    # audit scores the header against every pnl-carrying row and reports the under-drawing separately.
    hidden = [e for e in events if isinstance(e, dict) and e.get("pnl_dollars") is not None]
    if hidden:
        _add(rec["findings"], "ERROR", "CHART_UNDERDRAWN", f"{len(hidden)} realised close(s) carrying P&L sit in EVENTS instead of TRADES, so the chart draws only {len(trades)} of its {len(trades) + len(hidden)} trades — the header counts them, the pictograms do not", dollars=_r(sum(float(h['pnl_dollars'] or 0.0) for h in hidden), 2))
        trades = trades + hidden
        events = [e for e in events if e.get("pnl_dollars") is None]
    hdr = parse_header(text)
    hdr["last_label"] = labels[-1] if labels else None
    man_path = html.parent / (html.stem + "_manifest.json")
    man = None
    if man_path.exists():
        try:
            man = json.loads(man_path.read_text())
        except Exception as exc:
            _add(rec["findings"], "WARN", "SRC_MANIFEST_UNREADABLE", f"{man_path.name}: {type(exc).__name__}")
    is_long = symside.endswith("_LONG")
    r = recompute(trades, events, close, is_long)
    rec["recomputed"] = r
    rec["header"] = hdr
    rec["manifest_metrics"] = (man or {}).get("metrics")
    if not trades:
        _add(rec["findings"], "WARN", "NO_TRADES", "the chart draws no trades at all — there is no gain to verify")
    rows, median_bps, multi, unusable = cost_stats(trades, events, is_long)
    r["cost_model_bps"] = _r(median_bps, 2)
    r["single_leg_rows"] = len(rows)
    file_utc = time.strftime("%Y-%m-%d %H:%M", time.gmtime(html.stat().st_mtime))
    check_gain(rec["findings"], hdr, r, man, fname, args.tol)
    check_rows(rec["findings"], trades, rows, median_bps, multi, unusable)
    check_lookahead(rec["findings"], trades, events, close, labels, NPZ_DIR if args.npz else None, file_utc)
    if args.npz:
        check_npz_fills(rec["findings"], trades, close, labels, symside, len(close))
    check_sources(rec["findings"], html, man, hdr, r, fname, symside, args.no_xlsx)
    for key, claimed, got, tol, fid in (
        ("bh_pct", hdr.get("bh_pct"), r["bh_pct"], 0.02, "BH_VS_SERIES"),
        ("wr_pct", hdr.get("wr_pct"), r["wr_pct"], 0.6, "WR_VS_LEDGER"),
        ("max_dd_pct", hdr.get("max_dd_pct"), r["max_dd_pct"], 0.6, "DD_VS_LEDGER"),
        ("tim_pct", hdr.get("tim_pct"), r["tim_pct"], 3.0, "TIM_VS_LEDGER"),
    ):
        if claimed is not None and got is not None and abs(float(claimed) - float(got)) > tol:
            _add(rec["findings"], "WARN", fid, f"header {key} {float(claimed):+.2f} vs ledger-recomputed {float(got):+.2f}")
    if hdr.get("fork_note"):
        _add(rec["findings"], "WARN", "HEADER_FORK_FIXED", f"the generator itself logged a metrics/closes fork: metrics={hdr['fork_note']['metrics']} closes={hdr['fork_note']['closes']}")
    ladder = "/".join(f"+{k}={v}" for k, v in r["cost_ladder"].items())
    if median_bps is None:
        pass
    elif median_bps < 0.5:
        _add(rec["findings"], "WARN", "LA_ZERO_COST", f"this chart deducts {median_bps:.2f} bps per round turn — no commission, no slippage, no spread is modelled at all. Net gain with cost added: {ladder}")
    else:
        _add(rec["findings"], "INFO", "LA_COST_MODEL", f"{median_bps:.2f} bps round turn already deducted per trade; net with further cost added: {ladder}")
    sev = max((SEV_ORDER[f["sev"]] for f in rec["findings"]), default=0)
    rec["verdict"] = {2: "FAIL", 1: "WARN", 0: "PASS"}[sev]
    rec["secs"] = round(time.time() - t0, 2)
    return rec


def recover_set(symside, want_md5):
    """The override set that produced a board — progress JSON first, then the orchestrator's per_sym_history."""
    for d in list(PROGRESS_DIRS) + sorted(Path.home().glob("v15_*/progress")):
        if not Path(d).is_dir():
            continue
        for p in sorted(Path(d).glob(f"{symside}_v14_progress.json*")):
            try:
                j = json.loads(p.read_text())
            except Exception:
                continue
            ov = j.get("cumulative_overrides") or j.get("hustler_overrides")
            if isinstance(ov, dict) and ov and (not want_md5 or _overrides_md5(ov) == want_md5):
                return dict(ov), str(p)
    db = Path(os.environ.get("PER_SYM_STORE_DB") or (ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
    if db.exists():
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for (ovj,) in conn.execute("select overrides_json from per_sym_history where sym_side=? order by updated_at desc", (symside.upper(),)):
                try:
                    ov = json.loads(ovj) if ovj else None
                except Exception:
                    continue
                if isinstance(ov, dict) and ov and (not want_md5 or _overrides_md5(ov) == want_md5):
                    return dict(ov), f"{db.name}:per_sym_history"
        except Exception:
            pass
        finally:
            conn.close()
    return None, None


def rerun_one(rec, html, args, parse_name):
    """Actually re-execute the generator on the published set and diff fresh vs published."""
    man_path = html.parent / (html.stem + "_manifest.json")
    if not man_path.exists():
        rec["rerun"] = {"ok": False, "why": "no manifest — the published set cannot be identified"}
        return
    man = json.loads(man_path.read_text())
    ov, src = recover_set(rec["symside"], str(man.get("overrides_md5") or ""))
    if not ov:
        rec["rerun"] = {"ok": False, "why": f"no set on this host whose md5 == manifest overrides_md5 {man.get('overrides_md5')}"}
        return
    fname = parse_name(html.name) or {}
    wd = int(fname.get("window_days") or 30)
    out_name = f".rerun_{rec['symside']}_{int(time.time())}.html"
    try:
        from tools.opt.hires_chart import generate_hires

        p = generate_hires(rec["symside"], dict(ov), wd, out_name=out_name)
    except Exception as exc:
        rec["rerun"] = {"ok": False, "why": f"{type(exc).__name__}: {str(exc)[:160]}", "set_from": src}
        return
    p = Path(p)
    text = p.read_text(errors="replace")
    fresh_tr = parse_js_const(text, "TRADES") or []
    fresh_ev = parse_js_const(text, "EVENTS") or []
    fresh_cl = parse_js_const(text, "CLOSE") or []
    fr = recompute(fresh_tr, fresh_ev, fresh_cl, rec["symside"].endswith("_LONG"))
    pub = rec["recomputed"]
    rec["rerun"] = {
        "ok": True,
        "set_from": src,
        "fresh": {"gain_pct": fr["gain_pct_event_peak"], "trades": fr["trades"], "bh_pct": fr["bh_pct"], "bars": len(fresh_cl)},
        "published": {"gain_pct": pub["gain_pct_event_peak"], "trades": pub["trades"], "bh_pct": pub["bh_pct"], "bars": len(parse_js_const(html.read_text(errors="replace"), "CLOSE") or [])},
    }
    a, b = fr["gain_pct_event_peak"], pub["gain_pct_event_peak"]
    if a is not None and b is not None and abs(a - b) > args.rerun_tol:
        _add(rec["findings"], "WARN", "RERUN_GAIN_DRIFT", f"the same set re-measures {a:+.2f}% today against the published {b:+.2f}% — the window slid or the NPZ changed under the board")
        rec["verdict"] = "FAIL" if rec["verdict"] == "FAIL" else "WARN"
    if not args.keep_rerun:
        try:
            p.unlink()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="re-run every published chart's arithmetic and prove its trades are tradeable")
    ap.add_argument("--dir", default=str(SHEET_DIR))
    ap.add_argument("--symside", default="", help="one sym_side (e.g. APO_SHORT) or a comma list")
    ap.add_argument("--glob", default="*_30d_matrix.html")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--latest-only", action="store_true", help="newest chart per sym_side only (what live actually reads)")
    ap.add_argument("--tol", type=float, default=0.01, help="gain tolerance in percentage points")
    ap.add_argument("--npz", action="store_true", help="also verify every fill against its bar's [low, high] (needs the NPZ — S1/S2)")
    ap.add_argument("--no-xlsx", action="store_true", help="skip the xlsx cross-check")
    ap.add_argument("--rerun", action="store_true", help="re-execute generate_hires on the published set and diff (S1/S2 only)")
    ap.add_argument("--rerun-tol", type=float, default=2.5, help="BIBLE board-nondeterminism tolerance for the rerun diff")
    ap.add_argument("--keep-rerun", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every finding, not just the failing charts")
    ap.add_argument("--fail-on", default="", choices=["", "ERROR", "WARN"], help="exit 1 when any chart reaches this severity")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    from tools.v15_final_naming import parse_final_matrix_name as parse_name

    d = Path(args.dir)
    if not d.is_dir():
        log(f"[audit] {d} is not a directory")
        return 2
    charts = sorted(d.glob(args.glob))
    if args.symside:
        want = {s.strip().upper() for s in args.symside.split(",") if s.strip()}
        charts = [c for c in charts if (c.name.split("_bh")[0]).upper() in want]
    if args.latest_only:
        best = {}
        for c in charts:
            k = c.name.split("_bh")[0]
            if k not in best or c.stat().st_mtime > best[k].stat().st_mtime:
                best[k] = c
        charts = [best[k] for k in sorted(best)]
    if args.limit:
        charts = charts[: args.limit]
    if not charts:
        log(f"[audit] no charts matched {args.glob} in {d}")
        return 1
    tag = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    log(f"[audit] {len(charts)} chart(s) in {d} · npz={args.npz} xlsx={not args.no_xlsx} rerun={args.rerun} host={os.uname().nodename}")
    recs, counts, by_id = [], {"PASS": 0, "WARN": 0, "FAIL": 0, "UNPARSEABLE": 0, "UNREADABLE": 0}, {}
    t0 = time.time()
    for i, c in enumerate(charts, 1):
        rec = audit_one(c, args, parse_name)
        if args.rerun and rec["verdict"] not in ("UNREADABLE", "UNPARSEABLE"):
            try:
                rerun_one(rec, c, args, parse_name)
            except Exception as exc:
                rec["rerun"] = {"ok": False, "why": f"{type(exc).__name__}: {str(exc)[:160]}"}
        recs.append(rec)
        counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
        for f in rec["findings"]:
            if f["sev"] != "INFO":
                by_id[f["id"]] = by_id.get(f["id"], 0) + 1
        if args.verbose or rec["verdict"] == "FAIL":
            r = rec.get("recomputed") or {}
            log(f"[{i}/{len(charts)}] {rec['verdict']:5s} {c.name}  header={(rec.get('header') or {}).get('gain_pct')} ledger={r.get('gain_pct_event_peak')} trades={r.get('trades')}")
            for f in rec["findings"]:
                if args.verbose or f["sev"] == "ERROR":
                    log(f"         {f['sev']:5s} {f['id']}: {f['msg']}")
    out = Path(args.out) if args.out else OUT_DIR / f"v15_chart_gain_audit_{tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tag": tag, "host": os.uname().nodename, "dir": str(d), "args": vars(args), "counts": counts, "findings_by_id": dict(sorted(by_id.items(), key=lambda kv: -kv[1])), "charts": recs}, indent=1, default=str))
    log("")
    log(f"[summary] {len(charts)} charts in {time.time() - t0:.1f}s · " + " · ".join(f"{k} {v}" for k, v in counts.items() if v))
    for fid, n in sorted(by_id.items(), key=lambda kv: -kv[1]):
        log(f"[summary] {n:5d}  {fid}")
    log(f"[summary] report {out}")
    worst = max((SEV_ORDER[f["sev"]] for r in recs for f in r["findings"]), default=0)
    if args.fail_on and worst >= SEV_ORDER[args.fail_on]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
