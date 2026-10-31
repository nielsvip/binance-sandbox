#!/usr/bin/env python3
"""V15 VERSION GATE (USER 2026-10-14): "make sure the verifier is in place on the fixed npz set on s2 that runs every new version of
v15_pilot.py and subscripts and only allows a modification if results improve — not when skipping switches or filters leads to
deterioration of the system".

Runs on S2 (~/v15_bench). Nothing reaches production pilots unless it PASSES here:
  submit  --files F [F ...] [--set K=V ...] [--note TXT]   new candidate = champion tree + these files (+ gated switch defaults)
  run                                                      (cron */20, flock) bench the oldest pending candidate vs the champion
  status                                                   champion + last decisions
  watch                                                    alert when production code differs from the champion without a PASS
Bench: tools/v15_npz_bench.py on the frozen snapshot (default snap_20261014, 4 sides, one per cat_side), champion and candidate
run CONCURRENTLY (same host load), both with V15_HOLDOUT_DAYS=7 so every board gives its FIT gain (what the board finds) and its
OOS gain on 7 unseen days.
PASS = every side has a result in both runs AND sum(fit candidate) >= sum(fit champion) - TOL_SUM AND no side's fit gain drops more
than TOL_SIDE AND sum(OOS candidate) >= sum(OOS champion) - TOL_SUM. Skipping switches/filters that loses board gain = FAIL.
On PASS: candidate files are deployed (atomic, md5-verified) to S2 + S1 sandbox and live dirs, the gated switch defaults are
written to data/v15_gate_flags.json on both hosts (the pilot reads defaults from it; env still wins), and the candidate becomes the
champion. On FAIL nothing is deployed. Decisions: ~/v15_bench/gate/decisions.jsonl + GATE_STATUS.md. Never reverts production.
"""
import argparse
import ast
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

HOME = Path.home()
BENCH = Path(os.environ.get("V15_BENCH_DIR", str(HOME / "v15_bench")))
GATE = BENCH / "gate"
CHAMP = GATE / "champion"
CANDS = GATE / "candidates"
DECISIONS = GATE / "decisions.jsonl"
STATUS = GATE / "GATE_STATUS.md"
SNAP = os.environ.get("V15_GATE_SNAP", "20261014")
# 2026-10-16 USER "test every version side by side against the previous version so corner cutting is exposed": default =
# EVERY side of the snapshot (8, two per cat_side) instead of 4 — one side per cat_side let a single noisy board decide.
SIDES = os.environ.get("V15_GATE_SIDES", "")
# Corner-cut rule: a candidate whose boards evaluate less of the template (yellow cells or naked rows) than the champion's
# boards by more than this share must WIN on unseen days by more than the noise floor and may not lose fit gain at all.
COVERAGE_MIN = float(os.environ.get("V15_GATE_COVERAGE_MIN", "0.90"))
# Verdicts are made on chart-truth numbers only (tools/v15_bench_truth.py); V15_GATE_REQUIRE_TRUTH=0 = legacy self-reported.
REQUIRE_TRUTH = os.environ.get("V15_GATE_REQUIRE_TRUTH", "1") != "0"
# Launch-env keys that only route files/caches (never what a board computes) — ignored by env drift + env diffs.
ENV_IGNORE = {"V15_PROGRESS_DIR", "V15_OUT_DIR", "V15_START_OVERRIDES", "V15_ISOLATED_PROOF", "V15_EVAL_CACHE_DB", "V15_FILL_SLICE_S", "V15_V17_CARRY_DIR", "V15_DEFAULTS_ROUND", "V15_FRESH_RUN", "V15_SKIP_LIVE_AT_DONE", "V12_NPZ_CACHE", "V12_ALLOW_STALE_NPZ", "V15_HOLDOUT_DAYS"}
# USER 2026-10-15 "make sure some noise is acceptable we need to move ahead". MEASURED noise floor: two runs of the
# IDENTICAL champion tree on the IDENTICAL frozen NPZ gave 3374 vs 3376 rows, 8 vs 10 promotions, gain 8.4366 vs
# 8.9128 -> |d_gain| <= 0.48 per board, promoted-set hash unstable, because the late phases are budgeted in wall-clock
# seconds (V15_V17_P3_MAX_S_*, V15_FOCUS_S_*, V15_ENDGAME_S) so timing decides what gets explored. A per-side
# tolerance of 2.0 is ~4x that floor, but the SUM of 4 sides carries ~0.48*sqrt(4) = 0.95 of noise on its own, so the
# old TOL_SUM=1.0 sat exactly on the floor and would FAIL correct candidates about half the time. 2.5 keeps a real
# deterioration (a lost switch costs far more than 2.5 summed) detectable while noise alone can no longer block a ship.
NOISE_FLOOR_PER_SIDE = float(os.environ.get("V15_GATE_NOISE_FLOOR", "0.48"))
TOL_SUM = float(os.environ.get("V15_GATE_TOL_SUM", "2.5"))
TOL_SIDE = float(os.environ.get("V15_GATE_TOL_SIDE", "2.0"))
# USER 2026-10-28: the greedy in-sample fit sum is advisory; only the unseen-days OOS sum (and per-side OOS drops) can FAIL
# a candidate. V15_GATE_FIT_BINDING=1 restores the old rule.
FIT_BINDING = os.environ.get("V15_GATE_FIT_BINDING", "0") == "1"
RUN_TIMEOUT_MIN = float(os.environ.get("V15_GATE_RUN_TIMEOUT_MIN", "720"))  # 8 sides x ~55 min per tree, two trees concurrently
MIN_FREE_MB = float(os.environ.get("V15_GATE_MIN_FREE_MB", "20000"))
SRC = HOME / "binance-sandbox"
DEPLOY = [("local", HOME / "binance-sandbox"), ("local", HOME / "binance"), ("10.0.0.3", "/home/niels/binance-sandbox"), ("10.0.0.3", "/home/niels/binance")]
# 2026-10-20 WP1 (V19 full-course round): S5/S6 are EPHEMERAL images of S1 (deleted when the round ends). A PASS deploys to
# them too, best-effort: an unreachable ephemeral host is recorded (ok=None, optional) and never blocks champion promotion,
# so deleting the hosts later cannot wedge the gate in PASS_DEPLOY_INCOMPLETE. V15_GATE_EPHEMERAL_HOSTS="" turns it off.
EPHEMERAL_DEPLOY = [(h.strip(), "/home/niels/binance-sandbox") for h in os.environ.get("V15_GATE_EPHEMERAL_HOSTS", "10.0.0.5,10.0.0.6").split(",") if h.strip()]


def _reachable(host):
    try:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "true"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def deploy_targets():
    """[(host, root, optional)] — the four production roots (mandatory) + reachable ephemeral workers (optional)."""
    out = [(h, r, False) for h, r in DEPLOY]
    out += [(h, r, True) for h, r in EPHEMERAL_DEPLOY if _reachable(h)]
    return out


def deploy_complete(records):
    """PASS deploy is complete when every MANDATORY record is ok; optional (ephemeral) misses are reported, never blocking."""
    return bool(records) and all(x["ok"] for x in records if not x.get("optional"))


def install_model_bundle(source, destination, expected):
    source, destination = Path(source), Path(destination)
    if model_bundle_pin(source) != expected:
        raise ValueError("candidate model bundle changed before champion installation")
    for relative in expected["artifact_sha256"]:
        src = source / relative
        dst = destination / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.model-gate.tmp")
        shutil.copy2(src, tmp)
        if sha256(tmp) != expected["artifact_sha256"][relative]:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"temporary champion model copy failed SHA256: {relative}")
        os.replace(tmp, dst)
    relative = MODEL_BUNDLE_PATH
    src = source / relative
    dst = destination / relative
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.model-gate.tmp")
    shutil.copy2(src, tmp)
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != expected["manifest_sha256"]:
        tmp.unlink(missing_ok=True)
        raise ValueError("temporary champion model declaration failed SHA256")
    os.replace(tmp, dst)
    actual = model_bundle_pin(destination)
    if actual != expected:
        raise ValueError("installed CHAMP model bundle does not equal the candidate bundle")
    return actual
# USER 2026-10-17 ("all of it should be fed into the next v15_pilot calculations then it gets into live trading if
# proven successful"): switch_types.py and switch_parity.py were NOT gated, yet v15_pilot imports both and
# tools/switch_types.coerce IS the pilot's type law (_types_coerce_reason, v15_pilot:2193) — "a candidate the engine
# cannot consume must never be evaluated". A change there can therefore add or remove candidate cells and move board
# output with no bench at all. Widening the allowlist only makes the gate STRICTER (more files must PASS to deploy).
# The 2026-10-17 parse fix that exposed this was measured inert first: 0 of 241,471 template candidate values
# changed accept/reject verdict, so it moved nothing — the hole was in coverage, not in that change.
GATED = ("v15_pilot.py", "switch_parity.py", "switch_types.py", "tools/switch_types.py")
GATED_PREFIX = ("tools/v15_", "tools/v17_", "tools/v18_")
# 2026-10-20: V18 brain code + its data (trained models, digests) are bench inputs too; the bench tree is a full copy of the champion
# tree, so data files copied here are visible to the candidate pilot. Deploy is the same generic path: a destination whose parent dir
# does not exist is skipped (not failed), exactly as the local branch already did.
GATED_DATA_RE = re.compile(r"^data/(brain/v18_brain_[A-Za-z0-9_]+|learn/(?:digest|needed_filters|switch_profile|positives_all|binding_map|linknet_proposals|must_pos)_[A-Za-z0-9_]+)\.json(?:\.gz)?$")
MODEL_BUNDLE_PATH = "tools/v18_model_bundle.json"
MODEL_HOLDOUT_DAYS = 7
MODEL_CATEGORIES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
MODEL_ARTIFACT_NAMES = {f"{kind}_{category}.json{'.gz' if kind == 'positives_all' else ''}" for category in MODEL_CATEGORIES for kind in ("v18_brain", "digest", "needed_filters", "switch_profile", "positives_all", "binding_map", "linknet_proposals", "must_pos")}
CODE_EXCLUDE = ["data", "backtest_v8", "backtest_v7", "SPREADSHEETS", "klines_*", "archive", "backups", ".venv", "backtest_framework", ".git", "logs", "*.log", "*.db", "*.sqlite", "*.npz", "*.xlsx", "__pycache__", ".history", ".elastic"]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def model_bundle_pin(root):
    root = Path(root)
    manifest_path = root / MODEL_BUNDLE_PATH
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"model bundle declaration missing or symlinked: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    declaration = json.loads(manifest_bytes)
    expected = declaration.get("expected_sha256_by_filename")
    if not isinstance(expected, dict) or set(expected) != MODEL_ARTIFACT_NAMES:
        raise ValueError("model bundle must declare exactly 32 artifacts")
    artifacts = {}
    for name, digest in expected.items():
        relative = f"data/brain/{name}" if name.startswith("v18_brain_") else f"data/learn/{name}"
        source = root / relative
        if not GATED_DATA_RE.fullmatch(relative) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid gated model artifact declaration: {name}")
        if source.is_symlink() or not source.is_file() or sha256(source) != digest:
            raise ValueError(f"model artifact missing or fails its declared SHA256: {relative}")
        artifacts[relative] = digest
    return {"bundle_id": declaration.get("bundle_id"), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "artifact_sha256": artifacts}


def model_snapshot_pin():
    snapshot = BENCH / f"snap_{SNAP}"
    raw = (snapshot / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    sides = manifest.get("sides")
    if not isinstance(sides, list) or len(sides) != 8 or len(set(sides)) != 8:
        raise ValueError("model comparison requires a snapshot manifest with exactly eight unique sides")
    if set(gate_sides()) != set(sides):
        raise ValueError("model comparison gate side selection must equal the complete pinned snapshot side set")
    holdout_start_by_side = {}
    start_sha256_by_side = {}
    for side in sides:
        symbol = side.rsplit("_", 1)[0]
        npz = snapshot / "npz" / f"{symbol}.npz"
        if npz.is_symlink() or not npz.is_file():
            raise ValueError(f"model comparison snapshot NPZ is missing or symlinked: {symbol}")
        npz_record = (manifest.get("npz") or {}).get(symbol) or {}
        if npz_record.get("md5") != md5(npz) or npz_record.get("bytes") != npz.stat().st_size:
            raise ValueError(f"model comparison snapshot NPZ differs from its frozen manifest: {symbol}")
        start_path = snapshot / "starts" / f"{side}_autopsy_base.json"
        expected_start_md5 = (manifest.get("starts") or {}).get(side)
        if not expected_start_md5 or not start_path.is_file() or md5(start_path) != expected_start_md5:
            raise ValueError(f"model comparison snapshot start overrides differ from frozen manifest: {side}")
        start_sha256_by_side[side] = sha256(start_path)
        code = "import numpy as np,sys; z=np.load(sys.argv[1],mmap_mode='r',allow_pickle=True); k=next((k for k in z.files if 'timestamp' in k and '15m' in k),None); print('' if k is None or len(z[k]) < 2 else float(z[k][-1]))"
        result = subprocess.run([str(SRC / ".venv" / "bin" / "python"), "-c", code, str(npz)], capture_output=True, text=True, timeout=120)
        try:
            last_bar = float(result.stdout.strip()) if result.returncode == 0 else math.nan
        except (ValueError, TypeError):
            last_bar = math.nan
        if not math.isfinite(last_bar):
            raise ValueError(f"model comparison cannot read final 15m timestamp from snapshot NPZ: {symbol}")
        start = dt.datetime.fromtimestamp(last_bar - MODEL_HOLDOUT_DAYS * 86400, dt.timezone.utc)
        holdout_start_by_side[side] = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    template_sha256_by_name = {}
    for name, expected_md5 in (manifest.get("templates") or {}).items():
        path = snapshot / "templates" / name
        if not path.is_file() or md5(path) != expected_md5:
            raise ValueError(f"model comparison snapshot template differs from frozen manifest: {name}")
        template_sha256_by_name[name] = sha256(path)
    if not template_sha256_by_name:
        raise ValueError("model comparison snapshot lacks frozen templates")
    return {"snapshot_id": SNAP, "manifest_sha256": hashlib.sha256(raw).hexdigest(), "sides": sides, "holdout_days": MODEL_HOLDOUT_DAYS, "npz_sha256_by_symbol": {side.rsplit("_", 1)[0]: sha256(snapshot / "npz" / f"{side.rsplit('_', 1)[0]}.npz") for side in sides}, "oos_start_by_side": holdout_start_by_side, "start_sha256_by_side": start_sha256_by_side, "template_sha256_by_name": template_sha256_by_name}


def model_training_market_max_by_category(root):
    root = Path(root)
    latest = {}
    for category in MODEL_CATEGORIES:
        values = []
        sources = ((f"data/brain/v18_brain_{category}.json", ("_meta", "last_ts")), (f"data/learn/digest_{category}.json", ("meta", "era_cut")), (f"data/learn/linknet_proposals_{category}.json", ("meta", "corpus_last_ts")))
        for relative, keys in sources:
            path = root / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"model training window source is missing or symlinked: {relative}")
            document = json.loads(path.read_text())
            value = document
            for key in keys:
                value = value.get(key) if isinstance(value, dict) else None
            if not isinstance(value, str):
                raise ValueError(f"model training market cutoff is absent: {relative}:{'.'.join(keys)}")
            try:
                stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid model training market cutoff {relative}: {value}") from exc
            if stamp.tzinfo is None:
                raise ValueError(f"model training market cutoff lacks timezone: {relative}")
            values.append(stamp.astimezone(dt.timezone.utc))
        latest[category] = max(values).isoformat(timespec="seconds").replace("+00:00", "Z")
    return latest


def champion_resolved_model_inventory(root, env):
    root = Path(root).resolve()
    planner = root / "tools" / "v15_v18_planner.py"
    source = planner.read_bytes()
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "learn_file"]
    if len(functions) != 1:
        raise ValueError("current champion planner must define exactly one learn_file resolver")
    namespace = {"Path": Path, "os": types.SimpleNamespace(environ=dict(env)), "__file__": str(planner)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(planner), "exec"), namespace)
    resolved = {}
    for name in sorted(MODEL_ARTIFACT_NAMES):
        sub = "brain" if name.startswith("v18_brain_") else "learn"
        path = Path(namespace["learn_file"](name, root, sub))
        if not path.is_absolute():
            path = root / path
        path = Path(os.path.abspath(path))
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"current champion resolver did not return a regular model file: {name}")
        resolved[name] = {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}
    return resolved


def validate_model_training_windows(spec, reference_root, candidate_root, snapshot_pin):
    if int(spec.get("oos_holdout_days") or 0) != snapshot_pin.get("holdout_days"):
        raise ValueError("comparison spec holdout days differ from exact frozen snapshot holdout")
    snapshot = spec.get("snapshot") or {}
    for key in ("snapshot_id", "manifest_sha256", "npz_sha256_by_symbol", "oos_start_by_side", "start_sha256_by_side", "template_sha256_by_name"):
        if snapshot.get(key) != snapshot_pin.get(key):
            raise ValueError(f"comparison spec {key} differs from the exact frozen snapshot")
    declared = spec.get("training_market_max_by_category")
    if not isinstance(declared, dict):
        raise ValueError("comparison spec must pin both arms' training market cutoffs")
    actual = {"reference": model_training_market_max_by_category(reference_root), "candidate": model_training_market_max_by_category(candidate_root)}
    for arm in ("reference", "candidate"):
        if declared.get(arm) != actual[arm]:
            raise ValueError(f"comparison spec {arm} training market cutoffs differ from pinned model artifacts")
    starts = snapshot_pin.get("oos_start_by_side") or {}
    if set(starts) != set(snapshot_pin.get("sides") or []):
        raise ValueError("exact OOS start timestamps are missing for one or more pinned sides")
    for side in snapshot_pin["sides"]:
        category = ("CRYPTO_" if side.split("_", 1)[0].endswith("USDC") or side.split("_", 1)[0].endswith("USDT") else "STOCKS_") + side.rsplit("_", 1)[1]
        holdout = dt.datetime.fromisoformat(starts[side].replace("Z", "+00:00"))
        for arm in ("reference", "candidate"):
            trained = dt.datetime.fromisoformat(actual[arm][category].replace("Z", "+00:00"))
            if trained >= holdout:
                raise ValueError(f"{arm} model training market cutoff overlaps exact OOS holdout for {side}")
    return actual


def validate_model_authority_eligibility(spec, candidate_root, snapshot_pin, reference_root=None):
    if not spec.get("promotion_eligible"):
        return False
    # The user's 101-result rule governs authoritative CELL decisions, not S2 promotion of learned sampling models.
    # No raw per-cell proof validator exists here: static authority changes remain refused, including hashed counts.
    if reference_root is None:
        raise ValueError("learned-only model promotion requires the pinned reference model root")
    reference_root, candidate_root = Path(reference_root), Path(candidate_root)
    brain_fields = {"_meta", "prequential_mb", "heads", "buckets", "ema", "cs", "regime", "pair", "sym", "coverage", "exits", "prequential", "forward_eval", "nodes"}
    brain_meta = {"at", "bind", "cat_side", "counters", "cs_cols", "first_ts", "floor_per_1000", "gain_eligibility_policy", "gain_target", "heads_mb", "heads_trained", "last_ts", "m_shrink", "nf", "p_ref", "pair_cols", "pos_floor_pp", "regime_cols", "schema", "source_noop_gain", "sym_cols", "trimmed"}
    linknet_fields = {"meta", "template_untried", "template_untried_by_chain", "rows_untried", "pair_with_context", "needs_row", "fixed_stop_family"}
    linknet_meta = {"beyond_template_enumerated", "beyond_template_refused_by_switch_types", "built_at", "cat_side", "contexts", "corpus_last_ts", "distinct_live_chains", "diversity_cap_per_row", "label", "live_chains", "model_trained_at", "n_predictions_scored", "needs_row_source", "pair_with_context_file", "platt", "production_is_context_free", "production_scorer", "schema", "scores_npz", "secondary_p_v2ctx", "secs_cells", "secs_grid", "template_cells", "template_cells_n_lt_3", "template_cells_never_measured", "template_cells_self_pair_excluded", "unknown_context_features_at_scoring", "unknown_feature_hits_at_scoring"}
    def checked_diff(reference, candidate, allowed, label):
        if not isinstance(reference, dict) or not isinstance(candidate, dict):
            raise ValueError(f"unclassified model structure: {label}")
        changed = {key for key in set(reference) | set(candidate) if key not in reference or key not in candidate or reference[key] != candidate[key]}
        unknown = changed - allowed
        if unknown:
            raise ValueError(f"static authority or unclassified model field changed: {label} {sorted(unknown)}; authoritative cell changes require 101 qualifying unique raw results per cell, not a hashed count receipt")
    for name in sorted(MODEL_ARTIFACT_NAMES):
        relative = Path("data/brain" if name.startswith("v18_brain_") else "data/learn") / name
        before, after = reference_root / relative, candidate_root / relative
        if name.startswith(("needed_filters_", "switch_profile_", "positives_all_", "binding_map_", "must_pos_")):
            if sha256(before) != sha256(after):
                raise ValueError(f"static authority artifact changed: {name}; authoritative cell changes require 101 qualifying unique raw results per cell, not a hashed count receipt")
            continue
        reference, candidate = json.loads(before.read_bytes()), json.loads(after.read_bytes())
        if name.startswith("digest_"):
            checked_diff(reference, candidate, {"proposals", "scorer"}, name)
        elif name.startswith("v18_brain_"):
            checked_diff(reference, candidate, brain_fields, name)
            old_meta, new_meta = reference.get("_meta") or {}, candidate.get("_meta") or {}
            checked_diff(old_meta, new_meta, brain_meta, name + "._meta")
            for key in ("floor_per_1000", "p_ref", "m_shrink", "nf", "schema", "cat_side", "cs_cols", "pair_cols", "regime_cols", "sym_cols"):
                if old_meta.get(key) != new_meta.get(key):
                    raise ValueError(f"learned-only model changes must preserve runtime policy/schema: {name} {key}")
            if (new_meta.get("source_noop_gain") or {}).get("current_engine_cell_authority"):
                raise ValueError("historical source learning cannot declare current-engine cell authority without 101 qualifying unique raw results per cell")
        else:
            checked_diff(reference, candidate, linknet_fields, name)
            checked_diff(reference.get("meta") or {}, candidate.get("meta") or {}, linknet_meta, name + ".meta")
            if (reference.get("meta") or {}).get("cat_side") != (candidate.get("meta") or {}).get("cat_side"):
                raise ValueError(f"learned LinkNet category changed: {name}")
    return True


def model_runtime_pin(runtime_spec):
    runtime_spec = runtime_spec or {}
    manifest_path = Path(runtime_spec.get("path") or "").resolve()
    raw = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(raw).hexdigest()
    if manifest_sha != runtime_spec.get("sha256"):
        raise ValueError("comparison runtime manifest SHA256 differs from its pinned declaration")
    manifest = json.loads(raw)
    if manifest.get("schema") != "v15_nn_runtime/1" or not isinstance(manifest.get("files"), dict):
        raise ValueError("comparison runtime manifest has an unsupported schema")
    code_root = Path(manifest.get("code_root") or "").resolve()
    if not code_root.is_dir():
        raise ValueError("comparison runtime code_root is missing")
    files = manifest["files"]
    symlinks = manifest.get("symlinks") or {}
    if not isinstance(symlinks, dict) or not manifest.get("scope"):
        raise ValueError("comparison runtime must declare a scoped complete file/symlink inventory")
    actual_files, actual_symlinks = set(), {}
    for directory, dirnames, filenames in os.walk(code_root, followlinks=False):
        base = Path(directory)
        for name in list(dirnames):
            path = base / name
            if path.is_symlink():
                actual_symlinks[path.relative_to(code_root).as_posix()] = os.readlink(path)
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            relative = path.relative_to(code_root).as_posix()
            if path.is_symlink():
                actual_symlinks[relative] = os.readlink(path)
            elif path.is_file():
                actual_files.add(relative)
    if set(files) != actual_files or symlinks != actual_symlinks:
        raise ValueError("comparison runtime inventory is incomplete or includes paths outside the exact champion tree")
    for relative, expected in files.items():
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid comparison runtime file declaration: {relative}")
        source = code_root / rel
        if source.is_symlink() or not source.is_file() or sha256(source) != expected:
            raise ValueError(f"comparison runtime file fails its declared SHA256: {relative}")
    for relative, target in symlinks.items():
        rel = Path(relative)
        source = code_root / rel
        if rel.is_absolute() or ".." in rel.parts or not isinstance(target, str) or not source.is_symlink() or os.readlink(source) != target:
            raise ValueError(f"comparison runtime symlink differs from its pinned target: {relative}")
    return {"manifest_path": str(manifest_path), "manifest_sha256": manifest_sha, "code_root": str(code_root), "files": files, "symlinks": symlinks}


def verify_model_runtime_tree(runtime_pin, code_root):
    root = Path(code_root).resolve()
    for relative, expected in (runtime_pin or {}).get("files", {}).items():
        target = root / relative
        if target.is_symlink() or not target.is_file() or sha256(target) != expected:
            raise ValueError(f"model comparison runtime differs from pinned champion tree: {relative}")
    for relative, target in (runtime_pin or {}).get("symlinks", {}).items():
        link = root / relative
        if not link.is_symlink() or os.readlink(link) != target:
            raise ValueError(f"model comparison runtime symlink differs from pinned champion tree: {relative}")
    return True


def model_arm_flags(spec, arm_name, base_flags, cli_flags):
    equal_flags = spec.get("equal_runtime_flags") or {}
    arm_flags = spec.get(f"{arm_name}_runtime_flags") or {}
    if not isinstance(equal_flags, dict) or not isinstance(arm_flags, dict):
        raise ValueError("comparison runtime flags must be objects")
    prescribed = dict(equal_flags)
    prescribed.update(arm_flags)
    for key, value in cli_flags.items():
        if key not in prescribed:
            raise ValueError(f"--set {key} is not declared in the pinned comparison spec")
        if str(value) != str(prescribed[key]):
            raise ValueError(f"--set {key}={value} conflicts with pinned {arm_name} comparison flag {prescribed[key]}")
    flags = dict(base_flags)
    flags.update(equal_flags)
    flags.update(arm_flags)
    return flags


def model_comparison_spec_pin(path, reference_root, candidate_root, reference_pin, candidate_pin, snapshot_pin, champion_tree=None, base_flags=None, cli_flags=None):
    path = Path(path).resolve()
    raw = path.read_bytes()
    spec = json.loads(raw)
    if spec.get("schema") != "v15_nn_comparison/1":
        raise ValueError("model comparison spec has an unknown schema")
    runtime_pin = model_runtime_pin(spec.get("runtime"))
    if champion_tree is not None:
        verify_model_runtime_tree(runtime_pin, champion_tree)
    for arm_name, root, pin in (("reference", reference_root, reference_pin), ("candidate", candidate_root, candidate_pin)):
        arm = spec.get(arm_name) or {}
        declaration_path = Path(arm.get("path") or "").resolve()
        if arm_name == "reference" and declaration_path != Path(root).resolve() / MODEL_BUNDLE_PATH:
            raise ValueError(f"comparison spec {arm_name} bundle path differs from the submitted bundle")
        if arm.get("sha256") != pin.get("manifest_sha256"):
            raise ValueError(f"comparison spec {arm_name} bundle manifest hash differs from submitted bundle")
    baseline_provenance = spec.get("baseline_provenance") or {}
    if baseline_provenance.get("mode") != "current_champion_resolver":
        raise ValueError("canonical comparison baseline must be pinned to the current champion resolver")
    champion_root = Path(champion_tree).resolve() if champion_tree is not None else CHAMP / "tree"
    champion_meta_path = CHAMP / "meta.json"
    if not champion_meta_path.is_file() or baseline_provenance.get("champion_meta_sha256") != sha256(champion_meta_path):
        raise ValueError("comparison baseline does not pin current champion metadata")
    champion_meta = json.loads(champion_meta_path.read_text())
    if dict(base_flags or {}) != dict(champion_meta.get("flags") or {}):
        raise ValueError("comparison base flags do not equal the current champion flags")
    resolver_env = dict(champion_meta.get("env") or {})
    resolver_env.update(champion_meta.get("flags") or {})
    resolved = champion_resolved_model_inventory(champion_root, resolver_env)
    if baseline_provenance.get("resolved_artifacts") != resolved:
        raise ValueError("comparison baseline resolver paths or hashes differ from current champion")
    reference_declaration = json.loads((Path(reference_root) / MODEL_BUNDLE_PATH).read_text())
    if reference_declaration.get("linknet_activation"):
        raise ValueError("current champion reference bundle must not add a LinkNet activation declaration")
    for name, record in resolved.items():
        if reference_pin.get("artifact_sha256", {}).get(f"data/brain/{name}" if name.startswith("v18_brain_") else f"data/learn/{name}") != record["sha256"]:
            raise ValueError(f"reference model bundle is not byte-identical to current champion resolver output: {name}")
    training_windows = validate_model_training_windows(spec, reference_root, candidate_root, snapshot_pin)
    model_promotion_eligible = validate_model_authority_eligibility(spec, candidate_root, snapshot_pin, reference_root)
    base_flags = dict(base_flags or {})
    cli_flags = cli_flags or {}
    reference_flags = model_arm_flags(spec, "reference", base_flags, {})
    candidate_flags = model_arm_flags(spec, "candidate", base_flags, cli_flags)
    allowed_reference_overrides = {"V18_LINKNET_FIRST": "0", "V18_LINKNET_BUNDLE_REQUIRED": "0"}
    for key, value in reference_flags.items():
        if key in base_flags and str(value) == str(base_flags[key]):
            continue
        if allowed_reference_overrides.get(key) != str(value):
            raise ValueError(f"reference runtime flag {key} differs from actual champion settings")
    for key, value in base_flags.items():
        if key not in reference_flags or str(reference_flags[key]) != str(value):
            raise ValueError(f"reference runtime flags omit or alter actual champion setting {key}")
    if model_promotion_eligible:
        learned_activation = {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1"}
        model_defaults = {"V18_FLOOR_PER_1000": ("floor_per_1000", None, 5.0), "V18_BRAIN_PREF_R": ("p_ref", "R", 0.001), "V18_BRAIN_PREF_F": ("p_ref", "F", 0.002)}
        for key in set(reference_flags) | set(candidate_flags):
            if str(reference_flags.get(key)) != str(candidate_flags.get(key)) and learned_activation.get(key) != str(candidate_flags.get(key)):
                if key in model_defaults:
                    field, member, fallback = model_defaults[key]
                    equivalent = True
                    for category in MODEL_CATEGORIES:
                        metadata = json.loads((Path(reference_root) / f"data/brain/v18_brain_{category}.json").read_bytes()).get("_meta") or {}
                        default = (metadata.get(field) or {}).get(member, fallback) if member else metadata.get(field, fallback)
                        equivalent = equivalent and float(reference_flags.get(key, default)) == float(candidate_flags.get(key, default))
                    if equivalent:
                        continue
                raise ValueError(f"learned-only model promotion must preserve sampling policy runtime flags: {key}")
    if not reference_flags or not candidate_flags:
        raise ValueError("comparison spec must pin reference and candidate runtime flags")
    return {"comparison_id": spec.get("comparison_id"), "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "runtime": runtime_pin, "training_market_max_by_category": training_windows, "promotion_eligible": model_promotion_eligible, "promotion_scope": "learned_only_unchanged_static_authority" if model_promotion_eligible else "research_only", "authority_eligibility": spec.get("authority_eligibility"), "base_flags": base_flags, "reference_flags": reference_flags, "candidate_flags": candidate_flags}


def stage_model_bundle(source, destination):
    source, destination = Path(source), Path(destination)
    pin = model_bundle_pin(source)
    destination.mkdir(parents=True)
    manifest = source / MODEL_BUNDLE_PATH
    target_manifest = destination / MODEL_BUNDLE_PATH
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, target_manifest)
    for relative in pin["artifact_sha256"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    if model_bundle_pin(destination) != pin:
        raise ValueError("staged baseline model bundle differs from its validated source")
    return pin


def stage_common_champion_model_bundle(champion_tree, champion, destination):
    champion_tree, destination = Path(champion_tree), Path(destination)
    manifest = champion_tree / MODEL_BUNDLE_PATH
    if manifest.is_symlink():
        raise ValueError("champion model bundle declaration is symlinked")
    if not manifest.is_file():
        return None
    pin = model_bundle_pin(champion_tree)
    resolver_env = result_env(champion.get("env"))
    resolver_env.update(champion.get("flags") or {})
    resolved = champion_resolved_model_inventory(champion_tree, resolver_env)
    for name, record in resolved.items():
        relative = f"data/brain/{name}" if name.startswith("v18_brain_") else f"data/learn/{name}"
        if record.get("path") != relative or record.get("sha256") != pin["artifact_sha256"].get(relative):
            raise ValueError(f"champion resolver output is not the declared model bundle: {name}")
    staged_pin = stage_model_bundle(champion_tree, destination)
    if staged_pin != pin:
        raise ValueError("common champion model bundle changed while it was staged")
    return {"pin": pin, "bundle_path": str(destination.resolve()), "source_path": str(champion_tree.resolve()), "resolver_artifacts": resolved}


def common_champion_model_bundle_for_run(cd, meta, champion):
    binding = meta.get("common_champion_model_bundle")
    if not binding:
        return None
    expected = binding.get("pin") or {}
    source = Path(binding.get("source_path") or CHAMP / "tree")
    staged = Path(binding.get("bundle_path") or "")
    if source.resolve() != (CHAMP / "tree").resolve():
        raise ValueError("common model pin source is not the current champion tree")
    if model_bundle_pin(source) != expected:
        raise ValueError("current champion model bundle changed after code-only candidate submission")
    if model_bundle_pin(staged) != expected:
        raise ValueError("staged common champion model bundle changed after submission")
    resolver_env = result_env(champion.get("env"))
    resolver_env.update(champion.get("flags") or {})
    resolved = champion_resolved_model_inventory(source, resolver_env)
    if resolved != binding.get("resolver_artifacts"):
        raise ValueError("current champion model resolver output changed after code-only candidate submission")
    candidate_manifest = Path(cd) / "tree" / MODEL_BUNDLE_PATH
    if not candidate_manifest.is_file() or candidate_manifest.is_symlink() or sha256(candidate_manifest) != expected.get("manifest_sha256"):
        raise ValueError("code-only candidate no longer carries the pinned champion model declaration")
    return staged


def apply_common_champion_model_acceptance(cd, meta, decision):
    binding = meta.get("common_champion_model_bundle")
    if not binding:
        return decision
    reasons = []
    expected = binding.get("pin") or {}
    try:
        champion = json.loads((CHAMP / "meta.json").read_text()) if (CHAMP / "meta.json").is_file() else {}
        staged = common_champion_model_bundle_for_run(cd, meta, champion)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        staged = None
        reasons.append(f"common champion model pin changed before finalization: {exc}")
    run_pins = {}
    receipts_verified = True
    for label, arm in ((f"gate_{meta['id']}_champ", "baseline"), (f"gate_{meta['id']}_cand", "candidate")):
        run_dir = BENCH / "runs" / f"{SNAP}__{label}"
        try:
            run_root_pin = model_bundle_pin(run_dir / "root")
            receipt = json.loads((run_dir / "model_bundle_receipt.json").read_text())
            receipt_pin = {"bundle_id": receipt.get("bundle_id"), "manifest_sha256": receipt.get("manifest_sha256"), "artifact_sha256": receipt.get("artifact_sha256")}
            if run_root_pin != expected or receipt_pin != expected:
                reasons.append(f"{arm} run root did not consume the pinned common champion model bundle")
                receipts_verified = False
            run_pins[arm] = run_root_pin
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            reasons.append(f"{arm} run-root common model bundle verification failed: {exc}")
            receipts_verified = False
    decision["common_champion_model_bundle_acceptance"] = {"bundle_id": expected.get("bundle_id"), "manifest_sha256": expected.get("manifest_sha256"), "run_root_pins_verified": len(run_pins) == 2 and all(pin == expected for pin in run_pins.values()), "bundle_receipts_verified": receipts_verified, "champion_pin_revalidated": staged is not None}
    if reasons:
        decision["PASS"] = False
        decision.setdefault("reasons", []).extend(f"COMMON_CHAMPION_MODEL: {reason}" for reason in reasons)
    return decision


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rsync_tree(src, dst):
    ex = []
    for e in CODE_EXCLUDE:
        ex += ["--exclude", e]
    subprocess.run(["rsync", "-a", "--delete"] + ex + [f"{src}/", f"{dst}/"], check=True)


def ensure_champion():
    if not (CHAMP / "tree" / "v15_pilot.py").exists():
        (CHAMP / "tree").mkdir(parents=True, exist_ok=True)
        rsync_tree(SRC, CHAMP / "tree")
        meta = {"at": now(), "from": "production sandbox at gate install", "pilot_md5": md5(CHAMP / "tree" / "v15_pilot.py"), "flags": {}}
        write_json_atomic(CHAMP / "meta.json", meta)
    return json.loads((CHAMP / "meta.json").read_text())


def gated_path(rel):
    return rel in GATED or rel.startswith(GATED_PREFIX) or bool(GATED_DATA_RE.match(rel))


def snap_manifest():
    try:
        return json.loads((BENCH / f"snap_{SNAP}" / "manifest.json").read_text())
    except Exception:
        return {}


def gate_sides():
    if SIDES:
        return [s for s in SIDES.split(",") if s]
    man = snap_manifest()
    return [s for s in man.get("sides") or [] if any(s.startswith(sym) and s[len(sym):] in ("_LONG", "_SHORT") for sym in (man.get("npz") or {}))]


def result_env(env):
    return {k: str(v) for k, v in (env or {}).items() if k.startswith(("V15_", "V12_")) and k not in ENV_IGNORE}


def champ_env(champ):
    """The launch env the champion is benched (and production must run) with. Bootstrap = the snapshot's captured env."""
    return result_env(champ.get("env") if champ.get("env") is not None else snap_manifest().get("env"))


def env_diff(want, got):
    keys = sorted(set(want) | set(got))
    return {k: {"champion": want.get(k), "running": got.get(k)} for k in keys if want.get(k) != got.get(k)}


PROC_ENV_SH = "for p in $(pgrep -f '^[^ ]*python[^ ]* -u [^ ]*v15_pilot.py --sym-side'); do c=$(readlink /proc/$p/cwd); case \"$c\" in *v15_bench*) continue;; esac; echo \"@@PID $p $c\"; tr '\\0' '\\n' < /proc/$p/environ 2>/dev/null | grep -E '^V1[25]_'; done"


def running_pilot_envs(host):
    """Result-affecting env of every PRODUCTION pilot on a host (bench pilots under ~/v15_bench excluded)."""
    cmd = ["bash", "-c", PROC_ENV_SH] if host == "local" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, PROC_ENV_SH]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    envs, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@PID "):
            cur = {"pid": line.split()[1], "env": {}}
            envs.append(cur)
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur["env"][k] = v
    for e in envs:
        e["env"] = result_env(e["env"])
    return [e for e in envs if e["env"]]  # unreadable environ (process gone / not ours) carries no evidence


def cmd_submit(a):
    champ = ensure_champion()
    cid = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cd = CANDS / cid
    (cd / "tree").mkdir(parents=True)
    rsync_tree(CHAMP / "tree", cd / "tree")
    files = {}
    for spec in a.files or []:
        src, _, rel = spec.partition("=")
        rel = rel or os.path.relpath(os.path.abspath(src), str(SRC))
        if not gated_path(rel):
            print(f"[gate] {rel}: not a gated file (v15_pilot.py, tools/v15_*, tools/v17_*, tools/v18_*, data/brain/v18_brain_*.json, data/learn/digest_*.json) — refused", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        (cd / "tree" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, cd / "tree" / rel)
        files[rel] = md5(cd / "tree" / rel)
    flags = dict(champ.get("flags") or {})
    cli_flags = {}
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        cli_flags[k] = v
        flags[k] = v
    has_model_assets = MODEL_BUNDLE_PATH in files or any(GATED_DATA_RE.fullmatch(rel) for rel in files)
    model_overlay = None
    common_champion_model_bundle = None
    if has_model_assets:
        if MODEL_BUNDLE_PATH not in files or not a.nn_baseline_bundle or not a.nn_comparison_spec:
            print("[gate] model submission requires tools/v18_model_bundle.json, --nn-baseline-bundle, and --nn-comparison-spec pinning both arm declarations", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        try:
            candidate_pin = model_bundle_pin(cd / "tree")
            baseline_pin = stage_model_bundle(a.nn_baseline_bundle, cd / "nn_baseline_bundle")
            snapshot_pin = model_snapshot_pin()
            comparison_spec_pin = model_comparison_spec_pin(a.nn_comparison_spec, a.nn_baseline_bundle, cd / "tree", baseline_pin, candidate_pin, snapshot_pin, CHAMP / "tree", champ.get("flags") or {}, cli_flags)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[gate] invalid model bundle: {exc}", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        flags = comparison_spec_pin["candidate_flags"]
        model_overlay = {"candidate": candidate_pin, "baseline": baseline_pin, "baseline_bundle_path": str(cd / "nn_baseline_bundle"), "baseline_source_path": str(Path(a.nn_baseline_bundle).resolve()), "candidate_code_root": str((cd / "tree").resolve()), "comparison_spec": comparison_spec_pin, "snapshot": snapshot_pin, "champion_flags_sha256": hashlib.sha256(json.dumps(champ.get("flags") or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "base_flags": comparison_spec_pin["base_flags"], "reference_flags": comparison_spec_pin["reference_flags"], "candidate_flags": comparison_spec_pin["candidate_flags"]}
    elif a.nn_baseline_bundle or a.nn_comparison_spec:
        print("[gate] NN bundle/spec arguments require candidate model bundle assets", file=sys.stderr)
        shutil.rmtree(cd)
        return 2
    else:
        try:
            common_champion_model_bundle = stage_common_champion_model_bundle(CHAMP / "tree", champ, cd / "nn_common_champion_bundle")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"[gate] current champion model bundle cannot be safely pinned for code-only candidate: {exc}", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
    # Launch env (what the scheduler/autotune export at dispatch) is benched like code: candidate env = champion env + changes.
    env = champ_env(champ)
    if a.env_from_production:
        envs = running_pilot_envs(a.env_from_production)
        if not envs:
            print(f"[gate] --env-from-production {a.env_from_production}: no running production pilot found — refused", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        env = dict(envs[0]["env"])
    for kv in a.env or []:
        k, _, v = kv.partition("=")
        if v == "":
            env.pop(k, None)
        else:
            env[k] = v
    env = result_env(env)
    if model_overlay:
        model_overlay["common_env_sha256"] = hashlib.sha256(json.dumps(env, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    meta = {"id": cid, "submitted": now(), "note": a.note or "", "priority": int(a.priority or 0), "files": files, "flags": flags, "env": env, "env_changes": env_diff(champ_env(champ), env), "champion_pilot_md5": champ.get("pilot_md5"), "model_overlay": model_overlay, "common_champion_model_bundle": common_champion_model_bundle, "state": "pending"}
    if not files and not a.set and not meta["env_changes"] and not a.allow_identical:
        print("[gate] nothing to bench: no files, no --set, launch env identical to the champion's — refused", file=sys.stderr)
        shutil.rmtree(cd)
        return 2
    write_json_atomic(cd / "meta.json", meta)
    print(json.dumps(meta, indent=1))
    return 0


def bench_env(env):
    """Full env a bench pilot runs with: the snapshot's captured env (routing keys included) with the version's
    result-affecting env laid over it; a result key the version removed is removed. 2026-10-16: passing the filtered env
    alone dropped V12_ALLOW_STALE_NPZ, every frozen-snapshot NPZ read as STALE and both arms aborted in 10-40 s (rc=2)."""
    man_env = dict(snap_manifest().get("env") or {})
    out = {k: v for k, v in man_env.items() if k not in result_env(man_env)}
    out.update(result_env(env))
    out["V12_ALLOW_STALE_NPZ"] = "1"  # the snapshot is frozen by design; staleness vs wall clock is not a verdict
    return out


def bench(label, code, flags, env, model_bundle=None):
    env_file = GATE / f"env_{label}.json"
    write_json_atomic(env_file, bench_env(env))
    args = [sys.executable, str(BENCH / "tools" / "v15_npz_bench.py"), "run", "--snap", SNAP, "--label", label, "--code", str(code), "--only", ",".join(gate_sides()), "--env-file", str(env_file), "--parallel", "1", "--workers", "2", "--nice", "10", "--min-free-mb", "7000", "--force", "--set", "V15_HOLDOUT_DAYS=7"]
    if model_bundle:
        args += ["--model-bundle", str(model_bundle), "--clear-nn-env"]
    for k, v in flags.items():
        args += ["--set", f"{k}={v}"]
    for k in ("V15_BRAIN", "V15_BRAIN_NAKED"):
        if k not in flags:
            args += ["--set", f"{k}=0"]
    log = open(GATE / f"bench_{label}.log", "w")
    return subprocess.Popen(args, cwd=str(BENCH), stdout=log, stderr=subprocess.STDOUT)


def results(label):
    p = BENCH / "runs" / f"{SNAP}__{label}" / "results.json"
    return json.loads(p.read_text()).get("results") or {} if p.exists() else {}


def _progress_json(label, side):
    d = BENCH / "runs" / f"{SNAP}__{label}" / side / "progress"
    for p in sorted(d.glob("*_progress.json")) if d.exists() else []:
        if ".npzprev_" not in p.name and ".hollowprev_" not in p.name:
            return p
    return None


def truth_rescore(lc, la, ch, ca):
    """USER 2026-10-16 "numbers never exaggerated and match exactly the html charts": both arms' final sets (and the shared
    start set) re-scored by ONE scorer (tools/v15_bench_truth.py in the champion run root, same frozen NPZ) from the full
    execution ledger, unrealised window-edge marks excluded — the chart's own math. decide() uses these when present."""
    root = BENCH / "runs" / f"{SNAP}__{lc}" / "root"
    py = root / ".venv" / "bin" / "python"
    try:
        fit_days = int(json.loads((BENCH / "runs" / f"{SNAP}__{lc}" / "results.json").read_text()).get("window_days_fit") or 20)
    except Exception:
        fit_days = 20
    for s in set(ch) | set(ca):
        P, Q = _progress_json(lc, s), _progress_json(la, s)
        if not (P and Q and py.exists()):
            continue
        args = [str(py if py.exists() else sys.executable), str(BENCH / "tools" / "v15_bench_truth.py"), "--root", str(root), "--side", s, "--fit-days", str(fit_days), "--holdout-days", "7", "--set", f"champ={P}", "--set", f"cand={Q}", "--set", f"start={P}:initial_overrides"]
        try:
            r = subprocess.run(["nice", "-n", "10"] + args, capture_output=True, text=True, timeout=900)
            t = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except Exception as e:
            t = {"error": f"{type(e).__name__}: {e}"[:200]}
        for res, key in ((ch, "champ"), (ca, "cand")):
            if s in res and isinstance(t.get(key), dict):
                res[s]["truth"] = t[key]
                res[s]["truth_start"] = t.get("start")
            elif s in res:
                res[s]["truth_error"] = t.get("error") or "no truth row"
    return ch, ca


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def _cov(m, k):
    c = m.get("coverage") if isinstance(m.get("coverage"), dict) else {}
    return int(c.get(k) or 0)


def decide(ch, ca, sides=None):
    sides = sides or gate_sides() or sorted(set(ch) | set(ca))
    rows, missing, missing_champ, missing_cand, drops, uninformative, scored = [], [], [], [], [], [], []
    oos_drops, advisories = [], []
    sf_ch = sf_ca = so_ch = so_ca = 0.0
    cov = {"cells_champ": 0, "cells_cand": 0, "naked_champ": 0, "naked_cand": 0, "rows_champ": 0, "rows_cand": 0}
    n_truth = 0
    for s in sides:
        a, b = ch.get(s) or {}, ca.get(s) or {}
        fa, fb, oa, ob = _f(a.get("final_gain")), _f(b.get("final_gain")), _f(a.get("oos_final")), _f(b.get("oos_final"))
        ta, tb = a.get("truth") or {}, b.get("truth") or {}
        if all(_f((t.get(ph) or {}).get("gain_pct")) is not None for t in (ta, tb) for ph in ("fit", "oos")):
            # Chart-truth numbers (common scorer, no unrealised marks) replace each arm's self-reported gains.
            fa, fb, oa, ob = _f(ta["fit"]["gain_pct"]), _f(tb["fit"]["gain_pct"]), _f(ta["oos"]["gain_pct"]), _f(tb["oos"]["gain_pct"])
            n_truth += 1
        rows.append({"side": s, "fit_champ": fa, "fit_cand": fb, "oos_champ": oa, "oos_cand": ob, "wall_champ": a.get("wall_s"), "wall_cand": b.get("wall_s"), "evals_champ": a.get("evals_noncached"), "evals_cand": b.get("evals_noncached"),
                     "truth": bool(ta and tb), "engine_fit_champ": a.get("final_gain"), "engine_fit_cand": b.get("final_gain"), "engine_oos_champ": a.get("oos_final"), "engine_oos_cand": b.get("oos_final"),
                     "wr_champ": (ta.get("fit") or {}).get("wr_pct"), "wr_cand": (tb.get("fit") or {}).get("wr_pct"), "marks_champ": sum(int((ta.get(ph) or {}).get("unrealised_marks") or 0) for ph in ("fit", "oos")), "marks_cand": sum(int((tb.get(ph) or {}).get("unrealised_marks") or 0) for ph in ("fit", "oos")),
                     "oos_trades_champ": (ta.get("oos") or {}).get("trades"), "oos_trades_cand": (tb.get("oos") or {}).get("trades"), "oos_start": ((a.get("truth_start") or {}).get("oos") or {}).get("gain_pct"),
                     "trades_champ": (ta.get("fit") or {}).get("trades", a.get("trades")), "trades_cand": (tb.get("fit") or {}).get("trades", b.get("trades")), "dd_champ": (ta.get("fit") or {}).get("max_dd_pct", a.get("dd")), "dd_cand": (tb.get("fit") or {}).get("max_dd_pct", b.get("dd")), "tim_champ": (ta.get("fit") or {}).get("tim_pct", a.get("tim")), "tim_cand": (tb.get("fit") or {}).get("tim_pct", b.get("tim")),
                     "cells_champ": _cov(a, "cells_eval"), "cells_cand": _cov(b, "cells_eval"), "naked_champ": _cov(a, "naked_rows"), "naked_cand": _cov(b, "naked_rows"),
                     "skipped_champ": (a.get("coverage") or {}).get("skipped_by_mechanism") if isinstance(a.get("coverage"), dict) else None,
                     "skipped_cand": (b.get("coverage") or {}).get("skipped_by_mechanism") if isinstance(b.get("coverage"), dict) else None})
        # 2026-10-15: a side that fails IDENTICALLY on both trees (both IMPOSSIBLE / both no-progress) is a harness or
        # snapshot problem — it carries no information about the candidate, so it is excluded instead of auto-FAILing
        # every submission. A side missing on ONE tree only still blocks: that is the candidate losing a board.
        if None in (fa, oa) and None in (fb, ob):
            uninformative.append(s)
            continue
        if None in (fa, fb, oa, ob):
            missing.append(s)
            # 2026-10-20: attribute the loss to the arm that lost it. The champion arm missing a board is the HARNESS
            # (OOM sacrifice / lingering pilot lock killed the reference run) and says nothing about the candidate.
            if None in (fa, oa):
                missing_champ.append(s)
            if None in (fb, ob):
                missing_cand.append(s)
            continue
        scored.append(s)
        sf_ch += fa
        sf_ca += fb
        so_ch += oa
        so_ca += ob
        for k in ("cells", "naked"):
            cov[f"{k}_champ"] += rows[-1][f"{k}_champ"]
            cov[f"{k}_cand"] += rows[-1][f"{k}_cand"]
        cov["rows_champ"] += _cov(a, "rows")
        cov["rows_cand"] += _cov(b, "rows")
        if fb < fa - TOL_SIDE:
            drops.append(s)
        if ob < oa - TOL_SIDE:
            oos_drops.append(s)
    _n = max(1, len(scored))
    _sum_floor = round(NOISE_FLOOR_PER_SIDE * (_n ** 0.5), 3)
    # Corner-cut detection (USER 2026-10-16): coverage = what the boards actually evaluated. Unknown coverage on the champion
    # side (pre-coverage results) = ratio None = no verdict either way; a candidate whose coverage is unreadable while the
    # champion's is known counts as a cut (fail-closed).
    cell_ratio = (cov["cells_cand"] / cov["cells_champ"]) if cov["cells_champ"] else None
    naked_ratio = (cov["naked_cand"] / cov["naked_champ"]) if cov["naked_champ"] else None
    corner_cut = any(r is not None and r < COVERAGE_MIN for r in (cell_ratio, naked_ratio))
    reasons = []
    if not scored:
        reasons.append("no side scored on both versions")
    if missing_champ:
        reasons.append(f"HARNESS_FAIL: CHAMPION arm lost boards {missing_champ} (harness kill / lingering lock, not a candidate result) - no verdict on the candidate; requeue")
    if missing_cand:
        reasons.append(f"CANDIDATE arm lost boards: {missing_cand}")
    if drops:
        (reasons if FIT_BINDING else advisories).append(f"side fit drop > {TOL_SIDE}: {drops}")
    if oos_drops and not FIT_BINDING:
        # with fit advisory, a per-side OOS collapse is the per-side guard instead
        reasons.append(f"side OOS drop > {TOL_SIDE}: {oos_drops}")
    if scored and n_truth < len(scored) and REQUIRE_TRUTH:
        reasons.append(f"TRUTH_MISSING: chart-truth rescore available on {n_truth}/{len(scored)} scored sides — self-reported gains are never a PASS basis")
    if corner_cut:
        # Fewer evaluations must EARN their place: strictly better on unseen days by more than noise, never worse in fit.
        if not so_ca > so_ch + _sum_floor:
            reasons.append(f"CORNER_CUT (cells x{cell_ratio if cell_ratio is None else round(cell_ratio, 3)}, naked x{naked_ratio if naked_ratio is None else round(naked_ratio, 3)} < {COVERAGE_MIN}) without an OOS win: OOS {so_ch:.3f} -> {so_ca:.3f} needs > +{_sum_floor}")
        if sf_ca < sf_ch:
            reasons.append(f"CORNER_CUT with lower fit gain: {sf_ch:.3f} -> {sf_ca:.3f}")
    else:
        if sf_ca < sf_ch - TOL_SUM:
            # USER 2026-10-28 01:4xZ: "if v21 does not beat v20 in numbers unless you use the old accounting system that is
            # now forbidden it should still promote". The fit sum is the greedy in-sample envelope (measured 2026-10-27: 80%
            # lift, forward Spearman -0.15, 1,540 promotions +1,322 pp fit -> -205 pp forward), so a lower fit sum is
            # reported but is NOT a FAIL reason unless V15_GATE_FIT_BINDING=1. The unseen-days OOS sum stays binding.
            (reasons if FIT_BINDING else advisories).append(f"fit sum {sf_ch:.3f} -> {sf_ca:.3f} below tolerance {TOL_SUM}")
        if so_ca < so_ch - TOL_SUM:
            reasons.append(f"OOS sum {so_ch:.3f} -> {so_ca:.3f} below tolerance {TOL_SUM}")
    ok = not reasons
    wc = sum(r["wall_champ"] or 0 for r in rows)
    wn = sum(r["wall_cand"] or 0 for r in rows)
    return {"PASS": ok, "harness_fail": bool(missing_champ), "missing_champ": missing_champ, "missing_cand": missing_cand, "reasons": reasons, "advisories": advisories, "fit_binding": FIT_BINDING, "oos_side_drops": oos_drops, "corner_cut": corner_cut, "truth_sides": n_truth, "scorer": "chart-truth (tools/v15_bench_truth.py, common scorer, window-edge mark counted)" if n_truth else "self-reported final_gain/oos_final", "coverage": {**cov, "cell_ratio": None if cell_ratio is None else round(cell_ratio, 4), "naked_ratio": None if naked_ratio is None else round(naked_ratio, 4), "min": COVERAGE_MIN},
            "scored": scored, "missing": missing, "uninformative": uninformative, "side_drops": drops,
            "noise_floor_per_side": NOISE_FLOOR_PER_SIDE, "noise_floor_sum": _sum_floor,
            "sum_delta": round(sf_ca - sf_ch, 3), "sum_delta_in_noise": bool(abs(sf_ca - sf_ch) <= _sum_floor),
            "oos_delta": round(so_ca - so_ch, 3), "fit_sum_champ": round(sf_ch, 3), "fit_sum_cand": round(sf_ca, 3), "oos_sum_champ": round(so_ch, 3), "oos_sum_cand": round(so_ca, 3), "speedup": round(wc / wn, 3) if wn else None, "rows": rows, "tol_sum": TOL_SUM, "tol_side": TOL_SIDE}


def deploy(cd, meta):
    out = []
    flags_doc = json.dumps({"at": now(), "candidate": meta["id"], "flags": meta.get("flags") or {}}, indent=1)
    (GATE / "flags.json").write_text(flags_doc)
    # The benched launch env is published next to the flags: data/v15_gate_env.json = the env production must run.
    _env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(json.loads((CHAMP / "meta.json").read_text()) if (CHAMP / "meta.json").exists() else {})
    (GATE / "env.json").write_text(json.dumps({"at": now(), "candidate": meta["id"], "env": _env}, indent=1))
    extra = {"data/v15_gate_flags.json": GATE / "flags.json", "data/v15_gate_env.json": GATE / "env.json"}
    for host, root, optional in deploy_targets():
        for rel in list(meta.get("files") or {}) + list(extra):
            src = extra[rel] if rel in extra else (cd / "tree" / rel)
            want = md5(src)
            if host == "local":
                dst = Path(root) / rel
                if GATED_DATA_RE.fullmatch(rel) or rel == MODEL_BUNDLE_PATH:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                elif not dst.parent.exists():
                    out.append({"host": host, "root": str(root), "file": rel, "ok": False, **({"optional": True} if optional else {})})
                    continue
                tmp = dst.with_name(f".{dst.name}.gate.tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                got = md5(dst)
            else:
                tmpr = f"{root}/.{Path(rel).name}.gate.tmp"
                subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(src), f"{host}:{tmpr}"], check=False)
                make_parent = GATED_DATA_RE.fullmatch(rel) or rel == MODEL_BUNDLE_PATH
                parent_cmd = f"mkdir -p {root}/{Path(rel).parent} && " if make_parent else f"test -d {root}/{Path(rel).parent} && "
                r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"{parent_cmd}mv {tmpr} {root}/{rel} && md5sum {root}/{rel} || rm -f {tmpr}"], capture_output=True, text=True)
                got = (r.stdout.split() or [""])[0]
            out.append({"host": host, "root": str(root), "file": rel, "ok": got == want, **({"optional": True} if optional else {})})
    return out


def cmd_publish_env(a):
    """Publish the CHAMPION's benched launch env as data/v15_gate_env.json on every production root (the scheduler appends it
    to each 30D launch, dispatch drops unbenched autotune exports while it exists). Only the champion env — never a candidate's."""
    champ = ensure_champion()
    env = champ_env(champ)
    (GATE / "env.json").write_text(json.dumps({"at": now(), "candidate": f"champion {champ.get('pilot_md5')}", "env": env}, indent=1))
    want = md5(GATE / "env.json")
    out = []
    for host, root, optional in deploy_targets():
        rel = "data/v15_gate_env.json"
        if host == "local":
            dst = Path(root) / rel
            if not dst.parent.exists():
                continue
            tmp = dst.with_name(f".{dst.name}.gate.tmp")
            shutil.copy2(GATE / "env.json", tmp)
            os.replace(tmp, dst)
            got = md5(dst)
        else:
            tmpr = f"{root}/.v15_gate_env.json.gate.tmp"
            subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(GATE / "env.json"), f"{host}:{tmpr}"], check=False)
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"test -d {root}/data && mv {tmpr} {root}/{rel} && md5sum {root}/{rel} || rm -f {tmpr}"], capture_output=True, text=True)
            got = (r.stdout.split() or [""])[0]
        out.append({"host": host, "root": str(root), "ok": got == want, **({"optional": True} if optional else {})})
    with open(DECISIONS, "a") as fh:
        fh.write(json.dumps({"id": f"publish-env-{now()}", "state": "ENV_PUBLISHED", "env": env, "deploy": out}) + "\n")
    print(json.dumps({"env_keys": len(env), "deploy": out}, indent=1))
    return 0 if deploy_complete(out) else 1


def read_meta(cd):
    """A candidate's meta, or None when it is missing/empty/corrupt. 2026-10-15: one 0-byte meta.json (ENOSPC during
    submit) made the listing comprehension raise JSONDecodeError, so EVERY `run` tick died before benching anything —
    the gate was dead from 00:15Z to 02:20Z and the guard kept falling production back to the champion meanwhile.
    An unreadable meta is quarantined as BROKEN (never silently re-run) and the tick continues."""
    p = Path(cd) / "meta.json"
    try:
        return json.loads(p.read_text())
    except Exception as e:
        try:
            write_json_atomic(p, {"id": Path(cd).name, "state": "BROKEN", "error": f"{type(e).__name__}: {e}"[:200], "quarantined": now()})
        except Exception:
            pass
        print(f"[gate] BROKEN candidate {Path(cd).name}: unreadable meta.json ({type(e).__name__}) — quarantined, not benched", flush=True)
        return None


def write_json_atomic(path, obj):
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def requeue_stale(metas):
    """A candidate whose bench died (ENOSPC, OOM, reboot) stays state=running forever and is never retried, because
    only `pending` is picked up. After RUN_TIMEOUT_MIN with no live bench it goes back to pending (max 2 requeues)."""
    for cd, meta in metas:
        if meta.get("state") != "running":
            continue
        try:
            age_min = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(meta.get("started") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 60.0
        except Exception:
            age_min = RUN_TIMEOUT_MIN + 1
        if age_min < RUN_TIMEOUT_MIN:
            continue
        if int(meta.get("requeues") or 0) >= 2:
            meta["state"] = "FAIL_STALLED"
            meta["note"] = f"{meta.get('note') or ''} [stalled {age_min:.0f} min, 2 requeues spent]".strip()
        else:
            meta["state"] = "pending"
            meta["requeues"] = int(meta.get("requeues") or 0) + 1
        meta["requeued_at"] = now()
        write_json_atomic(Path(cd) / "meta.json", meta)
        print(f"[gate] candidate {meta.get('id')} stale {age_min:.0f} min -> {meta['state']}", flush=True)


def cmd_run(a):
    GATE.mkdir(parents=True, exist_ok=True)
    champ = ensure_champion()
    metas = [(p, m) for p in sorted(CANDS.glob("*")) if (p / "meta.json").exists() and (m := read_meta(p)) is not None] if CANDS.exists() else []
    requeue_stale(metas)
    pend = sorted([p for p, _m in metas if (read_meta(p) or {}).get("state") == "pending"], key=lambda p: (-int((read_meta(p) or {}).get("priority") or 0), p.name))
    if not pend:
        write_status(champ)
        return 0
    free_mb = shutil.disk_usage(str(BENCH)).free / (1 << 20)
    if free_mb < MIN_FREE_MB:
        print(f"[gate] HOLD: {free_mb:.0f} MB free on {BENCH} < {MIN_FREE_MB:.0f} MB — two bench runs need ~12 GB; benching now would truncate results.json (2026-10-14 ENOSPC)", flush=True)
        write_status(champ)
        return 0
    cd = pend[0]
    meta = read_meta(cd) or {}
    model_overlay = meta.get("model_overlay")
    common_model_bundle = None
    if model_overlay:
        try:
            candidate_pin = model_bundle_pin(cd / "tree")
            baseline_path = Path(model_overlay["baseline_bundle_path"])
            baseline_pin = model_bundle_pin(baseline_path)
            current_snapshot = model_snapshot_pin()
            baseline_source = Path(model_overlay["baseline_source_path"])
            baseline_source_pin = model_bundle_pin(baseline_source)
            current_champion_flags = champ.get("flags") or {}
            champion_flags_sha = hashlib.sha256(json.dumps(current_champion_flags, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if champion_flags_sha != model_overlay.get("champion_flags_sha256"):
                raise ValueError("current champion flags changed after model comparison submit")
            comparison_spec_pin = model_comparison_spec_pin(model_overlay["comparison_spec"]["path"], baseline_source, cd / "tree", baseline_source_pin, candidate_pin, current_snapshot, CHAMP / "tree", model_overlay.get("base_flags"))
            verify_model_runtime_tree(comparison_spec_pin["runtime"], cd / "tree")
            common_env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(champ)
            common_env_sha = hashlib.sha256(json.dumps(common_env, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"[gate] REFUSED model overlay candidate {meta.get('id')}: {exc}", file=sys.stderr)
            return 2
        if candidate_pin != model_overlay.get("candidate") or baseline_pin != model_overlay.get("baseline") or baseline_source_pin != model_overlay.get("baseline") or current_snapshot != model_overlay.get("snapshot") or comparison_spec_pin != model_overlay.get("comparison_spec"):
            print(f"[gate] REFUSED model overlay candidate {meta.get('id')}: pinned bundle changed after submit", file=sys.stderr)
            return 2
        if (meta.get("flags") or {}) != model_overlay.get("candidate_flags") or comparison_spec_pin.get("reference_flags") != model_overlay.get("reference_flags") or comparison_spec_pin.get("candidate_flags") != model_overlay.get("candidate_flags") or common_env_sha != model_overlay.get("common_env_sha256"):
            print(f"[gate] REFUSED model overlay candidate {meta.get('id')}: common runtime settings changed after submit", file=sys.stderr)
            return 2
    elif meta.get("common_champion_model_bundle"):
        try:
            common_model_bundle = common_champion_model_bundle_for_run(cd, meta, champ)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"[gate] REFUSED code-only candidate {meta.get('id')}: common champion model pin changed: {exc}", file=sys.stderr)
            return 2
    meta["state"] = "running"
    meta["started"] = now()
    write_json_atomic(cd / "meta.json", meta)
    lc, la = f"gate_{meta['id']}_champ", f"gate_{meta['id']}_cand"
    if model_overlay:
        common_env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(champ)
        p1 = bench(lc, CHAMP / "tree", model_overlay["reference_flags"], common_env, Path(model_overlay["baseline_bundle_path"]))
        p2 = bench(la, cd / "tree", model_overlay["candidate_flags"], common_env, cd / "tree")
    else:
        p1 = bench(lc, CHAMP / "tree", champ.get("flags") or {}, champ_env(champ), common_model_bundle)
        p2 = bench(la, cd / "tree", meta.get("flags") or {}, result_env(meta["env"]) if meta.get("env") is not None else champ_env(champ), common_model_bundle)
    p1.wait()
    p2.wait()
    decision = decide(*truth_rescore(lc, la, results(lc), results(la)))
    return finalize(cd, meta, decision)


def apply_model_overlay_acceptance(cd, meta, decision):
    overlay = meta.get("model_overlay")
    if not overlay:
        return decision
    reasons = []
    expected_sides = set((overlay.get("snapshot") or {}).get("sides") or [])
    training_windows_verified = False
    whole_board_wall = {}
    promotion_eligible = bool(((overlay.get("comparison_spec") or {}).get("promotion_eligible")))
    if not promotion_eligible:
        reasons.append("pinned model comparison spec is explicitly non-promotable")
    expected_candidate_files = {MODEL_BUNDLE_PATH} | set((overlay.get("candidate") or {}).get("artifact_sha256", {}))
    if set(meta.get("files") or {}) != expected_candidate_files:
        reasons.append("candidate submission is not an exact model-only 33-file overlay")
    try:
        current_snapshot = model_snapshot_pin()
        if current_snapshot != overlay.get("snapshot"):
            reasons.append("pinned eight-side snapshot identity changed")
        baseline_source = Path(overlay["baseline_source_path"])
        baseline_source_pin = model_bundle_pin(baseline_source)
        candidate_pin = model_bundle_pin(cd / "tree")
        comparison_spec_pin = model_comparison_spec_pin(overlay["comparison_spec"]["path"], baseline_source, cd / "tree", baseline_source_pin, candidate_pin, current_snapshot, CHAMP / "tree", overlay.get("base_flags"))
        if comparison_spec_pin != overlay.get("comparison_spec"):
            reasons.append("pinned comparison spec, runtime, model training cutoffs, or arm flags changed")
        else:
            training_windows_verified = True
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        reasons.append(f"pinned snapshot/spec/training window could not be verified: {exc}")
    for label, arm in ((f"gate_{meta['id']}_champ", "baseline"), (f"gate_{meta['id']}_cand", "candidate")):
        expected = overlay.get(arm) or {}
        run_dir = BENCH / "runs" / f"{SNAP}__{label}"
        try:
            actual = model_bundle_pin(run_dir / "root")
            if actual != expected:
                reasons.append(f"{arm} actual run-root model declaration or artifact hashes differ from the pinned bundle")
            receipt = json.loads((run_dir / "model_bundle_receipt.json").read_text())
            if receipt.get("manifest_sha256") != expected.get("manifest_sha256") or receipt.get("artifact_sha256") != expected.get("artifact_sha256"):
                reasons.append(f"{arm} build_root receipt differs from the pinned bundle")
            snapshot_inputs = receipt.get("snapshot_inputs") or {}
            expected_inputs = {key: overlay["snapshot"][key] for key in ("npz_sha256_by_symbol", "template_sha256_by_name", "start_sha256_by_side")}
            if snapshot_inputs != expected_inputs:
                reasons.append(f"{arm} build_root receipt differs from pinned NPZ/template/start inputs")
            for symbol, digest in expected_inputs["npz_sha256_by_symbol"].items():
                path = run_dir / "root" / "backtest_v8" / "indicators" / f"{symbol}.npz"
                if not path.is_file() or sha256(path) != digest:
                    reasons.append(f"{arm} run-root NPZ bytes differ from pinned snapshot: {symbol}")
            for name, digest in expected_inputs["template_sha256_by_name"].items():
                paths = (run_dir / "root" / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / name, run_dir / "root" / "SPREADSHEETS" / name)
                if any(not path.is_file() or sha256(path) != digest for path in paths):
                    reasons.append(f"{arm} run-root template bytes differ from pinned snapshot: {name}")
            run_result = json.loads((run_dir / "results.json").read_text())
            whole_board_wall[arm] = run_result.get("board_wall_s")
            verify_model_runtime_tree(overlay["comparison_spec"]["runtime"], run_dir / "root")
            training_actual = model_training_market_max_by_category(run_dir / "root")
            training_key = "reference" if arm == "baseline" else "candidate"
            training_expected = (overlay.get("comparison_spec") or {}).get("training_market_max_by_category", {}).get(training_key)
            if training_actual != training_expected:
                reasons.append(f"{arm} run-root model training cutoffs differ from the pinned source artifacts: actual={training_actual} expected={training_expected}")
                training_windows_verified = False
            reasons.extend(validate_model_run_evidence(run_dir, label, run_dir / "root", expected_sides, overlay, arm))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            reasons.append(f"{arm} actual run-root model/runtime verification failed: {exc}")
            training_windows_verified = False
    rows = decision.get("rows") or []
    row_by_side = {row.get("side"): row for row in rows if isinstance(row, dict)}
    scored = set(decision.get("scored") or [])
    coverage_complete = len(expected_sides) == 8 and set(row_by_side) == expected_sides and scored == expected_sides
    truth_complete = coverage_complete and decision.get("truth_sides") == 8 and all(bool(row_by_side[side].get("truth")) for side in expected_sides)
    if not coverage_complete:
        reasons.append("canonical gate results do not cover all eight pinned sides")
    if not truth_complete:
        reasons.append("all eight sides lack common chart-truth OOS scores")
    try:
        baseline_scores = [float(row_by_side[side]["oos_champ"]) for side in sorted(expected_sides)]
        candidate_scores = [float(row_by_side[side]["oos_cand"]) for side in sorted(expected_sides)]
        baseline_walls = [float(row_by_side[side]["wall_champ"]) for side in sorted(expected_sides)]
        candidate_walls = [float(row_by_side[side]["wall_cand"]) for side in sorted(expected_sides)]
        if not all(math.isfinite(value) for value in baseline_scores + candidate_scores + baseline_walls + candidate_walls):
            raise ValueError("non-finite OOS score or wall measurement")
        if not all(value > 0 for value in baseline_walls + candidate_walls):
            raise ValueError("whole-board wall measurements must be positive")
        baseline_sum, candidate_sum = sum(baseline_scores), sum(candidate_scores)
        if baseline_sum == 0:
            quality_change = None
            quality_pass = False
            reasons.append("aggregate baseline OOS score is zero; relative 20% quality change is undefined")
        else:
            quality_change = 100.0 * (candidate_sum - baseline_sum) / abs(baseline_sum)
            quality_pass = quality_change >= 20.0
            if not quality_pass:
                reasons.append("aggregate OOS quality change is below 20%")
        baseline_wall, candidate_wall = sum(baseline_walls), sum(candidate_walls)
        if not math.isfinite(baseline_wall) or not math.isfinite(candidate_wall) or baseline_wall <= 0 or candidate_wall <= 0:
            raise ValueError("whole-board active wall-clock measurements are missing, non-finite, or non-positive")
        time_reduction = 100.0 * (baseline_wall - candidate_wall) / baseline_wall
        speed_pass = time_reduction > 90.0
        if not speed_pass:
            reasons.append("aggregate whole-board wall reduction is not greater than 90%")
        quality_label = "aggregate_loss_reduction_pct" if baseline_sum < 0 and candidate_sum < 0 else "aggregate_oos_gain_change_pct"
        side_regressions = sorted(side for side in expected_sides if row_by_side[side]["oos_cand"] < row_by_side[side]["oos_champ"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        baseline_sum = candidate_sum = baseline_wall = candidate_wall = None
        quality_change = time_reduction = None
        quality_pass = speed_pass = False
        quality_label = "undefined"
        side_regressions = []
        reasons.append(f"aggregate OOS/wall calculation failed: {exc}")
    decision["model_overlay_acceptance"] = {
        "baseline_model_bundle_id": (overlay.get("baseline") or {}).get("bundle_id"),
        "candidate_model_bundle_id": (overlay.get("candidate") or {}).get("bundle_id"),
        "snapshot": overlay.get("snapshot"),
        "coverage_complete_8": coverage_complete,
        "chart_truth_oos_complete_8": truth_complete,
        "aggregate_baseline_oos_gain_pct": baseline_sum,
        "aggregate_candidate_oos_gain_pct": candidate_sum,
        "aggregate_oos_quality_change_pct": quality_change,
        "quality_change_label": quality_label,
        "quality_pass": quality_pass,
        "side_regressions": side_regressions,
        "baseline_aggregate_side_processing_wall_seconds": baseline_wall,
        "candidate_aggregate_side_processing_wall_seconds": candidate_wall,
        "baseline_run_elapsed_seconds_diagnostic": whole_board_wall.get("baseline"),
        "candidate_run_elapsed_seconds_diagnostic": whole_board_wall.get("candidate"),
        "whole_board_time_reduction_pct": time_reduction,
        "speed_pass": speed_pass,
        "training_windows_verified": training_windows_verified,
        "run_root_model_hashes_verified": not any("run-root" in reason or "build_root" in reason for reason in reasons),
        "automatic_promotion_requires_canonical_gate_pass": True,
    }
    if reasons:
        decision["PASS"] = False
        decision.setdefault("reasons", []).extend(f"MODEL_OVERLAY: {reason}" for reason in reasons)
    return decision


def validate_model_run_evidence(run_dir, label, run_root, expected_sides, overlay, arm):
    """Bind scored rows to this exact code/model arm and require pilot-side consumer telemetry."""
    reasons = []
    results_path = Path(run_dir) / "results.json"
    try:
        payload = json.loads(results_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{arm} results.json is missing or unreadable: {exc}"]
    # The bench records the code source path, while its run root is a copied build. Pin each arm's intended source path.
    if arm == "candidate":
        expected_code = str(Path(overlay.get("candidate_code_root") or "").resolve())
    else:
        expected_code = str((CHAMP / "tree").resolve())
    if payload.get("snap") != SNAP or payload.get("label") != label or str(Path(str(payload.get("code") or "")).resolve()) != expected_code:
        reasons.append(f"{arm} results.json snapshot/label/code source does not match its pinned bench arm")
    if payload.get("pilot_md5") != md5(Path(run_root) / "v15_pilot.py"):
        reasons.append(f"{arm} results.json pilot_md5 does not match its actual run-root pilot")
    expected_flags = dict((overlay.get("reference_flags") if arm == "baseline" else overlay.get("candidate_flags")) or {})
    expected_set = {"V15_HOLDOUT_DAYS=7", *(f"{key}={value}" for key, value in expected_flags.items())}
    expected_set.update(f"{key}=0" for key in ("V15_BRAIN", "V15_BRAIN_NAKED") if key not in expected_flags)
    actual_set = payload.get("set") or []
    if not isinstance(actual_set, list) or len(actual_set) != len(set(actual_set)) or set(actual_set) != expected_set:
        reasons.append(f"{arm} results.json switch set differs from pinned holdout and runtime flags")
    if payload.get("window_days_fit") != 30 or payload.get("holdout_days") != MODEL_HOLDOUT_DAYS:
        reasons.append(f"{arm} results.json fit/holdout window differs from requested 30d/7d")
    nn_env = {key: value for key, value in expected_flags.items() if key.startswith(("V18_", "V20_"))}
    nn_env_sha = hashlib.sha256(json.dumps(nn_env, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload.get("nn_env_sha256") != nn_env_sha:
        reasons.append(f"{arm} effective V18_/V20_ environment hash differs from explicitly pinned arm flags")
    if not isinstance(payload.get("retry_count"), int) or payload.get("retry_count") < 0:
        reasons.append(f"{arm} retry/requeue count is absent or invalid")
    elif payload.get("retry_count") != 0:
        reasons.append(f"{arm} used {payload.get('retry_count')} memory requeues; per-side processing-time comparison is invalid")
    board_wall = payload.get("board_wall_s")
    if not isinstance(board_wall, (int, float)) or not math.isfinite(float(board_wall)) or board_wall <= 0:
        reasons.append(f"{arm} active whole-board wall measurement is absent or invalid")
    rows = payload.get("results") if isinstance(payload.get("results"), dict) else {}
    if set(rows) != set(expected_sides):
        reasons.append(f"{arm} results.json side rows do not equal the pinned eight-side set")
    for side in expected_sides:
        row = rows.get(side)
        if not isinstance(row, dict) or row.get("rc") != 0 or not isinstance(row.get("wall_s"), (int, float)) or not math.isfinite(float(row["wall_s"])) or row["wall_s"] <= 0:
            reasons.append(f"{arm} {side} has no successful finite positive wall measurement")
            continue
        if any(not isinstance(row.get(key), (int, float)) or not math.isfinite(float(row[key])) for key in ("final_gain", "oos_final")):
            reasons.append(f"{arm} {side} has no finite fit/OOS score in its original results.json row")
        progress_path = _progress_json(label, side)
        if progress_path is None:
            reasons.append(f"{arm} {side} has no pilot progress file for attribution")
            continue
        try:
            progress = json.loads(progress_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"{arm} {side} progress file is unreadable: {exc}")
            continue
        if progress.get("symside") != side or progress.get("window_days") != 30:
            reasons.append(f"{arm} {side} progress identity/window does not match the scored side")
        v18 = progress.get("v18") if isinstance(progress.get("v18"), dict) else {}
        if str(expected_flags.get("V18_BRAIN", "0")).strip().lower() in ("1", "true", "on"):
            brain = v18.get("brain") if isinstance(v18.get("brain"), dict) else {}
            side_venue = (((snap_manifest().get("admission") or {}).get("admission") or {}).get(side) or {}).get("venue")
            category_prefix = {"crypto": "CRYPTO", "stocks": "STOCKS"}.get(str(side_venue).lower())
            if not category_prefix:
                reasons.append(f"{arm} {side} pinned snapshot does not identify a market venue for brain attribution")
                continue
            category = f"{category_prefix}_{side.rsplit('_', 1)[-1]}"
            model_path = Path(run_root) / "data" / "brain" / f"v18_brain_{category}.json"
            if not brain or not model_path.is_file():
                reasons.append(f"{arm} {side} enabled brain did not report successful model initialization")
            else:
                try:
                    model = json.loads(model_path.read_text())
                    expected_at = (model.get("_meta") or {}).get("at")
                    if not expected_at or brain.get("model_at") != expected_at:
                        reasons.append(f"{arm} {side} brain telemetry model_at differs from the run-root brain artifact")
                except (OSError, json.JSONDecodeError) as exc:
                    reasons.append(f"{arm} {side} run-root brain artifact is unreadable: {exc}")
        if str(expected_flags.get("V18_LINKNET_FIRST", "0")).strip().lower() in ("1", "true", "on"):
            linknet = v18.get("linknet_first") if isinstance(v18.get("linknet_first"), dict) else {}
            why = str(linknet.get("why") or "")
            if not linknet or not why.startswith("ok "):
                reasons.append(f"{arm} {side} enabled LinkNet has no successful loader telemetry")
            elif any(not isinstance(linknet.get(key), int) or linknet.get(key) < 0 for key in ("proposals", "booked_cells", "cells_forced")):
                reasons.append(f"{arm} {side} LinkNet telemetry is incomplete or invalid")
        if str(expected_flags.get("V20_SELECTOR", "0")).strip().lower() in ("1", "true", "on"):
            selector = v18.get("v20") if isinstance(v18.get("v20"), dict) else {}
            if str(selector.get("why") or "").strip() != "binding_map ok":
                reasons.append(f"{arm} {side} enabled V20 selector has no successful binding-map loader telemetry")
    return reasons


def finalize(cd, meta, d):
    """The single PASS/deploy/record path, shared by run and redecide so the criterion can never diverge."""
    d = apply_model_overlay_acceptance(cd, meta, d)
    d = apply_common_champion_model_acceptance(cd, meta, d)
    meta["decision"] = d
    meta["finished"] = now()
    meta["state"] = "PASS" if d["PASS"] else "FAIL"
    if not d.get("scored") or d.get("harness_fail"):
        # No side produced a result on both versions, or the CHAMPION arm lost sides = the harness failed; never booked
        # as a verdict on the candidate. A candidate FAILs only on measured results of sides both arms finished.
        meta["state"] = "HARNESS_FAIL"
        meta["decision"]["PASS"] = False
    if d["PASS"]:
        meta["deploy"] = deploy(cd, meta)
        if deploy_complete(meta["deploy"]):
            try:
                if meta.get("model_overlay"):
                    install_model_bundle(cd / "tree", CHAMP / "tree", meta["model_overlay"]["candidate"])
                rsync_tree(cd / "tree", CHAMP / "tree")
                if meta.get("model_overlay") and model_bundle_pin(CHAMP / "tree") != meta["model_overlay"]["candidate"]:
                    raise ValueError("CHAMP resolved model bundle differs from the accepted candidate")
                _prev = json.loads((CHAMP / "meta.json").read_text()) if (CHAMP / "meta.json").exists() else {}
                _env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(_prev)
                write_json_atomic(CHAMP / "meta.json", {"at": now(), "from": f"candidate {meta['id']}", "pilot_md5": md5(CHAMP / "tree" / "v15_pilot.py"), "flags": meta.get("flags") or {}, "env": _env, "previous_pilot_md5": _prev.get("pilot_md5"), "decision": {k: v for k, v in d.items() if k != "rows"}})
            except (OSError, ValueError, KeyError) as exc:
                meta["state"] = "PASS_DEPLOY_INCOMPLETE"
                meta["model_install_error"] = str(exc)
        else:
            meta["state"] = "PASS_DEPLOY_INCOMPLETE"
    write_json_atomic(cd / "meta.json", meta)
    with open(DECISIONS, "a") as fh:
        fh.write(json.dumps({"id": meta["id"], "state": meta["state"], "note": meta.get("note"), "files": meta.get("files"), "flags": meta.get("flags"), **{k: v for k, v in d.items() if k != "rows"}}) + "\n")
    write_status(json.loads((CHAMP / "meta.json").read_text()))
    print(json.dumps({"id": meta["id"], "state": meta["state"], **{k: v for k, v in d.items() if k != "rows"}}, indent=1))
    return 0


def cmd_redecide(a):
    """Re-score an ALREADY BENCHED candidate from the artifacts its bench left on disk, with the same decide() and the
    same tolerances — no board is recomputed. For the runs whose sides were mis-read as "no progress json" by the
    pre-2026-10-15 bench glob (tools/v15_npz_bench.py find_progress): the boards really ran (rc=0), their progress
    JSONs are on disk, only the scoring step never saw them."""
    cd = CANDS / a.id
    meta = read_meta(cd)
    if meta is None:
        print(f"[gate] no candidate {a.id}", file=sys.stderr)
        return 2
    lc, la = f"gate_{a.id}_champ", f"gate_{a.id}_cand"
    live = subprocess.run(["pgrep", "-f", f"v15_npz_bench.py run .*--label gate_{a.id}_"], capture_output=True, text=True)
    if live.returncode == 0 and live.stdout.strip():
        print(f"[gate] REFUSED: a bench for {a.id} is still running (pids {live.stdout.split()}) — never score a half-written run", file=sys.stderr)
        return 3
    for lb in (lc, la):
        rp = BENCH / "runs" / f"{SNAP}__{lb}" / "results.json"
        if not rp.exists():
            print(f"[gate] REFUSED: {rp} missing — {a.id} was never benched", file=sys.stderr)
            return 3
        r = subprocess.run([sys.executable, str(BENCH / "tools" / "v15_npz_bench.py"), "rescore", "--snap", SNAP, "--label", lb], cwd=str(BENCH), capture_output=True, text=True)
        print(r.stdout.strip(), flush=True)
        if r.returncode != 0:
            print(f"[gate] rescore {lb} failed: {r.stderr[-400:]}", file=sys.stderr)
            return 3
    d = decide(*truth_rescore(lc, la, results(lc), results(la)))
    if a.dry_run:
        d = apply_model_overlay_acceptance(cd, meta, d)
        d = apply_common_champion_model_acceptance(cd, meta, d)
        print(json.dumps({"id": a.id, "would_be": "PASS" if d["PASS"] else "FAIL", "dry_run": True, **{k: v for k, v in d.items() if k != "rows"}}, indent=1))
        return 0
    return finalize(cd, meta, d)


def write_status(champ):
    L = [f"# V15 version gate — {now()}", "", f"champion: pilot md5 `{champ.get('pilot_md5')}` from {champ.get('from')} at {champ.get('at')}", f"gated switch defaults: `{json.dumps(champ.get('flags') or {})}`", "", "| candidate | state | note | fit Σ champ→cand | OOS Σ champ→cand | speedup | drops/missing |", "|---|---|---|---|---|---|---|"]
    if DECISIONS.exists():
        for line in DECISIONS.read_text().splitlines()[-20:]:
            r = json.loads(line)
            L.append(f"| {r.get('id','?')} | {r.get('state','?')} | {r.get('note','')[:40]} | {r.get('fit_sum_champ')}→{r.get('fit_sum_cand')} | {r.get('oos_sum_champ')}→{r.get('oos_sum_cand')} | {r.get('speedup')} | {r.get('side_drops')}/{r.get('missing')} |")
    pend = [p.name for p in CANDS.glob("*") if (p / "meta.json").exists() and (read_meta(p) or {}).get("state") in ("pending", "running")] if CANDS.exists() else []
    L += ["", f"pending/running: {pend}"]
    # Side-by-side of the last benched candidate vs the champion it was measured against (USER 2026-10-16).
    last = None
    for p in sorted(CANDS.glob("*")) if CANDS.exists() else []:
        m = read_meta(p) or {}
        if isinstance(m.get("decision"), dict) and m["decision"].get("rows"):
            last = m
    if last:
        d = last["decision"]
        L += ["", f"## Side by side — {last['id']} ({last.get('state')}) vs champion", f"reasons: {d.get('reasons') or '-'}", f"coverage: {d.get('coverage')}", f"env changes: {json.dumps(last.get('env_changes') or {})[:600]}", "",
              "| side | fit champ→cand | OOS champ→cand | trades | DD % | TIM % | cells eval | naked rows | wall s |", "|---|---|---|---|---|---|---|---|---|"]
        for r in d["rows"]:
            L.append(f"| {r['side']} | {r.get('fit_champ')}→{r.get('fit_cand')} | {r.get('oos_champ')}→{r.get('oos_cand')} | {r.get('trades_champ')}→{r.get('trades_cand')} | {r.get('dd_champ')}→{r.get('dd_cand')} | {r.get('tim_champ')}→{r.get('tim_cand')} | {r.get('cells_champ')}→{r.get('cells_cand')} | {r.get('naked_champ')}→{r.get('naked_cand')} | {r.get('wall_champ')}→{r.get('wall_cand')} |")
    for alert in ("ALERT_UNGATED_PILOT.json", "ALERT_UNGATED_ENV.json"):
        if (GATE / alert).exists():
            L += ["", f"**{alert}**: {(GATE / alert).read_text()[:800]}"]
    STATUS.write_text("\n".join(L) + "\n")


def cmd_status(a):
    champ = ensure_champion()
    write_status(champ)
    print(STATUS.read_text())
    return 0


def cmd_watch(a):
    """Alert (never revert) when a production pilot differs from the champion without a PASS."""
    champ = ensure_champion()
    want = champ.get("pilot_md5")
    alerts = []
    for host, root, _opt in deploy_targets():
        if host == "local":
            p = Path(root) / "v15_pilot.py"
            got = md5(p) if p.exists() else None
        else:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, f"md5sum {root}/v15_pilot.py"], capture_output=True, text=True)
            got = (r.stdout.split() or [None])[0]
        if got and got != want:
            alerts.append({"host": host, "root": str(root), "md5": got, "champion": want})
    if alerts:
        (GATE / "ALERT_UNGATED_PILOT.json").write_text(json.dumps({"at": now(), "alerts": alerts, "fix": "submit the change: python3 ~/v15_bench/tools/v15_version_gate.py submit --files <path>=v15_pilot.py"}, indent=1))
        print(json.dumps(alerts, indent=1))
    else:
        (GATE / "ALERT_UNGATED_PILOT.json").unlink(missing_ok=True)
    # 2026-10-16: the launch env is part of the version. Scheduler pace overrides (INERT 1/100, V17 forced on, FOCUS cuts)
    # and the daily V17 autotune changed what every board computes without ever being benched.
    want_env = champ_env(champ)
    drift = []
    for host in sorted({h for h, _r, _o in deploy_targets()}):
        for e in running_pilot_envs(host):
            d = env_diff(want_env, e["env"])
            if d:
                drift.append({"host": host, "pid": e["pid"], "diff": d})
    if drift:
        keys = sorted({k for x in drift for k in x["diff"]})
        (GATE / "ALERT_UNGATED_ENV.json").write_text(json.dumps({"at": now(), "pilots": len(drift), "keys": keys, "sample": drift[:3], "fix": "bench the running env: python3 ~/v15_bench/tools/v15_version_gate.py submit --env-from-production 10.0.0.3 --note '...'"}, indent=1))
        print(f"[gate] ENV DRIFT: {len(drift)} production pilots run a launch env the gate never benched: {keys}", flush=True)
    else:
        (GATE / "ALERT_UNGATED_ENV.json").unlink(missing_ok=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--files", nargs="*", help="SRC[=REL] (REL defaults to SRC's path relative to ~/binance-sandbox)")
    s.add_argument("--set", action="append", help="gated switch default K=V (e.g. V15_BRAIN=1)")
    s.add_argument("--note")
    s.add_argument("--env", action="append", help="launch-env change K=V benched against the champion env (K= removes K)")
    s.add_argument("--env-from-production", metavar="HOST", help="candidate env = the env a running production pilot on HOST (local or 10.0.0.3) really has")
    s.add_argument("--allow-identical", action="store_true", help="champion-vs-champion run (noise calibration)")
    s.add_argument("--nn-baseline-bundle", help="independently pinned 32-artifact bundle to use for the champion arm of a model comparison")
    s.add_argument("--nn-comparison-spec", help="v15_nn_comparison/1 spec pinning the exact reference and candidate model declarations")
    s.add_argument("--priority", type=int, default=0, help="higher benches first (user-ordered comparisons)")
    sub.add_parser("run")
    rd = sub.add_parser("redecide")
    rd.add_argument("--id", required=True, help="candidate id whose bench artifacts are already on disk")
    rd.add_argument("--dry-run", action="store_true", help="print the verdict without deploying or recording it")
    sub.add_parser("status")
    sub.add_parser("watch")
    sub.add_parser("publish-env")
    a = ap.parse_args(argv)
    return {"submit": cmd_submit, "run": cmd_run, "redecide": cmd_redecide, "status": cmd_status, "watch": cmd_watch, "publish-env": cmd_publish_env}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
