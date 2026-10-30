"""Mac position books -> the executors that trade them (S1 crypto, S5 stocks).

USER 2026-10-30 (verbatim): "Macbook runs the ez_ and tradier_ suites completely while only paper trading until the
trading scripts get fixed but it should rsyn positions data to s1 crypto and s5 stocks which the vectorized trader needs
to use for its positions tracking".

The Mac suites own the position LIFECYCLE — entry_price, opened_at, max_gain, gain, last_signal, was_reduced — and keep
it per account in <acct>/long_positions.json / short_positions.json. Neither broker API hands that back: Tradier
/positions returns quantity + cost_basis only, Binance positionRisk returns mark and entry but no history. The executors
on S1/S5 read the broker for the quantity that is TRUE and had no source at all for the rest.

Measured before this tool existed: S5 held NO position file for any stock account, and S1's crypto books were last
written 2026-03-12 — 7.5 months stale. Nothing was syncing.

WHAT THIS IS NOT. The broker stays the only authority on what is held; this is lifecycle context beside it. The reader
(tools/vector_direct_trader.py Trader.positions) never lets a row here create, remove or resize a position.

Every live path is raced per CLAUDE.md: each destination is tried over all of its aliases at once and the first copy that
lands with a matching sha256 wins.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_ACCOUNTS = ("ang", "inf", "flz", "men", "fin")
STOCK_ACCOUNTS = ("tra", "trb", "trc")
# first path to land with a matching hash wins; a path that drops is simply not the winner
ROUTES = {
    "crypto": [("s1-int", "binance"), ("s1-pub", "binance"), ("s1", "binance"), ("niels@10.0.0.3", "binance")],
    "stocks": [("s5", "binance"), ("s5", "trc7d_root"), ("niels@10.0.0.5", "binance"), ("niels@10.0.0.5", "trc7d_root")],
}
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]
LIFECYCLE = ("entry_price", "opened_at", "entry_time", "max_gain", "gain", "prev_gain", "last_signal", "was_reduced",
             "was_reentered", "mark_price", "last_updated", "max_quantity", "cycle_peak_gain", "trade_wing")


def stamp(epoch=None):
    return dt.datetime.fromtimestamp(epoch if epoch is not None else time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_book(path):
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"
    return (doc, None) if isinstance(doc, dict) else (None, f"not an object: {type(doc).__name__}")


def quantity(record):
    for key in ("positionAmt", "quantity", "qty", "position_amt"):
        if record.get(key) is not None:
            try:
                return abs(float(record[key]))
            except (TypeError, ValueError):
                continue
    return 0.0


def collect(accounts):
    """{acct: {SYM_SIDE: {qty, lifecycle fields...}}} for every account book that could be read."""
    out, meta = {}, {"books": [], "unreadable": []}
    for acct in accounts:
        held = {}
        for side in ("long", "short"):
            path = ROOT / acct / f"{side}_positions.json"
            if not path.exists():
                meta["unreadable"].append({"acct": acct, "file": f"{side}_positions.json", "error": "missing"})
                continue
            doc, error = read_book(path)
            if doc is None:
                meta["unreadable"].append({"acct": acct, "file": f"{side}_positions.json", "error": error})
                continue
            meta["books"].append({"acct": acct, "side": side, "records": len(doc), "file_epoch": round(path.stat().st_mtime, 3),
                                  "file_age_s": round(time.time() - path.stat().st_mtime, 1)})
            for key, record in doc.items():
                if not isinstance(record, dict):
                    continue
                qty = quantity(record)
                if qty <= 0:
                    continue
                symbol = str(record.get("symbol") or key.split(":")[-1].rsplit("_", 1)[0]).upper()
                position_side = str(record.get("position_side") or record.get("side") or side).upper()
                position_side = "LONG" if position_side.startswith("L") else "SHORT"
                row = {"qty": qty}
                for field in LIFECYCLE:
                    if record.get(field) is not None:
                        row[field] = record[field]
                held[f"{symbol}_{position_side}"] = row
        out[acct] = held
    return out, meta


def document(venue, accounts):
    held, meta = collect(accounts)
    now = time.time()
    return {"source": "mac", "venue": venue, "epoch": now, "written_at": stamp(now), "accounts": held,
            "sides": sorted({ss for per in held.values() for ss in per}),
            "held_total": sum(len(per) for per in held.values()), "books": meta["books"], "unreadable": meta["unreadable"],
            "contract": "broker quantity is the only authority; these are lifecycle fields beside it"}


def push(host, tree, local, digest):
    remote = f"~/{tree}/data/vector_direct/mac_positions_{local.stem.rsplit('_', 1)[-1]}.json"
    target = f"~/{tree}/data/vector_direct/{local.name}"
    try:
        subprocess.run(SSH + [host, f"mkdir -p ~/{tree}/data/vector_direct"], check=True, capture_output=True, timeout=25)
        subprocess.run(["rsync", "-az", "--timeout=25", "-e", " ".join(SSH), str(local), f"{host}:{target}"],
                       check=True, capture_output=True, timeout=60)
        got = subprocess.run(SSH + [host, f"sha256sum {target} | cut -d' ' -f1"], check=True, capture_output=True, timeout=25).stdout.decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        return {"host": host, "tree": tree, "ok": False, "error": f"{type(exc).__name__}: {detail.decode(errors='replace')[:160] or str(exc)[:160]}", "remote": remote}
    return {"host": host, "tree": tree, "ok": got == digest, "sha256": got, "remote": target}


def write_local(doc, venue):
    out = ROOT / "data" / "vector_direct"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mac_positions_{venue}.json"
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    with tempfile.NamedTemporaryFile("w", dir=str(out), prefix=f".{path.name}.", delete=False) as fh:
        fh.write(body)
        temp = Path(fh.name)
    os.replace(temp, path)
    return path, hashlib.sha256(body.encode()).hexdigest()


def run(venues, dry_run=False):
    report = {"epoch": time.time(), "written_at": stamp(), "venues": {}}
    for venue in venues:
        doc = document(venue, CRYPTO_ACCOUNTS if venue == "crypto" else STOCK_ACCOUNTS)
        path, digest = write_local(doc, venue)
        entry = {"local": str(path), "sha256": digest, "held_total": doc["held_total"], "sides": doc["sides"],
                 "books": len(doc["books"]), "unreadable": doc["unreadable"], "routes": []}
        if not dry_run:
            routes = ROUTES[venue]
            with cf.ThreadPoolExecutor(max_workers=len(routes)) as pool:
                entry["routes"] = [f.result() for f in [pool.submit(push, host, tree, path, digest) for host, tree in routes]]
            entry["delivered"] = sorted({r["tree"] for r in entry["routes"] if r["ok"]})
            entry["ok"] = bool(entry["delivered"])
        report["venues"][venue] = entry
    status = ROOT / "data" / "reports" / "mac_positions_push.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps(report, indent=1, default=str))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--venue", choices=("crypto", "stocks", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true", help="write the local document, push nothing")
    a = ap.parse_args(argv)
    venues = ("crypto", "stocks") if a.venue == "both" else (a.venue,)
    report = run(venues, a.dry_run)
    for venue, entry in report["venues"].items():
        failed = [f"{r['host']}:{r['tree']} {r.get('error', 'hash mismatch')}" for r in entry["routes"] if not r["ok"]]
        print(f"{stamp()} {venue}: {entry['held_total']} held sides from {entry['books']} books -> "
              f"{'dry-run' if a.dry_run else ','.join(entry.get('delivered') or ['NOTHING DELIVERED'])}"
              + (f" | unreadable {len(entry['unreadable'])}" if entry["unreadable"] else "")
              + (f" | failed routes: {'; '.join(failed)}" if failed else ""))
    return 0 if a.dry_run or all(e.get("ok") for e in report["venues"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
