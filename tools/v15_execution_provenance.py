"""Capture the effective QuickConfig and exact NPZ arrays at the engine call boundary."""

from __future__ import annotations

import base64
import dataclasses
import datetime
import enum
import hashlib
import importlib
import json
import math
import marshal
import os
import tempfile
import threading
import time
import zlib
from pathlib import Path

_HOOK_LOCK = threading.RLock()
_HOOK_LOCAL = threading.local()
_SECRET_PARTS = (
    "APIKEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "PRIVATEKEY",
    "ACCESSKEY",
)


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _identity_body(provenance):
    return {
        key: value
        for key, value in provenance.items()
        if key
        not in (
            "provenance_sha256",
            "capture_ms",
            "capture_components_ms",
            "config",
            "npz_manifest",
        )
    }


def _blob_reference(group, digest):
    return f"{group}/{digest[:2]}/{digest}.json.zlib"


def _write_blob(root, group, digest, value):
    relative = _blob_reference(group, digest)
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_bytes(value)
    compressed = zlib.compress(raw, level=6)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if zlib.decompress(path.read_bytes()) != raw:
                raise ValueError(f"immutable {group} provenance collision")
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    return relative, len(raw), len(compressed)


def _read_blob(root, group, digest, relative):
    if (
        len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or relative != _blob_reference(group, digest)
    ):
        raise ValueError(f"invalid {group} provenance reference")
    value = json.loads(zlib.decompress((Path(root) / relative).read_bytes()))
    return value


def _redact_path(path, key, under_accounts):
    normalized = "".join(char for char in str(key).upper() if char.isalnum())
    return under_accounts or any(part in normalized for part in _SECRET_PARTS)


def encode_value(value, path="", redactions=None, under_accounts=False):
    """Encode supported Python values with type tags; unknown values fail closed."""
    redactions = [] if redactions is None else redactions
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return {"__type__": "float", "value": value}
        return {
            "__type__": "float",
            "value": "nan" if math.isnan(value) else ("+inf" if value > 0 else "-inf"),
        }
    if isinstance(value, Path):
        return {
            "__type__": "path",
            "class": type(value).__name__,
            "value": value.as_posix(),
        }
    if isinstance(value, datetime.timedelta):
        return {
            "__type__": "timedelta",
            "days": value.days,
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, enum.Enum):
        return {
            "__type__": "enum",
            "class": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": encode_value(value.value, path, redactions, under_accounts),
        }
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return {
                "__type__": "numpy_scalar",
                "dtype": value.dtype.str,
                "value": encode_value(value.item(), path, redactions, under_accounts),
            }
        if isinstance(value, np.ndarray):
            return {
                "__type__": "ndarray",
                "dtype": value.dtype.str,
                "shape": list(value.shape),
                "values": encode_value(
                    value.tolist(), path, redactions, under_accounts
                ),
            }
    except ImportError:
        pass
    if isinstance(value, dict):
        pairs = []
        if all(isinstance(key, str) for key in value):
            keys = sorted(value)
        else:
            keys = sorted(value, key=lambda item: canonical_bytes(encode_value(item)))
        for key in keys:
            child_path = f"{path}.{key}" if path else str(key)
            child_value = value[key]
            redact = _redact_path(path, key, under_accounts)
            if under_accounts and isinstance(child_value, (dict, list, tuple)):
                redact = False
            if redact:
                redactions.append(
                    {"path": child_path, "value_type": _type_name(value[key])}
                )
                encoded = {"__type__": "redacted", "value_type": _type_name(value[key])}
            else:
                encoded = encode_value(
                    child_value,
                    child_path,
                    redactions,
                    under_accounts or str(key).upper() == "ACCOUNTS",
                )
            pairs.append([encode_value(key), encoded])
        return {"__type__": "dict", "items": pairs}
    if isinstance(value, tuple):
        return {
            "__type__": "tuple",
            "items": [
                encode_value(item, f"{path}[{index}]", redactions, under_accounts)
                for index, item in enumerate(value)
            ],
        }
    if isinstance(value, list):
        return {
            "__type__": "list",
            "items": [
                encode_value(item, f"{path}[{index}]", redactions, under_accounts)
                for index, item in enumerate(value)
            ],
        }
    if isinstance(value, (set, frozenset)):
        items = [encode_value(item, path, redactions, under_accounts) for item in value]
        items.sort(key=canonical_bytes)
        return {
            "__type__": "frozenset" if isinstance(value, frozenset) else "set",
            "items": items,
        }
    raise TypeError(
        f"unsupported value type at {path or '<root>'}: {_type_name(value)}"
    )


def _type_name(value):
    return f"{type(value).__module__}.{type(value).__qualname__}"


def decode_value(value):
    if not isinstance(value, dict) or "__type__" not in value:
        return value
    kind = value["__type__"]
    if kind == "float":
        number = value["value"]
        return (
            {"nan": float("nan"), "+inf": float("inf"), "-inf": float("-inf")}.get(
                number, float(number)
            )
            if isinstance(number, str)
            else float(number)
        )
    if kind == "path":
        cls = Path if value.get("class") == "PosixPath" else Path
        return cls(value["value"])
    if kind == "timedelta":
        return datetime.timedelta(
            days=value["days"],
            seconds=value["seconds"],
            microseconds=value["microseconds"],
        )
    if kind == "bytes":
        return base64.b64decode(value["base64"])
    if kind == "tuple":
        return tuple(decode_value(item) for item in value["items"])
    if kind == "list":
        return [decode_value(item) for item in value["items"]]
    if kind == "set":
        return set(decode_value(item) for item in value["items"])
    if kind == "frozenset":
        return frozenset(decode_value(item) for item in value["items"])
    if kind == "dict":
        return {decode_value(key): decode_value(item) for key, item in value["items"]}
    if kind == "numpy_scalar":
        import numpy as np

        return np.dtype(value["dtype"]).type(decode_value(value["value"]))
    if kind == "ndarray":
        import numpy as np

        return np.asarray(
            decode_value(value["values"]), dtype=np.dtype(value["dtype"])
        ).reshape(value["shape"])
    if kind == "redacted":
        value_type = str(value.get("value_type") or "")
        if value_type.endswith(".str"):
            return ""
        if value_type.endswith(".bool"):
            return False
        if value_type.endswith(".int"):
            return 0
        if value_type.endswith(".float"):
            return 0.0
        if value_type.endswith(".bytes"):
            return b""
        return None
    if kind == "enum":
        module_name, _, class_name = value["class"].rpartition(".")
        cls = getattr(importlib.import_module(module_name), class_name)
        return cls(decode_value(value["value"]))
    raise ValueError(f"unknown encoded type: {kind}")


def config_document(cfg):
    if not dataclasses.is_dataclass(cfg):
        raise TypeError("engine cfg is not a dataclass")
    fields = vars(cfg)
    redactions = []
    encoded = encode_value(fields, redactions=redactions)
    return {
        "class": _type_name(cfg),
        "values": encoded,
        "redactions": sorted(redactions, key=lambda row: row["path"]),
        "replay_note": "redacted credential fields restore as placeholders and require secret-free runtime injection",
    }


def restore_config(document, config_type):
    if document.get("class") != f"{config_type.__module__}.{config_type.__qualname__}":
        raise ValueError("QuickConfig type does not match provenance")
    values = decode_value(document["values"])
    if not isinstance(values, dict):
        raise ValueError("config provenance values are not a mapping")
    cfg = config_type()
    for key, value in values.items():
        setattr(cfg, key, value)
    return cfg


def _array_digest(array):
    import numpy as np

    dtype = array.dtype.str
    shape = list(array.shape)
    if array.dtype.hasobject:
        payload = canonical_bytes(encode_value(array.tolist()))
        content_hash = hashlib.sha256(payload).digest()
    else:
        contiguous = np.ascontiguousarray(array)
        content_hash = hashlib.sha256(memoryview(contiguous).cast("B")).digest()
    return {
        "dtype": dtype,
        "shape": shape,
        "content_sha256_b64": base64.b64encode(content_hash).decode("ascii"),
    }


def npz_manifest(npz):
    if not isinstance(npz, dict):
        raise TypeError("engine NPZ input is not a dict")
    import numpy as np

    arrays = {}
    non_arrays = {}
    for key in sorted(npz):
        value = npz[key]
        if isinstance(value, np.ndarray):
            arrays[str(key)] = _array_digest(value)
            continue
        non_arrays[str(key)] = encode_value(value, str(key))
    manifest = {
        "hash_algorithm": "sha256",
        "hash_encoding": "base64",
        "arrays": arrays,
        "non_arrays": non_arrays,
    }
    manifest["sha256"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    return manifest


def _capture_call(args, kwargs, module, decision_context=None, loaded_callable=None):
    if args:
        npz = args[0]
        symbol = args[1] if len(args) > 1 else kwargs.get("sym")
        is_long = args[2] if len(args) > 2 else kwargs.get("is_long")
        cfg = args[3] if len(args) > 3 else kwargs.get("cfg")
        force = args[4] if len(args) > 4 else kwargs.get("force_initial_seed", False)
    else:
        npz = kwargs.get("npz")
        symbol = kwargs.get("sym")
        is_long = kwargs.get("is_long")
        cfg = kwargs.get("cfg")
        force = kwargs.get("force_initial_seed", False)
    if cfg is None or npz is None:
        raise ValueError("simulate_one call did not expose cfg and npz")
    started = time.perf_counter()
    config_started = time.perf_counter()
    config = config_document(cfg)
    config_ms = (time.perf_counter() - config_started) * 1000.0
    npz_started = time.perf_counter()
    manifest = npz_manifest(npz)
    npz_ms = (time.perf_counter() - npz_started) * 1000.0
    engine_file = getattr(module, "__file__", None)
    engine_disk_hash = (
        hashlib.sha256(Path(engine_file).read_bytes()).hexdigest()
        if engine_file and Path(engine_file).is_file()
        else None
    )
    loaded_code = getattr(loaded_callable, "__code__", None)
    loaded_entrypoint_hash = (
        hashlib.sha256(marshal.dumps(loaded_code)).hexdigest()
        if loaded_code is not None
        else None
    )
    timestamps = npz.get("timestamps") if isinstance(npz, dict) else None
    bar_ts = int(timestamps[-1]) if timestamps is not None and len(timestamps) else None
    body = {
        "schema_version": 1,
        "capture_status": "VERIFIED",
        "config": config,
        "effective_config_sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "config_sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "config_path": _blob_reference(
            "configs", hashlib.sha256(canonical_bytes(config)).hexdigest()
        ),
        "npz_manifest": manifest,
        "npz_manifest_sha256": manifest["sha256"],
        "npz_manifest_path": _blob_reference("npz_manifests", manifest["sha256"]),
        "engine_disk_source_path": (
            str(Path(engine_file).resolve()) if engine_file else None
        ),
        "engine_disk_source_sha256": engine_disk_hash,
        "engine_loaded_entrypoint_sha256": loaded_entrypoint_hash,
        "engine_loaded_entrypoint_scope": "simulate_one Python code object",
        "engine_module": getattr(module, "__name__", None),
        "invocation": {
            "symbol": symbol,
            "is_long": is_long,
            "force_initial_seed": force,
            "bar_ts": bar_ts,
        },
        "decision_context": decision_context or {},
        "capture_components_ms": {
            "config": round(config_ms, 3),
            "npz_manifest": round(npz_ms, 3),
        },
    }
    body["capture_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    body["provenance_sha256"] = hashlib.sha256(
        canonical_bytes(_identity_body(body))
    ).hexdigest()
    return body


class EngineInvocationCapture:
    def __init__(self, module=None, decision_context=None):
        self.module = module
        self.decision_context = decision_context or {}
        self.invocations = []
        self.errors = []
        self.elapsed_ms = 0.0
        self._previous = None
        self._wrapper = None
        self._prior_local = None

    def __enter__(self):
        if self.module is None:
            self.module = importlib.import_module("v12_quick_engine")
        _HOOK_LOCK.acquire()
        self._previous = self.module.simulate_one
        owner = self

        def wrapped(*args, **kwargs):
            active = getattr(_HOOK_LOCAL, "active", None)
            if active is owner:
                try:
                    call_args = list(args)
                    call_kwargs = dict(kwargs)
                    npz = call_args[0] if call_args else call_kwargs.get("npz")
                    symbol = (
                        call_args[1] if len(call_args) > 1 else call_kwargs.get("sym")
                    )
                    is_long = (
                        call_args[2]
                        if len(call_args) > 2
                        else call_kwargs.get("is_long")
                    )
                    cfg = call_args[3] if len(call_args) > 3 else call_kwargs.get("cfg")
                    resolver = getattr(owner.module, "ensure_template_layer", None)
                    if callable(resolver) and cfg is not None:
                        cfg = resolver(cfg, symbol, is_long)
                        if len(call_args) > 3:
                            call_args[3] = cfg
                        else:
                            call_kwargs["cfg"] = cfg
                    if isinstance(getattr(cfg, "_FX_PROBE_SINK", None), dict):
                        return owner._previous(*call_args, **call_kwargs)
                    capture = _capture_call(
                        tuple(call_args),
                        call_kwargs,
                        owner.module,
                        owner.decision_context,
                        owner._previous,
                    )
                    owner.invocations.append(capture)
                    owner.elapsed_ms += capture["capture_ms"]
                except Exception as exc:
                    owner.errors.append(f"{type(exc).__name__}: {str(exc)[:200]}")
                    call_args, call_kwargs = list(args), dict(kwargs)
                else:
                    pass
            else:
                call_args, call_kwargs = list(args), dict(kwargs)
            return owner._previous(*call_args, **call_kwargs)

        self._wrapper = wrapped
        self._prior_local = getattr(_HOOK_LOCAL, "active", None)
        _HOOK_LOCAL.active = self
        self.module.simulate_one = wrapped
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            _HOOK_LOCAL.active = self._prior_local
            if self.module.simulate_one is self._wrapper:
                self.module.simulate_one = self._previous
        finally:
            _HOOK_LOCK.release()
        return False

    def result(self):
        if self.errors:
            return {
                "schema_version": 1,
                "capture_status": "UNVERIFIED",
                "capture_errors": list(self.errors),
                "capture_ms": round(self.elapsed_ms, 3),
            }
        if len(self.invocations) != 1:
            return {
                "schema_version": 1,
                "capture_status": "UNVERIFIED",
                "capture_errors": [
                    f"expected one engine call, observed {len(self.invocations)}"
                ],
                "capture_ms": round(self.elapsed_ms, 3),
            }
        return self.invocations[0]


def capture_engine_invocation(module=None, decision_context=None):
    return EngineInvocationCapture(module, decision_context)


def persist_provenance(root, provenance):
    if (
        not isinstance(provenance, dict)
        or provenance.get("capture_status") != "VERIFIED"
    ):
        raise ValueError("only verified execution provenance is immutable-persistable")
    body = _identity_body(provenance)
    expected = hashlib.sha256(canonical_bytes(body)).hexdigest()
    if provenance.get("provenance_sha256") != expected:
        raise ValueError("execution provenance hash mismatch")
    digest = str(provenance["provenance_sha256"])
    stored = {**body, "provenance_sha256": digest}
    relative = Path("provenance") / digest[:2] / f"{digest}.json.zlib"
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    config = provenance.get("config")
    manifest = provenance.get("npz_manifest")
    if not isinstance(config, dict) or not isinstance(manifest, dict):
        raise ValueError("execution provenance is missing config or NPZ manifest")
    config_digest = hashlib.sha256(canonical_bytes(config)).hexdigest()
    manifest_digest = hashlib.sha256(
        canonical_bytes(
            {key: value for key, value in manifest.items() if key != "sha256"}
        )
    ).hexdigest()
    if config_digest != provenance.get(
        "config_sha256"
    ) or manifest_digest != provenance.get("npz_manifest_sha256"):
        raise ValueError("config or NPZ manifest hash mismatch")
    config_path, config_raw_size, config_stored_size = _write_blob(
        root, "configs", config_digest, config
    )
    manifest_path, manifest_raw_size, manifest_stored_size = _write_blob(
        root, "npz_manifests", manifest_digest, manifest
    )
    if config_path != provenance.get("config_path") or manifest_path != provenance.get(
        "npz_manifest_path"
    ):
        raise ValueError("config or NPZ manifest path mismatch")
    raw_payload = canonical_bytes(stored)
    payload = zlib.compress(raw_payload, level=6)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            try:
                existing = zlib.decompress(path.read_bytes())
            except zlib.error as exc:
                raise ValueError(
                    "immutable execution provenance is not valid zlib"
                ) from exc
            if existing != raw_payload:
                raise ValueError("immutable execution provenance collision")
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    total_stored = len(payload) + config_stored_size + manifest_stored_size
    total_raw = len(raw_payload) + config_raw_size + manifest_raw_size
    return {
        "provenance_sha256": digest,
        "provenance_path": str(relative),
        "capture_status": "VERIFIED",
        "compression": "zlib",
        "stored_bytes": total_stored,
        "raw_bytes": total_raw,
        "config_sha256": config_digest,
        "config_path": config_path,
        "npz_manifest_sha256": manifest_digest,
        "npz_manifest_path": manifest_path,
        "capture_ms": provenance.get("capture_ms"),
        "capture_components_ms": provenance.get("capture_components_ms"),
    }


def load_provenance(root, reference):
    digest = str(reference.get("provenance_sha256") or "")
    relative = str(reference.get("provenance_path") or "")
    expected = f"provenance/{digest[:2]}/{digest}.json.zlib"
    if (
        reference.get("compression") != "zlib"
        or relative != expected
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("invalid execution provenance reference")
    payload = zlib.decompress((Path(root) / relative).read_bytes())
    provenance = json.loads(payload)
    if (
        provenance.get("provenance_sha256") != digest
        or hashlib.sha256(canonical_bytes(_identity_body(provenance))).hexdigest()
        != digest
    ):
        raise ValueError("execution provenance content hash mismatch")
    for key in (
        "config_sha256",
        "config_path",
        "npz_manifest_sha256",
        "npz_manifest_path",
    ):
        if provenance.get(key) != reference.get(key):
            raise ValueError("execution provenance blob reference mismatch")
    config = _read_blob(
        root,
        "configs",
        str(reference.get("config_sha256") or ""),
        str(reference.get("config_path") or ""),
    )
    manifest = _read_blob(
        root,
        "npz_manifests",
        str(reference.get("npz_manifest_sha256") or ""),
        str(reference.get("npz_manifest_path") or ""),
    )
    if hashlib.sha256(canonical_bytes(config)).hexdigest() != reference.get(
        "config_sha256"
    ):
        raise ValueError("effective config content hash mismatch")
    manifest_digest = hashlib.sha256(
        canonical_bytes(
            {key: value for key, value in manifest.items() if key != "sha256"}
        )
    ).hexdigest()
    if manifest.get("sha256") != reference.get(
        "npz_manifest_sha256"
    ) or manifest_digest != reference.get("npz_manifest_sha256"):
        raise ValueError("NPZ manifest content hash mismatch")
    return {**provenance, "config": config, "npz_manifest": manifest}
