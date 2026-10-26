#!/usr/bin/env python3
"""Recompute each side as its NPZ tail or published SQL row changes."""
import argparse
import json
import os
import signal
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_forward_state import _one, OUT

WATCH_CODE = (ROOT / "v12_quick_engine.py", ROOT / "tools" / "opt" / "v12_pilot.py", ROOT / "tools" / "v15_forward_state.py", ROOT / "per_sym_live_set.py")
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
STOP = False


def _stop(*_):
    global STOP
    STOP = True


def _stamp(paths):
    return tuple((path.stat().st_mtime_ns if path.exists() else 0) for path in paths)


def _sides(path):
    return {side.strip().upper() for side in path.read_text().split() if side.endswith(("_LONG", "_SHORT"))}


def _sql_versions(db, sides):
    if not sides:
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        marks = ",".join("?" for _ in sides)
        return {symbol_side: updated for symbol_side, updated in con.execute(f"SELECT sym_side,updated_at FROM per_sym_active WHERE sym_side IN ({marks})", tuple(sides))}
    finally:
        con.close()


def _publish(symbol_side, state):
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{symbol_side}.json"
    tmp = target.with_name(target.name + f".new.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, target)
    print(f"[{time.strftime('%FT%TZ', time.gmtime())}] {symbol_side} ok={state.get('ok')} tail={state.get('npz_tail')} settings={state.get('settings_updated_at')} reason={state.get('reason')}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sides-file", required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    sides_path = Path(args.sides_file).expanduser()
    db = Path(os.environ.get("PER_SYM_STORE_DB") or "")
    if not db.is_file():
        raise SystemExit("PER_SYM_STORE_DB must point to the current published store")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    code_stamp = _stamp(WATCH_CODE)
    side_stamp = 0
    db_stamp = ()
    sides = set()
    pending = set()
    npz_stamp = {}
    sql_versions = {}
    running = {}
    retry = {}
    with ProcessPoolExecutor(max_workers=max(1, min(4, args.workers))) as pool:
        while not STOP:
            if _stamp(WATCH_CODE) != code_stamp:
                print("forward code changed; restarting process pool on current files", flush=True)
                pool.shutdown(wait=True, cancel_futures=True)
                os.execv(sys.executable, [sys.executable, *sys.argv])
            current_side_stamp = sides_path.stat().st_mtime_ns
            if current_side_stamp != side_stamp:
                sides = _sides(sides_path)
                pending.update(symbol_side for symbol_side in sides if symbol_side.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES))
                side_stamp = current_side_stamp
                print(f"watching {len(sides)} exact sides", flush=True)
            for symbol_side in sides:
                symbol = symbol_side.rsplit("_", 1)[0]
                path = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
                mtime = path.stat().st_mtime_ns if path.exists() else 0
                previous_mtime = npz_stamp.get(symbol_side)
                if previous_mtime != mtime:
                    npz_stamp[symbol_side] = mtime
                    if previous_mtime is not None or symbol.endswith(CRYPTO_SUFFIXES):
                        pending.add(symbol_side)
                    retry.pop(symbol_side, None)
            current_db_stamp = _stamp((db, Path(str(db) + "-wal")))
            if current_db_stamp != db_stamp:
                try:
                    versions = _sql_versions(db, sides)
                    for symbol_side in sides:
                        if sql_versions.get(symbol_side) != versions.get(symbol_side):
                            if db_stamp or symbol_side.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES):
                                pending.add(symbol_side)
                    sql_versions = versions
                    db_stamp = current_db_stamp
                except Exception as exc:
                    print(f"SQL version scan retry: {exc}", flush=True)
            done = [future for future in running if future.done()]
            for future in done:
                symbol_side, started_npz, started_settings = running.pop(future)
                try:
                    state = future.result()
                except Exception as exc:
                    state = {"sym_side": symbol_side, "ok": False, "reason": f"worker_error:{exc}"}
                _publish(symbol_side, state)
                if npz_stamp.get(symbol_side) != started_npz or sql_versions.get(symbol_side) != started_settings:
                    pending.add(symbol_side)
                elif not state.get("ok") and retry.get(symbol_side, 0) < 2:
                    retry[symbol_side] = retry.get(symbol_side, 0) + 1
                    pending.add(symbol_side)
            active = {item[0] for item in running.values()}
            for symbol_side in sorted(pending - active):
                if len(running) >= max(1, min(4, args.workers)):
                    break
                pending.discard(symbol_side)
                future = pool.submit(_one, symbol_side)
                running[future] = (symbol_side, npz_stamp.get(symbol_side), sql_versions.get(symbol_side))
            if running:
                wait(tuple(running), timeout=1, return_when=FIRST_COMPLETED)
            else:
                time.sleep(1)


if __name__ == "__main__":
    main()
