#!/usr/bin/env python3
"""Keep forward workers subscribed to current tradeable crypto sides."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "tradeable_keys.json"
CACHE = ROOT / "data" / "fwdstate_side_list_synced"
SOURCES = (("s1-pub", "ALL", "/home/niels/fwdstate_syms.txt"), ("s2", "LONG", "/home/niels/fwdstate_syms.txt"), ("s5", "STOCK", "/home/niels/fwd_stocks_syms.txt"))


def main():
    raw = json.loads(KEYS.read_text())
    if not isinstance(raw, list):
        raise ValueError("tradeable_keys.json is not a list")
    sides = {str(key).split(":", 1)[-1].upper() for key in raw if ":" in str(key)}
    if not sides or any(not side.endswith(("_LONG", "_SHORT")) for side in sides):
        raise ValueError("invalid tradeable side list")
    stock_sides = set()
    for direction in ("LONG", "SHORT"):
        source = ROOT / f"symbols_trb_{direction.lower()}.json"
        if source.is_file():
            stock_sides.update(f"{str(symbol).upper()}_{direction}" for symbol in json.loads(source.read_text()))
        else:
            cached = CACHE / "s5"
            if cached.is_file():
                stock_sides.update(cached.read_text().split())
            print(f"missing {source}; retaining last synced S5 stock sides", file=sys.stderr)
    for host, direction, remote_path in SOURCES:
        selected = sorted(stock_sides if direction == "STOCK" else (side for side in sides if direction == "ALL" or side.endswith("_" + direction)))
        if not selected:
            raise ValueError("empty forward side list")
        payload = "\n".join(selected) + "\n"
        marker = CACHE / host
        if marker.exists() and marker.read_text() == payload:
            continue
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host,
                   f"python3 -c 'import pathlib,sys; p=pathlib.Path(\"{remote_path}\"); data=sys.stdin.read(); old=p.read_text() if p.exists() else \"\"; p.write_text(data) if data != old else None'"]
        try:
            result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            print(f"{host}: timed out", file=sys.stderr)
            return 1
        if result.returncode:
            print(f"{host}: {result.stderr.strip()}", file=sys.stderr)
            return 1
        CACHE.mkdir(parents=True, exist_ok=True)
        marker.write_text(payload)
        print(f"{host}: {len(selected)} sides", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
