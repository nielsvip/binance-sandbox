#!/usr/bin/env python3
"""switch_vs_actual — every trade the SWITCHES dictated vs every trade that actually happened (USER 2026-10-18).

USER: "Give me a rundown of ALL trades that the switches dictated over the last 12 hours and what trades actually happened"
+ "make a database of all the stupidities that are pulling me down".

DICTATED  the sym_side's COMPLETE live set (per_sym_active.full_config — "override" is not a concept any more, the set is
          the authority) run through the engine the sheets use (tools.opt.evaluate_v12.evaluate_prepared, include_ledger)
          on the twin's own frozen 15m bars. Every OPEN / AUGMENT / REDUCE / CLOSE the engine books inside the window,
          with the bar it was decided on. No reimplementation: the engine's ledger IS the dictate.
ACTUAL    every fill the account recorded in the same window (data/history/<acct>/<SYM>_<SIDE>.jsonl: type, qty, price,
          reason, timestamp). Broker-bookkeeping mirrors (BROKER_RECONCILED / SYNC_DETECTION) are not decisions and are
          excluded; everything else counts, including stop fills.

VERDICT per dictated act   FILLED_ON_BAR (a matching fill inside the bar after the decision) · FILLED_LATE_<n>bars ·
                           NOT_FILLED (the switches said trade and nothing happened).
VERDICT per actual fill    MATCHED · EXTRA (a trade no switch dictated in this window — the ones to kill first).

Writes data/reports/switch_vs_actual_<stamp>.md (+ .json) and every mismatch into the discrepancy SQL
(discrepancy_ledger: SWITCH_SAID_NO_TRADE / TRADE_WITHOUT_SWITCH), so the "database of stupidities" accumulates.
Read-only on everything live. Usage: tools/switch_vs_actual.py [--hours 12] [--accounts ang,fin,flz,inf,men] [--workers 10]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
BAR_S = 900.0
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
BOOKKEEPING = ("BROKER_RECONCILED", "SYNC_DETECTION")
FROZEN = Path(os.environ.get("VEC_EXACT_FROZEN_DIR", "/tmp/vec_exact_frozen"))


def iso(ts):
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%m-%d %H:%M") if ts else ""


def live_sets():
    con = sqlite3.connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True)
    out = {}
    for ss, upd, fc in con.execute("select sym_side,updated_at,full_config_json from per_sym_active where cat_side like 'CRYPTO%'"):
        try:
            full = json.loads(fc or "{}")
        except Exception:
            continue
        if full:
            out[ss] = {"updated_at": upd, "set": full}
    con.close()
    return out


def _dictated(job):
    """Engine acts for one sym_side inside the window, on the twin's bars, with the live complete set."""
    ss, cfgset, t_from = job
    import pickle

    from tools.opt import evaluate_v12 as E

    path = FROZEN / f"{ss.rsplit('_', 1)[0]}.pkl"
    if not path.exists():
        return ss, {"status": "NO_BAR_STORE", "acts": []}
    try:
        with open(path, "rb") as fh:
            raw = dict(pickle.load(fh))
        o_s, o_d = E._store, E._frozen_npz_start_date
        E._store = lambda sym, mode, start: raw
        E._frozen_npz_start_date = lambda *a, **k: "1970-01-01"
        try:
            prep = E.prepare(ss, 30)
        finally:
            E._store, E._frozen_npz_start_date = o_s, o_d
        if prep is None:
            return ss, {"status": "PREPARE_FAILED", "acts": []}
        r = E.evaluate_prepared(prep, dict(cfgset), include_ledger=True)
        acts = []
        for e in r.get("execution_ledger") or []:
            ts = float(e.get("ts") or 0.0)
            if ts < t_from:
                continue
            reason = str(e.get("reason") or e.get("exit_reason") or e.get("entry_reason") or "")
            if reason.startswith("FINAL_MTM"):
                continue
            typ = str(e.get("type") or "").upper()
            if typ not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
                continue
            acts.append({"bar_ts": ts, "type": typ, "reason": reason[:80], "qty": abs(float(e.get("qty") or 0.0)),
                         "price": float(e.get("price") or 0.0), "pnl": e.get("pnl_dollars")})
        return ss, {"status": "OK", "acts": acts, "engine_gain": r.get("gain_pct"), "engine_trades": r.get("trades")}
    except Exception as exc:
        return ss, {"status": f"ERROR:{type(exc).__name__}", "acts": [], "err": str(exc)[:160]}


def actual_fills(accounts, t_from):
    out = collections.defaultdict(list)
    for acct in accounts:
        for fp in glob.glob(str(ROOT / "data" / "history" / acct / "*.jsonl")):
            ss = Path(fp).stem
            try:
                for line in open(fp, errors="ignore"):
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    try:
                        ts = dt.datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                    if ts < t_from:
                        continue
                    reason = str(r.get("reason") or "")
                    if any(b in reason.upper() for b in BOOKKEEPING):
                        continue
                    out[(acct, ss)].append({"ts": ts, "type": str(r.get("type") or "").upper(), "qty": r.get("qty"),
                                            "price": r.get("price"), "value": r.get("value"), "reason": reason[:90],
                                            "vec": "|VEC_EXACT" in reason})
            except Exception:
                continue
    for v in out.values():
        v.sort(key=lambda r: r["ts"])
    return out


def match(acts, fills, tol_bars):
    """A dictated act on bar b should fill inside bar b+1; later is late, never is NOT_FILLED."""
    used = set()
    pairs = []
    for i, a in enumerate(acts):
        want_exit = a["type"] in ("CLOSE", "REDUCE")
        best, bd = None, None
        for j, f in enumerate(fills):
            if j in used:
                continue
            is_exit = f["type"] in ("CLOSE", "REDUCE")
            if is_exit != want_exit:
                continue
            d = int((f["ts"] - (a["bar_ts"] + BAR_S)) // BAR_S)
            if -1 <= d <= tol_bars and (bd is None or abs(d) < abs(bd)):
                best, bd = j, d
        if best is None:
            pairs.append((a, None, None))
        else:
            used.add(best)
            pairs.append((a, fills[best], bd))
    extra = [f for j, f in enumerate(fills) if j not in used]
    return pairs, extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--tol-bars", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports"))
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    accounts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    now = time.time()
    t_from = now - a.hours * 3600.0
    sets = live_sets()
    fills = actual_fills(accounts, t_from)
    traded = {ss for _ac, ss in fills}
    universe = sorted(set(sets) & (traded | set(sets)))
    jobs = [(ss, sets[ss]["set"], t_from) for ss in universe]
    print(f"[switch_vs_actual] window {iso(t_from)} -> {iso(now)} ({a.hours}h) · {len(jobs)} sym_sides with a live set · "
          f"{sum(len(v) for v in fills.values())} actual fills", flush=True)
    dictated = {}
    with ProcessPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for ss, res in ex.map(_dictated, jobs, chunksize=1):
            dictated[ss] = res
    tot = collections.Counter()
    per_acct = collections.defaultdict(collections.Counter)
    rows, worst = [], []
    for ss in universe:
        d = dictated.get(ss) or {}
        acts = d.get("acts") or []
        tot[f"status_{d.get('status','?')}"] += 1
        accts = sorted({ac for ac, s in fills if s == ss}) or []
        if not accts and acts:
            accts = []  # dictated but this sym_side traded on no account in the window
            tot["dictated_but_no_account_traded_it"] += len(acts)
        for acct in accts:
            f = fills.get((acct, ss)) or []
            pairs, extra = match(acts, f, a.tol_bars)
            for act, fill, d_bars in pairs:
                if fill is None:
                    v = "NOT_FILLED"
                elif d_bars <= 0:
                    v = "FILLED_ON_BAR"
                else:
                    v = f"FILLED_LATE_{d_bars}bars"
                tot[v.split("_")[0] if v.startswith("FILLED_LATE") else v] += 1
                per_acct[acct][v.split("_")[0] if v.startswith("FILLED_LATE") else v] += 1
                rows.append({"acct": acct, "sym_side": ss, "dictated_bar": iso(act["bar_ts"]), "dictated": act["type"],
                             "dictated_reason": act["reason"], "actual_at": iso(fill["ts"]) if fill else "",
                             "actual": fill["type"] if fill else "", "actual_reason": fill["reason"] if fill else "",
                             "late_bars": d_bars, "verdict": v, "vec_tagged": (fill or {}).get("vec")})
            for f_ in extra:
                tot["EXTRA"] += 1
                per_acct[acct]["EXTRA"] += 1
                rows.append({"acct": acct, "sym_side": ss, "dictated_bar": "", "dictated": "", "dictated_reason": "",
                             "actual_at": iso(f_["ts"]), "actual": f_["type"], "actual_reason": f_["reason"],
                             "late_bars": None, "verdict": "EXTRA", "vec_tagged": f_.get("vec")})
            nf = sum(1 for p in pairs if p[1] is None)
            if nf or extra:
                worst.append((acct, ss, len(acts), len(f), nf, len(extra)))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    L = []
    A = L.append
    A(f"# What the switches dictated vs what actually traded — last {a.hours:.0f} h\n")
    A(f"Window **{iso(t_from)} → {iso(now)}** · DICTATED = each sym_side's complete live set run through the sheets' own "
      f"engine on the twin's bars · ACTUAL = the account's own fills (bookkeeping mirrors excluded)\n")
    A("| | count |")
    A("|---|---|")
    for k in ("FILLED_ON_BAR", "FILLED", "NOT_FILLED", "EXTRA"):
        if tot[k]:
            A(f"| {'FILLED_LATE (1+ bars)' if k == 'FILLED' else k} | {tot[k]} |")
    A(f"| dictated acts on sym_sides no account traded | {tot['dictated_but_no_account_traded_it']} |")
    A("")
    A("## Per account\n")
    A("| acct | on bar | late | not filled | extra (no switch said so) |")
    A("|---|---|---|---|---|")
    for ac in sorted(per_acct):
        c = per_acct[ac]
        A(f"| {ac} | {c['FILLED_ON_BAR']} | {c['FILLED']} | {c['NOT_FILLED']} | {c['EXTRA']} |")
    A("")
    A("## Every mismatch, newest first\n")
    A("| acct | sym_side | switch said | at bar | actually | at | late bars | verdict | actual reason |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in sorted([r for r in rows if r["verdict"] != "FILLED_ON_BAR"], key=lambda r: (r["dictated_bar"] or r["actual_at"]), reverse=True)[:120]:
        A(f"| {r['acct']} | {r['sym_side']} | {r['dictated'] or '-'} {r['dictated_reason'][:38]} | {r['dictated_bar'] or '-'} |"
          f" {r['actual'] or '-'} | {r['actual_at'] or '-'} | {r['late_bars'] if r['late_bars'] is not None else '-'} |"
          f" {r['verdict']} | {r['actual_reason'][:46]} |")
    A("")
    A("## Matched exactly on the bar\n")
    A("| acct | sym_side | switch said | at bar | actually at | reason |")
    A("|---|---|---|---|---|---|")
    for r in [r for r in rows if r["verdict"] == "FILLED_ON_BAR"][:60]:
        A(f"| {r['acct']} | {r['sym_side']} | {r['dictated']} | {r['dictated_bar']} | {r['actual_at']} | {r['dictated_reason'][:46]} |")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = "\n".join(L) + "\n"
    (out / f"switch_vs_actual_{stamp}.md").write_text(md)
    (out / "switch_vs_actual_latest.md").write_text(md)
    (out / f"switch_vs_actual_{stamp}.json").write_text(json.dumps({"window_from": iso(t_from), "hours": a.hours,
                                                                    "totals": dict(tot), "rows": rows}, default=str, indent=1))
    if not a.no_db:
        try:
            import discrepancy_ledger as DL

            con = DL.connect()
            n = 0
            for r in rows:
                if r["verdict"] == "FILLED_ON_BAR":
                    continue
                cls = "PROMISE_NOT_DELIVERED" if r["verdict"] == "NOT_FILLED" else ("LIVE_ONLY_FILL" if r["verdict"] == "EXTRA" else "PROMISE_LATE")
                n += DL.add(con, cls, now, r["acct"], r["sym_side"], action=r["dictated"] or r["actual"],
                            who_wanted=("VECTOR" if r["verdict"] != "EXTRA" else "LIVE"),
                            vec_reason=r["dictated_reason"], live_reason=r["actual_reason"],
                            bars_off=r["late_bars"], source="switch_vs_actual",
                            detail={"verdict": r["verdict"], "dictated_bar": r["dictated_bar"], "actual_at": r["actual_at"]},
                            fp_extra=f"{r['verdict']}|{r['dictated_bar']}|{r['actual_at']}")
            con.commit()
            con.close()
            print(f"[switch_vs_actual] {n} new rows in the discrepancy database")
        except Exception as exc:
            print(f"[switch_vs_actual] db write skipped: {exc}")
    print("\n".join(L[:26]))
    print(f"\nwrote {out}/switch_vs_actual_latest.md")


if __name__ == "__main__":
    main()
