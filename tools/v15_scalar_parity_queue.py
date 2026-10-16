#!/usr/bin/env python3
"""v15_scalar_parity_queue — Mac side of the S2 scalar parity lane (USER 2026-10-14: "run backtest_v12_engine … to get real parity
between the live and vector scripts instead of just overriding live with vector results … only on s2, give it plenty of time to
finish each, the order sounds perfect").
Builds data/parity/scalar_lane/queue.json from the latest PUBLISHED boards (data/published_selection.json: the exact set live is
registered with, overrides inline + overrides_md5) in the agreed order:
  tier 1  boards that went live today (latest persym_golive_<today>*.json, result PASS / registered)
  tier 2  sym_sides with an open live position or a live trade in the last 3 days (positions files + data/per_trade_returns)
  tier 3  sym_sides on a 2-loss streak (data/v15_365_priority.json)
  tier 4  every other published board
Within a tier: open position > recent trades > streak > rest. Pushed md5-verified to S2; tools/v15_scalar_parity_lane.py runs it.
"""
import argparse
import datetime
import glob
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "parity" / "scalar_lane" / "queue.json"
ACCOUNTS = ("ang", "fin", "flz", "inf", "men", "tra", "trb", "trc")


def _load(p):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return None


def live_today(today):
    out = set()
    for f in sorted(glob.glob(str(ROOT / "data" / "daily_chain" / f"persym_golive_{today}*.json"))):
        d = _load(f) or {}
        for ss, e in (d.get("sym_sides") or {}).items():
            if isinstance(e, dict) and (e.get("registered") or e.get("result") == "PASS"):
                out.add(ss)
    return out


def open_positions():
    out = set()
    for a in ACCOUNTS:
        for side in ("long", "short"):
            d = _load(ROOT / a / f"{side}_positions.json") or {}
            for k, p in d.items():
                if isinstance(p, dict) and abs(float(p.get("positionAmt") or 0.0)) > 0:
                    out.add(f"{p.get('symbol')}_{str(p.get('position_side') or side).upper()}")
    return out


def recent_trades(days):
    out = {}
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    for f in glob.glob(str(ROOT / "data" / "per_trade_returns" / "*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
                if datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) >= cut:
                    ss = f"{r['symbol']}_{r['side'].upper()}"
                    out[ss] = out.get(ss, 0) + 1
            except Exception:
                continue
    return out


def streak():
    d = _load(ROOT / "data" / "v15_365_priority.json") or {}
    return set((d.get("sym_sides") or {}).keys())


def build(days):
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    sel = _load(ROOT / "data" / "published_selection.json") or {}
    pub = sel.get("sym_sides") or {}
    lt, pos, tr, st = live_today(today), open_positions(), recent_trades(days), streak()
    items = []
    for ss, e in pub.items():
        ov = e.get("overrides") or {}
        if not ov:
            continue
        md5 = e.get("overrides_md5") or hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()
        tier = 1 if ss in lt else 2 if (ss in pos or ss in tr) else 3 if ss in st else 4
        items.append({"sym_side": ss, "tier": tier, "open_position": ss in pos, "trades_recent": tr.get(ss, 0), "streak": ss in st,
                      "overrides_md5": md5, "published_at": e.get("published_at"), "history_id": e.get("history_id"),
                      # USER 2026-10-15: the board's OWN NPZ md5 (published_selection evidence.npz_id), so the s2 lane can
                      # verify the published result on the NPZ that produced it instead of a newly refreshed window.
                      "npz_md5": ((e.get("evidence") or {}).get("npz_id") or None), "overrides": ov})
    items.sort(key=lambda x: (x["tier"], not x["open_position"], -x["trades_recent"], not x["streak"], x["sym_side"]))
    return {"_meta": {"built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "data/published_selection.json",
                      "n": len(items), "tiers": {t: sum(1 for i in items if i["tier"] == t) for t in (1, 2, 3, 4)},
                      "order": "1 live today > 2 open position / live trades last %dd > 3 2-loss streak > 4 rest" % days}, "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--push", default="s2", help="ssh host of the lane ('' = no push)")
    a = ap.parse_args()
    q = build(a.days)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(q, indent=1, default=str))
    tmp.replace(OUT)
    print(f"[queue] {q['_meta']['n']} items tiers={q['_meta']['tiers']} -> {OUT}")
    if a.push:
        dest = "binance-sandbox/data/parity/scalar_lane/"
        subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", a.push, f"mkdir -p ~/{dest}"], check=True)
        subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", str(OUT), f"{a.push}:{dest}queue.json.tmp"], check=True)
        local = hashlib.md5(OUT.read_bytes()).hexdigest()
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", a.push, f"cd ~/{dest} && md5sum queue.json.tmp | cut -d' ' -f1"], capture_output=True, text=True, check=True)
        if r.stdout.strip() != local:
            sys.exit(f"[queue] push md5 mismatch {r.stdout.strip()} != {local}")
        subprocess.run(["ssh", "-o", "BatchMode=yes", a.push, f"cd ~/{dest} && mv queue.json.tmp queue.json"], check=True)
        print(f"[queue] pushed to {a.push}:{dest}queue.json md5 {local}")


if __name__ == "__main__":
    main()
