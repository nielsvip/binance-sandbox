#!/usr/bin/env python3
"""Recompute each side as its NPZ tail or published SQL row changes.

2026-10-27 (USER: every sym_side S1 trades or holds must have a correct, fresh state within SECONDS of each 15m bar
close, every bar). Measured before this rewrite: first state of a bar 100 s after the close, p50 240-360 s, only
43-119 of 135 sides current per bar, 153-666 jobs per bar for 135 sides. Causes fixed here:
  * the closed bar lands in the .tail sidecar (npz_tail_service), not the zip, so watching the zip's mtime missed it for
    up to an hour -> the sidecar is watched too and the tail is max(zip tip, sidecar tip); the compute reads zip+sidecar
    through tools/npz_merged_view.py.
  * every zip mtime change (barrefresh, mirror, compaction) re-queued the side, so most jobs recomputed an unchanged
    tail and starved the sides whose bar had just landed -> a side is computed once per NEW TAIL; a same-tail content
    rewrite is recomputed at the lowest priority.
  * the queue ran alphabetically -> held sides (data/vector_direct/held_<venue>.json, written by
    tools/vector_direct_trader.py from the real broker read) go first, then the rest of the universe, then rewrites.
  * a held side outside the sides file never got a state -> held crypto sides are watched too.
  * failures retried immediately twice (a stale NPZ produced three identical not-ok states in one second) -> a side
    whose NPZ is behind the last closed bar waits for its tail to move; transient errors retry once after RETRY_S.
  * other hosts' quarter lanes rsynced their (env-less / 2-day-old) states over S1's -> any state file that changes
    under us and is WORSE than what we published (older npz_tail, or not ok where ours was ok) is restored from memory
    at once and logged RESTORED_EXTERNAL_OVERWRITE.
Pool size stays at most 4 (USER 2026-10-25: max 4 sym evals in RAM; S1 OOM-rebooted twice).
"""
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
from tools.v15_forward_state import _one, OUT, store_db
from npz_tail_store import _tail_tip_ts

WATCH_CODE = (ROOT / "v12_quick_engine.py", ROOT / "tools" / "opt" / "v12_pilot.py", ROOT / "tools" / "v15_forward_state.py", ROOT / "per_sym_live_set.py", ROOT / "tools" / "npz_merged_view.py", Path(__file__).resolve())
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
BAR_S = 900
RETRY_S = 30.0
REWRITE_MIN_S = 120.0
NICE = int(os.environ.get("FWDSTATE_NICE", "-5"))
STOP = False


def _stop(*_):
    global STOP
    STOP = True


def _stamp(paths):
    return tuple((path.stat().st_mtime_ns if path.exists() else 0) for path in paths)


def _sides(path):
    return {side.strip().upper() for side in path.read_text().split() if side.endswith(("_LONG", "_SHORT"))}


def _is_crypto(symbol_side):
    return symbol_side.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES)


def _held(path):
    try:
        doc = json.loads(path.read_text())
        return {str(s).upper() for s in doc.get("sides") or [] if str(s).upper().endswith(("_LONG", "_SHORT"))}
    except Exception:
        return set()


def _sql_versions(db, sides):
    if not sides:
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        marks = ",".join("?" for _ in sides)
        return {symbol_side: updated for symbol_side, updated in con.execute(f"SELECT sym_side,updated_at FROM per_sym_active WHERE sym_side IN ({marks})", tuple(sides))}
    finally:
        con.close()


def _npz_tail(path):
    """Last bar-open timestamp in the NPZ (reads only the timestamps member)."""
    import numpy as np
    try:
        with np.load(path, allow_pickle=False) as z:
            ts = z["timestamps"]
            return int(ts[-1]) if len(ts) else 0
    except Exception:
        return None


def _timed(symbol_side):
    t0 = time.time()
    state = _one(symbol_side)
    return state, t0, time.time()


def _publish(symbol_side, state, extra=""):
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{symbol_side}.json"
    tmp = target.with_name(target.name + f".new.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, target)
    print(f"[{time.strftime('%FT%TZ', time.gmtime())}] {symbol_side} ok={state.get('ok')} tail={state.get('npz_tail')} settings={state.get('settings_updated_at')} src={state.get('src')} reason={state.get('reason')}{extra}", flush=True)
    try:
        return target.stat().st_mtime_ns
    except OSError:
        return None


def _worse(disk, ours):
    if not isinstance(disk, dict):
        return True
    d_tail, o_tail = int(disk.get("npz_tail") or 0), int(ours.get("npz_tail") or 0)
    if d_tail != o_tail:
        return d_tail < o_tail
    return bool(ours.get("ok")) and not disk.get("ok")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sides-file", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--held-file", default=str(Path.home() / "binance" / "data" / "vector_direct" / "held_crypto.json"))
    args = parser.parse_args()
    workers = max(1, min(4, args.workers))
    sides_path = Path(args.sides_file).expanduser()
    held_path = Path(args.held_file).expanduser()
    db = Path(store_db() or "")
    if not db.is_file():
        raise SystemExit("PER_SYM_STORE_DB must point to the current published store")
    os.environ["PER_SYM_STORE_DB"] = str(db)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if os.nice(0) > NICE:
        # live states outrank backtests (pilots run at nice 15) and the klines/ingest helpers at 0; pool workers inherit
        import subprocess
        subprocess.run(["sudo", "-n", "renice", "-n", str(NICE), "-p", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    code_stamp = _stamp(WATCH_CODE)
    side_stamp = held_stamp = None
    db_stamp = ()
    base_sides, held, sides = set(), set(), set()
    npz_mtime, npz_tail, zip_tail, side_tail = {}, {}, {}, {}
    sql_versions = {}
    computed = {}  # side -> (tail, settings_version, npz_mtime, ok, finished_at)
    published = {}  # side -> (state, mtime_ns)
    pending = {}  # side -> priority class (0 held new tail, 1 new tail / settings / new side, 2 retry, 3 same-tail rewrite)
    running = {}
    last_heal = 0.0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while not STOP:
            now = time.time()
            if _stamp(WATCH_CODE) != code_stamp:
                print("forward code changed; restarting process pool on current files", flush=True)
                pool.shutdown(wait=True, cancel_futures=True)
                os.execv(sys.executable, [sys.executable, "-u", *sys.argv])  # keep -u: S2/S6 cron guards match "python -u"; a bare re-exec escaped them and stacked one ~4 GB pool per code change
            current = sides_path.stat().st_mtime_ns
            if current != side_stamp:
                base_sides, side_stamp = _sides(sides_path), current
            current = held_path.stat().st_mtime_ns if held_path.exists() else 0
            if current != held_stamp:
                held, held_stamp = {s for s in _held(held_path) if _is_crypto(s)}, current
            new_sides = base_sides | held
            if new_sides != sides:
                print(f"watching {len(new_sides)} exact sides ({len(held)} held, {len(held - base_sides)} held outside the sides file)", flush=True)
                sides = new_sides
            expected_tail = (int(now) // BAR_S - 1) * BAR_S
            for symbol in {s.rsplit("_", 1)[0] for s in sides}:
                path = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
                side_path = path.parent / ".tail" / f"{symbol}.jsonl"
                try:
                    zip_mtime = path.stat().st_mtime_ns
                except OSError:
                    zip_mtime = 0
                try:
                    tail_mtime = side_path.stat().st_mtime_ns
                except OSError:
                    tail_mtime = 0
                mtime = (zip_mtime, tail_mtime)
                previous = npz_mtime.get(symbol)
                if previous != mtime:
                    npz_mtime[symbol] = mtime
                    if previous is None or previous[0] != zip_mtime:
                        zip_tail[symbol] = _npz_tail(path) if zip_mtime else None
                    if previous is None or previous[1] != tail_mtime:
                        side_tail[symbol] = _tail_tip_ts(side_path) if tail_mtime else None
                    tips = [v for v in (zip_tail.get(symbol), side_tail.get(symbol)) if v]
                    if tips:
                        npz_tail[symbol] = max(tips)
            current_db_stamp = _stamp((db, Path(str(db) + "-wal")))
            if current_db_stamp != db_stamp:
                try:
                    sql_versions = _sql_versions(db, sides)
                    db_stamp = current_db_stamp
                except Exception as exc:
                    print(f"SQL version scan retry: {exc}", flush=True)
            active = {item[0] for item in running.values()}
            for symbol_side in sides:
                if symbol_side in active:
                    continue
                symbol = symbol_side.rsplit("_", 1)[0]
                tail, mtime, version = npz_tail.get(symbol), npz_mtime.get(symbol), sql_versions.get(symbol_side)
                last = computed.get(symbol_side)
                if last is None:
                    cls = 0 if symbol_side in held else 1
                elif tail != last[0]:
                    cls = 0 if symbol_side in held else 1
                elif version != last[1]:
                    cls = 1
                elif not last[3] and now - last[4] >= RETRY_S and last[5] < 1 and not (_is_crypto(symbol_side) and (tail or 0) < expected_tail):
                    cls = 2
                elif mtime and last[2] and mtime[1] != last[2][1]:
                    cls = 0 if symbol_side in held else 1  # same-bar correction appended to the sidecar (replace_latest)
                elif mtime != last[2] and now - last[4] >= REWRITE_MIN_S:
                    cls = 3
                else:
                    continue
                if cls < pending.get(symbol_side, 9):
                    pending[symbol_side] = cls
            for future in [future for future in running if future.done()]:
                symbol_side, started = running.pop(future)
                try:
                    state, t0, t1 = future.result()
                except Exception as exc:
                    state, t0, t1 = {"sym_side": symbol_side, "ok": False, "reason": f"worker_error:{exc}"}, started[4], time.time()
                tail = state.get("npz_tail") or started[0]
                lag = f" lag_s={time.time() - (int(tail) + BAR_S):.1f}" if tail else ""
                mt = _publish(symbol_side, state, f" class={started[3]} queue_s={t0 - started[4]:.1f} compute_s={t1 - t0:.1f}{lag}")
                published[symbol_side] = (state, mt)
                prev = computed.get(symbol_side)
                retries = (prev[5] + 1) if prev and not state.get("ok") and prev[0] == started[0] else 0
                computed[symbol_side] = (started[0], started[1], started[2], bool(state.get("ok")), time.time(), retries)
            if now - last_heal >= 1.0:
                last_heal = now
                for symbol_side, (state, mt) in list(published.items()):
                    if symbol_side in active:
                        continue
                    target = OUT / f"{symbol_side}.json"
                    try:
                        disk_mt = target.stat().st_mtime_ns
                    except OSError:
                        disk_mt = None
                    if disk_mt == mt:
                        continue
                    try:
                        disk = json.loads(target.read_text()) if disk_mt else None
                    except Exception:
                        disk = None
                    if _worse(disk, state):
                        why = f" RESTORED_EXTERNAL_OVERWRITE (disk ok={(disk or {}).get('ok')} tail={(disk or {}).get('npz_tail')} src={(disk or {}).get('src')})"
                        published[symbol_side] = (state, _publish(symbol_side, state, why))
                    else:
                        published[symbol_side] = (disk, disk_mt)
            for symbol_side in sorted(pending, key=lambda s: (pending[s], s)):
                if len(running) >= workers:
                    break
                cls = pending.pop(symbol_side)
                symbol = symbol_side.rsplit("_", 1)[0]
                future = pool.submit(_timed, symbol_side)
                running[future] = (symbol_side, (npz_tail.get(symbol), sql_versions.get(symbol_side), npz_mtime.get(symbol), cls, time.time()))
            if running:
                wait(tuple(running), timeout=0.25, return_when=FIRST_COMPLETED)
            else:
                time.sleep(0.25)


if __name__ == "__main__":
    main()
