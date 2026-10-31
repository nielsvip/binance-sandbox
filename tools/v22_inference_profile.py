#!/usr/bin/env python3
"""Stream timing evidence from pilot, coverage, and run-manifest artifacts.

This profiler reports measured durations by source and scope. It deliberately
does not combine wall time, CPU time, worker time, or potentially overlapping
phase durations into one total.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


SCHEMA = "v22_inference_runtime_profile/1"
MAX_JSON_BYTES = 16 * 1024 * 1024
PHASE_RE = re.compile(r"\[V17-PHASE\]\s+([A-Za-z0-9_.-]+).*?\bwall(?:_s)?\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)")
TS_RE = re.compile(r"(20\d\d-\d\d-\d\d[T ][0-9:.]+Z?)")


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _duration_bucket():
    return {"count": 0, "sum_s": 0.0, "min_s": None, "max_s": None}


def _add_duration(bucket, value):
    number = _number(value)
    if number is None:
        return False
    bucket["count"] += 1
    bucket["sum_s"] += number
    bucket["min_s"] = number if bucket["min_s"] is None else min(bucket["min_s"], number)
    bucket["max_s"] = number if bucket["max_s"] is None else max(bucket["max_s"], number)
    return True


def _finish_bucket(bucket):
    result = dict(bucket)
    if result["count"]:
        result["mean_s"] = result["sum_s"] / result["count"]
    else:
        result["mean_s"] = None
    result["sum_s"] = round(result["sum_s"], 9)
    for key in ("min_s", "max_s", "mean_s"):
        if result[key] is not None:
            result[key] = round(result[key], 9)
    return result


def _open_source(source):
    path = str(source["path"])
    host = source.get("host")
    if not host or host in ("local", "localhost"):
        return open(path, "rb"), None
    command = "cat -- " + shlex.quote(path)
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(host), command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout, proc


def _read_small_json(stream, limit=MAX_JSON_BYTES):
    chunks = []
    size = 0
    while True:
        chunk = stream.read(min(65536, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            raise ValueError(f"JSON source exceeded {limit} byte cap")
    raw = b"".join(chunks)
    return raw, json.loads(raw.decode("utf-8"))


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _record_timestamp(record):
    for key in ("ts", "timestamp", "observed_at", "started_at", "finished_at", "computed_at_utc", "at_utc"):
        value = _timestamp(record.get(key))
        if value:
            return value
    return None


def _add_timestamp(state, stamp):
    if not stamp:
        return
    state["timestamp_min"] = stamp if state["timestamp_min"] is None else min(state["timestamp_min"], stamp)
    state["timestamp_max"] = stamp if state["timestamp_max"] is None else max(state["timestamp_max"], stamp)


def _model_identity(manifest):
    declared = []
    consumed = []

    def visit(value, context=()):
        if isinstance(value, dict):
            local_context = context + tuple(str(k).lower() for k in value.keys())
            status = str(value.get("status", "")).lower()
            is_consumed = value.get("consumed") is True or value.get("loaded") is True or value.get("used") is True or status in {"consumed", "loaded", "used", "active"}
            for key, item in value.items():
                key_lower = str(key).lower()
                if key_lower in {"model_sha256", "consumed_model_sha256", "actual_model_sha256", "bundle_sha256", "model_bundle_sha256"}:
                    if isinstance(item, str):
                        entry = {"field": str(key), "sha256": item}
                        declared.append(entry)
                        if is_consumed or any(token in " ".join(context).lower() for token in ("consumed", "loaded", "actual")):
                            consumed.append(entry)
                elif key_lower in {"models", "model_files", "model_bundle", "consumed_models"}:
                    visit(item, context + (key_lower,))
                elif isinstance(item, (dict, list)):
                    visit(item, context + (key_lower,))
        elif isinstance(value, list):
            for item in value:
                visit(item, context)

    visit(manifest)
    uniq = lambda rows: [dict(t) for t in {tuple(sorted(row.items())) for row in rows}]
    return {
        "consumed_model_hashes": uniq(consumed),
        "declared_model_hashes": uniq(declared),
        "consumed_identity_status": "measured_from_manifest" if consumed else "unknown",
    }


def _parse_pilot_line(line, state):
    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
    ts_match = TS_RE.search(text)
    if ts_match:
        stamp = _timestamp(ts_match.group(1))
        _add_timestamp(state, stamp)
    match = PHASE_RE.search(text)
    if match:
        _add_duration(state["phases"][match.group(1)], match.group(2))


def _parse_jsonl_record(record, kind, state):
    stamp = _record_timestamp(record)
    _add_timestamp(state, stamp)
    label = str(record.get("label", "")).upper()
    if kind == "delta_log":
        if label == "ROW_DONE":
            _add_duration(state["row_secs"], record.get("row_secs"))
        elif label not in {"BOARD_CTX", "SETTLE", "BRAIN_SKIP"}:
            if record.get("cached") is False:
                _add_duration(state["delta_eval_secs_uncached"], record.get("secs"))
            elif record.get("cached") is True:
                _add_duration(state["delta_eval_secs_cached"], record.get("secs"))
    if kind == "coverage_attempts":
        state["coverage_attempts"] += 1
        if record.get("engine_execution_attempted") is True:
            state["coverage_engine_attempts"] += 1
        _add_duration(state["coverage_eval_wall_seconds_sum"], record.get("wall_seconds"))


def _scan_source(source):
    kind = str(source.get("kind", "unknown"))
    stream, proc = _open_source(source)
    digest = hashlib.sha256()
    byte_count = 0
    record_count = 0
    invalid_records = 0
    state = {
        "phases": defaultdict(_duration_bucket),
        "row_secs": _duration_bucket(),
        "delta_eval_secs_uncached": _duration_bucket(),
        "delta_eval_secs_cached": _duration_bucket(),
        "coverage_eval_wall_seconds_sum": _duration_bucket(),
        "coverage_attempts": 0,
        "coverage_engine_attempts": 0,
        "timestamp_min": None,
        "timestamp_max": None,
        "json": None,
    }
    try:
        if kind in {"progress", "campaign_result", "oracle", "run_manifest", "manifest"}:
            raw, parsed = _read_small_json(stream)
            digest.update(raw)
            byte_count += len(raw)
            record_count = 1
            state["json"] = parsed
            stamp = _record_timestamp(parsed) if isinstance(parsed, dict) else None
            _add_timestamp(state, stamp)
            if kind == "oracle" and isinstance(parsed, dict):
                resources = parsed.get("resources") or parsed.get("previous_actual_engine_proof", {}).get("resources") or {}
                state["oracle_wall_s"] = _number(resources.get("wall_s"))
                state["oracle_cpu_s"] = _number(resources.get("self_cpu_s"))
                state["oracle_measurement_scope"] = resources.get("measurement_scope")
            if kind == "progress" and isinstance(parsed, dict):
                state["progress_observed_at"] = parsed.get("observed_at")
                state["progress_counts"] = parsed.get("counts")
        else:
            for line in stream:
                digest.update(line)
                byte_count += len(line)
                if kind == "pilot_log":
                    _parse_pilot_line(line, state)
                    record_count += 1
                    continue
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    invalid_records += 1
                    continue
                if not isinstance(record, dict):
                    invalid_records += 1
                    continue
                record_count += 1
                _parse_jsonl_record(record, kind, state)
    finally:
        stream.close()
        if proc:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            rc = proc.wait()
            if rc:
                raise RuntimeError(f"remote read failed for {source.get('host')}:{source.get('path')}: {stderr[-2000:]}")
    timestamps = [stamp for stamp in (state.pop("timestamp_min"), state.pop("timestamp_max")) if stamp]
    json_value = state.pop("json")
    return {
        "source": {"kind": kind, "host": source.get("host", "local"), "path": str(source["path"]), "sha256": digest.hexdigest(), "bytes_read": byte_count, "records_read": record_count, "invalid_records": invalid_records},
        "timestamps": timestamps,
        "json": json_value,
        "metrics": state,
    }


def profile(manifest):
    output = {
        "schema": SCHEMA,
        "generated_at_utc": _utc_now(),
        "run_id": manifest.get("run_id"),
        "scope": manifest.get("scope", "sources as declared; no whole-board equivalence inferred"),
        "sources": [],
        "measured": {
            "pilot_phase_wall_s": {},
            "row_secs_sum": _duration_bucket(),
            "delta_eval_secs_uncached_sum": _duration_bucket(),
            "delta_eval_secs_cached_sum": _duration_bucket(),
            "coverage_eval_wall_seconds_sum": _duration_bucket(),
            "coverage_attempts": 0,
            "coverage_engine_attempts": 0,
            "coverage_progress_snapshots": [],
            "campaign_receipts": [],
            "oracle_direct_wall_s": [],
            "oracle_direct_self_cpu_s": [],
            "oracle_measurement_scope": [],
            "timing_timestamp_min_utc": None,
            "timing_timestamp_max_utc": None,
            "timestamp_span_s": None,
            "timestamp_span_definition": "min-to-max timestamps explicitly present in input records; not asserted as whole-run wall time",
        "campaign_wall_s_from_launch_terminal": None,
        "campaign_wall_definition": "launch receipt at_utc to terminal receipt at_utc, only when both explicit receipts are supplied",
        },
        "model_identity": {"consumed_model_hashes": [], "declared_model_hashes": [], "consumed_identity_status": "unknown"},
        "limitations": [
            "Phase durations may overlap and are reported by phase only; they are not summed into a wall total.",
            "Per-evaluation wall_seconds sums are not process CPU time or campaign wall time.",
            "Missing actual consumed-model identity is reported as unknown, not inferred from a directory or bundle declaration.",
        ],
    }
    timestamp_min = None
    timestamp_max = None
    launch_at = None
    terminal_at = None
    for source in manifest.get("sources", []):
        scanned = _scan_source(source)
        output["sources"].append(scanned["source"])
        state = scanned["metrics"]
        for phase, bucket in state["phases"].items():
            dest = output["measured"]["pilot_phase_wall_s"].setdefault(phase, _duration_bucket())
            dest["count"] += bucket["count"]
            dest["sum_s"] += bucket["sum_s"]
            dest["min_s"] = bucket["min_s"] if dest["min_s"] is None else (min(dest["min_s"], bucket["min_s"]) if bucket["min_s"] is not None else dest["min_s"])
            dest["max_s"] = bucket["max_s"] if dest["max_s"] is None else (max(dest["max_s"], bucket["max_s"]) if bucket["max_s"] is not None else dest["max_s"])
        for src_key, dst_key in (("row_secs", "row_secs_sum"), ("delta_eval_secs_uncached", "delta_eval_secs_uncached_sum"), ("delta_eval_secs_cached", "delta_eval_secs_cached_sum"), ("coverage_eval_wall_seconds_sum", "coverage_eval_wall_seconds_sum")):
            src = state[src_key]
            dst = output["measured"][dst_key]
            dst["count"] += src["count"]
            dst["sum_s"] += src["sum_s"]
            dst["min_s"] = src["min_s"] if dst["min_s"] is None else (min(dst["min_s"], src["min_s"]) if src["min_s"] is not None else dst["min_s"])
            dst["max_s"] = src["max_s"] if dst["max_s"] is None else (max(dst["max_s"], src["max_s"]) if src["max_s"] is not None else dst["max_s"])
        output["measured"]["coverage_attempts"] += state["coverage_attempts"]
        output["measured"]["coverage_engine_attempts"] += state["coverage_engine_attempts"]
        if source.get("kind") == "progress":
            output["measured"]["coverage_progress_snapshots"].append({"source": scanned["source"]["path"], "observed_at": state.get("progress_observed_at"), "counts": state.get("progress_counts")})
        if source.get("kind") == "campaign_result":
            campaign = scanned["json"] or {}
            output["measured"]["campaign_receipts"].append({"source": scanned["source"]["path"], "status": campaign.get("status"), "KG_complete_claimed": campaign.get("KG_complete_claimed"), "promotion_enabled": campaign.get("promotion_enabled"), "coverage": campaign.get("coverage", {}).get("counts")})
        if state.get("oracle_wall_s") is not None:
            output["measured"]["oracle_direct_wall_s"].append(state["oracle_wall_s"])
        if state.get("oracle_cpu_s") is not None:
            output["measured"]["oracle_direct_self_cpu_s"].append(state["oracle_cpu_s"])
        if state.get("oracle_measurement_scope"):
            output["measured"]["oracle_measurement_scope"].append(state["oracle_measurement_scope"])
        if scanned["timestamps"]:
            timestamp_min = scanned["timestamps"][0] if timestamp_min is None else min(timestamp_min, scanned["timestamps"][0])
            timestamp_max = scanned["timestamps"][-1] if timestamp_max is None else max(timestamp_max, scanned["timestamps"][-1])
        if source.get("kind") == "launch_receipt" and scanned["timestamps"]:
            launch_at = scanned["timestamps"][0]
        if source.get("kind") == "terminal_receipt" and scanned["timestamps"]:
            terminal_at = scanned["timestamps"][-1]
        if source.get("kind") in {"run_manifest", "manifest"} and scanned["json"] is not None:
            identity = _model_identity(scanned["json"])
            for key in ("consumed_model_hashes", "declared_model_hashes"):
                output["model_identity"][key].extend(identity[key])
            if identity["consumed_identity_status"] == "measured_from_manifest":
                output["model_identity"]["consumed_identity_status"] = "measured_from_manifest"
    for key in ("pilot_phase_wall_s",):
        output["measured"][key] = {name: _finish_bucket(bucket) for name, bucket in output["measured"][key].items()}
    for key in ("row_secs_sum", "delta_eval_secs_uncached_sum", "delta_eval_secs_cached_sum", "coverage_eval_wall_seconds_sum"):
        output["measured"][key] = _finish_bucket(output["measured"][key])
    if timestamp_min and timestamp_max:
        low, high = timestamp_min, timestamp_max
        output["measured"]["timing_timestamp_min_utc"] = low.isoformat().replace("+00:00", "Z")
        output["measured"]["timing_timestamp_max_utc"] = high.isoformat().replace("+00:00", "Z")
        output["measured"]["timestamp_span_s"] = round((high - low).total_seconds(), 6)
    if launch_at and terminal_at and terminal_at >= launch_at:
        output["measured"]["campaign_wall_s_from_launch_terminal"] = round((terminal_at - launch_at).total_seconds(), 6)
    for key in ("consumed_model_hashes", "declared_model_hashes"):
        seen = set()
        deduped = []
        for item in output["model_identity"][key]:
            marker = (item.get("field"), item.get("sha256"))
            if marker not in seen:
                seen.add(marker)
                deduped.append(item)
        output["model_identity"][key] = deduped
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSON input manifest with run_id and typed sources")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)
    manifest_bytes = Path(args.manifest).read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    result = profile(manifest)
    result["input_manifest"] = {"path": str(Path(args.manifest)), "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(target), "schema": result["schema"], "sources": len(result["sources"]), "model_identity": result["model_identity"]["consumed_identity_status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
