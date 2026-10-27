#!/usr/bin/env python3
"""Keep S1/S2 crypto and S5/S6 stock workers on current exact side books."""
import json
import fcntl
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "tradeable_keys.json"
CACHE = ROOT / "data" / "fwdstate_side_list_synced"
LOCAL_LEASE = ROOT / "data" / "live_failover" / "lease_local.json"
SOURCES = (("s1-pub", "ALL", "/home/niels/fwdstate_syms.txt"), ("s2", "LONG", "/home/niels/fwdstate_syms.txt"), ("s5", "STOCK", "/home/niels/fwd_stocks_syms.txt"), ("s6", "STOCK", "/home/niels/fwd_stocks_syms.txt"))


def main():
    lock_handle = open("/tmp/fwdstate_side_list_sync.lock", "a")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    raw = json.loads(KEYS.read_text())
    if not isinstance(raw, list):
        raise ValueError("tradeable_keys.json is not a list")
    sides = {str(key).split(":", 1)[-1].upper() for key in raw if ":" in str(key)}
    if not sides or any(not side.endswith(("_LONG", "_SHORT")) for side in sides):
        raise ValueError("invalid tradeable side list")
    stock_sides = set()
    for direction in ("LONG", "SHORT"):
        source = ROOT / f"symbols_trb_{direction.lower()}.json"
        if not source.is_file():
            cached = CACHE / "stocks"
            if not cached.is_file():
                raise FileNotFoundError(f"required stock side book missing: {source}")
            stock_sides.update(cached.read_text().split())
            print(f"missing {source}; retaining last synced stock sides", file=sys.stderr)
            continue
        values = json.loads(source.read_text())
        if isinstance(values, dict):
            values = values.get("symbols") or values.get("pairs")
        if not isinstance(values, list):
            raise ValueError(f"invalid stock side book: {source}")
        stock_sides.update(f"{str(symbol).upper()}_{direction}" for symbol in values if str(symbol).strip())
    if not stock_sides:
        raise ValueError("empty stock side books; refusing to replace worker subscriptions")
    try:
        lease = json.loads(LOCAL_LEASE.read_text())
    except (OSError, ValueError):
        lease = {}
    leased = set(str(lease.get("venues") or "").split(","))
    skip_stock = lease.get("holder") == "s1" and (not leased or "stocks" in leased)
    failed = []
    for host, direction, remote_path in (SOURCES[2:] + SOURCES[:2]):
        if direction == "STOCK" and skip_stock:
            continue
        selected = sorted(stock_sides if direction == "STOCK" else (side for side in sides if direction == "ALL" or side.endswith("_" + direction)))
        if not selected:
            raise ValueError("empty forward side list")
        payload = "\n".join(selected) + "\n"
        marker = CACHE / host
        checked = CACHE / f"{host}.checked"
        try:
            last_check = float(checked.read_text())
        except (OSError, ValueError):
            last_check = 0.0
        if marker.is_file() and marker.read_text() == payload and time.time() - last_check < 30:
            continue
        # Always compare the remote file; a local cache cannot prove that a worker still has the same list.
        try:
            check = subprocess.run(["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new", host,
                                    f"cat {remote_path}"], capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            check = subprocess.CompletedProcess([], 124, "", "remote list read timed out")
        if check.returncode == 0 and check.stdout == payload:
            CACHE.mkdir(parents=True, exist_ok=True)
            marker.write_text(payload)
            checked.write_text(str(time.time()))
            continue
        command = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new", host,
                   f"python3 -c 'import pathlib,sys; p=pathlib.Path(\"{remote_path}\"); data=sys.stdin.read(); old=p.read_text() if p.exists() else \"\"; p.write_text(data) if data != old else None'"]
        try:
            result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            print(f"{host}: timed out", file=sys.stderr)
            failed.append(host)
            continue
        if result.returncode:
            print(f"{host}: {result.stderr.strip()}", file=sys.stderr)
            failed.append(host)
            continue
        CACHE.mkdir(parents=True, exist_ok=True)
        marker.write_text(payload)
        checked.write_text(str(time.time()))
        print(f"{host}: {len(selected)} sides", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
