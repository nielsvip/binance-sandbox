#!/usr/bin/env python3
"""pure_board_report — hourly: the PURE board lane vs what the accounts actually did, and why they differ (USER 2026-10-18).

"get me an hourly report of discrepancies that you analyze so we know why real accounts lose and backtests win."

THREE COLUMNS, ONE BAR AT A TIME
  BOARD/PURE   `tools/pure_board_trader.py` intents: the latest published board set (data/published_selection.json) decided on
               the twin's own frozen bars for exactly the keys in `tradeable_keys.json`. Nothing else may enter this lane.
  LIVE         what the running stack actually did: PROMISE rows from the live twin (`data/promise_ledger`, what live's own
               set decided) and real fills (`data/history/<acct>/<SYM>_<SIDE>.jsonl`, what reached the exchange).
  SET          is the set live runs the SAME set the board published? (`per_sym_active` vs `published_selection`, key by key.)

WHAT IT ANALYSES, in the order the money is lost
  1 SET        keys where live's set is not the published board -> live is trading a different strategy than the chart.
  2 DECISION   board acts with no live promise at the same bar (live's set never decided it) and the reverse.
  3 EXECUTION  live promises with no fill, or a fill more than one bar after the bar it was decided on.
  4 EXTRA      fills with no vector authorisation at all (live-only functions).
  5 RESULT     the pure paper book's realised P&L vs the accounts' realised P&L over the same window.

Read-only. Usage: tools/pure_board_report.py [--hours 1] [--out data/reports/pure_board]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BAR_S = 900.0
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
VEC_TAG = "|VEC_EXACT"
BOOKKEEPING = ("BROKER_RECONCILED", "SYNC_DETECTION")
STOPS = ("FIXED_STOP", "DAYTRADE_STOP", "FROZEN_STOP", "PPL_SL")


def jsonl(pattern, t_from, ts_key="ts"):
    out = []
    for fp in sorted(glob.glob(str(pattern))):
        try:
            for line in open(fp, errors="ignore"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = r.get(ts_key)
                if isinstance(t, str):
                    try:
                        t = dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                if t is None or float(t) < t_from:
                    continue
                r["_ts"] = float(t)
                out.append(r)
        except Exception:
            continue
    return out


def live_fills(t_from):
    out = []
    for acct in ACCOUNTS:
        for fp in glob.glob(str(ROOT / "data" / "history" / acct / "*.jsonl")):
            ss = Path(fp).stem
            try:
                for line in open(fp, errors="ignore"):
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    try:
                        t = dt.datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                    if t < t_from:
                        continue
                    reason = str(r.get("reason") or "")
                    if any(b in reason.upper() for b in BOOKKEEPING):
                        continue
                    out.append({"ts": t, "acct": acct, "sym_side": ss, "type": str(r.get("type") or ""), "reason": reason,
                                "qty": r.get("qty"), "price": r.get("price"), "value": r.get("value"),
                                "vec": VEC_TAG in reason, "stop": any(s in reason.upper() for s in STOPS)})
            except Exception:
                continue
    return out


def set_drift():
    """Live's per-sym set vs the published board, key by key, for the traded universe only."""
    from live_twins import vec_exact as VX

    tk = [k for k in json.load(open(ROOT / "tradeable_keys.json")) if ":" in k]
    pub = (json.load(open(ROOT / "data" / "published_selection.json")).get("sym_sides") or {})
    con = sqlite3.connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True)
    live = {ss: json.loads(oj or "{}") for ss, oj in con.execute("select sym_side,overrides_json from per_sym_active where cat_side like 'CRYPTO%'")}
    con.close()
    rows = []
    for k in sorted(tk):
        acct, ss = k.split(":", 1)
        pb, lv = pub.get(ss), live.get(ss)
        if not pb:
            rows.append((acct, ss, "NO_PUBLISHED_BOARD", []))
            continue
        if not lv:
            rows.append((acct, ss, "NO_LIVE_SET", []))
            continue
        diff = [kk for kk, x in (pb.get("overrides") or {}).items()
                if not (kk in VX._LB_SKIP or kk.startswith("ABLATION_")) and VX._lb_norm(lv.get(kk, "<MISSING>")) != VX._lb_norm(x)]
        rows.append((acct, ss, "SAME_SET" if not diff else f"DIFFERS_{len(diff)}", diff[:8]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "pure_board"))
    a = ap.parse_args()
    now = time.time()
    t_from = now - a.hours * 3600.0
    intents = [r for r in jsonl(ROOT / "data" / "pure_board" / "intents_*.jsonl", t_from) if r.get("action")]
    promises = [r for r in jsonl(ROOT / "data" / "promise_ledger" / "*.jsonl", t_from) if r.get("kind") == "PROMISE"]
    delivs = [r for r in jsonl(ROOT / "data" / "promise_ledger" / "*.jsonl", t_from) if r.get("kind") == "DELIVERY"]
    fills = live_fills(t_from)
    drift = set_drift()
    try:
        paper = json.load(open(ROOT / "data" / "pure_board" / "paper_state.json"))
    except Exception:
        paper = {"pos": {}, "closed": []}
    L = []
    A = L.append
    A("# PURE BOARD lane vs the live accounts — hourly discrepancy report\n")
    A(f"Window **{dt.datetime.fromtimestamp(t_from, dt.timezone.utc).strftime('%m-%d %H:%M')}Z → "
      f"{dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime('%m-%d %H:%M')}Z** ({a.hours} h)\n")
    A("PURE = the latest published board set, decided on the twin's frozen bars, for exactly the keys in tradeable_keys. "
      "LIVE = the running stack's own twin promises and its real fills. Every number below is counted, never modelled.\n")
    A("## 1 · Is live even running the board's set?\n")
    byst = collections.Counter(st.split("_")[0] if st.startswith("DIFFERS") else st for _a, _s, st, _d in drift)
    A(f"- keys in the traded universe: **{len(drift)}** · live set == published board: **{byst['SAME_SET']}** · "
      f"differs: **{byst['DIFFERS']}** · no published board: **{byst['NO_PUBLISHED_BOARD']}** · no live set: **{byst['NO_LIVE_SET']}**")
    bad = [(ac, ss, st, d) for ac, ss, st, d in drift if st.startswith("DIFFERS") or st.startswith("NO_")]
    if bad:
        A("\n| acct | sym_side | state | first differing switches |")
        A("|---|---|---|---|")
        for ac, ss, st, d in sorted(bad, key=lambda r: -int(r[2].split("_")[1]) if r[2].startswith("DIFFERS") else 0)[:20]:
            A(f"| {ac} | {ss} | {st} | {', '.join(d) if d else '-'} |")
    A("\n**Those keys cannot match any chart: live is trading a different set than the one that produced it.**\n")
    A("## 2 · Did the two lanes decide the same thing?\n")
    pure_by = collections.defaultdict(list)
    for r in intents:
        pure_by[(r["acct"], r["sym_side"])].append(r)
    live_by = collections.defaultdict(list)
    for r in promises:
        live_by[(r.get("acct"), r.get("sym_side"))].append(r)
    both = set(pure_by) | set(live_by)
    agree = pure_only = live_only = 0
    rows = []
    for key in sorted(both):
        pl, ll = pure_by.get(key, []), live_by.get(key, [])
        pbars = {(int(float(r["bar_ts"])), str(r["action"]).upper()) for r in pl}
        lbars = {(int(float(r.get("bar_ts") or 0)), str(r.get("action")).upper()) for r in ll}
        same = pbars & lbars
        agree += len(same)
        pure_only += len(pbars - lbars)
        live_only += len(lbars - pbars)
        if pbars - lbars or lbars - pbars:
            rows.append((key, sorted(pbars - lbars)[:3], sorted(lbars - pbars)[:3]))
    A(f"- acts both lanes decided at the same bar: **{agree}** · only the PURE board lane: **{pure_only}** · only LIVE: **{live_only}**")
    if rows:
        A("\n| acct:sym_side | board decided, live did not | live decided, board did not |")
        A("|---|---|---|")
        for (ac, ss), po, lo in rows[:20]:
            f = lambda xs: ", ".join(f"{dt.datetime.fromtimestamp(b, dt.timezone.utc).strftime('%H:%M')} {t}" for b, t in xs) or "-"
            A(f"| {ac}:{ss} | {f(po)} | {f(lo)} |")
    A("")
    A("## 3 · Did live execute what its own twin decided?\n")
    ok = sum(1 for d in delivs if any(t in str(d.get("outcome") or "").upper() for t in ("SUCCESS", "FILLED", "FORCE")))
    skipped = sum(1 for d in delivs if "SKIPPED_VX_INFLIGHT" in str(d.get("outcome") or ""))
    lags = [float(d["lag_s"]) / 60.0 for d in delivs if d.get("lag_s") is not None and any(t in str(d.get("outcome") or "").upper() for t in ("SUCCESS", "FILLED"))]
    med = sorted(lags)[len(lags) // 2] if lags else None
    A(f"- live promises: **{len(promises)}** · deliveries recorded: **{len(delivs)}** (SUCCESS-class **{ok}**, "
      f"skipped in-flight **{skipped}**) · median delivery lag after the bar closed: "
      f"**{('%.1f min' % med) if med is not None else 'n/a'}**")
    late = [d for d in delivs if (d.get("lag_s") or 0) > BAR_S]
    A(f"- delivered more than a bar late: **{len(late)}**" + ("" if not late else " → " + ", ".join(f"{d.get('key')} {float(d['lag_s']) / 60:.0f}min" for d in late[:6])))
    A("")
    A("## 4 · What did live trade that no vector asked for?\n")
    extra = [f for f in fills if not f["vec"] and not f["stop"]]
    stopf = [f for f in fills if not f["vec"] and f["stop"]]
    fam = collections.Counter(f["reason"].split("_g")[0][:40] for f in extra)
    A(f"- fills in the window: **{len(fills)}** · vector-tagged **{sum(1 for f in fills if f['vec'])}** · "
      f"protective-stop fills **{len(stopf)}** · **live-only {len(extra)}**")
    if fam:
        A("\n| live-only function | fills | notional |")
        A("|---|---|---|")
        notl = collections.Counter()
        for f in extra:
            notl[f["reason"].split("_g")[0][:40]] += float(f.get("value") or 0)
        for k, v in fam.most_common(10):
            A(f"| {k} | {v} | ${notl[k]:.2f} |")
    A("")
    A("## 5 · Result so far\n")
    closed = paper.get("closed") or []
    cl_win = sum(1 for c in closed if (c.get("pnl_pct") or 0) > 0)
    A(f"- PURE paper book: **{len(paper.get('pos') or {})} open**, **{len(closed)} closed**, "
      f"win rate **{(100.0 * cl_win / len(closed)) if closed else 0:.0f}%**, "
      f"sum of per-trip returns **{sum(float(c.get('pnl_pct') or 0) for c in closed):+.2f}%**")
    A("- the accounts' realised P&L over the same window comes from the exchange, not from here: "
      "`tools/binance_fills_fetch.py --days 1` then `tools/v15_daily_retrace.py` (the pure lane is paper until you flip it live).")
    A("")
    A("## Reading this report\n")
    A("- Section 1 is the only one that can make a chart unreachable: a different set is a different strategy.")
    A("- Section 2 separates *the board decided something live's set did not* (set/bar/data difference) from the reverse.")
    A("- Section 3 is pure plumbing: same decision, late or never executed.")
    A("- Section 4 is live acting alone; `tools/discrepancy_report.py` prices those decisions against holding instead.")
    A("- The PURE lane holds nothing at start: its book only fills as the board's own entries fire, so sections 2 and 5 "
      "are only fully comparable once it is warm. Until then, part of 'only LIVE decided it' is simply live holding a "
      "position the pure lane has not opened yet — an exit cannot be decided on a position you do not hold.")
    A("- Live's twin pins the set of a HELD position until it is flat (VEC_EXACT_PIN_SET_PER_POSITION), so even a key whose "
      "set matches the board can still be deciding on the set it was opened under. That is by design, and it is a second "
      "reason the two lanes differ on open positions.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = "\n".join(L) + "\n"
    (out / f"pure_board_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H')}Z.md").write_text(md)
    (out / "pure_board_latest.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
