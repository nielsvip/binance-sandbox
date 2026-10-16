#!/usr/bin/env python3
"""v15_board_ab_proof — A/B PROOF that the corrected templates + corrected v15_pilot build a BETTER board
than the previous (broken) run, per sym_side. READ-ONLY. Writes nothing but its own reports.

Why this exists (USER 2026-10-15, verbatim): "we need EVERY ROW to be corrected and running perfectly with
confirmation from new runs vs previous run for same sym_side" and "BACKTESTS ARE PROVEN TO IMPROVE GAIN AND
WR COMPARED TO PREVIOUS BROKEN TEMPLATE AND V15_PILOT". A rerun OVERWRITES the per-sym_side progress JSON and
appends a new per_sym_history row, so the pre-fix numbers are gone the moment the new round lands. Therefore
step one is a SNAPSHOT of the current (pre-fix) state; step two, after the reruns, is the comparison.

NO-LIES (CLAUDE.md): every number carries the file+key it came from. Nothing is annualised, nothing is
estimated, no win rate is invented. Win rate is only printed when a recorded per-trade-derived WR record
(engine `wr` = wins/trades) exists whose own `gain` matches the board's headline gain — otherwise the board
is labelled [UNVERIFIED] and NO WR number is printed. Sharpe is only ever reported as `pool_sharpe`.

Sources of truth, in order of trust:
  1. data/reports/lifecycle_pilot/{SYM_SIDE}_v14_progress.json
     final_gain / final_gain_fresh_vec / cumulative_gain / baseline_gain / bh / trades / verdict /
     defaults_round / npz_id, and the `done` dict (one entry per evaluated template row) which carries the
     YELLOW-COVERAGE truth this fix is about: delta, reason, yellows.
  2. data/hourly_reconfig/per_sym_store.db  table per_sym_history (sqlite, opened mode=ro)
     latest row per sym_side = the currently PUBLISHED board: gain_pct, baseline_gain, delta_pct, bh_pct,
     trades, pool_sharpe, max_dd_pct, tim_pct, valid, defaults_round, updated_at.

Outputs (the only files it creates):
  data/reports/v15_board_ab_baseline_<UTCSTAMP>.json   — immutable stamped snapshot
  data/reports/v15_board_ab_baseline_latest.json       — stable pointer-copy of the snapshot
  data/reports/v15_board_ab_compare_<UTCSTAMP>.json    — only with --compare --write-report

Usage:
  python3 tools/v15_board_ab_proof.py --snapshot                 # BEFORE the reruns (run this first)
  python3 tools/v15_board_ab_proof.py --compare                  # AFTER the reruns: the proof
  python3 tools/v15_board_ab_proof.py --compare --symside AAPL_LONG
  python3 tools/v15_board_ab_proof.py --compare --cat-side STOCKS_LONG --json

Exit codes: 0 = aggregate IMPROVED, 1 = aggregate did NOT improve, 2 = insufficient data.
"""
import argparse
import datetime
import glob
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROG_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
DB_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
REPORT_DIR = ROOT / "data" / "reports"
BASELINE_LATEST = REPORT_DIR / "v15_board_ab_baseline_latest.json"
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_QUOTES = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
EPS = 1e-9

# Mirrored from v15_pilot.py `_qualifies_30d` / module constants (v15_pilot.py L2303-2359) — NOT invented here.
QUAL_FLOOR_TRADES = 10  # v15_pilot.ADAPT_FLOOR_TRADES
QUAL_TIM_MIN, QUAL_TIM_MAX = 20.0, 80.0  # v15_pilot.QUAL_TIM_MIN / QUAL_TIM_MAX
QUAL_DD_MAX = 30.0  # v15_pilot.QUAL_DD_MAX (USER 2026-10-14 hard finish gate)

# The WT15 family — the rows where the yellow-coverage fix must show up first.
WT15_PREFIXES = ("WT_15M_BOUNCE_", "WT_15M_CROSS_", "WT_CROSS_EXIT_", "WT_CROSSUNDER_")
WT15_EXACT = ("WT_LOWER_CROSS_EXIT_TF",)
# The single row the operator measured by hand (0 yellows on ZCSH_SHORT/ULTA_LONG/ORCL_SHORT, 49 on WDAY_SHORT).
WT_PROBE_SWITCH = "WT_15M_BOUNCE_OPEN_ENABLED"
REASON_TAGS = ("NOT_WIRED", "RUNNING_IDENTITY", "CARRIED_DEAD", "ZERO_TRADES", "TYPE_MISMATCH", "NO_CANDIDATE", "INVENTED_ALT")


def utcstamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cat_side_of(symside):
    """CRYPTO_|STOCKS_ + LONG|SHORT, same rule as v15_pilot.py L706 (quote-suffix test)."""
    s = (symside or "").upper()
    side = "SHORT" if s.endswith("_SHORT") else "LONG" if s.endswith("_LONG") else "?"
    base = s.rsplit("_", 1)[0] if side != "?" else s
    return ("CRYPTO_" if base.endswith(CRYPTO_QUOTES) else "STOCKS_") + side


def fnum(x):
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def is_wt15(switch):
    return switch.startswith(WT15_PREFIXES) or switch in WT15_EXACT


def parse_done_key(key):
    """'EXIT_STRUCTURAL!27:WT_LOWER_CROSS_EXIT_TF=4h' -> (sheet, row, switch, cand)."""
    sheet, _, rest = key.partition("!")
    row, _, rest2 = rest.partition(":")
    switch, _, cand = rest2.partition("=")
    return sheet, row, switch, cand


def reason_tag(reason):
    r = reason or ""
    for t in REASON_TAGS:
        if t in r:
            return t
    if not r:
        return "NONE"
    if r.lower().startswith("corrupt"):
        return "CORRUPT_PRICE_DATA"
    return "OTHER"


def _blank_rowstats():
    return {"rows": 0, "calculated": 0, "delta_none": 0, "yellows_total": 0, "zero_yellow_rows": 0}


def _add_row(acc, calculated, n_yellows):
    acc["rows"] += 1
    if calculated:
        acc["calculated"] += 1
        acc["yellows_total"] += n_yellows
        if n_yellows == 0:
            acc["zero_yellow_rows"] += 1
    else:
        acc["delta_none"] += 1


def _share(num, den):
    return round(100.0 * num / den, 2) if den else None


def _finish_rowstats(acc):
    acc["zero_yellow_share_pct"] = _share(acc["zero_yellow_rows"], acc["calculated"])
    acc["yellows_per_calc_row"] = round(acc["yellows_total"] / acc["calculated"], 3) if acc["calculated"] else None
    return acc


def find_wr_records(obj, path="", out=None, depth=0):
    """Every dict that carries BOTH a win rate and the gain it belongs to -> (json path, wr, gain, trades).
    Only such a pair is traceable: the WR is printed solely when its own gain matches the headline gain."""
    if out is None:
        out = []
    if depth > 6 or not isinstance(obj, dict):
        return out
    wr = None
    wr_key = None
    for k in ("wr", "wr_pct", "win_rate", "winrate"):
        if fnum(obj.get(k)) is not None:
            wr, wr_key = fnum(obj[k]), k
            break
    gain = None
    for k in ("gain", "gain_pct"):
        if fnum(obj.get(k)) is not None:
            gain = fnum(obj[k])
            break
    if wr is not None and gain is not None:
        out.append({"path": f"{path}.{wr_key}".lstrip("."), "wr_pct": wr, "gain_pct": gain, "trades": obj.get("trades")})
    for k, v in obj.items():
        if isinstance(v, dict):
            find_wr_records(v, f"{path}.{k}".lstrip("."), out, depth + 1)
        elif isinstance(v, list):
            for i, it in enumerate(v):
                if isinstance(it, dict):
                    find_wr_records(it, f"{path}.{k}[{i}]".lstrip("."), out, depth + 1)
    return out


def scan_progress(path):
    """One board snapshot from one *_v14_progress.json. The `done` dict is summarised, never retained."""
    st = os.stat(path)
    with open(path, "r") as fh:
        d = json.load(fh)
    symside = d.get("symside") or os.path.basename(path).replace("_v14_progress.json", "")
    gain, gain_src = None, None
    for k in ("final_gain", "final_gain_fresh_vec", "cumulative_gain"):
        if fnum(d.get(k)) is not None:
            gain, gain_src = fnum(d[k]), k
            break
    imp = d.get("impossible_metrics") or {}
    if gain is None and fnum(imp.get("gain_pct")) is not None:
        gain, gain_src = fnum(imp["gain_pct"]), "impossible_metrics.gain_pct"
    trades, trades_src = None, None
    for k in ("final_trades", "run_trades"):
        if d.get(k) is not None:
            trades, trades_src = int(d[k]), k
            break
    if trades is None and imp.get("trades") is not None:
        trades, trades_src = int(imp["trades"]), "impossible_metrics.trades"
    wr_records = find_wr_records({k: v for k, v in d.items() if k != "done"})
    wr, wr_src = None, None
    if gain is not None:
        for rec in wr_records:
            if abs(rec["gain_pct"] - gain) <= 1e-6:
                wr, wr_src = rec["wr_pct"], rec["path"]
                break
    overall = _blank_rowstats()
    exit_tabs = _blank_rowstats()
    wt15 = _blank_rowstats()
    tags = {}
    per_sheet = {}
    wt_probe = []
    for key, v in (d.get("done") or {}).items():
        if not isinstance(v, dict):
            continue
        sheet, row, switch, cand = parse_done_key(key)
        calculated = v.get("delta") is not None
        n_yellows = len(v.get("yellows") or {})
        tag = reason_tag(v.get("reason"))
        tags[tag] = tags.get(tag, 0) + 1
        _add_row(overall, calculated, n_yellows)
        if sheet.startswith("EXIT"):
            _add_row(exit_tabs, calculated, n_yellows)
        ps = per_sheet.setdefault(sheet, _blank_rowstats())
        _add_row(ps, calculated, n_yellows)
        if is_wt15(switch):
            _add_row(wt15, calculated, n_yellows)
            if switch == WT_PROBE_SWITCH:
                wt_probe.append({"key": key, "cand": cand, "calculated": calculated, "yellows": n_yellows, "reason": (v.get("reason") or "")[:120]})
    npz = d.get("npz_id") or {}
    return {
        "sym_side": symside,
        "cat_side": cat_side_of(symside),
        "source_json": os.path.relpath(path, ROOT),
        "json_mtime_ns": st.st_mtime_ns,
        "json_mtime_utc": iso(st.st_mtime),
        "json_bytes": st.st_size,
        "verdict": d.get("verdict"),
        "defaults_round": d.get("defaults_round"),
        "npz_md5": npz.get("md5"),
        "npz_ts_last": npz.get("ts_last"),
        "window_days": d.get("window_days"),
        "gain_pct": gain,
        "gain_source": gain_src,
        "baseline_gain": fnum(d.get("baseline_gain")),
        "initial_baseline_gain": fnum(d.get("initial_baseline_gain")),
        "bh_pct": fnum(d.get("bh")),
        "trades": trades,
        "trades_source": trades_src,
        "tim_pct": fnum(imp.get("tim_pct")),
        "max_dd_pct": fnum(imp.get("max_dd_pct")),
        "valid": imp.get("valid"),
        "metrics_source": "impossible_metrics" if imp else None,
        "wr_pct": wr,
        "wr_source": wr_src,
        "wr_records_found": len(wr_records),
        "rows_overall": _finish_rowstats(overall),
        "rows_exit_tabs": _finish_rowstats(exit_tabs),
        "rows_wt15": _finish_rowstats(wt15),
        "reason_tags": tags,
        "per_sheet": {k: _finish_rowstats(v) for k, v in sorted(per_sheet.items())},
        "wt_probe_rows": wt_probe,
    }


def scan_db():
    """Latest per_sym_history row per sym_side = the currently published board. Opened READ-ONLY."""
    if not DB_PATH.exists():
        return {}, "missing"
    out = {}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30.0)
    try:
        cur = con.execute(
            "select sym_side, updated_at, epoch, cat_side, defaults_round, winning_tag, gain_pct, baseline_gain,"
            " delta_pct, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, valid, template_md5, meta_json"
            " from per_sym_history order by epoch asc"
        )
        cols = [c[0] for c in cur.description]
        for r in cur:
            row = dict(zip(cols, r))
            meta = {}
            if row.get("meta_json"):
                try:
                    meta = json.loads(row["meta_json"])
                except (ValueError, TypeError):
                    meta = {}
            row.pop("meta_json", None)
            if row.get("pool_sharpe") is None and fnum(meta.get("pool_sharpe")) is not None:
                row["pool_sharpe"] = fnum(meta["pool_sharpe"])
                row["pool_sharpe_source"] = "meta_json.pool_sharpe"
            else:
                row["pool_sharpe_source"] = "per_sym_history.pool_sharpe" if row.get("pool_sharpe") is not None else None
            row["n_promoted"] = meta.get("n_promoted")
            row["engine_md5"] = meta.get("engine_md5")
            row["source"] = "data/hourly_reconfig/per_sym_store.db:per_sym_history"
            out[row["sym_side"]] = row  # ordered by epoch asc -> last write wins = newest
    finally:
        con.close()
    return out, "ok"


def build_state(symside=None, cat_side=None, limit=None, verbose=False):
    files = sorted(glob.glob(str(PROG_DIR / "*_v14_progress.json")), key=os.path.getmtime, reverse=True)
    if symside:
        files = [f for f in files if os.path.basename(f) == f"{symside}_v14_progress.json"]
    if cat_side:
        files = [f for f in files if cat_side_of(os.path.basename(f).replace("_v14_progress.json", "")) == cat_side]
    if limit:
        files = files[: int(limit)]
    boards, errors = {}, []
    for i, f in enumerate(files):
        try:
            b = scan_progress(f)
            boards[b["sym_side"]] = b
        except (ValueError, OSError, KeyError) as e:
            errors.append({"file": os.path.relpath(f, ROOT), "error": f"{type(e).__name__}: {e}"})
        if verbose and (i + 1) % 50 == 0:
            print(f"  ... scanned {i + 1}/{len(files)} progress files", file=sys.stderr)
    db, db_state = scan_db()
    if symside:
        db = {k: v for k, v in db.items() if k == symside}
    if cat_side:
        db = {k: v for k, v in db.items() if cat_side_of(k) == cat_side}
    return {
        "generated_utc": utcnow(),
        "root": str(ROOT),
        "sources": {
            "progress_glob": str(PROG_DIR / "*_v14_progress.json"),
            "progress_files": len(files),
            "per_sym_store_db": str(DB_PATH),
            "per_sym_store_state": db_state,
        },
        "filters": {"symside": symside, "cat_side": cat_side, "limit": limit},
        "qual_gates_mirrored_from_v15_pilot": {
            "floor_trades": QUAL_FLOOR_TRADES,
            "tim_min": QUAL_TIM_MIN,
            "tim_max": QUAL_TIM_MAX,
            "dd_max": QUAL_DD_MAX,
        },
        "boards": boards,
        "published": db,
        "scan_errors": errors,
    }


def headline(board, pub):
    """(gain, trades, dd, tim, pool_sharpe, valid, source) — published row wins, progress JSON is the fallback."""
    if pub:
        return {
            "gain_pct": fnum(pub.get("gain_pct")),
            "baseline_gain": fnum(pub.get("baseline_gain")),
            "bh_pct": fnum(pub.get("bh_pct")),
            "trades": pub.get("trades"),
            "max_dd_pct": fnum(pub.get("max_dd_pct")),
            "tim_pct": fnum(pub.get("tim_pct")),
            "pool_sharpe": fnum(pub.get("pool_sharpe")),
            "pool_sharpe_source": pub.get("pool_sharpe_source"),
            "valid": bool(pub.get("valid")) if pub.get("valid") is not None else None,
            "source": "per_sym_history(latest)",
            "stamp": pub.get("updated_at"),
            "defaults_round": pub.get("defaults_round"),
        }
    b = board or {}
    return {
        "gain_pct": b.get("gain_pct"),
        "baseline_gain": b.get("baseline_gain"),
        "bh_pct": b.get("bh_pct"),
        "trades": b.get("trades"),
        "max_dd_pct": b.get("max_dd_pct"),
        "tim_pct": b.get("tim_pct"),
        "pool_sharpe": None,
        "pool_sharpe_source": None,
        "valid": b.get("valid"),
        "source": f"progress_json({b.get('gain_source')})" if b.get("gain_source") else "progress_json(none)",
        "stamp": b.get("json_mtime_utc"),
        "defaults_round": b.get("defaults_round"),
    }


def qualifies(h):
    """Mirror of v15_pilot._qualifies_30d — a board failing this can NEVER be called IMPROVED."""
    r = []
    if h.get("valid") is False:
        r.append("invalid")
    t = h.get("trades")
    if t is None:
        r.append("trades unknown")
    elif int(t) < QUAL_FLOOR_TRADES:
        r.append(f"trades {int(t)}<{QUAL_FLOOR_TRADES}")
    tim = h.get("tim_pct")
    if tim is None:
        r.append("TIM unknown")
    elif not (QUAL_TIM_MIN <= tim <= QUAL_TIM_MAX):
        r.append(f"TIM {tim:.1f} outside [{QUAL_TIM_MIN:.0f},{QUAL_TIM_MAX:.0f}]")
    g = h.get("gain_pct")
    if g is None or g <= 0:
        r.append(f"gain {g}")
    dd = h.get("max_dd_pct")
    if dd is None:
        r.append("DD unknown")
    elif dd > QUAL_DD_MAX:
        r.append(f"DD {dd:.1f}>{QUAL_DD_MAX:.0f}")
    return (len(r) == 0, r)


def _delta(new, old):
    if new is None or old is None:
        return None
    return round(new - old, 6)


def _rowdelta(new, old, key):
    n = (new or {}).get(key)
    o = (old or {}).get(key)
    if n is None or o is None:
        return None
    return round(n - o, 4)


def compare(base_state, cur_state, flat_eps):
    rows = []
    keys = sorted(set(base_state.get("boards", {})) | set(base_state.get("published", {})) | set(cur_state.get("boards", {})) | set(cur_state.get("published", {})))
    for ss in keys:
        b_board = base_state.get("boards", {}).get(ss)
        b_pub = base_state.get("published", {}).get(ss)
        c_board = cur_state.get("boards", {}).get(ss)
        c_pub = cur_state.get("published", {}).get(ss)
        bh_ = headline(b_board, b_pub)
        ch_ = headline(c_board, c_pub)
        new_json = bool(c_board) and (not b_board or c_board["json_mtime_ns"] != b_board["json_mtime_ns"])
        new_pub = bool(c_pub) and (not b_pub or c_pub.get("updated_at") != b_pub.get("updated_at"))
        no_new = not (new_json or new_pub)
        g_delta = _delta(ch_["gain_pct"], bh_["gain_pct"])
        wr_new = (c_board or {}).get("wr_pct")
        wr_old = (b_board or {}).get("wr_pct")
        wr_delta = _delta(wr_new, wr_old)
        qual_ok, qual_reasons = qualifies(ch_)
        if not (b_board or b_pub):
            verdict = "NO_BASELINE"
        elif not (c_board or c_pub):
            verdict = "NO_NEW_RUN"
        elif no_new:
            verdict = "NO_NEW_RUN"
        elif ch_["gain_pct"] is None or bh_["gain_pct"] is None:
            verdict = "INVALID"
            qual_reasons = qual_reasons + ["headline gain missing on one side"]
        elif not qual_ok:
            verdict = "INVALID"
        elif g_delta > flat_eps:
            verdict = "IMPROVED"
        elif g_delta < -flat_eps:
            verdict = "WORSE"
        else:
            verdict = "FLAT"
        rows.append(
            {
                "sym_side": ss,
                "cat_side": cat_side_of(ss),
                "verdict": verdict,
                "qual_fail_reasons": [] if qual_ok else qual_reasons,
                "gain_before": bh_["gain_pct"],
                "gain_after": ch_["gain_pct"],
                "gain_delta": g_delta,
                "gain_source_before": bh_["source"],
                "gain_source_after": ch_["source"],
                "wr_before": wr_old,
                "wr_after": wr_new,
                "wr_delta": wr_delta,
                "wr_source_before": (b_board or {}).get("wr_source"),
                "wr_source_after": (c_board or {}).get("wr_source"),
                "wr_status": "verified" if (wr_old is not None and wr_new is not None) else "[UNVERIFIED]",
                "trades_before": bh_["trades"],
                "trades_after": ch_["trades"],
                "dd_before": bh_["max_dd_pct"],
                "dd_after": ch_["max_dd_pct"],
                "tim_before": bh_["tim_pct"],
                "tim_after": ch_["tim_pct"],
                "pool_sharpe_before": bh_["pool_sharpe"],
                "pool_sharpe_after": ch_["pool_sharpe"],
                "pool_sharpe_source_after": ch_["pool_sharpe_source"],
                "bh_before": bh_["bh_pct"],
                "bh_after": ch_["bh_pct"],
                "stamp_before": bh_["stamp"],
                "stamp_after": ch_["stamp"],
                "defaults_round_before": bh_["defaults_round"],
                "defaults_round_after": ch_["defaults_round"],
                "rows_before": (b_board or {}).get("rows_overall"),
                "rows_after": (c_board or {}).get("rows_overall"),
                "exit_rows_before": (b_board or {}).get("rows_exit_tabs"),
                "exit_rows_after": (c_board or {}).get("rows_exit_tabs"),
                "wt15_before": (b_board or {}).get("rows_wt15"),
                "wt15_after": (c_board or {}).get("rows_wt15"),
                "zero_yellow_share_delta": _rowdelta((c_board or {}).get("rows_overall"), (b_board or {}).get("rows_overall"), "zero_yellow_share_pct"),
                "wt15_yellows_per_row_delta": _rowdelta((c_board or {}).get("rows_wt15"), (b_board or {}).get("rows_wt15"), "yellows_per_calc_row"),
                "reason_tags_before": (b_board or {}).get("reason_tags"),
                "reason_tags_after": (c_board or {}).get("reason_tags"),
                "wt_probe_before": (b_board or {}).get("wt_probe_rows"),
                "wt_probe_after": (c_board or {}).get("wt_probe_rows"),
            }
        )
    return rows


def _agg_rowstats(states, key):
    calc = sum((b.get(key) or {}).get("calculated") or 0 for b in states)
    zero = sum((b.get(key) or {}).get("zero_yellow_rows") or 0 for b in states)
    yel = sum((b.get(key) or {}).get("yellows_total") or 0 for b in states)
    none = sum((b.get(key) or {}).get("delta_none") or 0 for b in states)
    return {
        "boards": len(states),
        "calculated_rows": calc,
        "rows_delta_none": none,
        "zero_yellow_rows": zero,
        "zero_yellow_share_pct": _share(zero, calc),
        "yellows_total": yel,
        "yellows_per_calc_row": round(yel / calc, 3) if calc else None,
    }


def summarise(rows, base_state, cur_state, flat_eps):
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    rerun = [r for r in rows if r["verdict"] in ("IMPROVED", "FLAT", "WORSE", "INVALID")]
    gd = [r["gain_delta"] for r in rerun if r["gain_delta"] is not None]
    wd = [r["wr_delta"] for r in rerun if r["wr_delta"] is not None]
    changed = {r["sym_side"] for r in rerun}
    b_states = [v for k, v in base_state.get("boards", {}).items() if k in changed] or list(base_state.get("boards", {}).values())
    c_states = [v for k, v in cur_state.get("boards", {}).items() if k in changed] or list(cur_state.get("boards", {}).values())
    n_imp, n_worse = counts.get("IMPROVED", 0), counts.get("WORSE", 0)
    improved = bool(rerun) and n_imp > n_worse and gd and statistics.median(gd) > 0
    return {
        "generated_utc": utcnow(),
        "flat_eps_gain_pct": flat_eps,
        "n_sym_sides": len(rows),
        "verdict_counts": counts,
        "n_with_new_run": len(rerun),
        "median_gain_delta": round(statistics.median(gd), 4) if gd else None,
        "mean_gain_delta": round(statistics.fmean(gd), 4) if gd else None,
        "n_gain_delta_measured": len(gd),
        "median_wr_delta": round(statistics.median(wd), 4) if wd else None,
        "n_wr_delta_measured": len(wd),
        "n_wr_unverified": sum(1 for r in rerun if r["wr_status"] != "verified"),
        "yellow_coverage_before": {
            "all_tabs": _agg_rowstats(b_states, "rows_overall"),
            "exit_tabs": _agg_rowstats(b_states, "rows_exit_tabs"),
            "wt15_rows": _agg_rowstats(b_states, "rows_wt15"),
        },
        "yellow_coverage_after": {
            "all_tabs": _agg_rowstats(c_states, "rows_overall"),
            "exit_tabs": _agg_rowstats(c_states, "rows_exit_tabs"),
            "wt15_rows": _agg_rowstats(c_states, "rows_wt15"),
        },
        "aggregate_improved": improved,
        "aggregate_note": (
            "AGGREGATE IMPROVED" if improved else "AGGREGATE DID NOT IMPROVE — reported honestly, not a tool failure"
        ),
    }


def _fmt(v, nd=2):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int,)) and not isinstance(v, bool):
        return str(v)
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def print_compare(rows, summ, show_rows=True):
    print(f"=== v15 BOARD A/B PROOF — {summ['generated_utc']} (FLAT band +/-{summ['flat_eps_gain_pct']} gain pct) ===")
    print(f"sym_sides considered: {summ['n_sym_sides']}   with a NEW run: {summ['n_with_new_run']}")
    print("verdicts: " + "  ".join(f"{k}={v}" for k, v in sorted(summ["verdict_counts"].items())))
    print(f"median gain delta: {_fmt(summ['median_gain_delta'],4)} (n={summ['n_gain_delta_measured']})   mean {_fmt(summ['mean_gain_delta'],4)}")
    print(f"median WR delta  : {_fmt(summ['median_wr_delta'],4)} (n={summ['n_wr_delta_measured']} verified, {summ['n_wr_unverified']} [UNVERIFIED])")
    for scope in ("all_tabs", "exit_tabs", "wt15_rows"):
        b = summ["yellow_coverage_before"][scope]
        a = summ["yellow_coverage_after"][scope]
        print(
            f"yellow coverage {scope:10s} BEFORE calc={b['calculated_rows']} zero_yellow={b['zero_yellow_rows']} "
            f"({_fmt(b['zero_yellow_share_pct'])}%) yel/row={_fmt(b['yellows_per_calc_row'],3)}  ->  "
            f"AFTER calc={a['calculated_rows']} zero_yellow={a['zero_yellow_rows']} "
            f"({_fmt(a['zero_yellow_share_pct'])}%) yel/row={_fmt(a['yellows_per_calc_row'],3)}"
        )
    if show_rows:
        shown = [r for r in rows if r["verdict"] not in ("NO_NEW_RUN",)] or rows
        shown = sorted(shown, key=lambda r: (r["gain_delta"] is None, -(r["gain_delta"] or 0)))
        print("")
        print(f"{'SYM_SIDE':22s} {'VERDICT':11s} {'gain_b':>9s} {'gain_a':>9s} {'dgain':>8s} {'wr_b':>7s} {'wr_a':>7s} {'dwr':>7s} {'trd_b':>6s} {'trd_a':>6s} {'dd_a':>6s} {'0yel%_b':>8s} {'0yel%_a':>8s} {'wt_y/row_b':>10s} {'wt_y/row_a':>10s}")
        for r in shown[:400]:
            wb = (r["wt15_before"] or {}).get("yellows_per_calc_row")
            wa = (r["wt15_after"] or {}).get("yellows_per_calc_row")
            print(
                f"{r['sym_side']:22s} {r['verdict']:11s} {_fmt(r['gain_before']):>9s} {_fmt(r['gain_after']):>9s} "
                f"{_fmt(r['gain_delta']):>8s} {_fmt(r['wr_before'],1):>7s} {_fmt(r['wr_after'],1):>7s} {_fmt(r['wr_delta'],1):>7s} "
                f"{_fmt(r['trades_before'],0):>6s} {_fmt(r['trades_after'],0):>6s} {_fmt(r['dd_after']):>6s} "
                f"{_fmt((r['rows_before'] or {}).get('zero_yellow_share_pct')):>8s} {_fmt((r['rows_after'] or {}).get('zero_yellow_share_pct')):>8s} "
                f"{_fmt(wb,2):>10s} {_fmt(wa,2):>10s}"
            )
        if len(shown) > 400:
            print(f"... {len(shown) - 400} more rows (use --json for all)")
    print("")
    print(("*** " + summ["aggregate_note"] + " ***") if not summ["aggregate_improved"] else summ["aggregate_note"])


def atomic_write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh, indent=1, sort_keys=False, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def main():
    ap = argparse.ArgumentParser(description="A/B proof: corrected templates+pilot vs the previous (broken) run, per sym_side. READ-ONLY.")
    ap.add_argument("--snapshot", action="store_true", help="write the pre-fix baseline snapshot and exit")
    ap.add_argument("--compare", action="store_true", help="compare the baseline snapshot against the current state")
    ap.add_argument("--baseline", default=str(BASELINE_LATEST), help="baseline snapshot to compare against")
    ap.add_argument("--symside", default=None, help="restrict to one SYM_SIDE, e.g. AAPL_LONG")
    ap.add_argument("--cat-side", default=None, choices=list(CAT_SIDES), help="restrict to one cat_side")
    ap.add_argument("--limit", type=int, default=None, help="scan only the N newest progress files (plumbing checks)")
    ap.add_argument("--flat-eps", type=float, default=0.5, help="FLAT band on gain pct; default 0.5 = the measured same-tree board nondeterminism bound")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--write-report", action="store_true", help="--compare: also write data/reports/v15_board_ab_compare_<stamp>.json")
    ap.add_argument("--force", action="store_true", help="--snapshot: allow overwriting an existing *_latest.json")
    ap.add_argument("--no-rows", action="store_true", help="--compare: summary only")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.snapshot == args.compare:
        ap.error("pass exactly one of --snapshot / --compare")
    if args.snapshot:
        if BASELINE_LATEST.exists() and not args.force:
            print(f"REFUSING: {BASELINE_LATEST} already exists — it is the pre-fix baseline. Pass --force to overwrite (the stamped copies are kept either way).", file=sys.stderr)
            return 2
        state = build_state(args.symside, args.cat_side, args.limit, verbose=not args.quiet)
        state["snapshot_kind"] = "baseline"
        stamped = REPORT_DIR / f"v15_board_ab_baseline_{utcstamp()}.json"
        atomic_write_json(stamped, state)
        state["stamped_path"] = os.path.relpath(stamped, ROOT)
        atomic_write_json(BASELINE_LATEST, state)
        n_b, n_p = len(state["boards"]), len(state["published"])
        both = len(set(state["boards"]) & set(state["published"]))
        agg = {
            "all_tabs": _agg_rowstats(list(state["boards"].values()), "rows_overall"),
            "exit_tabs": _agg_rowstats(list(state["boards"].values()), "rows_exit_tabs"),
            "wt15_rows": _agg_rowstats(list(state["boards"].values()), "rows_wt15"),
        }
        if args.json:
            print(json.dumps({"stamped": str(stamped), "latest": str(BASELINE_LATEST), "boards": n_b, "published": n_p, "both": both, "coverage": agg, "scan_errors": state["scan_errors"]}, indent=1))
        else:
            print(f"BASELINE SNAPSHOT written {state['generated_utc']}")
            print(f"  stamped : {stamped}")
            print(f"  latest  : {BASELINE_LATEST}")
            print(f"  sym_sides from progress JSON : {n_b}")
            print(f"  sym_sides from per_sym_history: {n_p}   (in both: {both})")
            print(f"  scan errors: {len(state['scan_errors'])}")
            for scope, a in agg.items():
                print(f"  {scope:10s} calc_rows={a['calculated_rows']} delta_none={a['rows_delta_none']} zero_yellow={a['zero_yellow_rows']} ({_fmt(a['zero_yellow_share_pct'])}%) yellows={a['yellows_total']} yel/row={_fmt(a['yellows_per_calc_row'],3)}")
        return 0
    bp = pathlib.Path(args.baseline)
    if not bp.exists():
        print(f"INSUFFICIENT DATA: no baseline snapshot at {bp}. Run --snapshot first.", file=sys.stderr)
        return 2
    with open(bp) as fh:
        base_state = json.load(fh)
    cur_state = build_state(args.symside, args.cat_side, args.limit, verbose=not args.quiet)
    if args.symside:
        base_state = dict(base_state)
        base_state["boards"] = {k: v for k, v in base_state.get("boards", {}).items() if k == args.symside}
        base_state["published"] = {k: v for k, v in base_state.get("published", {}).items() if k == args.symside}
    if args.cat_side:
        base_state = dict(base_state)
        base_state["boards"] = {k: v for k, v in base_state.get("boards", {}).items() if cat_side_of(k) == args.cat_side}
        base_state["published"] = {k: v for k, v in base_state.get("published", {}).items() if cat_side_of(k) == args.cat_side}
    rows = compare(base_state, cur_state, args.flat_eps)
    summ = summarise(rows, base_state, cur_state, args.flat_eps)
    summ["baseline_snapshot"] = str(bp)
    summ["baseline_generated_utc"] = base_state.get("generated_utc")
    out = {"summary": summ, "rows": rows}
    if args.write_report:
        p = atomic_write_json(REPORT_DIR / f"v15_board_ab_compare_{utcstamp()}.json", out)
        summ["report_path"] = str(p)
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print_compare(rows, summ, show_rows=not args.no_rows)
        if args.write_report:
            print(f"report: {summ['report_path']}")
    if summ["n_with_new_run"] == 0:
        return 2
    return 0 if summ["aggregate_improved"] else 1


if __name__ == "__main__":
    sys.exit(main())
