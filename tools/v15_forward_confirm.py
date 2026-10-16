#!/usr/bin/env python3
"""v15_forward_confirm — did the REAL scripts, run forward, make the chart's trades and reproduce its numbers?

USER 2026-10-16: *"dont stop until you have confirmed forward testing on the actual scripts makes the correct
trades and confirms the numbers"*.

The chart audit proved a chart's headline equals its own drawn trades. That is ARITHMETIC. It says nothing about
whether those trades are real. This closes the loop by collecting what `tools/v15_trade_parity.py` measured on
S1/s2 — where the live leg is `backtest_v12_engine.run_one`, i.e. the actual `ez_manage.process_position` /
`tradier_manage.process_position` replayed bar by bar — and scoring each sym_side on three questions:

  NUMBERS  does the vector leg reproduce the published chart's gain/trades on the SAME frozen window?
  TRADES   does the live call path take the same round trips as the vector leg (the parity verdict)?
  LIVE     is the sym_side even in the live trading universe? (a board for a symbol live never trades is
           unverifiable by construction, and must never be reported as confirmed)

A sym_side is CONFIRMED only when all three pass. Anything else is named with the reason — never rounded up.

USAGE
    python3 tools/v15_forward_confirm.py --pull            # scp the reports off S1/s2 first
    python3 tools/v15_forward_confirm.py
"""
import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
LOCAL = ROOT / "data" / "reports" / "trade_parity_collected"
HOSTS = {"s1-int": "~/binance-sandbox/data/reports/trade_parity", "s2": "~/binance-sandbox/data/reports/trade_parity"}
EXTRA = {"s1-int": "/tmp/parity_frozen"}


def log(m):
    print(m, flush=True)


def pull():
    LOCAL.mkdir(parents=True, exist_ok=True)
    for host, d in list(HOSTS.items()) + list(EXTRA.items()):
        tgt = LOCAL / host.replace("-", "_") / Path(d).name
        tgt.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10", f"{host}:{d}/", str(tgt) + "/"], capture_output=True, text=True, timeout=300)
        log(f"[pull] {host}:{d} -> {tgt} rc={r.returncode} {r.stderr.strip()[:120]}")


def chart_truth_for(symside):
    """The published chart's own numbers, for the newest chart of this sym_side."""
    from tools.opt.chart_truth import parse_js_const, recompute

    cands = sorted(SHEET_DIR.glob(f"{symside}_bh*_30d_matrix.html"), key=lambda p: p.stat().st_mtime)
    if not cands:
        return None, None
    p = cands[-1]
    s = p.read_text(errors="replace")
    t, e, c = parse_js_const(s, "TRADES"), parse_js_const(s, "EVENTS") or [], parse_js_const(s, "CLOSE")
    if t is None or c is None:
        return p.name, None
    lab = parse_js_const(s, "LABELS") or []
    r = recompute(t, e, c, symside.endswith("_LONG"))
    r["window"] = f"{lab[0]} -> {lab[-1]}" if lab else None
    return p.name, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "v15_forward_confirm.json"))
    args = ap.parse_args()
    if args.pull:
        pull()
    reports = sorted(glob.glob(str(LOCAL / "**" / "*.json"), recursive=True))
    log(f"[confirm] {len(reports)} parity report(s) collected")
    rows = []
    for f in reports:
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        ss = d.get("sym_side") or d.get("symside")
        if not ss:
            continue
        vec, live = d.get("vec") or {}, d.get("live") or {}
        chart_name, ct = chart_truth_for(ss)
        in_uni = bool((d.get("live_universe") or {}).get("in_universe"))
        frozen = d.get("npz_frozen_dir")
        row = {
            "sym_side": ss,
            "report": Path(f).name,
            "frozen_window": bool(frozen),
            "in_live_universe": in_uni,
            "chart": chart_name,
            "chart_gain": round(ct["gain_pct"], 4) if ct else None,
            "chart_trades": ct["trades"] if ct else None,
            "chart_window": ct.get("window") if ct else None,
            "vec_gain": vec.get("gain_pct"),
            "vec_trades": vec.get("trades"),
            "live_gain": live.get("gain_pct"),
            "live_trades": live.get("trades"),
            "vec_trips": len(d.get("vec_trips_all") or []),
            "live_trips": len(d.get("live_trips_all") or []),
            "matched": d.get("matched_n") if d.get("matched_n") is not None else len(d.get("matched") or []),
            "verdict_harness": d.get("verdict"),
            "npz_md5": (d.get("npz") or {}).get("md5"),
        }
        # NUMBERS: vec must reproduce the chart on the same window
        if ct and vec.get("gain_pct") is not None:
            row["numbers_drift"] = round(abs(float(vec["gain_pct"]) - float(ct["gain_pct"])), 4)
            row["numbers_ok"] = row["numbers_drift"] <= args.tol and int(vec.get("trades") or -1) == int(ct["trades"])
        else:
            row["numbers_ok"] = None
        # TRADES: the live call path must take them
        row["trades_ok"] = bool(row["live_trips"]) and str(d.get("verdict")).upper() == "PASS"
        if not in_uni:
            row["status"] = "UNVERIFIABLE — symbol is not in the live trading universe, the live leg can never trade it"
        elif row["numbers_ok"] and row["trades_ok"]:
            row["status"] = "CONFIRMED"
        elif row["numbers_ok"] is False:
            row["status"] = f"NUMBERS DIFFER — vec {vec.get('gain_pct')} vs chart {row['chart_gain']} (drift {row.get('numbers_drift')})"
        elif not row["live_trips"]:
            row["status"] = "LIVE TOOK NO TRADES — the real call path did not reproduce a single round trip"
        else:
            row["status"] = f"TRADES DIFFER — harness verdict {d.get('verdict')} ({row['matched']}/{row['vec_trips']} matched)"
        rows.append(row)
    rows.sort(key=lambda r: (r["status"].split()[0], r["sym_side"]))
    Path(args.out).write_text(json.dumps({"rows": rows}, indent=1, default=str))
    log("")
    log(f"{'sym_side':22s} {'uni':3s} {'frz':3s} {'chart':>9s} {'vec':>9s} {'live':>9s} {'trips v/l/m':>13s}  status")
    for r in rows:
        log(f"{r['sym_side']:22s} {'Y' if r['in_live_universe'] else 'n':3s} {'Y' if r['frozen_window'] else 'n':3s} "
            f"{(r['chart_gain'] if r['chart_gain'] is not None else float('nan')):9.2f} "
            f"{(r['vec_gain'] if r['vec_gain'] is not None else float('nan')):9.2f} "
            f"{(r['live_gain'] if r['live_gain'] is not None else float('nan')):9.2f} "
            f"{str(r['vec_trips'])+'/'+str(r['live_trips'])+'/'+str(r['matched']):>13s}  {r['status'][:80]}")
    n_conf = sum(1 for r in rows if r["status"] == "CONFIRMED")
    log("")
    log(f"[confirm] CONFIRMED {n_conf}/{len(rows)} · report {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
