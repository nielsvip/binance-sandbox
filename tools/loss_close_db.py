#!/usr/bin/env python3
"""loss_close_db — which ENTRY functions end up having to close at a loss (USER 2026-10-17: "a clear database of what functions
ended having to close in a loss - these are to be excluded from newborn protection and should only be accepted with additional
filters in future application (unless backtest deltas say otherwise)").

Sources (read-only):
  board  the latest 30-day published chart per sym_side (data/reports/charts_1Y/*.html): TRADES rows carry entry_reason,
         exit_reason, pnl_pct, bar_entry/bar_exit; MFE is measured on the chart's own CLOSE series between entry and exit
         (close basis — what the board could have banked), so "green then red" = MFE > 0 and pnl < 0.
  live   confirmed full closes (data/per_trade_returns/*.jsonl) joined to the opening function from the decision logs
         (data/decisions/decisions_{acct}_*.jsonl, last OPEN/REENTRY for the same position key at or before the open).
Outputs:
  data/loss_close_db.sqlite              trades(source, venue, account, sym_side, entry_fn, exit_fn, pnl_pct, mfe_pct, green_then_red, ts, ref)
  data/loss_close_db_summary.json        per (source, venue, entry_fn): n, loss_n, loss_rate, pnl_sum, avg_loss, green_then_red_n, top loss exits
  data/loss_prone_entries.json           entry functions flagged loss-prone per venue (read by ez_manage newborn protection and by the
                                         vector's LOSS_PRONE_ENTRY_FILTER switch). Flag = board n >= --min-board AND pnl_sum < 0 AND
                                         loss_rate >= --min-loss-rate, OR live n >= --min-live AND pnl_sum < 0.
Usage: tools/loss_close_db.py [--days 30] [--min-board 30] [--min-live 10] [--min-loss-rate 0.55]
"""
import argparse
import collections
import glob
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD")
_STRIP = re.compile(r"_(px|lvl|g|exit|cur|k3m|k15m|qty|mult|arm|leg|tgt|act|dev|pct|gain|age|m)[-+]?[\d.].*$", re.I)


def fn_family(reason) -> str:
    """ONE normaliser shared with live and the vector (loss_prone.fn_family)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from loss_prone import fn_family as _f

    return _f(reason)


def venue_of(sym: str) -> str:
    return "crypto" if str(sym).upper().endswith(CRYPTO_SUFFIX) else "stocks"


def _arr(html: str, name: str):
    m = re.search(r"const %s\s*=\s*(\[.*?\]);" % name, html, re.S)
    try:
        return json.loads(m.group(1)) if m else []
    except Exception:
        return []


def board_rows(days: int):
    latest = {}
    for f in glob.glob(str(ROOT / "data" / "reports" / "charts_1Y" / "*.html")):
        b = os.path.basename(f)
        if "_tmp" in b or "365" in b or not re.search(r"(30d|30D)", b):
            continue
        m = re.match(r"^([A-Z0-9.]+_(LONG|SHORT))_", b)
        if not m:
            continue
        mt = os.path.getmtime(f)
        if time.time() - mt > days * 86400:
            continue
        if m.group(1) not in latest or mt > latest[m.group(1)][0]:
            latest[m.group(1)] = (mt, f)
    for ss, (mt, f) in latest.items():
        h = open(f, encoding="utf-8", errors="replace").read()
        close = _arr(h, "CLOSE")
        is_long = ss.endswith("_LONG")
        for t in _arr(h, "TRADES"):
            if t.get("unrealised") or str(t.get("exit_reason") or "").startswith("FINAL_MTM"):
                continue
            try:
                pnl = float(t.get("pnl_pct") or 0.0)
                e, x = int(t.get("bar_entry")), int(t.get("bar_exit"))
                ep = float(t.get("entry_price") or 0.0)
                seg = [float(c) for c in close[max(0, e): x + 1] if c]
                mfe = None
                if seg and ep > 0:
                    mfe = ((max(seg) - ep) / ep * 100.0) if is_long else ((ep - min(seg)) / ep * 100.0)
            except Exception:
                continue
            yield ("board", venue_of(ss.rsplit("_", 1)[0]), "", ss, fn_family(t.get("entry_reason")), fn_family(t.get("exit_reason") or t.get("reason")),
                   pnl, mfe, int(mfe is not None and mfe > 0 and pnl < 0), float(t.get("ts") or mt), os.path.basename(f))


def _decisions(acct: str):
    out = collections.defaultdict(list)  # position_key -> [(epoch, reason)]
    for f in sorted(glob.glob(str(ROOT / "data" / "decisions" / f"decisions_{acct}_*.jsonl"))):
        for ln in open(f, errors="replace"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            a = str(d.get("action") or "").upper()
            if a not in ("OPEN", "REENTRY", "QUICK_OPEN"):
                continue
            try:
                ts = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            out[str(d.get("position_key") or "")].append((ts, str(d.get("reason") or d.get("reason_text") or "")))
    for v in out.values():
        v.sort()
    return out


def live_rows(days: int):
    cut = time.time() - days * 86400
    decs = {}
    for f in sorted(glob.glob(str(ROOT / "data" / "per_trade_returns" / "*.jsonl"))):
        acct = os.path.basename(f).rsplit("_", 1)[0]
        if acct not in decs:
            decs[acct] = _decisions(acct)
        for ln in open(f, errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if not r.get("full_close") or r.get("confirmed") is False:
                continue
            try:
                ts = datetime.fromisoformat(str(r.get("ts")).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts < cut:
                continue
            sym, side = str(r.get("symbol") or "").upper(), str(r.get("side") or "").upper()
            pk = f"{acct}:{sym}_{side}"
            opened = None
            try:
                opened = datetime.fromisoformat(str(r.get("lifecycle", "")).split("|")[-1] + ":00+00:00").timestamp()
            except Exception:
                opened = ts - 60.0 * float(r.get("hold_minutes") or 0.0)
            cand = [rs for (t, rs) in decs[acct].get(pk, []) if t <= (opened or ts) + 120.0]
            entry = cand[-1] if cand else ""
            yield ("live", venue_of(sym), acct, f"{sym}_{side}", fn_family(entry), fn_family(r.get("reason")), float(r.get("pnl_pct") or 0.0),
                   None, 0, ts, str(r.get("lifecycle") or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-board", type=int, default=30)
    ap.add_argument("--min-live", type=int, default=10)
    ap.add_argument("--min-loss-rate", type=float, default=0.55)
    a = ap.parse_args()
    rows = list(board_rows(a.days)) + list(live_rows(a.days))
    db = ROOT / "data" / "loss_close_db.sqlite"
    tmp = db.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE trades(source TEXT, venue TEXT, account TEXT, sym_side TEXT, entry_fn TEXT, exit_fn TEXT, pnl_pct REAL, mfe_pct REAL, green_then_red INTEGER, ts REAL, ref TEXT)")
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("CREATE INDEX ix_fn ON trades(source, venue, entry_fn)")
    con.commit()
    con.close()
    os.replace(tmp, db)
    agg = collections.defaultdict(lambda: {"n": 0, "loss_n": 0, "pnl_sum": 0.0, "loss_sum": 0.0, "green_then_red_n": 0, "loss_exits": collections.Counter()})
    for src, ven, _acct, _ss, ent, ex, pnl, _mfe, gtr, _ts, _ref in rows:
        g = agg[(src, ven, ent)]
        g["n"] += 1
        g["pnl_sum"] += pnl
        g["green_then_red_n"] += gtr
        if pnl < 0:
            g["loss_n"] += 1
            g["loss_sum"] += pnl
            g["loss_exits"][ex] += 1
    summary, flags = [], {"crypto": set(), "stocks": set()}
    for (src, ven, ent), g in agg.items():
        lr = g["loss_n"] / g["n"] if g["n"] else 0.0
        row = {"source": src, "venue": ven, "entry_fn": ent, "n": g["n"], "loss_n": g["loss_n"], "loss_rate": round(lr, 3), "pnl_sum": round(g["pnl_sum"], 3),
               "avg_loss": round(g["loss_sum"] / g["loss_n"], 3) if g["loss_n"] else 0.0, "green_then_red_n": g["green_then_red_n"],
               "top_loss_exits": g["loss_exits"].most_common(5)}
        summary.append(row)
        if ent != "UNKNOWN" and ((src == "board" and g["n"] >= a.min_board and g["pnl_sum"] < 0 and lr >= a.min_loss_rate) or (src == "live" and g["n"] >= a.min_live and g["pnl_sum"] < 0)):
            flags[ven].add(ent)
    summary.sort(key=lambda r: (r["source"], r["venue"], r["pnl_sum"]))
    stamp = datetime.now(timezone.utc).isoformat()
    (ROOT / "data" / "loss_close_db_summary.json").write_text(json.dumps({"generated_at": stamp, "days": a.days, "rows": summary}, indent=1, default=list))
    fl = {"generated_at": stamp, "criteria": vars(a), "crypto": sorted(flags["crypto"]), "stocks": sorted(flags["stocks"])}
    p = ROOT / "data" / "loss_prone_entries.json"
    t2 = p.with_suffix(".tmp")
    t2.write_text(json.dumps(fl, indent=1))
    os.replace(t2, p)
    nb, nl = sum(1 for r in rows if r[0] == "board"), sum(1 for r in rows if r[0] == "live")
    print(f"[loss-close-db] trades board={nb} live={nl} functions={len(summary)} flagged crypto={len(fl['crypto'])} stocks={len(fl['stocks'])}")


if __name__ == "__main__":
    main()
