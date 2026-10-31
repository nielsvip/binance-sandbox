#!/usr/bin/env python3
"""Read-only causal replay of v15_forward_state.state_for over frozen NPZ prefixes.

Each observation replays the real configured 30-day vector state ending at a 15m
NPZ bar-open timestamp. The observation is timestamped at the following bar close
(t + 900s). Outputs are diagnostic artifacts only; active settings and source files
are supplied as immutable snapshots and are never written by this driver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def _actions_at(ledger, bar_ts: int):
    return [
        event for event in (ledger or [])
        if isinstance(event, dict)
        and str(event.get("type", "")).upper() in _ACTION_TYPES
        and int(float(event.get("ts") or -1)) == int(bar_ts)
    ]


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
        Path("tools/opt/v12_pilot.py"),
        Path("tools/opt/evaluate_v12.py"),
        Path("v12_quick_engine.py"),
        Path("cat_side_defaults.py"),
        Path("per_sym_live_set.py"),
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


def _install_state_context(forward_state, evaluate_v12, pilot_v12, frozen_npz,
                           active_set, provenance_root: Path):
    """Patch only data providers around the actual state_for implementation."""
    original = {
        "load_overrides": forward_state._load_overrides,
        "out": forward_state.OUT,
        "state_time": forward_state.time,
        "store": evaluate_v12._store,
        "freshness": pilot_v12.npz_freshness,
    }
    overrides = dict(active_set["overrides"])
    meta = {
        "settings_updated_at": active_set.get("updated_at"),
        "settings_sha256": active_set["overrides_sha256"],
        "template_md5": active_set.get("template_md5"),
        "defaults_round": active_set.get("defaults_round"),
        "active_set_source": active_set.get("source"),
    }
    holder = {"npz": frozen_npz}
    forward_state._load_overrides = lambda _ss: (dict(overrides), "replay.frozen_active_set", dict(meta))
    forward_state.OUT = provenance_root
    pilot_v12.npz_freshness = lambda _ss: (True, "frozen causal replay input")
    evaluate_v12._store = lambda _sym, _mode, _start: holder["npz"]
    original["npz_holder"] = holder
    return original


def _restore_state_context(forward_state, evaluate_v12, pilot_v12, original):
    forward_state._load_overrides = original["load_overrides"]
    forward_state.OUT = original["out"]
    forward_state.time = original["state_time"]
    evaluate_v12._store = original["store"]
    pilot_v12.npz_freshness = original["freshness"]


def replay(npz_path: Path, active_path: Path, symside: str, start_ts: int,
           end_ts: int, output_path: Path, code_root: Path, code_manifest: Path | None = None,
           limit: int | None = None):
    import tools.v15_forward_state as forward_state
    import tools.opt.evaluate_v12 as evaluate_v12
    import tools.opt.v12_pilot as pilot_v12

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

    source_hashes = _source_hashes(code_root)
    manifest_hash = _sha256(code_manifest) if code_manifest and code_manifest.is_file() else None
    input_hash = _sha256(npz_path)
    active_hash = _sha256(active_path)
    run_started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_root = output_path.parent / (output_path.stem + "_state_provenance")
    real_time_module = time
    original = _install_state_context(
        forward_state, evaluate_v12, pilot_v12, arrays, active_set, provenance_root
    )
    original_clock = forward_state.time
    counts = {name: 0 for name in sorted(_ACTION_TYPES)}
    state_rows = 0
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
                "code_root": str(code_root),
                "code_manifest": str(code_manifest) if code_manifest else None,
                "code_manifest_sha256": manifest_hash,
                "source_hashes": source_hashes,
                "interval_start_exclusive": _iso(start_ts),
                "interval_end_inclusive": _iso(end_ts),
                "bar_count": len(selected),
                "npz_open_ts_to_decision_clock_seconds": 900,
                "decision_clock_semantics": "bar open timestamp t is evaluated as closed at t+900s",
                "configuration_semantics": "frozen active override snapshot; retrospectively applied to every historical prefix",
                "replay_started_at_wall_utc": run_started,
                "qty_is_diagnostic_only": True,
            }
            stream.write(json.dumps(header, sort_keys=True, default=str) + "\n")
            for row_number, idx in enumerate(selected, 1):
                bar_ts = int(timestamps[idx])
                decision_ts = bar_ts + 900
                if _expected_closed_bar(decision_ts) != bar_ts:
                    raise AssertionError("decision clock does not resolve to the selected closed bar")
                prefix = row_prefix(arrays, int(idx), len(timestamps))
                original["npz_holder"]["npz"] = prefix
                if int(prefix["timestamps"][-1]) != bar_ts:
                    raise AssertionError("causal prefix ends after or before its selected signal bar")
                forward_state.time = _ReplayClock(decision_ts, real_time_module)
                result = forward_state.state_for(symside, window_days=30)
                if int(result.get("npz_tail") or 0) not in (0, bar_ts):
                    raise AssertionError(f"state_for consumed tail {result.get('npz_tail')} beyond selected bar {bar_ts}")
                state_rows += 1
                state_record = {
                    "record": "state",
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
                action_rows = _actions_at(result.get("ledger"), bar_ts)
                for event in action_rows:
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
            trailer = {
                "record": "replay_summary",
                "sym_side": symside.upper(),
                "states_evaluated": state_rows,
                "action_counts": counts,
                "replay_completed_at_wall_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "provenance_output_dir": str(provenance_root),
            }
            stream.write(json.dumps(trailer, sort_keys=True, default=str) + "\n")
    finally:
        _restore_state_context(forward_state, evaluate_v12, pilot_v12, original)
    return {"states_evaluated": state_rows, "action_counts": counts, "output": str(output_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npz", required=True, type=Path)
    parser.add_argument("--active-set", required=True, type=Path)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--start-ts", required=True, type=int, help="exclusive bar-open timestamp")
    parser.add_argument("--end-ts", required=True, type=int, help="inclusive bar-open timestamp")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--code-manifest", type=Path)
    parser.add_argument("--limit", type=int, help="process only the first N selected bars (proof mode)")
    args = parser.parse_args(argv)
    code_root = args.code_root.resolve()
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))
    os.environ.setdefault("V15_SIM_ALLOW_LIVE_MARKET_FILE", "0")
    print(json.dumps(replay(
        args.npz, args.active_set, args.sym_side, args.start_ts, args.end_ts,
        args.out, code_root, args.code_manifest, args.limit,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
