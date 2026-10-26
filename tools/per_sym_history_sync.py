#!/usr/bin/env python3
"""Append Mac and S1 sandbox per-sym history into the S1 live database.

Active settings are never replaced. Source row ids are recorded only as per-source
watermarks; S1 assigns its own ids and retains all distinct historical rows.
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAC_DB = ROOT / "data/hourly_reconfig/per_sym_store.db"
S1_DB = Path("/home/niels/binance/data/hourly_reconfig/per_sym_store.db")
S1_SANDBOX_DB = Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db")
REMOTE_SCRIPT = Path("/home/niels/binance-sandbox/tools/per_sym_history_sync.py")
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12"]


def connect(path):
    connection = sqlite3.connect(str(path), timeout=60)
    connection.row_factory = sqlite3.Row
    return connection


def progress(connection, source):
    connection.execute("CREATE TABLE IF NOT EXISTS per_sym_history_sync_progress (source TEXT PRIMARY KEY, last_id INTEGER NOT NULL)")
    row = connection.execute("SELECT last_id FROM per_sym_history_sync_progress WHERE source=?", (source,)).fetchone()
    return int(row[0]) if row else 0


def ingest_rows(target, source, rows):
    columns = [row[1] for row in target.execute("PRAGMA table_info(per_sym_history)") if row[1] != "id"]
    target.execute("CREATE TABLE IF NOT EXISTS per_sym_history_sync_uid (uid TEXT PRIMARY KEY)")
    last = progress(target, source)
    seen = added = 0
    pending = []
    for record in rows:
        source_id = int(record["id"])
        if source_id <= last:
            continue
        values = None if record.get("_uid_only") else {column: record.get(column) for column in columns}
        uid = record.get("_uid_only") or hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        pending.append((source_id, uid, values))
        if len(pending) >= 250:
            n, last = commit_batch(target, source, pending, columns)
            added += n
            seen += len(pending)
            pending.clear()
    if pending:
        n, last = commit_batch(target, source, pending, columns)
        added += n
        seen += len(pending)
    return {"source": source, "seen": seen, "added": added, "last_id": last}


def commit_batch(target, source, batch, columns):
    marks = ",".join("?" for _ in columns)
    target.execute("BEGIN IMMEDIATE")
    try:
        added = 0
        for _, uid, values in batch:
            if values is None:
                if target.execute("SELECT 1 FROM per_sym_history_sync_uid WHERE uid=?", (uid,)).fetchone() is None:
                    raise ValueError("missing referenced history uid")
                continue
            if target.execute("INSERT OR IGNORE INTO per_sym_history_sync_uid(uid) VALUES (?)", (uid,)).rowcount:
                target.execute(f"INSERT INTO per_sym_history ({','.join(columns)}) VALUES ({marks})", [values[column] for column in columns])
                added += 1
        last = batch[-1][0]
        target.execute("INSERT INTO per_sym_history_sync_progress(source,last_id) VALUES (?,?) ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id", (source, last))
        target.commit()
    except Exception:
        target.rollback()
        raise
    return added, last


def run_ingest(source, ingest_file=None):
    target = connect(S1_DB)
    try:
        if ingest_file:
            incoming_file = Path(ingest_file)
            if incoming_file.parent != S1_DB.parent or not incoming_file.name.startswith("incoming_mac_history_"):
                raise ValueError("invalid incoming history path")
            input_stream = gzip.open(incoming_file, "rb")
        else:
            input_stream = gzip.GzipFile(fileobj=sys.stdin.buffer)
        with input_stream:
            incoming = (json.loads(line) for line in input_stream if line.strip())
            reports = [ingest_rows(target, source, incoming)]
        sandbox = connect(S1_SANDBOX_DB)
        try:
            last = progress(target, "s1_sandbox")
            rows = (dict(row) for row in sandbox.execute("SELECT * FROM per_sym_history WHERE id>? ORDER BY id", (last,)))
            reports.append(ingest_rows(target, "s1_sandbox", rows))
        finally:
            sandbox.close()
        print(json.dumps(reports), flush=True)
    finally:
        target.close()
    if ingest_file:
        Path(ingest_file).unlink()


def run_push(host):
    subprocess.run(["rsync", "-az", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=12", str(Path(__file__).resolve()), f"{host}:{REMOTE_SCRIPT}"], check=True, timeout=90)
    command = [*SSH, host, f"python3 {REMOTE_SCRIPT} --watermark mac"]
    last = int(subprocess.check_output(command, text=True, timeout=25).strip())
    known = set(subprocess.check_output([*SSH, host, f"python3 {REMOTE_SCRIPT} --uids"], text=True, timeout=40).splitlines())
    source = connect(MAC_DB)
    spool = Path(tempfile.gettempdir()) / f"per_sym_history_mac_after_{last}.jsonl.gz"
    count = 0
    try:
        if not spool.is_file():
            building = spool.with_suffix(spool.suffix + ".building")
            with gzip.open(building, "wt", compresslevel=3) as output:
                for row in source.execute("SELECT * FROM per_sym_history WHERE id>? ORDER BY id", (last,)):
                    record = dict(row)
                    values = {key: value for key, value in record.items() if key != "id"}
                    uid = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
                    payload = {"id": record["id"], "_uid_only": uid} if uid in known else record
                    output.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
                    count += 1
            os.replace(building, spool)
        source.close()
        remote_spool = S1_DB.parent / f"incoming_mac_history_after_{last}.jsonl.gz"
        subprocess.run(["rsync", "-a", "--partial", "--append-verify", "--timeout=180", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=12", str(spool), f"{host}:{remote_spool}"], check=True, timeout=900)
        result = subprocess.run([*SSH, host, f"python3 {REMOTE_SCRIPT} --ingest mac --ingest-file {remote_spool}"], capture_output=True, text=False, timeout=480)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace")[-1000:])
        print(json.dumps({"mac_rows_sent": count, "s1": result.stdout.decode(errors="replace").strip()}), flush=True)
        spool.unlink()
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host")
    parser.add_argument("--watermark")
    parser.add_argument("--uids", action="store_true")
    parser.add_argument("--ingest")
    parser.add_argument("--ingest-file")
    args = parser.parse_args()
    if args.uids:
        target = connect(S1_DB)
        try:
            for (uid,) in target.execute("SELECT uid FROM per_sym_history_sync_uid"):
                print(uid)
        finally:
            target.close()
    elif args.watermark:
        target = connect(S1_DB)
        try:
            print(progress(target, args.watermark))
        finally:
            target.close()
    elif args.ingest:
        run_ingest(args.ingest, args.ingest_file)
    elif args.host:
        run_push(args.host)
    else:
        parser.error("choose --host, --watermark, or --ingest")


if __name__ == "__main__":
    main()
