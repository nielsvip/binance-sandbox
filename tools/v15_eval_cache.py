#!/usr/bin/env python3
"""v15_eval_cache — persistent cross-run memoization for evaluate_prepared (USER 2026-10-09).

No non-yellow per-switch filter eval may be recalculated on every run: identical inputs MUST hit
disk cache instead of burning 0.07s (30D) to seconds (365D) re-measuring the same cell in repair
rounds, GS, resume boards and fresh campaigns.

NO-LIES design (a wrong cache hit = fabricated delta = live-money bug):
- Key = sha1 of EVERYTHING that determines the number: sliced-NPZ array bytes, symside, window,
  offset, sim account, mode, full materialized base_cfg_dict, canonical overrides, include_ledger,
  and the md5 of every engine file (v12 + evaluate_v12 + v12_pilot + lifecycle_pilot + vec_decisions/*).
  Any byte change anywhere = different key = fresh eval. No TTL games, no fuzzy match.
- include_ledger=True calls are NEVER cached (nested ledger rows must keep numeric types; JSON
  round-trip would stringify them).
- V15_EVAL_CACHE_VERIFY (default 0.01): 1% of hits are re-evaluated fresh and compared EXACTLY on
  (gain_pct, trades, tim_pct, max_dd_pct, pool_sharpe, valid). Mismatch -> row deleted, warning
  logged, fresh result returned. Determinism is self-proving in production.
- Fail-open everywhere: any DB/cache error -> miss (fresh eval). V15_EVAL_CACHE=0 disables all.
- Per-host SQLite (data/eval_cache_v1.db, WAL, busy_timeout 30s). No network, no sharing.
"""
import hashlib
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("V15_EVAL_CACHE_DB") or (ROOT / "data" / "eval_cache_v1.db"))
MAX_ROWS = int(os.environ.get("V15_EVAL_CACHE_MAX_ROWS", "2000000"))

_stats = {"hits": 0, "miss": 0, "stored": 0, "verify_ok": 0, "verify_bad": 0, "errors": 0}
_ver_cache: dict = {}
_conn = None


def enabled() -> bool:
    return os.environ.get("V15_EVAL_CACHE", "1") != "0"


def _conn():
    global _conn
    if _conn is not None:
        return _conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("CREATE TABLE IF NOT EXISTS evals (key TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL, hits INT DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    _conn = c
    return _conn


def engine_versions() -> str:
    """md5 of every file whose bytes can change a number. Memoized by mtime (a mid-run
    deploy changes mtimes -> new version string -> old rows orphaned, never mis-hit)."""
    try:
        files = [ROOT / "v12_quick_engine.py", ROOT / "tools" / "opt" / "evaluate_v12.py",
                 ROOT / "tools" / "opt" / "v12_pilot.py", ROOT / "tools" / "opt" / "lifecycle_pilot.py"]
        vd = ROOT / "vec_decisions"
        if vd.is_dir():
            files += sorted(vd.glob("*.py"))
        sig = []
        for f in files:
            try:
                sig.append(f"{f.name}:{f.stat().st_mtime_ns}")
            except Exception:
                sig.append(f"{f.name}:?")
        memo_key = "|".join(sig)
        if _ver_cache.get("memo") == memo_key:
            return _ver_cache["ver"]
        h = hashlib.sha1()
        for f in files:
            try:
                h.update(f.name.encode())
                h.update(f.read_bytes())
            except Exception:
                h.update(b"?")
        ver = h.hexdigest()[:24]
        _ver_cache["memo"] = memo_key
        _ver_cache["ver"] = ver
        return ver
    except Exception:
        return "unknown"


def npz_fingerprint(npz_prepared) -> str:
    """sha1 over the sliced+compacted arrays actually simulated (bytes AND slicing covered)."""
    h = hashlib.sha1()
    try:
        if isinstance(npz_prepared, dict):
            for k in sorted(npz_prepared.keys()):
                v = npz_prepared[k]
                h.update(str(k).encode())
                try:
                    import numpy as _np
                    if isinstance(v, _np.ndarray):
                        h.update(str(v.shape).encode())
                        h.update(str(v.dtype).encode())
                        h.update(v.tobytes())
                        continue
                except Exception:
                    pass
                h.update(repr(v)[:4000].encode())
        else:
            h.update(repr(npz_prepared)[:8000].encode())
    except Exception as e:
        h.update(f"ERR:{e}".encode())
    return h.hexdigest()[:32]


def _canon(obj) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(f"{json.dumps(str(k))}:{_canon(obj[k])}" for k in sorted(obj.keys(), key=str)) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canon(x) for x in obj) + "]"
    if isinstance(obj, float):
        return repr(obj)
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if obj is None:
        return "null"
    try:
        import numpy as _np
        if isinstance(obj, (_np.floating,)):
            return repr(float(obj))
        if isinstance(obj, (_np.integer,)):
            return str(int(obj))
        if isinstance(obj, (_np.bool_,)):
            return "true" if bool(obj) else "false"
        if isinstance(obj, (_np.ndarray,)):
            return "arr:" + hashlib.sha1(obj.tobytes()).hexdigest()[:16]
    except Exception:
        pass
    return json.dumps(obj, sort_keys=True, default=repr)


def cache_key(prepared: dict, overrides: dict, include_ledger: bool) -> str:
    h = hashlib.sha1()
    h.update(b"v1|")
    h.update(str(prepared.get("_eval_cache_fp") or npz_fingerprint(prepared.get("npz_prepared"))).encode())
    for k in ("symside", "window_days", "offset_days", "sim_account", "mode", "tokenised"):
        h.update(f"|{k}={prepared.get(k)}".encode())
    h.update(b"|base=")
    h.update(_canon(prepared.get("base_cfg_dict") or {}).encode())
    h.update(b"|ov=")
    h.update(_canon(dict(overrides or {})).encode())
    h.update(f"|led={bool(include_ledger)}".encode())
    h.update(f"|eng={engine_versions()}".encode())
    return h.hexdigest()


def lookup(key: str):
    try:
        row = _conn().execute("SELECT result FROM evals WHERE key=?", (key,)).fetchone()
        if row is None:
            _stats["miss"] += 1
            return None
        _stats["hits"] += 1
        return json.loads(row[0])
    except Exception:
        _stats["errors"] += 1
        return None


def store(key: str, result: dict) -> bool:
    try:
        import time as _t
        blob = json.dumps(result)
        json.loads(blob)
        c = _conn()
        c.execute("INSERT OR REPLACE INTO evals (key, result, created, hits) VALUES (?, ?, ?, 0)", (key, blob, _t.time()))
        _stats["stored"] += 1
        if random.random() < 0.002:
            try:
                n = c.execute("SELECT COUNT(*) FROM evals").fetchone()[0]
                if n > MAX_ROWS:
                    c.execute("DELETE FROM evals WHERE key IN (SELECT key FROM evals ORDER BY created ASC LIMIT ?)", (int(n - MAX_ROWS + MAX_ROWS // 10),))
            except Exception:
                pass
        return True
    except Exception:
        _stats["errors"] += 1
        return False


def bump_hit(key: str):
    try:
        _conn().execute("UPDATE evals SET hits = hits + 1 WHERE key=?", (key,))
    except Exception:
        pass


def invalidate(key: str):
    try:
        _conn().execute("DELETE FROM evals WHERE key=?", (key,))
    except Exception:
        pass


def stats() -> dict:
    return dict(_stats)


def maybe_log(tag: str):
    total = _stats["hits"] + _stats["miss"]
    if total and total % 10000 == 0:
        print(f"[EVAL-CACHE] {tag} hits={_stats['hits']} miss={_stats['miss']} stored={_stats['stored']} verify_ok={_stats['verify_ok']} verify_bad={_stats['verify_bad']} err={_stats['errors']}", flush=True)
