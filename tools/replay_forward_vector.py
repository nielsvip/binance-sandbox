#!/usr/bin/env python3
"""Read-only causal replay of v15_forward_state.state_for over frozen NPZ prefixes.

Each observation calls the real forward-state path and QuickConfig NPZ loader on
an exact row-prefix view of the frozen NPZ. A bar-open timestamp t is evaluated
with decision clock t+900s. Outputs are diagnostic artifacts only; settings and
source files are immutable inputs and are never written by this driver.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_ACTION_TYPES = {"OPEN", "AUGMENT", "REDUCE", "CLOSE"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha256(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def row_prefix(npz, stop: int, row_count: int):
    """Return a causal mapping whose row-aligned arrays end at `stop` (inclusive)."""
    if stop < 0 or stop >= row_count:
        raise IndexError(f"prefix stop {stop} outside 0..{row_count - 1}")
    out = {}
    for key, value in npz.items():
        if isinstance(value, np.ndarray) and value.ndim and len(value) == row_count:
            out[key] = value[: stop + 1]
        else:
            out[key] = value
    return out


def _expected_closed_bar(decision_ts: int) -> int:
    return (int(decision_ts) // 900 - 1) * 900


def _is_signal_event(event):
    return (
        isinstance(event, dict)
        and str(event.get("type", "")).upper() in _ACTION_TYPES
        and not (
            str(event.get("type", "")).upper() == "CLOSE"
            and str(event.get("reason") or event.get("exit_reason") or "").upper() == "FINAL_MTM"
        )
    )


def _actions_at(ledger, bar_ts: int):
    return [
        event for event in (ledger or [])
        if _is_signal_event(event) and int(float(event.get("ts") or -1)) == int(bar_ts)
    ]


def _open_lifecycle_at(ledger, cutoff_ts: int):
    rows = [
        dict(event) for event in (ledger or [])
        if _is_signal_event(event) and int(float(event.get("ts") or -1)) <= int(cutoff_ts)
    ]
    rows.sort(key=lambda event: (
        float(event.get("ts") or 0.0),
        0 if str(event.get("type", "")).upper() in ("OPEN", "AUGMENT", "REDUCE") else 1,
    ))
    active = []
    qty = cost = peak_qty = peak_cost = 0.0
    for event in rows:
        kind = str(event.get("type", "")).upper()
        try:
            event_qty = abs(float(event.get("qty") or 0.0))
            price = float(event.get("price") or 0.0)
        except (TypeError, ValueError):
            event_qty, price = 0.0, 0.0
        if kind == "OPEN":
            active = []
            qty, cost = event_qty, event_qty * price
        elif kind == "AUGMENT":
            if not active:
                continue
            qty += event_qty
            cost += event_qty * price
        elif kind == "REDUCE":
            if not active:
                continue
            reduced = min(event_qty, qty)
            if qty > 0:
                cost -= cost * reduced / qty
            qty = max(0.0, qty - reduced)
        elif kind == "CLOSE":
            if not active:
                continue
            event["diagnostic_qty_before_close"] = qty
            active.append(event)
            active = []
            qty = cost = 0.0
            continue
        event["diagnostic_qty_after"] = qty
        event["diagnostic_cost_basis_after"] = cost
        active.append(event)
        peak_qty = max(peak_qty, qty)
        peak_cost = max(peak_cost, cost)
    return {
        "events": active,
        "last_open": next((event for event in reversed(active) if str(event.get("type", "")).upper() == "OPEN"), None),
        "peak_qty": peak_qty,
        "peak_cost_basis": peak_cost,
        "qty_is_diagnostic_only": True,
    }


def _prefix_digest(prefix):
    h = hashlib.sha256()
    for key in ("timestamps", "close"):
        value = np.ascontiguousarray(prefix[key])
        h.update(key.encode())
        h.update(value.view(np.uint8))
    return h.hexdigest()


def _source_hashes(code_root: Path):
    candidates = [
        Path("tools/v15_forward_state.py"),
        Path("tools/vector_direct_trader.py"),
        Path("tools/account_comparison_books.py"),
        Path("tools/opt/v12_pilot.py"),
        Path("tools/opt/evaluate_v12.py"),
        Path("v12_quick_engine.py"),
        Path("cat_side_defaults.py"),
        Path("per_sym_live_set.py"),
        Path("config.py"),
        Path("config_tradier.py"),
        Path("data/cat_side_defaults_4.json"),
        Path("data/sweep_cat_overrides.json"),
        Path("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx"),
    ]
    out = {}
    for rel in candidates:
        path = code_root / rel
        if path.is_file():
            out[str(rel)] = _sha256(path)
    vec_root = code_root / "vec_decisions"
    if vec_root.is_dir():
        for path in sorted(vec_root.glob("*.py")):
            out[str(path.relative_to(code_root))] = _sha256(path)
    return out


class _ReplayClock:
    """Supply a historical decision clock while preserving real wall-time stamps."""

    def __init__(self, decision_ts: int, real_time_module):
        self._decision_ts = int(decision_ts)
        self._real = real_time_module

    def time(self):
        return float(self._decision_ts)

    def gmtime(self, *args):
        return self._real.gmtime(*args)

    def strftime(self, *args):
        return self._real.strftime(*args)


class _CausalNpzView:
    """Read-only, NpzFile-compatible mapping over one causal NPZ row prefix."""

    def __init__(self, arrays):
        self._arrays = arrays
        self.files = list(arrays)

    def __getitem__(self, key):
        return self._arrays[key]

    def __contains__(self, key):
        return key in self._arrays

    def get(self, key, default=None):
        return self._arrays.get(key, default)

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _make_runtime(code_root: Path, npz_path: Path, symside: str, output_path: Path):
    """Copy the immutable source bundle to scratch and link only the frozen NPZ."""
    runtime_root = output_path.parent / (output_path.stem + "_runtime")
    runtime_code = runtime_root / "code"
    if runtime_root.exists():
        raise FileExistsError(f"isolated runtime already exists: {runtime_root}")
    shutil.copytree(code_root, runtime_code)
    symbol = symside.rsplit("_", 1)[0]
    canonical = runtime_code / "backtest_v8" / "indicators" / f"{symbol}.npz"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.symlink_to(npz_path.resolve())
    return runtime_root, runtime_code, canonical


def _install_state_context(forward_state, evaluate_v12, pilot_v12, quick_engine,
                           npz_cutoffs, canonical_npz_path, runtime_code,
                           active_set, provenance_root: Path):
    """Inject frozen settings and a causal NPZ reader around actual state_for/load_npz."""
    original = {
        "load_overrides": forward_state._load_overrides,
        "out": forward_state.OUT,
        "state_time": forward_state.time,
        "forward_root": forward_state.ROOT,
        "evaluate_root": evaluate_v12.ROOT,
        "pilot_root": pilot_v12.ROOT,
        "engine_base_path": quick_engine.BASE_PATH,
        "np_load": np.load,
        "last_closed_bar": npz_cutoffs.last_closed_bar,
    }
    overrides = dict(active_set["overrides"])
    meta = {
        "settings_updated_at": active_set.get("updated_at"),
        "settings_sha256": active_set["overrides_sha256"],
        "template_md5": active_set.get("template_md5"),
        "defaults_round": active_set.get("defaults_round"),
        "active_set_source": active_set.get("source"),
    }
    holder = {"npz": None, "decision_ts": 0}
    canonical_abs = os.path.abspath(os.fspath(canonical_npz_path))

    def causal_load(file, *args, **kwargs):
        try:
            requested = os.path.abspath(os.fspath(file))
        except TypeError:
            requested = None
        if requested == canonical_abs:
            return _CausalNpzView(holder["npz"])
        return original["np_load"](file, *args, **kwargs)

    forward_state._load_overrides = lambda _ss: (dict(overrides), "replay.frozen_active_set", dict(meta))
    forward_state.OUT = provenance_root
    forward_state.ROOT = runtime_code
    evaluate_v12.ROOT = runtime_code
    pilot_v12.ROOT = runtime_code
    quick_engine.BASE_PATH = runtime_code
    np.load = causal_load
    npz_cutoffs.last_closed_bar = lambda now=None: original["last_closed_bar"](
        holder["decision_ts"] if now is None else now
    )
    original["npz_holder"] = holder
    return original


def _restore_state_context(forward_state, evaluate_v12, pilot_v12, quick_engine,
                           npz_cutoffs, original):
    forward_state._load_overrides = original["load_overrides"]
    forward_state.OUT = original["out"]
    forward_state.time = original["state_time"]
    forward_state.ROOT = original["forward_root"]
    evaluate_v12.ROOT = original["evaluate_root"]
    pilot_v12.ROOT = original["pilot_root"]
    quick_engine.BASE_PATH = original["engine_base_path"]
    np.load = original["np_load"]
    npz_cutoffs.last_closed_bar = original["last_closed_bar"]


def _planner_state(result):
    """Persist the exact account-planner inputs returned by the causal state path."""
    return {
        key: result.get(key) for key in (
            "ok", "reason", "open", "npz_tail", "engine_qty", "engine_open_qty",
            "engine_open_px", "engine_open_bar_ts", "entry_bar", "entry_px",
            "acts_bar_ts", "acts_last_bar", "src", "settings_fallback",
        )
    }


def _parallel_state_chunk(task):
    """Evaluate independent causal prefixes in one isolated process context."""
    (npz_path, symside, indexes, runtime_code, canonical_npz_path,
     active_set, provenance_root) = task
    with np.load(npz_path, allow_pickle=True) as archive:
        arrays = {key: archive[key] for key in archive.files}
    row_count = len(arrays["timestamps"])
    old_sys_path = list(sys.path)
    if str(runtime_code) in sys.path:
        sys.path.remove(str(runtime_code))
    sys.path.insert(0, str(runtime_code))
    os.environ["BASE_PATH"] = str(runtime_code)
    os.environ["V12_BASE_PATH"] = str(runtime_code)
    os.environ["V12_NPZ_DIR"] = str(Path(canonical_npz_path).parent)
    os.environ["V12_RUN_ONE_SET_PRECEDENCE"] = "1"
    os.environ["V12_SIM_ALLOW_LIVE_MARKET_FILE"] = "0"
    os.environ.pop("V12_ALLOW_STALE_NPZ", None)
    import tools.v15_forward_state as forward_state
    import tools.opt.evaluate_v12 as evaluate_v12
    import tools.opt.v12_pilot as pilot_v12
    import tools.npz_cutoffs as npz_cutoffs
    import v12_quick_engine as quick_engine
    real_time_module = time
    original = _install_state_context(
        forward_state, evaluate_v12, pilot_v12, quick_engine, npz_cutoffs,
        canonical_npz_path, runtime_code, active_set, provenance_root,
    )
    results = []
    try:
        for idx in indexes:
            bar_ts = int(arrays["timestamps"][idx])
            decision_ts = bar_ts + 900
            original["npz_holder"]["npz"] = row_prefix(arrays, int(idx), row_count)
            original["npz_holder"]["decision_ts"] = decision_ts
            forward_state.time = _ReplayClock(decision_ts, real_time_module)
            try:
                result = forward_state.state_for(symside, window_days=30)
                if int(result.get("npz_tail") or 0) not in (0, bar_ts):
                    raise AssertionError(f"state_for consumed tail {result.get('npz_tail')} beyond selected bar {bar_ts}")
            except Exception as exc:
                result = {
                    "ts": time.strftime("%FT%TZ", time.gmtime()),
                    "sym_side": symside,
                    "ok": False,
                    "reason": f"state_for exception {type(exc).__name__}: {str(exc)[:240]}",
                    "npz_tail": bar_ts,
                    "execution_error": True,
                }
            results.append((int(idx), result))
    finally:
        _restore_state_context(
            forward_state, evaluate_v12, pilot_v12, quick_engine,
            npz_cutoffs, original,
        )
        sys.path[:] = old_sys_path
    return results


def replay(npz_path: Path, active_path: Path, symside: str, start_ts: int,
           end_ts: int, output_path: Path, code_root: Path, code_manifest: Path | None = None,
           limit: int | None = None, planner_account: str | None = None,
           exchange_info_path: Path | None = None, state_workers: int = 1):
    active_set = json.loads(active_path.read_text())
    if str(active_set.get("sym_side", "")).upper() != symside.upper():
        raise ValueError(f"active set is for {active_set.get('sym_side')}, requested {symside}")
    calculated_override_hash = _canonical_sha256(active_set.get("overrides") or {})
    if calculated_override_hash != active_set.get("overrides_sha256"):
        raise ValueError("active override JSON hash does not match its declared SHA-256")

    with np.load(npz_path, allow_pickle=True) as archive:
        arrays = {key: archive[key] for key in archive.files}
    timestamps = np.asarray(arrays.get("timestamps", []), dtype=np.int64)
    if timestamps.ndim != 1 or len(timestamps) < 2 or np.any(np.diff(timestamps) <= 0):
        raise ValueError("NPZ timestamps must be a strictly increasing 1D vector")
    if not np.all(np.diff(timestamps) == 900):
        raise ValueError("frozen input is not a complete, regular 15m sequence")
    selected = np.flatnonzero((timestamps > int(start_ts)) & (timestamps <= int(end_ts)))
    expected_rows = int((int(end_ts) - int(start_ts)) // 900)
    if len(selected) != expected_rows or np.any(np.diff(timestamps[selected]) != 900):
        raise ValueError(f"requested interval expected {expected_rows} rows, found {len(selected)}")
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    state_workers = max(1, min(int(state_workers), len(selected) or 1))

    source_hashes = _source_hashes(code_root)
    manifest_hash = _sha256(code_manifest) if code_manifest and code_manifest.is_file() else None
    row_aligned_keys = sorted(
        key for key, value in arrays.items()
        if isinstance(value, np.ndarray) and value.ndim and len(value) == len(timestamps)
    )
    nonrow_shapes = {
        key: list(value.shape) for key, value in arrays.items()
        if isinstance(value, np.ndarray) and (not value.ndim or len(value) != len(timestamps))
    }
    input_hash = _sha256(npz_path)
    active_hash = _sha256(active_path)
    exchange_info = None
    if planner_account:
        if planner_account not in {str(x).split(":", 1)[0] for x in active_set.get("account_admission_keys", [])}:
            raise ValueError(f"planner account {planner_account!r} absent from frozen active-set admissions")
        if not exchange_info_path or not exchange_info_path.is_file():
            raise ValueError("planner replay requires a frozen exchange-info JSON input")
        exchange_info = json.loads(exchange_info_path.read_text())
        symbol = symside.rsplit("_", 1)[0]
        if symbol not in (exchange_info.get("symbols") or {}):
            raise ValueError(f"frozen exchange info has no symbol filters for {symbol}")
    run_started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_root = output_path.parent / (output_path.stem + "_state_provenance")
    runtime_root, runtime_code, canonical_npz_path = _make_runtime(
        code_root, npz_path, symside, output_path
    )
    env_names = (
        "BASE_PATH", "V12_BASE_PATH", "V12_NPZ_DIR", "V12_RUN_ONE_SET_PRECEDENCE",
        "V12_SIM_ALLOW_LIVE_MARKET_FILE", "V12_ALLOW_STALE_NPZ",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    old_sys_path = list(sys.path)
    if str(runtime_code) in sys.path:
        sys.path.remove(str(runtime_code))
    sys.path.insert(0, str(runtime_code))
    os.environ["BASE_PATH"] = str(runtime_code)
    os.environ["V12_BASE_PATH"] = str(runtime_code)
    os.environ["V12_NPZ_DIR"] = str(canonical_npz_path.parent)
    os.environ["V12_RUN_ONE_SET_PRECEDENCE"] = "1"
    os.environ["V12_SIM_ALLOW_LIVE_MARKET_FILE"] = "0"
    os.environ.pop("V12_ALLOW_STALE_NPZ", None)

    import tools.v15_forward_state as forward_state
    import tools.opt.evaluate_v12 as evaluate_v12
    import tools.opt.v12_pilot as pilot_v12
    import tools.npz_cutoffs as npz_cutoffs
    import v12_quick_engine as quick_engine
    if planner_account:
        import tools.vector_direct_trader as vector_direct_trader
        sizing_source = vector_direct_trader.apply_venue_sizing("crypto")
        vector_direct_trader.ROOT = runtime_code
        vector_direct_trader.OUT = provenance_root
        vector_direct_trader.BAR_S = 900
        exchange_cache = provenance_root / "exchange_info_crypto.json"
        exchange_cache.parent.mkdir(parents=True, exist_ok=True)
        exchange_cache.write_text(json.dumps(exchange_info, sort_keys=True))
        binance_sizer = object.__new__(vector_direct_trader.Binance)
        binance_sizer.out = provenance_root
        binance_sizer._xi = None
        broker_position = {}
        done_keys = set()

    real_time_module = time
    original = _install_state_context(
        forward_state, evaluate_v12, pilot_v12, quick_engine, npz_cutoffs,
        canonical_npz_path, runtime_code, active_set, provenance_root,
    )
    counts = {name: 0 for name in sorted(_ACTION_TYPES)}
    state_rows = 0
    invalid_counts = {}
    try:
        with output_path.open("w", encoding="utf-8") as stream:
            header = {
                "record": "replay_manifest",
                "schema_version": 1,
                "sym_side": symside.upper(),
                "window_days": 30,
                "input_npz": str(npz_path),
                "input_npz_sha256": input_hash,
                "active_set": str(active_path),
                "active_set_file_sha256": active_hash,
                "active_set_overrides_sha256": calculated_override_hash,
                "active_set_updated_at": active_set.get("updated_at"),
                "active_set_template_md5": active_set.get("template_md5"),
                "active_set_defaults_round": active_set.get("defaults_round"),
                "active_set_admission_keys": active_set.get("account_admission_keys"),
                "active_set_has_current_admission": bool(active_set.get("account_admission_keys")),
                "planner_account": planner_account,
                "planner_starting_broker_position": "flat" if planner_account else None,
                "planner_done_keys_start": "empty" if planner_account else None,
                "planner_exchange_info_json": str(exchange_info_path) if planner_account else None,
                "planner_exchange_info_sha256": _sha256(exchange_info_path) if planner_account else None,
                "planner_sizing_source": sizing_source if planner_account else None,
                "code_root": str(code_root),
                "runtime_code_root": str(runtime_code),
                "driver_script": str(Path(__file__).resolve()),
                "driver_script_sha256": _sha256(Path(__file__).resolve()),
                "code_manifest": str(code_manifest) if code_manifest else None,
                "code_manifest_sha256": manifest_hash,
                "source_hashes": source_hashes,
                "interval_start_exclusive": _iso(start_ts),
                "interval_end_inclusive": _iso(end_ts),
                "bar_count": len(selected),
                "state_workers": state_workers,
                "state_worker_semantics": "independent causal state_for prefixes; output sorted by bar; planner and simulated fills remain serial" if state_workers > 1 else "serial causal state_for prefixes",
                "npz_row_aligned_array_count": len(row_aligned_keys),
                "npz_row_aligned_keys_sha256": _canonical_sha256(row_aligned_keys),
                "npz_nonrow_array_shapes": nonrow_shapes,
                "npz_open_ts_to_decision_clock_seconds": 900,
                "decision_clock_semantics": "bar open timestamp t is evaluated as closed at t+900s",
                "configuration_semantics": "frozen active override snapshot; retrospectively applied to every historical prefix",
                "npz_freshness_semantics": "actual npz_freshness and npz_cutoffs run on the causal reader prefix with historical decision clock",
                "canonical_loader_semantics": "actual evaluate_v12._store -> v12_quick_engine.load_npz -> htf_causal_align over exact-path NPZ view",
                "replay_started_at_wall_utc": run_started,
                "qty_is_diagnostic_only": True,
            }
            stream.write(json.dumps(header, sort_keys=True, default=str) + "\n")
            cutoff_index = int(np.searchsorted(timestamps, int(start_ts), side="left"))
            if cutoff_index >= len(timestamps) or int(timestamps[cutoff_index]) != int(start_ts):
                raise ValueError("cutoff timestamp must be present in the frozen NPZ")
            cutoff_prefix = row_prefix(arrays, cutoff_index, len(timestamps))
            original["npz_holder"]["npz"] = cutoff_prefix
            cutoff_decision_ts = int(start_ts) + 900
            original["npz_holder"]["decision_ts"] = cutoff_decision_ts
            forward_state.time = _ReplayClock(cutoff_decision_ts, real_time_module)
            cutoff_state = forward_state.state_for(symside, window_days=30)
            if int(cutoff_state.get("npz_tail") or 0) not in (0, int(start_ts)):
                raise AssertionError("cutoff state consumed a bar beyond the initial lifecycle boundary")
            lifecycle = _open_lifecycle_at(cutoff_state.get("ledger"), int(start_ts))
            stream.write(json.dumps({
                "record": "initial_lifecycle",
                "sym_side": symside.upper(),
                "cutoff_npz_open_ts": int(start_ts),
                "cutoff_npz_open_utc": _iso(start_ts),
                "cutoff_decision_clock_ts": cutoff_decision_ts,
                "cutoff_decision_clock_utc": _iso(cutoff_decision_ts),
                "state_for_wall_ts": cutoff_state.get("ts"),
                "prefix_sha256_timestamps_close": _prefix_digest(cutoff_prefix),
                "ok": cutoff_state.get("ok"),
                "reason": cutoff_state.get("reason"),
                "engine_position": {
                    key: cutoff_state.get(key) for key in (
                        "open", "engine_qty", "engine_open_qty", "engine_open_px", "engine_open_bar_ts", "acts_last_bar"
                    )
                },
                "open_lifecycle": lifecycle,
                "settings_sha256": cutoff_state.get("settings_sha256"),
                "ledger_sha256": cutoff_state.get("ledger_sha256"),
                "qty_is_diagnostic_only": True,
            }, sort_keys=True, default=str) + "\n")
            if cutoff_state.get("ok") is not True:
                _why = str(cutoff_state.get("reason") or "cutoff_state_failure")
                invalid_counts[_why] = invalid_counts.get(_why, 0) + 1
            parallel_results = None
            if state_workers > 1:
                chunks = [
                    (npz_path, symside, selected[worker_index::state_workers].tolist(),
                     runtime_code, canonical_npz_path, active_set,
                     provenance_root / f"state_worker_{worker_index:02d}")
                    for worker_index in range(state_workers)
                ]
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=state_workers,
                    mp_context=multiprocessing.get_context("spawn"),
                ) as pool:
                    shard_results = list(pool.map(_parallel_state_chunk, chunks))
                parallel_results = {
                    idx: result for shard in shard_results for idx, result in shard
                }
            for row_number, idx in enumerate(selected, 1):
                bar_ts = int(timestamps[idx])
                decision_ts = bar_ts + 900
                if _expected_closed_bar(decision_ts) != bar_ts:
                    raise AssertionError("decision clock does not resolve to the selected closed bar")
                prefix = row_prefix(arrays, int(idx), len(timestamps))
                original["npz_holder"]["npz"] = prefix
                original["npz_holder"]["decision_ts"] = decision_ts
                if int(prefix["timestamps"][-1]) != bar_ts:
                    raise AssertionError("causal prefix ends after or before its selected signal bar")
                if parallel_results is None:
                    forward_state.time = _ReplayClock(decision_ts, real_time_module)
                    result = forward_state.state_for(symside, window_days=30)
                else:
                    result = parallel_results[int(idx)]
                if int(result.get("npz_tail") or 0) not in (0, bar_ts):
                    raise AssertionError(f"state_for consumed tail {result.get('npz_tail')} beyond selected bar {bar_ts}")
                state_rows += 1
                if result.get("ok") is not True:
                    _why = str(result.get("reason") or "unspecified_state_for_failure")
                    invalid_counts[_why] = invalid_counts.get(_why, 0) + 1
                planner_state = _planner_state(result)
                market_price = float(prefix["close"][-1])
                state_record = {
                    "record": "state",
                    "planner_state": planner_state,
                    "market_price": market_price,
                    "row": row_number,
                    "npz_open_ts": bar_ts,
                    "npz_open_utc": _iso(bar_ts),
                    "decision_clock_ts": decision_ts,
                    "decision_clock_utc": _iso(decision_ts),
                    "state_for_wall_ts": result.get("ts"),
                    "prefix_last_ts": int(prefix["timestamps"][-1]),
                    "prefix_sha256_timestamps_close": _prefix_digest(prefix),
                    "ok": result.get("ok"),
                    "valid": result.get("valid"),
                    "performance_valid": result.get("performance_valid"),
                    "reason": result.get("reason") or result.get("performance_invalid_reason"),
                    "open": result.get("open"),
                    "entry_bar_ts": result.get("entry_bar"),
                    "source": result.get("src"),
                    "settings_sha256": result.get("settings_sha256"),
                    "ledger_sha256": result.get("ledger_sha256"),
                }
                stream.write(json.dumps(state_record, sort_keys=True, default=str) + "\n")
                for event in _actions_at(result.get("ledger"), bar_ts):
                    action = str(event.get("type", "")).upper()
                    counts[action] += 1
                    stream.write(json.dumps({
                        "record": "action",
                        "sym_side": symside.upper(),
                        "npz_open_ts": bar_ts,
                        "npz_open_utc": _iso(bar_ts),
                        "decision_clock_ts": decision_ts,
                        "decision_clock_utc": _iso(decision_ts),
                        "action": action,
                        "qty": event.get("qty"),
                        "price": event.get("price"),
                        "reason": event.get("reason") or event.get("exit_reason"),
                        "ledger_event_ts": event.get("ts"),
                        "bar": event.get("bar"),
                        "qty_is_diagnostic_only": True,
                    }, sort_keys=True, default=str) + "\n")
                if planner_account:
                    states = {symside.upper(): planner_state}
                    allowed_keys = {str(x).upper() for x in active_set.get("account_admission_keys", [])}
                    allowed = lambda account, side: f"{account}:{side}".upper() in allowed_keys
                    orders, notes = vector_direct_trader.plan_account(
                        planner_account, "crypto", [symside.upper()], states,
                        {symside.upper(): dict(broker_position)} if broker_position else {},
                        bar_ts, done_keys, allowed,
                    )
                    if notes:
                        stream.write(json.dumps({
                            "record": "planner_notes", "npz_open_ts": bar_ts,
                            "account": planner_account, "notes": notes,
                        }, sort_keys=True, default=str) + "\n")
                    current_model_types = {str(x.get("type") or "").upper() for x in (planner_state.get("acts_last_bar") or [])}
                    for order in orders:
                        order_type = str(order["act"]).upper()
                        held = float((broker_position or {}).get("qty") or 0.0)
                        fill_qty, size_reason = binance_sizer.size(order, held, market_price)
                        convergence = order_type == "OPEN" and "OPEN" not in current_model_types
                        stream.write(json.dumps({
                            "record": "planner_order", "npz_open_ts": bar_ts,
                            "decision_clock_ts": decision_ts, "account": planner_account,
                            "action": order_type,
                            "classification": "convergence" if convergence else "same_bar_model_action",
                            "model_action_types_at_bar": sorted(current_model_types),
                            "reason": order.get("why"), "order_key": order.get("key"),
                            "requested_notional": order.get("notional"),
                            "requested_qty": order.get("qty"), "held_qty_before": held,
                            "market_price": market_price, "sized_qty": fill_qty,
                            "size_reason": size_reason, "qty_is_diagnostic_only": True,
                        }, sort_keys=True, default=str) + "\n")
                        if not (float(fill_qty) > 0):
                            continue
                        done_keys.add(order["key"])
                        if order_type == "OPEN":
                            broker_position = {"qty": float(fill_qty), "price": market_price}
                        elif order_type == "AUGMENT":
                            old_qty = held
                            new_qty = old_qty + float(fill_qty)
                            old_price = float((broker_position or {}).get("price") or market_price)
                            broker_position = {"qty": new_qty, "price": (old_qty * old_price + float(fill_qty) * market_price) / new_qty}
                        elif order_type == "REDUCE":
                            left = max(0.0, held - float(fill_qty))
                            broker_position = {"qty": left, "price": float((broker_position or {}).get("price") or market_price)} if left > 0 else {}
                        elif order_type == "CLOSE":
                            broker_position = {}
                        stream.write(json.dumps({
                            "record": "planner_fill", "npz_open_ts": bar_ts,
                            "decision_clock_ts": decision_ts, "account": planner_account,
                            "action": order_type, "order_key": order["key"],
                            "filled_qty": float(fill_qty), "broker_qty_after": float((broker_position or {}).get("qty") or 0.0),
                            "broker_price_after": (broker_position or {}).get("price"),
                            "done_key_count": len(done_keys), "qty_is_diagnostic_only": True,
                        }, sort_keys=True, default=str) + "\n")
            trailer = {
                "record": "replay_summary",
                "sym_side": symside.upper(),
                "states_evaluated": state_rows,
                "action_counts": counts,
                "planner_account": planner_account,
                "planner_final_broker_position": broker_position if planner_account else None,
                "planner_done_key_count": len(done_keys) if planner_account else None,
                "invalid_state_counts": invalid_counts,
                "replay_completed_at_wall_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "provenance_output_dir": str(provenance_root),
                "runtime_code_root": str(runtime_root),
            }
            stream.write(json.dumps(trailer, sort_keys=True, default=str) + "\n")
    finally:
        _restore_state_context(
            forward_state, evaluate_v12, pilot_v12, quick_engine, npz_cutoffs, original
        )
        sys.path[:] = old_sys_path
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return {"states_evaluated": state_rows, "action_counts": counts,
            "invalid_state_counts": invalid_counts, "output": str(output_path),
            "runtime_root": str(runtime_root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npz", required=True, type=Path)
    parser.add_argument("--active-set", required=True, type=Path)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--start-ts", required=True, type=int, help="exclusive bar-open timestamp")
    parser.add_argument("--end-ts", required=True, type=int, help="inclusive bar-open timestamp")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--code-manifest", type=Path)
    parser.add_argument("--limit", type=int, help="process only the first N selected bars (proof mode)")
    parser.add_argument("--planner-account", help="also replay the actual vector_direct_trader plan_account for this frozen admitted account")
    parser.add_argument("--exchange-info-json", type=Path, help="frozen Binance exchange-info cache required by planner sizing")
    parser.add_argument("--state-workers", type=int, default=1, help="parallel independent state_for evaluations; planner remains ordered in the parent")
    args = parser.parse_args(argv)
    print(json.dumps(replay(
        args.npz, args.active_set, args.sym_side, args.start_ts, args.end_ts,
        args.out, args.code_root.resolve(), args.code_manifest, args.limit,
        args.planner_account, args.exchange_info_json, args.state_workers,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
