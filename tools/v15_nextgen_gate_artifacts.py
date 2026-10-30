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
    return result

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
    return {"evidence_type": "actual resolver evaluated under supplied launch environment; not a runtime read trace", "planner_sha256": hashlib.sha256(source).hexdigest(), "models": resolved}
