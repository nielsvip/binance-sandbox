#!/usr/bin/env python3
"""Create an isolated RTH-only 7D stock NPZ plus today's latest four premarket rows."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")


def build_overlay(symbol: str, overlay_dir: Path, now: dt.datetime | None = None) -> dict:
    from tools.npz_cutoffs import is_premarket_bar_start, is_rth_bar_start, last_premarket_bar

    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    source = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
    if not source.is_file():
        raise FileNotFoundError(f"canonical NPZ missing: {source}")
    with np.load(source, allow_pickle=True) as archive:
        arrays = {key: archive[key] for key in archive.files}
    ts = np.asarray(arrays.get("timestamps", []), dtype=np.int64).reshape(-1)
    if not len(ts):
        raise ValueError(f"{symbol}: NPZ has no timestamps")
    scale = 1000 if int(ts[-1]) > 10**11 else 1
    ts_s = ts // scale
    local_day = now.astimezone(NY).date()
    pm_cutoff = int(last_premarket_bar(now))
    import npz_live_generator as live_npz

    kline_path = live_npz._resolve_kline_path(symbol, "15m", "tradier", ROOT)
    if kline_path is None:
        raise FileNotFoundError(f"{symbol}: current Tradier 15m source is missing")
    kline_df = live_npz._load_klines_df(kline_path)
    kline_df = live_npz._drop_forming_bar(kline_df, "15m")
    if kline_df is None or kline_df.empty:
        raise ValueError(f"{symbol}: no completed Tradier 15m bars")
    pm_candidates = []
    for stamp in kline_df.index:
        epoch = int(stamp.timestamp())
        if (is_premarket_bar_start(epoch)
                and stamp.tz_convert(NY).date() == local_day
                and epoch <= pm_cutoff):
            pm_candidates.append(epoch)
    pm_keep = set(pm_candidates[-4:])
    if len(pm_keep) < 4:
        raise ValueError(f"{symbol}: need four completed current-day premarket NPZ rows; found {len(pm_keep)}")
    end = max(int(ts_s[-1]), max(pm_keep))
    start = end - 7 * 86400
    keep = np.asarray([
        (int(value) >= start and int(value) <= end and
         (is_rth_bar_start(int(value)) or int(value) in pm_keep))
        for value in ts_s
    ], dtype=bool)
    if int(keep.sum()) < 10:
        raise ValueError(f"{symbol}: temporary 7D NPZ has only {int(keep.sum())} rows")
    out_arrays = {}
    for key, value in arrays.items():
        if isinstance(value, np.ndarray) and value.ndim > 0 and len(value) == len(ts):
            out_arrays[key] = value[keep]
        else:
            out_arrays[key] = value
    target = overlay_dir / f"{symbol}.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{__import__('os').getpid()}.tmp.npz")
    np.savez_compressed(tmp, **out_arrays)
    tmp.replace(target)
    manager = SimpleNamespace(base_path=ROOT)
    for epoch in sorted(pm_keep):
        row = live_npz._compute_row_for_15m_close(
            symbol, dt.datetime.fromtimestamp(epoch, dt.timezone.utc), manager, None, "tradier")
        if not row or not live_npz._append_row_to_npz(target, row):
            raise ValueError(f"{symbol}: failed to append temporary premarket row {epoch}")
    with np.load(target, allow_pickle=True) as result:
        out_ts = np.asarray(result["timestamps"], dtype=np.int64)
    out_ts_s = out_ts // (1000 if int(out_ts[-1]) > 10**11 else 1)
    if not all(int(epoch) in set(map(int, out_ts_s)) for epoch in pm_keep):
        raise ValueError(f"{symbol}: temporary premarket rows failed timestamp verification")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "symbol": symbol,
        "source_npz": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "overlay_npz": str(target),
        "overlay_sha256": digest,
        "created_utc": now.astimezone(dt.timezone.utc).isoformat(),
        "window_start_utc": dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(),
        "window_end_utc": dt.datetime.fromtimestamp(end, dt.timezone.utc).isoformat(),
        "premarket_rows": sorted(pm_keep),
        "premarket_row_count": len(pm_keep),
        "rth_row_count": int(sum(is_rth_bar_start(int(value)) for value in out_ts_s)),
        "total_rows": int(len(out_ts_s)),
        "trade_mask": "stocks_live_session_gates.rth_mask",
    }
    (overlay_dir / f"{symbol}.manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return manifest
