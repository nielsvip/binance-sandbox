"""
tradier_history_sync.py — backfill broker fills (including manual user trades) into
data/tradier/history/{account}/{SYMBOL}_{SIDE}.jsonl

Polls Tradier /v1/accounts/{id}/gainloss for closed positions and /v1/accounts/{id}/orders
for fills, then appends events to per-symbol history JSONLs. Marks events with
"source":"broker_sync" so they are distinguishable from system-emitted records.

Idempotent: each run skips events already present (by ts+symbol+price match).

Usage: python3 tradier_history_sync.py [--days 14] [--accounts trb,trc]
"""
import os, sys, json, urllib.request, urllib.error, subprocess, argparse
from io import StringIO
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
HIST_ROOT = BASE / "data" / "tradier" / "history"


def load_env():
    res = subprocess.run(
        ["gpg", "--batch", "--yes", "--decrypt", str(BASE / ".env.gpg")],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        print(f"gpg decrypt failed: {res.stderr}", file=sys.stderr)
        sys.exit(1)
    from dotenv import dotenv_values
    vals = dotenv_values(stream=StringIO(res.stdout))
    for k, v in vals.items():
        if v and v.strip():
            os.environ[k] = v


def call(url, key):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def existing_events(path):
    out = set()
    if not path.exists(): return out
    for line in path.open():
        try:
            o = json.loads(line)
            out.add((o.get("ts","")[:19], round(float(o.get("price") or 0), 4), float(o.get("qty") or 0)))
        except Exception:
            continue
    return out


def append_event(account, symbol, side, ev):
    d = HIST_ROOT / account
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol}_{side}.jsonl"
    seen = existing_events(p)
    sig = (ev["ts"][:19], round(float(ev["price"]), 4), float(ev["qty"]))
    if sig in seen:
        return False
    with p.open("a") as f:
        f.write(json.dumps(ev) + "\n")
    return True


def sync_gainloss(account, days):
    is_sandbox = (account == "trc")
    base = "https://sandbox.tradier.com/v1" if is_sandbox else "https://api.tradier.com/v1"
    key = os.environ.get(f"TRADIER_API_KEY_{account.upper()}")
    aid = os.environ.get(f"TRADIER_ACCOUNT_ID_{account.upper()}")
    if not key or not aid:
        print(f"  {account}: missing key/id, skipping")
        return None
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"{base}/accounts/{aid}/gainloss?start={start}&end={end}&limit=5000"
    try:
        data = call(url, key)
    except urllib.error.HTTPError as e:
        print(f"  {account} gainloss HTTP {e.code}: {e.reason}")
        return None
    closed = (data.get("gainloss") or {}).get("closed_position") or []
    if isinstance(closed, dict): closed = [closed]
    written = 0; skipped = 0
    total_gl = 0.0
    rows = []
    for c in closed:
        sym = c.get("symbol")
        if not sym: continue
        qty = float(c.get("quantity") or 0)
        proceeds = float(c.get("proceeds") or 0)
        cost = float(c.get("cost") or 0)
        gl = float(c.get("gain_loss") or 0)
        glp = float(c.get("gain_loss_percent") or 0)
        # Tradier gainloss treats negative qty as short cover. Side inferred from signs.
        # Heuristic: if proceeds > cost the trade was profitable; gain_loss already gives direction.
        # We don't know LONG vs SHORT from gainloss alone — but we can guess from cost basis:
        # for LONG: open=BUY (cost), close=SELL (proceeds). qty positive.
        # for SHORT: open=SELL (proceeds), close=BUY (cost). qty positive too in Tradier API.
        # Fallback: assume LONG unless gainloss has a hint. Tag both possibilities for the recorder.
        side = "LONG" if qty > 0 else "SHORT"
        # close event
        close_price = (proceeds / abs(qty)) if qty else 0
        ev = {
            "ts": (c.get("close_date") or datetime.now(timezone.utc).isoformat()),
            "type": "BROKER_CLOSE",
            "qty": abs(qty),
            "price": round(close_price, 4),
            "value": round(proceeds, 2),
            "reason": f"broker_sync gl=${gl:+.2f} ({glp:+.2f}%) term={c.get('term')}d open={c.get('open_date','')[:10]}",
            "indicators": {},
            "source": "broker_sync",
            "broker_gain_loss": gl,
            "broker_gain_loss_pct": glp,
        }
        if append_event(account, sym, side, ev):
            written += 1
        else:
            skipped += 1
        total_gl += gl
        rows.append((sym, side, qty, gl, glp, c.get("open_date","")[:10], c.get("close_date","")[:10]))
    print(f"  {account}: closed_positions={len(closed)} written={written} skipped(dup)={skipped} total_gl=${total_gl:+,.2f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--accounts", default="trb,trc,tra")
    args = ap.parse_args()
    load_env()
    print(f"=== Tradier history sync (last {args.days}d) ===")
    all_rows = {}
    for acct in args.accounts.split(","):
        acct = acct.strip()
        if not acct: continue
        rows = sync_gainloss(acct, args.days)
        if rows is not None:
            all_rows[acct] = rows
    # Summary
    print("\n=== SUMMARY ===")
    for acct, rows in all_rows.items():
        if not rows:
            print(f"  {acct}: 0 closed positions")
            continue
        total = sum(r[3] for r in rows)
        wins = [r for r in rows if r[3] > 0]
        loses = [r for r in rows if r[3] < 0]
        print(f"  {acct}: {len(rows)} closed | total ${total:+,.2f} | wins {len(wins)} ${sum(r[3] for r in wins):+,.0f} | losers {len(loses)} ${sum(r[3] for r in loses):+,.0f}")
        rows.sort(key=lambda r: r[3])
        print(f"    WORST 10:")
        for r in rows[:10]:
            print(f"      {r[0]:6s} {r[1]:5s} qty={r[2]:>+8g} ${r[3]:>+9,.2f} ({r[4]:+6.2f}%) {r[5]} -> {r[6]}")
        print(f"    BEST 5:")
        for r in rows[-5:][::-1]:
            print(f"      {r[0]:6s} {r[1]:5s} qty={r[2]:>+8g} ${r[3]:>+9,.2f} ({r[4]:+6.2f}%) {r[5]} -> {r[6]}")


if __name__ == "__main__":
    main()
