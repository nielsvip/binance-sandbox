"""Thin shim over data/knob_registry.json for build_matrix_interdependency_manual.

The JSON file is the canonical registry; this module exposes the small API
that build_matrix_interdependency_manual.registry_rows() expects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "knob_registry.json"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = json.loads(REGISTRY_PATH.read_text())
    return _cache


def config_knobs(mode: str) -> dict[str, Any]:
    data = _load()
    bucket = data.get(mode, {})
    return {k: v.get("default") for k, v in bucket.items()}


def per_sym_map(mode: str) -> dict[str, bool]:
    data = _load()
    bucket = data.get(mode, {})
    return {k: bool(v.get("per_sym")) for k, v in bucket.items()}


def family_of(name: str) -> str:
    data = _load()
    for bucket in data.values():
        if name in bucket:
            return str(bucket[name].get("family") or name)
    return name


def group_of(name: str) -> str:
    data = _load()
    for bucket in data.values():
        if name in bucket:
            return str(bucket[name].get("group") or "OTHER")
    return "OTHER"


def role_of(name: str, value: Any) -> str:
    data = _load()
    for bucket in data.values():
        if name in bucket:
            return str(bucket[name].get("role") or "SUB_SETTING")
    return "SUB_SETTING"


def off_value(name: str, value: Any) -> Any:
    data = _load()
    for bucket in data.values():
        if name in bucket:
            return bucket[name].get("off_value")
    return None
