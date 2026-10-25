#!/usr/bin/env python3
"""npz_tail_store.py — millisecond sidecar tail for 15m NPZ bars, no zip rewrite.

The live path in npz_live_generator._append_row_to_npz rewrites the whole zip
(load + savez_compressed + replace) per bar — O(rows) and seconds on large
stores. This module appends ONE closed 15m bar per symbol in milliseconds by
writing the row to a per-symbol sidecar JSONL tail next to the NPZ:

    backtest_v8/indicators/.tail/<SYMBOL>.jsonl

The .npz file itself is NEVER touched by an append (mtime + content hash are
invariant — pinned by tests/test_npz_tail_store.py). Readers that need the
full series call load_merged(), which loads the base NPZ once and replays the
tail rows in memory with the same carry-forward/label-encode semantics as
_append_row_to_npz. compact_tail() folds the tail into the zip in one rewrite
(operational rollup, not part of the hot path).

Crash safety: each append is a single O_APPEND write() + fsync of one line.
A torn final line is skipped by the reader, so a crash can lose at most the
in-flight bar, never corrupt the store.

Forming-bar guard: a row whose ts is not yet a closed 15m bar (ts >
last 15m close incl. 30 s kline settle, same rule as _last_15m_close_utc) is
REFUSED and nothing is persisted — provisional values must never land in the
store (npz_live_generator._drop_forming_bar doctrine). Duplicate/stale rows
(ts <= store tip) are refused idempotently.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("npz_tail_store")
TAIL_DIRNAME = ".tail"
FORMING_SETTLE_S = 30
GRID_S = 900


def last_closed_15m_epoch(now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    effective = now - timedelta(seconds=FORMING_SETTLE_S)
    floored_minute = (effective.minute // 15) * 15
    return int(
        effective.replace(minute=floored_minute, second=0, microsecond=0).timestamp()
    )


def is_closed_bar(ts_epoch, now=None):
    try:
        return int(ts_epoch) <= last_closed_15m_epoch(now)
    except Exception:
        return False


def tail_path_for_npz(npz_path):
    npz_path = Path(npz_path)
    return npz_path.parent / TAIL_DIRNAME / (npz_path.stem + ".jsonl")


def tail_path_for_symbol(base_path, symbol):
    from npz_live_generator import _npz_path as _resolve_npz

    return tail_path_for_npz(_resolve_npz(Path(base_path), symbol))


def _json_safe(value):
    try:
        import numpy as _np

        if isinstance(value, (_np.integer,)):
            return int(value)
        if isinstance(value, (_np.floating,)):
            return float(value)
        if isinstance(value, (_np.ndarray,)):
            return value.tolist()
    except ImportError:
        pass
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return 0.0
        return value
    return value


def _tail_tip_ts(tail_path):
    try:
        with open(tail_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return None
            step = min(size, 4096)
            fh.seek(-step, os.SEEK_END)
            chunk = fh.read().decode("utf-8", errors="replace")
        for line in reversed(chunk.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return int(json.loads(line).get("timestamps", 0)) or None
            except Exception:
                continue
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _base_tip_ts(npz_path):
    try:
        import numpy as _np

        with _np.load(str(npz_path), allow_pickle=True) as z:
            if "timestamps" not in z.files or len(z["timestamps"]) == 0:
                return None
            return int(_np.asarray(z["timestamps"]).astype("int64").reshape(-1)[-1])
    except Exception:
        return None


def append_tail_row(npz_path, row, now=None):
    npz_path = Path(npz_path)
    tail_path = tail_path_for_npz(npz_path)
    try:
        ts_epoch = int(row["timestamps"])
    except Exception:
        logger.warning("TAIL REFUSED %s: row has no int timestamps", npz_path.name)
        return False
    if not is_closed_bar(ts_epoch, now):
        logger.warning(
            "TAIL REFUSED %s ts=%d: forming bar (not yet closed)",
            npz_path.name,
            ts_epoch,
        )
        return False
    tip = _tail_tip_ts(tail_path)
    if tip is None:
        tip = _base_tip_ts(npz_path)
    if tip is not None and ts_epoch <= tip:
        return False
    try:
        payload = {str(k): _json_safe(v) for k, v in dict(row).items()}
        payload["timestamps"] = ts_epoch
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    except Exception as e:
        logger.warning(
            "TAIL REFUSED %s ts=%d: unserialisable (%s)",
            npz_path.name,
            ts_epoch,
            type(e).__name__,
        )
        return False
    try:
        tail_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(tail_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except Exception as e:
        logger.warning(
            "TAIL APPEND FAILED %s ts=%d: %s: %s",
            npz_path.name,
            ts_epoch,
            type(e).__name__,
            e,
        )
        return False


def tail_rows(npz_path):
    tail_path = tail_path_for_npz(npz_path)
    rows = []
    try:
        with open(tail_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    decoded = json.loads(line)
                except Exception:
                    continue
                if isinstance(decoded, dict) and "timestamps" in decoded:
                    try:
                        decoded["timestamps"] = int(decoded["timestamps"])
                    except Exception:
                        continue
                    rows.append(decoded)
    except FileNotFoundError:
        return []
    rows.sort(key=lambda r: r["timestamps"])
    return rows


def tail_len(npz_path):
    return len(tail_rows(npz_path))


def _apply_row_in_memory(data, row):
    import numpy as _np

    try:
        from npz_live_generator import _encode_row_label as _encode
    except Exception:
        _encode = None
    new_ts = int(row["timestamps"])
    n_old = len(data["timestamps"])
    out = {}
    for key in list(data.keys()):
        arr = data[key]
        if getattr(arr, "ndim", 0) != 1 or len(arr) != n_old:
            out[key] = arr
            continue
        if key in row:
            value = row[key]
        elif key.startswith("timestamp_") and key[len("timestamp_") :] in (
            "15m",
            "5m",
            "3m",
            "1m",
        ):
            value = new_ts
        else:
            value = arr[-1] if n_old else 0
        try:
            out[key] = (
                _np.append(arr, _np.array([value], dtype=object))
                if arr.dtype == object
                else _np.concatenate([arr, _np.array([value], dtype=arr.dtype)])
            )
        except Exception:
            enc = _encode(key, value) if (_encode and isinstance(value, str)) else None
            value = (
                arr[-1] if (enc is None and n_old) else (enc if enc is not None else 0)
            )
            try:
                out[key] = (
                    _np.append(arr, _np.array([value], dtype=object))
                    if arr.dtype == object
                    else _np.concatenate([arr, _np.array([value], dtype=arr.dtype)])
                )
            except Exception:
                return None
    bad = [
        k
        for k, a in out.items()
        if getattr(a, "ndim", 0) == 1 and len(data[k]) == n_old and len(a) != n_old + 1
    ]
    if bad:
        return None
    return out


def load_merged(npz_path):
    npz_path = Path(npz_path)
    try:
        import numpy as _np

        with _np.load(str(npz_path), allow_pickle=True) as z:
            data = {k: z[k] for k in z.files}
    except Exception as e:
        logger.warning("TAIL MERGE load failed %s: %s", npz_path.name, e)
        return None
    if "timestamps" not in data or len(data["timestamps"]) == 0:
        return data
    for row in tail_rows(npz_path):
        try:
            if int(row["timestamps"]) <= int(data["timestamps"][-1]):
                continue
        except Exception:
            continue
        merged = _apply_row_in_memory(data, row)
        if merged is None:
            logger.warning(
                "TAIL MERGE skipped ts=%s on %s (misaligned)",
                row.get("timestamps"),
                npz_path.name,
            )
            continue
        data = merged
    return data


def compact_tail(npz_path):
    npz_path = Path(npz_path)
    rows = tail_rows(npz_path)
    if not rows:
        return 0
    from npz_live_generator import _load_npz as _load, _save_npz_atomic as _save

    tail_path = tail_path_for_npz(npz_path)
    try:
        sealed_size = tail_path.stat().st_size
    except Exception:
        sealed_size = -1
    data = _load(npz_path)
    if data is None:
        return 0
    applied = 0
    for row in rows:
        try:
            if int(row["timestamps"]) <= int(data["timestamps"][-1]):
                applied += 1
                continue
        except Exception:
            continue
        merged = _apply_row_in_memory(data, row)
        if merged is None:
            return applied
        data = merged
        applied += 1
    _save(npz_path, data)
    try:
        if tail_path.stat().st_size != sealed_size:
            return applied
    except Exception:
        pass
    try:
        drained = tail_path.with_name(tail_path.name + ".drained")
        os.replace(str(tail_path), str(drained))
        try:
            os.remove(str(drained))
        except Exception:
            pass
    except FileNotFoundError:
        pass
    return applied
