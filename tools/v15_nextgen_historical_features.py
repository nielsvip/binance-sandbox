"""Explicit serving, intrinsic context-free, and fail-closed historical-quality feature modes."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from tools import v18_features as features

MODES = ("serving", "context_free", "historical_quality")


def mode(value=None):
    value = value or os.environ.get("V18_FEATURE_MODE", "serving")
    if value not in MODES:
        raise ValueError(f"unknown feature mode {value!r}")
    return value


def _epoch(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("as-of timestamps require timezone provenance")
    return result.astimezone(timezone.utc).timestamp()


def frozen_index(cat_side, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("schema") != "v18_asof_templates_v1":
        raise ValueError("unsupported pre-cut template/default manifest")
    entry = (manifest.get("templates") or {}).get(cat_side) or {}
    payload = Path(entry["index_path"]).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != entry.get("index_sha256"):
        raise ValueError("pre-cut template index hash mismatch")
    index = json.loads(payload)
    defaults_digest = hashlib.sha256(json.dumps(index.get("defaults") or {}, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if defaults_digest != entry.get("defaults_sha256") or index.get("cat_side") != cat_side:
        raise ValueError("pre-cut defaults hash/category mismatch")
    if _epoch(entry["recorded_at"]) >= _epoch(manifest["holdout_start"]):
        raise ValueError("template/default snapshot is not before the holdout")
    return index, dict(entry, verified_index_sha256=digest, holdout_start=manifest["holdout_start"])


class IsolatedContext(features.Context):
    def default(self, name, is_filter=False):
        return None

    def span(self, name, is_filter=False):
        if self.feature_mode == "historical_quality" and self.asof_verified:
            return super().span(name, is_filter)
        return []


def adapt(context, feature_mode=None, manifest_path=None):
    selected = mode(feature_mode)
    if selected == "serving":
        return context
    index = {"tabs": {}, "switches": {}, "defaults": {}}
    provenance = None
    if selected == "historical_quality":
        path = manifest_path or os.environ.get("V18_ASOF_TEMPLATE_MANIFEST")
        if path:
            index, provenance = frozen_index(context.cs, path)
    result = IsolatedContext(context.cs, tidx=index, bible={}, deps={}, tfsem={}, fdict={})
    result.feature_mode = selected
    result.asof_verified = provenance is not None
    result.asof_provenance = provenance
    return result


def inforce(context, value, source):
    selected = getattr(context, "feature_mode", "serving")
    if selected == "context_free" or (selected == "historical_quality" and source != "V"):
        return None, "U"
    return value, source


class AbsoluteEncoder(features.ContextEncoder):
    def differs(self, key, value):
        return True

    def diff(self, full):
        if getattr(self.fctx, "feature_mode", None) == "context_free":
            return {}
        result = {}
        for key, value in (full or {}).items():
            if key in features.CTX_SKIP_KEYS or key.startswith(features.CTX_SKIP_PREFIX):
                continue
            canonical = features.canon_value(value)
            if canonical == "<absent>":
                continue
            if not features.plausible_value(canonical):
                self.ignored_values[key] += 1
                continue
            result[key] = canonical
        return result


def context_encoder(context):
    return AbsoluteEncoder(context) if getattr(context, "feature_mode", "serving") != "serving" else features.ContextEncoder(context)


def require_quality_training(context, dataset_meta, dataset):
    selected = getattr(context, "feature_mode", "serving")
    if dataset_meta.get("feature_mode", "serving") != selected:
        raise ValueError("dataset/model feature modes differ; rebuild matched artifacts")
    if selected != "historical_quality":
        return
    if not getattr(context, "asof_verified", False):
        raise ValueError("historical quality requires immutable pre-cut template/default snapshots")
    required = {"pre_eval_verified", "market_data_start", "market_data_end"}
    if not required <= set(dataset):
        raise ValueError("historical quality requires verified pre-eval values and NPZ market-window lineage; current corpus remains diagnostic")
    if not dataset_meta.get("market_lineage_source_sha256"):
        raise ValueError("historical quality requires a hashed market-lineage source")
