#!/usr/bin/env python3
"""Preregistered vector-first research for two SHORT-native stock books.

This is deliberately not ``LONG * -1``:

* CORRECTION waits for a completed bullish-regime top/rejection, then requires
  a completed 1h structural break or WaveTrend rollover before a brief short.
* BEAR requires an established D/4h bear state and enters only on a failed
  reclaim or persistent downside continuation.

All signals use the shared parent-close availability clock.  Fills occur at
the first strictly later availability batch with adverse SHORT slippage.
Discovery folds are evaluated before the selected candidate is frozen; only
then is FINAL simulated.  This file is research-only and cannot change live
configuration or matrix evidence.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools import path_fleet_campaign as fleet  # noqa: E402
from tools import vec_asymmetric_short_campaign as legacy  # noqa: E402
from tools import vec_top_exit_campaign as top  # noqa: E402
from tools.research_fill_contract import adverse_fill_price  # noqa: E402
from tools.research_availability_clock import (  # noqa: E402
    CLOCK_CONTRACT,
    next_strictly_later_index,
)


STAGE = "VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS"
CONTRACT = "SHORT_NATIVE_TWO_BOOKS_PHASE2_V1"
ACCOUNT_USD = 10_000.0
BASE_USD = 2_000.0
CAPACITY_USD = 16_000.0
CORRECTION_DEFAULT = ("NVDA", "MU", "SNDK", "ARM", "MRVL", "LRCX", "PLTR")
BEAR_DEFAULT = ("MSTR", "IBIT", "COIN")
START = "2024-03-26"
FINAL_START = "2026-01-01"
FINAL_END = "2026-07-25"


@dataclasses.dataclass(frozen=True)
class EntryProfile:
    label: str
    book: str
    confirm: str
    top_k: float
    top_pct_b: float
    min_sma_distance: float
    max_peak_gap_atr: float
    min_peak_gap_atr: float
    min_velocity_atr: float
    min_acceleration_atr: float
    min_atr_ratio: float
    min_rel_volume: float
    min_upper_wick: float
    persistence: int
    arm_hours: int


@dataclasses.dataclass(frozen=True)
class CoverProfile:
    label: str
    book: str
    max_mult: float
    scale_atr: float
    exhaustion_k: float
    trail_arm_atr: float
    trail_atr: float
    reclaim_atr: float
    max_hold_hours: int
    emergency_atr: float


# Frozen coherent profiles.  They test distinct SHORT hypotheses rather than
# an unbounded field-by-field grid.
ENTRY_PROFILES = (
    EntryProfile("C01_STRUCT_BAL", "CORRECTION", "STRUCT", 70, .70, .03, 10, 0, .05, -1, .80, .75, 0, 1, 48),
    EntryProfile("C02_STRUCT_IMPULSE", "CORRECTION", "STRUCT", 75, .75, .06, 8, 0, .25, .00, .95, .90, 0, 1, 36),
    EntryProfile("C03_WT_ROLLOVER", "CORRECTION", "WT", 75, .75, .05, 8, 0, .15, -.05, .90, .80, 0, 1, 48),
    EntryProfile("C04_STRUCT_OR_WT_VOL", "CORRECTION", "OR", 75, .75, .05, 8, 0, .20, -.05, 1.00, 1.10, 0, 1, 48),
    EntryProfile("C05_STRUCT_AND_WT", "CORRECTION", "AND", 80, .80, .08, 6, 0, .15, -.05, .90, .90, 0, 1, 48),
    EntryProfile("C06_REJECTION_WICK", "CORRECTION", "OR", 75, .80, .06, 7, 0, .10, -.10, .85, .85, .30, 1, 36),
    EntryProfile("C07_PERSISTENT_LHLL", "CORRECTION", "STRUCT", 70, .70, .03, 10, 0, .05, -1, .80, .75, 0, 2, 64),
    EntryProfile("C08_ATR_ACCEL_SHOCK", "CORRECTION", "OR", 80, .80, .08, 6, 0, .35, .10, 1.15, 1.20, 0, 1, 36),
    EntryProfile("B01_FAILED_RECLAIM", "BEAR", "RECLAIM", 0, 0, 0, 0, 3, .05, -1, .80, .75, 0, 1, 96),
    EntryProfile("B02_RECLAIM_WT", "BEAR", "RECLAIM_WT", 0, 0, 0, 0, 3, .10, -.10, .85, .80, 0, 1, 96),
    EntryProfile("B03_STRUCT_CONTINUE", "BEAR", "STRUCT", 0, 0, 0, 0, 4, .15, -.10, .85, .80, 0, 1, 96),
    EntryProfile("B04_ATR_VOL_BREAK", "BEAR", "OR", 0, 0, 0, 0, 4, .30, .05, 1.10, 1.20, 0, 1, 72),
    EntryProfile("B05_DEEP_FAILED_RALLY", "BEAR", "RECLAIM", 0, 0, 0, 0, 7, .05, -1, .80, .75, 0, 1, 120),
    EntryProfile("B06_PERSISTENT_LHLL", "BEAR", "STRUCT", 0, 0, 0, 0, 4, .05, -1, .80, .75, 0, 2, 120),
    EntryProfile("B07_WT_ACCEL_CONTINUE", "BEAR", "WT", 0, 0, 0, 0, 4, .20, .05, .95, .90, 0, 1, 96),
    EntryProfile("B08_STRICT_BEAR", "BEAR", "AND", 0, 0, 0, 0, 5, .20, .00, 1.00, 1.00, 0, 1, 96),
)

COVER_PROFILES = (
    CoverProfile("COV_C_FAST", "CORRECTION", 4, .75, 20, 1.0, 1.25, .10, 24, 4.0),
    CoverProfile("COV_C_BAL", "CORRECTION", 4, 1.00, 20, 1.5, 1.75, .20, 48, 4.5),
    CoverProfile("COV_C_RIDE", "CORRECTION", 6, 1.00, 15, 2.0, 2.25, .25, 72, 5.0),
    CoverProfile("COV_B_FAST", "BEAR", 4, .75, 20, 1.5, 1.75, .20, 72, 4.5),
    CoverProfile("COV_B_BAL", "BEAR", 6, 1.00, 15, 2.0, 2.25, .25, 144, 5.0),
    CoverProfile("COV_B_RIDE", "BEAR", 8, 1.25, 10, 3.0, 3.00, .35, 240, 6.0),
)


def profiles(book: str) -> list[tuple[EntryProfile, CoverProfile]]:
    return [
        (entry, cover)
        for entry in ENTRY_PROFILES
        for cover in COVER_PROFILES
        if entry.book == book and cover.book == book
    ]


def _field(data: top.ExecutionData, name: str, default: float = math.nan) -> np.ndarray:
    if name not in data.z.files:
        return np.full(len(data.ts), default, dtype=np.float64)
    return np.asarray(data.z[name], dtype=np.float64)[data.full_indices]


def _aligned(data: top.ExecutionData, htf: top.HTFData, name: str) -> np.ndarray:
    return legacy._aligned(data, htf, name)


def _rolling_median_prior(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) <= n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values[:-1], n)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        out[n:] = np.nanmedian(windows, axis=1)
    return out


def build_features(
    data: top.ExecutionData, htfs: dict[str, top.HTFData]
) -> dict[str, Any]:
    n = len(data.ts)
    h1, h4, day = (htfs[x] for x in ("1h", "4h", "D"))
    a = lambda h, name: _aligned(data, h, name)
    out: dict[str, Any] = {
        "atr1": top._align_feature(n, h1, h1.atr),
        "k1": a(h1, "stoch_k_1h"),
        "k4": a(h4, "stoch_k_4h"),
        "pctb4": a(h4, "lrL_pct_b_4h"),
        "w11": a(h1, "wt1_1h"),
        "w21": a(h1, "wt2_1h"),
        "w14": a(h4, "wt1_4h"),
        "w24": a(h4, "wt2_4h"),
        "w1d": a(day, "wt1_D"),
        "w2d": a(day, "wt2_D"),
        "ema1": a(h1, "ema_20_1h"),
        "ema4": a(h4, "ema_20_4h"),
        "ema50_4": a(h4, "ema_50_4h"),
        "emad": a(day, "ema_20_D"),
        "sma4": a(h4, "sma_200_4h"),
        "upper_wick1": a(h1, "bar_upper_wick_1h"),
        "relvol1": a(h1, "relative_volume_1h"),
        "h1_hi": top._align_feature(n, h1, h1.high),
        "h1_lo": top._align_feature(n, h1, h1.low),
        "h1_cl": top._align_feature(n, h1, h1.close),
    }
    atr_med = _rolling_median_prior(h1.atr, 10)
    peak = top._rolling_prior(day.high, 63, "max")
    out["atr_ratio"] = top._align_feature(
        n, h1, h1.atr / np.where(atr_med > 0, atr_med, np.nan)
    )
    out["peak_gap_atr"] = top._align_feature(
        n, day, (peak - day.close) / np.where(day.atr > 0, day.atr, np.nan)
    )
    out["event_index"] = h1.event_index
    out["event_source_ts"] = h1.source_ts
    return out


def entry_events(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    f: dict[str, Any],
    p: EntryProfile,
) -> tuple[np.ndarray, dict[str, int]]:
    h1 = htfs["1h"]
    event = np.zeros(len(data.ts), dtype=np.uint8)
    armed_until = -1
    armed = False
    lhll_streak = 0
    prior_velocity = 0.0
    audit = {
        "qualified_context": 0,
        "structural_breaks": 0,
        "wt_rollovers": 0,
        "failed_reclaims": 0,
        "impulse_passes": 0,
        "entry_signals": 0,
        "future_htf_sources": 0,
    }
    for j, i0 in enumerate(h1.event_index):
        i = int(i0)
        if j < 65:
            continue
        audit["future_htf_sources"] += int(h1.source_ts[j] > data.ts[i])
        atr = float(h1.atr[j])
        if not math.isfinite(atr) or atr <= 0:
            continue
        close = float(h1.close[j])
        velocity = (float(h1.close[j - 1]) - close) / atr
        acceleration = velocity - prior_velocity
        prior_velocity = velocity
        lhll = bool(
            h1.high[j] < h1.high[j - 1]
            and h1.low[j] < h1.low[j - 1]
            and close < h1.low[j - 1]
        )
        lhll_streak = lhll_streak + 1 if lhll else 0
        structure = lhll_streak >= p.persistence
        wt_bear = f["w11"][i] < f["w21"][i]
        wt_roll = bool(
            wt_bear
            and f["w11"][i] < f["w11"][int(h1.event_index[j - 1])]
            and close < h1.close[j - 1]
        )
        failed_reclaim = bool(
            math.isfinite(f["ema1"][i])
            and h1.high[j - 1] >= f["ema1"][int(h1.event_index[j - 1])]
            and close < f["ema1"][i]
            and h1.high[j] < h1.high[j - 1]
        )
        audit["structural_breaks"] += int(structure)
        audit["wt_rollovers"] += int(wt_roll)
        audit["failed_reclaims"] += int(failed_reclaim)

        if p.book == "CORRECTION":
            sma_distance = (
                close / f["sma4"][i] - 1.0
                if math.isfinite(f["sma4"][i]) and f["sma4"][i] > 0 else -1.0
            )
            top_context = bool(
                f["k4"][i] >= p.top_k
                and f["pctb4"][i] >= p.top_pct_b
                and sma_distance >= p.min_sma_distance
                and f["peak_gap_atr"][i] <= p.max_peak_gap_atr
                and (f["w1d"][i] >= f["w2d"][i] or f["w14"][i] >= f["w24"][i])
            )
            if top_context:
                armed = True
                armed_until = int(data.ts[i] + p.arm_hours * 3600)
                audit["qualified_context"] += 1
            context = armed and int(data.ts[i]) <= armed_until
        else:
            context = bool(
                f["peak_gap_atr"][i] >= p.min_peak_gap_atr
                and close < f["ema1"][i]
                and f["h1_cl"][i] < f["ema4"][i]
                and f["h1_cl"][i] < f["ema50_4"][i]
                and f["h1_cl"][i] < f["emad"][i]
                and f["w14"][i] < f["w24"][i]
                and f["w1d"][i] < f["w2d"][i]
            )
            audit["qualified_context"] += int(context)

        confirm = {
            "STRUCT": structure,
            "WT": wt_roll,
            "OR": structure or wt_roll,
            "AND": structure and wt_roll,
            "RECLAIM": failed_reclaim and (structure or velocity >= p.min_velocity_atr),
            "RECLAIM_WT": failed_reclaim and wt_roll,
        }[p.confirm]
        impulse = bool(
            velocity >= p.min_velocity_atr
            and acceleration >= p.min_acceleration_atr
            and f["atr_ratio"][i] >= p.min_atr_ratio
            and f["relvol1"][i] >= p.min_rel_volume
            and (
                p.min_upper_wick <= 0
                or (
                    math.isfinite(f["upper_wick1"][i])
                    and f["upper_wick1"][i] >= p.min_upper_wick
                )
            )
        )
        audit["impulse_passes"] += int(impulse)
        if context and confirm and impulse:
            event[i] = 1
            audit["entry_signals"] += 1
            armed = False
    return event, audit


def simulate(
    data: top.ExecutionData,
    f: dict[str, Any],
    entry: np.ndarray,
    cover: CoverProfile,
    left: int,
    right: int,
    commission_bps: float,
    slippage_bps: float,
    *,
    emit_schedule: bool = False,
) -> dict[str, Any]:
    commission = commission_bps / 10_000.0
    slippage = slippage_bps / 10_000.0
    cash = ACCOUNT_USD
    qty = 0.0
    avg_entry = entry_atr = 0.0
    entry_i = -1
    low_water = math.inf
    next_scale = -math.inf
    pending: tuple[str, int, int, str] | None = None
    entry_fills = scales = exits = wins = held = 0
    realized = fees = weighted_tim = 0.0
    peak_equity = min_equity = ACCOUNT_USD
    max_dd = peak_notional = 0.0
    reason_counts: dict[str, int] = {}
    schedule_events: list[dict[str, Any]] = []
    trade_returns: list[float] = []

    def notional(px: float) -> float:
        return abs(qty) * px

    def request(kind: str, signal_i: int, reason: str) -> None:
        nonlocal pending
        if pending is not None:
            return
        fill_i = next_strictly_later_index(data.ts, signal_i, right)
        if fill_i is not None:
            pending = (kind, fill_i, signal_i, reason)

    def clock(prefix: str, idx: int) -> dict[str, int]:
        return {
            f"{prefix}_ts": int(data.ts[idx]),
            f"{prefix}_availability_ts": int(data.ts[idx]),
            f"{prefix}_source_ts": int(data.source_ts[idx]),
            f"{prefix}_source_row_index": int(data.full_indices[idx]),
            f"{prefix}_clock_index": int(idx),
        }

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None and i == pending[1]:
            kind, _, signal_i, reason = pending
            pending = None
            if kind in {"ENTRY", "SCALE"} and (qty == 0 or kind == "SCALE"):
                px = adverse_fill_price(
                    op, position_side="SHORT", opening=True,
                    slippage_rate=slippage,
                )
                cap = min(CAPACITY_USD, BASE_USD * cover.max_mult)
                add_notional = (
                    BASE_USD
                    if kind == "ENTRY"
                    else max(0.0, min(BASE_USD, cap - notional(px)))
                )
                if add_notional > 1.0:
                    add_q = add_notional / px
                    old_q = abs(qty)
                    fee = add_notional * commission
                    cash += add_notional - fee
                    fees += fee
                    qty -= add_q
                    avg_entry = (
                        px if old_q == 0
                        else (avg_entry * old_q + px * add_q) / (old_q + add_q)
                    )
                    if old_q == 0:
                        entry_atr = float(f["atr1"][i])
                        entry_i = i
                        low_water = close
                        entry_fills += 1
                    else:
                        scales += 1
                    next_scale = px - cover.scale_atr * max(
                        entry_atr, px * .005
                    )
                    peak_notional = max(peak_notional, notional(px))
                    if emit_schedule:
                        schedule_events.append({
                            "type": "ENTRY" if kind == "ENTRY" else "AUGMENT",
                            "reason": reason,
                            "signal_index": signal_i - left,
                            "fill_index": i - left,
                            "fill_px": px,
                            "quantity": add_q,
                            "requested_notional_usd": add_notional,
                            "filled_notional_usd": add_notional,
                            "post_fill_notional_usd": notional(px),
                            "entry_capacity_usd": CAPACITY_USD,
                            "semantics": "add",
                            **clock("signal", signal_i),
                            **clock("fill", i),
                        })
            elif kind == "EXIT" and qty < 0:
                px = adverse_fill_price(
                    op, position_side="SHORT", opening=False,
                    slippage_rate=slippage,
                )
                q = abs(qty)
                fee = q * px * commission
                pnl_before_fees = (avg_entry - px) * q
                cash -= q * px + fee
                fees += fee
                realized += pnl_before_fees
                trade_ret = (avg_entry - px) / avg_entry * 100.0
                trade_returns.append(trade_ret)
                wins += int(trade_ret > 0)
                exits += 1
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                if emit_schedule:
                    schedule_events.append({
                        "type": "EXIT",
                        "reason": reason,
                        "signal_index": signal_i - left,
                        "fill_index": i - left,
                        "fill_px": px,
                        "quantity": q,
                        "filled_notional_usd": q * px,
                        "position_qty_after_fill": 0.0,
                        "entry_capacity_usd": CAPACITY_USD,
                        **clock("signal", signal_i),
                        **clock("fill", i),
                    })
                qty = 0.0
                avg_entry = entry_atr = 0.0
                entry_i = -1
                low_water = math.inf

        if qty == 0:
            if entry[i]:
                request("ENTRY", i, "SHORT_NATIVE_ENTRY")
        else:
            held += 1
            weighted_tim += min(CAPACITY_USD, notional(close)) / CAPACITY_USD
            low_water = min(low_water, close)
            atr = max(entry_atr, close * .005)
            mfe_atr = (avg_entry - low_water) / atr
            elapsed = (int(data.ts[i]) - int(data.ts[entry_i])) / 3600.0
            wt_bull = f["w11"][i] > f["w21"][i]
            structural_reclaim = bool(
                close > f["h1_hi"][i] + cover.reclaim_atr * atr
            )
            exhaustion = bool(f["k1"][i] <= cover.exhaustion_k and wt_bull)
            vol_trail = bool(
                mfe_atr >= cover.trail_arm_atr
                and close >= low_water + cover.trail_atr * atr
            )
            emergency = close >= avg_entry + cover.emergency_atr * atr
            max_hold = elapsed >= cover.max_hold_hours
            if emergency or structural_reclaim or exhaustion or vol_trail or max_hold:
                reason = (
                    "EMERGENCY_ATR" if emergency else
                    "BULL_STRUCTURAL_RECLAIM" if structural_reclaim else
                    "DOWNSIDE_EXHAUSTION" if exhaustion else
                    "VOLATILITY_TRAILING_COVER" if vol_trail else
                    "MAX_HOLD"
                )
                request("EXIT", i, reason)
            elif (
                close <= next_scale
                and f["w11"][i] < f["w21"][i]
                and notional(close) < min(
                    CAPACITY_USD, BASE_USD * cover.max_mult
                ) - 1.0
            ):
                request("SCALE", i, "DOWNSIDE_PERSISTENCE_SCALE")

        equity = cash + qty * close
        peak_equity = max(peak_equity, equity)
        min_equity = min(min_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, (peak_equity - equity) / peak_equity * 100)

    final_px = float(data.close[right - 1])
    final_equity = cash + qty * final_px
    pnl = final_equity - ACCOUNT_USD
    capital_ret = pnl / BASE_USD * 100.0
    account_ret = pnl / ACCOUNT_USD * 100.0
    long_bh = (final_px / float(data.close[left]) - 1.0) * 100.0
    short_bh = -long_bh
    opportunity = max(short_bh, 0.0)
    rows = max(1, right - left)
    result = {
        "capital_return_pct": capital_ret,
        "account_return_pct": account_ret,
        "strategy_return_pct": account_ret,
        "realized_pnl_before_fees_usd": realized,
        "fees_usd": fees,
        "open_mtm_usd": (
            (avg_entry - final_px) * abs(qty) if qty < 0 else 0.0
        ),
        "cash_benchmark_pct": 0.0,
        "short_bh_return_pct": short_bh,
        "opportunity_benchmark_pct": opportunity,
        "beats_opportunity_benchmark": capital_ret > opportunity,
        "strategy_bh_multiple": (
            capital_ret / short_bh if short_bh > 0 else None
        ),
        "minimum_account_equity_usd": min_equity,
        "max_drawdown_account_pct": max_dd,
        "insolvent": min_equity <= 0,
        "time_in_market_pct": held / rows * 100.0,
        "exposure_weighted_tim_pct": weighted_tim / rows * 100.0,
        "peak_post_fill_notional_usd": peak_notional,
        "capacity_usd": CAPACITY_USD,
        "entry_fills": entry_fills,
        "scale_fills": scales,
        "technical_exits": exits,
        "open_position": qty < 0,
        "win_rate_pct": wins / max(1, exits) * 100.0,
        "exit_reason_counts": reason_counts,
        "emergency_exit_count": reason_counts.get("EMERGENCY_ATR", 0),
        "trade_returns_pct": trade_returns,
        "rows": rows,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
    }
    if emit_schedule:
        result["schedule_events"] = schedule_events
    return result


def discovery_gate(folds: dict[str, dict[str, Any]]) -> tuple[bool, float, list[str]]:
    failures: list[str] = []
    for name in ("D1", "D2"):
        row = folds[name]
        if row["insolvent"] or row["max_drawdown_account_pct"] >= 100:
            failures.append(f"{name}:SOLVENCY")
        if row["peak_post_fill_notional_usd"] > CAPACITY_USD + 1e-6:
            failures.append(f"{name}:CAPACITY")
        if row["technical_exits"] < 2:
            failures.append(f"{name}:ACTIVITY")
        if not row["beats_opportunity_benchmark"]:
            failures.append(f"{name}:OPPORTUNITY")
    excess = [
        folds[name]["capital_return_pct"]
        - folds[name]["opportunity_benchmark_pct"]
        for name in ("D1", "D2")
    ]
    score = (
        float(np.median(excess))
        - .35 * max(folds[x]["max_drawdown_account_pct"] for x in ("D1", "D2"))
        + .02 * sum(folds[x]["technical_exits"] for x in ("D1", "D2"))
    )
    return not failures, score, failures


def _profile_label(entry: EntryProfile, cover: CoverProfile) -> str:
    return f"{entry.label}__{cover.label}"


def _windows(data: top.ExecutionData) -> dict[str, tuple[int, int]]:
    return {name: (left, right) for name, left, right in legacy._windows(data)}


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def ingest(path_fleet_root: Path, payload: dict[str, Any], artifact: Path) -> dict[str, Any]:
    db = path_fleet_root / "queue.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.with_name(f"queue.db.bak_short_native_phase2_{stamp}")
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    job_ids = {
        path_id: int(job_id)
        for job_id, path_id in con.execute(
            "SELECT id,path_id FROM jobs WHERE path_id IN (?,?)",
            ("ENTRY_DELTA_MTF", "ENTRY_WT_DC"),
        )
    }
    existing = {
        json.loads(raw).get("source_row_sha256")
        for raw, in con.execute(
            "SELECT payload_json FROM results WHERE stage=?", (STAGE,)
        )
    }
    con.close()
    out = path_fleet_root / "short_native_phase2_ingest" / stamp
    appended = skipped = 0
    for row in payload["results"]:
        path_id = "ENTRY_DELTA_MTF" if row["book"] == "CORRECTION" else "ENTRY_WT_DC"
        source_hash = _sha({
            "symbol": row["symbol"],
            "book": row["book"],
            "freeze": row["freeze"],
            "final": row["untouched_final"],
        })
        if source_hash in existing:
            skipped += 1
            continue
        final = row["untouched_final"]
        fleet_payload = {
            "job_id": job_ids[path_id],
            "symbol": row["symbol"],
            "side": "SHORT",
            "stage": STAGE,
            "status": row["status"],
            "path_id": path_id,
            "book": row["book"],
            "strategy_return_pct": final["capital_return_pct"],
            "capital_return_pct": final["capital_return_pct"],
            "account_return_pct": final["account_return_pct"],
            "bh_return_pct": final["short_bh_return_pct"],
            "same_entry_control_return_pct": final["capital_return_pct"],
            "tim_pct": final["time_in_market_pct"],
            "trades": final["technical_exits"],
            "untouched_oos": True,
            "exact_replay": False,
            "future_htf_count": row["feature_audit"]["future_htf_sources"],
            "selected_entry_profile": row["freeze"]["entry_profile"],
            "selected_cover_profile": row["freeze"]["cover_profile"],
            "discovery_folds": row["discovery_folds"],
            "artifact": str(artifact),
            "source_row_sha256": source_hash,
            "matrix_eligible": False,
            "promotion_allowed": False,
            "control_contract": (
                "compound SHORT-native screen; no identical-entry engine "
                "control, so vector result cannot promote"
            ),
        }
        path = out / f"{row['symbol']}_{row['book']}.json"
        fleet.atomic_json(path, fleet_payload)
        fleet.add_result(path_fleet_root, path)
        appended += 1
    fleet.write_report(path_fleet_root)
    receipt = {
        "backup": str(backup),
        "appended": appended,
        "skipped": skipped,
        "matrix_written": False,
    }
    fleet.atomic_json(out / "summary.json", receipt)
    return receipt


def run(args: argparse.Namespace) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir).resolve() / f"short_native_phase2_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    cohort = {
        "CORRECTION": [x for x in args.correction_symbols.upper().split(",") if x],
        "BEAR": [x for x in args.bear_symbols.upper().split(",") if x],
    }
    preregistration = {
        "contract": CONTRACT,
        "stage": STAGE,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "cohort": cohort,
        "entry_profiles": [dataclasses.asdict(x) for x in ENTRY_PROFILES],
        "cover_profiles": [dataclasses.asdict(x) for x in COVER_PROFILES],
        "folds": {
            "D1": [START, "2025-07-01"],
            "D2": ["2025-07-01", FINAL_START],
            "FINAL_UNTOUCHED": [FINAL_START, FINAL_END],
        },
        "discovery_gate": (
            "D1 and D2 each: solvent, capacity<=16000, >=2 exits, fixed-$2k "
            "capital return > max(fixed-notional short B&H, cash=0)"
        ),
        "selection": "max discovery median excess - DD penalty + activity; FINAL excluded",
        "fill": "adverse SHORT first strictly later availability batch open",
        "availability_clock": CLOCK_CONTRACT,
        "account_usd": ACCOUNT_USD,
        "base_usd": BASE_USD,
        "capacity_usd": CAPACITY_USD,
        "costs": {
            "commission_bps_one_way": args.commission_bps,
            "slippage_bps_one_way": args.slippage_bps,
        },
    }
    preregistration["sha256"] = _sha(preregistration)
    (out / "PREREGISTRATION.json").write_text(
        json.dumps(preregistration, indent=2, sort_keys=True) + "\n"
    )

    frozen: list[dict[str, Any]] = []
    discovery_grid: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    discovery_cache: dict[tuple[str, str], Any] = {}
    for book, symbols in cohort.items():
        for symbol in symbols:
            data = None
            try:
                data = top._load_execution(
                    symbol, Path(args.npz_dir), START, "ladder", FINAL_END
                )
                if not data.contract["valid"]:
                    raise RuntimeError(str(data.contract["errors"]))
                htfs = {
                    tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")
                }
                windows = _windows(data)
                if set(windows) != {"D1", "D2", "FINAL"}:
                    raise RuntimeError(f"requires three folds, got {sorted(windows)}")
                features = build_features(data, htfs)
                candidate_rows = []
                event_cache: dict[str, tuple[np.ndarray, dict[str, int]]] = {}
                for entry_profile, cover_profile in profiles(book):
                    if entry_profile.label not in event_cache:
                        event_cache[entry_profile.label] = entry_events(
                            data, htfs, features, entry_profile
                        )
                    event, audit = event_cache[entry_profile.label]
                    discovery = {
                        fold: simulate(
                            data, features, event, cover_profile, *windows[fold],
                            args.commission_bps, args.slippage_bps,
                        )
                        for fold in ("D1", "D2")
                    }
                    passed, score, failures = discovery_gate(discovery)
                    candidate_rows.append({
                        "label": _profile_label(entry_profile, cover_profile),
                        "entry_profile": dataclasses.asdict(entry_profile),
                        "cover_profile": dataclasses.asdict(cover_profile),
                        "feature_audit": audit,
                        "discovery_folds": discovery,
                        "discovery_gate_pass": passed,
                        "discovery_score": score,
                        "discovery_failures": failures,
                    })
                candidate_rows.sort(
                    key=lambda x: (
                        not x["discovery_gate_pass"],
                        -x["discovery_score"],
                        x["label"],
                    )
                )
                selected = candidate_rows[0]
                discovery_grid.append({
                    "symbol": symbol,
                    "side": "SHORT",
                    "book": book,
                    "candidates": candidate_rows,
                })
                freeze = {
                    "symbol": symbol,
                    "book": book,
                    "selected_label": selected["label"],
                    "entry_profile": selected["entry_profile"],
                    "cover_profile": selected["cover_profile"],
                    "discovery_gate_pass": selected["discovery_gate_pass"],
                    "discovery_score": selected["discovery_score"],
                    "discovery_failures": selected["discovery_failures"],
                    "discovery_folds_sha256": _sha(selected["discovery_folds"]),
                    "candidate_count": len(candidate_rows),
                    "discovery_pass_count": sum(
                        x["discovery_gate_pass"] for x in candidate_rows
                    ),
                    "final_metrics_used_for_selection": False,
                }
                freeze["sha256"] = _sha(freeze)
                frozen.append(freeze)
                discovery_cache[(book, symbol)] = (
                    data, features, event_cache[selected["entry_profile"]["label"]][0],
                    CoverProfile(**selected["cover_profile"]), selected,
                )
            except Exception as exc:
                if data is not None:
                    data.z.close()
                errors.append({"symbol": symbol, "book": book, "error": str(exc)})

    # Both files exist before any FINAL simulation.  The full grid prevents a
    # selected-row-only report from hiding failed ranges.
    discovery_grid_receipt = {
        "preregistration_sha256": preregistration["sha256"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contains_final_metrics": False,
        "rows": discovery_grid,
    }
    discovery_grid_receipt["sha256"] = _sha(discovery_grid_receipt)
    (out / "DISCOVERY_GRID.json").write_text(
        json.dumps(discovery_grid_receipt, indent=2, sort_keys=True) + "\n"
    )
    freeze_receipt = {
        "preregistration_sha256": preregistration["sha256"],
        "discovery_grid_sha256": discovery_grid_receipt["sha256"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selected_on_discovery_only": True,
        "rows": frozen,
    }
    freeze_receipt["sha256"] = _sha(freeze_receipt)
    (out / "DISCOVERY_FREEZE.json").write_text(
        json.dumps(freeze_receipt, indent=2, sort_keys=True) + "\n"
    )

    results: list[dict[str, Any]] = []
    for freeze in frozen:
        key = (freeze["book"], freeze["symbol"])
        data, features, event, cover, selected = discovery_cache[key]
        try:
            final = simulate(
                data, features, event, cover, *_windows(data)["FINAL"],
                args.commission_bps, args.slippage_bps,
                emit_schedule=True,
            )
            final_pass = bool(
                not final["insolvent"]
                and final["peak_post_fill_notional_usd"] <= CAPACITY_USD + 1e-6
                and final["technical_exits"] >= 2
                and final["beats_opportunity_benchmark"]
            )
            exact_eligible = bool(
                freeze["discovery_gate_pass"]
                and selected["feature_audit"]["future_htf_sources"] == 0
                and final_pass
            )
            results.append({
                "symbol": freeze["symbol"],
                "side": "SHORT",
                "book": freeze["book"],
                "freeze": freeze,
                "feature_audit": selected["feature_audit"],
                "discovery_folds": selected["discovery_folds"],
                "untouched_final": final,
                "final_gate_pass": final_pass,
                "exact_replay_eligible": exact_eligible,
                "status": (
                    "VECTOR_SURVIVOR_EXACT_PENDING"
                    if exact_eligible else "GRAY_REJECTED"
                ),
            })
        finally:
            data.z.close()
    payload = {
        "manifest": {
            **preregistration,
            "preregistration_sha256": preregistration["sha256"],
            "discovery_freeze_sha256": freeze_receipt["sha256"],
            "discovery_grid_sha256": discovery_grid_receipt["sha256"],
            "tier": "VEC_RESEARCH",
            "matrix_eligible": False,
            "promotion_allowed": False,
            "exact_replay_only_for_survivors": True,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "results": results,
        "errors": errors,
    }
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    lines = [
        "# SHORT-native phase-2 vector result",
        "",
        "D1+D2 selected and froze one row per key before FINAL was simulated.",
        "",
        "| book | key | selected | D1 capital / floor | D2 capital / floor | FINAL capital / floor | account | DD | TIM | exits | reasons | status |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in results:
        d1, d2, final = (
            row["discovery_folds"]["D1"],
            row["discovery_folds"]["D2"],
            row["untouched_final"],
        )
        lines.append(
            f"| {row['book']} | {row['symbol']}_SHORT | "
            f"`{row['freeze']['selected_label']}` | "
            f"{d1['capital_return_pct']:+.2f}/{d1['opportunity_benchmark_pct']:.2f}% | "
            f"{d2['capital_return_pct']:+.2f}/{d2['opportunity_benchmark_pct']:.2f}% | "
            f"{final['capital_return_pct']:+.2f}/{final['opportunity_benchmark_pct']:.2f}% | "
            f"{final['account_return_pct']:+.2f}% | "
            f"{final['max_drawdown_account_pct']:.2f}% | "
            f"{final['time_in_market_pct']:.2f}% | {final['technical_exits']} | "
            f"`{json.dumps(final['exit_reason_counts'], sort_keys=True)}` | "
            f"{row['status']} |"
        )
    if errors:
        lines += ["", "## Data-contract errors", ""]
        lines += [f"- {x['book']} {x['symbol']}: {x['error']}" for x in errors]
    (out / "RESULTS.md").write_text("\n".join(lines) + "\n")
    if args.path_fleet_root:
        payload["fleet_ingest"] = ingest(
            Path(args.path_fleet_root).resolve(), payload, out
        )
        (out / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
    print(json.dumps({
        "artifact": str(out),
        "rows": len(results),
        "errors": len(errors),
        "survivors": sum(x["exact_replay_eligible"] for x in results),
        "fleet": payload.get("fleet_ingest"),
    }, sort_keys=True))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(ROOT / "data/reports/vec_research"))
    ap.add_argument("--correction-symbols", default=",".join(CORRECTION_DEFAULT))
    ap.add_argument("--bear-symbols", default=",".join(BEAR_DEFAULT))
    ap.add_argument("--commission-bps", type=float, default=0.0)
    ap.add_argument("--slippage-bps", type=float, default=2.5)
    ap.add_argument("--path-fleet-root")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
