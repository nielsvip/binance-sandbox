#!/usr/bin/env python3
"""Merge the last seven days of executed-trade history across Mac and S1.

Only rows inside the window travel. Existing rows and rotated archives stay in
place. The Mac coordinates the sync; a later run catches up after either host
has been offline.
"""

import argparse
import bisect
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = re.compile(r"^[a-z][a-z0-9_]*$")
LEDGER = re.compile(r"^([A-Za-z0-9_.-]+_(?:LONG|SHORT))\.jsonl(?:\..+\.bak)?$")
SSH = [
    "ssh",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=8",
]
RSYNC_SSH = (
    "ssh -o ControlMaster=no -o ControlPath=none -o BatchMode=yes -o ConnectTimeout=8"
)
REMOTE_ROOTS = ("/home/niels/binance-sandbox", "/home/niels/binance")
CRYPTO_ACCOUNTS = {"ang", "fin", "flz", "inf", "men"}
STOCK_ACCOUNTS = {"tra", "trb", "trc"}


def _epoch(value):
    try:
        n = float(value)
        return n / 1000.0 if n > 1e11 else n
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError):
            return 0.0


def _identity(line):
    try:
        row = json.loads(line)
        if not isinstance(row, dict):
            return None
        raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest(), _epoch(row.get("ts"))
    except (TypeError, ValueError):
        return None


def _rel(path, history):
    if path.parent.parent != history or not ACCOUNT.fullmatch(path.parent.name):
        return None
    match = LEDGER.fullmatch(path.name)
    return f"{path.parent.name}/{match.group(1)}.jsonl" if match else None


def export(root, now, days=7):
    history = root / "data/history"
    cutoff = now - days * 86400
    rows = {}
    seen = {}
    invalid = 0
    for path in sorted(history.glob("*/*")):
        rel = _rel(path, history)
        if rel is None or not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    item = _identity(line)
                    if item is None:
                        invalid += 1
                        continue
                    digest, stamp = item
                    if (
                        stamp < cutoff
                        or stamp > now + 300
                        or digest in seen.setdefault(rel, set())
                    ):
                        continue
                    seen[rel].add(digest)
                    rows.setdefault(rel, []).append(line.rstrip("\r\n"))
        except OSError:
            continue
    return {
        "schema": 1,
        "now": now,
        "days": days,
        "rows": rows,
        "invalid_rows": invalid,
    }


def _load(path):
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or not isinstance(data.get("rows"), dict):
        raise ValueError("invalid history sync pack")
    return data


def merge(packs):
    now = min(float(pack["now"]) for pack in packs)
    days = min(float(pack["days"]) for pack in packs)
    cutoff = now - days * 86400
    rows = {}
    seen = {}
    for pack in packs:
        for rel, lines in pack["rows"].items():
            if not _safe_rel(rel) or not isinstance(lines, list):
                raise ValueError(f"unsafe history path {rel!r}")
            for line in lines:
                item = _identity(line)
                if (
                    item is None
                    or not cutoff <= item[1] <= now + 300
                    or item[0] in seen.setdefault(rel, set())
                ):
                    continue
                seen[rel].add(item[0])
                rows.setdefault(rel, []).append(line)
    for lines in rows.values():
        lines.sort(key=lambda line: (_identity(line)[1], _identity(line)[0]))
    return {"schema": 1, "now": now, "days": days, "rows": rows}


def lease_authorized(pack, alerts):
    transitions = []
    for line in alerts.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") in ("LEASE_S1", "LEASE_MAC"):
            transitions.append((_epoch(event.get("ts")), event))
    transitions.sort(key=lambda item: item[0])
    epochs = [item[0] for item in transitions]
    rows = {}
    accepted = rejected = 0
    for rel, lines in pack["rows"].items():
        account = rel.split("/", 1)[0]
        venue = (
            "crypto"
            if account in CRYPTO_ACCOUNTS
            else "stocks" if account in STOCK_ACCOUNTS else None
        )
        for line in lines:
            item = _identity(line)
            if item is None or venue is None:
                rejected += 1
                continue
            index = bisect.bisect_right(epochs, item[1]) - 1
            event = transitions[index][1] if index >= 0 else {}
            venues = event.get("venues")
            accounts = event.get("accounts")
            venue_ok = not venues or venue in venues
            account_ok = not accounts or account in (
                accounts.get(venue, []) if isinstance(accounts, dict) else []
            )
            if event.get("kind") == "LEASE_S1" and venue_ok and account_ok:
                rows.setdefault(rel, []).append(line)
                accepted += 1
            else:
                rejected += 1
    return {**pack, "rows": rows}, {
        "s1_lease_rows": accepted,
        "s1_unproven_rows": rejected,
        "lease_transitions": len(transitions),
    }


def _safe_rel(rel):
    path = Path(rel)
    return (
        len(path.parts) == 2
        and ACCOUNT.fullmatch(path.parts[0])
        and LEDGER.fullmatch(path.parts[1])
        and path.parts[1].endswith(".jsonl")
    )


def _trade_signature(line):
    try:
        row = json.loads(line)
        return (
            str(row.get("type") or "").upper(),
            float(row.get("qty")),
            float(row.get("price")),
        ), _epoch(row.get("ts"))
    except (TypeError, ValueError):
        return None


def apply(root, pack, backup_stamp, dry_run=False):
    history = root / "data/history"
    changed = 0
    added = 0
    ambiguous = 0
    for rel, lines in sorted(pack["rows"].items()):
        if not _safe_rel(rel) or not isinstance(lines, list):
            raise ValueError(f"unsafe history path {rel!r}")
        target = history / rel
        current = set()
        signatures = {}
        for path in sorted(target.parent.glob(target.name + "*")):
            if _rel(path, history) != rel or not path.is_file():
                continue
            with path.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    item = _identity(line)
                    if item is not None:
                        current.add(item[0])
                    trade = _trade_signature(line)
                    if trade is not None:
                        signatures.setdefault(trade[0], []).append(trade[1])
        missing = []
        for line in lines:
            item = _identity(line)
            if item is None or item[0] in current:
                continue
            trade = _trade_signature(line)
            if trade is not None and any(
                abs(stamp - trade[1]) < 3 for stamp in signatures.get(trade[0], [])
            ):
                ambiguous += 1
                continue
            current.add(item[0])
            if trade is not None:
                signatures.setdefault(trade[0], []).append(trade[1])
            missing.append(line)
        if not missing:
            continue
        changed += 1
        added += len(missing)
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = root / "backups" / f"before_history_7d_sync_{backup_stamp}" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        with target.open("a", encoding="utf-8") as output:
            for line in missing:
                output.write(line + "\n")
            output.flush()
            os.fsync(output.fileno())
    return {
        "root": str(root),
        "changed_files": changed,
        "added_rows": added,
        "ambiguous_rows": ambiguous,
        "dry_run": dry_run,
    }


def _run(command, timeout=90):
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {result.stderr[-300:]}"
        )
    return result.stdout


def sync(root, host, days, dry_run=False):
    lock_path = root / "data/history/.history_7d_sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = time.time()
        stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        with tempfile.TemporaryDirectory(prefix="history_7d_sync_") as temp:
            temp = Path(temp)
            local = export(root, now, days)
            packs = [local]
            remote_path = f"/tmp/history_7d_sync_{stamp}_{os.getpid()}.json"
            tool = REMOTE_ROOTS[0] + "/tools/history_7d_sync.py"
            for index, remote_root in enumerate(REMOTE_ROOTS):
                _run(
                    SSH
                    + [
                        host,
                        "python3",
                        tool,
                        "export",
                        "--root",
                        remote_root,
                        "--out",
                        remote_path,
                        "--now",
                        str(now),
                        "--days",
                        str(days),
                    ]
                )
                dest = temp / f"remote_{index}.json"
                _run(
                    [
                        "rsync",
                        "-az",
                        "--timeout=45",
                        "-e",
                        RSYNC_SSH,
                        f"{host}:{remote_path}",
                        str(dest),
                    ]
                )
                packs.append(_load(dest))
            alerts_path = temp / "s1_failover_alerts.jsonl"
            _run(
                [
                    "rsync",
                    "-az",
                    "--timeout=45",
                    "-e",
                    RSYNC_SSH,
                    f"{host}:binance/data/live_failover/ALERTS.jsonl",
                    str(alerts_path),
                ]
            )
            s1_live, lease_audit = lease_authorized(packs[2], alerts_path.read_text())
            packs = [local, s1_live]
            union = merge(packs)
            union_path = temp / "union.json"
            union_path.write_text(json.dumps(union, separators=(",", ":")) + "\n")
            local_result = apply(root, union, stamp, dry_run)
            remote_results = []
            _run(
                [
                    "rsync",
                    "-az",
                    "--timeout=45",
                    "-e",
                    RSYNC_SSH,
                    str(union_path),
                    f"{host}:{remote_path}",
                ]
            )
            for remote_root in REMOTE_ROOTS:
                command = SSH + [
                    host,
                    "python3",
                    tool,
                    "apply",
                    "--root",
                    remote_root,
                    "--pack",
                    remote_path,
                    "--backup-stamp",
                    stamp,
                ]
                if dry_run:
                    command.append("--dry-run")
                raw = _run(command)
                remote_results.append(json.loads(raw))
            _run(SSH + [host, "rm", "-f", remote_path])
            return {
                "window_days": days,
                "window_rows": sum(map(len, union["rows"].values())),
                "window_files": len(union["rows"]),
                "s1_lease_audit": lease_audit,
                "mac": local_result,
                "s1": remote_results,
            }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("export", "apply", "sync"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pack", type=Path)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--backup-stamp", default="manual")
    parser.add_argument("--host", default="s1-pub")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    root = args.root.resolve()
    if args.mode == "export":
        if args.out is None:
            parser.error("export needs --out")
        result = export(
            root, args.now if args.now is not None else time.time(), args.days
        )
        args.out.write_text(json.dumps(result, separators=(",", ":")) + "\n")
        print(
            json.dumps(
                {
                    "root": str(root),
                    "files": len(result["rows"]),
                    "rows": sum(map(len, result["rows"].values())),
                    "invalid_rows": result["invalid_rows"],
                }
            )
        )
    elif args.mode == "apply":
        if args.pack is None:
            parser.error("apply needs --pack")
        print(
            json.dumps(apply(root, _load(args.pack), args.backup_stamp, args.dry_run))
        )
    else:
        print(json.dumps(sync(root, args.host, args.days, args.dry_run)))


if __name__ == "__main__":
    main()
