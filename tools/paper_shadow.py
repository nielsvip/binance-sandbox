#!/usr/bin/env python3
"""paper_shadow — ONE complete per-decision ledger: 15m vec vs faster-TF timing vs switched-off nonvec functions.

USER 2026-10-20: "make sure the forward testing 1m/3m (5m stocks) are always running in parallel to the trading and
keeping records of what performs better (per decision) vs the 15m vector copy trading we are doing right now. Same for
all non vectorizable functions switched off in live. We need a complete ledger of every different decision in paper
trading and its consequences so we can start switching the good functions back on as soon as we have confirmed total
parity on the vector trading."

What already exists (this tool joins, never duplicates):
  * promise_ledger  — every vec twin act at its closed 15m bar (the 15m vector copy-trading decisions);
  * data/history fills — what live actually filled;
  * paper_nonvec events + tools/paper_nonvec_eval.py — switched-off nonvec functions, scored vs the vector;
  * tools/forward_parity/ltf_flip.py — realized-PnL attribution per leg (LTF_135/NO_TWIN/VEC15), 6-hourly cron.

What this tool adds (the gap): for EVERY vec PROMISE, the faster-TF TIMING shadow — the earliest 1m/3m (crypto) or
5m (stocks) close inside the same 15m bar and the edge it would have given over acting at the 15m bar close — plus
the nonvec refusals at the same sym_side, all in one ledger with scored consequences per decision.

HONEST LABELS (NO-LIES): the shadow is TIMING ONLY, not a separate strategy — there is no 3m/5m vec engine ("3m/5m
needs a 3m vec", live_twins/vec_exact.py). Reference price = the 15m bar close of the decision bar (ref=15m_bar_close),
never implied fills. Same-bar timing costs nothing extra (cost 0); nonvec entries keep eval's round-trip costs.

Read-only. No live impact. Promotion is a decision, never automatic.
Every run also refreshes data/paper_shadow/shadow.db (SQLite, window-idempotent): the decisions table holds every
trade in each scenario with its edge vs the vec reference, and v_leg_sym / v_leg views give per_sym avg delta and
pos counts — the inputs for switching a function on/off blanket or per_sym per function.
Usage: python3 tools/paper_shadow.py [--days 14] [--horizon-bars 16] [--min-days 7] [--min-syms 5]
       python3 tools/paper_shadow.py --report [LEG]
Suggested cron (6-hourly, next to PAPER_NONVEC_EVAL):
  35 1,7,13,19 * * * cd /Users/niels/Documents/binance && /opt/anaconda3/envs/binance_env/bin/python tools/paper_shadow.py >> /tmp/paper_shadow.log 2>&1 # PAPER_SHADOW
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from paper_nonvec_eval import COST, close_at, real_closes  # noqa: E402

OUT = ROOT / "data" / "paper_shadow"
BAR = 900
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
SHADOW_TF = {"crypto": ("1m", "3m"), "stocks": ("5m",)}
_TFCL = {}


def _ep(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def split_sym_side(sym_side):
    s = str(sym_side or "")
    side = "LONG" if s.endswith("_LONG") else ("SHORT" if s.endswith("_SHORT") else "")
    sym = s[: -len("_LONG")] if side == "LONG" else (s[: -len("_SHORT")] if side else s)
    venue = "crypto" if sym.endswith(CRYPTO_SUFFIX) else "stocks"
    return venue, sym, side


def tf_closes(venue, symbol, tf):
    """(ts, closes) for a faster TF; stocks read klines_cache/tradier."""
    k = (venue, symbol, tf)
    if k not in _TFCL:
        sub = "tradier" if venue == "stocks" else ""
        p = ROOT / "klines_cache" / sub / f"{symbol}_{tf}.json"
        ts, cl = [], []
        try:
            rows = json.loads(p.read_text())
            rows = rows if isinstance(rows, list) else (rows.get("data") or rows.get("klines") or [])
            for r in rows:
                try:
                    ts.append(_ep(r["timestamp"]))
                    cl.append(float(r["close"]))
                except Exception:
                    continue
        except Exception:
            pass
        _TFCL[k] = (ts, cl)
    return _TFCL[k]


GLITCH_TOL = 0.20


def is_glitch(p_vec, p_sh, tol=GLITCH_TOL):
    """A faster-TF close disagreeing with its own 15m bar close by more than tol is a corrupt bar, not a market
    (measured 2026-10-20: ZCSH 5m 92.3 vs 15m 30.6 = 3x decimal-shift glitch, the same family ltf_flip documents
    for mkt_px). Scored, it would poison the mean; skipped rows are counted, never silently zeroed."""
    try:
        return abs(float(p_sh) / float(p_vec) - 1.0) > tol
    except (TypeError, ValueError, ZeroDivisionError):
        return True


def earliest_in_span(venue, symbol, tf, start, end):
    """First faster-TF (ts, close) with start <= ts < end; None when the file has no bar there."""
    ts, cl = tf_closes(venue, symbol, tf)
    if not ts:
        return None
    i = bisect.bisect_left(ts, start)
    if i >= len(ts) or ts[i] >= end:
        return None
    return ts[i], cl[i]


def entry_edge_bps(side, p_vec, p_sh, c_h):
    """Shadow-vs-vec forward edge over the same horizon exit, in bps. Positive = shadow better."""
    s = 1.0 if side == "LONG" else -1.0
    if not p_vec or not p_sh or not c_h:
        return None
    return ((s * (c_h - p_sh) / p_sh) - (s * (c_h - p_vec) / p_vec)) * 1e4


def exit_edge_bps(side, p_vec, p_sh):
    """Shadow-vs-vec exit edge in bps. Positive = shadow exited better."""
    s = 1.0 if side == "LONG" else -1.0
    if not p_vec or not p_sh:
        return None
    return s * (p_sh - p_vec) / p_vec * 1e4


def iter_promises(since):
    for p in sorted(glob.glob(str(ROOT / "data" / "promise_ledger" / "*.jsonl"))):
        try:
            fh = open(p, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("kind") != "PROMISE" or r.get("source") not in (None, "VEC_EXACT"):
                    continue
                if float(r.get("ts", 0)) < since:
                    continue
                if str(r.get("action", "")).upper() not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
                    continue
                yield r


def iter_nonvec(since):
    for p in sorted(glob.glob(str(ROOT / "data" / "paper_nonvec" / "events_*.jsonl"))):
        with open(p, errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                try:
                    t = _ep(r.get("ts", ""))
                except Exception:
                    continue
                if t < since:
                    continue
                yield r, t


def build_ledger(days, horizon):
    now = datetime.now(timezone.utc).timestamp()
    since = now - days * 86400
    rows, stats = [], defaultdict(int)
    for r in iter_promises(since):
        sym_side = r.get("sym_side", "")
        venue, sym, side = split_sym_side(sym_side)
        if not side:
            stats["no_side"] += 1
            continue
        bar = float(r.get("bar_ts", 0))
        p_vec, _ = close_at(venue, sym, bar)
        if p_vec is None:
            stats["no_vec_ref"] += 1
            continue
        action = str(r.get("action", "")).upper()
        is_exit = action in ("REDUCE", "CLOSE")
        for tf in SHADOW_TF[venue]:
            hit = earliest_in_span(venue, sym, tf, bar, bar + BAR)
            if hit is None:
                stats[f"nodata_{tf}"] += 1
                continue
            sh_ts, p_sh = hit
            if is_glitch(p_vec, p_sh):
                stats[f"glitch_skip_{tf}"] += 1
                continue
            if is_exit:
                edge = exit_edge_bps(side, p_vec, p_sh)
                rows.append({"ts": r["ts"], "kind": "shadow_exit", "leg": f"shadow_{tf}", "sym_side": sym_side,
                             "action": action, "reason": str(r.get("reason", ""))[:120], "ref": "15m_bar_close",
                             "p_vec": p_vec, "p_shadow": p_sh, "lead_s": round(bar + BAR - sh_ts, 1),
                             "edge_bps": edge, "pending": edge is None})
            else:
                c_h, _ = close_at(venue, sym, bar, offset=horizon)
                edge = entry_edge_bps(side, p_vec, p_sh, c_h)
                rows.append({"ts": r["ts"], "kind": "shadow_entry", "leg": f"shadow_{tf}", "sym_side": sym_side,
                             "action": action, "reason": str(r.get("reason", ""))[:120], "ref": "15m_bar_close",
                             "p_vec": p_vec, "p_shadow": p_sh, "lead_s": round(bar + BAR - sh_ts, 1),
                             "edge_bps": edge, "pending": edge is None or c_h is None})
            stats[f"rows_{tf}"] += 1
    for r, t in iter_nonvec(since):
        venue = r.get("venue", "crypto")
        key = str(r.get("position_key", ""))
        sym_side = key.split(":", 1)[1] if ":" in key else key
        _, sym, side = split_sym_side(sym_side)
        if not side:
            continue
        kind = r.get("kind", "entry")
        leg = f"nonvec:{r.get('fn') or (str(r.get('action', '')) + ':' + str(r.get('gate', '')))}"
        px = float(r.get("price") or 0) or None
        if kind == "exit":
            p_paper, _ = close_at(venue, sym, t)
            p_paper = px or p_paper
            acct = r.get("account", "")
            nxt = [c for c in real_closes(acct, sym_side) if c[0] > t]
            edge = exit_edge_bps(side, nxt[0][1], p_paper) if nxt and p_paper else None
            rows.append({"ts": t, "kind": "nonvec_exit", "leg": leg, "sym_side": sym_side,
                         "action": r.get("action", ""), "reason": str(r.get("reason", ""))[:120],
                         "ref": "lifecycle_close", "p_vec": (nxt[0][1] if nxt else None), "p_shadow": p_paper,
                         "edge_bps": edge, "pending": edge is None})
        else:
            p_paper, _ = close_at(venue, sym, t)
            p_paper = px or p_paper
            c_h, _ = close_at(venue, sym, t, offset=horizon)
            s = 1.0 if side == "LONG" else -1.0
            edge = (s * (c_h - p_paper) / p_paper * 1e4 - COST.get(venue, 0.10) * 100) \
                if (p_paper and c_h) else None
            rows.append({"ts": t, "kind": "nonvec_entry", "leg": leg, "sym_side": sym_side,
                         "action": r.get("action", ""), "reason": str(r.get("reason", ""))[:120],
                         "ref": "fwd_net_of_cost", "p_vec": None, "p_shadow": p_paper,
                         "edge_bps": edge, "pending": edge is None})
        stats["rows_nonvec"] += 1
    return rows, stats


def verdict(rows, min_days, min_syms, days):
    by_leg = defaultdict(list)
    for r in rows:
        if not r.get("pending"):
            by_leg[r["leg"]].append(r)
    out = {}
    for leg, rs in sorted(by_leg.items()):
        per_sym = defaultdict(list)
        for r in rs:
            per_sym[r["sym_side"]].append(r["edge_bps"])
        sym_mean = {s: sum(v) / len(v) for s, v in per_sym.items()}
        pos = sum(1 for v in sym_mean.values() if v > 0)
        pooled = sum(r["edge_bps"] for r in rs) / len(rs)
        span_days = (max(r["ts"] for r in rs) - min(r["ts"] for r in rs)) / 86400 if len(rs) > 1 else 0
        if span_days >= min_days and len(sym_mean) >= min_syms and pos > len(sym_mean) / 2 and pooled > 0:
            v = "FASTER_CANDIDATE" if leg.startswith("shadow_") else "PROMOTE_CANDIDATE"
        elif span_days >= min_days and len(sym_mean) >= min_syms and pos <= len(sym_mean) / 2:
            v = "REJECT"
        else:
            v = "WATCH"
        out[leg] = {"n": len(rs), "n_syms": len(sym_mean), "span_days": round(span_days, 1),
                    "mean_edge_bps": round(pooled, 2), "pos_syms": pos, "verdict": v,
                    "window_days": days}
    return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions(
  ts REAL, kind TEXT, leg TEXT, sym_side TEXT, venue TEXT, action TEXT,
  reason TEXT, ref TEXT, p_vec REAL, p_shadow REAL, lead_s REAL,
  edge_bps REAL, pending INTEGER, horizon_bars INTEGER, window_days INTEGER);
CREATE INDEX IF NOT EXISTS idx_dec_leg_sym ON decisions(leg, sym_side);
CREATE INDEX IF NOT EXISTS idx_dec_sym ON decisions(sym_side);
CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts);
CREATE VIEW IF NOT EXISTS v_leg_sym AS
  SELECT leg, sym_side, COUNT(*) n, AVG(edge_bps) avg_edge,
    SUM(CASE WHEN edge_bps > 0 THEN 1 ELSE 0 END) pos_n,
    SUM(edge_bps) tot_edge
  FROM decisions WHERE pending = 0 GROUP BY leg, sym_side;
CREATE VIEW IF NOT EXISTS v_leg AS
  SELECT leg, COUNT(*) n, COUNT(DISTINCT sym_side) syms, AVG(edge_bps) avg_edge,
    SUM(CASE WHEN edge_bps > 0 THEN 1 ELSE 0 END) pos_n, SUM(edge_bps) tot_edge
  FROM decisions WHERE pending = 0 GROUP BY leg;
"""


def venue_of(sym_side):
    return split_sym_side(sym_side)[0]


def write_db(rows, db_path, since, horizon, days):
    """Idempotent window refresh: rows recomputed each run replace everything with ts >= since."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.execute("DELETE FROM decisions WHERE ts >= ?", (since,))
        con.executemany(
            "INSERT INTO decisions(ts,kind,leg,sym_side,venue,action,reason,ref,p_vec,p_shadow,"
            "lead_s,edge_bps,pending,horizon_bars,window_days) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("ts"), r.get("kind"), r.get("leg"), r.get("sym_side"), venue_of(r.get("sym_side")),
              r.get("action"), r.get("reason"), r.get("ref"), r.get("p_vec"), r.get("p_shadow"),
              r.get("lead_s"), r.get("edge_bps"), 1 if r.get("pending") else 0, horizon, days)
             for r in rows])
        con.commit()
    finally:
        con.close()
    return db_path


def leg_sym_stats(db_path, leg=None):
    """Per-sym comparison rows driving blanket/per_sym/per-function switching: avg delta + pos counts."""
    con = sqlite3.connect(db_path)
    try:
        q = "SELECT leg, sym_side, n, ROUND(avg_edge,2), pos_n, ROUND(tot_edge,2) FROM v_leg_sym"
        args: tuple = ()
        if leg:
            q += " WHERE leg = ?"
            args = (leg,)
        q += " ORDER BY leg, avg_edge DESC"
        return con.execute(q, args).fetchall()
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="unified per-decision paper ledger: vec vs faster-TF timing vs nonvec")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--horizon-bars", type=int, default=16)
    ap.add_argument("--min-days", type=int, default=7)
    ap.add_argument("--min-syms", type=int, default=5)
    ap.add_argument("--report", nargs="?", const="", default=None,
                    help="print per_sym avg-delta table from SQLite (optional LEG filter) and exit")
    a = ap.parse_args(argv)
    if a.report is not None:
        db = OUT / "shadow.db"
        if not db.exists():
            print("no db yet; run without --report first")
            return 1
        print(f"{'leg':28} {'sym_side':22} {'n':>5} {'avg_bps':>9} {'pos':>5} {'tot_bps':>10}")
        for leg, sym, n, avg, pos, tot in leg_sym_stats(db, a.report or None):
            print(f"{leg:28} {sym:22} {n:>5} {avg:>9} {pos:>5} {tot:>10}")
        return 0
    rows, stats = build_ledger(a.days, a.horizon_bars)
    vd = verdict(rows, a.min_days, a.min_syms, a.days)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (OUT / f"ledger_{stamp}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
    since = datetime.now(timezone.utc).timestamp() - a.days * 86400
    write_db(rows, OUT / "shadow.db", since, a.horizon_bars, a.days)
    (OUT / "verdict.json").write_text(json.dumps(vd, indent=1, sort_keys=True))
    md = ["# paper_shadow verdict", "", f"decisions={len(rows)} window={a.days}d horizon={a.horizon_bars} bars", "",
          "| leg | n | syms | span_d | mean_edge_bps | pos_syms | verdict |",
          "|---|---|---|---|---|---|---|"]
    for leg, v in sorted(vd.items()):
        md.append(f"| {leg} | {v['n']} | {v['n_syms']} | {v['span_days']} | {v['mean_edge_bps']} | {v['pos_syms']} | {v['verdict']} |")
    (OUT / "verdict.md").write_text("\n".join(md) + "\n")
    pend = sum(1 for r in rows if r.get("pending"))
    print(f"decisions={len(rows)} pending={pend} legs={len(vd)} stats={dict(stats)}")
    for leg, v in sorted(vd.items()):
        print(f"  {leg}: n={v['n']} syms={v['n_syms']} mean={v['mean_edge_bps']}bps -> {v['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
