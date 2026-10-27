#!/usr/bin/env python3
"""npz_merged_view — let an unmodified NPZ reader see the zip PLUS its sidecar tail (npz_tail_store), lazily.

2026-10-27 (USER: every traded sym_side's STATE within seconds of each 15m bar close). Measured on S1: the closed bar
reaches the store in ~1-21 s, but npz_tail_service appends it to backtest_v8/indicators/.tail/<SYM>.jsonl and folds the
tail into the zip only at 4 rows (~1 h, tools/npz_tail_compactor.py) or when npz_bar_refresh rewrites the zip. The
forward-state compute (tools/v15_forward_state.py -> tools/opt/v12_pilot -> v12_quick_engine.load_npz) reads the zip
only, so the bar that had already landed was invisible for minutes to an hour: states stayed
`npz_tail_behind_last_closed_bar` / `prepare failed/stale`. Compacting per bar costs a full zip rewrite (2.6 s ZENUSDT,
34.8 s RUNEUSDT measured), so instead this context manager makes numpy.load return, for the named NPZ paths only, a
read-only view whose 1-D row arrays carry the tail rows appended exactly as npz_tail_store._apply_row_in_memory does
(the function compact_tail itself uses), per key and on demand. The engine then slices, HTF-aligns and evaluates the
same bytes a compacted zip would hold — v12_quick_engine.py is not touched (locked; its AST hash feeds the board feed).

Kill switch: NPZ_MERGED_VIEW=0.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


def _pending_rows(npz_path, base_last):
    from npz_tail_store import tail_rows
    out, last = [], base_last
    for row in tail_rows(npz_path):
        try:
            ts = int(row["timestamps"])
        except Exception:
            continue
        if ts < last or (ts == last and not row.get("_replace_latest")):
            continue
        out.append(row)
        last = ts
    return out


class _MergedNpz:
    """Mapping view over a real NpzFile: same .files, row arrays extended by the tail rows."""

    def __init__(self, real, rows):
        self._real, self._rows = real, rows
        self.files = real.files
        self._ts = None

    def _base_ts(self):
        if self._ts is None:
            self._ts = self._real["timestamps"]
        return self._ts

    def __getitem__(self, key):
        import numpy as np
        from npz_tail_store import _apply_row_in_memory
        arr = self._real[key]
        ts = self._base_ts()
        if getattr(arr, "ndim", 0) != 1 or len(arr) != len(ts):
            return arr
        data = {"timestamps": np.asarray(ts)} if key == "timestamps" else {"timestamps": np.asarray(ts), key: arr}
        for row in self._rows:
            merged = _apply_row_in_memory(data, row)
            if merged is None:
                raise ValueError(f"tail row {row.get('timestamps')} misaligned for {key}")
            data = merged
        return data[key]

    def __contains__(self, key):
        return key in self.files

    def keys(self):
        return list(self.files)

    def get(self, key, default=None):
        return self[key] if key in self.files else default

    def close(self):
        self._real.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def merged_tip(npz_path):
    """max(zip last ts, sidecar last ts) without loading the store."""
    import numpy as np
    from npz_tail_store import tail_path_for_npz, _tail_tip_ts
    base = None
    try:
        with np.load(str(npz_path), allow_pickle=True) as z:
            ts = z["timestamps"]
            base = int(ts[-1]) if len(ts) else None
    except Exception:
        pass
    tail = _tail_tip_ts(tail_path_for_npz(npz_path))
    vals = [v for v in (base, tail) if v]
    return max(vals) if vals else None


@contextmanager
def merged_npz_reads(paths):
    """Within the block, numpy.load(<one of paths>) returns the merged view when the sidecar holds newer rows."""
    import numpy as np
    if os.environ.get("NPZ_MERGED_VIEW", "1") == "0":
        yield
        return
    targets = {str(Path(p).resolve()) for p in paths if p and Path(p).exists()}
    real_load = np.load

    def load(file, *args, **kwargs):
        try:
            key = str(Path(os.fspath(file)).resolve())
        except TypeError:
            key = None
        if key not in targets:
            return real_load(file, *args, **kwargs)
        kwargs.pop("mmap_mode", None)
        if args:
            args = ()
        z = real_load(file, allow_pickle=kwargs.pop("allow_pickle", True), **kwargs)
        try:
            ts = z["timestamps"]
            rows = _pending_rows(key, int(ts[-1])) if len(ts) else []
        except Exception:
            rows = []
        return _MergedNpz(z, rows) if rows else z

    np.load = load
    try:
        yield
    finally:
        np.load = real_load
