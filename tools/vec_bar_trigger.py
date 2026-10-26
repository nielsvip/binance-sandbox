#!/usr/bin/env python3
"""vec_bar_trigger — vector STATE the moment a kline lands (USER 2026-10-25).

The executor's verdict is 1-3s per side; all the delay was orchestration (poll
cadence, full-shard cycles, zip-only reads). This loop runs the shard's
process() per side every TRIGGER_S: a stat-only mtime gate (zip + sidecar tail)
skips unchanged symbols without opening hundred-MB files, and process() itself
NO_NEW_BARs cheaply when the merged tip hasn't advanced. New bar ->
engine (1-3s) -> STATE written -> publish(). No live file touched.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRIGGER_S = 1.0
WORKERS = 4
_PRODUCER_ENGINES = {}


def held_sides(base):
    path = Path(base) / "data" / "vec_live" / "held_sides.json"
    try:
        payload = json.loads(path.read_text())
        if time.time() - float(payload["generated_ts"]) > 180.0:
            return set()
        return set(payload["held"])
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def shard_sides(sets, serves, shard, held=()):
    held = set(held)
    return sorted((ss for ss in sets if serves(ss, shard)), key=lambda ss: (ss not in held, ss))


def should_probe(ss, zip_mtime, tail_mtime, seen, revision=None):
    cur = (float(zip_mtime or 0.0), float(tail_mtime or 0.0))
    if revision is not None:
        cur += (revision,)
    if seen.get(ss) != cur:
        seen[ss] = cur
        return True
    return False


def store_mtimes(base, sym, path=None):
    ind = Path(base) / "backtest_v8" / "indicators"
    npz = Path(path) if path is not None else ind / f"{sym}.npz"
    try:
        z = npz.stat().st_mtime
    except OSError:
        z = 0.0
    try:
        t = (ind / ".tail" / f"{sym}.jsonl").stat().st_mtime
    except OSError:
        t = 0.0
    return z, t


def split_ss(ss):
    return ss.rsplit("_", 1)


def publish_producer(base, shard, engine):
    if shard not in ("crypto_long", "crypto_short", "stock_long", "stock_short"):
        return
    path = Path(base) / "data" / "vec_live" / f"producer_{shard}.json"
    payload = {"shard": shard, "engine_md5": engine["combined"], "v12_quick_engine_md5": engine["v12_quick_engine.py"], "generated_at": time.time(), "pid": os.getpid()}
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True))
    os.replace(temporary, path)


def run_once(base, shard, workers=WORKERS, verbose=False):
    sys.path.insert(0, str(Path(base)))
    os.chdir(str(base))
    import vec_live_executor as E

    try:
        from tools import vec_shard as _shard

        serves = _shard.serves
    except Exception:
        serves = lambda ss, sh: True  # noqa: E731
    em = E.engine_md5()
    os.environ["VEC_LIVE_ENGINE_MD5"] = em["combined"]
    sets = E.load_sets()
    _PRODUCER_ENGINES[shard] = em
    publish_producer(base, shard, em)
    held = held_sides(base)
    sides = shard_sides(sets, serves, shard, held)
    seen = run_once._seen if hasattr(run_once, "_seen") else {}
    jobs = []
    for ss in sides:
        entry, ov = sets[ss]
        if ov is None:
            continue
        sym, _ = split_ss(ss)
        z, t = store_mtimes(base, sym, E.npz_path(sym))
        if should_probe(ss, z, t, seen, revision=(E.set_md5(ov), em["combined"])):
            jobs.append((ss, entry, ov, em["combined"], False))
    run_once._seen = seen
    rec = {
        "t": time.time(),
        "shard": shard,
        "sides": len(sides),
        "probed": len(jobs),
        "acted": 0,
        "errors": 0,
    }
    if not jobs:
        return rec
    held_jobs = [job for job in jobs if job[0] in held]
    other_jobs = [job for job in jobs if job[0] not in held]
    rec["held_probed"] = len(held_jobs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for tier, tier_jobs in (("held", held_jobs), ("other", other_jobs)):
            futures = [pool.submit(E._worker, job) for job in tier_jobs]
            for future in as_completed(futures):
                try:
                    r = future.result()
                except Exception:
                    rec["errors"] += 1
                    continue
                if r.get("status") == "EXCEPTION":
                    rec["errors"] += 1
                elif r.get("status") != "NO_NEW_BAR":
                    rec["acted"] += 1
                    if tier == "held":
                        try:
                            E.publish()
                        except Exception:
                            pass
    if rec["acted"]:
        try:
            E.publish()
        except Exception:
            pass
    if verbose:
        print(json.dumps(rec, sort_keys=True), flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--shard", default=os.environ.get("VEC_SHARD", "all"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--trigger-s", type=float, default=TRIGGER_S)
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()
    base = a.base or str(ROOT)
    import fcntl
    lock_path = Path(base) / 'data' / f'.vec_bar_trigger_{a.shard}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    singleton = lock_path.open('a')
    try:
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    if not a.once:
        def heartbeat():
            while True:
                time.sleep(15)
                engine = _PRODUCER_ENGINES.get(a.shard)
                if engine is not None:
                    try:
                        publish_producer(base, a.shard, engine)
                    except Exception:
                        pass
        threading.Thread(target=heartbeat, daemon=True).start()
    while True:
        try:
            rec = run_once(base, a.shard, workers=a.workers, verbose=True)
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
        if a.once:
            return 0
        time.sleep(a.trigger_s)


if __name__ == "__main__":
    sys.exit(main())
