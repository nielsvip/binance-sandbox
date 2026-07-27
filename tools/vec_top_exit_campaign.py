#!/usr/bin/env python3
"""Isolated vector/compiled campaign for slow exits and stateful re-entry.

Research lane only.  This script does not import the live engine, does not read
or write per-symbol overrides, and never writes the ENGINE switch matrix.

Implemented candidates:
  E01  monotonic slow Chandelier (4h/D)
  E02  opposite Donchian close exit (4h/D)
  E04  EMA structural break -> rebound -> delayed rollover exit (4h)
  E05  confirmed RSI divergence -> structural break -> rebound/rollover (4h)
  E10  mandatory reclaim of stored exit/top level
  E11  lower-price re-entry on completed 1h HH+HL / LH+LL, with E10 fallback

Signals observed at bar close always execute at the next regular-session open.
The expensive rolling features are NumPy arrays; the path-dependent simulator
is compiled from vec_top_exit_scan.c and loaded through ctypes.

Examples on S1:
  python3 tools/vec_top_exit_campaign.py --symbol MU --side LONG
  python3 tools/vec_top_exit_campaign.py --symbol VT --side LONG
  python3 tools/vec_top_exit_campaign.py --self-test
"""
from __future__ import annotations

import argparse
import ctypes
import csv
import dataclasses
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from backtest_data_contract import audit_npz  # noqa: E402


DEFAULT_NPZ = ROOT / "backtest_v8" / "indicators"
DEFAULT_OUT = ROOT / "data" / "reports" / "vec_research"
C_SOURCE = Path(__file__).with_name("vec_top_exit_scan.c")
ET = ZoneInfo("America/New_York")


class ScanMetrics(ctypes.Structure):
    _fields_ = [
        ("final_equity", ctypes.c_double),
        ("max_drawdown_pct", ctypes.c_double),
        ("held_bars", ctypes.c_double),
        ("held_seconds", ctypes.c_double),
        ("saved_price_sum_pct", ctypes.c_double),
        ("saved_price_max_pct", ctypes.c_double),
        ("missed_move_sum_pct", ctypes.c_double),
        ("missed_move_max_pct", ctypes.c_double),
        ("reclaim_overshoot_sum_pct", ctypes.c_double),
        ("reclaim_overshoot_max_pct", ctypes.c_double),
        ("turnover", ctypes.c_double),
        ("round_trips", ctypes.c_int),
        ("technical_exits", ctypes.c_int),
        ("reentries", ctypes.c_int),
        ("reclaim_reentries", ctypes.c_int),
        ("resting_reclaim_reentries", ctypes.c_int),
        ("lower_reentries", ctypes.c_int),
        ("positive_saved_reentries", ctypes.c_int),
        ("flat_episodes", ctypes.c_int),
        ("bars_flat_beyond_reclaim", ctypes.c_int),
        ("rejected_exit_signals", ctypes.c_int),
        ("winning_exits", ctypes.c_int),
        ("losing_exits", ctypes.c_int),
        ("insolvent", ctypes.c_int),
    ]


def _compile_scanner() -> ctypes.CDLL:
    digest = hashlib.sha256(C_SOURCE.read_bytes()).hexdigest()[:16]
    target = Path("/tmp") / f"vec_top_exit_scan_{digest}.so"
    if not target.exists():
        cmd = [
            os.environ.get("CC", "cc"),
            "-O3",
            "-std=c11",
            "-fPIC",
            "-shared",
            str(C_SOURCE),
            "-lm",
            "-o",
            str(target),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    lib = ctypes.CDLL(str(target))
    f64 = np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS")
    u8 = np.ctypeslib.ndpointer(dtype=np.uint8, ndim=1, flags="C_CONTIGUOUS")
    i64 = np.ctypeslib.ndpointer(dtype=np.int64, ndim=1, flags="C_CONTIGUOUS")
    lib.vec_top_exit_scan.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        i64,
        f64,
        f64,
        f64,
        f64,
        f64,
        u8,
        f64,
        f64,
        u8,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.POINTER(ScanMetrics),
    ]
    lib.vec_top_exit_scan.restype = ctypes.c_int
    return lib


def _rth_mask(ts: np.ndarray) -> np.ndarray:
    out = np.zeros(len(ts), dtype=bool)
    for i, value in enumerate(np.asarray(ts, dtype=np.int64)):
        dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone(ET)
        minute = dt.hour * 60 + dt.minute
        out[i] = dt.weekday() < 5 and 9 * 60 + 30 <= minute <= 16 * 60
    return out


def _as_float(z, name: str, fallback: str | None = None) -> np.ndarray:
    key = name if name in z.files else fallback
    if not key or key not in z.files:
        raise KeyError(f"NPZ missing {name}" + (f" (fallback {fallback})" if fallback else ""))
    return np.asarray(z[key], dtype=np.float64)


def _start_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rolling_mean(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < n or n <= 0:
        return out
    cs = np.concatenate(([0.0], np.cumsum(np.nan_to_num(values, nan=0.0))))
    counts = np.concatenate(([0], np.cumsum(np.isfinite(values).astype(np.int64))))
    sums = cs[n:] - cs[:-n]
    cnt = counts[n:] - counts[:-n]
    valid = cnt == n
    target = out[n - 1 :]
    target[valid] = sums[valid] / n
    return out


def _rolling_prior(values: np.ndarray, n: int, mode: str) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) <= n or n <= 0:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values[:-1], n)
    reduced = np.nanmax(windows, axis=1) if mode == "max" else np.nanmin(windows, axis=1)
    out[n:] = reduced
    return out


def _ema(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if not len(values):
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum.reduce((high - low, np.abs(high - prev), np.abs(low - prev)))
    return _rolling_mean(tr, n)


@dataclasses.dataclass
class ExecutionData:
    symbol: str
    path: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    synthetic: np.ndarray
    full_indices: np.ndarray
    z: Any
    contract: dict[str, Any]


@dataclasses.dataclass
class HTFData:
    tf: str
    event_index: np.ndarray
    source_ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    rsi: np.ndarray
    atr: np.ndarray


@dataclasses.dataclass
class ExitCandidate:
    family: str
    label: str
    params: dict[str, Any]
    exit_mode: int
    exit_event: np.ndarray
    raw_stop: np.ndarray
    struct_ref: np.ndarray
    atr_exec: np.ndarray
    min_profit_gate: float = -1.0
    min_mfe_atr_gate: float = -1.0


def _load_execution(
    symbol: str,
    npz_dir: Path,
    start: str,
    profile: str,
    end: str | None = None,
) -> ExecutionData:
    path = npz_dir / f"{symbol}.npz"
    audit = audit_npz(symbol, npz_path=path, profile=profile, start=start)
    contract = dataclasses.asdict(audit)
    z = np.load(path, allow_pickle=False)
    ts_all = np.asarray(z["timestamps"], dtype=np.int64)
    mask = (ts_all >= _start_epoch(start)) & _rth_mask(ts_all)
    if end:
        # End is exclusive, which makes adjacent discovery/validation windows
        # non-overlapping without special timestamp arithmetic.
        mask &= ts_all < _start_epoch(end)
    idx = np.flatnonzero(mask)
    if len(idx) < 200:
        z.close()
        raise ValueError(f"{symbol}: only {len(idx)} RTH rows after {start}")
    synthetic = (
        np.asarray(z["synthetic_5m"], dtype=np.uint8)[idx]
        if "synthetic_5m" in z.files
        else np.zeros(len(idx), dtype=np.uint8)
    )
    data = ExecutionData(
        symbol=symbol,
        path=str(path),
        ts=np.ascontiguousarray(ts_all[idx]),
        open=np.ascontiguousarray(_as_float(z, "open_5m", "open")[idx]),
        high=np.ascontiguousarray(_as_float(z, "high_5m", "high")[idx]),
        low=np.ascontiguousarray(_as_float(z, "low_5m", "low")[idx]),
        close=np.ascontiguousarray(_as_float(z, "close_5m", "close")[idx]),
        synthetic=np.ascontiguousarray(synthetic),
        full_indices=idx,
        z=z,
        contract=contract,
    )
    if profile == "ladder":
        # Ladder research and its exact replay share one causal observation
        # clock.  This is an in-memory view only; the canonical NPZ is never
        # rewritten.
        from research_availability_clock import apply_availability_clock

        apply_availability_clock(data)
    return data


def _compress_htf(data: ExecutionData, tf: str) -> HTFData:
    z = data.z
    idx = data.full_indices
    availability = np.asarray(z[f"timestamp_{tf}"], dtype=np.int64)[idx]
    changed = np.ones(len(availability), dtype=bool)
    changed[1:] = availability[1:] != availability[:-1]
    changed &= availability > 0
    event_index = np.flatnonzero(changed)
    full_event = idx[event_index]
    if len(event_index) < 30:
        raise ValueError(f"{data.symbol}: only {len(event_index)} completed {tf} events")
    o = _as_float(z, f"open_{tf}")[full_event]
    h = _as_float(z, f"high_{tf}")[full_event]
    lo = _as_float(z, f"low_{tf}")[full_event]
    c = _as_float(z, f"close_{tf}")[full_event]
    rsi = (
        _as_float(z, f"rsi_{tf}")[full_event]
        if f"rsi_{tf}" in z.files
        else np.full(len(event_index), np.nan)
    )
    return HTFData(
        tf=tf,
        event_index=np.ascontiguousarray(event_index, dtype=np.int64),
        source_ts=np.ascontiguousarray(availability[event_index], dtype=np.int64),
        open=np.ascontiguousarray(o),
        high=np.ascontiguousarray(h),
        low=np.ascontiguousarray(lo),
        close=np.ascontiguousarray(c),
        rsi=np.ascontiguousarray(rsi),
        atr=np.ascontiguousarray(_atr(h, lo, c, 14)),
    )


def _align_feature(n_exec: int, htf: HTFData, values: np.ndarray) -> np.ndarray:
    out = np.full(n_exec, np.nan, dtype=np.float64)
    slots = np.searchsorted(htf.event_index, np.arange(n_exec), side="right") - 1
    valid = slots >= 0
    out[valid] = values[slots[valid]]
    return np.ascontiguousarray(out)


def _map_events(
    n_exec: int,
    htf: HTFData,
    event: np.ndarray,
    ref: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    out = np.zeros(n_exec, dtype=np.uint8)
    refs = np.full(n_exec, np.nan, dtype=np.float64)
    fired = np.flatnonzero(event)
    if len(fired):
        exec_idx = htf.event_index[fired]
        out[exec_idx] = 1
        if ref is not None:
            refs[exec_idx] = ref[fired]
    return np.ascontiguousarray(out), np.ascontiguousarray(refs)


def _lower_reentry_events(n_exec: int, htf: HTFData, side: int) -> np.ndarray:
    event = np.zeros(len(htf.close), dtype=np.uint8)
    if side > 0:
        event[1:] = (
            (htf.high[1:] > htf.high[:-1])
            & (htf.low[1:] > htf.low[:-1])
            & (htf.close[1:] > htf.open[1:])
        )
    else:
        event[1:] = (
            (htf.high[1:] < htf.high[:-1])
            & (htf.low[1:] < htf.low[:-1])
            & (htf.close[1:] < htf.open[1:])
        )
    mapped, _ = _map_events(n_exec, htf, event)
    return mapped


def _e01_candidates(data: ExecutionData, htfs: dict[str, HTFData], side: int) -> Iterator[ExitCandidate]:
    n_exec = len(data.ts)
    blank_event = np.zeros(n_exec, dtype=np.uint8)
    for tf in ("4h", "D"):
        h = htfs[tf]
        for lookback in (14, 22, 30, 44):
            prior_high = _rolling_prior(h.high, lookback, "max")
            prior_low = _rolling_prior(h.low, lookback, "min")
            atr_prev = np.concatenate(([np.nan], h.atr[:-1]))
            for k in (2.5, 3.0, 3.5, 4.0):
                raw = prior_high - k * atr_prev if side > 0 else prior_low + k * atr_prev
                ref_htf = prior_high if side > 0 else prior_low
                yield ExitCandidate(
                    family="E01_CHANDELIER",
                    label=f"E01_{tf}_N{lookback}_K{k:g}",
                    params={"tf": tf, "n": lookback, "k": k},
                    exit_mode=1,
                    exit_event=blank_event,
                    raw_stop=_align_feature(n_exec, h, raw),
                    struct_ref=_align_feature(n_exec, h, ref_htf),
                    atr_exec=_align_feature(n_exec, h, h.atr),
                )


def _e02_candidates(data: ExecutionData, htfs: dict[str, HTFData], side: int) -> Iterator[ExitCandidate]:
    n_exec = len(data.ts)
    blank_stop = np.full(n_exec, np.nan, dtype=np.float64)
    for tf in ("4h", "D"):
        h = htfs[tf]
        for lookback in (5, 10, 20, 30):
            prior_high = _rolling_prior(h.high, lookback, "max")
            prior_low = _rolling_prior(h.low, lookback, "min")
            event = h.close < prior_low if side > 0 else h.close > prior_high
            ref = prior_high if side > 0 else prior_low
            mapped, refs = _map_events(n_exec, h, event, ref)
            yield ExitCandidate(
                family="E02_DONCHIAN",
                label=f"E02_{tf}_N{lookback}",
                params={"tf": tf, "n": lookback},
                exit_mode=2,
                exit_event=mapped,
                raw_stop=blank_stop,
                struct_ref=refs,
                atr_exec=_align_feature(n_exec, h, h.atr),
            )


def _e04_signal(
    h: HTFData,
    side: int,
    ema_n: int,
    break_buffer: float,
    rebound_atr: float,
    max_wait: int,
) -> tuple[np.ndarray, np.ndarray]:
    event = np.zeros(len(h.close), dtype=np.uint8)
    ref = np.full(len(h.close), np.nan, dtype=np.float64)
    ema = _ema(h.close, ema_n)
    prior_extreme = _rolling_prior(h.high if side > 0 else h.low, ema_n, "max" if side > 0 else "min")
    state = 0
    wait = 0
    old_extreme = math.nan
    post_extreme = math.nan
    rebound_extreme = math.nan
    rebound_seen = False
    for j in range(2, len(h.close)):
        a = h.atr[j - 1]
        if not (math.isfinite(a) and a > 0 and math.isfinite(ema[j - 1])):
            continue
        if state == 0:
            if side > 0:
                broke = (
                    h.close[j] < ema[j - 1] - break_buffer * a
                    and h.close[j - 1] >= ema[j - 2] - break_buffer * h.atr[j - 2]
                )
            else:
                broke = (
                    h.close[j] > ema[j - 1] + break_buffer * a
                    and h.close[j - 1] <= ema[j - 2] + break_buffer * h.atr[j - 2]
                )
            if broke and math.isfinite(prior_extreme[j]):
                state = 1
                wait = 0
                old_extreme = prior_extreme[j]
                post_extreme = h.low[j] if side > 0 else h.high[j]
                rebound_extreme = h.high[j] if side > 0 else h.low[j]
                rebound_seen = False
            continue

        wait += 1
        if side > 0:
            post_extreme = min(post_extreme, h.low[j])
            rebound_extreme = max(rebound_extreme, h.high[j])
            if h.high[j] - post_extreme >= rebound_atr * a:
                rebound_seen = True
            invalid = h.close[j] > old_extreme + 0.25 * a
            rollover = rebound_seen and h.high[j] < h.high[j - 1] and h.close[j] < h.low[j - 1]
        else:
            post_extreme = max(post_extreme, h.high[j])
            rebound_extreme = min(rebound_extreme, h.low[j])
            if post_extreme - h.low[j] >= rebound_atr * a:
                rebound_seen = True
            invalid = h.close[j] < old_extreme - 0.25 * a
            rollover = rebound_seen and h.low[j] > h.low[j - 1] and h.close[j] > h.high[j - 1]
        if rollover:
            event[j] = 1
            ref[j] = rebound_extreme
            state = 0
        elif invalid or wait >= max_wait:
            state = 0
    return event, ref


def _e04_candidates(data: ExecutionData, h: HTFData, side: int) -> Iterator[ExitCandidate]:
    n_exec = len(data.ts)
    blank_stop = np.full(n_exec, np.nan, dtype=np.float64)
    for ema_n in (20, 34, 50):
        for break_buffer in (0.0, 0.25, 0.5):
            for rebound_atr in (0.25, 0.5, 1.0):
                for max_wait in (8, 12, 20):
                    event, ref = _e04_signal(
                        h, side, ema_n, break_buffer, rebound_atr, max_wait
                    )
                    mapped, refs = _map_events(n_exec, h, event, ref)
                    yield ExitCandidate(
                        family="E04_BREAK_RETEST",
                        label=(
                            f"E04_4h_EMA{ema_n}_B{break_buffer:g}_"
                            f"R{rebound_atr:g}_W{max_wait}"
                        ),
                        params={
                            "tf": "4h",
                            "ema": ema_n,
                            "break_buffer_atr": break_buffer,
                            "rebound_atr": rebound_atr,
                            "max_wait": max_wait,
                        },
                        exit_mode=2,
                        exit_event=mapped,
                        raw_stop=blank_stop,
                        struct_ref=refs,
                        atr_exec=_align_feature(n_exec, h, h.atr),
                    )


def _confirmed_pivots(values: np.ndarray, side: int, radius: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in range(radius, len(values) - radius):
        window = values[p - radius : p + radius + 1]
        is_pivot = values[p] >= np.max(window) if side > 0 else values[p] <= np.min(window)
        if is_pivot:
            out.append((p + radius, p))
    return out


def _e05_signal(
    h: HTFData,
    side: int,
    radius: int,
    div_min: float,
    break_buffer: float,
    rebound_atr: float,
    max_wait: int,
) -> tuple[np.ndarray, np.ndarray]:
    event = np.zeros(len(h.close), dtype=np.uint8)
    ref = np.full(len(h.close), np.nan, dtype=np.float64)
    pivots = _confirmed_pivots(h.high if side > 0 else h.low, side, radius)
    divergence_at: dict[int, tuple[float, float]] = {}
    previous: tuple[int, int] | None = None
    for confirmed, p in pivots:
        if previous is not None:
            _prev_confirmed, p0 = previous
            if math.isfinite(h.rsi[p0]) and math.isfinite(h.rsi[p]):
                if side > 0:
                    div = h.high[p] > h.high[p0] and h.rsi[p] <= h.rsi[p0] - div_min
                    break_level = float(np.min(h.low[p0 : p + 1]))
                    extreme = float(h.high[p])
                else:
                    div = h.low[p] < h.low[p0] and h.rsi[p] >= h.rsi[p0] + div_min
                    break_level = float(np.max(h.high[p0 : p + 1]))
                    extreme = float(h.low[p])
                if div:
                    divergence_at[confirmed] = (break_level, extreme)
        previous = (confirmed, p)

    state = 0  # 0 none, 1 divergence armed, 2 structural break/retest
    break_level = math.nan
    div_extreme = math.nan
    post_extreme = math.nan
    rebound_extreme = math.nan
    rebound_seen = False
    wait = 0
    for j in range(2, len(h.close)):
        if j in divergence_at:
            break_level, div_extreme = divergence_at[j]
            state = 1
            wait = 0
        a = h.atr[j - 1]
        if state == 0 or not (math.isfinite(a) and a > 0):
            continue
        if state == 1:
            broke = (
                h.close[j] < break_level - break_buffer * a
                if side > 0
                else h.close[j] > break_level + break_buffer * a
            )
            invalid = (
                h.close[j] > div_extreme + 0.25 * a
                if side > 0
                else h.close[j] < div_extreme - 0.25 * a
            )
            if broke:
                state = 2
                wait = 0
                post_extreme = h.low[j] if side > 0 else h.high[j]
                rebound_extreme = h.high[j] if side > 0 else h.low[j]
                rebound_seen = False
            elif invalid:
                state = 0
            continue

        wait += 1
        if side > 0:
            post_extreme = min(post_extreme, h.low[j])
            rebound_extreme = max(rebound_extreme, h.high[j])
            rebound_seen |= h.high[j] - post_extreme >= rebound_atr * a
            rollover = rebound_seen and h.high[j] < h.high[j - 1] and h.close[j] < h.low[j - 1]
            invalid = h.close[j] > div_extreme + 0.25 * a
        else:
            post_extreme = max(post_extreme, h.high[j])
            rebound_extreme = min(rebound_extreme, h.low[j])
            rebound_seen |= post_extreme - h.low[j] >= rebound_atr * a
            rollover = rebound_seen and h.low[j] > h.low[j - 1] and h.close[j] > h.high[j - 1]
            invalid = h.close[j] < div_extreme - 0.25 * a
        if rollover:
            event[j] = 1
            ref[j] = rebound_extreme
            state = 0
        elif invalid or wait >= max_wait:
            state = 0
    return event, ref


def _e05_candidates(data: ExecutionData, h: HTFData, side: int) -> Iterator[ExitCandidate]:
    n_exec = len(data.ts)
    blank_stop = np.full(n_exec, np.nan, dtype=np.float64)
    for radius in (2, 3, 5):
        for div_min in (3.0, 5.0, 8.0):
            for break_buffer in (0.0, 0.25):
                for rebound_atr in (0.25, 0.5):
                    for max_wait in (8, 12, 20):
                        event, ref = _e05_signal(
                            h,
                            side,
                            radius,
                            div_min,
                            break_buffer,
                            rebound_atr,
                            max_wait,
                        )
                        mapped, refs = _map_events(n_exec, h, event, ref)
                        yield ExitCandidate(
                            family="E05_DIVERGENCE_RETEST",
                            label=(
                                f"E05_4h_P{radius}_D{div_min:g}_B{break_buffer:g}_"
                                f"R{rebound_atr:g}_W{max_wait}"
                            ),
                            params={
                                "tf": "4h",
                                "pivot_radius": radius,
                                "rsi_div_min": div_min,
                                "break_buffer_atr": break_buffer,
                                "rebound_atr": rebound_atr,
                                "max_wait": max_wait,
                            },
                            exit_mode=2,
                            exit_event=mapped,
                            raw_stop=blank_stop,
                            struct_ref=refs,
                            atr_exec=_align_feature(n_exec, h, h.atr),
                        )


def _reentry_variants(quick: bool = False) -> list[tuple[str, int, float, float]]:
    rows = [("NONE", 0, 0.0, 0.0)]
    for reclaim_buffer in ((0.0, 0.1) if quick else (0.0, 0.1, 0.25)):
        rows.append((f"E10_RB{reclaim_buffer:g}", 1, reclaim_buffer, 0.0))
    gaps = (0.5, 1.0) if quick else (0.5, 1.0, 1.5, 2.0)
    reclaim_buffers = (0.0,) if quick else (0.0, 0.1)
    for gap in gaps:
        for reclaim_buffer in reclaim_buffers:
            rows.append(
                (
                    f"E11_G{gap:g}+E10_RB{reclaim_buffer:g}",
                    2,
                    reclaim_buffer,
                    gap,
                )
            )
    if not quick:
        for gap in (0.5, 1.0, 1.5, 2.0):
            rows.append((f"E11_ONLY_G{gap:g}", 3, 0.0, gap))
    return rows


def _candidate_iter(
    data: ExecutionData,
    htfs: dict[str, HTFData],
    side: int,
    families: set[str],
    quick: bool,
) -> Iterator[ExitCandidate]:
    if "E01" in families:
        yield from _e01_candidates(data, htfs, side)
    if "E02" in families:
        yield from _e02_candidates(data, htfs, side)
    if "E04" in families:
        source = _e04_candidates(data, htfs["4h"], side)
        if quick:
            source = (c for c in source if c.params["ema"] == 34 and c.params["max_wait"] == 12)
        yield from source
    if "E05" in families:
        source = _e05_candidates(data, htfs["4h"], side)
        if quick:
            source = (
                c
                for c in source
                if c.params["pivot_radius"] == 3
                and c.params["break_buffer_atr"] == 0.0
                and c.params["max_wait"] == 12
            )
        yield from source


def _side_bh(
    data: ExecutionData,
    side: int,
    cost_rate: float,
    slip_rate: float,
) -> dict[str, float]:
    entry = data.open[0] * (1.0 + slip_rate if side > 0 else 1.0 - slip_rate)
    exit_ = data.close[-1] * (1.0 - slip_rate if side > 0 else 1.0 + slip_rate)
    raw = side * (data.close[-1] - data.open[0]) / data.open[0]
    gross = 1.0 + side * (exit_ - entry) / entry
    # Match backtest_v8_engine: one round-trip charge at close, based on entry
    # position value.  ``cost_rate`` is the declared one-way rate.
    net_equity = gross - 2.0 * cost_rate
    return {
        "bh_raw_side_pct": 100.0 * raw,
        "bh_net_side_pct": 100.0 * (net_equity - 1.0),
        "bh_long_raw_pct": 100.0 * (data.close[-1] / data.open[0] - 1.0),
    }


def _scan(
    lib: ctypes.CDLL,
    data: ExecutionData,
    candidate: ExitCandidate,
    lower_event: np.ndarray,
    side: int,
    reentry: tuple[str, int, float, float],
    cost_rate: float,
    slip_rate: float,
    bh: dict[str, float],
) -> dict[str, Any]:
    reentry_label, reentry_mode, reclaim_buffer, lower_gap = reentry
    out = ScanMetrics()
    rc = lib.vec_top_exit_scan(
        len(data.ts),
        side,
        candidate.exit_mode,
        reentry_mode,
        data.ts,
        data.open,
        data.high,
        data.low,
        data.close,
        np.ascontiguousarray(candidate.atr_exec, dtype=np.float64),
        np.ascontiguousarray(candidate.exit_event, dtype=np.uint8),
        np.ascontiguousarray(candidate.raw_stop, dtype=np.float64),
        np.ascontiguousarray(candidate.struct_ref, dtype=np.float64),
        lower_event,
        reclaim_buffer,
        lower_gap,
        candidate.min_profit_gate,
        candidate.min_mfe_atr_gate,
        cost_rate,
        slip_rate,
        ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled scan returned {rc}")
    gain = 100.0 * (out.final_equity - 1.0)
    bh_gain = bh["bh_net_side_pct"]
    span = max(1.0, float(data.ts[-1] - data.ts[0]))
    reentries = int(out.reentries)
    episodes = int(out.flat_episodes)
    row = {
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "symbol": data.symbol,
        "side": "LONG" if side > 0 else "SHORT",
        "family": candidate.family,
        "strategy": candidate.label,
        "exit_params": candidate.params,
        "reentry": reentry_label,
        "reentry_mode": reentry_mode,
        "reclaim_buffer_atr": reclaim_buffer,
        "lower_gap_atr": lower_gap,
        "gain_pct": gain,
        **bh,
        "strategy_bh_multiple": gain / bh_gain if abs(bh_gain) > 1e-12 else None,
        "alpha_vs_bh_pp": gain - bh_gain,
        "tim_rth_pct": 100.0 * out.held_bars / len(data.ts),
        "tim_calendar_pct": 100.0 * out.held_seconds / span,
        "round_trips": int(out.round_trips),
        "technical_exits": int(out.technical_exits),
        "reentries": reentries,
        "reclaim_reentries": int(out.reclaim_reentries),
        "lower_reentries": int(out.lower_reentries),
        "positive_saved_reentries": int(out.positive_saved_reentries),
        "positive_saved_reentry_pct": (
            100.0 * out.positive_saved_reentries / reentries if reentries else 0.0
        ),
        "mean_saved_price_pct": out.saved_price_sum_pct / reentries if reentries else 0.0,
        "max_saved_price_pct": out.saved_price_max_pct,
        "mean_missed_move_pct": out.missed_move_sum_pct / episodes if episodes else 0.0,
        "max_missed_move_pct": out.missed_move_max_pct,
        "mean_reclaim_overshoot_pct": (
            out.reclaim_overshoot_sum_pct / out.reclaim_reentries
            if out.reclaim_reentries
            else 0.0
        ),
        "max_reclaim_overshoot_pct": out.reclaim_overshoot_max_pct,
        "resting_reclaim_reentries": int(out.resting_reclaim_reentries),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "rejected_exit_signals": int(out.rejected_exit_signals),
        "winning_exits": int(out.winning_exits),
        "losing_exits": int(out.losing_exits),
        "max_drawdown_pct": out.max_drawdown_pct,
        "turnover_one_way": out.turnover,
        "insolvent": bool(out.insolvent),
        "exit_signal_count": int(np.count_nonzero(candidate.exit_event)),
        "synthetic_rth_pct": 100.0 * float(data.synthetic.mean()),
    }
    # The user's non-negotiable policy: try to re-enter lower, but never remain
    # flat once the stored exit/top is reclaimed.  With next-open execution one
    # flat signal bar per reclaim is unavoidable and explicitly allowed.
    row["mandatory_reclaim_policy"] = bool(
        reentry_mode in (2, 4)
        and reclaim_buffer == 0.0
        and out.bars_flat_beyond_reclaim <= out.reclaim_reentries
    )
    row["target_tim_policy"] = bool(70.0 <= row["tim_rth_pct"] <= 80.0)
    row["policy_compliant"] = bool(
        row["mandatory_reclaim_policy"] and row["target_tim_policy"]
    )
    return row


def _reference_replay(
    data: ExecutionData,
    candidate: ExitCandidate,
    lower_event: np.ndarray,
    side: int,
    reentry: tuple[str, int, float, float],
    cost_rate: float,
    slip_rate: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Independent Python replay for the top compiled result.

    This intentionally does not call the C scanner.  It validates side-aware
    compounding, next-bar latency, counts, and TIM, and emits an auditable event
    schedule for a later exact-engine adapter.
    """
    reentry_label, reentry_mode, reclaim_buffer, lower_gap = reentry
    n = len(data.ts)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    in_position = True
    pending_exit: dict[str, Any] | None = None
    pending_entry: dict[str, Any] | None = None
    entry_px = data.open[0] * (1.0 + slip_rate if side > 0 else 1.0 - slip_rate)
    entry_equity = equity
    equity = entry_equity
    entry_index = 0
    hold_start = int(data.ts[0])
    held_bars = 0
    held_seconds = 0.0
    trail = math.nan
    position_extreme = entry_px
    mfe_lock_active = False
    last_exit = math.nan
    exit_atr = math.nan
    reclaim_level = math.nan
    gap_seen = False
    tech_exits = 0
    reentries = 0
    reclaim_reentries = 0
    resting_reclaim_reentries = 0
    lower_reentries = 0
    rejected_exit_signals = 0
    reclaim_overshoots: list[float] = []
    round_trips = 0
    events: list[dict[str, Any]] = [
        {
            "type": "ENTRY",
            "reason": "SEED_FULL_POSITION",
            "signal_index": None,
            "fill_index": 0,
            "fill_ts": int(data.ts[0]),
            "fill_px": entry_px,
            "equity_after_fill": equity,
        }
    ]

    def marked(px: float) -> float:
        return entry_equity * (1.0 + side * (px - entry_px) / entry_px)

    def drawdown(value: float) -> None:
        nonlocal peak, max_dd
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, 100.0 * (peak - value) / peak)

    for i in range(n):
        if pending_exit is not None and in_position:
            fill = data.open[i] * (1.0 - slip_rate if side > 0 else 1.0 + slip_rate)
            equity = marked(fill) - entry_equity * (2.0 * cost_rate)
            tech_exits += 1
            round_trips += 1
            held_seconds += int(data.ts[i]) - hold_start
            in_position = False
            last_exit = fill
            a = candidate.atr_exec[i - 1]
            exit_atr = a if math.isfinite(a) and a > 0 else max(1e-12, 0.01 * fill)
            stored_ref = float(pending_exit["struct_ref"])
            reclaim_level = (
                max(fill, stored_ref) if side > 0 else min(fill, stored_ref)
            )
            gap_seen = False
            trail = math.nan
            mfe_lock_active = False
            events.append(
                {
                    "type": "EXIT",
                    "reason": candidate.family,
                    "signal_index": pending_exit["signal_index"],
                    "signal_ts": pending_exit["signal_ts"],
                    "fill_index": i,
                    "fill_ts": int(data.ts[i]),
                    "fill_px": fill,
                    "entry_index": entry_index,
                    "entry_px": entry_px,
                    "stored_reclaim_level": reclaim_level,
                    "exit_atr": exit_atr,
                    "equity_after_fill": equity,
                    "latency_bars": i - int(pending_exit["signal_index"]),
                }
            )
            pending_exit = None
            drawdown(equity)

        if pending_entry is not None and not in_position:
            fill = data.open[i] * (1.0 + slip_rate if side > 0 else 1.0 - slip_rate)
            saved = (
                100.0 * (last_exit - fill) / last_exit
                if side > 0
                else 100.0 * (fill - last_exit) / last_exit
            )
            reason = str(pending_entry["reason"])
            reentries += 1
            reclaim_reentries += int(reason == "E10_RECLAIM")
            lower_reentries += int(reason == "E11_LOWER_PRICE")
            if reason == "E10_RECLAIM":
                reclaim_overshoots.append(
                    (
                        100.0 * max(0.0, fill - reclaim_level) / reclaim_level
                        if side > 0
                        else 100.0
                        * max(0.0, reclaim_level - fill)
                        / reclaim_level
                    )
                )
            entry_px = fill
            entry_equity = equity
            equity = entry_equity
            entry_index = i
            hold_start = int(data.ts[i])
            in_position = True
            trail = math.nan
            position_extreme = entry_px
            mfe_lock_active = False
            events.append(
                {
                    "type": "REENTRY",
                    "reason": reason,
                    "signal_index": pending_entry["signal_index"],
                    "signal_ts": pending_entry["signal_ts"],
                    "fill_index": i,
                    "fill_ts": int(data.ts[i]),
                    "fill_px": fill,
                    "prior_exit_px": last_exit,
                    "saved_price_pct": saved,
                    "equity_after_fill": equity,
                    "latency_bars": i - int(pending_entry["signal_index"]),
                }
            )
            pending_entry = None
            drawdown(equity)

        if in_position:
            held_bars += 1
            position_extreme = (
                max(position_extreme, float(data.high[i]))
                if side > 0
                else min(position_extreme, float(data.low[i]))
            )
            mark = marked(float(data.close[i]))
            drawdown(mark)
            should_exit = False
            if candidate.exit_mode == 1:
                raw = candidate.raw_stop[i]
                if math.isfinite(raw) and raw > 0:
                    if not math.isfinite(trail):
                        trail = raw
                    elif side > 0:
                        trail = max(trail, raw)
                    else:
                        trail = min(trail, raw)
                    should_exit = data.close[i] < trail if side > 0 else data.close[i] > trail
            elif candidate.exit_event[i]:
                if candidate.exit_mode == 2:
                    gate_active = (
                        candidate.min_profit_gate >= 0.0
                        or candidate.min_mfe_atr_gate >= 0.0
                    )
                    gate_pass = not gate_active
                    if candidate.min_profit_gate >= 0.0:
                        raw_leg = side * (data.close[i] - entry_px) / entry_px
                        gate_pass |= (
                            raw_leg
                            >= 2.0 * cost_rate + candidate.min_profit_gate
                        )
                    if (
                        candidate.min_mfe_atr_gate >= 0.0
                        and math.isfinite(candidate.atr_exec[i])
                        and candidate.atr_exec[i] > 0
                    ):
                        mfe = (
                            position_extreme - entry_px
                            if side > 0
                            else entry_px - position_extreme
                        )
                        gate_pass |= (
                            mfe
                            >= candidate.min_mfe_atr_gate
                            * candidate.atr_exec[i]
                        )
                    should_exit = gate_pass
                    if not gate_pass:
                        rejected_exit_signals += 1
                elif candidate.exit_mode == 3:
                    atr = candidate.atr_exec[i]
                    q = candidate.raw_stop[i]
                    k = candidate.struct_ref[i]
                    if (
                        math.isfinite(atr)
                        and atr > 0
                        and math.isfinite(q)
                        and q > 0
                        and math.isfinite(k)
                        and k > 0
                    ):
                        mfe = (
                            position_extreme - entry_px
                            if side > 0
                            else entry_px - position_extreme
                        )
                        mfe_lock_active |= mfe >= q * atr
                        if mfe_lock_active:
                            raw_trail = (
                                position_extreme - k * atr
                                if side > 0
                                else position_extreme + k * atr
                            )
                            if not math.isfinite(trail):
                                trail = raw_trail
                            elif side > 0:
                                trail = max(trail, raw_trail)
                            else:
                                trail = min(trail, raw_trail)
                            should_exit = (
                                data.close[i] < trail
                                if side > 0
                                else data.close[i] > trail
                            )
            if (
                candidate.exit_mode == 4
                and math.isfinite(candidate.raw_stop[i])
                and candidate.raw_stop[i] > 0
            ):
                # Existing DC_LOW4_STOP path freezes the indicator level at
                # entry/re-entry; it is not a moving Donchian exit.
                if not math.isfinite(trail):
                    trail = candidate.raw_stop[i]
                should_exit = (
                    data.close[i] <= trail
                    if side > 0
                    else data.close[i] >= trail
                )
            if should_exit and i + 1 < n:
                ref = (
                    position_extreme
                    if candidate.exit_mode == 3
                    else candidate.struct_ref[i]
                )
                if not (math.isfinite(ref) and ref > 0):
                    ref = data.high[i] if side > 0 else data.low[i]
                pending_exit = {
                    "signal_index": i,
                    "signal_ts": int(data.ts[i]),
                    "struct_ref": float(ref),
                }
        elif math.isfinite(last_exit):
            if side > 0 and not gap_seen:
                gap_seen = data.low[i] <= last_exit - lower_gap * exit_atr
            elif side < 0 and not gap_seen:
                gap_seen = data.high[i] >= last_exit + lower_gap * exit_atr
            if i + 1 < n:
                if reentry_mode == 4:
                    open_through = (
                        data.open[i] >= reclaim_level
                        if side > 0
                        else data.open[i] <= reclaim_level
                    )
                    touched = (
                        data.high[i] >= reclaim_level
                        if side > 0
                        else data.low[i] <= reclaim_level
                    )
                    if open_through or touched:
                        fill = (
                            data.open[i]
                            if open_through
                            else reclaim_level
                        ) * (
                            1.0 + slip_rate
                            if side > 0
                            else 1.0 - slip_rate
                        )
                        saved = (
                            100.0 * (last_exit - fill) / last_exit
                            if side > 0
                            else 100.0 * (fill - last_exit) / last_exit
                        )
                        overshoot = (
                            100.0
                            * max(0.0, fill - reclaim_level)
                            / reclaim_level
                            if side > 0
                            else 100.0
                            * max(0.0, reclaim_level - fill)
                            / reclaim_level
                        )
                        reentries += 1
                        reclaim_reentries += 1
                        resting_reclaim_reentries += 1
                        reclaim_overshoots.append(overshoot)
                        entry_px = fill
                        entry_equity = equity
                        entry_index = i
                        hold_start = int(data.ts[i])
                        in_position = True
                        trail = math.nan
                        position_extreme = (
                            float(data.high[i])
                            if side > 0
                            else float(data.low[i])
                        )
                        mfe_lock_active = False
                        held_bars += 1
                        events.append(
                            {
                                "type": "REENTRY",
                                "reason": "E10_RESTING_RECLAIM",
                                "signal_index": None,
                                "signal_ts": None,
                                "fill_index": i,
                                "fill_ts": int(data.ts[i]),
                                "fill_px": fill,
                                "prior_exit_px": last_exit,
                                "stored_reclaim_level": reclaim_level,
                                "saved_price_pct": saved,
                                "reclaim_overshoot_pct": overshoot,
                                "open_gap_fill": bool(open_through),
                                "equity_after_fill": equity,
                                "latency_bars": 0,
                            }
                        )
                        drawdown(
                            marked(float(data.close[i]))
                        )
                        continue
                lower_allowed = reentry_mode in (2, 3, 4)
                reclaim_allowed = reentry_mode in (1, 2)
                if lower_allowed and gap_seen and lower_event[i]:
                    pending_entry = {
                        "reason": "E11_LOWER_PRICE",
                        "signal_index": i,
                        "signal_ts": int(data.ts[i]),
                    }
                elif reclaim_allowed:
                    threshold = reclaim_buffer * exit_atr
                    reclaim = (
                        data.close[i] >= reclaim_level + threshold
                        if side > 0
                        else data.close[i] <= reclaim_level - threshold
                    )
                    if reclaim:
                        pending_entry = {
                            "reason": "E10_RECLAIM",
                            "signal_index": i,
                            "signal_ts": int(data.ts[i]),
                        }

    if in_position:
        held_seconds += int(data.ts[-1]) - hold_start
        fill = data.close[-1] * (1.0 - slip_rate if side > 0 else 1.0 + slip_rate)
        equity = marked(fill) - entry_equity * (2.0 * cost_rate)
        round_trips += 1
        events.append(
            {
                "type": "MTM_FINAL",
                "reason": "END_OF_SAMPLE",
                "signal_index": n - 1,
                "fill_index": n - 1,
                "fill_ts": int(data.ts[-1]),
                "fill_px": fill,
                "entry_index": entry_index,
                "entry_px": entry_px,
                "equity_after_fill": equity,
                "latency_bars": 0,
            }
        )
        drawdown(equity)

    result = {
        "strategy": candidate.label,
        "reentry": reentry_label,
        "final_equity": equity,
        "gain_pct": 100.0 * (equity - 1.0),
        "technical_exits": tech_exits,
        "round_trips": round_trips,
        "reentries": reentries,
        "reclaim_reentries": reclaim_reentries,
        "resting_reclaim_reentries": resting_reclaim_reentries,
        "lower_reentries": lower_reentries,
        "rejected_exit_signals": rejected_exit_signals,
        "mean_reclaim_overshoot_pct": (
            float(np.mean(reclaim_overshoots)) if reclaim_overshoots else 0.0
        ),
        "max_reclaim_overshoot_pct": (
            max(reclaim_overshoots) if reclaim_overshoots else 0.0
        ),
        "held_bars": held_bars,
        "held_seconds": held_seconds,
        "tim_rth_pct": 100.0 * held_bars / n,
        "tim_calendar_pct": 100.0 * held_seconds / max(1, int(data.ts[-1] - data.ts[0])),
        "max_drawdown_pct": max_dd,
        "all_action_latencies_one_bar": all(
            event.get("latency_bars") in (0, 1)
            for event in events
            if event["type"] in {"EXIT", "REENTRY", "MTM_FINAL"}
        ),
    }
    return result, events


def _parity_audit(
    compiled_row: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "gain_pct": abs(float(compiled_row["gain_pct"]) - float(reference["gain_pct"])) < 1e-9,
        "technical_exits": compiled_row["technical_exits"] == reference["technical_exits"],
        "round_trips": compiled_row["round_trips"] == reference["round_trips"],
        "reentries": compiled_row["reentries"] == reference["reentries"],
        "reclaim_reentries": (
            compiled_row["reclaim_reentries"] == reference["reclaim_reentries"]
        ),
        "lower_reentries": compiled_row["lower_reentries"] == reference["lower_reentries"],
        "tim_rth_pct": (
            abs(float(compiled_row["tim_rth_pct"]) - float(reference["tim_rth_pct"])) < 1e-9
        ),
        "tim_calendar_pct": (
            abs(float(compiled_row["tim_calendar_pct"]) - float(reference["tim_calendar_pct"]))
            < 1e-9
        ),
        "next_bar_latency": bool(reference["all_action_latencies_one_bar"]),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "compiled": {
            k: compiled_row[k]
            for k in (
                "gain_pct",
                "technical_exits",
                "round_trips",
                "reentries",
                "reclaim_reentries",
                "lower_reentries",
                "tim_rth_pct",
                "tim_calendar_pct",
            )
        },
        "reference": reference,
    }


def _write_artifacts(
    out_dir: Path,
    data: ExecutionData,
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    parity: dict[str, Any],
    audit_events: list[dict[str, Any]],
    replay_candidate: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    key = f"{data.symbol}_{manifest['side']}"
    with gzip.open(out_dir / f"candidates_{key}.jsonl.gz", "wt") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    (out_dir / f"contract_{data.symbol}.json").write_text(
        json.dumps(data.contract, sort_keys=True, indent=2) + "\n"
    )

    ranked = sorted(
        rows,
        key=lambda x: (
            bool(x["insolvent"]),
            -float(x["gain_pct"]),
            -float(x["tim_rth_pct"]),
        ),
    )
    top = ranked[:100]
    fields = [
        "family",
        "strategy",
        "reentry",
        "policy_compliant",
        "mandatory_reclaim_policy",
        "target_tim_policy",
        "gain_pct",
        "bh_net_side_pct",
        "strategy_bh_multiple",
        "alpha_vs_bh_pp",
        "tim_rth_pct",
        "tim_calendar_pct",
        "round_trips",
        "technical_exits",
        "reclaim_reentries",
        "lower_reentries",
        "mean_saved_price_pct",
        "mean_missed_move_pct",
        "bars_flat_beyond_reclaim",
        "max_drawdown_pct",
        "exit_signal_count",
    ]
    with (out_dir / f"top100_{key}.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(top)

    family_best: dict[str, dict[str, Any]] = {}
    for row in ranked:
        family_best.setdefault(row["family"], row)
    policy_family_best: dict[str, dict[str, Any]] = {}
    for row in ranked:
        if row.get("policy_compliant"):
            policy_family_best.setdefault(row["family"], row)
    digest = {
        **manifest,
        "contract_valid": bool(data.contract["valid"]),
        "contract_errors": data.contract["errors"],
        "contract_warnings": data.contract["warnings"],
        "rows": len(rows),
        "family_best": family_best,
        "policy_family_best": policy_family_best,
        "overall_top20": top[:20],
        "policy_top20": [
            row for row in ranked if row.get("policy_compliant")
        ][:20],
    }
    (out_dir / f"digest_{key}.json").write_text(
        json.dumps(digest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    snapshot = out_dir / "source_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    snapshot.joinpath(C_SOURCE.name).write_bytes(C_SOURCE.read_bytes())
    contract_source = ROOT / "tools" / "backtest_data_contract.py"
    snapshot.joinpath(contract_source.name).write_bytes(contract_source.read_bytes())
    (out_dir / "accounting_parity_audit.json").write_text(
        json.dumps(parity, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    with gzip.open(out_dir / "top_candidate_events.jsonl.gz", "wt") as fh:
        for event in audit_events:
            fh.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
    replay_queue = {
        "status": "PENDING_EXACT_ENGINE_RESEARCH_ADAPTER",
        "promotion_allowed": False,
        "reason": (
            "Candidate algorithms are isolated research implementations and do not yet have "
            "a backtest_v8_engine switch with proven event parity."
        ),
        "candidate": replay_candidate,
        "source_manifest": manifest,
        "accounting_parity": parity["status"],
        "required_replay_contract": {
            "seed": "one full side-aware unit at first RTH open",
            "signal_timing": "completed HTF close only",
            "fill_timing": "next RTH open",
            "costs": {
                "commission_bps_one_way": manifest["cost_bps_one_way"],
                "slippage_bps_one_way": manifest["slippage_bps_one_way"],
            },
            "event_schedule": "top_candidate_events.jsonl.gz",
            "acceptance": [
                "exact engine exit and reentry timestamps equal research schedule",
                "all fills have declared one-bar latency",
                "side-aware compounded gain matches within 1 basis point",
                "TIM matches within 0.05 percentage point",
                "no live config or ENGINE matrix write",
            ],
        },
    }
    (out_dir / "faithful_engine_replay_queue.json").write_text(
        json.dumps(replay_queue, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


def _self_test() -> None:
    lib = _compile_scanner()
    n = 7
    ts = np.ascontiguousarray(np.arange(n, dtype=np.int64) * 300)
    o = np.ascontiguousarray(np.array([100, 102, 98, 101, 103, 99, 100], dtype=np.float64))
    h = np.ascontiguousarray(o + 1)
    lo = np.ascontiguousarray(o - 1)
    c = np.ascontiguousarray(o.copy())
    atr = np.ascontiguousarray(np.ones(n, dtype=np.float64))
    exit_event = np.ascontiguousarray(np.array([0, 1, 0, 0, 0, 0, 0], dtype=np.uint8))
    blank = np.ascontiguousarray(np.full(n, np.nan, dtype=np.float64))
    ref = np.ascontiguousarray(np.full(n, np.nan, dtype=np.float64))
    lower = np.ascontiguousarray(np.zeros(n, dtype=np.uint8))
    out = ScanMetrics()
    rc = lib.vec_top_exit_scan(
        n,
        1,
        2,
        1,
        ts,
        o,
        h,
        lo,
        c,
        atr,
        exit_event,
        blank,
        ref,
        lower,
        0.0,
        0.5,
        -1.0,
        -1.0,
        0.0,
        0.0,
        ctypes.byref(out),
    )
    assert rc == 0
    assert out.technical_exits == 1, out.technical_exits
    assert out.reclaim_reentries == 1, out.reclaim_reentries
    assert out.round_trips == 2, out.round_trips

    out_short = ScanMetrics()
    rc = lib.vec_top_exit_scan(
        n,
        -1,
        2,
        1,
        ts,
        o,
        h,
        lo,
        c,
        atr,
        exit_event,
        blank,
        ref,
        lower,
        0.0,
        0.5,
        -1.0,
        -1.0,
        0.0,
        0.0,
        ctypes.byref(out_short),
    )
    assert rc == 0
    assert out_short.technical_exits == 1
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "long_equity": out.final_equity,
                "short_equity": out_short.final_equity,
                "next_bar_exit_and_reclaim": True,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--side", choices=("LONG", "SHORT"))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", help="exclusive UTC date, e.g. 2025-07-01")
    ap.add_argument("--npz-dir", type=Path, default=DEFAULT_NPZ)
    ap.add_argument("--profile", choices=("floor", "core", "ladder"), default="ladder")
    ap.add_argument("--families", default="E01,E02,E04,E05")
    ap.add_argument(
        "--select-strategy",
        help="research replay only: freeze this exact strategy label instead of re-optimizing",
    )
    ap.add_argument(
        "--select-reentry",
        help="research replay only: freeze this exact reentry label instead of re-optimizing",
    )
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--allow-invalid-diagnostic", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if not args.symbol or not args.side:
        ap.error("--symbol and --side are required unless --self-test is used")

    symbol = args.symbol.upper()
    side = 1 if args.side == "LONG" else -1
    data = _load_execution(symbol, args.npz_dir, args.start, args.profile, args.end)
    if not data.contract["valid"] and not args.allow_invalid_diagnostic:
        quarantine_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine_dir = (
            args.output_root / f"top_exit_{quarantine_id}_{symbol}_{args.side}_QUARANTINE"
        )
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        (quarantine_dir / f"contract_{symbol}.json").write_text(
            json.dumps(data.contract, sort_keys=True, indent=2) + "\n"
        )
        status = {
            "status": "DATA_QUARANTINE",
            "matrix_eligible": False,
            "campaign_executed": False,
            "symbol": symbol,
            "side": args.side,
            "errors": data.contract["errors"],
            "warnings": data.contract["warnings"],
            "artifact": str(quarantine_dir),
        }
        (quarantine_dir / "quarantine.json").write_text(
            json.dumps(status, sort_keys=True, indent=2) + "\n"
        )
        print(
            json.dumps(status, sort_keys=True)
        )
        data.z.close()
        return 2

    lib = _compile_scanner()
    htfs = {tf: _compress_htf(data, tf) for tf in ("1h", "4h", "D")}
    lower_event = _lower_reentry_events(len(data.ts), htfs["1h"], side)
    families = {v.strip().upper() for v in args.families.split(",") if v.strip()}
    reentries = _reentry_variants(args.quick)
    cost_rate = args.cost_bps / 10_000.0
    slip_rate = args.slippage_bps / 10_000.0
    bh = _side_bh(data, side, cost_rate, slip_rate)

    rows: list[dict[str, Any]] = []
    for candidate in _candidate_iter(data, htfs, side, families, args.quick):
        for reentry in reentries:
            rows.append(
                _scan(
                    lib,
                    data,
                    candidate,
                    lower_event,
                    side,
                    reentry,
                    cost_rate,
                    slip_rate,
                    bh,
                )
            )
    if not rows:
        data.z.close()
        raise RuntimeError("no candidates generated")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_root / f"top_exit_{run_id}_{symbol}_{args.side}"
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "symbol": symbol,
        "side": args.side,
        "start": args.start,
        "end_exclusive": args.end,
        "end_epoch": int(data.ts[-1]),
        "rth_rows": len(data.ts),
        "synthetic_rth_pct": 100.0 * float(data.synthetic.mean()),
        "npz_path": data.path,
        "npz_size": Path(data.path).stat().st_size,
        "npz_sha256": _sha256_file(data.path),
        "c_source_sha256": hashlib.sha256(C_SOURCE.read_bytes()).hexdigest(),
        "python_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "contract_source_sha256": hashlib.sha256(
            (ROOT / "tools" / "backtest_data_contract.py").read_bytes()
        ).hexdigest(),
        "cost_bps_one_way": args.cost_bps,
        "slippage_bps_one_way": args.slippage_bps,
        "families": sorted(families),
        "quick": args.quick,
        "invalid_data_diagnostic": not bool(data.contract["valid"]),
    }
    ranked = sorted(rows, key=lambda x: x["gain_pct"], reverse=True)
    policy_ranked = [row for row in ranked if row.get("policy_compliant")]
    if bool(args.select_strategy) != bool(args.select_reentry):
        data.z.close()
        raise ValueError("--select-strategy and --select-reentry must be supplied together")
    if args.select_strategy:
        selected = [
            row
            for row in rows
            if row["strategy"] == args.select_strategy
            and row["reentry"] == args.select_reentry
        ]
        if len(selected) != 1:
            data.z.close()
            raise ValueError(
                "frozen candidate not found exactly once: "
                f"strategy={args.select_strategy!r} reentry={args.select_reentry!r} "
                f"matches={len(selected)}"
            )
        best_row = selected[0]
    else:
        best_row = policy_ranked[0] if policy_ranked else ranked[0]
    best_candidate = next(
        candidate
        for candidate in _candidate_iter(data, htfs, side, families, args.quick)
        if candidate.label == best_row["strategy"]
    )
    best_reentry = next(row for row in reentries if row[0] == best_row["reentry"])
    reference, audit_events = _reference_replay(
        data,
        best_candidate,
        lower_event,
        side,
        best_reentry,
        cost_rate,
        slip_rate,
    )
    parity = _parity_audit(best_row, reference)
    if parity["status"] != "PASS":
        data.z.close()
        raise RuntimeError("compiled/reference accounting parity failed: " + json.dumps(parity))
    _write_artifacts(
        out_dir,
        data,
        rows,
        manifest,
        parity,
        audit_events,
        best_row,
    )
    data.z.close()

    print(
        json.dumps(
            {
                "status": "PASS" if data.contract["valid"] else "INVALID_DATA_DIAGNOSTIC",
                "symbol": symbol,
                "side": args.side,
                "rows": len(rows),
                "artifact": str(out_dir),
                "bh_net_side_pct": bh["bh_net_side_pct"],
                "accounting_parity": parity["status"],
                "best_policy_compliant": policy_ranked[0] if policy_ranked else None,
                "best_overall_diagnostic": ranked[0],
                "family_best": {
                    family: next(row for row in ranked if row["family"] == family)
                    for family in sorted({row["family"] for row in rows})
                },
            },
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
