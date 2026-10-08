#!/usr/bin/env python3
"""v15_missed_trend — MISSED-TREND revision round (USER 2026-10-08: AGLDUSDT_SHORT autopsy).

A finished 30D board can be "positive" while missing the move that mattered: AGLDUSDT_SHORT
2026-10-08 published gain +2.95% / TIM 1.94% / 17 one-bar scalps while price slid -15.97%
from the 0.2292 top (bar 1757) to 0.1926 (bar 2874) with no position for the first 201 bars.
The greedy sweep found 0 positive ENTRY/REENTRY/EXIT rows, compliance repair could not lift
TIM to [20,80] with single-switch steps, the board published BEST_EFFORT — and the
scheduler treats BEST_EFFORT as TERMINAL, so no revision round ever re-checks entries,
exits, filters or the missed move. The daily chain learns cross-sym averages, but nothing
per-sym ever learns "this sym missed a trend".

This tool closes that loop with ZERO locked-file edits (BACKTEST_BIBLE §80):

  scan    engine-free detection from a published zoomable chart (.html) or a progress
          JSON + NPZ: finds favorable excursions while flat, capture ratio, churn
          evidence. Runs on the Mac.
  screen  fleet-only engine screen (like tools/v15_trade_autopsy_run.py): every ENTRY_*
          + REENTRY_* template row + premature-exit HOLD rows + SOFTEN/ENTRY_PATH
          bools vs the final set, attributed to the missed trends, then an
          engine-verified surgical combo (kept only when valid AND gain up AND TIM
          not down). Writes {SS}_missed_trend.json + a V15_START_OVERRIDES-ready
          {SS}_missed_trend_base.json (same schema as tools/v15_autopsy_first.py, so
          the scheduler's existing launch env ingests it with no code change).
  requeue move the BEST_EFFORT/NO_TRADES published final to history, archive the stale
          progress file (its rows were measured for the old set), optionally install
          the trend base into ~/v15_autopsy_first/ — the scheduler then relaunches a
          fresh need30 revision round from the repaired set. Explicit, per-sym,
          reversible; never touches the scheduler, the pilot or the templates.

NO-LIES: every delta is a real evaluate_prepared_sanitized number (§19); detection is
pure arithmetic on the published ledger + close path; a trend with no capturing switch
is reported as a PROPOSED_NEW_SWITCH spec for the §71 pipeline, never fabricated.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TIM_FLOOR, TIM_CEIL = 20.0, 80.0
MISSED_TREND_MIN_PCT = 8.0
CAPTURE_COVER_MAX = 0.35
REVISE_CAPTURE_RATIO = 0.35
_SCREEN_PREP = None


def _screen_w(ov):
    """Fork-pool worker (MODULE level: a run_screen closure is unpicklable and fails
    all 994 futures with 'Can't get local object' — proven on s2 2026-10-08)."""
    from tools import v15_trade_autopsy as TA
    from tools.opt import v12_pilot as VP

    r = VP.evaluate_prepared_sanitized(_SCREEN_PREP, ov, 30, True)
    return {
        "gain": r.get("gain_pct"),
        "trades": r.get("trades"),
        "valid": r.get("valid"),
        "tim": r.get("tim_pct"),
        "dd": r.get("max_dd_pct"),
        "rows": TA.scaled_rows(r),
    }


ENTRY_TABS = (
    "ENTRY_REVERSAL_BOUNCE",
    "ENTRY_BREAKOUT_CHANNEL",
    "ENTRY_CONFIRMATION_GATES",
)
REENTRY_TABS = ("REENTRY_WINDOWED", "REENTRY_ADAPTIVE")
EXIT_TABS = (
    "EXIT_STRUCTURAL",
    "EXIT_VELOCITY",
    "REDUCE_PROFIT_LOCK",
    "REDUCE_SIGNAL_RATER",
)
HOLD_TOKENS = (
    "MIN_HOLD",
    "MIN_TFS",
    "MIN_TF",
    "COOLDOWN",
    "TARGET_TF",
    "STOP_TF",
    "REQUIRE_PROFIT",
    "REQUIRE_GAIN",
    "MIN_GAIN",
    "VELOCITY",
)
REQUEUE_VERDICTS = ("BEST_EFFORT", "NO_TRADES")


def fav_move(p0, p1, is_long):
    if not p0:
        return 0.0
    return ((p1 - p0) / p0 * 100.0) * (1 if is_long else -1)


def _held_bars(n, trades):
    held = [False] * n
    for t in trades or []:
        try:
            be, bx = int(t.get("be", -1)), int(t.get("bx", -1))
        except Exception:
            continue
        for i in range(max(0, be), min(n, bx + 1)):
            held[i] = True
    return held


def find_missed_trends(close, trades, is_long, min_pct=MISSED_TREND_MIN_PCT):
    """Non-overlapping favorable legs over the FULL series (single pass: a new extreme
    finalizes the previous leg). One-bar scalps inside a slide must not fragment it —
    AGLD's 1117-bar -15.97% slide (bars 1757->2874) is one trend even though 17 scalps
    sit inside it; cover says how much of it held a position. Returns biggest-first
    list of {b0, b1, move_pct, span_bars, cover, late_by_bars}."""
    n = len(close)
    if n < 3:
        return []
    held = _held_bars(n, trades)
    entries = sorted(
        int(t.get("be", -1)) for t in trades or [] if t.get("be") is not None
    )
    out = []
    ext, extb, best, bb0, bb1 = close[0], 0, 0.0, 0, 0
    for j in range(1, n):
        if (close[j] > ext) if not is_long else (close[j] < ext):
            if best >= min_pct:
                out.append((bb0, bb1, best))
            ext, extb, best, bb0, bb1 = close[j], j, 0.0, j, j
        else:
            m = fav_move(ext, close[j], is_long)
            if m > best:
                best, bb0, bb1 = m, extb, j
    if best >= min_pct:
        out.append((bb0, bb1, best))
    res = []
    for bb0, bb1, best in out:
        span = bb1 - bb0 + 1
        nxt = next((x for x in entries if x > bb0), None)
        res.append(
            {
                "b0": bb0,
                "b1": bb1,
                "move_pct": round(best, 4),
                "span_bars": span,
                "cover": (
                    round(sum(1 for i in range(bb0, bb1 + 1) if held[i]) / span, 4)
                    if span > 0
                    else 0.0
                ),
                "late_by_bars": (nxt - bb0) if nxt is not None else None,
            }
        )
    res.sort(key=lambda t: -t["move_pct"])
    return res


def churn_evidence(trades):
    """Median hold + dominant exit family (AGLD: median 1 bar, EXIT_VELOCITY_WT 14/17)."""
    if not trades:
        return {"n": 0, "median_hold": None, "top_exit": None, "top_share": 0.0}
    holds = sorted(int(t.get("bx", 0) - t.get("be", 0)) for t in trades)
    fams = Counter(
        str(t.get("exit_reason") or "?").split(" ")[0].split("(")[0].split(":")[0][:48]
        for t in trades
    )
    (top, cnt), n = fams.most_common(1)[0], len(trades)
    return {
        "n": n,
        "median_hold": holds[n // 2],
        "top_exit": top,
        "top_share": round(cnt / n, 4),
    }


def should_revise(tim_pct, gain_pct, trends, min_pct=MISSED_TREND_MIN_PCT):
    """TIM below floor + a big move captured <35% of the time = revision round."""
    reasons = []
    if tim_pct is not None and tim_pct < TIM_FLOOR:
        reasons.append(f"TIM_LOW {tim_pct:.2f}<{TIM_FLOOR:.0f}")
    big = [
        t for t in trends if t["move_pct"] >= min_pct and t["cover"] < CAPTURE_COVER_MAX
    ]
    if big:
        t = big[0]
        reasons.append(
            f"MISSED_TREND {t['move_pct']:+.2f}% bars {t['b0']}->{t['b1']} cover {t['cover'] * 100:.1f}% late {t['late_by_bars']}"
        )
    flag = bool(
        len(reasons) >= 2 or (big and tim_pct is not None and tim_pct < TIM_FLOOR)
    )
    if big and gain_pct is not None:
        ratio = gain_pct / big[0]["move_pct"] if big[0]["move_pct"] else 0.0
        if ratio < REVISE_CAPTURE_RATIO:
            reasons.append(
                f"LOW_CAPTURE gain {gain_pct:+.2f} / move {big[0]['move_pct']:.2f} = {ratio:.2f}"
            )
            flag = True
    return flag, reasons, (big[0] if big else None)


def html_inputs(path):
    """Parse a published zoomable chart into close/labels/trades/metrics (Mac-safe)."""
    t = Path(path).read_text()
    close = [
        float(x)
        for x in re.search(r"const CLOSE = \[(.*?)\];", t, re.S).group(1).split(",")
        if x.strip()
    ]
    labels = [
        x.strip().strip('"')
        for x in re.search(r"const LABELS = \[(.*?)\];", t, re.S).group(1).split(",")
    ]
    trades = json.loads(re.search(r"const TRADES = (\[.*?\]);", t, re.S).group(1))
    head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:3000]
    meta = {}
    for k, pat in (
        ("gain", r"gain ([-\d.]+)%"),
        ("bh", r"BH ([-\d.]+)%"),
        ("trades_n", r"trades (\d+)"),
        ("tim", r"TIM ([-\d.]+)%"),
        ("dd", r"DD ([-\d.]+)%"),
        ("wr", r"WR ([-\d.]+)%"),
    ):
        m = re.search(pat, head)
        meta[k] = float(m.group(1)) if m else None
    rows = [
        {
            "be": x["bar_entry"],
            "bx": x["bar_exit"],
            "pnl": x.get("pnl_pct", 0.0),
            "pnl_pct": x.get("pnl_pct", 0.0),
            "entry_reason": x.get("entry_reason", ""),
            "exit_reason": x.get("exit_reason", ""),
            "entry_price": x.get("entry_price", 0.0),
            "exit_price": x.get("exit_price", 0.0),
        }
        for x in trades
    ]
    return {"close": close, "labels": labels, "trades": rows, "meta": meta}


def scan_report(
    symside, close, trades, tim_pct, gain_pct, labels=None, min_pct=MISSED_TREND_MIN_PCT
):
    """Pure detection report (Mac-safe). trends carry time strings when labels given."""
    is_long = symside.upper().endswith("_LONG")
    trends = find_missed_trends(close, trades, is_long, min_pct)
    if labels:
        for t in trends:
            t["t0"] = labels[t["b0"]] if t["b0"] < len(labels) else None
            t["t1"] = labels[t["b1"]] if t["b1"] < len(labels) else None
            t["p0"] = close[t["b0"]]
            t["p1"] = close[t["b1"]]
    flag, reasons, big = should_revise(tim_pct, gain_pct, trends, min_pct)
    return {
        "symside": symside,
        "is_long": is_long,
        "n_bars": len(close),
        "n_trades": len(trades or []),
        "tim_pct": tim_pct,
        "gain_pct": gain_pct,
        "revise": flag,
        "reasons": reasons,
        "trends": trends,
        "churn": churn_evidence(trades),
    }


def _is_hold_row(c):
    return c.get("tab") in EXIT_TABS and any(
        t in str(c.get("switch", "")).upper() for t in HOLD_TOKENS
    )


def trend_candidates(symside, defaults, pilot):
    """ENTRY_* + REENTRY_* rows + premature-exit HOLD rows + SOFTEN/ENTRY_PATH bools."""
    from tools.v15_repair_driver import candidates_for
    from tools.v15_trade_autopsy_run import AUTOPSY_DENY, AUTOPSY_DENY_PREFIX

    out = []
    for c in candidates_for(symside, defaults, pilot):
        if c.get("blocked"):
            continue
        sw = str(c.get("switch", ""))
        if sw.strip() in AUTOPSY_DENY or sw.strip().startswith(AUTOPSY_DENY_PREFIX):
            continue
        if any(
            t in sw.upper() for t in ("SIZE_MULT", "SIZING", "POSITION_SIZE", "_SIZE")
        ):
            continue
        if c.get("tab") in ENTRY_TABS + REENTRY_TABS or _is_hold_row(c):
            out.append(c)
    cur = {}
    for k, dv in defaults.items():
        if (
            not isinstance(dv, bool)
            or k == "SIMPLE_PRICE_GT0_ENABLED"
            or k in pilot.DEAD_VEC_SWITCHES
            or k in pilot.LIVE_ONLY_SWITCHES
        ):
            continue
        if dv and any(t in k for t in pilot._ADAPT_SOFTEN_TOKENS):
            out.append(
                {
                    "tab": "SOFTEN",
                    "row": None,
                    "switch": k,
                    "cand": "False",
                    "ov": {k: False},
                }
            )
        elif (
            not dv
            and k.endswith("_ENABLED")
            and any(t in k for t in pilot._ADAPT_ENTRY_TOKENS)
            and not any(t in k for t in pilot._ADAPT_SOFTEN_TOKENS)
        ):
            out.append(
                {
                    "tab": "ENTRY_PATH",
                    "row": None,
                    "switch": k,
                    "cand": "True",
                    "ov": {k: True},
                }
            )
    return out


def verify_combo(
    ranked, base_ov, apply_eval, base_gain, base_tim, max_k=8, tim_tol_pp=0.0
):
    """Greedy engine-verified combo: keep a switch only when the set stays valid AND
    gain rises AND TIM does not fall (TIM is the fault being repaired). ranked =
    [(label, {switch: value} delta)]; apply_eval(full_ov) -> (gain, trades, tim,
    valid). Pure sequencing — the evaluator is injected. Returns kept, gain, tim,
    log, final_ov."""
    cur, cur_gain, cur_tim, kept, log = dict(base_ov), base_gain, base_tim, [], []
    for lab, delta in ranked:
        trial = {**cur, **delta}
        try:
            g, tr, tm, va = apply_eval(trial)
        except Exception as e:
            log.append({"switch": lab, "kept": False, "error": str(e)[:120]})
            continue
        ok = (
            bool(va)
            and g is not None
            and g > (cur_gain if cur_gain is not None else -1e9) + 1e-6
            and (tm if tm is not None else -1e9)
            >= (cur_tim if cur_tim is not None else -1e9) - tim_tol_pp
        )
        log.append(
            {
                "switch": lab,
                "gain_before": cur_gain,
                "gain_after": g,
                "trades": tr,
                "tim": tm,
                "valid": va,
                "kept": bool(ok),
            }
        )
        if ok:
            kept.append((lab, dict(delta)))
            cur, cur_gain, cur_tim = trial, g, tm
        if len(kept) >= max_k:
            break
    return kept, cur_gain, cur_tim, log, cur


def missing_spec(symside, trend, labels=None, close=None):
    """Concrete PROPOSED_NEW_SWITCH text when no existing switch captures the trend."""
    side = "SHORT" if symside.upper().endswith("_SHORT") else "LONG"
    trig = (
        "first lower high after the HTF rollover"
        if side == "SHORT"
        else "first higher low after the HTF rollover"
    )
    t0 = (
        labels[trend["b0"]]
        if labels and trend["b0"] < len(labels)
        else f"bar {trend['b0']}"
    )
    t1 = (
        labels[trend["b1"]]
        if labels and trend["b1"] < len(labels)
        else f"bar {trend['b1']}"
    )
    px = ""
    if close:
        px = f" ({close[trend['b0']]:.4f} -> {close[trend['b1']]:.4f})"
    return {
        "status": "PROPOSED_NEW_SWITCH",
        "trend": trend,
        "rule": f"{side} entry trigger on the {trig} at/after {t0}{px}: no template ENTRY/REENTRY/HOLD switch opens inside bars {trend['b0']}->{trend['b1']} ({trend['move_pct']:+.2f}% by {t1}); needs a §71 switch (wire code first, then rows), never a param tweak of an unrelated gate",
    }


def priors_partial(
    symside, verified_log, template_dir="SPREADSHEETS/TEMPLATE_FINAL_NORM"
):
    """Verified combo deltas in the exact merge_partials shape (same contract as
    tools/v15_autopsy_priors.fold: one value per sym_side per tab/switch=value)."""
    from tools.v15_autopsy_priors import (
        CAT_SIDES,
        cat_side_of,
        switch_tabs,
        PRIOR_DENY,
        PRIOR_DENY_PREFIX,
    )

    cs = cat_side_of(symside)
    tabs = switch_tabs(cs, template_dir)
    agg = {c: {} for c in CAT_SIDES}
    best = {}
    for v in verified_log or []:
        if not v.get("kept") or not v.get("valid"):
            continue
        try:
            d = float(v["gain_after"]) - float(v["gain_before"])
        except Exception:
            continue
        if d <= 1e-9:
            continue
        sw = str(v["switch"]).split("=", 1)[0].strip()
        if sw in PRIOR_DENY or sw.startswith(PRIOR_DENY_PREFIX):
            continue
        tab = tabs.get(sw) or v.get("tab")
        if not tab or tab in ("SOFTEN", "ENTRY_PATH"):
            continue
        k = f"{tab}\tswitch\t{str(v['switch']).strip()}"
        best[k] = max(best.get(k, 0.0), d)
    for k, d in best.items():
        agg[cs].setdefault(k, []).append(round(d, 6))
    out = dict(agg)
    out["_symsides"] = {c: ([symside] if c == cs and best else []) for c in CAT_SIDES}
    out["_source"] = (
        "missed_trend combo_verified (engine gain_after-gain_before on the published final set)"
    )
    return out


def run_screen(
    symside,
    set_json,
    out_dir,
    workers=6,
    max_combo=8,
    min_pct=MISSED_TREND_MIN_PCT,
    tim_tol_pp=0.0,
):
    """Fleet-only engine screen (needs full NPZ — never on the Mac). set_json = the
    published final set ({switch: value} or {final/cumulative_overrides: {...}})."""
    import concurrent.futures as cf
    import multiprocessing as mp
    import v15_pilot as P
    from tools import v15_trade_autopsy as TA
    from tools.opt import v12_pilot as VP

    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = json.load(open(os.path.expanduser(set_json)))
    base_ov = P.sanitize_overrides(
        d.get("final", {}).get("overrides", d.get("cumulative_overrides", d)),
        P.get_defaults_for_symside(symside),
    )[0]
    is_long = symside.upper().endswith("_LONG")
    global _SCREEN_PREP
    _SCREEN_PREP = VP.prepare_batch(symside, 30)
    prep = _SCREEN_PREP
    npz = prep.get("npz_prepared") or {}

    b = _screen_w(base_ov)
    close = [float(x) for x in npz.get("close")]
    base_rows = TA.classify(b["rows"], close, is_long)
    rep = scan_report(
        symside,
        close,
        [
            {
                "be": t["be"],
                "bx": t["bx"],
                "pnl_pct": t.get("pnl_pct", 0.0),
                "exit_reason": t.get("exit_reason", ""),
            }
            for t in base_rows
        ],
        b["tim"],
        b["gain"],
    )
    rep.update(
        {
            "set_src": set_json,
            "base": {k: b[k] for k in ("gain", "trades", "valid", "tim", "dd")},
            "n_base_trades": len(base_rows),
        }
    )
    if not rep["revise"]:
        rep.update({"status": "NO_REVISION", "secs": round(time.time() - t0, 1)})
        (out / f"{symside}_missed_trend.json").write_text(
            json.dumps(rep, indent=1, default=str)
        )
        print(
            f"[missed-trend] {symside} NO_REVISION ({'; '.join(rep['reasons']) or 'no fault'}; {rep['secs']}s)",
            flush=True,
        )
        return rep
    moves = [
        {"b0": t["b0"], "b1": t["b1"], "move_pct": t["move_pct"]} for t in rep["trends"]
    ]
    cands = trend_candidates(symside, P.get_defaults_for_symside(symside), P)
    san = lambda ov: P.sanitize_overrides(ov, P.get_defaults_for_symside(symside))[0]
    items = []
    for c in cands:
        v = san({**base_ov, **c["ov"]})
        if v == base_ov:
            continue
        items.append((f"{c['switch']}={c['cand']}", v, c))
    pool = cf.ProcessPoolExecutor(
        max_workers=workers, mp_context=mp.get_context("fork")
    )
    list(pool.map(abs, range(workers * 2)))
    attrs, cards, meta = {}, {}, {}
    eval_errors, eval_first_error = 0, ""
    try:
        futs = {pool.submit(_screen_w, v): (lab, c) for lab, v, c in items}
        for f in cf.as_completed(futs):
            lab, c = futs[f]
            try:
                r = f.result(timeout=120)
            except Exception as e:
                eval_errors += 1
                if not eval_first_error:
                    eval_first_error = f"{type(e).__name__}: {str(e)[:200]}"
                continue
            a = TA.attribute(base_rows, r["rows"], moves)
            if not a["eff"] and not a["added"]:
                continue
            attrs[lab] = a
            cards[lab] = {
                **TA.scorecard(base_rows, a),
                "d_gain": (r["gain"] or 0) - (b["gain"] or 0),
                "valid": r["valid"],
                "trades": r["trades"],
                "tim": r["tim"],
                "tab": c["tab"],
                "ov": c["ov"],
            }
            meta[lab] = c
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    ranked = sorted(
        ((lab, meta[lab]["ov"]) for lab in attrs),
        key=lambda x: (
            cards[x[0]]["captured"],
            cards[x[0]]["valid"] and cards[x[0]]["d_gain"] > 0,
            cards[x[0]]["d_gain"],
            cards[x[0]]["net"],
        ),
        reverse=True,
    )

    def _apply(full_ov):
        r = _screen_w(san(full_ov))
        return r["gain"], r["trades"], r["tim"], r["valid"]

    kept, combo_gain, combo_tim, log, final_ov = verify_combo(
        ranked, base_ov, _apply, b["gain"], b["tim"], max_combo, tim_tol_pp
    )
    verified = []
    for e in log:
        if e.get("kept") and e["switch"] in meta:
            e["tab"] = meta[e["switch"]]["tab"]
            verified.append(e)
    rep.update(
        {
            "status": "RESCUED" if kept else "NO_CAPTURE",
            "n_candidates": len(items),
            "n_effective": len(attrs),
            "n_eval_errors": eval_errors,
            "eval_first_error": eval_first_error,
            "effective": sorted(attrs),
            "top_switches": sorted(
                (
                    {"switch": k, **{kk: vv for kk, vv in v.items() if kk != "ov"}}
                    for k, v in cards.items()
                ),
                key=lambda x: (-x["captured"], -x["d_gain"]),
            )[:40],
            "captures": sorted(
                (
                    {
                        "switch": k,
                        "captured": v["captured"],
                        "added_pnl": v["added_pnl"],
                    }
                    for k, v in cards.items()
                    if v["captured"]
                ),
                key=lambda x: -x["added_pnl"],
            )[:15],
            "combo_verified": verified,
            "combo_gain": combo_gain,
            "combo_tim": combo_tim,
            "combo_overrides": {
                k: v for k, v in final_ov.items() if base_ov.get(k) != v
            },
            "secs": round(time.time() - t0, 1),
        }
    )
    if not any(v["captured"] for v in cards.values()) and rep["trends"]:
        rep["missing"] = missing_spec(symside, rep["trends"][0])
    (out / f"{symside}_missed_trend.json").write_text(
        json.dumps(rep, indent=1, default=str)
    )
    if kept:
        eff = sorted(
            {str(lab).split("=", 1)[0].strip() for lab in attrs}
            | {str(v["switch"]).split("=", 1)[0].strip() for v in verified}
            | set(rep["combo_overrides"])
        )
        (out / f"{symside}_missed_trend_base.json").write_text(
            json.dumps(
                {
                    "final": {"overrides": final_ov},
                    "autopsy": {
                        "symside": symside,
                        "status": "RESCUED",
                        "base_gain": b["gain"],
                        "base_trades": b["trades"],
                        "base_valid": b["valid"],
                        "combo_gain": combo_gain,
                        "switches": [v["switch"] for v in verified],
                        "effective_switches": eff,
                        "source": "missed_trend",
                    },
                },
                indent=1,
                default=str,
            )
        )
        (out / f"{symside}_missed_trend_priors.json").write_text(
            json.dumps(priors_partial(symside, verified), indent=1, default=str)
        )
    print(
        f"[missed-trend] {symside} {rep['status']} base {b['gain']} ({b['trades']}t TIM {b['tim']}) -> combo {combo_gain} ({len(kept)} kept) effective {len(attrs)}/{len(items)} ({rep['secs']}s)",
        flush=True,
    )
    return rep


def run_requeue(
    symside,
    progress_path,
    base_path,
    out_dir="SPREADSHEETS/V15_V16_CELL_BY_CELL",
    history_dir="SPREADSHEETS/V15_V16_HISTORY",
    autopsy_dir=None,
    dry_run=False,
):
    """Archive the BEST_EFFORT/NO_TRADES final + stale progress so the scheduler
    relaunches a fresh need30 revision round; optionally install the trend base where
    the scheduler's V15_START_OVERRIDES launch env picks it up. Reversible: every
    move is logged with its restore path."""
    pp = Path(progress_path)
    d = json.load(open(pp))
    verdict = d.get("verdict")
    if verdict not in REQUEUE_VERDICTS:
        return {
            "symside": symside,
            "requeued": False,
            "reason": f"verdict {verdict!r} not in {REQUEUE_VERDICTS} (QUALIFIED/IMPOSSIBLE need a human decision)",
        }
    moves = []
    cell, hist = Path(out_dir), Path(history_dir)
    for pat in (
        f"{symside}_bh*_gain*_30d_matrix.xlsx",
        f"{symside}_bh*_gain*_30d_matrix.html",
        f"{symside}_bh*_gain*_30d_matrix_manifest.json",
    ):
        for src in sorted(cell.glob(pat)):
            dst = hist / src.name
            moves.append({"from": str(src), "to": str(dst)})
            if not dry_run:
                hist.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    par = pp.with_name(pp.name + f".missedtrend_prev_{stamp}")
    moves.append({"from": str(pp), "to": str(par)})
    base_dst = None
    if autopsy_dir:
        base_dst = str(
            Path(os.path.expanduser(autopsy_dir)) / f"{symside}_autopsy_base.json"
        )
        if Path(base_dst).exists():
            moves.append({"from": base_dst, "to": base_dst + f".prev_{stamp}"})
    if not dry_run:
        shutil.move(str(pp), str(par))
        if autopsy_dir:
            ad = Path(os.path.expanduser(autopsy_dir))
            ad.mkdir(parents=True, exist_ok=True)
            if Path(base_dst).exists():
                shutil.move(base_dst, base_dst + f".prev_{stamp}")
            shutil.copy(str(base_path), base_dst)
    return {
        "symside": symside,
        "requeued": not dry_run,
        "dry_run": dry_run,
        "verdict": verdict,
        "moves": moves,
        "base_installed": base_dst,
        "restore": f"move every 'to' back to its 'from' to undo",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="missed-trend revision round: scan | screen | requeue"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("scan", help="engine-free detection (Mac-safe)")
    p1.add_argument("--html", required=True)
    p1.add_argument("--symside", required=True)
    p1.add_argument("--min-pct", type=float, default=MISSED_TREND_MIN_PCT)
    p1.add_argument("--out", default=None)
    p2 = sub.add_parser("screen", help="fleet-only engine screen + verified base")
    p2.add_argument("--symside", required=True)
    p2.add_argument("--set-json", required=True)
    p2.add_argument("--out", required=True)
    p2.add_argument("--workers", type=int, default=6)
    p2.add_argument("--max-combo", type=int, default=8)
    p2.add_argument("--min-pct", type=float, default=MISSED_TREND_MIN_PCT)
    p2.add_argument("--tim-tol", type=float, default=0.0)
    p3 = sub.add_parser(
        "requeue", help="archive BEST_EFFORT final + progress, install base"
    )
    p3.add_argument("--symside", required=True)
    p3.add_argument("--progress", required=True)
    p3.add_argument("--base", required=True)
    p3.add_argument("--out-dir", default="SPREADSHEETS/V15_V16_CELL_BY_CELL")
    p3.add_argument("--history-dir", default="SPREADSHEETS/V15_V16_HISTORY")
    p3.add_argument("--autopsy-dir", default=None)
    p3.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        hi = html_inputs(a.html)
        rep = scan_report(
            a.symside,
            hi["close"],
            hi["trades"],
            (hi["meta"] or {}).get("tim"),
            (hi["meta"] or {}).get("gain"),
            hi["labels"],
            a.min_pct,
        )
        rep["html"] = a.html
        print(
            json.dumps(
                {
                    k: rep[k]
                    for k in (
                        "symside",
                        "n_bars",
                        "n_trades",
                        "tim_pct",
                        "gain_pct",
                        "revise",
                        "reasons",
                        "churn",
                    )
                },
                indent=1,
                default=str,
            )
        )
        for t in rep["trends"][:5]:
            print(
                f"  trend {t['move_pct']:+.2f}% bars {t['b0']}->{t['b1']} {t.get('t0')} -> {t.get('t1')} cover {t['cover'] * 100:.1f}% late {t['late_by_bars']}"
            )
        if a.out:
            Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
        return 0
    if a.cmd == "screen":
        run_screen(
            a.symside, a.set_json, a.out, a.workers, a.max_combo, a.min_pct, a.tim_tol
        )
        return 0
    print(
        json.dumps(
            run_requeue(
                a.symside,
                a.progress,
                a.base,
                a.out_dir,
                a.history_dir,
                a.autopsy_dir,
                a.dry_run,
            ),
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
