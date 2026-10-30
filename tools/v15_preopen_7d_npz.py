#!/usr/bin/env python3
"""Create an isolated RTH-only 7D stock NPZ plus today's completed 08:45/09:00/09:15 ET premarket rows (temporary)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")


def build_overlay(symbol: str, overlay_dir: Path, now: dt.datetime | None = None) -> dict:
    from tools.npz_cutoffs import is_rth_bar_start

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
    import npz_live_generator as live_npz
    from tools.npz_premarket_temp_bars import fetch_premarket, private_klines, temp_epochs, tradier_key

    # USER 2026-10-29: exactly the 08:45 / 09:00 / 09:15 ET bars (bar-open stamps), each only once it has closed: a 09:00
    # launch carries 08:45, a 09:15 launch 08:45 + 09:00; never earlier premarket bars.
    # 2026-10-30: the host Tradier klines receive today's premarket bars only the next day (klines_canonical stocks-fetch
    # takes completed sessions), so they are fetched from Tradier timesales here, the same way the live temp rows are
    # (tools/npz_premarket_temp_bars.py); a launch right at a bar close waits up to 90 s for Tradier to serve that bar.
    wanted = set(temp_epochs(local_day))
    closed = sorted(epoch for epoch in wanted if epoch + 900 <= now.timestamp())
    if not closed:
        raise ValueError(f"{symbol}: no 08:45/09:00/09:15 ET premarket bar has closed yet")
    key = tradier_key(ROOT)
    if not key:
        raise ValueError(f"{symbol}: no Tradier market-data key for the premarket bars")
    live = abs(time.time() - now.timestamp()) < 600  # a past `now` (replay/test) never waits
    deadline = time.time() + (90 if live else 0)
    while True:
        bars, why = fetch_premarket(key, symbol, local_day, set(closed), time.time() if live else now.timestamp())
        if bars is None:
            raise ValueError(f"{symbol}: premarket fetch failed: {why}")
        if closed[-1] in bars or time.time() >= deadline:
            break
        time.sleep(10)
    pm_keep = set(bars)
    if not pm_keep:
        raise ValueError(f"{symbol}: Tradier returned no completed 08:45/09:00/09:15 ET premarket bar")
    kline_base = private_klines(ROOT, overlay_dir / "klines", symbol, bars)
    rth_days = []
    for value in ts_s:
        if is_rth_bar_start(int(value)):
            day = dt.datetime.fromtimestamp(int(value), dt.timezone.utc).astimezone(NY).date()
            if day not in rth_days:
                rth_days.append(day)
    selected_days = set(rth_days[-7:])
    if len(selected_days) < 7:
        raise ValueError(f"{symbol}: canonical NPZ has only {len(selected_days)} RTH sessions; 7D requires seven")
    rth_keep = np.asarray([
        is_rth_bar_start(int(value)) and
        dt.datetime.fromtimestamp(int(value), dt.timezone.utc).astimezone(NY).date() in selected_days
        for value in ts_s
    ], dtype=bool)
    end = max(int(ts_s[np.flatnonzero(rth_keep)[-1]]), max(pm_keep))
    start = int(ts_s[np.flatnonzero(rth_keep)[0]])
    keep = rth_keep  # the live store's own temporary premarket rows (npz_premarket_temp_bars) are never copied; they are recomputed below
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
    manager = SimpleNamespace(base_path=kline_base)  # host klines for 5m/1h/4h/D, private 15m view with today's premarket bars
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
        "rth_session_dates": [day.isoformat() for day in rth_days[-7:]],
        "rth_row_count": int(sum(is_rth_bar_start(int(value)) for value in out_ts_s)),
        "total_rows": int(len(out_ts_s)),
        "trade_mask": "stocks_live_session_gates.rth_mask",
    }
    (overlay_dir / f"{symbol}.manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return manifest


def install_overlay_for_process():
    """Make the existing v12 loader and 7D freshness check consume this process's overlay."""
    import os

    overlay_dir = os.environ.get("V12_NPZ_DIR", "")
    if not overlay_dir:
        return None
    import v12_quick_engine as engine

    original_load = engine.load_npz

    def load_overlay_npz(mode, symbols, start_date, npz_dir=""):
        return original_load(mode, symbols, start_date, npz_dir or overlay_dir)

    engine.load_npz = load_overlay_npz
    from tools.opt import v12_pilot as pilot
    import tools.v15_row_guards as row_guards

    def overlay_identity(symside, indicators_dir=None, root=None):
        from tools.opt.evaluate_v12 import resolve_tokenised

        symbol, _ = resolve_tokenised(symside.rsplit("_", 1)[0])
        path = Path(overlay_dir) / f"{symbol}.npz"
        if not path.is_file():
            return None
        stat = path.stat()
        try:
            with np.load(path, mmap_mode="r", allow_pickle=True) as archive:
                raw = np.asarray(archive["timestamps"], dtype=np.float64)
            finite = raw[np.isfinite(raw) & (raw > 0)]
            first = float(finite[0]) if finite.size else None
            last = float(finite[-1]) if finite.size else None
            count = int(raw.size)
        except Exception:
            first, last, count = None, None, None
        return {"path": str(path), "mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size),
                "n": count, "ts_first": first, "ts_last": last,
                "md5": hashlib.md5(path.read_bytes()).hexdigest()}

    row_guards.npz_identity_for_symside = overlay_identity

    def overlay_freshness(symside: str):
        from tools.opt.evaluate_v12 import is_crypto, resolve_tokenised
        from tools.npz_cutoffs import MAX_NPZ_AGE_MIN, last_closed_bar, last_stock_data_bar

        symbol, _ = resolve_tokenised(symside.rsplit("_", 1)[0])
        mode = "crypto" if is_crypto(symside.rsplit("_", 1)[0]) else "tradier"
        path = Path(overlay_dir) / f"{symbol}.npz"
        if not path.is_file():
            return False, f"7D overlay missing: {path}"
        try:
            with np.load(path, mmap_mode="r", allow_pickle=True) as archive:
                last = int(np.asarray(archive["timestamps"], dtype=np.int64)[-1])
            last = last // 1000 if last > 10**11 else last
            cutoff = int(last_closed_bar()) if mode == "crypto" else int(last_stock_data_bar())
            age = max(0.0, (cutoff - last) / 60.0)
            if age > MAX_NPZ_AGE_MIN:
                return False, f"STALE_7D_OVERLAY: {symbol} is {age:.1f} minutes behind current input cutoff"
            return True, ""
        except Exception as exc:
            return False, f"INVALID_7D_OVERLAY: {type(exc).__name__}: {exc}"

    pilot.npz_freshness = overlay_freshness
    return overlay_freshness
