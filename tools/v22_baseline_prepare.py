"""Prepare isolated per-side prior-runtime baseline oracle requests."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import zlib

DEFAULT_SIDES = (
    "1000PEPEUSDC_LONG",
    "AAVEUSDC_LONG",
    "1000BONKUSDC_SHORT",
    "1INCHUSDT_SHORT",
    "IBM_LONG",
    "TMO_LONG",
    "COIN_SHORT",
    "CDE_SHORT",
)
BASELINE_HARNESS_FILES = (
    "v15_nextgen_baseline_oracle.py",
    "v15_nextgen_baseline_guard.py",
    "v15_nextgen_bench_isolation.py",
)
CRYPTO_SYMBOLS = {"1000PEPEUSDC", "AAVEUSDC", "1000BONKUSDC", "1INCHUSDT"}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_json(path):
    return json.loads(Path(path).read_text())


def effective_defaults(source_root, source_side, env, cat_side, runtime_files):
    settings_path = Path(
        env.get("PER_SYM_SETTINGS_PATH")
        or env.get("CAT_SIDE_DEFAULTS_PATH")
        or (Path(source_root) / "data/per_sym_settings.json")
    )
    store_path = Path(
        env.get("PER_SYM_STORE_DB")
        or (Path(source_root) / "data/hourly_reconfig/per_sym_store.db")
    )
    if not settings_path.is_absolute():
        settings_path = Path(source_root) / settings_path
    if not store_path.is_absolute():
        store_path = Path(source_root) / store_path
    for relative in ("per_sym_settings.py", "per_sym_store.py"):
        if (
            relative not in runtime_files
            or sha(Path(source_root) / relative) != runtime_files[relative]
        ):
            raise ValueError(
                f"actual prior defaults loader differs from frozen runtime: {relative}"
            )
    sql_value = None
    sql_value_sha256 = None
    sql_enabled = (
        env.get("CAT_SIDE_DEFAULTS_SQLITE_DISABLED") != "1"
        and env.get("PER_SYM_STORE_SQLITE_DISABLED") != "1"
    )
    if sql_enabled and store_path.is_file() and not store_path.is_symlink():
        try:
            uri = f"file:{store_path.resolve()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                row = connection.execute(
                    "SELECT value_json FROM kv_json WHERE key=?", ("per_sym_settings",)
                ).fetchone()
            if row and row[0]:
                raw = row[0]
                if raw.startswith("z:"):
                    raw = zlib.decompress(
                        __import__("base64").b64decode(raw[2:])
                    ).decode("utf-8")
                sql_value_sha256 = hashlib.sha256(raw.encode()).hexdigest()
                candidate = json.loads(raw)
                if isinstance(candidate, dict) and any(
                    candidate.get(name)
                    for name in (
                        "CRYPTO_LONG",
                        "CRYPTO_SHORT",
                        "STOCKS_LONG",
                        "STOCKS_SHORT",
                    )
                ):
                    sql_value = candidate
        except (OSError, sqlite3.Error, ValueError, zlib.error, json.JSONDecodeError):
            sql_value = None
    if sql_value is not None:
        selected = sql_value
        source_kind = "sqlite.kv_json[per_sym_settings]"
        source_path = str(store_path.resolve())
    else:
        selected = load_json(settings_path)
        source_kind = "data/per_sym_settings.json fallback"
        source_path = str(settings_path.resolve())
    if (
        not isinstance(selected, dict)
        or not isinstance(selected.get(cat_side), dict)
        or not selected[cat_side]
    ):
        raise ValueError(
            f"actual prior defaults loader returned no complete {cat_side} layer"
        )
    category = selected[cat_side]
    canonical = json.dumps(
        category, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    proof = {
        "loader_module_path": str(
            (Path(source_root) / "per_sym_settings.py").resolve()
        ),
        "loader_module_sha256": sha(Path(source_root) / "per_sym_settings.py"),
        "store_module_path": str((Path(source_root) / "per_sym_store.py").resolve()),
        "store_module_sha256": sha(Path(source_root) / "per_sym_store.py"),
        "settings_path": str(settings_path.resolve()),
        "settings_sha256": sha(settings_path),
        "store_path": str(store_path.resolve()),
        "store_sha256": sha(store_path) if store_path.is_file() else None,
        "sql_value_sha256": sql_value_sha256,
        "sql_lookup_enabled": sql_enabled,
        "effective_source_kind": source_kind,
        "effective_source_path": source_path,
        "cat_side": cat_side,
        "effective_defaults_sha256": hashlib.sha256(canonical).hexdigest(),
        "effective_defaults_count": len(category),
    }
    return category, proof


def cat_side_for(sym_side, admission):
    symbol, side = sym_side.rsplit("_", 1)
    venue = admission[sym_side]["venue"]
    if venue not in ("crypto", "stocks"):
        raise ValueError(f"unsupported venue for {sym_side}: {venue}")
    expected_venue = "crypto" if symbol in CRYPTO_SYMBOLS else "stocks"
    if venue != expected_venue:
        raise ValueError(f"admission venue conflicts with symbol family: {sym_side}")
    return f"{venue.upper()}_{side.upper()}"


def verify_side_inputs(snapshot, snapshot_manifest, admission, live_manifest, sym_side):
    if sym_side not in admission.get("sides", ()):
        raise ValueError(f"side is absent from current8 admission: {sym_side}")
    if sym_side not in admission.get("admission", {}) or sym_side not in admission.get(
        "baseline_sha256", {}
    ):
        raise ValueError(f"side admission or baseline hash is missing: {sym_side}")
    if sym_side not in live_manifest.get("sets", {}):
        raise ValueError(f"side live baseline hash is missing: {sym_side}")
    symbol = sym_side.rsplit("_", 1)[0]
    start = snapshot / "starts" / f"{sym_side}_autopsy_base.json"
    live = snapshot / "data/live_baseline_snapshots" / f"{sym_side}.json"
    npz = snapshot / "backtest_v8/indicators" / f"{symbol}.npz"
    if not npz.is_file():
        npz = snapshot / "npz" / f"{symbol}.npz"
    if not all(path.is_file() and not path.is_symlink() for path in (start, live, npz)):
        raise ValueError(f"pinned start, live set, or NPZ missing for {sym_side}")
    start_hash, live_hash = sha(start), sha(live)
    expected = admission["baseline_sha256"][sym_side]
    if start_hash != expected or live_hash != expected:
        raise ValueError(
            f"start/live baseline do not match current8 source anchor: {sym_side}"
        )
    if hashlib.md5(start.read_bytes()).hexdigest() != snapshot_manifest.get(
        "starts", {}
    ).get(sym_side):
        raise ValueError(f"start differs from pinned snapshot manifest: {sym_side}")
    if live_hash != live_manifest["sets"][sym_side]:
        raise ValueError(
            f"side live baseline differs from frozen live manifest: {sym_side}"
        )
    frozen_live_hash = snapshot_manifest.get("frozen_data_sha256", {}).get(
        f"data/live_baseline_snapshots/{sym_side}.json"
    )
    if frozen_live_hash and live_hash != frozen_live_hash:
        raise ValueError(
            f"side live baseline differs from frozen data manifest: {sym_side}"
        )
    npz_identity = snapshot_manifest.get("npz", {}).get(symbol, {})
    if npz_identity.get("sha256") and sha(npz) != npz_identity["sha256"]:
        raise ValueError(f"NPZ differs from pinned snapshot manifest: {sym_side}")
    return {
        "sym_side": sym_side,
        "start_path": str(start),
        "start_sha256": start_hash,
        "live_baseline_path": str(live),
        "live_baseline_sha256": live_hash,
        "npz_path": str(npz),
        "npz_sha256": sha(npz),
        "cat_side": cat_side_for(sym_side, admission["admission"]),
    }


def copy_tree_by_hardlink(
    source,
    runtime_root,
    target,
    expected_files,
    runtime_files,
    verified_sources,
    routing_files=None,
):
    source, runtime_root, target = map(Path, (source, runtime_root, target))
    routing_files = routing_files or {}
    target.mkdir(parents=True, exist_ok=False)
    for relative, metadata in expected_files.items():
        route = routing_files.get(relative)
        if route is not None:
            if (
                runtime_files.get(relative) != route.get("source_sha256")
                or metadata.get("sha256") != route.get("source_sha256")
            ):
                raise ValueError(f"routing overlay preimage differs: {relative}")
            source_path = source / relative
            expected_hash = route.get("candidate_sha256")
        else:
            source_path = (
                runtime_root / relative
                if relative in runtime_files
                else source / relative
            )
            expected_hash = runtime_files.get(relative, metadata["sha256"])
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(f"source fixture file missing or unsafe: {source_path}")
        if expected_hash != metadata["sha256"]:
            if route is None or expected_hash != route.get("candidate_sha256"):
                raise ValueError(f"source file differs from immutable pins: {source_path}")
        stat = source_path.stat()
        if route is None and relative not in runtime_files and (
            stat.st_size,
            stat.st_ino,
            stat.st_mtime_ns,
        ) != (
            metadata["bytes"],
            metadata["inode"],
            metadata["mtime_ns"],
        ):
            raise ValueError(f"source fixture changed after receipt: {source_path}")
        cache_key = str(source_path.resolve())
        if verified_sources.get(cache_key) != expected_hash:
            if sha(source_path) != expected_hash:
                label = "routing overlay" if route is not None else "immutable source"
                raise ValueError(f"{label} differs from pinned SHA256: {source_path}")
            verified_sources[cache_key] = expected_hash
        target_path = target / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.link(source_path, target_path)
    if any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("materialized previous runtime contains symlinks")


def materialize_side(
    snapshot, source_base, output_root, sym_side, harness_root, verified_sources
):
    snapshot, source_base, output_root = map(Path, (snapshot, source_base, output_root))
    source_root, source_side = source_base / "reference", source_base / "side"
    source_request_path = source_side / "oracle_request.json"
    source_request = load_json(source_request_path)
    runtime_path = Path(source_request["previous_runtime_manifest"]["path"])
    policy_path = Path(source_request["previous_policy_receipt"]["path"])
    routing_path = Path(source_request["routing_manifest"]["path"])
    receipt_path = Path(source_request["materialization_receipt"]["path"])
    for source, key in (
        (runtime_path, "previous_runtime_manifest"),
        (policy_path, "previous_policy_receipt"),
        (routing_path, "routing_manifest"),
        (receipt_path, "materialization_receipt"),
    ):
        if source.is_symlink() or sha(source) != source_request[key]["sha256"]:
            raise ValueError(f"source baseline fixture pin changed: {source}")
    source_receipt = load_json(receipt_path)
    if source_receipt.get("reference_root") != str(source_root.resolve()):
        raise ValueError("source receipt does not describe the pinned reference root")
    snapshot_manifest_path = snapshot / "manifest.json"
    snapshot_manifest = load_json(snapshot_manifest_path)
    admission_path = snapshot / "data/admission.json"
    if not admission_path.is_file():
        admission_path = snapshot / "admission.json"
    if admission_path.is_file():
        admission = load_json(admission_path)
    else:
        admission_path = snapshot_manifest_path
        admission = snapshot_manifest.get("admission", {})
    if not admission:
        raise ValueError(
            "current8 snapshot does not contain its captured admission manifest"
        )
    live_manifest_path = snapshot / "data/live_baseline_snapshots/manifest.json"
    if not live_manifest_path.is_file():
        live_manifest_path = snapshot / "live_baseline_snapshots/manifest.json"
    live_manifest = load_json(live_manifest_path)
    current = verify_side_inputs(
        snapshot, snapshot_manifest, admission, live_manifest, sym_side
    )
    source_live_manifest = source_root / "data/live_baseline_snapshots/manifest.json"
    if sha(source_live_manifest) != sha(live_manifest_path):
        raise ValueError(
            "source runtime does not contain the exact current8 live-set manifest"
        )
    source_live = source_root / "data/live_baseline_snapshots" / f"{sym_side}.json"
    if sha(source_live) != current["live_baseline_sha256"]:
        raise ValueError(
            f"source runtime lacks exact per-side live baseline: {sym_side}"
        )
    snapshot_npz = Path(current["npz_path"])
    relative_npz = f"backtest_v8/indicators/{sym_side.rsplit('_', 1)[0]}.npz"
    reference = output_root / f"{sym_side}_reference"
    side = output_root / f"{sym_side}_side"
    if reference.exists() or side.exists():
        raise ValueError(
            f"side output already exists; preserving prior files: {sym_side}"
        )
    runtime = load_json(runtime_path)
    runtime_root = Path(runtime["code_root"])
    routing = load_json(routing_path)
    routing_files = {
        relative: {
            "source_sha256": routing["source_sha256"][relative],
            "candidate_sha256": routing["candidate_sha256"][relative],
        }
        for relative in routing.get("source_sha256", {})
    }
    if set(routing_files) != set(routing.get("candidate_sha256", {})):
        raise ValueError("routing source/candidate file sets differ")
    for relative, route in routing_files.items():
        if (
            runtime["files"].get(relative) != route["source_sha256"]
            or source_receipt["files"].get(relative, {}).get("sha256")
            != route["source_sha256"]
        ):
            raise ValueError(f"routing overlay preimage differs from prior runtime: {relative}")
        overlay = source_root / relative
        if overlay.is_symlink() or not overlay.is_file() or sha(overlay) != route[
            "candidate_sha256"
        ]:
            raise ValueError(f"routing overlay differs from its declared candidate: {relative}")
    copy_tree_by_hardlink(
        source_root,
        runtime_root,
        reference,
        source_receipt["files"],
        runtime["files"],
        verified_sources,
        routing_files,
    )
    side.mkdir(parents=True)
    destination_npz = reference / relative_npz
    destination_npz.parent.mkdir(parents=True, exist_ok=True)
    if not destination_npz.is_file() or sha(destination_npz) != current["npz_sha256"]:
        if destination_npz.exists():
            destination_npz.unlink()
        shutil.copyfile(snapshot_npz, destination_npz)
    if sha(destination_npz) != current["npz_sha256"]:
        raise ValueError(f"materialized NPZ mismatch: {sym_side}")
    runtime_target = output_root / f"{sym_side}_runtime_manifest.json"
    write_json(runtime_target, runtime)
    materialized_files = {}
    for relative, metadata in source_receipt["files"].items():
        path = reference / relative
        stat = path.stat()
        if relative == relative_npz:
            digest = sha(path)
        elif relative in routing_files:
            digest = sha(path)
            source_path = source_root / relative
            route = routing_files[relative]
            if (
                digest != route["candidate_sha256"]
                or path.stat().st_ino != source_path.stat().st_ino
                or verified_sources.get(str(source_path.resolve())) != digest
            ):
                raise ValueError(f"routed source changed while materializing: {path}")
        else:
            digest = metadata["sha256"]
            source_path = (
                runtime_root / relative
                if relative in runtime["files"]
                else source_root / relative
            )
            if (
                path.stat().st_ino != source_path.stat().st_ino
                or verified_sources.get(str(source_path.resolve())) != digest
            ):
                raise ValueError(
                    f"hardlinked source changed while materializing: {path}"
                )
        materialized_files[relative] = {
            "sha256": digest,
            "bytes": stat.st_size,
            "inode": stat.st_ino,
            "mtime_ns": stat.st_mtime_ns,
        }
    if relative_npz not in materialized_files:
        stat = destination_npz.stat()
        materialized_files[relative_npz] = {
            "sha256": sha(destination_npz),
            "bytes": stat.st_size,
            "inode": stat.st_ino,
            "mtime_ns": stat.st_mtime_ns,
        }
    materialization = {
        "schema": "v15_prior_runtime_materialization/1",
        "at_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "status": "CURRENT8_SIDE_MATERIALIZED_FULL_HASH_VERIFIED_NO_EVALUATION",
        "reference_root": str(reference.resolve()),
        "previous_runtime_manifest_sha256": sha(runtime_target),
        "source_materialization_receipt_sha256": sha(receipt_path),
        "source_reference_root": str(source_root.resolve()),
        "snapshot_manifest_sha256": sha(snapshot_manifest_path),
        "live_baseline_manifest_sha256": sha(live_manifest_path),
        "side_inputs": current,
        "files": materialized_files,
    }
    materialization_path = side / "materialization_receipt.json"
    write_json(materialization_path, materialization)
    prepared_env = load_json(source_request["prepared_env_file"])
    old_defaults, defaults_proof = effective_defaults(
        source_root, source_side, prepared_env, current["cat_side"], runtime["files"]
    )
    source_provenance = side / "source_provenance"
    source_provenance.mkdir()
    accepted_path = source_provenance / "accepted_live_baseline.json"
    shutil.copyfile(Path(current["live_baseline_path"]), accepted_path)
    defaults_path = source_provenance / "previous_effective_defaults.json"
    write_json(defaults_path, old_defaults)
    defaults_proof_path = (
        source_provenance / "previous_effective_defaults_provenance.json"
    )
    write_json(defaults_proof_path, defaults_proof)
    for key, value in list(prepared_env.items()):
        if isinstance(value, str):
            prepared_env[key] = (
                value.replace(str(source_root), str(reference))
                .replace(str(source_base), str(output_root))
                .replace(str(source_side), str(side))
            )
    for name in (
        "private_store",
        "flags",
        "carry",
        "prev_best",
        "parity_cache",
        "tmp",
        "numba_cache",
        "mpl",
        "xdg_cache",
        "audit_bootstrap",
    ):
        (side / name).mkdir()
    for relative in (
        "private_store/per_sym_store.db",
        "audit_bootstrap/sitecustomize.py",
    ):
        seed = source_side / relative
        if seed.is_file() and not seed.is_symlink():
            shutil.copyfile(seed, side / relative)
    env_path = side / "prepared_env.json"
    write_json(env_path, prepared_env)
    accepted_sha = sha(accepted_path)
    snapshot_id = f"mac_live_full_set:{snapshot_manifest['snapshot_id'] if 'snapshot_id' in snapshot_manifest else snapshot_manifest_path.parent.name}:{sym_side}:{accepted_sha}"
    request = dict(source_request)
    request.update(
        reference_root=str(reference.resolve()),
        side_dir=str(side.resolve()),
        sym_side=sym_side,
        window_days=30,
        holdout_days=source_request["holdout_days"],
        npz_sha256=current["npz_sha256"],
        previous_runtime_manifest={
            "path": str(runtime_target.resolve()),
            "sha256": sha(runtime_target),
        },
        materialization_receipt={
            "path": str(materialization_path.resolve()),
            "sha256": sha(materialization_path),
        },
        prepared_env_file=str(env_path.resolve()),
        accepted_source={
            "path": str(accepted_path.resolve()),
            "sha256": accepted_sha,
            "settings_pointer": [],
            "previous_acceptance_id": snapshot_id,
            "qualification": "Exact hash-pinned current live full-set snapshot; this is snapshot identity, not a recovered historical SQL acceptance receipt.",
            "snapshot_start_path": current["start_path"],
            "snapshot_start_sha256": current["start_sha256"],
            "live_manifest_sha256": sha(live_manifest_path),
        },
        previous_defaults_source={
            "path": str(defaults_path.resolve()),
            "sha256": sha(defaults_path),
            "settings_pointer": [],
            "effective_loader_proof": {
                "path": str(defaults_proof_path.resolve()),
                "sha256": sha(defaults_proof_path),
                **defaults_proof,
            },
            "qualification": "Category defaults captured from the frozen previous runtime's actual per_sym_settings loader, including SQL-primary lookup and JSON fallback semantics.",
        },
        current8_source={
            "snapshot_manifest_path": str(snapshot_manifest_path.resolve()),
            "snapshot_manifest_sha256": sha(snapshot_manifest_path),
            "admission_manifest_path": str(admission_path.resolve()),
            "admission_manifest_sha256": sha(admission_path),
            "live_baseline_manifest_path": str(live_manifest_path.resolve()),
            "live_baseline_manifest_sha256": sha(live_manifest_path),
            "side_inputs": current,
        },
        experiment_role="current8_side_previous_runtime_baseline_only",
    )
    request_path = side / "oracle_request.json"
    write_json(request_path, request)
    source_harness = Path(harness_root) if harness_root else source_base / "harness"
    harness = side / "harness"
    harness.mkdir()
    harness_hashes = {}
    for name in BASELINE_HARNESS_FILES:
        source_file = source_harness / name
        if source_file.is_symlink() or not source_file.is_file():
            raise ValueError(
                f"frozen current oracle harness file missing: {source_file}"
            )
        target_file = harness / name
        os.link(source_file, target_file)
        harness_hashes[name] = sha(target_file)
    runner = harness / "v15_nextgen_baseline_oracle.py"
    baseline_guard = harness / "v15_nextgen_baseline_guard.py"
    isolation = harness / "v15_nextgen_bench_isolation.py"
    if not runner.is_file() or not baseline_guard.is_file() or not isolation.is_file():
        raise ValueError("copied baseline-oracle runner changed")
    expected_prefix = source_request["expected_environment"].get("sys_prefix")
    executable = source_request["expected_environment"].get("executable")
    if not executable and expected_prefix:
        expected_executable = Path(expected_prefix) / "bin/python"
        if expected_executable.is_file():
            executable = str(expected_executable)
    if not executable:
        if sys.prefix != expected_prefix:
            raise ValueError(
                "oracle interpreter must match the frozen previous runtime environment"
            )
        executable = sys.executable
    command = [executable, "-u", str(runner), "--request", str(request_path)]
    script_path = side / "run_previous_baseline_oracle.sh"
    import shlex

    script_path.write_text(
        "#!/bin/sh\nexec env "
        + shlex.join(
            [f"{key}={value}" for key, value in prepared_env.items()] + command
        )
        + "\n"
    )
    script_path.chmod(0o755)
    return {
        "sym_side": sym_side,
        "status": "PREPARED_NO_ORACLE_EXECUTION",
        "reference_root": str(reference.resolve()),
        "side_dir": str(side.resolve()),
        "request_path": str(request_path.resolve()),
        "request_sha256": sha(request_path),
        "materialization_sha256": sha(materialization_path),
        "npz_sha256": current["npz_sha256"],
        "accepted_source_sha256": accepted_sha,
        "accepted_source_qualification": request["accepted_source"]["qualification"],
        "previous_defaults_sha256": sha(defaults_path),
        "effective_defaults_provenance_sha256": sha(defaults_proof_path),
        "effective_defaults_source_kind": defaults_proof["effective_source_kind"],
        "effective_defaults_source_sha256": (
            defaults_proof["settings_sha256"]
            if defaults_proof["effective_source_kind"].endswith("fallback")
            else defaults_proof["sql_value_sha256"]
        ),
        "oracle_command": command,
        "runner_sha256": sha(runner),
        "baseline_guard_sha256": sha(baseline_guard),
        "isolation_helper_sha256": sha(isolation),
        "harness_files": harness_hashes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--source-base", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--harness-root")
    parser.add_argument("--sides", default=",".join(DEFAULT_SIDES))
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    snapshot, source_base, output_root = map(
        Path, (args.snapshot, args.source_base, args.output_root)
    )
    if output_root.exists() or output_root.is_symlink():
        raise ValueError(
            f"new output root required; preserve prior output: {output_root}"
        )
    sides = [value.strip() for value in args.sides.split(",") if value.strip()]
    if not sides or len(sides) != len(set(sides)):
        raise ValueError("one or more unique sym_side values required")
    output_root.mkdir(parents=True)
    results = []
    held = []
    verified_sources = {}
    for sym_side in sides:
        try:
            results.append(
                materialize_side(
                    snapshot,
                    source_base,
                    output_root,
                    sym_side,
                    args.harness_root,
                    verified_sources,
                )
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            held.append(
                {
                    "sym_side": sym_side,
                    "status": "HELD_INPUT_PROVENANCE_OR_MATERIALIZATION",
                    "reason": str(error),
                }
            )
    execution = []
    if args.run and results:
        for item in results:
            env = load_json(Path(item["side_dir"]) / "prepared_env.json")
            command = item["oracle_command"]
            run = subprocess.run(
                command,
                cwd=item["reference_root"],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
            )
            resource_hold = "MemAvailable" in run.stderr or "disk" in run.stderr.lower()
            execution.append(
                {
                    "sym_side": item["sym_side"],
                    "status": (
                        "HELD_BY_ORACLE_RESOURCE_GUARD"
                        if run.returncode and resource_hold
                        else (
                            "ORACLE_COMPLETE"
                            if run.returncode == 0
                            else "ORACLE_FAILED"
                        )
                    ),
                    "returncode": run.returncode,
                    "stdout": run.stdout[-4000:],
                    "stderr": run.stderr[-4000:],
                }
            )
    manifest = {
        "schema": "v22_current8_prior_baseline_preparation/1",
        "created_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "status": (
            "PREPARED_NO_ORACLE_EXECUTION"
            if not execution
            else "ORACLE_EXECUTION_ATTEMPTED"
        ),
        "snapshot": str(snapshot.resolve()),
        "source_base": str(source_base.resolve()),
        "harness_root": (
            str(Path(args.harness_root).resolve())
            if args.harness_root
            else str((source_base / "harness").resolve())
        ),
        "output_root": str(output_root.resolve()),
        "run_requested": args.run,
        "prepared": results,
        "held": held,
        "execution": execution,
        "interpretation": "This preparer pins each side's own current live full-set snapshot and frozen NPZ. It does not claim a recovered historical SQL acceptance receipt. The existing oracle runner evaluates only the prior runtime and launches no optimizer.",
    }
    write_json(output_root / "preparation_manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "prepared": len(results),
                "held": held,
                "execution": execution,
                "manifest": str(output_root / "preparation_manifest.json"),
            },
            indent=2,
        ),
        flush=True,
    )
    if held or any(
        row.get("status") != "ORACLE_COMPLETE"
        for row in execution
        if row.get("sym_side")
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
