"""v15_page_truth — the number a published board is NAMED with is the number its own chart page proves (USER 2026-10-18).

USER: "make sure v15_pilot writes them correctly immediately from now on so we dont need double checks."

THE DEFECT THIS CLOSES. `v15_pilot` builds the filename from `final_gain` — its own fresh VECTOR eval — while the chart
page is drawn by `tools.opt.hires_chart.generate_hires`, which runs a SECOND evaluation (the ledger path) and verifies its
own header against its own arrays. Two evaluations, one filename: the name and the page can disagree, and on 2026-10-18 a
sweep over 8,192 pages found 1,491 headers and 1,557 names that disagreed with the trades on their own page (worst:
`gain13p62` on a page proving −11.83%).

Nothing here measures anything new: the truth comes from `tools.opt.chart_truth.recompute`, the project's own function,
over the page's own `const TRADES` / `const EVENTS` / `const CLOSE`, with the E2 semantic for the trade count (a realised
close is any row carrying `pnl_dollars`, label irrelevant, FINAL_MTM excluded) — the same definition
`board_name_truth_guard` enforces after the fact. This module simply makes the writer use it BEFORE naming, so the guard
has nothing left to catch.

Kill switch: V15_NAME_FROM_PAGE=0 restores the old behaviour (name from the pilot's own number).
"""
from __future__ import annotations

import glob
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART_DIRS = ("data/reports/charts_1M", "data/reports/charts_1Y", "SPREADSHEETS")


def enabled() -> bool:
    return os.environ.get("V15_NAME_FROM_PAGE", "1") != "0"


def page_truth_from_text(text: str, symside: str):
    """The verified metrics of one chart page, or None when the page carries no evidence."""
    try:
        from tools.opt import chart_truth as CT

        trades = CT.parse_js_const(text, "TRADES")
        events = CT.parse_js_const(text, "EVENTS") or []
        close = CT.parse_js_const(text, "CLOSE")
        if trades is None or close is None:
            return None
        t = CT.recompute(trades, events, close, str(symside).upper().endswith("_LONG"))
        hidden = [e for e in events if isinstance(e, dict) and e.get("pnl_dollars") is not None and not e.get("seeded_prewindow")]
        t["trades"] = len(trades) + len(hidden)  # E2: a realised close in EVENTS is still a trade
        t["hidden_realised_in_events"] = len(hidden)
        return t
    except Exception:
        return None


def newest_page(symside: str, window_days: int = 30, max_age_s: float = 7200.0, roots=None):
    """The freshest chart page for this board written by THIS run (default: within 2 h), or None."""
    pats = [f"{symside}_*{int(window_days)}d_matrix.html", f"{symside}_*{int(window_days)}D_REAL_ZOOMABLE*.html",
            f"{symside}_{int(window_days)}D_REAL_ZOOMABLE*.html"]
    best, best_mt = None, 0.0
    now = time.time()
    for d in (roots or CHART_DIRS):
        for pat in pats:
            for f in glob.glob(str((ROOT / d) / pat)):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if now - mt > max_age_s:
                    continue
                if mt > best_mt:
                    best, best_mt = Path(f), mt
    return best


def truth_for(symside: str, window_days: int = 30, max_age_s: float = 7200.0):
    """(truth_dict, page_path) from the freshest recent page for this board, or (None, None)."""
    p = newest_page(symside, window_days, max_age_s)
    if p is None:
        return None, None
    try:
        t = page_truth_from_text(p.read_text(errors="ignore"), symside)
    except Exception:
        return None, None
    return (t, p) if t else (None, None)


def reconcile(symside: str, gain, trades, window_days: int = 30, tol: float = 0.01, log=print):
    """Return (gain, trades, source, note) — the page's own numbers when a fresh page exists, else the caller's.

    The disagreement is LOGGED every time, because that delta is the two-engines defect itself and it must never be
    silent again: the previous guard wrote the truth into a side field and every consumer kept reading the old number.
    """
    if not enabled():
        return float(gain), int(trades), "caller(V15_NAME_FROM_PAGE=0)", ""
    t, p = truth_for(symside, window_days)
    if not t or t.get("gain_pct") is None:
        return float(gain), int(trades), "caller(no fresh page)", ""
    pg, pt = float(t["gain_pct"]), int(t["trades"])
    dg, dt_ = pg - float(gain), pt - int(trades)
    if abs(dg) <= tol and dt_ == 0:
        return pg, pt, "page(agrees)", ""
    note = (f"NAME FROM PAGE TRUTH: {symside} gain {float(gain):+.4f}->{pg:+.4f} ({dg:+.4f}) "
            f"trades {int(trades)}->{pt} ({dt_:+d}) page={p.name}")
    try:
        log(f"[page-truth] {note}", flush=True) if "flush" in getattr(log, "__code__", type("x", (), {"co_varnames": ()})).co_varnames else log(f"[page-truth] {note}")
    except Exception:
        pass
    return pg, pt, "page(corrected)", note


def metrics_patch(symside: str, fresh: dict | None, window_days: int = 30) -> dict:
    """The dict to MERGE into a publish manifest's metrics so `gain_pct`/`trades` are the page's own numbers.

    Cause #1 of the 2026-10-18 forensics: `board_name_truth_guard` wrote the true gain into
    `nolies_correction.true_gain_pct` and left `metrics.gain_pct` alone, and every consumer
    (`v15_settings_history` -> `per_sym_history` -> `v15_published_selection` -> go-live -> `per_sym_active`) reads
    `metrics.gain_pct`. 203 of 566 applied claims were contradicted by their own manifest. Writing the page truth into
    `metrics` at publish time removes the whole chain of inheritance. The vector numbers are kept beside it under
    `metrics_vector_before_page_truth` so nothing is lost and the two engines stay comparable.

    Returns {} on ANY problem — a naming/metrics helper may never break a publish.
    """
    try:
        if not enabled():
            return {}
        t, p = truth_for(symside, window_days)
        if not t or t.get("gain_pct") is None or t.get("trades") is None:
            return {}
        out = {"gain_pct": float(t["gain_pct"]), "trades": int(t["trades"]),
               "page_truth": {"page": p.name, "gain_pct": float(t["gain_pct"]), "trades": int(t["trades"]),
                              "max_dd_pct": t.get("max_dd_pct"), "tim_pct": t.get("tim_pct"), "wr_pct": t.get("wr_pct"),
                              "peak_capital": t.get("peak_capital"), "hidden_realised_in_events": t.get("hidden_realised_in_events"),
                              "source": "tools.opt.chart_truth.recompute over the page's own TRADES/EVENTS/CLOSE"},
               "metrics_vector_before_page_truth": {"gain_pct": (fresh or {}).get("gain_pct"), "trades": (fresh or {}).get("trades"),
                                                    "max_dd_pct": (fresh or {}).get("max_dd_pct"), "tim_pct": (fresh or {}).get("tim_pct")}}
        for k in ("max_dd_pct", "tim_pct", "wr_pct"):
            if t.get(k) is not None:
                out[k] = float(t[k])
        return out
    except Exception:
        return {}
