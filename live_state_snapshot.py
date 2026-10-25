#!/usr/bin/env python3
"""live_state_snapshot.py — per sym_side open/closed state snapshots, ms reads.

Trading scripts need the latest per-sym_side position state (open qty, entry,
closed pnl, bar ts) in milliseconds without importing engines. The producer
(the live loop, at the same instant it calculates a row) calls write_snapshot()
or append_bar_with_snapshot(); the consumer calls read_snapshot().

Storage: <BASE_PATH>/data/live_state/<SYM_SIDE>.json, one small file per
sym_side. Writes are crash-safe atomic (tmp + fsync + os.replace). Reads are
one small JSON parse — stdlib only on the hot path, no engine imports.

BASE_PATH resolves from config.BASE_PATH (never hardcoded); an explicit
base_path argument always wins (tests pass tmp dirs).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("live_state_snapshot")
SNAPSHOT_SUBDIR = Path("data") / "live_state"


def default_base_path():
    env_base = os.environ.get("BASE_PATH")
    if env_base:
        return Path(env_base)
    try:
        from config import Config as _Cfg

        return Path(_Cfg.BASE_PATH)
    except Exception:
        return Path.cwd()


def snapshot_dir(base_path=None):
    base = default_base_path() if base_path is None else Path(base_path)
    return base / SNAPSHOT_SUBDIR


def _safe_name(sym_side):
    name = str(sym_side).strip().upper()
    if not name or name in (".", ".."):
        raise ValueError(f"bad sym_side: {sym_side!r}")
    if not all(c.isalnum() or c in ("_", "-", ".") for c in name):
        raise ValueError(f"bad sym_side: {sym_side!r}")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"bad sym_side: {sym_side!r}")
    return name


def snapshot_path(base_path, sym_side):
    base = Path(base_path) if base_path is not None else default_base_path()
    return base / SNAPSHOT_SUBDIR / (_safe_name(sym_side) + ".json")


def write_snapshot(base_path, sym_side, state, bar_ts=None):
    dest = snapshot_path(base_path, sym_side)
    payload = {
        "sym_side": _safe_name(sym_side),
        "bar_ts": int(bar_ts) if bar_ts is not None else None,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "state": dict(state),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(dest))
    return dest


def read_snapshot(base_path, sym_side):
    dest = snapshot_path(base_path, sym_side)
    try:
        with open(dest, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict) or "state" not in payload:
        return None
    return payload


def append_bar_with_snapshot(npz_path, row, base_path, sym_side, state):
    from npz_tail_store import append_tail_row as _append

    tail_ok = _append(npz_path, row)
    snap_path = None
    try:
        snap_path = write_snapshot(
            base_path, sym_side, state, bar_ts=int(row["timestamps"])
        )
    except Exception as e:
        logger.warning(
            "SNAPSHOT write failed %s: %s: %s", sym_side, type(e).__name__, e
        )
    return tail_ok, snap_path


def snapshot_age_s(base_path, sym_side, now=None):
    payload = read_snapshot(base_path, sym_side)
    if payload is None:
        return None
    try:
        written = datetime.fromisoformat(str(payload["written_at"]))
        ref = now if now is not None else datetime.now(timezone.utc)
        return (ref - written).total_seconds()
    except Exception:
        return None
