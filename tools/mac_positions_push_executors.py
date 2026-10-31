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
import threading
import fcntl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_ACCOUNTS = ("ang", "inf", "flz", "men", "fin")
STOCK_ACCOUNTS = ("tra", "trb", "trc")
# first path to land with a matching hash wins; a path that drops is simply not the winner
ROUTES = {
    "crypto": [("s1-int", "binance"), ("s1-pub", "binance"), ("s1", "binance"), ("niels@10.0.0.3", "binance")],
    "stocks": [("s5", "binance"), ("s5", "trc7d_root"), ("niels@10.0.0.5", "binance"), ("niels@10.0.0.5", "trc7d_root")],
}
SSH = ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/binance-positions-%C", "-o", "ControlPersist=60", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2", "-o", "StrictHostKeyChecking=accept-new"]
LAST_ROUTE = {}
REPORT_LOCK = threading.Lock()
LIFECYCLE = ("entry_price", "opened_at", "entry_time", "max_gain", "gain", "prev_gain", "last_signal", "was_reduced",
             "was_reentered", "mark_price", "last_updated", "max_quantity", "cycle_peak_gain", "trade_wing", "fixed_stop_price", "fixed_stop_anchor", "fixed_stop_armed")


def stamp(epoch=None):
    return dt.datetime.fromtimestamp(epoch if epoch is not None else time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_book(path):
    try:
        with path.open() as source:
            file_epoch = os.fstat(source.fileno()).st_mtime
            doc = json.load(source)
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}", None
    return (doc, None, file_epoch) if isinstance(doc, dict) else (None, f"not an object: {type(doc).__name__}", file_epoch)


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
            doc, error, file_epoch = read_book(path)
            if doc is None:
                meta["unreadable"].append({"acct": acct, "file": f"{side}_positions.json", "error": error})
                continue
            meta["books"].append({"acct": acct, "side": side, "records": len(doc), "file_epoch": round(file_epoch, 3),
                                  "file_age_s": round(time.time() - file_epoch, 1)})
            for key, record in doc.items():
                if not isinstance(record, dict):
                    continue
                qty = quantity(record)
                if qty <= 0:
                    continue
                symbol = str(record.get("symbol") or key.split(":")[-1].rsplit("_", 1)[0]).upper()
                position_side = str(record.get("position_side") or record.get("side") or side).upper()
                position_side = "LONG" if position_side.startswith("L") else "SHORT"
                row = {"qty": qty, "source_file_epoch": file_epoch, "source_file": f"{acct}/{side}_positions.json"}
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
        subprocess.run(SSH + [host, f"mkdir -p ~/{tree}/data/vector_direct"], check=True, capture_output=True, timeout=8)
        subprocess.run(["rsync", "-az", "--timeout=8", "-e", " ".join(SSH), str(local), f"{host}:{target}"],
                       check=True, capture_output=True, timeout=12)
        got = subprocess.run(SSH + [host, f"sha256sum {target} | cut -d' ' -f1"], check=True, capture_output=True, timeout=8).stdout.decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        return {"host": host, "tree": tree, "ok": False, "error": f"{type(exc).__name__}: {detail.decode(errors='replace')[:160] or str(exc)[:160]}", "remote": remote}
    return {"host": host, "tree": tree, "ok": got == digest, "sha256": got, "remote": target}


def push_destination(venue, tree, local, digest):
    hosts = [host for host, target in ROUTES[venue] if target == tree]
    preferred = LAST_ROUTE.get((venue, tree), "s1-pub" if venue == "crypto" else "s5")
    hosts.sort(key=lambda host: host != preferred)
    attempts = []
    for host in hosts:
        result = push(host, tree, local, digest)
        attempts.append(result)
        if result["ok"]:
            LAST_ROUTE[(venue, tree)] = host
            break
    return attempts


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
                 "books": len(doc["books"]), "source_books": doc["books"], "snapshot_epoch": doc.get("epoch"), "unreadable": doc["unreadable"], "routes": []}
        if not dry_run:
            trees = sorted({tree for host, tree in ROUTES[venue]})
            with cf.ThreadPoolExecutor(max_workers=len(trees)) as pool:
                entry["routes"] = [route for result in pool.map(lambda tree: push_destination(venue, tree, path, digest), trees) for route in result]
            entry["delivered"] = sorted({r["tree"] for r in entry["routes"] if r["ok"]})
            entry["ok"] = entry["delivered"] == trees
        entry["completed_epoch"] = time.time()
        report["venues"][venue] = entry
    status = ROOT / "data" / "reports" / "mac_positions_push.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_LOCK:
        try:
            previous = json.loads(status.read_text())
        except (OSError, ValueError):
            previous = {}
        combined = dict(previous.get("venues") or {})
        combined.update(report["venues"])
        temp = status.with_suffix(".tmp")
        temp.write_text(json.dumps({**report, "venues": combined}, indent=1, default=str))
        os.replace(temp, status)
        if not dry_run:
            receipt = status.parent / f"mac_positions_push_{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}.jsonl"
            with receipt.open("a") as output:
                output.write(json.dumps(report, separators=(",", ":"), default=str) + "\n")
    return report


def book_fingerprint(venue):
    values = []
    for account in CRYPTO_ACCOUNTS if venue == "crypto" else STOCK_ACCOUNTS:
        for side in ("long", "short"):
            path = ROOT / account / f"{side}_positions.json"
            try:
                info = path.stat()
                values.append((str(path), info.st_ino, info.st_mtime_ns, info.st_size))
            except OSError:
                values.append((str(path), None))
    return tuple(values)


def watch(venues):
    lock_path = ROOT / "data" / "vector_direct" / "mac_positions_push.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("position transport watcher already running")
        completed, pending, last_send = {}, {}, {}
        with cf.ThreadPoolExecutor(max_workers=len(venues)) as pool:
            while True:
                for venue in venues:
                    job = pending.get(venue)
                    if job is not None and job[0].done():
                        try:
                            report = job[0].result()["venues"][venue]
                            if report.get("ok"):
                                completed[venue] = job[1]
                            print(f"{stamp()} {venue} positions delivered={report.get('delivered')} ok={report.get('ok')} held={report['held_total']}", flush=True)
                        except Exception as exc:
                            print(f"{stamp()} {venue} POSITION_TRANSPORT_FAILED {type(exc).__name__}: {exc}", flush=True)
                        pending.pop(venue)
                    fingerprint = book_fingerprint(venue)
                    if venue not in pending and (fingerprint != completed.get(venue) or time.monotonic() - last_send.get(venue, 0) >= 5):
                        pending[venue] = (pool.submit(run, (venue,)), fingerprint)
                        last_send[venue] = time.monotonic()
                time.sleep(0.25)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--venue", choices=("crypto", "stocks", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true", help="write the local document, push nothing")
    ap.add_argument("--watch", action="store_true", help="continuously deliver changed position books; independently per venue")
    a = ap.parse_args(argv)
    venues = ("crypto", "stocks") if a.venue == "both" else (a.venue,)
    if a.watch and not a.dry_run:
        return watch(venues)
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
