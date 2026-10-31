#!/usr/bin/env python3
"""Read-only exact-input account partial-execution projection audit."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def _canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value):
    return _sha256_bytes(_canonical_bytes(value))


def _engine_ast_sha256(path, source_bytes=None):
    source = (Path(path).read_bytes() if source_bytes is None else source_bytes).decode(
        "utf-8"
    )
    tree = ast.parse(source)
    return _sha256_bytes(ast.dump(tree, include_attributes=False).encode())


def _load_engine_source(path):
    engine_path = Path(path).resolve()
    source_bytes = engine_path.read_bytes()
    source_sha256 = _sha256_bytes(source_bytes)
    source_ast_sha256 = _engine_ast_sha256(engine_path, source_bytes)
    spec = importlib.util.spec_from_file_location("v12_quick_engine", engine_path)
    if spec is None or spec.loader is None:
        raise ValueError("engine module path is not importable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source_bytes, str(engine_path), "exec"), module.__dict__)
    module._audit_loaded_source_sha256 = source_sha256
    module._audit_loaded_source_ast_sha256 = source_ast_sha256
    if _sha256_bytes(engine_path.read_bytes()) != source_sha256:
        raise ValueError("engine source changed while loading")
    return module, source_ast_sha256, source_sha256


def _array_hash(value):
    import numpy as np

    array = np.ascontiguousarray(value)
    return _sha256_bytes(array.view(np.uint8))


def _epoch(value):
    from tools.account_execution_state import _epoch as account_epoch

    return account_epoch(value, "timestamp")


def _state_by_hash(root, state_sha256):
    matches = list(
        (root / "data" / "vector_direct" / "decision_states").glob(
            f"**/{state_sha256}.json"
        )
    )
    if len(matches) != 1:
        raise ValueError(f"expected one immutable decision state, found {len(matches)}")
    path = matches[0]
    payload = json.loads(path.read_text())
    if _canonical_sha256(payload) != state_sha256:
        raise ValueError("decision-state content hash mismatch")
    return path, payload


def _snapshot_for(root, account, sym_side):
    base = (
        root
        / "data"
        / "vector_direct"
        / "account_execution"
        / "snapshots"
        / account
        / sym_side
    )
    pointer_path = base / "latest.json"
    pointer = json.loads(pointer_path.read_text())
    relative = Path(pointer["snapshot_path"])
    path = (
        relative
        if relative.is_absolute()
        else (root / "data" / "vector_direct" / "account_execution" / relative)
    )
    snapshot = json.loads(path.read_text())
    copied = dict(snapshot)
    claimed = copied.pop("snapshot_sha256", None)
    actual = _canonical_sha256(copied)
    if claimed != actual or pointer.get("snapshot_sha256") != actual:
        raise ValueError("snapshot content-address verification failed")
    if snapshot.get("account") != account or snapshot.get("sym_side") != sym_side:
        raise ValueError("snapshot identity mismatch")
    return pointer_path, path, snapshot


def _active_settings(root, sym_side):
    db = root / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.is_file():
        raise ValueError("canonical per-symbol settings database is absent")
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT updated_at,full_config_json,template_md5,defaults_round FROM per_sym_active WHERE sym_side=?",
            (sym_side,),
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[1]:
        raise ValueError(
            "no active full settings row; historical/current fallback is forbidden"
        )
    full = json.loads(row[1])
    if not isinstance(full, dict) or not full:
        raise ValueError("active full settings row is malformed")
    return {
        "path": str(db),
        "sha256": _sha256_bytes(_canonical_bytes(full)),
        "updated_at": row[0],
        "template_md5": row[2],
        "defaults_round": row[3],
    }


def _filter_lot(root, symbol, expected_hash):
    path = root / "data" / "vector_direct" / "exchange_info_crypto.json"
    if not path.is_file():
        raise ValueError("market filter source is absent")
    document = json.loads(path.read_text())
    fetched = document.get("_fetched")
    symbols = document.get("symbols")
    if isinstance(symbols, dict):
        filters = symbols.get(symbol)
    else:
        matches = [
            row
            for row in symbols or []
            if isinstance(row, dict) and row.get("symbol") == symbol
        ]
        filters = matches[0].get("filters") if len(matches) == 1 else None
    if not isinstance(filters, (dict, list)):
        raise ValueError("exchange filter row is absent or malformed")
    source = {
        "venue": "crypto",
        "symbol": symbol,
        "filters": filters,
        "fetched_at": fetched,
    }
    source_hash = _sha256_bytes(_canonical_bytes(source))
    if source_hash != expected_hash:
        raise ValueError(
            "current market-filter source does not match broker snapshot filter hash"
        )
    if isinstance(filters, dict):
        step_size = filters.get("step")
        min_qty = filters.get("min_qty")
        filter_type = "cached_market_lot_size"
    else:
        lot = next(
            (f for f in filters if f.get("filterType") == "MARKET_LOT_SIZE"), None
        )
        if lot is None:
            lot = next((f for f in filters if f.get("filterType") == "LOT_SIZE"), None)
        if lot is None:
            raise ValueError("market lot filter is absent")
        step_size = lot.get("stepSize")
        min_qty = lot.get("minQty")
        filter_type = lot.get("filterType")
    return {
        "market_lot": {"step_size": str(step_size), "min_qty": str(min_qty)},
        "source": str(path),
        "source_file_sha256": _sha256_bytes(path.read_bytes()),
        "filter_source_sha256": source_hash,
        "filter_type": filter_type,
    }


def _ledger_hash(result):
    ledger = result.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("canonical engine did not return a ledger")
    return _canonical_sha256(ledger), ledger


def _safe_event(event, observation_bar_ts):
    """Keep structural event evidence while omitting every PnL/return field."""
    if not isinstance(event, dict):
        return None
    bar = event.get("bar_exit", event.get("bar", event.get("bar_entry")))
    is_observation = False
    if observation_bar_ts is None:
        is_observation = True
    else:
        try:
            is_observation = int(bar) == int(observation_bar_ts)
        except (TypeError, ValueError):
            pass
    projection = event.get("account_partial_projection")
    if not is_observation and projection is None:
        return None
    keep = (
        "type",
        "bar",
        "bar_entry",
        "bar_exit",
        "ts",
        "qty",
        "price",
        "qty_before",
        "qty_after",
        "qty_frac",
        "seeded_prewindow",
        "unrealised",
        "account_partial_projection",
    )
    safe = {key: event[key] for key in keep if key in event}
    if "pnl_is_lifecycle" in event:
        safe["lifecycle_terminal_marker"] = bool(event["pnl_is_lifecycle"])
    return safe


def _run_audit_with_engine(
    root,
    account,
    sym_side,
    *,
    max_snapshot_age_s=30.0,
    window_days=30,
    npz_dir=None,
    baseline_engine_module_path=None,
    candidate_engine_module_path=None,
):
    """Run only after every consumed source link and baseline ledger hash verifies."""
    root = Path(root).resolve()
    account = str(account).strip().lower()
    sym_side = str(sym_side).strip().upper()
    symbol, side = sym_side.rsplit("_", 1)
    if side not in ("LONG", "SHORT"):
        raise ValueError("sym_side must end with LONG or SHORT")
    state_pointer, state = None, None
    pointer_path, snapshot_path, snapshot = _snapshot_for(root, account, sym_side)
    state_path, state = _state_by_hash(root, snapshot.get("state_sha256", ""))
    state_pointer = snapshot.get("state_sha256")
    if state.get("sym_side") != sym_side or state.get("ok") is not True:
        raise ValueError(
            "consumed decision state is invalid or has a different identity"
        )
    settings = _active_settings(root, sym_side)
    if settings["sha256"] != state.get("settings_sha256") or settings[
        "updated_at"
    ] != state.get("settings_updated_at"):
        raise ValueError(
            "active full settings generation does not match the consumed state"
        )
    if snapshot.get("settings_sha256") != state.get("settings_sha256"):
        raise ValueError(
            "snapshot settings hash does not match consumed decision state"
        )
    if snapshot.get("state_sha256") != state_pointer:
        raise ValueError("snapshot does not point to the verified decision-state hash")
    snapshot_age = time.time() - _epoch(snapshot.get("observed_at"))
    if snapshot_age < 0 or snapshot_age > max_snapshot_age_s:
        raise ValueError("broker snapshot is stale or future-dated")
    if float(snapshot.get("qty") or 0.0) <= 0:
        raise ValueError("flat snapshot cannot seed a held-position partial projection")
    if snapshot.get("broker_source") not in (
        "Binance.positionRisk",
        "Tradier.account.positions",
    ):
        raise ValueError("snapshot broker source is not recognized")
    if snapshot.get("broker_source") != "Binance.positionRisk":
        raise ValueError(
            "this audit currently accepts Binance crypto execution snapshots only"
        )
    filter_info = _filter_lot(root, symbol, snapshot.get("filter_sha256"))

    sys.path.insert(0, str(root))
    from tools.opt import evaluate_v12 as evaluate_v12
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    from tools.v15_forward_state import _stale_anchor
    import v12_quick_engine as engine

    evaluate_v12._STORES.clear()
    npz_path = None
    npz_file_sha256 = None
    original_engine_load_npz = engine.load_npz
    original_eval_root = evaluate_v12.ROOT
    if npz_dir:
        npz_dir = Path(npz_dir).resolve()
        npz_path = npz_dir / f"{symbol}.npz"
        if not npz_path.is_file():
            raise ValueError(f"explicit frozen NPZ is absent: {npz_path}")
        npz_file_sha256 = _sha256_bytes(npz_path.read_bytes())
        engine.load_npz = (
            lambda mode, symbols, start_date, npz_dir_arg="": original_engine_load_npz(
                mode, symbols, start_date, npz_dir=str(npz_dir)
            )
        )
        evaluate_v12.ROOT = npz_dir.parent.parent
    try:
        if npz_path:
            from tools.npz_merged_view import merged_npz_reads

            with merged_npz_reads([npz_path]):
                prepared = prepare_batch(sym_side, window_days)
        else:
            prepared = prepare_batch(sym_side, window_days)
    finally:
        engine.load_npz = original_engine_load_npz
        evaluate_v12.ROOT = original_eval_root
    if not isinstance(prepared, dict) or prepared.get("_stale"):
        raise ValueError("frozen evaluator preparation failed")
    npz = prepared.get("npz_prepared")
    if not isinstance(npz, dict):
        raise ValueError("prepared replay lacks the canonical NPZ arrays")
    tail_values = npz.get("timestamps")
    if tail_values is None or len(tail_values) == 0:
        raise ValueError("prepared replay has no timestamp array")
    tail = int(tail_values[-1])
    input_hashes = {
        key: _array_hash(npz[key]) for key in ("timestamps", "close") if key in npz
    }
    if input_hashes != state.get("input_sha256"):
        raise ValueError(
            "prepared timestamps/close hashes differ from the consumed immutable state"
        )
    if tail != int(state.get("npz_tail") or 0):
        raise ValueError("prepared NPZ tail differs from the consumed state")
    observation = int(_epoch(snapshot.get("bar_ts")))
    if observation != tail:
        raise ValueError(
            "broker observation is not the exact current/last prepared bar"
        )
    if int(_epoch(snapshot.get("observed_at"))) < observation:
        raise ValueError("broker observation predates its recorded decision bar")

    from tools.v15_forward_state import _drop_retired, _load_overrides

    settings_env = os.environ.get("PER_SYM_STORE_DB")
    os.environ["PER_SYM_STORE_DB"] = settings["path"]
    try:
        overrides, source, setting_meta = _load_overrides(sym_side)
    finally:
        if settings_env is None:
            os.environ.pop("PER_SYM_STORE_DB", None)
        else:
            os.environ["PER_SYM_STORE_DB"] = settings_env
    if (
        source != "per_sym_store.active"
        or setting_meta.get("settings_sha256") != settings["sha256"]
        or setting_meta.get("settings_updated_at") != settings["updated_at"]
    ):
        raise ValueError(
            "canonical evaluator settings loader did not resolve the exact consumed active generation"
        )
    _drop_retired(overrides)
    stale_anchor = _stale_anchor(sym_side)
    if stale_anchor:
        overrides = {
            **overrides,
            "STALE_SET_RECALC_EPOCH": stale_anchor["STALE_SET_RECALC_EPOCH"],
            "STALE_SET_APPLY_EPOCH": stale_anchor["STALE_SET_APPLY_EPOCH"],
        }

    source_engine_ast = state.get("engine_ast_sha256")
    baseline_engine_path = Path(baseline_engine_module_path).resolve()
    candidate_engine_path = Path(candidate_engine_module_path).resolve()
    baseline_engine_sha = engine._audit_loaded_source_sha256
    baseline_engine_ast = engine._audit_loaded_source_ast_sha256
    baseline = evaluate_prepared_sanitized(
        prepared, dict(overrides), window_days, include_ledger=True
    )
    baseline_ledger_sha, baseline_ledger = _ledger_hash(baseline)
    consumed_ledger_sha = state.get("ledger_sha256")
    if baseline_ledger_sha != consumed_ledger_sha:
        consumed_ledger = (
            state.get("ledger") if isinstance(state.get("ledger"), list) else []
        )
        ledger_differences = []
        for index in range(max(len(consumed_ledger), len(baseline_ledger))):
            consumed_row = (
                _safe_event(consumed_ledger[index], None)
                if index < len(consumed_ledger)
                else None
            )
            candidate_row = (
                _safe_event(baseline_ledger[index], None)
                if index < len(baseline_ledger)
                else None
            )
            if consumed_row != candidate_row:
                ledger_differences.append(
                    {
                        "index": index,
                        "consumed": consumed_row,
                        "candidate": candidate_row,
                    }
                )
        return {
            "schema_version": 1,
            "audit_type": "read_only_current_bar_account_projection",
            "status": "REFUSED_BASELINE_LEDGER_MISMATCH",
            "labels": [
                "projection_not_run",
                "not_realized_fill",
                "not_account_history_parity",
            ],
            "identity": {"account_key": account, "sym_side": sym_side},
            "source": {
                "snapshot_pointer_path": str(pointer_path),
                "snapshot_pointer_sha256": _sha256_bytes(pointer_path.read_bytes()),
                "snapshot_path": str(snapshot_path),
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "decision_state_path": str(state_path),
                "decision_state_sha256": state_pointer,
                "settings_source_path": settings["path"],
                "settings_sha256": settings["sha256"],
                "settings_updated_at": settings["updated_at"],
                "engine_source_ast_sha256": source_engine_ast,
                "baseline_engine_file_sha256": baseline_engine_sha,
                "baseline_engine_ast_sha256": baseline_engine_ast,
                "baseline_engine_source_path": str(baseline_engine_path),
                "npz_input_sha256": input_hashes,
                "npz_file_path": str(npz_path) if npz_path else None,
                "npz_file_sha256": npz_file_sha256,
                "market_lot": filter_info,
            },
            "window": {
                "first_bar_ts": int(tail_values[0]),
                "last_bar_ts": tail,
                "observation_bar_ts": observation,
                "bar_count": len(tail_values),
            },
            "baseline_gate": {
                "passed": False,
                "consumed_ledger_sha256": consumed_ledger_sha,
                "candidate_no_context_ledger_sha256": baseline_ledger_sha,
                "consumed_event_count": len(consumed_ledger),
                "candidate_event_count": len(baseline_ledger),
                "full_structural_differences": ledger_differences,
            },
            "projection": {
                "status": "NOT_RUN_BASELINE_MISMATCH",
                "snapshot_observed_at": snapshot["observed_at"],
                "snapshot_age_seconds_at_run": round(snapshot_age, 3),
            },
        }
    perf_reason = str(baseline.get("invalid_reason") or "")
    baseline_completed = baseline.get("valid") is True or any(
        perf_reason.startswith(prefix) for prefix in ("trades ", "TIM ", "DD ")
    )
    if not baseline_completed:
        raise ValueError("canonical no-context baseline is invalid; projection refused")

    candidate_engine = engine
    if baseline_engine_sha != _sha256_bytes(candidate_engine_path.read_bytes()):
        candidate_engine, _, _ = _load_engine_source(candidate_engine_path)
        sys.modules["v12_quick_engine"] = candidate_engine
        candidate_baseline = evaluate_prepared_sanitized(
            prepared, dict(overrides), window_days, include_ledger=True
        )
        candidate_baseline_sha, candidate_baseline_ledger = _ledger_hash(
            candidate_baseline
        )
        if candidate_baseline_sha != baseline_ledger_sha:
            consumed_ledger = (
                state.get("ledger") if isinstance(state.get("ledger"), list) else []
            )
            candidate_differences = []
            for index in range(
                max(len(baseline_ledger), len(candidate_baseline_ledger))
            ):
                baseline_row = (
                    _safe_event(baseline_ledger[index], None)
                    if index < len(baseline_ledger)
                    else None
                )
                candidate_row = (
                    _safe_event(candidate_baseline_ledger[index], None)
                    if index < len(candidate_baseline_ledger)
                    else None
                )
                if baseline_row != candidate_row:
                    candidate_differences.append(
                        {
                            "index": index,
                            "baseline": baseline_row,
                            "candidate": candidate_row,
                        }
                    )
            return {
                "schema_version": 1,
                "audit_type": "read_only_current_bar_account_projection",
                "status": "REFUSED_CANDIDATE_BASELINE_MISMATCH",
                "labels": [
                    "projection_not_run",
                    "candidate_no_context_ledger_mismatch",
                ],
                "identity": {"account_key": account, "sym_side": sym_side},
                "source": {
                    "decision_state_path": str(state_path),
                    "decision_state_sha256": state_pointer,
                    "npz_input_sha256": input_hashes,
                    "npz_file_path": str(npz_path) if npz_path else None,
                    "npz_file_sha256": npz_file_sha256,
                    "baseline_engine_source_path": str(baseline_engine_path),
                    "baseline_engine_file_sha256": baseline_engine_sha,
                    "baseline_engine_ast_sha256": baseline_engine_ast,
                    "candidate_engine_source_path": str(candidate_engine_path),
                    "candidate_engine_file_sha256": candidate_engine._audit_loaded_source_sha256,
                    "candidate_engine_ast_sha256": candidate_engine._audit_loaded_source_ast_sha256,
                },
                "baseline_gate": {
                    "passed": False,
                    "consumed_ledger_sha256": consumed_ledger_sha,
                    "baseline_ledger_sha256": baseline_ledger_sha,
                    "candidate_no_context_ledger_sha256": candidate_baseline_sha,
                    "consumed_event_count": len(consumed_ledger),
                    "candidate_event_count": len(candidate_baseline_ledger),
                    "structural_differences_vs_baseline": candidate_differences,
                },
                "projection": {"status": "NOT_RUN_CANDIDATE_BASELINE_MISMATCH"},
            }
    engine = candidate_engine
    sys.modules["v12_quick_engine"] = engine
    candidate_engine_sha = engine._audit_loaded_source_sha256
    candidate_engine_ast = engine._audit_loaded_source_ast_sha256

    context = {
        "schema_version": 1,
        "account_key": account,
        "symbol": symbol,
        "side": side,
        "window_first_ts": int(tail_values[0]),
        "window_last_ts": tail,
        "observation_bar_ts": observation,
        "observed_qty": str(snapshot["qty"]),
        "market_lot": filter_info["market_lot"],
        "decision_generation": str(state["settings_updated_at"]),
        "config_sha256": str(state["settings_sha256"]),
    }

    original_simulate = engine.simulate_one
    simulation_results = []

    def simulate_with_context(
        npz_arg, sym_arg, is_long_arg, cfg, force_initial_seed=False
    ):
        import copy

        projected_cfg = copy.copy(cfg)
        object.__setattr__(projected_cfg, "_PARTIAL_EXECUTION_CONTEXT", context)
        simulation_result = original_simulate(
            npz_arg,
            sym_arg,
            is_long_arg,
            projected_cfg,
            force_initial_seed=force_initial_seed,
        )
        simulation_results.append(simulation_result)
        return simulation_result

    engine.simulate_one = simulate_with_context
    try:
        projected = evaluate_prepared_sanitized(
            prepared, dict(overrides), window_days, include_ledger=True
        )
    finally:
        engine.simulate_one = original_simulate
    projected_ledger_sha, projected_ledger = _ledger_hash(projected)
    raw_result = simulation_results[-1] if simulation_results else None
    account_audit = (
        raw_result.get("account_partial_audit")
        if isinstance(raw_result, dict)
        else None
    )
    if not isinstance(account_audit, dict):
        raise ValueError("engine did not return its account partial-projection audit")
    events = [_safe_event(row, observation) for row in projected_ledger]
    events = [row for row in events if row]
    projection_events = [row for row in events if "account_partial_projection" in row]
    if any(
        row["account_partial_projection"].get("projection_only_not_a_fill") is not True
        for row in projection_events
    ):
        raise ValueError("engine projection event lacks the not-a-fill marker")
    return {
        "schema_version": 1,
        "audit_type": "read_only_current_bar_account_projection",
        "labels": [
            "projection_only_not_a_fill",
            "not_realized_fill",
            "not_account_history_parity",
        ],
        "identity": {"account_key": account, "sym_side": sym_side},
        "source": {
            "snapshot_pointer_path": str(pointer_path),
            "snapshot_pointer_sha256": _sha256_bytes(pointer_path.read_bytes()),
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "decision_state_path": str(state_path),
            "decision_state_sha256": state_pointer,
            "settings_source_path": settings["path"],
            "settings_sha256": settings["sha256"],
            "settings_updated_at": settings["updated_at"],
            "engine_source_ast_sha256": source_engine_ast,
            "baseline_engine_file_sha256": baseline_engine_sha,
            "baseline_engine_ast_sha256": baseline_engine_ast,
            "baseline_engine_source_path": str(baseline_engine_path),
            "candidate_engine_file_sha256": candidate_engine_sha,
            "candidate_engine_ast_sha256": candidate_engine_ast,
            "candidate_engine_source_path": str(candidate_engine_path),
            "npz_input_sha256": input_hashes,
            "npz_file_path": str(npz_path) if npz_path else None,
            "npz_file_sha256": npz_file_sha256,
            "market_lot": filter_info,
        },
        "window": {
            "first_bar_ts": int(tail_values[0]),
            "last_bar_ts": tail,
            "observation_bar_ts": observation,
            "bar_count": len(tail_values),
        },
        "baseline_gate": {
            "passed": True,
            "consumed_ledger_sha256": consumed_ledger_sha,
            "candidate_no_context_ledger_sha256": baseline_ledger_sha,
            "equal": baseline_ledger_sha == consumed_ledger_sha,
        },
        "projection": {
            "snapshot_observed_at": snapshot["observed_at"],
            "snapshot_age_seconds_at_run": round(snapshot_age, 3),
            "observed_qty": str(snapshot["qty"]),
            "candidate_ledger_sha256": projected_ledger_sha,
            "account_partial_audit": account_audit,
            "observation_bar_events": events,
            "projection_event_count": len(projection_events),
        },
    }


def run_audit(
    root,
    account,
    sym_side,
    *,
    max_snapshot_age_s=30.0,
    window_days=30,
    npz_dir=None,
    engine_module_path=None,
    baseline_engine_module_path=None,
    candidate_engine_module_path=None,
):
    root = Path(root).resolve()
    account = str(account).strip().lower()
    sym_side = str(sym_side).strip().upper()
    pointer_path, _, snapshot = _snapshot_for(root, account, sym_side)
    _, state = _state_by_hash(root, snapshot.get("state_sha256", ""))
    expected_ast = state.get("engine_ast_sha256")
    if engine_module_path:
        baseline_engine_module_path = baseline_engine_module_path or engine_module_path
        candidate_engine_module_path = (
            candidate_engine_module_path or engine_module_path
        )
    baseline_engine_path = Path(
        baseline_engine_module_path or (root / "v12_quick_engine.py")
    ).resolve()
    candidate_engine_path = Path(
        candidate_engine_module_path or baseline_engine_path
    ).resolve()
    try:
        baseline_module, actual_ast, source_sha256 = _load_engine_source(
            baseline_engine_path
        )
    except (OSError, SyntaxError, UnicodeError) as exc:
        return {
            "schema_version": 1,
            "audit_type": "read_only_current_bar_account_projection",
            "status": "REFUSED_ENGINE_IDENTITY",
            "labels": ["projection_not_run", "engine_identity_unverified"],
            "identity": {"account_key": account, "sym_side": sym_side},
            "engine_identity": {
                "expected_ast_sha256": expected_ast,
                "source_path": str(baseline_engine_path),
                "error": f"{type(exc).__name__}: {exc}"[:200],
            },
        }
    if not expected_ast or actual_ast != expected_ast:
        return {
            "schema_version": 1,
            "audit_type": "read_only_current_bar_account_projection",
            "status": "REFUSED_ENGINE_IDENTITY",
            "labels": ["projection_not_run", "engine_identity_mismatch"],
            "identity": {"account_key": account, "sym_side": sym_side},
            "source": {
                "snapshot_pointer_path": str(pointer_path),
                "decision_state_sha256": snapshot.get("state_sha256"),
            },
            "engine_identity": {
                "expected_ast_sha256": expected_ast,
                "baseline_ast_sha256": actual_ast,
                "baseline_source_sha256": source_sha256,
                "baseline_source_path": str(baseline_engine_path),
            },
        }
    previous = sys.modules.get("v12_quick_engine")
    sys.modules["v12_quick_engine"] = baseline_module
    try:
        return _run_audit_with_engine(
            root,
            account,
            sym_side,
            max_snapshot_age_s=max_snapshot_age_s,
            window_days=window_days,
            npz_dir=npz_dir,
            baseline_engine_module_path=baseline_engine_path,
            candidate_engine_module_path=candidate_engine_path,
        )
    except Exception as exc:
        return {
            "schema_version": 1,
            "audit_type": "read_only_current_bar_account_projection",
            "status": "REFUSED",
            "reason": f"{type(exc).__name__}: {exc}"[:300],
            "projection": {"status": "NOT_RUN"},
        }
    finally:
        if previous is None:
            sys.modules.pop("v12_quick_engine", None)
        else:
            sys.modules["v12_quick_engine"] = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", required=True, type=Path, help="Read-only trading repository root"
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument(
        "--output", type=Path, help="Write one JSON audit result; stdout if omitted"
    )
    parser.add_argument("--max-snapshot-age-s", type=float, default=30.0)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument(
        "--npz-dir",
        type=Path,
        help="Explicit frozen indicator directory when outside --root",
    )
    parser.add_argument(
        "--engine-module-path",
        type=Path,
        help="Compatibility alias: use this engine for both baseline and candidate",
    )
    parser.add_argument(
        "--baseline-engine-module-path",
        type=Path,
        help="Exact engine source identity recorded by the consumed forward state",
    )
    parser.add_argument(
        "--candidate-engine-module-path",
        type=Path,
        help="Candidate engine; its no-context ledger must first match the baseline",
    )
    args = parser.parse_args()
    try:
        result = run_audit(
            args.root,
            args.account,
            args.sym_side,
            max_snapshot_age_s=args.max_snapshot_age_s,
            window_days=args.window_days,
            npz_dir=args.npz_dir,
            engine_module_path=args.engine_module_path,
            baseline_engine_module_path=args.baseline_engine_module_path,
            candidate_engine_module_path=args.candidate_engine_module_path,
        )
    except Exception as exc:
        result = {
            "schema_version": 1,
            "audit_type": "read_only_current_bar_account_projection",
            "status": "REFUSED",
            "reason": f"{type(exc).__name__}: {exc}"[:300],
            "projection": {"status": "NOT_RUN"},
        }
    encoded = json.dumps(result, sort_keys=True, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
