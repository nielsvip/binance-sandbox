#!/usr/bin/env python3
"""paper_nonvec_eval — score the live-only functions that live refuses (paper_nonvec.py) against what the vector actually did.

USER 2026-10-17: "... switched off then paper traded next to the live 100% vector parity trades and any function that is useful to keep
(pos delta on most sym_sides) gets to override vector in a week or so".

Read-only. Inputs: data/paper_nonvec/events_*.jsonl (refusals), data/history/{acct}/{SYM}_{SIDE}.jsonl (the real, vector-driven fills),
klines_cache[/tradier]/{SYM}_15m.json (exchange 15m closes).
  exit  : paper close at the 15m close of the refusal bar vs the real close of that lifecycle (first CLOSE fill after it):
          delta% = (paper − actual)/actual for LONG, (actual − paper)/actual for SHORT. One event per (key, function, lifecycle).
          Lifecycle still open -> marked against the latest close, counted as pending (not in the verdict).
  entry : paper open at the refusal bar's close, forward return over --horizon-bars 15m bars, side-signed, minus round-trip cost
          (crypto 0.10 %, stocks 0.02 %). One event per (key, function) per horizon window.
Verdict per (venue, kind, function): per-sym_side mean delta; PROMOTE_CANDIDATE when observed >= --min-days days, >= --min-syms
sym_sides scored and > half of them positive with a positive pooled mean; REJECT when the same sample says <= half; else WATCH.
Outputs data/paper_nonvec/verdict.json + verdict.md. Promotion is a decision for the user/pilot, never automatic.
Usage: tools/paper_nonvec_eval.py [--days 14] [--horizon-bars 16] [--min-days 7] [--min-syms 5]
"""
import argparse
import bisect
import collections
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "paper_nonvec"
COST = {"crypto": 0.10, "stocks": 0.02}
_KL = {}


def _ep(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def closes(venue, symbol):
    k = (venue, symbol)
    if k not in _KL:
        p = ROOT / "klines_cache" / ("tradier" if venue == "stocks" else "") / f"{symbol}_15m.json"
        ts, cl = [], []
        try:
            rows = json.loads(p.read_text())
            rows = rows if isinstance(rows, list) else (rows.get("data") or rows.get("klines") or [])
            for r in rows:
                ts.append(_ep(r["timestamp"]))
                cl.append(float(r["close"]))
        except Exception:
            pass
        _KL[k] = (ts, cl)
    return _KL[k]


def close_at(venue, symbol, t, offset=0):
    """Close of the 15m bar containing t (shifted by offset bars); None if outside the store."""
    ts, cl = closes(venue, symbol)
    if not ts:
        return None, None
    i = bisect.bisect_right(ts, t) - 1 + offset
    if i < 0 or i >= len(ts):
        return None, None
    return cl[i], ts[i]


def real_closes(account, sym_side):
    out = []
    p = ROOT / "data" / "history" / account / f"{sym_side}.jsonl"
    try:
        for ln in open(p, errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if "CLOSE" in str(r.get("type", "")).upper() and r.get("price"):
                out.append((_ep(r["ts"]), float(r["price"]), str(r.get("reason", ""))))
    except OSError:
        pass
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--horizon-bars", type=int, default=16)
    ap.add_argument("--min-days", type=float, default=7.0)
    ap.add_argument("--min-syms", type=int, default=5)
    a = ap.parse_args()
    now = datetime.now(timezone.utc).timestamp()
    events = []
    for f in sorted(glob.glob(str(OUT / "events_*.jsonl"))):
        for ln in open(f, errors="replace"):
            try:
                r = json.loads(ln)
                r["_t"] = _ep(r["ts"])
            except Exception:
                continue
            if now - r["_t"] <= a.days * 86400:
                events.append(r)
    events.sort(key=lambda r: r["_t"])
    scored = collections.defaultdict(list)  # (venue, kind, fn) -> [(sym_side, delta, pending)]
    first_seen = {}
    seen_exit, seen_entry = set(), {}
    rc_cache = {}
    for e in events:
        venue, kind, fn = e.get("venue"), e.get("kind"), e.get("fn")
        pk = e.get("position_key", "")
        acct, _, ss = pk.partition(":")
        if not ss or "_" not in ss:
            continue
        symbol, side = ss.rsplit("_", 1)
        is_long = side == "LONG"
        key = (venue, kind, fn)
        first_seen.setdefault(key, e["_t"])
        px, _ = close_at(venue, symbol, e["_t"])
        if not px:
            continue
        if kind == "exit":
            if (acct, ss) not in rc_cache:
                rc_cache[(acct, ss)] = real_closes(acct, ss)
            nxt = next(((t, p) for (t, p, _r) in rc_cache[(acct, ss)] if t >= e["_t"]), None)
            lc = (pk, fn, nxt[0] if nxt else "open")
            if lc in seen_exit:
                continue
            seen_exit.add(lc)
            pending = nxt is None
            actual = nxt[1] if nxt else close_at(venue, symbol, now)[0]
            if not actual:
                continue
            d = ((px - actual) / actual * 100.0) if is_long else ((actual - px) / actual * 100.0)
            scored[key].append((ss, d, pending))
        else:
            w = int(e["_t"] // (a.horizon_bars * 900))
            if seen_entry.get((pk, fn)) == w:
                continue
            seen_entry[(pk, fn)] = w
            fwd, _ = close_at(venue, symbol, e["_t"], a.horizon_bars)
            if not fwd:
                scored[key].append((ss, 0.0, True))
                continue
            d = ((fwd - px) / px * 100.0 if is_long else (px - fwd) / px * 100.0) - COST.get(venue, 0.1)
            scored[key].append((ss, d, False))
    rows = []
    for (venue, kind, fn), lst in scored.items():
        done = [(ss, d) for ss, d, p in lst if not p]
        per = collections.defaultdict(list)
        for ss, d in done:
            per[ss].append(d)
        means = {ss: sum(v) / len(v) for ss, v in per.items()}
        pos = sum(1 for m in means.values() if m > 0)
        pooled = (sum(d for _, d in done) / len(done)) if done else 0.0
        days = (now - first_seen[(venue, kind, fn)]) / 86400.0
        share = (pos / len(means)) if means else 0.0
        if days >= a.min_days and len(means) >= a.min_syms:
            verdict = "PROMOTE_CANDIDATE" if (share > 0.5 and pooled > 0) else "REJECT" if share <= 0.5 else "WATCH"
        else:
            verdict = "WATCH"
        rows.append({"venue": venue, "kind": kind, "fn": fn, "events": len(lst), "scored": len(done), "pending": len(lst) - len(done), "sym_sides": len(means),
                     "pos_sym_sides": pos, "pos_share": round(share, 3), "pooled_delta_pct": round(pooled, 4), "days_observed": round(days, 2), "verdict": verdict,
                     "per_sym_side": {k: round(v, 4) for k, v in sorted(means.items())}})
    rows.sort(key=lambda r: (r["verdict"] != "PROMOTE_CANDIDATE", r["venue"], r["kind"], -r["pooled_delta_pct"]))
    stamp = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "verdict.json.tmp"
    tmp.write_text(json.dumps({"generated_at": stamp, "criteria": vars(a), "rows": rows}, indent=1))
    os.replace(tmp, OUT / "verdict.json")
    md = [f"# Paper non-vec verdict — {stamp}", "", f"exit delta = paper close vs the real (vector) close of the same lifecycle; entry = {a.horizon_bars}-bar forward return net of cost. [DIAGNOSTIC] until a function has >= {a.min_days:g} days and >= {a.min_syms} sym_sides.", "",
          "| venue | kind | function | events | scored | sym_sides | pos | pooled Δ% | days | verdict |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['venue']} | {r['kind']} | {r['fn']} | {r['events']} | {r['scored']} | {r['sym_sides']} | {r['pos_sym_sides']} | {r['pooled_delta_pct']:+.3f} | {r['days_observed']:.1f} | {r['verdict']} |")
    (OUT / "verdict.md").write_text("\n".join(md) + "\n")
    print(f"[paper-nonvec] events={len(events)} functions={len(rows)} promote={sum(r['verdict'] == 'PROMOTE_CANDIDATE' for r in rows)} reject={sum(r['verdict'] == 'REJECT' for r in rows)}")


if __name__ == "__main__":
    main()
