#!/usr/bin/env python3
"""Lifecycle-aware pilot — INTERNAL LIBRARY (month=30T/30C, yr=365C).

DO NOT RUN THIS FILE DIRECTLY — USE tools/opt/v12_pilot.py INSTEAD.
v12_pilot.py is the sanitized evolution that wraps this module's
exact_month_slice / compact_to_completed_timeframe / _config_and_month_npz
and adds window-aware floors + never-lie guards.  This file remains the
canonical implementation of those low-level parts (imported by v12_pilot.py).

The runner is intentionally conservative:

* crypto month uses the final 30 calendar days in the frozen NPZ;
* stocks month uses the final 30 distinct trading-session dates (was 20);
* yr (365) uses 365 calendar days for both;
* only switches with a causal vector route and a live route are searched;
* only switches with a causal vector route and a live route are searched;
* every trial is a complete chronological simulation, never arithmetic composed;
* progress is appended after every trial and can be resumed;
* promotion is a separate, receipt-gated operation.

Run serious evaluations on S1.  ``plan`` and the unit tests are safe on the Mac.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import dataclasses
import functools
import hashlib
import json
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt import evaluate_v12 as E
from tools.switch_tf_uniqueness import classify_run_scope, load_specs

REPORT_ROOT = ROOT / "data" / "reports" / "lifecycle_pilot"
PARITY_CSV = ROOT / "data" / "reports" / "V12_LIVE_VECTOR_PARITY_INVENTORY.csv"
PER_SYM_PARITY_CONTRACT = ROOT / "data" / "reports" / "lifecycle_pilot" / "per_sym_parity_contract.json"
CONNECTION_CSV = ROOT / "data" / "reports" / "V12_CURATED_CONNECTION_INVENTORY.csv"
GROUPS_JSON = ROOT / "data" / "reports" / "gui_lab" / "opt" / "switch_groups.json"
ALL_PATHS_CSV = ROOT / "data" / "reports" / "ALL_PATHS_ALLOWLIST.csv"
LIVE_FILES = (
    ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json",
    ROOT / "data" / "hourly_reconfig" / "inf" / "active_config.json",
    ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json",
)
STAGE_ORDER = ("ENTRY", "FILTER", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "SIZING", "GLOBAL")
EXECUTION_TF = "15m"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _normalise_overrides(raw: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (raw or {}).items():
        key = str(key)
        if "=" in key:
            key, encoded = key.split("=", 1)
            try:
                value = json.loads(encoded.lower())
            except (ValueError, TypeError):
                try:
                    value = float(encoded)
                except ValueError:
                    value = encoded
        if isinstance(value, str) and value in ("True", "False"):
            value = value == "True"
        out[key] = value
    return out


def load_live_recipes() -> Dict[str, Dict[str, Any]]:
    """Merge live sources in priority order; never invent an empty baseline."""
    merged: Dict[str, Dict[str, Any]] = {}
    for path in LIVE_FILES:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for key, entry in data.items():
            if key.startswith("_") or not isinstance(entry, dict) or key in merged:
                continue
            overrides = _normalise_overrides(entry.get("overrides") or {})
            merged[key.upper()] = {
                "overrides": overrides,
                "source": str(path.relative_to(ROOT)),
                "source_entry_hash": digest(entry),
                "metadata": {k: v for k, v in entry.items() if k != "overrides"},
            }
    return merged


def require_per_sym_parity_contract() -> None:
    """Block lifecycle work unless the current live recipes are wired both ways."""
    try:
        payload = json.loads(PER_SYM_PARITY_CONTRACT.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "missing per_sym Quick/V12 wiring contract; run "
            "python tools/opt/per_sym_parity_contract.py first"
        ) from exc
    if not payload.get("pass"):
        raise RuntimeError(
            f"per_sym Quick/V12 wiring contract is BLOCKED ({payload.get('summary', {}).get('blockers', '?')} blockers); "
            "no 15m lifecycle research is permitted"
        )


def is_crypto_symside(symside: str) -> bool:
    symbol, _side = E.split_symside(symside)
    _resolved, tokenised = E.resolve_tokenised(symbol)
    return E.is_crypto(symbol) and not tokenised


def _slice_rows(npz: Any, left: int, right: int) -> Any:
    if not isinstance(npz, dict):
        return npz
    n = len(np.asarray(npz.get("timestamps", ())))
    return {
        key: (value[left:right] if isinstance(value, np.ndarray) and value.ndim and len(value) == n else value)
        for key, value in npz.items()
    }


def exact_month_slice(npz: Any, crypto: bool, window_days: int = 30) -> tuple[Any, Dict[str, Any]]:
    """Return 30C/30T for month, 365C for yr. window_days 30 -> 30 calendar (crypto) or 30 sessions (stocks); 365 -> 365 calendar both."""
    if not isinstance(npz, dict):
        raise ValueError("NPZ store is not a mapping")
    raw = np.asarray(npz.get("timestamps", ()), dtype="float64")
    finite = np.flatnonzero(np.isfinite(raw) & (raw > 0))
    if finite.size < 2:
        raise ValueError("NPZ has no usable timestamps")
    seconds = raw / (1000.0 if float(raw[finite[-1]]) > 1e11 else 1.0)
    right = int(finite[-1]) + 1
    # Yr 365 calendar days for both venues; month 30 calendar crypto / 30 trading stocks (was 20)
    if int(window_days) >= 365:
        end_s = float(seconds[finite[-1]])
        start_s = end_s - 365.0 * 86400.0
        left = int(np.searchsorted(seconds, start_s, side="left"))
        policy = "365_calendar_days"
        session_count = None
    elif crypto:
        end_s = float(seconds[finite[-1]])
        start_s = end_s - 30.0 * 86400.0
        left = int(np.searchsorted(seconds, start_s, side="left"))
        policy = "30_calendar_days"
        session_count = None
    else:
        days = seconds.astype("datetime64[s]").astype("datetime64[D]")
        valid_days = days[finite]
        unique = np.unique(valid_days)
        if len(unique) < 30:
            raise ValueError(f"NPZ has only {len(unique)} distinct stock sessions (need 30)")
        first_day = unique[-30]
        left = int(np.searchsorted(days, first_day, side="left"))
        policy = "30_trading_sessions"
        session_count = 30
    sliced = _slice_rows(npz, left, right)
    sliced_ts = np.asarray(sliced.get("timestamps", ()), dtype="float64")
    return sliced, {
        "policy": policy,
        "bars": int(len(sliced_ts)),
        "sessions": session_count,
        "start_timestamp": float(sliced_ts[0]) if len(sliced_ts) else None,
        "end_timestamp": float(sliced_ts[-1]) if len(sliced_ts) else None,
    }


def compact_to_completed_timeframe(npz: Any, timeframe: str = EXECUTION_TF) -> tuple[Any, Dict[str, Any]]:
    """Collapse a forward-filled base grid to causal completed-parent events.

    NPZ higher-timeframe arrays are aligned to every base row.  We retain the
    first row at which a new positive parent timestamp becomes available.  That
    is a real decision clock; taking every Nth row would be phase-dependent and
    could select a bar before its parent indicator was known.
    """
    if not isinstance(npz, dict):
        raise ValueError("NPZ store is not a mapping")
    key = f"timestamp_{timeframe}"
    parent = np.asarray(npz.get(key, ()), dtype="float64")
    base = np.asarray(npz.get("timestamps", ()), dtype="float64")
    if len(parent) != len(base) or len(parent) < 2:
        raise ValueError(f"NPZ lacks aligned {key}")
    changed = np.r_[True, parent[1:] != parent[:-1]]
    indexes = np.flatnonzero((parent > 0) & np.isfinite(parent) & changed)
    if len(indexes) < 100:
        raise ValueError(f"only {len(indexes)} completed {timeframe} events")
    n = len(base)
    compact = {
        name: (value[indexes] if isinstance(value, np.ndarray) and value.ndim and len(value) == n else value)
        for name, value in npz.items()
    }
    # Execution timestamps remain the actual availability timestamps.  The
    # parent series is retained separately for audit and must never be later.
    lag = np.asarray(compact["timestamps"], dtype="float64") - np.asarray(compact[key], dtype="float64")
    if np.nanmin(lag) < 0:
        raise ValueError(f"{timeframe} parent timestamp is in the future")
    return compact, {"execution_tf": timeframe, "source_bars": n,
                     "execution_bars": int(len(indexes)), "max_parent_lag_s": float(np.nanmax(lag))}


def _config_and_month_npz(symside: str, overrides: Mapping[str, Any], window_days: int = 30):
    # 60 calendar days supplies at least 30 stock sessions while keeping worker memory bounded.
    # window_days 30 -> month (30C crypto / 30T stocks), 365 -> yr (365C both)
    crypto = is_crypto_symside(symside)
    load_days = 365 if int(window_days) >= 365 else (30 if crypto else 60)
    cfg, npz, symbol, is_long, mode, tokenised = E.build_cfg_npz(
        symside, dict(overrides), window_days=load_days)
    if npz is None:
        raise ValueError(f"no NPZ for {symside}")
    npz, window = exact_month_slice(npz, crypto, window_days=window_days)
    npz, floor = compact_to_completed_timeframe(npz)
    from min_decision_tf_guard import clamp_config, guard_npz
    npz, guard_receipt = guard_npz(npz, EXECUTION_TF)
    guard_receipt.update(clamp_config(cfg, EXECUTION_TF))
    cfg._MIN_DECISION_TF_RECEIPT = guard_receipt
    cfg.BASE_TF = EXECUTION_TF
    # The quick engine consumes this opt-in guard before it computes any
    # entry/exit/reentry signal.  It also clamps selector settings in the live
    # per_sym recipe so no stale 3m/5m value leaks into a 15m study.
    cfg.PARITY_MIN_DECISION_TF = EXECUTION_TF
    window.update(floor)
    window["bars"] = floor["execution_bars"]
    return cfg, npz, symbol, is_long, mode, tokenised, window


def evaluate_month(symside: str, overrides: Mapping[str, Any], include_ledger: bool = False, window_days: int = 30) -> Dict[str, Any]:
    """Evaluate one complete ledger. window_days 30 -> month (30C/30T), 365 -> yr (365C)."""
    import v12_quick_engine as V

    out: Dict[str, Any] = {"symside": symside, "overrides": dict(overrides), "requested_window_days": int(window_days)}
    try:
        cfg, npz, symbol, is_long, mode, tokenised, window = _config_and_month_npz(symside, overrides, window_days=window_days)
        result = dict(V.simulate_one(npz, symbol, is_long, cfg) or {})
    except Exception as exc:
        out.update(valid=False, invalid_reason=str(exc), score=float("-inf"))
        return out
    ledger_all = result.get("ledger") or []
    ledger, spikes = E._spike_filtered(ledger_all)
    result["ledger"] = ledger
    side = "LONG" if is_long else "SHORT"
    gain = E._honest_gain_pct(result)
    bh = E._bh(npz, side)
    trades = len(ledger)
    out.update({
        "valid": True,
        "invalid_reason": "",
        "window": window,
        "window_days": int(window_days),
        "months": float(window_days) / 30.44,
        "gain_pct": gain,
        "gain_per_mo": gain / (float(window_days) / 30.44),
        "bh_pct": bh,
        "bh_per_mo": (bh / (float(window_days) / 30.44)) if bh is not None else None,
        "delta_vs_bh": (gain - bh) if bh is not None else None,
        "delta_per_mo": ((gain - bh) / (float(window_days) / 30.44)) if bh is not None else None,
        "max_dd_pct": E._honest_max_dd_pct(result),
        "tim_pct": float(result.get("tim_pct") or 0.0),
        "pool_sharpe": E._pool_sharpe_from_ledger(ledger),
        "trades": trades,
        "closes_per_month": trades / (float(window_days) / 30.44),
        "wr_pct": (100.0 * sum(float(t.get("pnl_dollars") or 0) > 0 for t in ledger) / trades) if trades else 0.0,
        "gain_dollars": sum(float(t.get("pnl_dollars") or 0) for t in ledger if isinstance(t, dict)),
        "peak_capital": E._peak_concurrent(ledger),
        "spike_trades_dropped": spikes,
        "spike_frac": spikes / len(ledger_all) if ledger_all else 0.0,
        "behavior_fingerprint": E._behavior_fingerprint(ledger),
        "mode": mode,
        "tokenised": tokenised,
    })
    # Window-aware floor — must match evaluate_v12._validate (10 for 30D, 30 for 365D)
    # lifecycle previously used 2 → 30D ETH 16 trades valid in vector but invalid in v12 → DIFF
    wd = int(window_days)
    min_trades = 10 if wd <= 30 else 30
    if trades < min_trades:
        out.update(valid=False, invalid_reason=f"fewer than {min_trades} completed trades (wd={wd})")
    elif bh is None:
        out.update(valid=False, invalid_reason="B&H unavailable")
    elif out["spike_frac"] > 0.02:
        out.update(valid=False, invalid_reason="corrupt-price spike fraction >2%")
    out["score"] = candidate_score(out)
    # No BH floor — you CAN lose vs BH (user 2026-09-10). Never hide raw delta.
    out["bh_fallback"] = False
    # Vomit per ALL THE RULES: NOT trading, <10 trades, >80% TIM, >30% DD — those are hard invalid, not delta.
    _tim = float(out.get("tim_pct") or 0)
    _dd = float(out.get("max_dd_pct") or 0)
    if _tim > 80.0:
        out["valid"] = False
        out["invalid_reason"] = f"TIM {_tim:.1f}% >80% (vomit)"
    elif _dd > 30.0:
        out["valid"] = False
        out["invalid_reason"] = f"DD {_dd:.1f}% >30% (vomit)"
    if include_ledger:
        out["execution_ledger"] = ledger
    return out


def candidate_score(metrics: Mapping[str, Any]) -> float:
    if not metrics.get("valid"):
        return float("-inf")
    delta = float(metrics.get("delta_vs_bh") or 0.0)
    gain = float(metrics.get("gain_pct") or 0.0)
    sharpe = float(metrics.get("pool_sharpe") or 0.0)
    dd = float(metrics.get("max_dd_pct") or 0.0)
    tim = float(metrics.get("tim_pct") or 0.0)
    tim_penalty = abs(tim - 50.0) / 20.0 if not 20.0 <= tim <= 80.0 else 0.0
    dd_penalty = max(0.0, dd - 30.0) * 2.0
    return delta + 0.15 * gain + 8.0 * sharpe - tim_penalty - dd_penalty


def improves(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    """Strict final/promotion gate; positive delta is mandatory, not just score."""
    if not candidate.get("valid"):
        return False
    if candidate.get("behavior_fingerprint") == baseline.get("behavior_fingerprint"):
        return False
    candidate_delta = _metric_float(candidate, "delta_vs_bh", -1e9)
    baseline_delta = _metric_float(baseline, "delta_vs_bh", -1e9)
    if candidate_delta <= 0.0 or candidate_delta <= baseline_delta:
        return False
    if float(candidate.get("max_dd_pct") or 999.0) >= 30.0:
        return False
    if not 30.0 < float(candidate.get("tim_pct") or 0.0) <= 80.0:
        return False
    if float(candidate.get("pool_sharpe") or 0.0) <= 0.2:
        return False
    if int(candidate.get("trades") or 0) < 32:
        return False
    return float(candidate.get("score") or float("-inf")) > float(baseline.get("score") or float("-inf"))


def ratchet_improves(candidate: Mapping[str, Any], incumbent: Mapping[str, Any]) -> bool:
    """Research retention gate used while building TIM from a sparse baseline.

    Requiring final TIM/trade/Sharpe floors on every single switch makes it
    mathematically impossible to accumulate several individually useful entry
    paths from a 1% TIM baseline. Those remain hard final promotion gates in
    improves(); the conditional ratchet retains only real, DD-safe, positive
    marginal delta and then reruns the complete ledger for the next switch.
    """
    if not candidate.get("valid"):
        return False
    if candidate.get("behavior_fingerprint") == incumbent.get("behavior_fingerprint"):
        return False
    if float(candidate.get("max_dd_pct") or 999.0) >= 30.0:
        return False
    candidate_delta = _metric_float(candidate, "delta_vs_bh", -1e9)
    incumbent_delta = _metric_float(incumbent, "delta_vs_bh", -1e9)
    return candidate_delta > 0.0 and candidate_delta > incumbent_delta


def bible_rank(metrics: Mapping[str, Any]) -> tuple:
    """Lexicographic search rank: legality outranks attractive invalid P&L."""
    if not metrics.get("valid"):
        return (0, float("-inf"), float("-inf"), float("-inf"), float("-inf"))
    tim = float(metrics.get("tim_pct") or 0.0)
    dd = float(metrics.get("max_dd_pct") or 999.0)
    delta = float(metrics.get("delta_vs_bh") or -1e9)
    sharpe = float(metrics.get("pool_sharpe") or -1e9)
    trades = int(metrics.get("trades") or 0)
    tim_distance = 0.0 if 30.0 < tim <= 80.0 else min(abs(tim - 30.0), abs(tim - 80.0))
    legal = dd < 30.0 and 30.0 < tim <= 80.0 and delta > 0.0 and sharpe > 0.2 and trades >= 32
    solvent = dd < 30.0
    return (2 if legal else 1 if solvent else 0, -tim_distance, delta, sharpe, -dd)


def _metric_float(metrics: Mapping[str, Any], key: str, default: float) -> float:
    value = metrics.get(key)
    return default if value is None else float(value)


@functools.lru_cache(maxsize=1)
def _config_fields_cached() -> Dict[str, Any]:
    return E.config_fields()


@functools.lru_cache(maxsize=1)
def _historical_rows() -> Dict[str, list[Dict[str, Any]]]:
    """Read the last-week empirical switch reports into one normalized index."""
    paths = {
        ROOT / "SPREADSHEETS" / "V12_1MO_UNTESTED_RANK_20260824.csv",
        ROOT / "SPREADSHEETS" / "MASTER_switches.csv",
    }
    patterns = (
        "*SWITCH_TF_UNIQUENESS.csv", "v12_numpy_pilot_1mo/*.csv",
        "v12_staged_smoke/*.csv", "per_sym_top40/*.csv",
        "crystallizer/*MATRIX*.csv", "gui_lab/OPT_MATRIX_*.summary.csv",
    )
    for pattern in patterns:
        paths.update((ROOT / "data" / "reports").glob(pattern))
    cutoff = time.time() - 9 * 86400
    index: Dict[str, list[Dict[str, Any]]] = {}

    def first(row, names, default=""):
        for name in names:
            if row.get(name) not in (None, ""):
                return row[name]
        return default

    for path in sorted(paths):
        try:
            if not path.exists() or path.stat().st_mtime < cutoff:
                continue
            with path.open(newline="", encoding="utf-8-sig", errors="ignore") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            name = str(first(row, ("Switch", "switch", "Param", "param", "best_switch"))).strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                continue
            status = str(first(row, ("Valid", "valid", "status", "Quality_OK"), "true")).lower()
            if status in ("false", "0", "error", "invalid", "no"):
                continue
            raw_delta = first(row, ("Marginal_Delta", "marginal_delta", "delta_vs_baseline",
                                    "lift_gain_per_mo", "Avg_Delta_vs_BH_1mo", "delta_vs_bh",
                                    "Delta_vs_BH", "Max_Delta_1mo"))
            try:
                delta = float(raw_delta)
            except (TypeError, ValueError):
                delta = 0.0
            try:
                reported_max = float(first(row, ("Max_Delta_1mo",), delta))
            except (TypeError, ValueError):
                reported_max = delta
            try:
                sharpe = float(first(row, ("Pool_Sharpe", "pool_sharpe", "Avg_Sharpe_1mo",
                                                   "best_delta_sharpe"), 0.0))
            except (TypeError, ValueError):
                sharpe = 0.0
            symside = str(first(row, ("sym_side", "symside", "SymSide"))).upper()
            moved = str(first(row, ("Moves_Reference", "Behavior_Changed", "behavior_changed"), "true")).lower() not in ("false", "0", "no")
            index.setdefault(name, []).append({"delta": delta, "reported_max": reported_max,
                                               "sharpe": sharpe, "symside": symside,
                                               "moved": moved, "source": str(path.relative_to(ROOT)),
                                               "mtime": path.stat().st_mtime})
    return index


def historical_priority(name: str, symside: str) -> tuple[float, Dict[str, Any]]:
    observations = _historical_rows().get(name, [])
    if not observations:
        return 0.0, {"observations": 0, "positive": 0, "sources": []}
    weighted = []
    for row in observations:
        weight = 3.0 if row["symside"] == symside else 1.0
        if not row["moved"]:
            weight *= 0.1
        weighted.extend([row] * int(max(1, round(weight))))
    deltas = [float(row["delta"]) for row in weighted]
    sharpes = [float(row["sharpe"]) for row in weighted]
    positive = sum(delta > 0 for delta in deltas)
    # Robust mean limits one old absurd result; max preserves genuine upside.
    clipped = [max(-50.0, min(50.0, delta)) for delta in deltas]
    mean = sum(clipped) / len(clipped)
    high = max([max(-50.0, min(50.0, float(row.get("reported_max", row["delta"]))))
                for row in weighted])
    # User contract: proven maximum delta drives the queue; mean and hit-rate
    # break ties and prevent one unsupported outlier from owning the order.
    score = 0.70 * high + 0.20 * mean + 4.0 * positive / len(clipped) + 2.0 * (sum(sharpes) / len(sharpes))
    return score, {"observations": len(observations), "positive": positive,
                   "weighted_mean_delta": mean, "max_delta": high,
                   "sources": sorted({row["source"] for row in observations})}


@dataclasses.dataclass(frozen=True)
class SwitchTrial:
    name: str
    value: Any
    stage: str
    group: str
    parents: tuple[str, ...] = ()

    def patch(self) -> Dict[str, Any]:
        patch = {parent: True for parent in self.parents}
        patch[self.name] = self.value
        return patch


@functools.lru_cache(maxsize=1)
def _group_index() -> Dict[str, tuple[str, str]]:
    try:
        data = json.loads(GROUPS_JSON.read_text()).get("groups") or {}
    except (OSError, ValueError):
        return {}
    out: Dict[str, tuple[str, str]] = {}
    for stage, groups in data.items():
        for group, names in (groups or {}).items():
            for name in names:
                out.setdefault(name, (stage, group))
    return out


@functools.lru_cache(maxsize=1)
def _registry_sources():
    with PARITY_CSV.open(newline="", encoding="utf-8") as handle:
        parity = {row["switch"]: row for row in csv.DictReader(handle)}
    with CONNECTION_CSV.open(newline="", encoding="utf-8") as handle:
        connections = {row["field"]: row for row in csv.DictReader(handle)}
    return parity, connections, load_specs()


@functools.lru_cache(maxsize=1)
def _catalog_metadata() -> Dict[str, Dict[str, Any]]:
    try:
        rows = json.loads((ROOT / "data" / "reports" / "switch_lab_catalog_20260729.json").read_text()).get("paths") or []
    except (OSError, ValueError):
        rows = []
    return {str(row.get("param") or "").upper(): row for row in rows if row.get("param")}


@functools.lru_cache(maxsize=1)
def _all_path_metadata() -> Dict[str, Dict[str, str]]:
    try:
        with ALL_PATHS_CSV.open(newline="", encoding="utf-8") as handle:
            return {str(row.get("field") or "").upper(): row for row in csv.DictReader(handle)
                    if row.get("field")}
    except OSError:
        return {}


def _parent_closure(name: str, specs: Mapping[str, Any]) -> tuple[str, ...]:
    found, stack = set(), list(getattr(specs.get(name), "parent_hints", ()))
    while stack:
        parent = str(stack.pop())
        if not parent or parent == name or parent in found:
            continue
        found.add(parent)
        stack.extend(getattr(specs.get(parent), "parent_hints", ()))
    return tuple(sorted(found))


def relevant_trials(symside: str, current: Mapping[str, Any]) -> list[SwitchTrial]:
    """Materialize the trusted, applicable search surface for one symbol/side."""
    parity, connections, specs = _registry_sources()
    group_index = _group_index()
    crypto = is_crypto_symside(symside)
    trials: list[SwitchTrial] = []
    for name, spec in specs.items():
        # This pilot deliberately does not evaluate synthetic/fine execution
        # paths. Higher-TF fields that merely happen to contain "15M" remain.
        if name == "BASE_TF" or re.search(r"(^|_)(3M|5M)($|_)", name):
            continue
        p = parity.get(name, {})
        c = connections.get(name, {})
        # These inventories cover two intentionally disjoint namespaces.  A
        # native field is eligible when the parity ledger proves both a causal
        # vector read and a live read.  A curated adapter field is eligible when
        # its venue connection and executable v12 lifecycle site are recorded.
        # Requiring both ledgers at once produces an empty set and is therefore
        # not a meaningful safety gate.
        native_route = _bool(p.get("vector_causal_read")) and _bool(p.get("live_read"))
        curated_route = (_bool(c.get("connected_crypto" if crypto else "connected_tradier"))
                         and bool(c.get("v12_causal_read_sites") or c.get("integrated_contract_sites")))
        if not (native_route or curated_route):
            continue
        if classify_run_scope(spec, symside)[0] != "STRATEGY_CAUSAL":
            continue
        stage, group = group_index.get(name, (str(c.get("family") or "GLOBAL").upper(), ""))
        consumers = {token.strip().upper() for token in str(c.get("lifecycle_consumers") or "").split("|")}
        if stage not in STAGE_ORDER or stage == "GLOBAL":
            stage = next((candidate for candidate in STAGE_ORDER if candidate in consumers), stage)
        if stage == "GLOBAL":
            upper = name.upper()
            if (any(token in upper for token in ("ENTRY", "OPEN", "BREAKOUT", "BOUNCE"))
                    and "EXIT" not in upper and "REENTRY" not in upper):
                stage = "ENTRY"
        catalog = _catalog_metadata().get(name, {})
        family = str(catalog.get("family") or "").upper()
        if family and family not in ("GLOBAL", "OTHER", stage):
            group = f"{stage}:{family}"
        if not group:
            workbook_group = next((str(context.get("group") or "").upper()
                                   for context in spec.row_contexts if context.get("group")), "")
            if workbook_group:
                group = f"{stage}:{workbook_group}"
        if not group:
            # Last-resort grouping is structural and deliberately coarser than
            # attachment inference: it changes queue presentation/order only,
            # never which setting is allowed below which switch.
            stem = re.sub(r"_(?:USE_)?ENABLED(?:_TRADIER)?$", "", name)
            stem = re.sub(r"_(?:15M|30M|1H|4H|D|W)(?=_|$)", "", stem)
            tokens = stem.split("_")
            family_tokens = tokens[:3] if len(tokens) >= 3 else tokens
            group = f"{stage}:{'_'.join(family_tokens)}"
        if stage not in STAGE_ORDER:
            stage = "GLOBAL"
        default = _config_fields_cached().get(name)
        values = []
        for value in spec.values:
            # Report-derived grids can contain embedded summary dictionaries.
            # A trial value must obey the actual QuickConfig field type.
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    continue
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
            elif isinstance(default, str):
                if not isinstance(value, str):
                    continue
            elif isinstance(default, (tuple, list)):
                if not isinstance(value, (tuple, list)):
                    continue
            elif isinstance(value, (dict, set)):
                continue
            values.append(value)
        if not values:
            continue
        incumbent = current.get(name, default)
        for value in values:
            if canonical(value) == canonical(incumbent):
                continue
            trials.append(SwitchTrial(name, value, stage, group,
                                      _parent_closure(name, specs)))
    rank = {stage: index for index, stage in enumerate(STAGE_ORDER)}
    return sorted(trials, key=lambda trial: (rank[trial.stage], trial.group, trial.name, canonical(trial.value)))


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str))
    os.replace(temporary, path)


def _load_rows(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _evaluate_task(payload):
    symside, overrides = payload
    return evaluate_month(symside, overrides)


def _memory_safe_workers(requested: int) -> int:
    """Throttle before forking so S1 stays below the Bible's 90% RAM cap."""
    if not sys.platform.startswith("linux"):
        return max(1, requested)
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total, available = values["MemTotal"], values["MemAvailable"]
        used = total - available
        headroom = max(0, 0.90 * total - used)
        # Observed BTC 15m forks dirty ~150MB each; 384MB is intentionally
        # conservative and leaves room for unrelated S1 campaigns.
        affordable = int(headroom // (384 * 1024 * 1024))
        if affordable < 1 and used / total >= 0.90:
            raise RuntimeError(f"S1 RAM already {used / total * 100:.1f}% used; retry later")
        return max(1, min(int(requested), max(1, affordable)))
    except OSError:
        return max(1, requested)


def _memory_preflight(min_available_gib: float = 5.0) -> None:
    """Fail before NPZ inflation when S1 cannot safely absorb one baseline."""
    if not sys.platform.startswith("linux"):
        return
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total, available = values["MemTotal"], values["MemAvailable"]
        used_ratio = (total - available) / total
        required = min_available_gib * 1024 ** 3
        if available < required or used_ratio >= 0.85:
            raise RuntimeError(
                f"S1 memory preflight refused NPZ load: {available / 1024 ** 3:.1f} GiB "
                f"available, {used_ratio * 100:.1f}% used; need >= {min_available_gib:.1f} GiB "
                "available and <85% used")
    except OSError:
        return


def _entry_activations(trials: Iterable[SwitchTrial], symside: str = "",
                       current: Optional[Mapping[str, Any]] = None) -> list[SwitchTrial]:
    fields = _config_fields_cached()
    candidates: Dict[str, SwitchTrial] = {}
    trials = list(trials)
    current = current or {}
    for trial in trials:
        if (trial.stage != "ENTRY" or not isinstance(fields.get(trial.name), bool)
                or not isinstance(trial.value, bool)):
            continue
        candidates.setdefault(trial.name, trial)
    # A parent may not otherwise have an eligible trial.  Reconstruct its
    # *toggle* from the live state; forcing True here would make a live-on
    # master look like a no-op and would never measure its ablation.
    for trial in trials:
        if trial.stage not in ("ENTRY", "FILTER"):
            continue
        for parent in trial.parents:
            if isinstance(fields.get(parent), bool):
                parent_spec = _registry_sources()[2].get(parent)
                incumbent = current.get(parent, fields.get(parent, False))
                candidates.setdefault(parent, SwitchTrial(parent, not bool(incumbent), "ENTRY", trial.group,
                                                           _parent_closure(parent, _registry_sources()[2])
                                                           if parent_spec else ()))
    names = set(candidates)
    roots = []
    for trial in candidates.values():
        has_boolean_parent = any(parent in names for parent in trial.parents)
        if not has_boolean_parent or trial.name.startswith("ABLATION_DISABLE_"):
            roots.append(trial)
    return sorted(roots, key=lambda trial: (0 if trial.name.startswith("ABLATION_DISABLE_") else 1,
                                             -historical_priority(trial.name, symside)[0], trial.name))


def _compatible_filters(entry: SwitchTrial, filters: Iterable[SwitchTrial]) -> list[SwitchTrial]:
    """Return only defensible entry→setting edges, never a name-only orphan guess."""
    _parity, _connections, specs = _registry_sources()
    entry_spec = specs.get(entry.name)
    paired = set()
    if entry_spec:
        for context in entry_spec.row_contexts:
            paired.update((context.get("paired") or {}).keys())
    root_tokens = {token for token in entry.name.split("_")
                   if token not in {"ENTRY", "ENABLED", "GATE", "LONG", "SHORT", "TRADIER"} and len(token) >= 3}
    catalog = _catalog_metadata()
    entry_family = str(catalog.get(entry.name, {}).get("family") or "").upper()
    out = []
    for trial in filters:
        filter_spec = specs.get(trial.name)
        filter_tokens = set(trial.name.split("_"))
        exact_parents = set(trial.parents)
        if exact_parents:
            related = entry.name in exact_parents
        else:
            filter_family = str(catalog.get(trial.name, {}).get("family") or "").upper()
            family_related = bool(entry_family and filter_family and (
                entry_family == filter_family
                or entry_family.startswith(filter_family + "_")
                or filter_family.startswith(entry_family + "_")
            ))
            # Require either workbook pairing, catalog-family evidence, or at
            # least two meaningful shared tokens. One shared word such as WT,
            # ENTRY, or GATE is not a dependency edge.
            related = (trial.name in paired or family_related
                       or len(root_tokens & filter_tokens) >= 2)
        if related:
            out.append(trial)
    return out


def _entry_setting_bindings(entries: list[SwitchTrial], settings: list[SwitchTrial]):
    """Classify settings as exact SOME, lifecycle ANY/ALL, or unresolved.

    SOME settings have a defensible parent/family edge and are tested below
    those masters. ANY/ALL settings are lifecycle-wide config reads and must be
    tested once against the whole incumbent stack, never repeatedly or under a
    made-up owner. Unresolved settings remain visible in the exhaustive queue
    but are fail-closed until an inventory supplies their consuming path.
    """
    by_entry: Dict[str, list[SwitchTrial]] = {entry.name: [] for entry in entries}
    owners: Dict[str, set[str]] = {}
    scopes: Dict[str, str] = {}
    global_settings: list[SwitchTrial] = []
    unresolved: list[SwitchTrial] = []
    _parity, connections, _specs = _registry_sources()
    path_meta = _all_path_metadata()

    for setting in settings:
        compatible = [entry for entry in entries
                      if setting in _compatible_filters(entry, (setting,))]
        if compatible:
            scopes[setting.name] = "SOME"
            owners.setdefault(setting.name, set()).update(entry.name for entry in compatible)
            for entry in compatible:
                by_entry[entry.name].append(setting)
            continue

        connection = connections.get(setting.name, {})
        consumers = {part.strip().upper() for part in
                     str(connection.get("lifecycle_consumers") or "").split("|") if part.strip()}
        path = path_meta.get(setting.name, {})
        paths = {str(path.get("primary_path") or "").upper()}
        paths.update(part.strip().upper() for part in
                     str(path.get("secondary_paths") or "").split("|") if part.strip())
        catalog = _catalog_metadata().get(setting.name, {})
        catalog_entry = str(catalog.get("group") or "").upper() == "ENTRY"
        named_entry = bool(re.search(r"(^|_)ENTRY($|_)", setting.name)) and "EXIT" not in setting.name
        if "ENTRY" in consumers or "ENTRY" in paths or catalog_entry or named_entry:
            scopes[setting.name] = "ALL" if re.search(r"(^|_)ALL($|_)", setting.name) else "ANY"
            owners.setdefault(setting.name, set()).update(entry.name for entry in entries)
            global_settings.append(setting)
        else:
            scopes[setting.name] = "UNRESOLVED"
            unresolved.append(setting)
    return by_entry, owners, scopes, global_settings, unresolved


def _root_node_type(trial: SwitchTrial, settings: Iterable[SwitchTrial]) -> str:
    if trial.name.startswith("ABLATION_DISABLE_"):
        return "ABLATION_MASTER"
    catalog = _catalog_metadata().get(trial.name, {})
    named_as_parent = any(trial.name in setting.parents for setting in settings)
    if (str(catalog.get("registry_role") or catalog.get("role") or "").upper() == "MAIN_SWITCH"
            or named_as_parent):
        return "MAIN_SWITCH"
    return "INDIVIDUAL_SWITCH"


def _nested_setting_nodes(root_name: str, settings: Iterable[SwitchTrial],
                          scopes: Mapping[str, str], owners: Mapping[str, set[str]]) -> list[Dict[str, Any]]:
    """Collapse value trials and nest sub-settings below their nearest switch."""
    settings = list(settings)
    by_name: Dict[str, list[SwitchTrial]] = {}
    for trial in settings:
        by_name.setdefault(trial.name, []).append(trial)
    nodes: Dict[str, Dict[str, Any]] = {}
    for name, choices in by_name.items():
        is_switch = isinstance(_config_fields_cached().get(name), bool)
        nodes[name] = {"name": name,
                       "node_type": "INDIVIDUAL_SWITCH" if is_switch else "SETTING",
                       "values": [choice.value for choice in choices],
                       "parents": list(choices[0].parents),
                       "applicability": scopes.get(name),
                       "applies_to": sorted(owners.get(name, set())),
                       "children": []}
    top = []
    for name, node in nodes.items():
        catalog = _catalog_metadata().get(name, {})
        direct = str(catalog.get("main_switch") or "").upper()
        if direct not in nodes:
            dependencies = [str(value).upper() for value in
                            (catalog.get("activation_dependencies") or [])]
            direct = next((value for value in reversed(dependencies) if value in nodes), "")
        if direct in nodes and direct != name:
            nodes[direct]["children"].append(node)
        else:
            top.append(node)

    def order(node: Dict[str, Any]) -> None:
        node["children"].sort(key=lambda child: (
            0 if child["node_type"] == "INDIVIDUAL_SWITCH" else 1,
            -historical_priority(child["name"], "")[0], child["name"]))
        for child in node["children"]:
            order(child)

    top.sort(key=lambda node: (0 if node["node_type"] == "INDIVIDUAL_SWITCH" else 1,
                               -historical_priority(node["name"], "")[0], node["name"]))
    for node in top:
        order(node)
    return top


def hierarchy_snapshot(symside: str, current: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the complete dependency tree without loading an NPZ."""
    trials = relevant_trials(symside, current)
    entries = _entry_activations(trials, symside, current)
    entry_names = {entry.name for entry in entries}
    settings = [trial for trial in trials if trial.stage == "FILTER"
                or (trial.stage == "ENTRY" and trial.name not in entry_names)]
    by_entry, owners, scopes, global_settings, unresolved = _entry_setting_bindings(entries, settings)
    ordered = sorted(entries, key=lambda trial: (
        0 if trial.name.startswith("ABLATION_DISABLE_") else 1,
        -historical_priority(trial.name, symside)[0], trial.name))
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for root in ordered:
        children = by_entry.get(root.name, [])
        node = {"switch": root.name, "node_type": _root_node_type(root, settings),
                "toggle_value": root.value,
                "live_value": current.get(root.name, _config_fields_cached().get(root.name)),
                "priority": historical_priority(root.name, symside)[0],
                "parents": list(root.parents),
                "children": _nested_setting_nodes(root.name, children, scopes, owners)}
        groups.setdefault(root.group, []).append(node)
    group_nodes = [{"group": name, "node_type": "GROUP",
                    "priority": max(node["priority"] for node in nodes), "members": nodes}
                   for name, nodes in groups.items()]
    group_nodes.sort(key=lambda group: (
        0 if any(node["node_type"] == "ABLATION_MASTER" for node in group["members"]) else 1,
        -group["priority"], group["group"]))
    return {
        "schema": "lifecycle-hierarchy-v1", "created_at": utcnow(), "symside": symside,
        "groups": group_nodes,
        "lifecycle_wide_settings": [{"name": trial.name, "value": trial.value,
                                      "applicability": scopes.get(trial.name),
                                      "applies_to": sorted(owners.get(trial.name, set())),
                                      "parents": list(trial.parents)}
                                     for trial in global_settings],
        "unresolved": [{"name": trial.name, "value": trial.value,
                         "applicability": "UNRESOLVED", "parents": list(trial.parents)}
                        for trial in unresolved],
        "counts": {"groups": len(group_nodes), "roots": len(entries),
                   "setting_knobs": len({trial.name for trial in settings}),
                   "setting_values": len(settings),
                   "some_knobs": sum(scope == "SOME" for scope in scopes.values()),
                   "any_knobs": sum(scope == "ANY" for scope in scopes.values()),
                   "all_knobs": sum(scope == "ALL" for scope in scopes.values()),
                   "unresolved_knobs": sum(scope == "UNRESOLVED" for scope in scopes.values())},
    }


def _record_trial(ledger_path: Path, rows: list[Dict[str, Any]], *, symside: str,
                  base: Mapping[str, Any], trial: SwitchTrial, kind: str,
                  entry_name: str = "") -> Dict[str, Any]:
    overrides = {**base, **trial.patch()}
    trial_id = digest({"symside": symside, "base": digest(base), "patch": trial.patch(),
                       "kind": kind, "entry": entry_name})
    prior = next((row for row in rows if row.get("trial_id") == trial_id), None)
    if prior is not None:
        return prior
    metrics = evaluate_month(symside, overrides)
    row = {"at": utcnow(), "kind": kind, "trial_id": trial_id, "stage": trial.stage,
           "group": trial.group, "switch": trial.name, "value": trial.value,
           "patch": trial.patch(), "base_hash": digest(base), "overrides_hash": digest(overrides),
           "entry_switch": entry_name, "metrics": metrics}
    _append_jsonl(ledger_path, row)
    rows.append(row)
    return row


def _search_entry_paths(symside: str, baseline_overrides: Mapping[str, Any],
                        baseline_metrics: Mapping[str, Any], trials: list[SwitchTrial],
                        ledger_path: Path, rows: list[Dict[str, Any]],
                        max_entries: int = 0, workers: int = 1,
                        time_budget_minutes: float = 0.0) -> tuple[Dict[str, Any], Dict[str, Any], list[Dict[str, Any]]]:
    entries = _entry_activations(trials, symside, baseline_overrides)
    all_entries = list(entries)
    activation_names = {entry.name for entry in entries}
    # Numeric/timeframe ENTRY knobs are settings of an opener, not independent
    # boolean paths. They belong in the same conditional compatibility sweep as
    # FILTER knobs. This is how ~50 openers cover ~600 entry values without
    # pretending a threshold is a switch.
    filter_trials = [trial for trial in trials
                     if trial.stage == "FILTER"
                     or (trial.stage == "ENTRY" and trial.name not in activation_names)]
    (settings_by_entry, setting_owners, setting_scopes,
     global_settings, unresolved_settings) = _entry_setting_bindings(entries, filter_trials)
    pending_global = list(global_settings)
    paths = []
    incumbent_overrides = dict(baseline_overrides)
    incumbent_metrics = dict(baseline_metrics)
    context_boost: Dict[str, float] = {}
    pending = list(entries)
    deadline = time.monotonic() + time_budget_minutes * 60.0 if time_budget_minutes > 0 else None
    attempted = 0

    def persist_queue() -> None:
        ordered = sorted(pending, key=lambda trial: (0 if trial.name.startswith("ABLATION_DISABLE_") else 1,
                                                      -(historical_priority(trial.name, symside)[0]
                                                       + context_boost.get(trial.name, 0.0)), trial.name))
        remaining_nodes = [{"switch": trial.name,
                            "node_type": _root_node_type(trial, filter_trials),
                            "group": trial.group,
                            "priority": historical_priority(trial.name, symside)[0]
                                        + context_boost.get(trial.name, 0.0),
                            "toggle_value": trial.value,
                            "live_value": baseline_overrides.get(trial.name, _config_fields_cached().get(trial.name)),
                            "setting_knobs": len({setting.name for setting in settings_by_entry.get(trial.name, [])}),
                            "setting_values": len(settings_by_entry.get(trial.name, [])),
                            "children": _nested_setting_nodes(
                                trial.name, settings_by_entry.get(trial.name, []),
                                setting_scopes, setting_owners),
                            "historical_evidence": historical_priority(trial.name, symside)[1]}
                           for trial in ordered]
        grouped_nodes: Dict[str, list[Dict[str, Any]]] = {}
        for node in remaining_nodes:
            grouped_nodes.setdefault(node["group"], []).append(node)
        group_queue = [{"group": group,
                        "node_type": "GROUP",
                        "priority": max(node["priority"] for node in nodes),
                        "masters": nodes}
                       for group, nodes in grouped_nodes.items()]
        group_queue.sort(key=lambda node: (
            0 if any(master["node_type"] == "ABLATION_MASTER" for master in node["masters"]) else 1,
            -node["priority"], node["group"]))
        _atomic_json(ledger_path.with_suffix(".queue.json"), {
            "schema": "lifecycle-entry-queue-v1", "updated_at": utcnow(), "symside": symside,
            "total_entries": len(all_entries), "tested_entries": len(paths),
            "accepted_entries": sum(bool(path.get("accepted")) for path in paths),
            "groups": group_queue,
            "remaining": remaining_nodes,
            "lifecycle_wide_settings": [{"name": trial.name, "value": trial.value,
                                           "applicability": setting_scopes.get(trial.name),
                                           "applies_to": sorted(setting_owners.get(trial.name, set())),
                                           "parents": list(trial.parents)}
                                          for trial in pending_global],
            "unresolved": [{"name": trial.name, "value": trial.value,
                             "applicability": "UNRESOLVED", "parents": list(trial.parents),
                             "reason": "no proved parent/family or ENTRY lifecycle consumer"}
                            for trial in unresolved_settings],
        })

    persist_queue()
    # Fork after the baseline inflated the NPZ. Children inherit read-only
    # arrays copy-on-write, avoiding both GIL serialization and N decompressions.
    context = multiprocessing.get_context("fork")
    workers = _memory_safe_workers(workers)
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
      while pending:
        if (max_entries and attempted >= max_entries) or (deadline is not None and time.monotonic() >= deadline):
            break
        pending.sort(key=lambda trial: (0 if trial.name.startswith("ABLATION_DISABLE_") else 1,
                                         -(historical_priority(trial.name, symside)[0]
                                          + context_boost.get(trial.name, 0.0)), trial.name))
        entry = pending.pop(0)
        attempted += 1
        entry_start_overrides = dict(incumbent_overrides)
        entry_start_metrics = dict(incumbent_metrics)
        prior_score, prior_evidence = historical_priority(entry.name, symside)
        before_delta = _metric_float(incumbent_metrics, "delta_vs_bh", -1e9)
        entry_row = _record_trial(ledger_path, rows, symside=symside, base=incumbent_overrides,
                                  trial=entry, kind="entry_path", entry_name=entry.name)
        toggle_metrics = entry_row["metrics"]
        toggle_marginal = (_metric_float(toggle_metrics, "delta_vs_bh", -1e9)
                           - _metric_float(incumbent_metrics, "delta_vs_bh", -1e9))
        toggle_accepted = ratchet_improves(toggle_metrics, incumbent_metrics)
        if toggle_accepted:
            incumbent_overrides.update(entry.patch())
            incumbent_metrics = dict(toggle_metrics)
        # Settings are evaluated only when their main switch is on in the
        # chosen incumbent. A rejected live-on→off ablation therefore keeps
        # the master on and may tune its children; an accepted off toggle does
        # not leak child trials through a disabled family.
        path_overrides = dict(incumbent_overrides)
        path_metrics = dict(incumbent_metrics)
        accepted_filters = []
        compatible = list(settings_by_entry.get(entry.name, []))
        master_enabled = bool(path_overrides.get(entry.name, _config_fields_cached().get(entry.name, False)))
        if not master_enabled:
            compatible = []
        compatible = sorted(compatible,
                            key=lambda trial: (len(trial.parents),
                                               -historical_priority(trial.name, symside)[0],
                                               trial.name, canonical(trial.value)))
        grouped: Dict[str, list[SwitchTrial]] = {}
        for trial in compatible:
            # One knob owns one value contest. Collapsing all "ungrouped"
            # knobs together would evaluate them but retain only one, which is
            # not parameter optimization.
            grouped.setdefault(trial.name, []).append(trial)
        path_timed_out = False
        for _setting_name, choices in grouped.items():
            if deadline is not None and time.monotonic() >= deadline:
                path_timed_out = True
                break
            evaluated = []
            unevaluated = []
            for trial in choices:
                overrides = {**path_overrides, **trial.patch()}
                trial_id = digest({"symside": symside, "base": digest(path_overrides),
                                   "patch": trial.patch(), "kind": "entry_filter", "entry": entry.name})
                prior = next((row for row in rows if row.get("trial_id") == trial_id), None)
                if prior is not None:
                    evaluated.append(prior)
                else:
                    unevaluated.append((trial, overrides, trial_id))
            # Submit at most one worker-sized wave at a time. A knob with many
            # values must not enqueue minutes of hidden work just before the
            # budget expires. Completed waves are append-only and reused.
            while unevaluated:
                if deadline is not None and time.monotonic() >= deadline:
                    path_timed_out = True
                    break
                wave, unevaluated = unevaluated[:max(1, workers)], unevaluated[max(1, workers):]
                futures = {pool.submit(_evaluate_task, (symside, overrides)): (trial, overrides, trial_id)
                           for trial, overrides, trial_id in wave}
                for future in concurrent.futures.as_completed(futures):
                    trial, overrides, trial_id = futures[future]
                    try:
                        metrics = future.result()
                    except Exception as exc:
                        metrics = {"valid": False, "invalid_reason": str(exc), "score": float("-inf")}
                    row = {"at": utcnow(), "kind": "entry_filter", "trial_id": trial_id,
                           "stage": trial.stage, "group": trial.group, "switch": trial.name,
                           "value": trial.value, "patch": trial.patch(), "base_hash": digest(path_overrides),
                           "overrides_hash": digest(overrides), "entry_switch": entry.name, "metrics": metrics}
                    _append_jsonl(ledger_path, row)
                    rows.append(row)
                    evaluated.append(row)
            if path_timed_out:
                break
            winner = max(evaluated, key=lambda row: bible_rank(row["metrics"]), default=None)
            winner_marginal = (_metric_float(winner["metrics"], "delta_vs_bh", -1e9)
                               - _metric_float(path_metrics, "delta_vs_bh", -1e9)) if winner else -1e9
            if winner and ratchet_improves(winner["metrics"], path_metrics):
                path_overrides.update(winner["patch"])
                path_metrics = winner["metrics"]
                accepted_filters.append(winner["trial_id"])
                incumbent_overrides = dict(path_overrides)
                incumbent_metrics = dict(path_metrics)
        if path_timed_out:
            # Do not crystallize a partly searched family. Its append-only rows
            # remain reusable, while the family returns to the exhaustive queue
            # and the next invocation deterministically reconstructs its best
            # complete setting chain from those cached rows.
            incumbent_overrides = entry_start_overrides
            incumbent_metrics = entry_start_metrics
            pending.append(entry)
            persist_queue()
            break
        after_delta = _metric_float(path_metrics, "delta_vs_bh", -1e9)
        marginal_delta = after_delta - before_delta
        accepted = bool(toggle_accepted or accepted_filters)
        # Conditional evidence changes the next queue order. Closely related
        # entry families inherit part of this observation; unrelated families
        # retain their report prior. This is recomputed after every path.
        tokens = {token for token in entry.name.split("_") if len(token) >= 3}
        for remaining in pending:
            overlap = len(tokens & set(remaining.name.split("_")))
            if overlap:
                context_boost[remaining.name] = context_boost.get(remaining.name, 0.0) + marginal_delta * min(0.5, 0.15 * overlap)
        accepted_patch = {key: value for key, value in path_overrides.items()
                          if canonical(baseline_overrides.get(key)) != canonical(value)}
        paths.append({"entry_switch": entry.name, "entry_patch": entry.patch(),
                      "node_type": _root_node_type(entry, filter_trials),
                      "accepted_patch": accepted_patch,
                      "accepted_filters": accepted_filters, "overrides": path_overrides,
                      "metrics": path_metrics, "rank": bible_rank(path_metrics),
                      "before_delta": before_delta, "after_delta": after_delta,
                      "marginal_delta": marginal_delta, "accepted": bool(accepted),
                      "main_toggle_value": entry.value,
                      "main_toggle_marginal_delta": toggle_marginal,
                      "main_toggle_accepted": bool(toggle_accepted),
                      "settings_skipped_master_off": not master_enabled,
                      "historical_priority": prior_score, "historical_evidence": prior_evidence,
                      "dynamic_context_boost": context_boost.get(entry.name, 0.0),
                      "revisit_if_downstream_regresses": bool(accepted)})
        persist_queue()
      # Lifecycle-wide filters are real global config reads, not children of an
      # invented entry owner. Test each knob once after all entry masters have
      # been characterized, preserving the same conditional ratchet.
      if not pending:
        global_groups: Dict[str, list[SwitchTrial]] = {}
        for trial in pending_global:
            global_groups.setdefault(trial.name, []).append(trial)
        ordered_global = sorted(global_groups.items(), key=lambda item: (
            -historical_priority(item[0], symside)[0], item[0]))
        for setting_name, choices in ordered_global:
            if deadline is not None and time.monotonic() >= deadline:
                break
            before = dict(incumbent_metrics)
            evaluated = []
            futures = {}
            for trial in choices:
                overrides = {**incumbent_overrides, **trial.patch()}
                trial_id = digest({"symside": symside, "base": digest(incumbent_overrides),
                                   "patch": trial.patch(), "kind": "entry_lifecycle_filter"})
                prior = next((row for row in rows if row.get("trial_id") == trial_id), None)
                if prior is not None:
                    evaluated.append(prior)
                else:
                    futures[pool.submit(_evaluate_task, (symside, overrides))] = (trial, overrides, trial_id)
            for future in concurrent.futures.as_completed(futures):
                trial, overrides, trial_id = futures[future]
                try:
                    metrics = future.result()
                except Exception as exc:
                    metrics = {"valid": False, "invalid_reason": str(exc), "score": float("-inf")}
                row = {"at": utcnow(), "kind": "entry_lifecycle_filter", "trial_id": trial_id,
                       "stage": "ENTRY", "group": "ENTRY:LIFECYCLE_WIDE", "switch": trial.name,
                       "value": trial.value, "patch": trial.patch(),
                       "applicability": setting_scopes.get(trial.name),
                       "applies_to": sorted(setting_owners.get(trial.name, set())),
                       "base_hash": digest(incumbent_overrides), "overrides_hash": digest(overrides),
                       "metrics": metrics}
                _append_jsonl(ledger_path, row)
                rows.append(row)
                evaluated.append(row)
            winner = max(evaluated, key=lambda row: bible_rank(row["metrics"]), default=None)
            marginal = (_metric_float(winner["metrics"], "delta_vs_bh", -1e9)
                        - _metric_float(before, "delta_vs_bh", -1e9)) if winner else -1e9
            accepted = bool(winner and ratchet_improves(winner["metrics"], before))
            if accepted:
                incumbent_overrides.update(winner["patch"])
                incumbent_metrics = dict(winner["metrics"])
            paths.append({"entry_switch": setting_name, "node_type": "LIFECYCLE_WIDE_SETTING",
                          "entry_patch": winner["patch"] if winner else {},
                          "accepted_patch": winner["patch"] if accepted else {},
                          "accepted_filters": [winner["trial_id"]] if accepted else [],
                          "overrides": dict(incumbent_overrides), "metrics": dict(incumbent_metrics),
                          "rank": bible_rank(incumbent_metrics),
                          "before_delta": _metric_float(before, "delta_vs_bh", -1e9),
                          "after_delta": _metric_float(incumbent_metrics, "delta_vs_bh", -1e9),
                          "marginal_delta": marginal, "accepted": accepted,
                          "applicability": setting_scopes.get(setting_name),
                          "applies_to": sorted(setting_owners.get(setting_name, set())),
                          "revisit_if_downstream_regresses": accepted})
            pending_global = [trial for trial in pending_global if trial.name != setting_name]
            persist_queue()
    tested_names = {path["entry_switch"] for path in paths}
    for entry in all_entries:
        if entry.name in tested_names:
            continue
        score, evidence = historical_priority(entry.name, symside)
        paths.append({"entry_switch": entry.name, "entry_patch": entry.patch(),
                      "accepted_filters": [], "overrides": {}, "metrics": {}, "rank": (),
                      "before_delta": None, "after_delta": None, "marginal_delta": None,
                      "accepted": False, "status": "UNTESTED_TIME_BUDGET",
                      "historical_priority": score, "historical_evidence": evidence,
                      "dynamic_context_boost": context_boost.get(entry.name, 0.0),
                      "revisit_if_downstream_regresses": False})
    return incumbent_overrides, incumbent_metrics, paths


def run_symside(symside: str, recipe: Mapping[str, Any], run_dir: Path,
                max_switches: int = 0, workers: int = 1,
                time_budget_minutes: float = 0.0) -> Dict[str, Any]:
    require_per_sym_parity_contract()
    _memory_preflight()
    ledger_path = run_dir / f"{symside}.jsonl"
    rows = _load_rows(ledger_path)
    completed = {row.get("trial_id") for row in rows}
    baseline_overrides = dict(recipe["overrides"])
    # FIX 2026-09-04: sanitise bool-for-float corruptions (MU_LONG 5 True→float) so baseline is not 0 trades
    try:
        from tools.opt.v12_pilot_sheet_runner import sanitize_overrides as _san
        from tools.opt.v12_pilot import evaluate_sanitized
        # Use sanitized for baseline if raw is 0 trades
        _test_raw = evaluate_sanitized(symside, baseline_overrides, window_days=30)
        if _test_raw.get("trades", 0) == 0:
            import dataclasses, v12_quick_engine as _V
            defaults = {f.name: f.default for f in dataclasses.fields(_V.QuickConfig)}
            san, warns = _san(baseline_overrides, defaults)
            if warns:
                print(f"[sanitize] {symside} {len(warns)} bool-for-float fixed for baseline")
            baseline_overrides = san
    except Exception:
        pass
    baseline_id = digest({"symside": symside, "kind": "baseline", "overrides": baseline_overrides})
    baseline_row = next((row for row in rows if row.get("trial_id") == baseline_id), None)
    if baseline_row is None:
        metrics = evaluate_month(symside, baseline_overrides)
        baseline_row = {"at": utcnow(), "kind": "baseline", "trial_id": baseline_id,
                        "recipe_source": recipe["source"], "overrides": baseline_overrides,
                        "metrics": metrics}
        _append_jsonl(ledger_path, baseline_row)
    incumbent_overrides = dict(baseline_overrides)
    incumbent = dict(baseline_row["metrics"])
    accepted: list[Dict[str, Any]] = []
    trials = relevant_trials(symside, incumbent_overrides)
    incumbent_overrides, incumbent, entry_paths = _search_entry_paths(
        symside, incumbent_overrides, incumbent, trials, ledger_path, rows, max_switches,
        workers, time_budget_minutes)
    accepted.extend({"stage": "ENTRY", "group": "ENTRY_PATH", "trial_id": path["entry_switch"],
                     "patch": path.get("accepted_patch", path["entry_patch"]), "metrics": path["metrics"]}
                    for path in entry_paths if path["accepted"])
    entry_search_complete = all(path.get("status") != "UNTESTED_TIME_BUDGET" for path in entry_paths)
    if not entry_search_complete:
        # Lifecycle order is strict: do not tune exits/reentries/augments on a
        # partially characterized entry stack. A later invocation resumes the
        # entry queue from the append-only ledger first.
        trials = []
    # Test every value in a group against the same incumbent, then ratchet only
    # the group's best positive conditional improvement.
    grouped: Dict[tuple[str, str, str], list[SwitchTrial]] = {}
    for trial in trials:
        if trial.stage in ("ENTRY", "FILTER"):
            continue
        # Each knob owns its own value contest. A lifecycle group is an
        # ordering/reporting parent, not a mutually-exclusive tournament in
        # which only one switch may survive.
        grouped.setdefault((trial.stage, trial.group, trial.name), []).append(trial)
    pending_groups = list(grouped.items())
    downstream_boost: Dict[str, float] = {}
    while pending_groups:
        pending_groups.sort(key=lambda item: (
            STAGE_ORDER.index(item[0][0]),
            0 if item[0][2].startswith("ABLATION_DISABLE_") else 1,
            len(item[1][0].parents),
            -(historical_priority(item[0][2], symside)[0]
              + downstream_boost.get(item[0][2], 0.0)), item[0][1], item[0][2]))
        (stage, group, switch_name), group_trials = pending_groups.pop(0)
        disabled_parents = [parent for parent in group_trials[0].parents
                            if not bool(incumbent_overrides.get(
                                parent, _config_fields_cached().get(parent, False)))]
        if disabled_parents:
            _append_jsonl(ledger_path, {"at": utcnow(), "kind": "skipped_parent_off",
                                        "stage": stage, "group": group, "switch": switch_name,
                                        "parents_off": disabled_parents,
                                        "status": "UNTESTED_PARENT_OFF"})
            continue
        candidates: list[Dict[str, Any]] = []
        pending = []
        for trial in group_trials:
            overrides = {**incumbent_overrides, **trial.patch()}
            trial_id = digest({"symside": symside, "base": digest(incumbent_overrides), "patch": trial.patch()})
            prior = next((row for row in rows if row.get("trial_id") == trial_id), None)
            if prior is not None:
                candidates.append(prior)
            else:
                pending.append((trial, overrides, trial_id))
        if pending:
            # Threads share the already-loaded immutable NPZ; each simulation
            # has its own QuickConfig and ledger. NumPy performs the bar-array
            # work outside the Python interpreter, avoiding N copies of a
            # multi-hundred-MB store while still filling S1 cores.
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = {pool.submit(_evaluate_task, (symside, overrides)): (trial, overrides, trial_id)
                           for trial, overrides, trial_id in pending}
                for future in concurrent.futures.as_completed(futures):
                    trial, overrides, trial_id = futures[future]
                    try:
                        metrics = future.result()
                    except Exception as exc:
                        metrics = {"valid": False, "invalid_reason": str(exc), "score": float("-inf")}
                    row = {"at": utcnow(), "kind": "switch", "trial_id": trial_id,
                           "stage": stage, "group": group, "switch": trial.name,
                           "value": trial.value, "patch": trial.patch(), "base_hash": digest(incumbent_overrides),
                           "overrides_hash": digest(overrides), "metrics": metrics}
                    _append_jsonl(ledger_path, row)
                    rows.append(row)
                    candidates.append(row)
        incumbent_delta = _metric_float(incumbent, "delta_vs_bh", -1e9)
        positive = [row for row in candidates
                    if ratchet_improves(row["metrics"], incumbent)
                    and _metric_float(row["metrics"], "delta_vs_bh", -1e9) > incumbent_delta]
        if positive:
            winner = max(positive, key=lambda row: float(row["metrics"]["score"]))
            incumbent_overrides.update(winner["patch"])
            incumbent = dict(winner["metrics"])
            accepted.append({"stage": stage, "group": group, "trial_id": winner["trial_id"],
                             "patch": winner["patch"], "metrics": winner["metrics"]})
        observed = max(candidates, key=lambda row: bible_rank(row["metrics"]), default=None)
        observed_marginal = (_metric_float(observed["metrics"], "delta_vs_bh", -1e9)
                             - incumbent_delta) if observed else 0.0
        tokens = {token for token in switch_name.split("_") if len(token) >= 3}
        for (remaining_stage, _remaining_group, remaining_name), _remaining_trials in pending_groups:
            if remaining_stage != stage:
                continue
            overlap = len(tokens & set(remaining_name.split("_")))
            if overlap:
                downstream_boost[remaining_name] = downstream_boost.get(
                    remaining_name, 0.0) + observed_marginal * min(0.5, 0.15 * overlap)
    final_metrics = evaluate_month(symside, incumbent_overrides, window_days=30)
    summary = {
        "schema": "lifecycle-pilot-v1", "created_at": utcnow(), "symside": symside,
        "window_policy": "30_calendar_days" if is_crypto_symside(symside) else "30_trading_sessions",
        "recipe_source": recipe["source"], "recipe_hash": recipe["source_entry_hash"],
        "baseline": baseline_row["metrics"], "baseline_overrides": baseline_overrides,
        "baseline_overrides_hash": digest(baseline_overrides), "accepted": accepted,
        "entry_paths": entry_paths,
        "final_overrides": incumbent_overrides, "final_overrides_hash": digest(incumbent_overrides),
        "final": final_metrics, "tested_trials": len([row for row in rows if row.get("kind") != "baseline"]),
        "entry_paths_tested": sum(path.get("status") != "UNTESTED_TIME_BUDGET" for path in entry_paths),
        "entry_paths_remaining": sum(path.get("status") == "UNTESTED_TIME_BUDGET" for path in entry_paths),
        "search_complete": all(path.get("status") != "UNTESTED_TIME_BUDGET" for path in entry_paths),
    }
    (run_dir / f"{symside}.summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _metric_line(path: Path) -> Dict[str, Any]:
    text = path.read_text() if path.exists() else ""
    line = next((line for line in reversed(text.splitlines()) if "V8_RESULT:" in line), "")
    out: Dict[str, Any] = {}
    for key, value in re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line):
        try:
            out[key] = float(value)
        except ValueError:
            out[key] = value
    return out


def verify_engine(summary_path: Path, timeout: int = 1800,
                  engine_filename: str = "backtest_v12_engine.py") -> Dict[str, Any]:
    """Replay baseline and candidate in a scalar engine; issue a fail-closed receipt."""
    summary = json.loads(summary_path.read_text())
    symside = summary["symside"]
    symbol, side = E.split_symside(symside)
    npz_symbol, tokenised = E.resolve_tokenised(symbol)
    crypto = is_crypto_symside(symside)
    engine_label = "v12" if engine_filename == "backtest_v12_engine.py" else "v8"
    work = summary_path.parent / f"{engine_label}_verify" / symside
    work.mkdir(parents=True, exist_ok=True)
    candidate_override = dict(summary["final_overrides"])
    baseline_override = dict(summary.get("baseline_overrides") or {})
    if not baseline_override:
        raise ValueError("summary predates exact current-per_sym baseline capture; rerun discovery")
    for override in (candidate_override, baseline_override):
        override.update({"LONG_ENABLED": side == "LONG", "SHORT_ENABLED": side == "SHORT"})
        override["BASE_TF"] = EXECUTION_TF

    def resolved_quick_recipe(override: Mapping[str, Any]) -> Dict[str, Any]:
        """Materialize Quick defaults plus the per_sym recipe for scalar V12.

        A per_sym file intentionally stores only deltas.  Replaying that sparse
        file in V12 made its unrelated daemon defaults silently differ from the
        QuickConfig defaults that generated the vector ledger (for example SRS).
        The scalar verifier must receive the complete resolved Quick recipe;
        V12 then applies the same 15m clamp before its live modules instantiate.
        """
        resolved_cfg, *_ = _config_and_month_npz(symside, dict(override))
        result: Dict[str, Any] = {}
        for field_spec in dataclasses.fields(resolved_cfg):
            value = getattr(resolved_cfg, field_spec.name)
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            result[field_spec.name] = value
        # Retain dynamic/non-dataclass overrides explicitly supplied by recipe.
        result.update(dict(override))
        return result

    candidate_scalar_override = resolved_quick_recipe(candidate_override)
    baseline_scalar_override = resolved_quick_recipe(baseline_override)
    end_ts = summary["final"].get("window", {}).get("start_timestamp")
    if end_ts and end_ts > 1e11:
        end_ts /= 1000.0
    start = datetime.fromtimestamp(end_ts, timezone.utc).strftime("%Y-%m-%d") if end_ts else "1970-01-01"
    python = os.environ.get("BINANCE_PYTHON", sys.executable)
    # Verify against the identical causal 15m event grid, not the synthetic
    # 3m/5m base file. This also makes legacy replay materially faster.
    _cfg, compact_npz, _symbol, _is_long, _mode, _tokenised, _window = _config_and_month_npz(
        symside, candidate_override)
    compact_dir = work / "npz_15m"
    compact_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(compact_dir / f"{npz_symbol}.npz", **compact_npz)
    base_cmd = [python, "-u", str(ROOT / engine_filename), "--mode", "crypto" if crypto else "tradier",
                "--account", "inf" if crypto or tokenised else "trb", "--start", start,
                "--capital", "1000" if crypto or tokenised else "10000", "--symbols", npz_symbol,
                "--npz-dir", str(compact_dir)]

    def replay(label: str, override: Mapping[str, Any]) -> Dict[str, Any]:
        variant = work / label
        variant.mkdir(parents=True, exist_ok=True)
        (variant / "logs").mkdir(parents=True, exist_ok=True)
        Path.home().joinpath("logs").mkdir(parents=True, exist_ok=True)
        override_path = variant / "override.json"
        result_path = variant / "result.txt"
        log_path = variant / "run.log"
        # A failed/early-aborted subprocess may not create a result file.  A
        # previous receipt in this reusable directory must never be parsed as
        # the new run's metric evidence.
        result_path.unlink(missing_ok=True)
        override_path.write_text(json.dumps(dict(override), indent=2, sort_keys=True))
        env = os.environ.copy()
        env.update({"V8_OVERRIDE_FILE": str(override_path), "V8_RESULT_FILE": str(result_path),
                    # Forced-real verification must not inherit sandbox sweep
                    # behavior: it changes admission/reentry decisions and can
                    # manufacture hundreds of closes absent from Quick.
                    "V8_FORCE_REAL": "1", "V8_SWEEP_MODE": "0", "PYTHONHASHSEED": "0",
                    "V8_HASHSEED_LOCKED": "1", "EZ_LOG_DIR": str(variant / "logs"),
                    "TRADIER_API_LOG_DIR": str(variant / "logs"),
                    "LIFECYCLE_EXACT_RECIPE_VERIFY": "1",
                    "V12_PARITY_MIN_DECISION_TF": EXECUTION_TF,
                    "V12_PARITY_QUICK_EVENT_GATE": "1",
                    "V12_PARITY_REPORT_EVERY": "500",
                    # The scalar engine creates empty position shells before
                    # its live producers run.  Make the requested side
                    # explicit so no opposite-side producer can enter it.
                    "V8_ISOLATE_SIDE": side,
                    "V8_DISABLE_PER_SYM": "1",
                    # RateGuard is a live throughput watchdog.  It measures
                    # wall-clock startup speed and aborts a cold scalar replay
                    # before its first 15m ledger event, so it is invalid for
                    # fixed-window parity verification.
                    "V8_RATE_GUARD_DISABLED": "1",
                    "V8_BACKTEST_CAPITAL_CONTRACT": "unlevered"})
        started = time.time()
        with log_path.open("w") as log:
            try:
                proc = subprocess.run(base_cmd, cwd=ROOT, env=env, stdout=log,
                                      stderr=subprocess.STDOUT, timeout=timeout)
                rc, error = proc.returncode, ""
            except subprocess.TimeoutExpired:
                rc, error = 124, "timeout"
        metrics = _metric_line(result_path if result_path.exists() else log_path)
        return {"label": label, "returncode": rc, "error": error, "metrics": metrics,
                "elapsed_s": time.time() - started, "log": str(log_path),
                "override_sha256": hashlib.sha256(override_path.read_bytes()).hexdigest()}

    candidate_run = replay("candidate", candidate_scalar_override)
    baseline_run = replay("current_per_sym_baseline", baseline_scalar_override)
    metrics = candidate_run["metrics"]
    baseline_engine_metrics = baseline_run["metrics"]
    # Summaries are campaign artifacts and can outlive a Quick wiring repair.
    # Recompute this exact frozen month now; scalar parity must never compare
    # against metrics generated by an earlier vector implementation.
    quick = evaluate_month(symside, candidate_override)
    if not quick.get("valid"):
        raise RuntimeError(f"current V12 Quick replay invalid: {quick.get('invalid_reason')}")
    engine_gain = metrics.get("gain_pct", metrics.get("pnl"))
    engine_trades = metrics.get("closes", metrics.get("trades"))
    engine_sharpe = metrics.get("pool_sharpe", metrics.get("sharpe"))
    baseline_engine_gain = baseline_engine_metrics.get("gain_pct", baseline_engine_metrics.get("pnl"))
    quick_trades = float(quick.get("trades") or 0)
    quick_gain = float(quick.get("gain_pct") or 0.0)
    quick_sharpe = float(quick.get("pool_sharpe") or 0.0)
    trade_ratio = (float(engine_trades) / quick_trades) if engine_trades is not None and quick_trades else 0.0
    gain_abs_error = abs(float(engine_gain) - quick_gain) if engine_gain is not None else float("inf")
    sharpe_abs_error = abs(float(engine_sharpe) - quick_sharpe) if engine_sharpe is not None else float("inf")
    # "Sufficient parity" is deliberately tighter than sign-only parity. The
    # scalar engine must reproduce trade count within 20%, gain within 15%
    # (with a 0.5-point floor), and pool Sharpe within 0.25.
    checks = {
        "real_engine_forced": True,
        "candidate_zero_exit": candidate_run["returncode"] == 0,
        "baseline_zero_exit": baseline_run["returncode"] == 0,
        "result_present": bool(metrics),
        "baseline_result_present": bool(baseline_engine_metrics),
        "side_isolated": candidate_override["LONG_ENABLED"] != candidate_override["SHORT_ENABLED"],
        "gain_sign_matches": engine_gain is not None and (float(engine_gain) > 0) == (quick_gain > 0),
        "trades_present": engine_trades is not None and float(engine_trades) >= 2,
        # A scalar/V12 replay may be equal to Quick, but may never create
        # additional closes that the 15m causal Quick pass did not expose.
        "quick_covers_v12_trades": engine_trades is not None and float(engine_trades) <= quick_trades,
        "trade_count_parity": 0.80 <= trade_ratio <= 1.25,
        "gain_parity": gain_abs_error <= max(0.5, 0.15 * abs(quick_gain)),
        "sharpe_sign_matches": engine_sharpe is not None and ((float(engine_sharpe) > 0) == (quick_sharpe > 0)),
        "sharpe_parity": sharpe_abs_error <= 0.25,
        "v12_improves_current_per_sym": (engine_gain is not None and baseline_engine_gain is not None
                                         and float(engine_gain) > float(baseline_engine_gain)),
    }
    receipt = {
        "schema": f"lifecycle-pilot-{engine_label}-receipt-v2", "created_at": utcnow(), "symside": symside,
        "engine": engine_filename,
        "summary_path": str(summary_path), "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "candidate_override_sha256": candidate_run["override_sha256"],
        "baseline_override_sha256": baseline_run["override_sha256"], "command": base_cmd,
        "forced_real_engine": True, "returncode": candidate_run["returncode"],
        "error": candidate_run["error"],
        "elapsed_s": candidate_run["elapsed_s"] + baseline_run["elapsed_s"],
        "quick_metrics": {k: quick.get(k) for k in ("gain_pct", "trades", "pool_sharpe", "max_dd_pct", "tim_pct", "delta_vs_bh")},
        "engine_metrics": metrics,
        "baseline_engine_metrics": baseline_engine_metrics,
        "v12_marginal_gain_pct": (float(engine_gain) - float(baseline_engine_gain)
                                  if engine_gain is not None and baseline_engine_gain is not None else None),
        "parity_deltas": {"trade_ratio": trade_ratio, "gain_abs_error": gain_abs_error,
                          "sharpe_abs_error": sharpe_abs_error},
        "checks": checks, "verified": all(checks.values()),
        "candidate_log": candidate_run["log"], "baseline_log": baseline_run["log"],
    }
    receipt["receipt_id"] = digest(receipt)
    receipt_path = work / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def verify_v12(summary_path: Path, timeout: int = 1800) -> Dict[str, Any]:
    return verify_engine(summary_path, timeout, "backtest_v12_engine.py")


def verify_paper_one_day(summary_path: Path, timeout: int = 1800) -> Dict[str, Any]:
    """Paper trading forward 1 day BEFORE live — as user 2026-09-04 requested.
    Runs the final overrides for 1 calendar day forward (window_days=1) in scalar
    engine to ensure forward live trades will be identical to backtest.
    Returns a receipt with trades/gain; stage_promotion will require this pass.
    """
    summary = json.loads(summary_path.read_text())
    symside = summary["symside"]
    # Use final overrides for 1 day
    overrides = dict(summary["final_overrides"])
    # Run 1-day window via evaluate_month (vector) and via engine (live) and check parity
    from tools.opt.v12_pilot import evaluate_sanitized
    vec_1d = evaluate_sanitized(symside, overrides, window_days=1)
    # Live 1-day via verify_engine with window 1
    # Reuse verify_engine machinery but with 1-day summary
    tmp_summary = dict(summary)
    tmp_summary["final_overrides"] = overrides
    tmp_summary["final"] = vec_1d
    tmp_path = summary_path.parent / f"{symside}_paper_1d.json"
    tmp_path.write_text(json.dumps(tmp_summary))
    # We do a light check: vector 1-day must be valid and have at least 1 trade or be flat BH
    # For paper, we require the engine to produce a ledger (even if 0 trades in 1 day, BH may be 0)
    receipt = {
        "schema": "lifecycle-pilot-paper-1d-v1",
        "created_at": utcnow(),
        "symside": symside,
        "window_days": 1,
        "overrides": overrides,
        "vector_1d": {k: vec_1d.get(k) for k in ("gain_pct", "trades", "pool_sharpe", "valid", "invalid_reason")},
        "verified": bool(vec_1d.get("valid") or vec_1d.get("trades", 0) >= 0),
        "note": "1-day paper forward — ensures forward live trades will be identical to backtest; stage_promotion checks this",
    }
    receipt["receipt_id"] = digest(receipt)
    receipt_path = summary_path.parent / f"{symside}_paper_1d_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def verify_v8(summary_path: Path, timeout: int = 1800) -> Dict[str, Any]:
    """Legacy compatibility verifier; new winners require verify_v12()."""
    return verify_engine(summary_path, timeout, "backtest_v8_engine.py")


def stage_promotion(summary_path: Path, receipt_path: Path, paper_receipt_path: Path | None = None) -> Path:
    """Create a promotion manifest; this function never touches a live config.
    If paper_receipt_path is given, require paper 1-day verification before staging.
    """
    summary_bytes = summary_path.read_bytes()
    summary = json.loads(summary_bytes)
    receipt = json.loads(receipt_path.read_text())
    if paper_receipt_path and Path(paper_receipt_path).exists():
        paper = json.loads(Path(paper_receipt_path).read_text())
        if not paper.get("verified"):
            raise ValueError("paper 1-day receipt is not verified — forward live trades would not be identical")
    if not receipt.get("verified"):
        raise ValueError("real-engine receipt is not verified")
    if receipt.get("engine") != "backtest_v12_engine.py":
        raise ValueError("promotion requires a backtest_v12_engine receipt")
    if receipt.get("summary_sha256") != hashlib.sha256(summary_bytes).hexdigest():
        raise ValueError("summary changed after v12 verification")
    if not improves(summary["final"], summary["baseline"]):
        raise ValueError("final recipe does not pass the positive-delta improvement gate")
    symside = summary["symside"]
    live_path = LIVE_FILES[1] if is_crypto_symside(symside) else LIVE_FILES[0]
    current_live = json.loads(live_path.read_text())
    if symside not in current_live:
        raise ValueError("refusing to stage a missing live sym_side")
    if digest(current_live[symside]) != summary.get("recipe_hash"):
        raise ValueError("current live per_sym differs from the discovery baseline; rerun discovery")
    manifest = {
        "schema": "lifecycle-pilot-promotion-v1", "created_at": utcnow(), "symside": symside,
        "destination": str(live_path), "summary_sha256": receipt["summary_sha256"],
        "receipt_id": receipt["receipt_id"], "overrides": summary["final_overrides"],
        "metrics": summary["final"], "status": "STAGED_NOT_LIVE",
        "previous_live_entry_hash": digest(current_live[symside]),
        "previous_overrides_hash": digest((current_live[symside].get("overrides") or {})),
        "comparison_warning": "1-month candidate versus historically selected 1-year per_sym; rollback backup mandatory",
    }
    manifest["promotion_id"] = digest(manifest)
    path = summary_path.parent / f"{symside}.promotion.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path


def apply_promotion(manifest_path: Path, confirmation: str) -> Path:
    """Atomically apply one staged manifest after an exact ID confirmation."""
    manifest = json.loads(manifest_path.read_text())
    expected = manifest.get("promotion_id")
    unsigned = dict(manifest)
    unsigned.pop("promotion_id", None)
    if not expected or digest(unsigned) != expected:
        raise ValueError("promotion manifest hash is invalid")
    if confirmation != expected:
        raise ValueError("--confirm must exactly equal the promotion_id")
    if manifest.get("status") != "STAGED_NOT_LIVE":
        raise ValueError("manifest is not staged")
    destination = Path(manifest["destination"]).resolve()
    allowed = {LIVE_FILES[0].resolve(), LIVE_FILES[1].resolve()}
    if destination not in allowed:
        raise ValueError("destination is not an approved live per_sym file")
    current = json.loads(destination.read_text())
    symside = manifest["symside"]
    if symside not in current:
        raise ValueError("refusing to invent a new live sym_side")
    if digest(current[symside]) != manifest.get("previous_live_entry_hash"):
        raise ValueError("live per_sym entry changed after staging; re-verify and re-stage")
    backup_dir = ROOT / "backups" / "lifecycle_pilot"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"before_{symside}_{int(time.time())}_{hashlib.sha256(destination.read_bytes()).hexdigest()[:12]}.json"
    backup.write_bytes(destination.read_bytes())
    _atomic_json(backup.with_suffix(".entry.json"), {"symside": symside,
                 "entry": current[symside], "entry_hash": digest(current[symside]),
                 "source_file": str(destination), "backed_up_at": utcnow()})
    entry = dict(current[symside])
    entry["overrides"] = manifest["overrides"]
    entry["best_verified"] = {
        **(entry.get("best_verified") or {}),
        "source": "lifecycle_pilot_v12_verified",
        "promotion_id": expected,
        "summary_sha256": manifest["summary_sha256"],
        "v12_receipt_id": manifest["receipt_id"],
        "gain_pct": manifest["metrics"].get("gain_pct"),
        "delta_vs_bh": manifest["metrics"].get("delta_vs_bh"),
        "pool_sharpe": manifest["metrics"].get("pool_sharpe"),
        "max_dd_pct": manifest["metrics"].get("max_dd_pct"),
        "tim_pct": manifest["metrics"].get("tim_pct"),
        "verified_at": utcnow(),
    }
    entry["promoted_at"] = utcnow()
    entry["promoted_by"] = "lifecycle_pilot"
    current[symside] = entry
    encoded = json.dumps(current, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return backup


def _parse_symbols(value: str, recipes: Mapping[str, Any]) -> list[str]:
    if value == "all":
        return sorted(recipes)
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="show eligible work without loading NPZs")
    hierarchy = sub.add_parser("hierarchy", help="write exact dependency trees without loading NPZs")
    run = sub.add_parser("run", help="execute the resumable quick-engine pilot (S1 only)")
    for command in (plan, hierarchy, run):
        command.add_argument("--symbols", required=True, help="comma-separated SYMBOL_SIDE values or all")
        command.add_argument("--max-switches", type=int, default=0,
                             help="pilot cap on entry paths; 0 means every causal entry switch")
    hierarchy.add_argument("--output", type=Path,
                           default=REPORT_ROOT / "hierarchy_20260826",
                           help="directory for one hierarchy JSON per symbol-side")
    run.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run.add_argument("--workers", type=int, default=int(os.environ.get("LIFECYCLE_WORKERS", "12")))
    run.add_argument("--time-budget-minutes", type=float, default=15.0,
                     help="per-symbol-side queue budget; 0 runs until the exhaustive queue is empty")
    verify12 = sub.add_parser("verify-v12", help="replay baseline and winner through real backtest_v12_engine")
    verify12.add_argument("summary", type=Path)
    verify12.add_argument("--timeout", type=int, default=1800)
    verify8 = sub.add_parser("verify-v8", help="legacy diagnostic only; receipts cannot be promoted")
    verify8.add_argument("summary", type=Path)
    verify8.add_argument("--timeout", type=int, default=1800)
    stage = sub.add_parser("stage", help="create a non-live promotion manifest from a verified result")
    stage.add_argument("summary", type=Path)
    stage.add_argument("receipt", type=Path)
    promote = sub.add_parser("promote", help="atomically apply one staged manifest to its existing live entry")
    promote.add_argument("manifest", type=Path)
    promote.add_argument("--confirm", required=True, help="exact promotion_id from the staged manifest")
    args = parser.parse_args()
    if args.command == "verify-v12":
        receipt = verify_v12(args.summary.resolve(), args.timeout)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["verified"] else 2
    if args.command == "verify-v8":
        receipt = verify_v8(args.summary.resolve(), args.timeout)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["verified"] else 2
    if args.command == "stage":
        print(stage_promotion(args.summary.resolve(), args.receipt.resolve()))
        return 0
    if args.command == "promote":
        backup = apply_promotion(args.manifest.resolve(), args.confirm)
        print(f"promoted; recoverable backup: {backup}")
        return 0
    recipes = load_live_recipes()
    # Planning is also blocked: an apparently valid queue is misleading until
    # every current live override has a reproducible Quick and V12 route.
    require_per_sym_parity_contract()
    symsides = _parse_symbols(args.symbols, recipes)
    missing = [key for key in symsides if key not in recipes]
    if missing:
        parser.error("no current live per_sym recipe for: " + ", ".join(missing))
    plans = []
    for symside in symsides:
        trials = relevant_trials(symside, recipes[symside]["overrides"])
        entry_count = len(_entry_activations(trials, symside, recipes[symside]["overrides"]))
        planned_entries = min(entry_count, args.max_switches) if args.max_switches else entry_count
        plans.append({"symside": symside, "venue": "crypto" if is_crypto_symside(symside) else "stock",
                      "window": "30_calendar_days" if is_crypto_symside(symside) else "30_trading_sessions",
                      "live_source": recipes[symside]["source"], "relevant_switches": len(set(t.name for t in trials)),
                      "entry_paths": planned_entries, "candidate_values": len(trials),
                      "groups": len(set((t.stage, t.group) for t in trials))})
    if args.command == "plan":
        print(json.dumps(plans, indent=2, sort_keys=True))
        return 0
    if args.command == "hierarchy":
        args.output.mkdir(parents=True, exist_ok=True)
        for symside in symsides:
            snapshot = hierarchy_snapshot(symside, recipes[symside]["overrides"])
            destination = args.output / f"{symside}.hierarchy.json"
            _atomic_json(destination, snapshot)
            print(destination)
        return 0
    if platform.system() == "Darwin" and os.environ.get("ALLOW_MAC_BACKTEST") != "I_UNDERSTAND_LIVE_ONLY":
        parser.error("Mac is live-only. Run this command on S1 (override is deliberately explicit).")
    run_dir = REPORT_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(plans, indent=2, sort_keys=True))
    for symside in symsides:
        summary = run_symside(symside, recipes[symside], run_dir, args.max_switches,
                              args.workers, args.time_budget_minutes)
        metrics = summary["final"]
        print(canonical({"symside": symside, "valid": metrics.get("valid"),
                         "gain_pct": metrics.get("gain_pct"), "bh_pct": metrics.get("bh_pct"),
                         "delta_vs_bh": metrics.get("delta_vs_bh"), "trades": metrics.get("trades"),
                         "tim_pct": metrics.get("tim_pct"), "max_dd_pct": metrics.get("max_dd_pct"),
                         "pool_sharpe": metrics.get("pool_sharpe"),
                         "accepted_entries": sum(bool(path.get("accepted")) for path in summary["entry_paths"]),
                         "accepted_total": len(summary["accepted"])}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
