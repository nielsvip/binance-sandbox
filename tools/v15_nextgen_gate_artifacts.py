import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
import types
from pathlib import Path

ARTIFACT = re.compile(r"^data/(brain/v18_brain_[A-Za-z0-9_]+|learn/digest_[A-Za-z0-9_]+)\.json$")

def _plain_path(root, path):
    root = Path(root).absolute()
    path = Path(path).absolute()
    path.relative_to(root)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink forbidden for gate artifact: {parent}")
        if parent == root:
            break

def inventory(root):
    root = Path(root).absolute()
    result = {}
    for directory, pattern in (("brain", "v18_brain_*.json"), ("learn", "digest_*.json")):
        parent = root / "data" / directory
        _plain_path(root, parent)
        if not parent.exists():
            continue
        for path in sorted(parent.glob(pattern)):
            relative = path.relative_to(root).as_posix()
            if not ARTIFACT.fullmatch(relative):
                continue
            _plain_path(root, path)
            payload = path.read_bytes()
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError(f"gate model artifact must be a JSON object: {relative}")
            result[relative] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    declaration = root / "tools/v18_model_bundle.json"
    if declaration.is_file():
        value = json.loads(declaration.read_bytes())
        if value.get("linknet_activation", {}).get("schema") == "compiled_linknet_first/1":
            for name, expected in value.get("expected_sha256_by_filename", {}).items():
                relative = declared_relative(name)
                path = root / relative
                _plain_path(root, path)
                payload = path.read_bytes()
                sha = hashlib.sha256(payload).hexdigest()
                if sha != expected:
                    raise ValueError(f"declared artifact mismatch: {relative}")
                result[relative] = {"sha256": sha, "bytes": len(payload)}
    return result

def declared_relative(name):
    if not re.fullmatch(r"(?:v18_brain|digest|needed_filters|switch_profile|positives_all|binding_map|linknet_proposals|must_pos)_(?:CRYPTO_LONG|CRYPTO_SHORT|STOCKS_LONG|STOCKS_SHORT)\.json(?:\.gz)?", name):
        raise ValueError(f"undeclared artifact kind: {name}")
    return "data/" + ("brain/" if name.startswith("v18_brain_") else "learn/") + name

def overlay(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    before = inventory(source)
    for relative, expected in before.items():
        origin, target = source / relative, destination / relative
        _plain_path(destination, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".gate_artifact_", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(origin, temporary)
            copied = Path(temporary).read_bytes()
            if hashlib.sha256(copied).hexdigest() != expected["sha256"]:
                raise ValueError(f"gate artifact changed while copying: {relative}")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if inventory(source) != before:
        raise ValueError("gate artifact source changed during overlay")
    after = inventory(destination)
    if any(after.get(relative) != expected for relative, expected in before.items()):
        raise ValueError("gate artifact destination verification failed")
    return before

def resolve_models(root, env, expected=None):
    root = Path(root).absolute()
    planner = root / "tools/v15_v18_planner.py"
    _plain_path(root, planner)
    source = planner.read_bytes()
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "learn_file"]
    if len(functions) != 1:
        raise ValueError("runtime planner must define exactly one learn_file resolver")
    namespace = {"Path": Path, "os": types.SimpleNamespace(environ=dict(env)), "__file__": str(planner)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(planner), "exec"), namespace)
    resolved = {}
    for category in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        for sub, prefix in (("brain", "v18_brain"), ("learn", "digest")):
            name = f"{prefix}_{category}.json"
            path = Path(namespace["learn_file"](name, root, sub))
            if not path.is_absolute():
                path = root / path
            path = Path(os.path.abspath(path))
            _plain_path(root, path)
            payload = path.read_bytes()
            if not isinstance(json.loads(payload), dict):
                raise ValueError(f"resolved model must be a JSON object: {name}")
            digest = hashlib.sha256(payload).hexdigest()
            if expected is not None and expected.get(name) != digest:
                raise ValueError(f"resolved model differs from intended bundle: {name}")
            resolved[name] = {"path": path.relative_to(root).as_posix(), "sha256": digest, "bytes": len(payload)}
    for name, expected_sha in (expected or {}).items():
        if name in resolved:
            continue
        path = Path(namespace["learn_file"](name, root, "learn"))
        if not path.is_absolute():
            path = root / path
        _plain_path(root, path)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError(f"resolved ancillary differs from intended bundle: {name}")
        resolved[name] = {"path": path.relative_to(root).as_posix(), "sha256": expected_sha, "bytes": len(payload)}
    return {"evidence_type": "actual resolver evaluated under supplied launch environment; not a runtime read trace", "planner_sha256": hashlib.sha256(source).hexdigest(), "models": resolved}

def verify_declared_bundle(root, env, required=False):
    root = Path(root).absolute()
    manifest = root / "tools/v18_model_bundle.json"
    _plain_path(root, manifest)
    if not manifest.exists():
        if required or env.get("V15_REQUIRE_MODEL_BUNDLE") == "1":
            raise ValueError("neural-network comparison requires a declared model bundle")
        return None
    payload = manifest.read_bytes()
    declaration = json.loads(payload)
    if declaration.get("schema") != "v15_nn_bundle/1":
        raise ValueError("unknown neural-network bundle schema")
    expected = declaration.get("expected_sha256_by_filename")
    names = {f"{prefix}_{category}.json" for prefix in ("v18_brain", "digest") for category in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    expanded = declaration.get("linknet_activation", {}).get("schema") == "compiled_linknet_first/1"
    if expanded:
        names = {f"{prefix}_{category}.json{'.gz' if prefix == 'positives_all' else ''}" for prefix in ("v18_brain", "digest", "needed_filters", "switch_profile", "positives_all", "binding_map", "linknet_proposals", "must_pos") for category in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    if not isinstance(expected, dict) or set(expected) != names or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected.values()):
        raise ValueError("neural-network bundle requires exact hashes for all declared32 runtime artifacts" if expanded else "neural-network bundle requires exact hashes for all eight runtime artifacts")
    if expanded:
        prior_inactive = env.get("V15_NN_REFERENCE_LEGACY_POLICY") == "1"
        if prior_inactive:
            categories = declaration.get("linknet_activation", {}).get("by_category", {})
            if env.get("V18_LINKNET_FIRST") != "0" or env.get("V18_LINKNET_BUNDLE_REQUIRED") != "0" or len(categories) != 4 or any(entry.get("production_scorer") != "legacy_compiled_output" for entry in categories.values()):
                raise ValueError("prior inactive-policy artifact guard requires exact legacy declarations and consumer off")
        else:
            for key, value in declaration.get("required_runtime_flags", {}).items():
                if env.get(key) != str(value):
                    raise ValueError(f"required neural-network activation flag differs: {key}")
            if env.get("V18_LINKNET_FIRST") != "1" or env.get("V18_LINKNET_BUNDLE_REQUIRED") != "1":
                raise ValueError("compiled neural-network activation guards required")
        for name, expected_sha in expected.items():
            path = root / declared_relative(name)
            _plain_path(root, path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
                raise ValueError(f"declared ancillary artifact differs: {name}")
    receipt = resolve_models(root, env, expected)
    receipt["bundle_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    receipt["bundle_id"] = declaration.get("bundle_id")
    return receipt
