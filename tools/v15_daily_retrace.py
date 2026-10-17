#!/usr/bin/env python3
"""v15_daily_retrace — the settings applied each morning -> the trades they SHOULD have produced -> what Binance actually did.

USER 2026-10-17: "most binance accounts are still losing money every day even though they are only trading metrics that show
they should be making money ... a script that does a full retrace of the settings applied every morning before market open
(found in the sql on s1) for each symbol and their executions on binance plus a rerun of all the numbers (metrics) presented
as results vs the same trades running on the exchange ... compare whether every trade took place at the bar of the switches
settings of the symbol and side for that day and exited at the same bar ... how many exit at a gain or a loss ... plus compare
the paper trade results as they should be absolutely identical to what the v15_pilot.py and the TEMPLATE_* calculated".

FOUR LAYERS, each measured, never asserted:
  L1 CLAIM     per_sym_store.db `per_sym_history` / `per_sym_active` (the SQL): every set ever written to the live store with
               the gain/trades/dd/tim/bh the board claimed for it, and when it was applied. The timeline is cut into segments
               (one per distinct md5(overrides)); the set in force at --preopen-hour is that day's morning set.
  L2 PAPER     that exact set re-run by the vector engine on the bars LIVE decided on: the twin's append-only frozen store
               (/tmp/vec_exact_frozen/<SYM>.pkl, live_twins.vec_exact) -> vec_exact._prepare_from_arrays ->
               evaluate_v12.evaluate_prepared(include_ledger=True) = the paper forward test at the exact vectorized size.
               This is the same call live itself makes every 15m bar (PARITY_VEC_EXACT_MODE), so it is not a reimplementation.
  L3 TWIN LOG  the [VEC_EXACT] decision lines live actually emitted (optional, Mac logs only; coverage is reported).
  L4 EXCHANGE  Binance round trips rebuilt from userTrades + allOrders + positionRisk (tools/binance_fills_fetch.py), folded
               by tools/live_vs_board_parity.live_trips. The internal ledger (data/per_trade_returns, data/history) is never
               used as truth (BIBLE §106).

WHAT IT ANSWERS
  * claim vs re-measurement: does the number that put the set live survive a re-run on live's own bars (L1 vs L2)?
  * bar equality: for every exchange trip, was the ENTRY at the bar the set decided on, and the EXIT at the same bar (L2 vs L4)?
  * gain/loss: how many exits ended green/red on each side, and where the two disagree in sign.
  * size: the matched vector trips priced at live's notional vs at the vector's own notional (sizing is not parity, BIBLE §68.3a,
    so it is reported as a separate term and never as a divergence).
  * a verdict per sym_side x account, and per account.

HONEST LIMITS (printed into the report, never papered over):
  * the twin store spans ~21 d; a 30 d board claim is a different window. Window length is reported next to every claim, and
    the metric verdict only fires on differences no window can explain (sign flip, or trades/day off by more than --trades-ratio).
  * L2 runs the segment's set over the whole store from the twin anchor (that is what the sheet claims); a set that went live
    mid-window therefore inherits a position state the real day may not have had. Trips are attributed to the segment that was
    in force at their decision bar, which is the closest honest cut.
  * accounts are compared per account because each one sizes differently; the hedge accounts carry pre-window positions, which
    are counted as carried_in and never matched.

Read-only: SQLite is opened mode=ro, the frozen store is read with pickle (never re-frozen), no order path is imported.
Usage:
    python3 tools/binance_fills_fetch.py --days 8 --orders --out data/reports/retrace/binance_fills_<stamp>.json
    python3 tools/v15_daily_retrace.py --fills data/reports/retrace/binance_fills_<stamp>.json --days 7
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import pickle
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
BAR_S = 900.0
DB_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
FROZEN_DIR = Path(os.environ.get("VEC_EXACT_FROZEN_DIR", "/tmp/vec_exact_frozen"))
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")


def md5_of(ov) -> str:
    return hashlib.md5(json.dumps(json.loads(json.dumps(ov or {}, default=str)), sort_keys=True).encode()).hexdigest()


def bar_of(ts) -> int:
    return int(float(ts) // BAR_S)


def iso(ts) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""


def day_of(ts) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def sign_name(x) -> str:
    return "GAIN" if (x or 0) > 0 else ("LOSS" if (x or 0) < 0 else "FLAT")


def set_segments(con, sym_side: str, t_from: float, t_to: float, active: dict):
    """The SQL trail of applied sets, cut into [t0, t1) segments of one md5 each, clipped to the window."""
    sql = ("select coalesce(epoch, strftime('%s', replace(replace(updated_at,'T',' '),'Z',''))) ep, updated_at, overrides_json,"
           " gain_pct, baseline_gain, bh_pct, trades, max_dd_pct, tim_pct, valid, meta_json"
           " from per_sym_history where sym_side=? and overrides_json is not null order by ep")
    seq = []
    for ep, upd, oj, gain, base, bh, trades, dd, tim, valid, meta in con.execute(sql, (sym_side,)):
        try:
            ov = json.loads(oj or "{}")
        except Exception:
            continue
        if not ov:
            continue
        try:
            ep = float(ep)
        except Exception:
            continue
        src = ""
        try:
            src = str((json.loads(meta or "{}") or {}).get("source") or "")[:60]
        except Exception:
            pass
        row = {"t0": ep, "updated_at": upd, "md5": md5_of(ov), "ov": ov, "source": src,
               "claim": {"gain_pct": gain, "baseline_gain": base, "bh_pct": bh, "trades": trades, "max_dd_pct": dd, "tim_pct": tim, "valid": valid}}
        if seq and seq[-1]["md5"] == row["md5"]:
            continue
        seq.append(row)
    act = active.get(sym_side)
    if act and (not seq or seq[-1]["md5"] != act["md5"]):
        seq.append({"t0": act["epoch"], "updated_at": act["updated_at"], "md5": act["md5"], "ov": act["ov"], "source": "per_sym_active",
                    "claim": {"gain_pct": act.get("gain_pct"), "baseline_gain": act.get("baseline_30d"), "bh_pct": act.get("bh_pct"), "trades": act.get("trades"), "max_dd_pct": act.get("max_dd_pct"), "tim_pct": None, "valid": None}})
    out = []
    for i, r in enumerate(seq):
        t1 = seq[i + 1]["t0"] if i + 1 < len(seq) else float("inf")
        if t1 <= t_from or r["t0"] >= t_to:
            continue
        s = dict(r)
        s["seg_from"], s["seg_to"] = max(r["t0"], t_from), min(t1, t_to)
        s["applied_before_window"] = r["t0"] < t_from
        out.append(s)
    if not out and seq:
        last = dict(seq[-1])
        last["seg_from"], last["seg_to"] = t_from, t_to
        last["applied_before_window"] = True
        out = [last]
    return out


def load_active(con):
    act = {}
    for ss, upd, oj, g, trades, dd, bh, base in con.execute(
        "select sym_side,updated_at,overrides_json,acc_gain_pct,trades,max_dd_pct,bh_pct,baseline_30d from per_sym_active where cat_side like 'CRYPTO%'"
    ):
        try:
            ov = json.loads(oj or "{}")
        except Exception:
            ov = {}
        if not ov:
            continue
        try:
            ep = dt.datetime.fromisoformat(str(upd).replace("Z", "+00:00")).timestamp()
        except Exception:
            ep = 0.0
        act[ss] = {"updated_at": upd, "epoch": ep, "md5": md5_of(ov), "ov": ov, "gain_pct": g, "trades": trades, "max_dd_pct": dd, "bh_pct": bh, "baseline_30d": base}
    return act


def rerun_symbol(symbol: str, jobs, errors):
    """One prepare per symbol (the twin's own frozen bars), then one evaluate per (sym_side, set). Returns {(ss, md5): result}."""
    from live_twins import vec_exact as VX
    from tools.opt import evaluate_v12 as E

    out = {}
    path = FROZEN_DIR / f"{symbol}.pkl"
    if not path.exists():
        for ss, md5, _ov in jobs:
            out[(ss, md5)] = {"status": "NO_TWIN_STORE"}
        return out
    try:
        with open(path, "rb") as fh:
            raw = pickle.load(fh)
    except Exception as exc:
        errors.append(f"{symbol}: twin store read failed: {exc}")
        for ss, md5, _ov in jobs:
            out[(ss, md5)] = {"status": "TWIN_STORE_UNREADABLE"}
        return out
    prep_cache = {}
    for ss, md5, ov in jobs:
        try:
            if ss not in prep_cache:
                prep_cache[ss] = VX._prepare_from_arrays(ss, dict(raw))
            prep = prep_cache[ss]
            if prep is None:
                out[(ss, md5)] = {"status": "PREPARE_FAILED"}
                continue
            ts = prep["npz_prepared"]["timestamps"]
            res = E.evaluate_prepared(prep, ov, include_ledger=True)
            out[(ss, md5)] = {"status": "OK", "store_first": float(ts[0]), "store_last": float(ts[-1]), "bars": int(len(ts)),
                              "metrics": {k: res.get(k) for k in ("gain_pct", "trades", "bh_pct", "max_dd_pct", "tim_pct", "wr_pct", "valid", "invalid_reason", "pool_sharpe", "gain_dollars", "net_gain_dollars", "peak_notional", "data_days", "window_days", "mean_deployed")},
                              "ledger": res.get("execution_ledger") or []}
        except Exception as exc:
            errors.append(f"{ss} {md5[:8]}: eval failed: {exc}")
            out[(ss, md5)] = {"status": f"EVAL_ERROR:{type(exc).__name__}"}
        finally:
            prep_cache.pop("__none__", None)
    return out


def match(vec_trips, live_trips, tol_bars: int, tol_early: int = 1):
    """Greedy nearest-bar assignment. A vector decision at bar b is executed live in bar b+1, so delta 0 = same bar.

    Causal: live cannot execute a decision that had not been taken yet, so a fill more than --tol-early bars BEFORE the
    decision bar is never the same trade (tol_early covers a fill landing on the bar edge / clock skew)."""
    cand = []
    for i, v in enumerate(vec_trips):
        for j, l in enumerate(live_trips):
            d = (bar_of(l["entry_ts"]) - 1) - bar_of(v["bar_ts"])
            if -tol_early <= d <= tol_bars:
                cand.append((abs(d), abs(l["entry_ts"] - v["entry_ts"]), i, j, d))
    cand.sort()
    uv, ul, pairs = set(), set(), []
    for _ad, _dt_, i, j, d in cand:
        if i in uv or j in ul:
            continue
        uv.add(i)
        ul.add(j)
        pairs.append((i, j, d))
    return pairs, [i for i in range(len(vec_trips)) if i not in uv], [j for j in range(len(live_trips)) if j not in ul]


def exit_delta(v, l):
    """delta = live's decision bar minus the vector's exit bar.

    A vector exit decided on bar b is executable from b's close, i.e. during bar b+1 -> delta 0 = ON_BAR. A live fill INSIDE
    bar b (delta -1) is earlier than the vector could ever act: that is the intrabar stop/target semantics (the exchange stop
    is a MARK-price STOP_MARKET while the engine checks the frozen stop on the 15m close, BIBLE 106 C1), counted separately
    and never as lateness. delta >= 1 is a genuinely late exit, delta <= -2 a genuinely early one."""
    vb = bar_of(float(v["exit_ts"]) - BAR_S) if v.get("exit_ts") else None
    lb = (bar_of(float(l["exit_ts"])) - 1) if l.get("exit_ts") else None
    if vb is None and lb is None:
        return None, "BOTH_OPEN"
    if vb is None:
        return None, "VEC_STILL_HOLDS_LIVE_CLOSED"
    if lb is None:
        return None, "LIVE_STILL_HOLDS_VEC_CLOSED"
    d = lb - vb
    return d, "ON_BAR" if d == 0 else ("INTRABAR_SAME_BAR" if d == -1 else (f"LIVE_LATE_{d}b" if d > 0 else f"LIVE_EARLY_{-d}b"))


def trip_view(l, v, d_entry, d_exit, how):
    return {"day": day_of(l["entry_ts"]), "live_entry": iso(l["entry_ts"]), "live_exit": iso(l["exit_ts"]) if l.get("exit_ts") else "OPEN",
            "vec_bar": iso(v["bar_ts"]) if v else "", "vec_exit_bar": iso(float(v["exit_ts"]) - BAR_S) if v and v.get("exit_ts") else ("OPEN" if v else ""),
            "entry_bar_delta": d_entry, "exit_bar_delta": d_exit, "exit_match": how,
            "live_qty": round(l["open_qty"], 8), "live_notional": round(l["notional"] or 0, 2), "live_net_pnl": round(l["net_pnl"], 4), "live_net_pct": l.get("net_pct"),
            "live_result": sign_name(l["net_pnl"]) if not l["is_open"] else "OPEN",
            "vec_notional": round(v["deployed"], 2) if v else None, "vec_pnl": round(v["pnl_dollars"], 4) if v else None, "vec_pct": v.get("pct") if v else None,
            "vec_result": (sign_name(v["pnl_dollars"]) if v.get("exit_ts") else "OPEN") if v else "",
            "vec_entry_reason": (v.get("entry_reason") or "")[:70] if v else "", "vec_exit_reason": (v.get("exit_reason") or "")[:70] if v else "",
            "live_trade_ids": (l.get("fills") or [])[:2], "live_exit_orders": [o.get("orderId") for o in (l.get("exit_orders") or [])][-2:]}


def metric_verdict(claim, rerun, trades_ratio: float):
    """Only differences no window length can explain. Everything else is reported as a number, not a verdict."""
    cg, ct = claim.get("gain_pct"), claim.get("trades")
    if rerun.get("status") != "OK":
        return "NO_RERUN", None, None
    m = rerun["metrics"]
    rg, rt = m.get("gain_pct"), m.get("trades")
    if cg is None or rg is None:
        return "NO_CLAIM", rg, rt
    d = float(rg) - float(cg)
    if float(cg) > 0 and float(rg) <= 0:
        return "CLAIM_POSITIVE_RERUN_NEGATIVE", rg, rt
    span = max(1e-9, float(rerun.get("store_last", 0) - rerun.get("store_first", 0)) / 86400.0)
    cw = float(claim.get("window_days") or m.get("window_days") or 30.0)
    if ct and rt is not None and cw > 0:
        cpd, rpd = float(ct) / cw, float(rt) / span
        if cpd > 0 and (rpd / cpd > trades_ratio or (rpd > 0 and cpd / rpd > trades_ratio)):
            return f"TRADE_RATE_OFF_{cpd:.1f}vs{rpd:.1f}_per_day", rg, rt
        if rpd == 0 and cpd > 0:
            return "RERUN_HAS_NO_TRADES", rg, rt
    if abs(d) > 0.48:
        return "WINDOW_DIFFERENT", rg, rt
    return "REPRODUCED", rg, rt


def parse_twin_log(accounts, t_from: float):
    """[VEC_EXACT] <SYMSIDE> bar=<ts> k=.. acts=[..] lines live emitted. Mac logs only; absence is reported, not assumed."""
    pat = re.compile(r"^(\d\d) (\d\d):(\d\d):(\d\d) - \w+ - \[(\w+)\] .*\[VEC_EXACT\] (\S+) bar=(\d+) k=\d+ acts=(\[.*?\])")
    seen = collections.defaultdict(set)
    files = []
    for a in accounts:
        files += sorted(glob.glob(os.path.expanduser(f"~/logs/ez_manage_{a}.log*")))
    for fp in files:
        try:
            with open(fp, errors="ignore") as fh:
                for line in fh:
                    if "[VEC_EXACT] " not in line or " acts=" not in line:
                        continue
                    m = pat.match(line)
                    if not m:
                        continue
                    acts = m.group(8)
                    if acts in ("[]", "[ ]"):
                        continue
                    bar = float(m.group(7))
                    if bar < t_from:
                        continue
                    seen[(m.group(5), m.group(6))].add(bar)
        except Exception:
            continue
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", required=True, help="tools/binance_fills_fetch.py output (exchange truth)")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--symsides", default="", help="comma list, default = every crypto sym_side with a live set or a live trip")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "reports" / "retrace"))
    ap.add_argument("--tag", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ"))
    ap.add_argument("--tol-bars", type=int, default=8, help="max entry bar delta still counted as the same trade (8 = 2h late)")
    ap.add_argument("--tol-early", type=int, default=1, help="bars a fill may precede its decision bar (bar-edge/clock skew)")
    ap.add_argument("--trades-ratio", type=float, default=3.0, help="claim/rerun trades-per-day ratio that flags the claim")
    ap.add_argument("--preopen-hour", type=float, default=6.0, help="UTC hour of the daily chain apply = that day's morning set")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-trips", type=int, default=40, help="per (sym_side, account) trip rows kept in the JSON detail")
    ap.add_argument("--no-logs", action="store_true")
    a = ap.parse_args()
    t_start = time.time()
    accounts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    from tools import live_vs_board_parity as LVB

    fills = json.load(open(a.fills))
    now = float(fills.get("end_ms", time.time() * 1000)) / 1000.0
    t_from = now - a.days * 86400.0
    live = [t for t in LVB.live_trips(fills, LVB.bnb_price_fn()) if t["acct"] in accounts]
    by_key = collections.defaultdict(list)
    for t in live:
        by_key[(f"{t['symbol']}_{t['side']}", t["acct"])].append(t)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    active = load_active(con)
    try:
        tradeable = set(json.load(open(ROOT / "tradeable_keys.json")))
    except Exception:
        tradeable = set()
    wanted = [x.strip().upper() for x in a.symsides.split(",") if x.strip()]
    sym_sides = sorted(set(wanted) if wanted else (set(active) | {k[0] for k in by_key}))
    errors, segments = [], {}
    for ss in sym_sides:
        segments[ss] = set_segments(con, ss, t_from, now, active)
    jobs = collections.defaultdict(list)
    for ss, segs in segments.items():
        sym = ss.rsplit("_", 1)[0]
        for s in segs:
            if (ss, s["md5"]) not in [(x[0], x[1]) for x in jobs[sym]]:
                jobs[sym].append((ss, s["md5"], s["ov"]))
    print(f"[retrace] window {iso(t_from)} -> {iso(now)}  sym_sides={len(sym_sides)}  symbols={len(jobs)}  sets={sum(len(v) for v in jobs.values())}  live_trips={len(live)}", flush=True)
    reruns = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(rerun_symbol, sym, j, errors): sym for sym, j in jobs.items()}
        done = 0
        for f in as_completed(futs):
            try:
                reruns.update(f.result())
            except Exception as exc:
                errors.append(f"{futs[f]}: rerun crashed: {exc}")
            done += 1
            if done % 25 == 0:
                print(f"[retrace] reruns {done}/{len(futs)} symbols ({time.time() - t_start:.0f}s)", flush=True)
    twin_log = {} if a.no_logs else parse_twin_log(accounts, t_from)
    rows, daily, detail, trips_all = [], [], {}, []
    late_reasons, late_usd = collections.Counter(), collections.Counter()
    for ss in sym_sides:
        segs = segments.get(ss) or []
        sym = ss.rsplit("_", 1)[0]
        srec = {"sym_side": ss, "segments": [], "accounts": {}}
        morning = {}
        for s in segs:
            rr = reruns.get((ss, s["md5"])) or {"status": "NO_RERUN"}
            mv, rg, rt = metric_verdict(s["claim"], rr, a.trades_ratio)
            m = (rr.get("metrics") or {}) if rr.get("status") == "OK" else {}
            srec["segments"].append({"from": iso(s["seg_from"]), "to": iso(s["seg_to"]) if s["seg_to"] != float("inf") else "now",
                                     "applied_at": s["updated_at"], "applied_before_window": s["applied_before_window"], "set_md5": s["md5"][:8], "writer": s["source"],
                                     "claim": s["claim"], "rerun_status": rr.get("status"),
                                     "rerun": {"gain_pct": rg, "trades": rt, "bh_pct": m.get("bh_pct"), "max_dd_pct": m.get("max_dd_pct"), "tim_pct": m.get("tim_pct"), "wr_pct": m.get("wr_pct"), "pool_sharpe": m.get("pool_sharpe"), "valid": m.get("valid"), "invalid_reason": m.get("invalid_reason"), "span_days": round((rr.get("store_last", 0) - rr.get("store_first", 0)) / 86400.0, 2) if rr.get("status") == "OK" else None},
                                     "metric_verdict": mv})
        day = dt.datetime.fromtimestamp(t_from, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        while day.timestamp() < now:
            stamp = day.timestamp() + a.preopen_hour * 3600.0
            seg = next((s for s in segs if s["t0"] <= stamp and stamp < s["seg_to"]), None) or next((s for s in segs if s["seg_from"] <= stamp), None)
            morning[day.strftime("%Y-%m-%d")] = seg
            day += dt.timedelta(days=1)
        srec["morning_set"] = {d: ((s or {}).get("md5") or "")[:8] for d, s in morning.items()}
        traded = {k[1] for k in by_key if k[0] == ss}
        allowed = {acct for acct in accounts if f"{acct}:{ss}" in tradeable}
        accts = sorted(traded | allowed) or (["(no-account)"] if active.get(ss) else [])
        for acct in accts:
            lts = by_key.get((ss, acct)) or []
            carried = [t for t in lts if t["pre_window"] or t["entry_ts"] < t_from]
            agg = collections.Counter()
            pair_rows, seg_stats = [], []
            for s in segs:
                rr = reruns.get((ss, s["md5"])) or {}
                if rr.get("status") != "OK":
                    continue
                last = float(rr["store_last"])
                vts = [t for t in LVB.board_trips(rr["ledger"]) if s["seg_from"] <= t["bar_ts"] < s["seg_to"] and t["bar_ts"] <= last]
                lin = [t for t in lts if t not in carried and s["seg_from"] + BAR_S <= t["entry_ts"] < s["seg_to"] + BAR_S and t["entry_ts"] <= last + BAR_S * (a.tol_bars + 1)]
                pairs, vonly, lonly = match(vts, lin, a.tol_bars, a.tol_early)
                for i, j, d in pairs:
                    v, l = vts[i], lin[j]
                    dx, how = exit_delta(v, l)
                    pr = trip_view(l, v, d, dx, how)
                    pr.update(sym_side=ss, acct=acct, set_md5=s["md5"][:8], kind="MATCHED")
                    pair_rows.append(pr)
                    agg["matched"] += 1
                    agg["same_entry_bar"] += 1 if d == 0 else 0
                    agg["entry_bar_le1"] += 1 if abs(d) <= 1 else 0
                    agg["same_exit_bar"] += 1 if dx == 0 else 0
                    agg["exit_intrabar"] += 1 if dx == -1 else 0
                    agg["exit_on_or_intrabar"] += 1 if dx in (0, -1) else 0
                    agg["exit_late"] += 1 if (dx is not None and dx >= 1) else 0
                    agg["exit_early"] += 1 if (dx is not None and dx <= -2) else 0
                    agg["exit_open_disagree"] += 1 if (dx is None and how != "BOTH_OPEN") else 0
                    agg["both_same_bar"] += 1 if (d == 0 and dx in (0, -1)) else 0
                    if dx is not None and dx >= 1:
                        late_reasons[(v.get("exit_reason") or "?").split("_g")[0][:38]] += 1
                        late_usd[(v.get("exit_reason") or "?").split("_g")[0][:38]] += l["net_pnl"] if not l["is_open"] else 0.0
                    if not l["is_open"]:
                        agg["live_gain" if l["net_pnl"] > 0 else "live_loss"] += 1
                    if v.get("exit_ts"):
                        agg["vec_gain" if v["pnl_dollars"] > 0 else "vec_loss"] += 1
                    if not l["is_open"] and v.get("exit_ts") and sign_name(l["net_pnl"]) != sign_name(v["pnl_dollars"]):
                        agg["sign_flip"] += 1
                        agg["sign_flip_usd"] += l["net_pnl"]
                    agg["matched_live_pnl"] += l["net_pnl"] if not l["is_open"] else 0.0
                    if v.get("pct") is not None:
                        agg["matched_vec_pnl_at_live_size"] += v["pct"] / 100.0 * (l["notional"] or 0.0)
                    agg["matched_vec_pnl_at_vec_size"] += v["pnl_dollars"] if v.get("exit_ts") else 0.0
                    agg["entry_bar_delta_abs"] += abs(d)
                for j in lonly:
                    l = lin[j]
                    pr = trip_view(l, None, None, None, "LIVE_ONLY")
                    pr.update(sym_side=ss, acct=acct, set_md5=s["md5"][:8], kind="LIVE_ONLY")
                    pair_rows.append(pr)
                    agg["live_only"] += 1
                    agg["live_only_pnl"] += l["net_pnl"] if not l["is_open"] else 0.0
                    if not l["is_open"]:
                        agg["live_only_gain" if l["net_pnl"] > 0 else "live_only_loss"] += 1
                for i in vonly:
                    v = vts[i]
                    agg["vec_only"] += 1
                    agg["vec_only_pnl_at_vec_size"] += v["pnl_dollars"] if v.get("exit_ts") else 0.0
                    if v.get("exit_ts"):
                        agg["vec_only_gain" if v["pnl_dollars"] > 0 else "vec_only_loss"] += 1
                    lg = twin_log.get((acct, ss)) or set()
                    if lg and not any(abs(b - v["bar_ts"]) <= BAR_S for b in lg):
                        agg["vec_only_no_twin_decision"] += 1
                agg["n_vec"] += len(vts)
                agg["n_live"] += len(lin)
                seg_stats.append({"set_md5": s["md5"][:8], "vec_trips": len(vts), "live_trips": len(lin), "matched": len(pairs)})
            for pr in pair_rows:
                daily.append({"day": pr["day"], "sym_side": ss, "acct": acct, "set_md5": pr["set_md5"], "kind": pr["kind"],
                              "entry_bar_delta": pr["entry_bar_delta"], "exit_bar_delta": pr["exit_bar_delta"], "exit_match": pr["exit_match"],
                              "live_result": pr["live_result"], "vec_result": pr["vec_result"], "live_net_pnl": pr["live_net_pnl"],
                              "live_notional": pr["live_notional"], "vec_notional": pr["vec_notional"], "vec_pnl": pr["vec_pnl"],
                              "vec_entry_reason": pr["vec_entry_reason"], "vec_exit_reason": pr["vec_exit_reason"]})
            trips_all.extend(pair_rows)
            closed = [t for t in lts if not t["is_open"] and t not in carried]
            live_net = sum(t["net_pnl"] for t in closed)
            mv_set = [sg["metric_verdict"] for sg in srec["segments"]]
            verdict = _verdict(agg, mv_set, len(lts), srec["segments"])
            rec = {"n_live_trips": agg["n_live"], "carried_in": len(carried), **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in agg.items()},
                   "live_net_pnl_usd": round(live_net, 4), "verdict": verdict,
                   "twin_log_bars_with_actions": len(twin_log.get((acct, ss)) or set()) if twin_log else None,
                   "segments": seg_stats, "trips": pair_rows[: a.max_trips]}
            srec["accounts"][acct] = rec
            rows.append({"sym_side": ss, "acct": acct, "verdict": verdict,
                         "sets_in_window": len(srec["segments"]), "set_md5_latest": (srec["segments"][-1]["set_md5"] if srec["segments"] else ""),
                         "metric_verdicts": ";".join(sorted(set(mv_set))), "claim_gain_pct": (srec["segments"][-1]["claim"]["gain_pct"] if srec["segments"] else None),
                         "claim_trades": (srec["segments"][-1]["claim"]["trades"] if srec["segments"] else None),
                         "rerun_gain_pct": (srec["segments"][-1]["rerun"]["gain_pct"] if srec["segments"] else None),
                         "rerun_trades": (srec["segments"][-1]["rerun"]["trades"] if srec["segments"] else None),
                         "rerun_span_days": (srec["segments"][-1]["rerun"]["span_days"] if srec["segments"] else None),
                         "rerun_wr_pct": (srec["segments"][-1]["rerun"]["wr_pct"] if srec["segments"] else None),
                         "n_vec_trips": agg["n_vec"], "n_live_trips": agg["n_live"], "matched": agg["matched"],
                         "same_entry_bar": agg["same_entry_bar"], "entry_bar_le1": agg["entry_bar_le1"],
                         "same_exit_bar": agg["same_exit_bar"], "exit_bar_le1": agg["exit_bar_le1"], "both_same_bar": agg["both_same_bar"],
                         "exit_intrabar": agg["exit_intrabar"], "exit_late": agg["exit_late"], "exit_early": agg["exit_early"],
                         "exit_open_disagree": agg["exit_open_disagree"],
                         "vec_only": agg["vec_only"], "vec_only_no_twin_decision": agg["vec_only_no_twin_decision"],
                         "live_only": agg["live_only"], "carried_in": len(carried),
                         "live_gain": agg["live_gain"], "live_loss": agg["live_loss"], "vec_gain": agg["vec_gain"], "vec_loss": agg["vec_loss"],
                         "sign_flip": agg["sign_flip"], "live_net_pnl_usd": round(live_net, 4),
                         "matched_live_pnl": round(agg["matched_live_pnl"], 4), "matched_vec_pnl_at_live_size": round(agg["matched_vec_pnl_at_live_size"], 4),
                         "matched_vec_pnl_at_vec_size": round(agg["matched_vec_pnl_at_vec_size"], 4),
                         "live_only_pnl": round(agg["live_only_pnl"], 4), "vec_only_pnl_at_vec_size": round(agg["vec_only_pnl_at_vec_size"], 4)})
        detail[ss] = srec
    stamp = a.tag
    jpath, cpath, dpath, mpath = (out_dir / f"retrace_{stamp}.json", out_dir / f"retrace_symside_{stamp}.csv",
                                  out_dir / f"retrace_daily_{stamp}.csv", out_dir / f"retrace_{stamp}.md")
    meta = {"generated_utc": iso(time.time()), "window_from": iso(t_from), "window_to": iso(now), "days": a.days,
            "fills": str(a.fills), "db": a.db, "frozen_dir": str(FROZEN_DIR), "accounts": accounts,
            "tol_bars": a.tol_bars, "trades_ratio": a.trades_ratio, "preopen_hour": a.preopen_hour,
            "twin_log_keys": len(twin_log), "errors": errors[:200], "n_errors": len(errors),
            "caveats": ["L2 runs each segment's set over the whole twin store from its anchor (what the sheet claims); trips are attributed to the segment in force at their decision bar.",
                        "Claim windows (usually 30 d) differ from the twin store span; span_days is reported next to every rerun.",
                        "Sizing is not parity (BIBLE 68.3a): matched_vec_pnl_at_live_size vs _at_vec_size is the size term, never a divergence verdict.",
                        "Exchange truth only (userTrades/allOrders/positionRisk); the internal ledger is never used as truth."]}
    jpath.write_text(json.dumps({"meta": meta, "sym_sides": detail}, default=str, indent=1))
    if rows:
        with open(cpath, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["live_net_pnl_usd"]))
    if daily:
        with open(dpath, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(daily[0].keys()))
            w.writeheader()
            w.writerows(sorted(daily, key=lambda r: (r["day"], r["sym_side"], r["acct"])))
    mpath.write_text(report_md(meta, rows, detail, trips_all, daily, late_reasons, late_usd))
    print(f"[retrace] wrote {jpath}\n[retrace] wrote {cpath}\n[retrace] wrote {dpath}\n[retrace] wrote {mpath}")
    print(f"[retrace] rows={len(rows)} trips={len(trips_all)} errors={len(errors)} elapsed={time.time() - t_start:.0f}s")


def _verdict(agg, metric_verdicts, n_live_trips, segs):
    if not segs:
        return "NO_LIVE_SET_IN_SQL"
    if all(s["rerun_status"] != "OK" for s in segs):
        return "NO_RERUN_" + str(segs[-1]["rerun_status"])
    if "CLAIM_POSITIVE_RERUN_NEGATIVE" in metric_verdicts:
        return "CLAIM_DOES_NOT_SURVIVE_RERUN"
    if any(str(v).startswith("TRADE_RATE_OFF") for v in metric_verdicts):
        return "CLAIM_TRADE_RATE_OFF"
    if agg["n_vec"] >= 3 and agg["matched"] / max(1, agg["n_vec"]) < 0.5:
        return "LIVE_MISSES_VEC_TRADES"
    if agg["live_only"] > max(1, agg["matched"]):
        return "LIVE_TRADES_WHAT_VEC_DID_NOT"
    if agg["matched"] >= 3 and agg["exit_on_or_intrabar"] / max(1, agg["matched"]) < 0.7:
        return "EXIT_BAR_DRIFT"
    if agg["matched"] >= 3 and agg["sign_flip"] / max(1, agg["matched"]) > 0.3:
        return "RESULT_SIGN_FLIP"
    if agg["matched"] >= 3 and agg["matched_live_pnl"] < 0 <= agg["matched_vec_pnl_at_vec_size"]:
        return "SAME_TRADES_LIVE_LOSES"
    if agg["matched"] == 0 and n_live_trips == 0:
        return "NO_TRADES_IN_WINDOW"
    return "FOLLOWS_VEC"


def report_md(meta, rows, detail, trips, daily, late_reasons=None, late_usd=None):
    L = []
    A = L.append
    A(f"# Daily retrace — settings applied -> paper (vector) -> Binance\n")
    A(f"Window **{meta['window_from']} -> {meta['window_to']}** ({meta['days']} d) · accounts {', '.join(meta['accounts'])} · generated {meta['generated_utc']}\n")
    A("Layers: **L1 claim** = the set + metrics the SQL store recorded when it went live · **L2 paper** = that set re-run by the")
    A("vector engine on the bars live decided on (twin frozen store, exact vectorized size) · **L4 exchange** = Binance round trips")
    A("from userTrades/allOrders/positionRisk. Internal ledgers are never used as truth.\n")
    tot = collections.Counter()
    per_acct = collections.defaultdict(collections.Counter)
    for r in rows:
        for k in ("n_vec_trips", "n_live_trips", "matched", "same_entry_bar", "entry_bar_le1", "same_exit_bar",
                  "both_same_bar", "vec_only", "vec_only_no_twin_decision", "live_only",
                  "live_gain", "live_loss", "vec_gain", "vec_loss", "sign_flip", "carried_in",
                  "exit_intrabar", "exit_late", "exit_early", "exit_open_disagree"):
            tot[k] += r[k] or 0
            per_acct[r["acct"]][k] += r[k] or 0
        for k in ("live_net_pnl_usd", "matched_live_pnl", "matched_vec_pnl_at_live_size", "matched_vec_pnl_at_vec_size", "live_only_pnl", "vec_only_pnl_at_vec_size"):
            tot[k] += r[k] or 0.0
            per_acct[r["acct"]][k] += r[k] or 0.0
        tot["rows"] += 1
        per_acct[r["acct"]]["rows"] += 1
    A("## Headline\n")
    A(f"- Binance net over the window (closed trips, fees in): **{tot['live_net_pnl_usd']:+.2f} $**")
    A(f"- exchange trips {tot['n_live_trips']} · vector trips for the same sets {tot['n_vec_trips']} · matched {tot['matched']}"
      f" ({100.0 * tot['matched'] / max(1, tot['n_live_trips']):.0f}% of live, {100.0 * tot['matched'] / max(1, tot['n_vec_trips']):.0f}% of vector)")
    A(f"- entries: ON the decision bar {tot['same_entry_bar']} ({100.0 * tot['same_entry_bar'] / max(1, tot['matched']):.0f}% of matched),"
      f" within 1 bar {tot['entry_bar_le1']}")
    A(f"- exits: ON the vector's bar {tot['same_exit_bar']} ({100.0 * tot['same_exit_bar'] / max(1, tot['matched']):.0f}%) ·"
      f" intrabar (stop/target filled inside the vector's bar) {tot['exit_intrabar']} · LATE {tot['exit_late']} · EARLY {tot['exit_early']} ·"
      f" one side still holding {tot['exit_open_disagree']} · entry AND exit on the bar {tot['both_same_bar']}")
    A(f"- exits: live GAIN {tot['live_gain']} / LOSS {tot['live_loss']} (WR {100.0 * tot['live_gain'] / max(1, tot['live_gain'] + tot['live_loss']):.0f}%)"
      f" vs vector GAIN {tot['vec_gain']} / LOSS {tot['vec_loss']} (WR {100.0 * tot['vec_gain'] / max(1, tot['vec_gain'] + tot['vec_loss']):.0f}%)"
      f" · sign flips on matched trips {tot['sign_flip']}")
    A(f"- matched trips: live **{tot['matched_live_pnl']:+.2f} $** vs the same vector trips **{tot['matched_vec_pnl_at_live_size']:+.2f} $** at live size"
      f" / **{tot['matched_vec_pnl_at_vec_size']:+.2f} $** at vector size")
    A(f"- live-only trips (no vector decision) **{tot['live_only_pnl']:+.2f} $** over {tot['live_only']} trips"
      f" · vector trips live never took {tot['vec_only']} worth **{tot['vec_only_pnl_at_vec_size']:+.2f} $** at vector size"
      f" (of those, {tot['vec_only_no_twin_decision']} had no [VEC_EXACT] decision in the live log at all)\n")
    A("## Per account\n")
    A("| acct | rows | live trips | vec trips | matched | entry on bar | exit on bar | exit late | live WR | vec WR | live net $ | matched live $ | matched vec $ (vec size) | live-only $ | vec-only $ |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for acct in sorted(per_acct):
        c = per_acct[acct]
        A(f"| {acct} | {c['rows']} | {c['n_live_trips']} | {c['n_vec_trips']} | {c['matched']} | {c['same_entry_bar']} | {c['same_exit_bar'] + c['exit_intrabar']} | {c['exit_late']} |"
          f" {100.0 * c['live_gain'] / max(1, c['live_gain'] + c['live_loss']):.0f}% | {100.0 * c['vec_gain'] / max(1, c['vec_gain'] + c['vec_loss']):.0f}% |"
          f" {c['live_net_pnl_usd']:+.2f} | {c['matched_live_pnl']:+.2f} | {c['matched_vec_pnl_at_vec_size']:+.2f} | {c['live_only_pnl']:+.2f} | {c['vec_only_pnl_at_vec_size']:+.2f} |")
    A("")
    A("## Verdicts\n")
    vc = collections.Counter(r["verdict"] for r in rows)
    pnl_by_v = collections.defaultdict(float)
    for r in rows:
        pnl_by_v[r["verdict"]] += r["live_net_pnl_usd"] or 0.0
    A("| verdict | sym_side x acct | Binance net $ |")
    A("|---|---|---|")
    for v, n in vc.most_common():
        A(f"| {v} | {n} | {pnl_by_v[v]:+.2f} |")
    A("")
    mvc = collections.Counter()
    for ss, rec in detail.items():
        for s in rec["segments"]:
            mvc[s["metric_verdict"]] += 1
    A("## L1 claim vs L2 re-measurement (every applied set in the window)\n")
    A("| metric verdict | applied sets |")
    A("|---|---|")
    for v, n in mvc.most_common():
        A(f"| {v} | {n} |")
    A("")
    A("## Worst 25 by Binance net $\n")
    A("| sym_side | acct | verdict | claim gain% | rerun gain% (span d) | claim trades | rerun trades | live/vec trips | matched | exits on bar | live G/L | vec G/L | live net $ | vec $ @vec size |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["live_net_pnl_usd"] or 0.0)[:25]:
        A(f"| {r['sym_side']} | {r['acct']} | {r['verdict']} | {_f(r['claim_gain_pct'])} | {_f(r['rerun_gain_pct'])} ({_f(r['rerun_span_days'],1)}) |"
          f" {r['claim_trades']} | {r['rerun_trades']} | {r['n_live_trips']}/{r['n_vec_trips']} | {r['matched']} |"
          f" {r['same_exit_bar'] + r['exit_intrabar']} | {r['live_gain']}/{r['live_loss']} | {r['vec_gain']}/{r['vec_loss']} | {r['live_net_pnl_usd']:+.2f} | {r['matched_vec_pnl_at_vec_size']:+.2f} |")
    A("")
    A("## Exit-bar disagreement on matched trips (top 20 by live loss)\n")
    bad = [t for t in trips if t["kind"] == "MATCHED" and t["exit_match"] != "SAME_BAR"]
    A("| sym_side | acct | day | live entry | vec bar | dEntry | live exit | vec exit bar | dExit | how | live $ | vec $ | vec exit reason |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for t in sorted(bad, key=lambda t: t["live_net_pnl"])[:20]:
        A(f"| {t['sym_side']} | {t['acct']} | {t['day']} | {t['live_entry']} | {t['vec_bar']} | {t['entry_bar_delta']} | {t['live_exit']} |"
          f" {t['vec_exit_bar']} | {t['exit_bar_delta']} | {t['exit_match']} | {t['live_net_pnl']:+.2f} | {_f(t['vec_pnl'])} | {t['vec_exit_reason']} |")
    A("")
    if late_reasons:
        A("## Which vector exit did live fail to execute on its bar (>1 bar off)\n")
        A("| vector exit reason | trips | live net $ on those trips |")
        A("|---|---|---|")
        for k, n in late_reasons.most_common(15):
            A(f"| {k} | {n} | {(late_usd or {}).get(k, 0.0):+.2f} |")
        A("")
    A("## Per day\n")
    dd = collections.defaultdict(collections.Counter)
    for d in daily:
        c = dd[d["day"]]
        c["trips"] += 1
        c[d["kind"]] += 1
        c["same_exit_bar"] += 1 if d["exit_bar_delta"] == 0 else 0
        c["live_gain" if (d["live_net_pnl"] or 0) > 0 else "live_loss"] += 1
        c["live_net"] += d["live_net_pnl"] or 0.0
    A("| day | trips | matched | live-only | same exit bar | live G/L | live net $ |")
    A("|---|---|---|---|---|---|---|")
    for d in sorted(dd):
        c = dd[d]
        A(f"| {d} | {c['trips']} | {c['MATCHED']} | {c['LIVE_ONLY']} | {c['same_exit_bar']} | {c['live_gain']}/{c['live_loss']} | {c['live_net']:+.2f} |")
    A("")
    A("## Caveats (read before quoting any number)\n")
    for c in meta["caveats"]:
        A(f"- {c}")
    if meta["n_errors"]:
        A(f"\n{meta['n_errors']} errors; first 10:\n")
        for e in meta["errors"][:10]:
            A(f"- `{e}`")
    return "\n".join(L) + "\n"


def _f(x, nd=2):
    return "" if x is None else (f"{float(x):.{nd}f}" if isinstance(x, (int, float)) else str(x))


if __name__ == "__main__":
    main()
