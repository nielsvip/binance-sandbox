#!/usr/bin/env python3
"""Read-only census of live-vs-vector discrepancy records (data/vector_disparity_queue.jsonl).

Groups every record by (kind, venue, action, reason family), counts events, distinct
positions and episodes, and measures a dollar figure from broker fills where one exists:

* blocked/refused native exits (VEC_DICTATE_ONLY_BLOCK / VEC_LAST_REFUSE on CLOSE/REDUCE):
  delay_usd = (price at the NEXT real closing fill - price when the exit was blocked)
  x qty of that closing order, signed for the side. Positive = blocking the native exit
  was worth money; negative = it cost money. The blocked price comes from the reason
  (px...) or from klines_cache (1m, then 3m, then 15m close of the event minute/bar).
* vec orders that bypassed native gates (VEC_LAST_OVERRIDE OPEN): realized_usd = sum of
  realizedPnl - commission(USDT) of that sym_side's fills from the first fill after the
  event until 24 h later (or the next OVERRIDE episode) — an episode figure, not a
  counterfactual.
Records with no matching fill keep usd=None (never estimated).

Usage: python3 tools/vec_log_census.py [--days 7] [--since-iso 2026-10-27T00:00:00]
Writes data/reports/vec_log_fixes/census.json and census.md.
"""
import argparse
import bisect
import collections
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data" / "vector_disparity_queue.jsonl"
FILLS = ROOT / "data" / "exchange_fills"
KLINES = ROOT / "klines_cache"
OUT = ROOT / "data" / "reports" / "vec_log_fixes"
EXIT_ACTIONS = ("CLOSE", "QUICK_CLOSE", "QUICK_QUICK_CLOSE", "REDUCE", "STRONG_REDUCE")
EPISODE_GAP_S = 3600.0


def family(reason):
    r = re.sub(r"^(QUICK_)+", "", str(reason or ""))
    r = re.sub(r"\|.*$", "", r).strip()
    out = []
    for tok in r.split("_"):
        if re.fullmatch(r"[A-Z]+", tok) or (out == ["E"] and re.fullmatch(r"\d+", tok)):
            out.append(tok)
            continue
        break
    fam = "_".join(out) or r[:24]
    return fam[:48]


def _ts_ms(iso):
    return int(dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


_KCACHE = {}


def kline_series(symbol, tf):
    key = (symbol, tf)
    if key not in _KCACHE:
        path = KLINES / f"{symbol}_{tf}.json"
        try:
            rows = json.loads(path.read_text())
            _KCACHE[key] = ([_ts_ms(r["timestamp"]) for r in rows], [float(r["close"]) for r in rows])
        except Exception:
            _KCACHE[key] = ([], [])
    return _KCACHE[key]


def price_at(symbol, ts):
    ms = int(ts * 1000)
    for tf, span in (("1m", 60000), ("3m", 180000), ("15m", 900000)):
        times, closes = kline_series(symbol, tf)
        if not times or ms < times[0] or ms > times[-1] + span:
            continue
        i = bisect.bisect_right(times, ms) - 1
        if i >= 0:
            return closes[i], tf
    return None, None


_FCACHE = {}


def fills(account, symbol):
    key = (account, symbol)
    if key not in _FCACHE:
        try:
            rows = json.loads((FILLS / account / f"{symbol}.json").read_text()).get("rows", [])
        except Exception:
            rows = []
        rows.sort(key=lambda r: r.get("time", 0))
        _FCACHE[key] = rows
    return _FCACHE[key]


def closing_order_after(account, symbol, side, ts, horizon_s=7 * 86400):
    close_side = "SELL" if side == "LONG" else "BUY"
    rows = [r for r in fills(account, symbol) if r.get("positionSide") == side and r.get("side") == close_side and ts * 1000 <= r.get("time", 0) <= (ts + horizon_s) * 1000]
    if not rows:
        return None
    oid = rows[0].get("orderId")
    leg = [r for r in rows if r.get("orderId") == oid]
    qty = sum(float(r["qty"]) for r in leg)
    px = sum(float(r["price"]) * float(r["qty"]) for r in leg) / qty if qty else None
    return {"time": leg[0]["time"] / 1000.0, "qty": qty, "px": px}


def episode_realized(account, symbol, side, t0, t1):
    open_side = "BUY" if side == "LONG" else "SELL"
    rows = [r for r in fills(account, symbol) if r.get("positionSide") == side and t0 * 1000 <= r.get("time", 0) <= t1 * 1000]
    if not rows or rows[0].get("side") != open_side:
        return None
    pnl = sum(float(r.get("realizedPnl", 0) or 0) for r in rows)
    fee = sum(float(r.get("commission", 0) or 0) for r in rows if r.get("commissionAsset") in ("USDT", "USDC"))
    return pnl - fee


def load(since_ts):
    out = []
    for line in QUEUE.open():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(rec.get("ts", 0)) >= since_ts:
            out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--since-iso", default="")
    ap.add_argument("--no-usd", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    since = dt.datetime.fromisoformat(args.since_iso).replace(tzinfo=dt.timezone.utc).timestamp() if args.since_iso else now - args.days * 86400
    recs = load(since)
    groups = collections.defaultdict(lambda: {"events": 0, "positions": set(), "first": 9e18, "last": 0.0, "by_day": collections.Counter(), "episodes": [], "sample": ""})
    last_seen = {}
    for rec in sorted(recs, key=lambda r: r["ts"]):
        kind = rec.get("kind") or "(untagged)"
        key = (kind, rec.get("venue", ""), str(rec.get("action", "")), family(rec.get("reason")), "+".join(rec.get("gates") or []))
        g = groups[key]
        g["events"] += 1
        g["positions"].add(rec.get("position_key"))
        g["first"] = min(g["first"], rec["ts"])
        g["last"] = max(g["last"], rec["ts"])
        g["by_day"][dt.datetime.fromtimestamp(rec["ts"], dt.timezone.utc).strftime("%m-%d")] += 1
        g["sample"] = g["sample"] or str(rec.get("reason", ""))[:120]
        ek = (key, rec.get("position_key"))
        if ek not in last_seen or rec["ts"] - last_seen[ek] > EPISODE_GAP_S:
            g["episodes"].append(rec)
        last_seen[ek] = rec["ts"]
    rows = []
    for key, g in groups.items():
        kind, venue, action, fam, gates = key
        usd, priced, unpriced, how = 0.0, 0, 0, ""
        if not args.no_usd and venue == "crypto":
            for rec in g["episodes"]:
                pk = str(rec.get("position_key") or "")
                if ":" not in pk:
                    unpriced += 1
                    continue
                account, ss = pk.split(":", 1)
                symbol, side = ss.rsplit("_", 1)
                val = None
                if kind in ("VEC_DICTATE_ONLY_BLOCK", "VEC_LAST_REFUSE", "VEC_HOLD_EXPIRED") and action in EXIT_ACTIONS:
                    how = "delay_usd: next real close vs blocked price"
                    m = re.search(r"px([0-9.]+)", str(rec.get("reason", "")))
                    p0 = float(m.group(1)) if m else price_at(symbol, rec["ts"])[0]
                    leg = closing_order_after(account, symbol, side, rec["ts"])
                    if p0 and leg and leg["px"]:
                        sign = 1.0 if side == "LONG" else -1.0
                        val = (leg["px"] - p0) * sign * leg["qty"]
                elif kind == "VEC_LAST_OVERRIDE" and action in ("OPEN", "AUGMENT"):
                    how = "realized_usd: 24h episode after the vec open"
                    val = episode_realized(account, symbol, side, rec["ts"], rec["ts"] + 86400)
                if val is None:
                    unpriced += 1
                else:
                    priced += 1
                    usd += val
        rows.append({"kind": kind, "venue": venue, "action": action, "family": fam, "gates": gates,
                     "events": g["events"], "positions": len(g["positions"]), "episodes": len(g["episodes"]),
                     "first_utc": dt.datetime.fromtimestamp(g["first"], dt.timezone.utc).strftime("%m-%d %H:%M"),
                     "last_utc": dt.datetime.fromtimestamp(g["last"], dt.timezone.utc).strftime("%m-%d %H:%M"),
                     "by_day": dict(sorted(g["by_day"].items())), "usd": round(usd, 2) if priced else None,
                     "usd_priced_episodes": priced, "usd_unpriced_episodes": unpriced, "usd_method": how,
                     "sample_reason": g["sample"]})
    rows.sort(key=lambda r: -r["events"])
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": str(QUEUE.relative_to(ROOT)),
            "since_utc": dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "records": len(recs),
            "queue_first_utc": dt.datetime.fromtimestamp(min((r["ts"] for r in recs), default=since), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (OUT / "census.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=1) + "\n")
    lines = [f"# VEC discrepancy census — {meta['generated_utc']}", "", f"Source `{meta['source']}`, {meta['records']} records since {meta['since_utc']} (first record {meta['queue_first_utc']}).", "",
             "| events | pos | episodes | kind | venue | action | family | gates | last | usd (priced/unpriced) |", "|---:|---:|---:|---|---|---|---|---|---|---|"]
    for r in rows:
        usd = "n/a" if r["usd"] is None else f"{r['usd']:+.2f}"
        lines.append(f"| {r['events']} | {r['positions']} | {r['episodes']} | {r['kind']} | {r['venue']} | {r['action']} | {r['family']} | {r['gates']} | {r['last_utc']} | {usd} ({r['usd_priced_episodes']}/{r['usd_unpriced_episodes']}) |")
    (OUT / "census.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(meta))
    for r in rows[:40]:
        print(r["events"], r["episodes"], r["kind"], r["venue"], r["action"], r["family"], r["gates"], r["last_utc"], r["usd"], f"{r['usd_priced_episodes']}/{r['usd_unpriced_episodes']}")


if __name__ == "__main__":
    main()
