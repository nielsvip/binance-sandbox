#!/usr/bin/env python3
"""Prove that every matrix switch/timeframe changes real BTC execution.

This is the hard gate before a multi-symbol pilot.  It intentionally refuses
three common false proofs:

* a different override/config hash is not an execution difference;
* a declared field with no engine read is not wired;
* a child behind a disabled parent is not tested until the parent is enabled.

The detailed CSV contains one row per tested value/timeframe.  The summary CSV
contains one verdict per switch and is the spreadsheet gate consumed by later
pilot launchers.  Only ``PASS`` is pilot-admissible.
"""
from __future__ import annotations

import argparse
import ast
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHEETS = (
    "ENTRY_SWITCHES_ALL", "FILTERS_F1-F5", "EXITS_ALL",
    "REENTRIES_ALL", "AUGMENTS_REDUCTIONS",
)
TF_ORDER = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "D", "W")
# Canonical crypto decision frames consumed by the live cross/price engines.
# Each non-timeframe switch is exercised under every one of these contexts.
AUDIT_TFS = ("3m", "15m", "1h", "4h", "D")
STATIC_SURFACES = {
    "quick": ("v12_quick_engine.py",),
    "wide": ("v12_wide_engine.py",),
    "scalar": ("backtest_v12_engine.py",),
    "live_crypto": ("ez_manage.py", "ez_positions_quick.py", "ez_reentry.py"),
    "live_stock": ("tradier_manage.py",),
}
STRATEGY_SOURCES = set(SHEETS) | {
    "SWITCH_CATALOG", "QUICK_CONFIG", "DISCONNECTED_LIVE_PATHS",
}
HARNESS_FIELDS = {
    "MODE", "BASE_TF", "HOOKUP_TF", "AUTO_WIRED_PARAMS",
    "VERBOSE", "VERBOSE_STOPS",
}
DEPRECATED_FIELDS = {"EMA_9_21_SCORE_BONUS"}
OPS_TOKENS = re.compile(
    r"(^|_)(API|BROKER|REDIS|TELEGRAM|DISCORD|SLACK|EMAIL|GUI|REPORT|LOG|DEBUG|"
    r"VERBOSE|HEARTBEAT|WATCHDOG|MONITOR|WORKER|THREAD|PROCESS|BATCH|CACHE|"
    r"RETRY|TIMEOUT|POLL|SLEEP|CRON|BACKUP|DATABASE|DB|HOST|PORT|URL|PATH|DIR|"
    r"FILE|LOCK|RATE_LIMIT|QUEUE|WEBHOOK|HTTP|AWS|S3|IP|CONCURRENCY|SEMAPHORE|"
    r"INTERVAL|PLOT|MACBOOK|MULTI_INSTANCE)(_|$)"
)
EXTERNAL_TOKENS = re.compile(
    r"(^|_)(NEWS|SENTIMENT|FUNDING|OPEN_INTEREST|ORDER_BOOK|ACCOUNT|PORTFOLIO|"
    r"SECTOR|CROSS_SYMBOL|TRADEABILITY|UNIVERSE)(_|$)"
)
STRATEGY_TOKENS = re.compile(
    r"(^|_)(ENTRY|EXIT|REENTRY|REENTER|AUGMENT|REDUCE|STOP|TRAIL|HEDGE|WT|DC|"
    r"BB|RSI|MFI|STOCH|EMA|SMA|ATR|VWAP|REGIME|BREAKOUT|BOUNCE|SCALP|PYRAMID|"
    r"NOLOSS|BREAKEVEN|TAKE_PROFIT|PROFIT|GAIN|LOSS|HOLD|SIZING|SIZE|QTY|"
    r"POSITION|COOLDOWN|DIVERGENCE|MOMENTUM|VOL|VOLUME|TREND|RANGE|STRUCT|"
    r"GOLDEN_RULE|GR|RZ|K_ZONE|FORMATION|FIB|DONCHIAN|CLENOW|CONNORS|"
    r"CIRCUIT_BREAKER|DAYTRADE|REVERSAL|RETEST|RISK|DD)(_|$)"
)
METRIC_KEYS = (
    "gain_pct", "delta_vs_bh", "trades", "tim_pct", "max_dd_pct",
    "pool_sharpe", "wr_pct",
)
_WORKER_EVAL_CACHE: dict[str, dict[str, Any]] = {}

FORMATION_FAMILIES = {
    "HEAD_SHOULDERS": "head_shoulders",
    "DOUBLE_TOP_BOTTOM": "double_top_bottom",
    "WEDGE": "wedge",
    "TRIANGLE": "triangle",
    "FLAG_PENNANT": "flag_pennant",
    "CUP_HANDLE": "cup_handle",
    "TREND_STRUCTURE": "trend_structure",
}
FORMATION_ROUTE_TFS = ("15m", "1h", "4h", "D")
FORMATION_GENERIC_FIELDS = {
    "FORMATION_TFS", "FORMATION_MIN_SCORE", "FORMATION_POSITION_SIZE_MULT",
    "FORMATION_EXIT_MIN_GAIN_PCT",
}


def typed_key(value: Any) -> str:
    return f"{type(value).__name__}:{json.dumps(value, sort_keys=True, default=str)}"


def dedup(values: Iterable[Any]) -> list[Any]:
    out, seen = [], set()
    for value in values:
        key = typed_key(value)
        if key not in seen:
            out.append(value)
            seen.add(key)
    return out


def is_tf_param(name: str) -> bool:
    upper = name.upper()
    return (upper.endswith("_TF") or upper.endswith("_TIMEFRAME") or
            "FILTER_TF" in upper or upper in {"KINDERGARTEN_TF", "BASE_TF"})


def tf_values(values: Iterable[Any]) -> list[str]:
    found = {str(value) for value in values if str(value) in TF_ORDER}
    return [tf for tf in TF_ORDER if tf in found]


@dataclass
class Spec:
    name: str
    values: list[Any] = field(default_factory=list)
    groups: set[str] = field(default_factory=set)
    sheets: set[str] = field(default_factory=set)
    tf_contexts: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    row_contexts: list[dict[str, Any]] = field(default_factory=list)
    parent_hints: set[str] = field(default_factory=set)


def classify_scope(spec: Spec) -> tuple[str, str]:
    """Conservatively separate causal knobs from non-ledger inventory names."""
    name = spec.name
    if name in DEPRECATED_FIELDS:
        return "N/A_DEPRECATED", "explicitly retired/dead field; no strategy read site"
    if name.endswith(("_DEFAULT", "_DELTA")) and spec.sheets == {"RECENT_RESULTS_5D"}:
        return "N/A_DEPRECATED", "result-artifact comparison column, not a setting"
    if name in HARNESS_FIELDS:
        return "N/A_NON_LEDGER", "evaluator/harness context, not a strategy decision"
    if name.endswith(("_LOG_ONLY", "_LOG_EVERY_TICK")):
        return "N/A_NON_LEDGER", "shadow/logging-only control"
    if STRATEGY_SOURCES.intersection(spec.sheets):
        return "STRATEGY_CAUSAL", "declared strategy/catalog/live-path setting"
    if OPS_TOKENS.search(name):
        return "N/A_NON_LEDGER", "operations/transport/observability control"
    if EXTERNAL_TOKENS.search(name):
        return "N/A_PER_SYMBOL_NPZ", "external-state or universe control; requires integration evidence"
    if STRATEGY_TOKENS.search(name):
        return "STRATEGY_CAUSAL", "strategy-semantic name observed in a concrete backtest override"
    return "REVIEW", "not yet proven strategy-causal or explicitly non-ledger"


def classify_run_scope(spec: Spec, symside: str) -> tuple[str, str]:
    """Add symbol/venue/available-base-TF applicability to static scope."""
    scope = classify_scope(spec)
    if scope[0] != "STRATEGY_CAUSAL":
        return scope
    name = spec.name
    symbol = symside.rsplit("_", 1)[0].upper()
    crypto = symbol.endswith(("USDT", "USDC", "USD1"))
    if name.startswith("BTC_") and not symbol.startswith("BTC"):
        return "N/A_SYMBOL", "BTC-dedicated strategy path is not applicable to this symbol"
    if "TRADIER" in name and crypto:
        return "N/A_VENUE", "Tradier-only strategy path is not applicable to crypto"
    if "CRYPTO" in name and not crypto:
        return "N/A_VENUE", "crypto-only strategy path is not applicable to Tradier"
    side = symside.rsplit("_", 1)[-1].upper()
    has_long = bool(re.search(r"(^|_)LONG($|_)", name))
    has_short = bool(re.search(r"(^|_)SHORT($|_)", name))
    if side == "LONG" and has_short and not has_long:
        return "N/A_SIDE", "exact SHORT-side field cannot alter a LONG ledger"
    if side == "SHORT" and has_long and not has_short:
        return "N/A_SIDE", "exact LONG-side field cannot alter a SHORT ledger"
    # Do not infer missing data from a timeframe token alone.  N/A_DATA is
    # allowed only for a route whose exact persisted array is known absent.
    known_missing = {
        (True, "EMA_TF_5M_ENABLED"), (True, "WT_TF_5M_ENABLED"),
        (False, "EMA_TF_3M_ENABLED"), (False, "WT_TF_3M_ENABLED"),
    }
    if (crypto, name) in known_missing:
        return "N/A_DATA", "exact opposite-venue native EMA/WT route array is absent"
    return scope


def npz_key_inventory(symside: str) -> tuple[set[str] | None, str]:
    """Read only the NPZ central directory; never materialise indicator arrays."""
    symbol = symside.rsplit("_", 1)[0]
    for prefix in ("backtest_v8", "backtest_v7"):
        path = ROOT / prefix / "indicators" / f"{symbol}.npz"
        if not path.exists():
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                keys = {
                    name[:-4] if name.endswith(".npy") else name
                    for name in archive.namelist()
                }
            return keys, str(path)
        except (OSError, zipfile.BadZipFile):
            return None, str(path)
    return None, ""


def formation_required_groups(name: str, symside: str) -> list[set[str]]:
    """Return exact persisted score-array alternatives for a formation field."""
    if name not in FORMATION_GENERIC_FIELDS and not (
        name.startswith("FORMATION_") and name.endswith(("_ENTRY_ENABLED", "_EXIT_ENABLED"))
    ):
        return []
    side = symside.rsplit("_", 1)[-1].upper()
    actions: tuple[str, ...]
    families: list[str]
    if name.endswith("_ENTRY_ENABLED"):
        actions = ("ENTRY",)
        prefix = name.removeprefix("FORMATION_").removesuffix("_ENTRY_ENABLED")
        family = FORMATION_FAMILIES.get(prefix)
        families = [family] if family else []
    elif name.endswith("_EXIT_ENABLED"):
        actions = ("EXIT",)
        prefix = name.removeprefix("FORMATION_").removesuffix("_EXIT_ENABLED")
        family = FORMATION_FAMILIES.get(prefix)
        families = [family] if family else []
    else:
        actions = ("EXIT",) if name == "FORMATION_EXIT_MIN_GAIN_PCT" else (
            ("ENTRY",) if name == "FORMATION_POSITION_SIZE_MULT" else ("ENTRY", "EXIT")
        )
        families = list(FORMATION_FAMILIES.values())

    groups: list[set[str]] = []
    for action in actions:
        wanted = "bull" if side == "LONG" else "bear"
        if action == "EXIT":
            wanted = "bear" if side == "LONG" else "bull"
        for family in families:
            groups.append({
                f"formation_{family}_{wanted}_score_{tf}"
                for tf in FORMATION_ROUTE_TFS
            })
    return groups


def load_scalp_v3_missing_contracts() -> dict[str, dict[str, set[str]]]:
    """Load exact S1 native-3m missing-array contracts by symbol-side/field."""
    path = ROOT / "data/reports/S1_SCALP_V3_NATIVE_3M_NPZ_AVAILABILITY.csv"
    result: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("status") or "").upper() == "PRESENT":
                    continue
                try:
                    affected = json.loads(row.get("affected_scalp_v3_fields") or "[]")
                except (TypeError, ValueError):
                    affected = []
                for field_name in affected:
                    result[str(row.get("symbol_side") or "")][str(field_name)].add(
                        str(row.get("required_array") or "")
                    )
    except OSError:
        pass
    return {ss: dict(fields) for ss, fields in result.items()}


def required_data_scope(
    name: str, symside: str, npz_keys: set[str] | None,
    scalp_missing: dict[str, dict[str, set[str]]] | None = None,
) -> tuple[str, str] | None:
    """Classify an exact persisted-array blocker before causal execution."""
    groups = formation_required_groups(name, symside)
    if groups and npz_keys is not None:
        if not any(group.issubset(npz_keys) for group in groups):
            missing = sorted(set.intersection(*(set(group) - npz_keys for group in groups)))
            if not missing:
                missing = sorted(set(groups[0]) - npz_keys)
            preview = ",".join(missing[:6])
            suffix = "..." if len(missing) > 6 else ""
            return "N/A_DATA", f"exact persisted formation arrays absent: {preview}{suffix}"
    missing_scalp = (scalp_missing or {}).get(symside, {}).get(name, set())
    if missing_scalp:
        return (
            "N/A_DATA",
            "exact native-3m SCALP_V3 arrays absent: " + ",".join(sorted(missing_scalp)),
        )
    return None


def load_specs() -> dict[str, Spec]:
    from tools.delta_matrix import (
        _expand_values, load_inventory, parse_paired_str, parse_sheet_rows,
    )

    _recent, _s1, sheets = load_inventory()
    specs: dict[str, Spec] = {}
    for sheet_name in SHEETS:
        _headers, rows = parse_sheet_rows(sheets.get(sheet_name, []))
        for row_number, row in enumerate(rows, start=2):
            primary = str(row.get("PARAM", "") or "").strip()
            if not primary:
                continue
            group = str(row.get("ENTRY_SWITCH (group)", row.get("GROUP", "")) or "")
            primary_values = _expand_values(row.get("VALUES", "")) or [True, False]
            paired = parse_paired_str(
                row.get("PAIRED FILTER_TF/MIN_TFS", "") or row.get("PAIRED", "")
            )
            fields = {primary: primary_values, **paired}
            tf_fields = {key: tf_values(values) for key, values in fields.items()
                         if tf_values(values)}
            context = {
                "sheet": sheet_name, "row": row_number, "group": group,
                "primary": primary, "primary_values": primary_values,
                "paired": paired, "tf_fields": tf_fields,
            }
            for name, values in fields.items():
                spec = specs.setdefault(name, Spec(name))
                spec.values = dedup([*spec.values, *values])
                spec.groups.add(group)
                spec.sheets.add(sheet_name)
                spec.row_contexts.append(context)
                for tf_name, tfs in tf_fields.items():
                    spec.tf_contexts[tf_name].update(tfs)
    # The workbook is only the curated optimisation subset.  The engine and
    # recent backtests contain thousands of additional switches, all of which
    # must be accounted for before the pilot gate can pass.
    import v12_quick_engine as V

    def merge(name: str, values: Iterable[Any], group: str, source: str) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", str(name or "")):
            return
        spec = specs.setdefault(name, Spec(name))
        spec.values = dedup([*spec.values, *list(values)])
        spec.groups.add(group)
        spec.sheets.add(source)

    catalog_path = ROOT / "data/reports/switch_lab_catalog_20260729.json"
    try:
        catalog = json.loads(catalog_path.read_text()).get("paths") or []
    except (OSError, ValueError):
        catalog = []
    catalog_defaults: dict[str, Any] = {}
    catalog_grids: dict[str, list[Any]] = {}
    for row in catalog:
        if not isinstance(row, dict) or not row.get("param"):
            continue
        name = str(row["param"])
        values = row.get("test_values") if isinstance(row.get("test_values"), list) else []
        if "default" in row:
            catalog_defaults[name] = row["default"]
        # Catalog grids are the authoritative test domain. Historical result
        # artifacts may contain optimizer-local sensitivity points (for
        # example 58.1 beside an authoritative RSI default of 58); those are
        # evidence of a past run, not permission to expand the current gate.
        if values:
            catalog_grids[name] = dedup([
                *([row["default"]] if "default" in row else []), *values,
            ])
        merge(name, values, str(row.get("group") or "CATALOG"), "SWITCH_CATALOG")
        spec = specs.get(name)
        if spec is not None:
            for parent in row.get("activation_dependencies") or []:
                if isinstance(parent, str) and parent:
                    spec.parent_hints.add(parent)
            main_switch = str(row.get("main_switch") or "")
            if main_switch and main_switch != name:
                spec.parent_hints.add(main_switch)

    static_defaults: dict[str, Any] = dict(catalog_defaults)
    for filename in ("config.py", "config_tradier.py", "v12_quick_engine.py"):
        path = ROOT / filename
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            name = None
            value_node = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value_node = node.target.id, node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value_node = node.targets[0].id, node.value
            if not name or value_node is None:
                continue
            try:
                static_defaults.setdefault(name, ast.literal_eval(value_node))
            except (ValueError, TypeError):
                pass

    # The disconnected-live-path ledger is part of the strategy inventory,
    # not a result file.  Preserve its declared type/default and lifecycle
    # stage so every loose switch remains visible until it is causally modeled.
    disconnected_path = ROOT / "SPREADSHEETS/SWITCHES_NOT_CONNECTED.csv"
    try:
        with disconnected_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                name = str(row.get("switch") or "").strip().upper()
                raw = row.get("default")
                declared_type = str(row.get("type") or "").strip().lower()
                try:
                    default = ast.literal_eval(str(raw))
                except (ValueError, SyntaxError):
                    default = raw
                if declared_type == "bool" and isinstance(default, str):
                    default = default.strip().lower() == "true"
                merge(name, [default], str(row.get("stage") or "DISCONNECTED"),
                      "DISCONNECTED_LIVE_PATHS")
                if name:
                    static_defaults.setdefault(name, default)
    except OSError:
        pass

    for item in dataclasses.fields(V.QuickConfig):
        default = getattr(V.QuickConfig, item.name, item.default)
        merge(item.name, [default], "QUICK_CONFIG", "QUICK_CONFIG")
        static_defaults[item.name] = default
    for name in getattr(V, "AUTO_WIRED_PARAMS", ()):
        default = static_defaults.get(name)
        values: list[Any] = [] if default is None else [default]
        if isinstance(default, bool):
            values = [False, True]
        elif str(name).endswith(("_ENABLED", "_DISABLED", "_PAPER")):
            values = [False, True]
        elif is_tf_param(str(name)):
            values = list(TF_ORDER)
        merge(str(name), values, "ENGINE_AUTO_WIRED", "ENGINE_AUTO_WIRED")

    # Concrete values observed in recent result artifacts override guesswork.
    for inventory_path in (
        ROOT / "data/reports/RECENT_BACKTEST_SWITCH_INVENTORY_5D.csv",
        ROOT / "data/reports/RECENT_BACKTEST_SWITCH_INVENTORY_5D_S1.csv",
    ):
        try:
            with inventory_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    values = []
                    for raw in json.loads(row.get("values") or "[]"):
                        try:
                            values.append(json.loads(raw))
                        except (TypeError, ValueError):
                            values.append(raw)
                    name = str(row.get("switch") or "")
                    allowed = catalog_grids.get(name)
                    if allowed:
                        def catalog_match(value: Any) -> bool:
                            for expected in allowed:
                                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                                        and isinstance(expected, (int, float)) and not isinstance(expected, bool)):
                                    if math.isfinite(float(value)) and float(value) == float(expected):
                                        return True
                                elif typed_key(value) == typed_key(expected):
                                    return True
                            return False
                        values = [value for value in values if catalog_match(value)]
                    merge(name, values, "RECENT_5D", "RECENT_RESULTS_5D")
        except (OSError, ValueError):
            pass
    # Do not narrow the empirical audit here.  Every declared switch with a
    # causal spec is swept; the Numbers workbook remains classification and
    # reporting metadata, not a cap on per-switch/filter coverage.
    return specs


def static_read_sets() -> dict[str, set[str]]:
    """Index executable-looking config reads once per source surface.

    The old implementation reread multi-megabyte live/engine files for every
    switch, leaving a 24-worker box on one CPU during preflight.
    """
    patterns = (
        re.compile(r"\.\s*([A-Z][A-Z0-9_]{2,})\b"),
        re.compile(r"getattr\([^\n,]+,\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"),
        re.compile(r"\[\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]\s*\]"),
    )
    result: dict[str, set[str]] = {}
    for surface, files in STATIC_SURFACES.items():
        found: set[str] = set()
        for filename in files:
            path = ROOT / filename
            if not path.exists():
                continue
            text_value = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                found.update(pattern.findall(text_value))
        result[surface] = found
    return result


def active_baseline(symside: str) -> dict[str, Any]:
    paths = (
        ROOT / "data/hourly_reconfig/trb/active_config.json",
        ROOT / "data/reports/gui_lab/next_gen_beam_per_sym.json",
    )
    for path in paths:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get(symside), dict):
            overrides = raw[symside].get("overrides")
            if isinstance(overrides, dict):
                return dict(overrides)
        ledger = raw.get("ledger") if isinstance(raw, dict) else raw
        if isinstance(ledger, list):
            for item in ledger:
                if not isinstance(item, dict) or item.get("symside") != symside:
                    continue
                overrides = item.get("overrides") or (item.get("best") or {}).get("overrides")
                if isinstance(overrides, dict):
                    return dict(overrides)
    return {}


def parents_of(name: str, declared: set[str]) -> list[str]:
    tokens = name.split("_")
    candidates = []
    for end in range(len(tokens) - 1, 1, -1):
        candidate = "_".join(tokens[:end]) + "_ENABLED"
        if candidate != name and candidate in declared:
            candidates.append(candidate)
    # Common generated triplet: FEATURE_FILTER_TF / FEATURE_MIN_TFS is gated by
    # FEATURE_ENABLED even when simple prefix truncation misses it.
    for suffix in ("_FILTER_TF", "_MIN_TFS", "_TIMEFRAME", "_TF"):
        if name.endswith(suffix):
            candidate = name[:-len(suffix)] + "_ENABLED"
            if candidate in declared and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def choose_active(values: list[Any], default: Any) -> Any:
    for value in values:
        if isinstance(value, bool) and value:
            return value
    for value in values:
        if typed_key(value) != typed_key(default):
            return value
    return values[0] if values else default


def ensure_probe_values(name: str, values: list[Any], default: Any) -> list[Any]:
    """Ensure a declared scalar has at least one genuine non-default probe.

    Inventory/workbook values remain authoritative.  This only supplies the
    missing opposite state for a boolean or a conservative sensitivity point
    for a numeric field that otherwise has its default alone.  Unknown string
    enums are not guessed; timeframe selectors use the declared audit frames.
    """
    cleaned: list[Any] = []
    for value in values:
        if isinstance(default, bool):
            if isinstance(value, bool):
                cleaned.append(value)
            elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                cleaned.append(value.strip().lower() == "true")
        elif isinstance(default, int) and not isinstance(default, bool):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # Integer switches must not inherit fractional "values" from
                # recent-result comparison columns. Those artifacts are not
                # executable settings and can create impossible VALUE_ALIAS
                # failures (for example an int score bonus receiving 1.5).
                numeric = float(value)
                if math.isfinite(numeric) and numeric.is_integer():
                    cleaned.append(int(numeric))
        elif isinstance(default, float):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cleaned.append(float(value))
        elif isinstance(default, tuple):
            if isinstance(value, (tuple, list)):
                cleaned.append(tuple(value))
        elif isinstance(default, list):
            if isinstance(value, (tuple, list)):
                cleaned.append(list(value))
        elif isinstance(default, str):
            if (isinstance(value, str) and value not in {"", "True", "False"}
                    and not value.endswith("_alt")):
                cleaned.append(value)
        elif value is None or isinstance(value, type(default)):
            cleaned.append(value)
    result = dedup([default, *cleaned])
    if isinstance(default, bool):
        return dedup([False, True, *result])
    if isinstance(default, int) and not isinstance(default, bool) and len(result) < 2:
        alternate = 1 if default == 0 else (0 if default > 0 else default + 1)
        return dedup([default, alternate])
    if isinstance(default, float) and len(result) < 2:
        alternate = 1.0 if default == 0 else 0.0
        return dedup([default, alternate])
    if isinstance(default, str) and is_tf_param(name):
        return dedup([default, *AUDIT_TFS])
    if isinstance(default, str) and name.endswith("_SIDE"):
        return dedup([default, "LONG", "SHORT", "BOTH"])
    if isinstance(default, (tuple, list)) and name.endswith("_TFS"):
        values_by_tf = [(tf,) for tf in AUDIT_TFS]
        return dedup([default, *values_by_tf])
    return result


def is_tradier_score_family(name: str) -> bool:
    """Return whether *name* is part of live Tradier signal scoring."""
    return (
        name == "K_ZONE_ENTRY_ENABLED_TRADIER" or
        name.startswith("MOMENTUM_FADE_") or
        name.startswith("MTS_") or
        name in {
            "RSI_ENTRY_LONG_TRADIER", "RSI_ENTRY_SHORT_TRADIER",
            "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER",
            "WT_COMPOSITE_SCORING_ENABLED_TRADIER",
        }
    )


ADDITIVE_ENTRY_FAMILIES = {
    "BB_BREAKOUT": {
        "parent": "BB_BREAKOUT_ENABLED", "score": "BB_BREAKOUT_SCORE",
        "tf": "BB_BREAKOUT_TF",
    },
    "DC_BREAKOUT": {
        "parent": "DC_BREAKOUT_ENTRY_ENABLED", "score": "DC_BREAKOUT_SCORE",
        "tf": "DC_BREAKOUT_TF",
    },
    "HA_WICK_QUALITY": {
        "parent": "HA_WICK_QUALITY_ENABLED", "score": "HA_WICK_QUALITY_SCORE",
        "tf": "HA_WICK_QUALITY_TF",
    },
    "MACD_ZERO_CROSS": {
        "parent": "MACD_ZERO_CROSS_ENABLED", "score": "MACD_ZERO_CROSS_SCORE",
        "tf": "MACD_ZERO_CROSS_TF",
    },
    "RSI2": {
        "parent": "RSI2_MEAN_REVERSION_ENABLED", "score": "RSI2_SCORE_BONUS",
        "tf": "",
    },
    "RSI_MACD_EMA": {
        "parent": "RSI_MACD_EMA_ENABLED", "score": "RSI_MACD_EMA_SCORE",
        "tf": "RSI_MACD_EMA_TF",
    },
    "TRIPLE_CONF": {
        "parent": "TRIPLE_CONF_ENABLED", "score": "TRIPLE_CONF_SCORE",
        "tf": "TRIPLE_CONF_TF",
    },
}


def additive_entry_family(name: str) -> dict[str, str] | None:
    # Longest prefix first so RSI_MACD_EMA is not captured by RSI2/RSI.
    for prefix in sorted(ADDITIVE_ENTRY_FAMILIES, key=len, reverse=True):
        if name == prefix or name.startswith(prefix + "_"):
            return ADDITIVE_ENTRY_FAMILIES[prefix]
    return None


def native_base_tf(symside: str) -> str:
    symbol = symside.rsplit("_", 1)[0].upper()
    return "3m" if symbol.endswith(("USDT", "USDC", "USD1")) else "5m"


def fixed_field_tfs(name: str) -> list[str]:
    """Return only timeframes literally encoded in a switch name."""
    found = set()
    for token in re.findall(r"(?:^|_)(1M|3M|5M|15M|30M|1H|4H|D|W)(?:_|$)", name):
        found.add(token.lower() if token not in {"D", "W"} else token)
    return [tf for tf in TF_ORDER if tf in found]


def choose_context(
    spec: Spec, declared: set[str], defaults: dict[str, Any], symside: str
) -> dict[str, Any]:
    """Enable only parents/siblings needed to make the tested path callable."""
    context: dict[str, Any] = {}

    def set_if_declared(name: str, value: Any) -> None:
        if name in declared and name != spec.name:
            context[name] = value

    # BASE_TF is a venue contract, never the audited filter timeframe.  BTC
    # stays on native 3m and Tradier stays on native 5m; explicit selector or
    # per-TF route fields below choose the completed higher-timeframe arrays.
    set_if_declared("BASE_TF", native_base_tf(symside))

    # A switch cannot be certified behind a lifecycle path that never fires.
    # Non-entry lifecycle arms receive a sparse causal WT entry witness.  An
    # entry family must *not* receive that competing route: its OFF reference
    # stays entry-free and the candidate itself has to create the real fill.
    # This prevents an otherwise valid 5m EMA/WT route from aliasing merely
    # because every one of its fills was already opened by the 15m witness.
    labels = "|".join([spec.name, *spec.groups, *spec.sheets]).upper()
    entry_route = (
        "ENTRY" in labels or additive_entry_family(spec.name) is not None or spec.name.startswith((
            "EMA_TF_", "WT_TF_", "BB_RSI_STOCH_SCALP_",
            "EMA200_STOCHRSI_", "EMA_PULLBACK_",
        ))
    )
    if not entry_route:
        set_if_declared("WT_TF_15M_ENABLED", True)
    set_if_declared("COOLDOWN_BARS", 0)
    set_if_declared("MIN_HOLD_BARS", 0)
    if entry_route:
        # Existing real lifecycle controls expose accepted entry differences
        # as fills without inventing signals. Apply them equally to reference
        # and candidate and let the tested field overwrite itself last.
        set_if_declared("COOLDOWN_BARS_TRADIER", 0)
        set_if_declared("MIN_HOLD_BARS_BEFORE_EXIT", 0)
        set_if_declared("MIN_HOLD_MINUTES_TRADIER", 0.0)
        set_if_declared("DELTA_ENGINE_ENABLED", True)
        set_if_declared("DELTA_MAX_HOLD_BARS", 1)
    if "EXIT" in labels or "REENTRY" in labels or "REDUCE" in labels:
        set_if_declared("STRUCTURAL_EXIT_GATE_ENABLED", False)
    if "REENTRY" in labels:
        set_if_declared("MTF_EXIT_USE_COMPOUND", True)
        set_if_declared("MTF_BB_REJECT_EXIT_ENABLED", True)
        set_if_declared("MTF_BB_REJECT_EXIT_TF", "15m")
        set_if_declared("MTF_BB_REJECT_EXIT_LOOKBACK", 5)
    if "REDUCE" in labels:
        set_if_declared("REGIME_GATE_ENABLED", True)
    if "AUGMENT" in labels:
        set_if_declared("DELTA_GATE_AUGMENT", True)
        set_if_declared("AUGMENTATION_COOLDOWN_SECONDS", 0.0)

    # Exact callability contract for tradier_manage.calculate_signal_score.
    # These fields are a coupled family: thresholds/bonuses are inert while
    # their parent is OFF, and bonuses cannot change execution unless the real
    # live score floor is active.  Candidate and reference receive the same
    # sibling context; build_variants writes the tested field last.
    name = spec.name
    tradier_score_family = is_tradier_score_family(name)
    additive_family = additive_entry_family(name)
    if additive_family:
        # Every score family is isolated from its scored siblings. The target
        # parent is activated below (unless it is itself the tested field).
        for family in ADDITIVE_ENTRY_FAMILIES.values():
            set_if_declared(family["parent"], False)
        set_if_declared(additive_family["parent"], True)
        set_if_declared("TRADIER_ENTRY_SCORE_THRESHOLD", 0)
        set_if_declared("K3M_FLOOR", 0.0)
        set_if_declared("HTF_ALIGNMENT_ENABLED", False)
        set_if_declared("D_TREND_REQUIRED", False)
        set_if_declared("MFI_ENTRY_ENABLED", False)
        set_if_declared("KINDERGARTEN_EMA_GATE_ENABLED", False)
        set_if_declared("MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED", False)
        set_if_declared("BACKTEST_VALIDATED_GATES_TRADIER", False)
        set_if_declared("WT_DC_HTF_GATE", "none")
        # Explicit route siblings are off. They must not swallow the additive
        # event before its own score boundary is reached.
        for tf_key in ("3M", "5M", "15M", "1H", "4H", "D"):
            set_if_declared(f"WT_TF_{tf_key}_ENABLED", False)
            set_if_declared(f"EMA_TF_{tf_key}_ENABLED", False)
    if tradier_score_family:
        # MU executes on native 5m Tradier bars.  The audited timeframe selects
        # the persisted WT decision route below; it must not replace the venue
        # base with an aligned/repeated 15m/1h/4h/D close series.
        set_if_declared("BASE_TF", "5m")
        # build_variants activates the native WT route matching each audit TF.
        # Start with every route disabled so a lower-TF witness cannot mask a
        # present higher-TF path.
        for tf_key in ("5M", "15M", "1H", "4H", "D"):
            set_if_declared(f"WT_TF_{tf_key}_ENABLED", False)
        set_if_declared("WT_COMPOSITE_SCORING_ENABLED_TRADIER", False)
        set_if_declared("RSI_ENTRY_LONG_TRADIER", 101.0)
        set_if_declared("RSI_ENTRY_SHORT_TRADIER", -1.0)
        set_if_declared("TRADIER_ENTRY_SCORE_THRESHOLD", 0)

        # The score-family witness must not be starved by unrelated default
        # entry filters. These are real, equal config settings on candidate
        # and reference; they only make the selected native WT route callable.
        set_if_declared("K3M_FLOOR", 0.0)
        set_if_declared("HTF_ALIGNMENT_ENABLED", False)
        set_if_declared("D_TREND_REQUIRED", False)
        set_if_declared("MFI_ENTRY_ENABLED", False)
        set_if_declared("KINDERGARTEN_EMA_GATE_ENABLED", False)
        set_if_declared("MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED", False)
        set_if_declared("BACKTEST_VALIDATED_GATES_TRADIER", False)
        set_if_declared("COMBINED_STOCH_GATE_TRADIER", 100.0)
        set_if_declared("STOCH_CROSS_ENTRY_TRADIER", False)
        set_if_declared("WT_DC_HTF_GATE", "none")

        # Entry-mask differences must reach executed fills. Tradier uses its
        # own cooldown and a second minimum-hold field, so changing only the
        # generic fields still left positions open for 32 bars and swallowed
        # later candidate signals. A one-bar DELTA max hold is an existing,
        # causal lifecycle setting applied equally to every comparison arm.
        set_if_declared("COOLDOWN_BARS_TRADIER", 0)
        set_if_declared("MIN_HOLD_BARS_BEFORE_EXIT", 0)
        set_if_declared("MIN_HOLD_MINUTES_TRADIER", 0.0)
        set_if_declared("DELTA_ENGINE_ENABLED", True)
        set_if_declared("DELTA_MAX_HOLD_BARS", 1)

        if name.startswith("MOMENTUM_FADE_") and name != "MOMENTUM_FADE_ENABLED_TRADIER":
            set_if_declared("MOMENTUM_FADE_ENABLED_TRADIER", True)
        if name.startswith(("MTS_BOTTOM_", "MTS_ENTRY_QUALITY_")):
            set_if_declared("MTS_GATE_ENABLED_TRADIER", True)
        if name == "MTS_BOTTOM_MIN_TRADIER":
            set_if_declared("MTS_ENTRY_QUALITY_MIN_TRADIER", -100.0)
        if name == "MTS_ENTRY_QUALITY_MIN_TRADIER":
            set_if_declared("MTS_BOTTOM_MIN_TRADIER", -100.0)
        if name == "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER":
            set_if_declared("K_ZONE_ENTRY_ENABLED_TRADIER", True)
            # The declared field is integer-valued. A 7.25 decision floor
            # separates every real int candidate (1, 3, 4, 25) on MU while
            # remaining the same real score gate on both comparison arms.
            set_if_declared("TRADIER_ENTRY_SCORE_THRESHOLD", 7.25)
        if name in {
            "MOMENTUM_FADE_ENABLED_TRADIER",
            "MOMENTUM_FADE_K_ZONE_TRADIER",
        }:
            set_if_declared("TRADIER_ENTRY_SCORE_THRESHOLD", 5.0)

        # Isolate fields that control the three-way K/Stoch/RSI hard gate.
        # Present persisted K-zone/flow data must be the only way through.
        if name in {
            "K_ZONE_ENTRY_ENABLED_TRADIER", "RSI_ENTRY_LONG_TRADIER",
            "RSI_ENTRY_SHORT_TRADIER", "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER",
        }:
            set_if_declared("STOCH_ENTRY_LONG_TRADIER", -1.0)
            set_if_declared("STOCH_EXTREME_LONG_TRADIER", -1.0)
            set_if_declared("STOCH_ENTRY_SHORT_TRADIER", 101.0)
            set_if_declared("STOCH_EXTREME_SHORT_TRADIER", 101.0)
        if name in {
            "K_ZONE_ENTRY_ENABLED_TRADIER",
            "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER",
        }:
            # K-zone must be the only surviving arm of the live
            # Stoch/K-zone/MFI setup OR.
            set_if_declared("RSI_ENTRY_LONG_TRADIER", -1.0)
            set_if_declared("RSI_ENTRY_SHORT_TRADIER", 101.0)
        if name.startswith("RSI_ENTRY_"):
            set_if_declared("K_ZONE_ENTRY_ENABLED_TRADIER", False)

        # More whole-share units make genuine size/score changes observable;
        # this is an equal, existing sizing input on both arms, not a synthetic
        # behavior modifier.
        set_if_declared("START_POSITION_SIZE", 10000.0)
    for parent in [*parents_of(spec.name, declared), *sorted(spec.parent_hints)]:
        if parent in declared and parent != spec.name:
            # Parent hints are activation gates. Never write boolean True into
            # a numeric parent merely because its name appeared in metadata.
            if isinstance(defaults.get(parent), bool):
                context[parent] = True
    # Prefer the row where the switch is primary; otherwise activate that row's
    # primary gate so a paired filter/timeframe is not tested behind an OFF path.
    rows = sorted(spec.row_contexts, key=lambda row: row["primary"] != spec.name)
    if rows:
        row = rows[0]
        primary = row["primary"]
        if primary != spec.name and primary in declared:
            context[primary] = choose_active(row["primary_values"], defaults.get(primary))
    return context


def apply_native_entry_witness(
    cfg: dict[str, Any], spec: Spec, tf: str, declared: set[str]
) -> None:
    """Call a coupled Tradier entry family through the audited native TF."""
    name = spec.name
    tradier_score_family = is_tradier_score_family(name)
    if not tradier_score_family:
        return
    route = f"WT_TF_{tf.upper()}_ENABLED"
    if route in declared:
        cfg[route] = True

    # Score additions only affect execution at a real score boundary. These
    # floors were selected from the persisted one-year MU arrays and are
    # applied equally to the reference and every candidate value.
    score_floors = {
        "MOMENTUM_FADE_BODY_ATR_MIN_TRADIER": {
            "5m": .25, "15m": 2.25, "1h": 5.25, "4h": .25, "D": 5.25,
        },
        "MOMENTUM_FADE_VOL_MIN_TRADIER": {
            "5m": 5.25, "15m": 5.25, "1h": 18.25, "4h": 9.25, "D": 14.25,
        },
        "MOMENTUM_FADE_SCORE_BONUS_TRADIER": {
            "5m": 2.25, "15m": 2.25, "1h": 4.25, "4h": 2.25, "D": 4.25,
        },
    }
    floor = score_floors.get(name, {}).get(tf)
    if floor is not None and "TRADIER_ENTRY_SCORE_THRESHOLD" in declared:
        cfg["TRADIER_ENTRY_SCORE_THRESHOLD"] = floor

    # The native D WT route has no MU_LONG candidates between MTS bottom 2.5
    # and 3.75. Add the persisted native 5m WT route as an equal, causal
    # sibling witness; it contains real candidates in that exact interval.
    if name == "MTS_BOTTOM_MIN_TRADIER" and tf == "D" and "WT_TF_5M_ENABLED" in declared:
        cfg["WT_TF_5M_ENABLED"] = True


def apply_additive_entry_witness(
    cfg: dict[str, Any], spec: Spec, tf: str, candidate_value: Any,
    default: Any, defaults: dict[str, Any], declared: set[str],
) -> None:
    """Apply an equal real score boundary to one additive entry comparison.

    A score value is only observable at a boundary between that candidate and
    the control.  The boundary is applied to both arms of the pair; it creates
    no bars and merely lets the existing causal predicate qualify one arm.
    """
    family = additive_entry_family(spec.name)
    if family is None:
        return
    parent = family["parent"]
    score_name = family["score"]
    tf_name = family["tf"]
    if parent in declared and parent != spec.name:
        cfg[parent] = True
    if tf_name and tf_name in declared and tf_name != spec.name:
        cfg[tf_name] = tf

    score_default = defaults.get(score_name, 1.0)
    floor = max(0.5, float(score_default) * 0.5)
    if spec.name == score_name:
        try:
            candidate_num = float(candidate_value)
            default_num = float(default)
            if math.isfinite(candidate_num) and math.isfinite(default_num):
                if candidate_num != default_num:
                    floor = (candidate_num + default_num) * 0.5
                else:
                    floor = max(0.5, default_num * 0.5)
        except (TypeError, ValueError):
            pass
    if "ENTRY_SCORE_THRESHOLD" in declared and spec.name != "ENTRY_SCORE_THRESHOLD":
        cfg["ENTRY_SCORE_THRESHOLD"] = floor
    if "TRADIER_ENTRY_SCORE_THRESHOLD" in declared and spec.name != "TRADIER_ENTRY_SCORE_THRESHOLD":
        cfg["TRADIER_ENTRY_SCORE_THRESHOLD"] = 0


def execution_validity(metrics: dict[str, Any]) -> tuple[bool, str]:
    """Return causal-audit validity, separate from pilot restrictions."""
    fingerprint = str(metrics.get("behavior_fingerprint") or "")
    invalid_reason = str(metrics.get("invalid_reason") or "")
    lowered = invalid_reason.lower()
    if not fingerprint:
        return False, "behavior fingerprint unavailable"
    if int(metrics.get("bars") or 0) <= 0:
        return False, "no executable bars"
    if lowered.startswith("no npz") or "npz covers" in lowered:
        return False, invalid_reason or "NPZ data unavailable"
    if lowered.startswith("v12 "):
        return False, invalid_reason
    if "bh unavailable" in lowered:
        return False, invalid_reason
    return True, ""


def signature(metrics: dict[str, Any]) -> tuple[Any, ...] | None:
    # This is a wiring audit, not a strategy-qualification run.  A low trade
    # count, TIM/DD miss, or other optimisation restriction does not make an
    # executed ledger unusable as wiring evidence.  Only evaluator/data
    # failures make the execution untestable.
    executable, _reason = execution_validity(metrics)
    if not executable:
        return None
    fingerprint = str(metrics.get("behavior_fingerprint") or "")
    numeric = tuple(round(float(metrics.get(key) or 0.0), 12) for key in METRIC_KEYS)
    return (fingerprint, *numeric)


def delta_value(metrics: dict[str, Any]) -> float:
    return round(float(metrics.get("delta_vs_bh") or 0.0), 12)


def metric_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    execution_valid, execution_reason = execution_validity(metrics)
    payload = {
        key: metrics.get(key) for key in (
            *METRIC_KEYS, "valid", "invalid_reason", "behavior_fingerprint",
            "bars", "data_days", "window_days", "spike_trades_dropped",
        )
    }
    payload.update({
        "execution_valid": execution_valid,
        "execution_invalid_reason": execution_reason,
        "pilot_eligible": bool(metrics.get("valid")),
        "pilot_ineligible_reason": str(metrics.get("invalid_reason") or ""),
    })
    return payload


def build_variants(
    spec: Spec, default: Any, declared: set[str], defaults: dict[str, Any], symside: str
):
    context = choose_context(spec, declared, defaults, symside)
    tradier_score_family = is_tradier_score_family(spec.name)
    additive_family = additive_entry_family(spec.name)
    native_tf = native_base_tf(symside)
    variants: list[dict[str, Any]] = []
    # If the switch itself selects the timeframe, each of its TF values is one
    # applicable state.  Otherwise use the narrowest sibling TF field from its
    # workbook row and run every listed timeframe with every switch value.
    own_tfs = tf_values(spec.values) if is_tf_param(spec.name) else []
    if own_tfs:
        for tf in own_tfs:
            cfg = dict(context)
            apply_additive_entry_witness(
                cfg, spec, tf, tf, default, defaults, declared
            )
            cfg[spec.name] = tf
            ref_cfg = dict(context)
            apply_additive_entry_witness(
                ref_cfg, spec, tf, tf, default, defaults, declared
            )
            ref_cfg[spec.name] = default
            variants.append({
                "tf": tf, "value": tf, "overrides": cfg,
                "reference_overrides": ref_cfg,
            })
    else:
        tf_items = sorted(
            ((name, [tf for tf in TF_ORDER if tf in values])
             for name, values in spec.tf_contexts.items() if name != spec.name),
            key=lambda item: (0 if item[0].startswith(spec.name.removesuffix("_ENABLED")) else 1,
                              item[0]),
        )
        # The base series is fixed by venue. ``tf`` must select an explicit
        # sibling selector/route; a field without one has native-only semantics
        # and is audited once rather than repeated under fake BASE_TF aliases.
        tf_name = None if tradier_score_family else (tf_items[0][0] if tf_items else None)
        if tradier_score_family:
            contexts = list(AUDIT_TFS)
        elif additive_family and additive_family["tf"]:
            contexts = list(AUDIT_TFS)
        elif additive_family:
            # RSI2 is intentionally fixed to its persisted 1h score.
            contexts = ["1h"]
        elif tf_items:
            supported = set(tf_items[0][1])
            contexts = [tf for tf in AUDIT_TFS if tf in supported]
        else:
            encoded = fixed_field_tfs(spec.name)
            contexts = [tf for tf in AUDIT_TFS if tf in encoded] or [native_tf]
        for tf in contexts:
            for value in spec.values:
                cfg = dict(context)
                apply_native_entry_witness(cfg, spec, tf, declared)
                apply_additive_entry_witness(
                    cfg, spec, tf, value, default, defaults, declared
                )
                cfg[spec.name] = value
                display_tf_param = (
                    f"WT_TF_{tf.upper()}_ENABLED" if tradier_score_family
                    else (tf_name or "NATIVE_BASE_FIXED")
                )
                if tf_name:
                    cfg[tf_name] = tf
                    # Turn on the TF field's own feature parent if present.
                    for parent in parents_of(tf_name, declared):
                        cfg[parent] = True
                ref_cfg = dict(context)
                apply_native_entry_witness(ref_cfg, spec, tf, declared)
                apply_additive_entry_witness(
                    ref_cfg, spec, tf, value, default, defaults, declared
                )
                if tf_name:
                    ref_cfg[tf_name] = tf
                    for parent in parents_of(tf_name, declared):
                        ref_cfg[parent] = True
                ref_cfg[spec.name] = default
                variants.append({
                    "tf": tf, "tf_param": display_tf_param, "value": value,
                    "overrides": cfg, "reference_overrides": ref_cfg,
                })
    # Reference uses the same activation context but the engine default for the
    # tested switch.  Explicit defaults are retained as evidence rows.
    refs: dict[str, dict[str, Any]] = {}
    for variant in variants:
        tf = variant["tf"]
        cfg = dict(context)
        apply_native_entry_witness(cfg, spec, tf, declared)
        cfg[spec.name] = default
        tf_name = None if tradier_score_family else variant.get("tf_param")
        if tf_name == "NATIVE_BASE_FIXED":
            tf_name = None
        if tf_name:
            cfg[tf_name] = tf
            for parent in parents_of(tf_name, declared):
                cfg[parent] = True
        refs[tf] = cfg
    return variants, refs, context


def _probe_switch(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.opt import evaluate_v12 as EV
    import v12_quick_engine as V

    name = payload["name"]
    base = payload["base"]
    window_days = payload["window_days"]
    symside = payload["symside"]
    rows = []
    ref_metrics: dict[str, dict[str, Any]] = {}

    defaults = {item.name: getattr(V.QuickConfig, item.name, item.default)
                for item in dataclasses.fields(V.QuickConfig)}

    def cached_evaluate(applied: dict[str, Any]) -> dict[str, Any]:
        # Explicit defaults are semantically identical to omitted defaults.
        # Normalising them lets a reused pool worker execute each timeframe
        # control once instead of once per switch.
        normalized = {
            key: value for key, value in applied.items()
            if key not in defaults or typed_key(value) != typed_key(defaults[key])
        }
        key = json.dumps(
            [symside, window_days, normalized], sort_keys=True,
            separators=(",", ":"), default=str,
        )
        hit = _WORKER_EVAL_CACHE.get(key)
        if hit is not None:
            return hit
        value = EV.evaluate(symside, applied, window_days, 0)
        if len(_WORKER_EVAL_CACHE) >= 256:
            _WORKER_EVAL_CACHE.pop(next(iter(_WORKER_EVAL_CACHE)))
        _WORKER_EVAL_CACHE[key] = value
        return value

    needed_reference_tfs = {
        variant["tf"] for variant in payload["variants"]
        if variant.get("reference_overrides") is None
    }
    for tf, overrides in payload["references"].items():
        if tf not in needed_reference_tfs:
            continue
        applied = dict(base)
        applied.update(overrides)
        ref_metrics[tf] = cached_evaluate(applied)
    for variant in payload["variants"]:
        applied = dict(base)
        applied.update(variant["overrides"])
        reference_applied = dict(base)
        reference_applied.update(
            variant.get("reference_overrides") or payload["references"][variant["tf"]]
        )
        ref = (
            cached_evaluate(reference_applied)
            if variant.get("reference_overrides") is not None
            else ref_metrics[variant["tf"]]
        )
        # The explicit default/off cell is normally byte-for-byte the control.
        # Reuse that already executed control rather than rerunning the same
        # one-year simulation; this preserves evidence and removes ~1/3 of the
        # campaign work for binary switches.
        metrics = ref if applied == reference_applied else cached_evaluate(applied)
        row = dict(variant)
        row["applied_overrides"] = applied
        row["metrics"] = metric_payload(metrics)
        row["reference"] = metric_payload(ref)
        row["signature"] = signature(metrics)
        row["reference_signature"] = signature(ref)
        # Certification requires a real ledger change AND a unique delta.  A
        # changed config hash or reason label is never sufficient evidence.
        row["moves_reference"] = (
            row["signature"] is not None and
            row["reference_signature"] is not None and
            row["metrics"].get("behavior_fingerprint") != row["reference"].get("behavior_fingerprint") and
            delta_value(row["metrics"]) != delta_value(row["reference"])
        )
        rows.append(row)
    return {"name": name, "rows": rows}


def evaluate_verdict(
    spec: Spec, rows: list[dict[str, Any]], symside: str
) -> tuple[str, str, dict[str, Any]]:
    if not rows:
        return "INVALID", "no empirical rows", {}
    valid = [row for row in rows if row["signature"] is not None]
    if len(valid) != len(rows):
        invalid_rows = [row for row in rows if row["signature"] is None]
        data_reasons = [
            str(row["metrics"].get("execution_invalid_reason") or "").lower()
            for row in invalid_rows
        ]
        if invalid_rows and len(invalid_rows) == len(rows) and all(
            reason.startswith("no npz") or "npz covers" in reason
            for reason in data_reasons
        ):
            return "N/A_DATA", data_reasons[0], {}
        return "INVALID", f"{len(rows)-len(valid)}/{len(rows)} invalid executions", {}
    by_tf: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_tf[row["tf"]].append(row)

    side = symside.rsplit("_", 1)[-1].upper()
    opposite_side_field = (
        (side == "LONG" and spec.name == "RSI_ENTRY_SHORT_TRADIER") or
        (side == "SHORT" and spec.name == "RSI_ENTRY_LONG_TRADIER")
    )
    if opposite_side_field:
        return (
            "SIDE_NA",
            f"{spec.name} is an exact {('SHORT' if side == 'LONG' else 'LONG')}-side gate; "
            f"it cannot alter a {symside} ledger",
            {},
        )

    witness: dict[str, dict[str, Any]] = {}
    for tf, tf_rows in by_tf.items():
        moved = [row for row in tf_rows if row["moves_reference"]]
        # For a TF selector, the default TF is a legitimate unique state even
        # though it equals its own reference.  Pick it when it is the only row.
        if not moved and len(tf_rows) == 1 and is_tf_param(spec.name):
            moved = tf_rows
        if not moved:
            return "INERT", f"no value changes execution at {tf}", {}
        witness[tf] = max(moved, key=lambda row: abs(float(
            (row["metrics"].get("delta_vs_bh") or 0.0) -
            (row["reference"].get("delta_vs_bh") or 0.0))))
    if len(witness) > 1:
        fingerprints = [row["metrics"].get("behavior_fingerprint") for row in witness.values()]
        deltas = [round(float(row["metrics"].get("delta_vs_bh") or 0.0), 12)
                  for row in witness.values()]
        if len(set(fingerprints)) != len(fingerprints):
            return "TF_ALIAS", "two or more timeframes have identical executed ledgers", witness
        if len(set(deltas)) != len(deltas):
            return "TF_ALIAS", "two or more timeframes have identical delta", witness
    # Values inside a TF must not collapse to one state, except an explicit
    # disabled value whose purpose is to match the reference.
    for tf, tf_rows in by_tf.items():
        active = [row for row in tf_rows if not (isinstance(row["value"], bool) and not row["value"])]
        sigs = [row["signature"] for row in active]
        deltas = [delta_value(row["metrics"]) for row in active]
        if len(sigs) > 1 and len(set(sigs)) != len(sigs):
            return "VALUE_ALIAS", f"distinct active values alias at {tf}", witness
        if len(deltas) > 1 and len(set(deltas)) != len(deltas):
            return "VALUE_ALIAS", f"distinct active values have identical delta at {tf}", witness
    return "PASS", "all applicable values/timeframes change real execution uniquely", witness


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish progress atomically so the watchdog never reads partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    temp.replace(path)


def select_requested_specs(
    specs: dict[str, Spec], requested: str,
    declared_defaults: dict[str, Any] | None = None,
) -> dict[str, Spec]:
    """Resolve focused CLI names case-insensitively without changing schema case.

    QuickConfig contains mixed-case timeframe suffixes such as ``MTS_WEIGHT_5m``.
    The command line is intentionally forgiving, but the returned keys must be
    the exact canonical names used by the dataclass and engine read sites.
    """
    canonical_by_fold: dict[str, str] = {}
    declared_by_fold = {
        name.casefold(): name for name in (declared_defaults or {})
    }
    for canonical in specs:
        folded = canonical.casefold()
        # The dataclass spelling is authoritative when an older inventory
        # source uppercased a mixed-case suffix.
        if folded in declared_by_fold and canonical != declared_by_fold[folded]:
            continue
        previous = canonical_by_fold.get(folded)
        if previous is not None and previous != canonical:
            raise ValueError(
                f"ambiguous switch names differ only by case: {previous},{canonical}"
            )
        canonical_by_fold[folded] = canonical
    canonical_by_fold.update(declared_by_fold)

    tokens = [name.strip() for name in requested.split(",") if name.strip()]
    missing = sorted({name for name in tokens if name.casefold() not in canonical_by_fold})
    if missing:
        raise ValueError(f"unknown --switches: {','.join(missing)}")
    resolved = {canonical_by_fold[name.casefold()] for name in tokens}
    selected: dict[str, Spec] = {}
    for name in sorted(resolved):
        spec = specs.get(name)
        if spec is None:
            # QuickConfig declarations with lowercase TF suffixes were
            # historically filtered out by the uppercase inventory regex.
            # A focused audit must still be able to select the real field.
            alias = next(
                (value for key, value in specs.items() if key.casefold() == name.casefold()),
                None,
            )
            default = (declared_defaults or {}).get(name)
            if alias is None:
                spec = Spec(
                    name=name, values=[default], groups={"QUICK_CONFIG"},
                    sheets={"QUICK_CONFIG"},
                )
            else:
                spec = Spec(
                    name=name,
                    values=list(alias.values),
                    groups=set(alias.groups) | {"QUICK_CONFIG"},
                    sheets=set(alias.sheets) | {"QUICK_CONFIG"},
                    tf_contexts=defaultdict(
                        set, {key: set(values) for key, values in alias.tf_contexts.items()}
                    ),
                    row_contexts=list(alias.row_contexts),
                    parent_hints=set(alias.parent_hints),
                )
                if default is not None:
                    spec.values = dedup([default, *spec.values])
        selected[name] = spec
    return selected


def main() -> int:
    global AUDIT_TFS
    parser = argparse.ArgumentParser()
    parser.add_argument("--symside", default="BTCUSDC_LONG")
    parser.add_argument("--window-days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--audit-tfs", default=",".join(AUDIT_TFS),
                        help="comma-separated base/filter TF contexts")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--switches", default="",
                        help="comma-separated exact switch names for a focused repair audit")
    parser.add_argument("--out", default="data/reports/BTCUSDC_LONG_SWITCH_TF_UNIQUENESS.csv")
    parser.add_argument("--summary-out", default="data/reports/BTCUSDC_LONG_SWITCH_TF_UNIQUENESS_SUMMARY.csv")
    parser.add_argument("--status", default="data/reports/BTCUSDC_LONG_SWITCH_TF_UNIQUENESS.status.json")
    args = parser.parse_args()

    requested_tfs = tuple(tf.strip() for tf in args.audit_tfs.split(",") if tf.strip())
    invalid_tfs = [tf for tf in requested_tfs if tf not in TF_ORDER]
    if not requested_tfs or invalid_tfs:
        parser.error(f"invalid --audit-tfs: {invalid_tfs or args.audit_tfs}")
    AUDIT_TFS = requested_tfs

    import v12_quick_engine as V

    specs = load_specs()
    quick_defaults = {
        item.name: getattr(V.QuickConfig, item.name, item.default)
        for item in dataclasses.fields(V.QuickConfig)
    }
    if args.switches:
        try:
            specs = select_requested_specs(specs, args.switches, quick_defaults)
        except ValueError as exc:
            parser.error(str(exc))
    if args.limit:
        specs = dict(list(sorted(specs.items()))[:args.limit])
    declared = {item.name for item in dataclasses.fields(V.QuickConfig)}
    defaults = {item.name: getattr(V.QuickConfig, item.name, item.default)
                for item in dataclasses.fields(V.QuickConfig)}
    # Per-symbol presets are the baseline for every single-switch cell.  They
    # are re-evaluated on the same frozen NPZ; their historical metrics are not
    # trusted, but their already-established causal route is retained.  Only
    # allowlisted QuickConfig fields enter the audit.
    base: dict[str, Any] = {}
    try:
        from tools.next_gen_beam_per_sym import _load_per_sym
        preset = (_load_per_sym().get(args.symside.upper()) or {}).get("overrides") or {}
        # Preserve the established preset wholesale for the baseline.  The
        # curated allowlist limits which fields are swept as independent
        # switches, but stripping preset fields changes the route and destroys
        # the known trade-producing starting point.
        base = {k: v for k, v in preset.items() if k in declared}
    except Exception:
        base = {}
    if os.environ.get("V12_ENTRY_SWEEP_BASELINE"):
        # Filter phase baseline: all declared entry switches on, filter gates
        # off, zero score floor, and WT15 fixed as the exit route.
        for field in dataclasses.fields(V.QuickConfig):
            value = field.default
            if isinstance(value, bool):
                base[field.name] = bool("ENTRY" in field.name.upper() and field.name.endswith("ENABLED"))
        base.update({
            "ENTRY_SCORE_THRESHOLD": 0,
            "TRADIER_ENTRY_SCORE_THRESHOLD": 0,
            "HTF_ALIGNMENT_ENABLED": False,
            "D_TREND_REQUIRED": False,
            "MTF_ARMED_ENTRY_ENABLED": True,
            "TRADIER_MIN_HOLD_MINUTES": 0.0,
            "TRA_MIN_HOLD_MINUTES": 0.0,
            "COOLDOWN_BARS": 0,
            "WT_CROSS_EXIT_ENABLED": True,
            "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM": False,
            "MTF_WT_CROSS_EXIT_TF": "15m",
        })
    read_sets = static_read_sets()
    static = {name: {surface: name in names for surface, names in read_sets.items()}
              for name in specs}
    scopes = {name: classify_run_scope(spec, args.symside) for name, spec in specs.items()}
    npz_keys, _npz_key_source = npz_key_inventory(args.symside)
    scalp_missing = load_scalp_v3_missing_contracts()
    for name in specs:
        if scopes[name][0] != "STRATEGY_CAUSAL":
            continue
        data_scope = required_data_scope(name, args.symside, npz_keys, scalp_missing)
        if data_scope is not None:
            scopes[name] = data_scope

    empirical: dict[str, list[dict[str, Any]]] = {}
    payloads = []
    summary: dict[str, dict[str, Any]] = {}
    for name, spec in sorted(specs.items()):
        scope, scope_reason = scopes[name]
        if scope != "STRATEGY_CAUSAL":
            summary[name] = {"verdict": scope, "reason": scope_reason}
            continue
        if name not in declared:
            summary[name] = {"verdict": "UNDECLARED", "reason": "not on QuickConfig"}
            continue
        spec.values = ensure_probe_values(name, spec.values, defaults.get(name))
        if name.endswith("_SYMBOLS") and isinstance(defaults.get(name), (tuple, list)):
            symbol = args.symside.rsplit("_", 1)[0]
            spec.values = dedup([defaults.get(name), (symbol,), *spec.values])
        variants, refs, context = build_variants(
            spec, defaults.get(name), declared, defaults, args.symside
        )
        payloads.append({
            "name": name, "base": base, "symside": args.symside,
            "window_days": args.window_days, "variants": variants,
            "references": refs, "context": context,
        })

    status_path = ROOT / args.status if not Path(args.status).is_absolute() else Path(args.status)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    done = 0
    write_json(status_path, {"status": "starting", "symside": args.symside,
                             "switches_total": len(specs), "declared_to_probe": len(payloads),
                             "empirical_done": 0, "watchdog_s": 20,
                             "elapsed_s": 0.0})
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(_probe_switch, payload): payload["name"] for payload in payloads}
        pending = set(futures)
        while pending:
            ready, pending = wait(pending, timeout=20.0, return_when=FIRST_COMPLETED)
            if not ready:
                for future in pending:
                    future.cancel()
                for process in (getattr(executor, "_processes", {}) or {}).values():
                    if process.is_alive():
                        process.terminate()
                write_json(status_path, {"status": "stalled_no_result", "symside": args.symside,
                                         "switches_total": len(specs), "empirical_done": done,
                                         "watchdog_s": 20, "elapsed_s": time.time() - started})
                return 3
            for future in ready:
                name = futures[future]
                try:
                    result = future.result()
                    empirical[name] = result["rows"]
                    verdict, reason, witness = evaluate_verdict(
                        specs[name], result["rows"], args.symside
                    )
                    summary[name] = {"verdict": verdict, "reason": reason, "witness": witness}
                except Exception as exc:
                    summary[name] = {"verdict": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
                done += 1
                counts: dict[str, int] = defaultdict(int)
                for row in summary.values():
                    counts[row["verdict"]] += 1
                status = {
                    "status": "running", "symside": args.symside,
                    "window_days": args.window_days, "audit_tfs": list(AUDIT_TFS),
                    "switches_total": len(specs),
                    "declared_to_probe": len(payloads), "empirical_done": done,
                    "counts": dict(counts), "elapsed_s": time.time() - started,
                    "verdicts": {switch: row["verdict"] for switch, row in sorted(summary.items())},
                }
                write_json(status_path, status)
                if done % 10 == 0 or done == len(payloads):
                    print(f"{done}/{len(payloads)} empirical | {dict(counts)} | {time.time()-started:.1f}s", flush=True)

    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for name, spec in sorted(specs.items()):
        verdict = summary[name]["verdict"]
        reason = summary[name]["reason"]
        common = {
            "Switch": name,
            "Scope": scopes[name][0],
            "Scope_Reason": scopes[name][1],
            "Verdict": verdict,
            "Reason": reason,
            "Groups": "|".join(sorted(spec.groups)),
            "Source_Sheets": "|".join(sorted(spec.sheets)),
            "Declared_Quick": name in declared,
            "Read_Quick": static[name]["quick"],
            "Read_Wide": static[name]["wide"],
            "Read_Scalar": static[name]["scalar"],
            "Read_Live_Crypto": static[name]["live_crypto"],
            "Read_Live_Stock": static[name]["live_stock"],
        }
        rows = empirical.get(name, [])
        summary_rows.append({
            **common,
            "Values_Defined": json.dumps(spec.values, separators=(",", ":"), default=str),
            "TFs_Tested": "|".join(sorted({row["tf"] for row in rows}, key=lambda x: TF_ORDER.index(x) if x in TF_ORDER else 99)),
            "Empirical_Rows": len(rows),
            "Unique_Behaviors": len({row["metrics"].get("behavior_fingerprint") for row in rows if row["signature"]}),
            "Unique_Deltas": len({round(float(row["metrics"].get("delta_vs_bh") or 0.0), 12) for row in rows if row["signature"]}),
        })
        for row in rows:
            metrics, ref = row["metrics"], row["reference"]
            detail_rows.append({
                **common,
                "Timeframe": row["tf"], "TF_Param": row.get("tf_param", ""),
                "Value": json.dumps(row["value"], default=str),
                "Moves_Reference": row["moves_reference"],
                "Delta_vs_BH": metrics.get("delta_vs_bh"),
                "Reference_Delta_vs_BH": ref.get("delta_vs_bh"),
                "Marginal_Delta": ((metrics.get("delta_vs_bh") or 0.0) - (ref.get("delta_vs_bh") or 0.0)),
                "Gain_Pct": metrics.get("gain_pct"), "Trades": metrics.get("trades"),
                "TIM_Pct": metrics.get("tim_pct"), "Max_DD_Pct": metrics.get("max_dd_pct"),
                "Pool_Sharpe": metrics.get("pool_sharpe"),
                "Behavior_Fingerprint": metrics.get("behavior_fingerprint"),
                "Reference_Fingerprint": ref.get("behavior_fingerprint"),
                "Execution_Valid": metrics.get("execution_valid"),
                "Execution_Invalid_Reason": metrics.get("execution_invalid_reason"),
                "Pilot_Eligible": metrics.get("pilot_eligible"),
                "Pilot_Ineligible_Reason": metrics.get("pilot_ineligible_reason"),
                "Applied_Overrides_JSON": json.dumps(row["applied_overrides"], sort_keys=True, separators=(",", ":"), default=str),
            })

    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    summary_out = Path(args.summary_out) if Path(args.summary_out).is_absolute() else ROOT / args.summary_out
    detail_fields = [
        "Switch", "Scope", "Scope_Reason", "Verdict", "Reason", "Groups", "Source_Sheets",
        "Declared_Quick", "Read_Quick", "Read_Wide", "Read_Scalar",
        "Read_Live_Crypto", "Read_Live_Stock", "Timeframe", "TF_Param",
        "Value", "Moves_Reference", "Delta_vs_BH", "Reference_Delta_vs_BH",
        "Marginal_Delta", "Gain_Pct", "Trades", "TIM_Pct", "Max_DD_Pct",
        "Pool_Sharpe", "Behavior_Fingerprint", "Reference_Fingerprint",
        "Execution_Valid", "Execution_Invalid_Reason",
        "Pilot_Eligible", "Pilot_Ineligible_Reason", "Applied_Overrides_JSON",
    ]
    summary_fields = [
        "Switch", "Scope", "Scope_Reason", "Verdict", "Reason", "Groups", "Source_Sheets",
        "Declared_Quick", "Read_Quick", "Read_Wide", "Read_Scalar",
        "Read_Live_Crypto", "Read_Live_Stock", "Values_Defined", "TFs_Tested",
        "Empirical_Rows", "Unique_Behaviors", "Unique_Deltas",
    ]
    write_csv(out, detail_fields, detail_rows)
    write_csv(summary_out, summary_fields, summary_rows)
    counts: dict[str, int] = defaultdict(int)
    for row in summary_rows:
        counts[row["Verdict"]] += 1
    blocking_verdicts = {
        "UNDECLARED", "REVIEW", "INERT", "TF_ALIAS", "VALUE_ALIAS", "SIDE_NA",
        "INVALID", "ERROR",
    }
    complete = not any(counts.get(verdict, 0) for verdict in blocking_verdicts)
    strategy_total = sum(1 for scope, _reason in scopes.values() if scope == "STRATEGY_CAUSAL")
    status = {
        "status": "complete_pass" if complete else "complete_blocked",
        "pilot_admissible": complete, "symside": args.symside,
        "window_days": args.window_days, "audit_tfs": list(AUDIT_TFS),
        "switches_total": len(specs),
        "strategy_switches_total": strategy_total,
        "strategy_switches_pass": counts.get("PASS", 0),
        "counts": dict(counts), "detail_csv": str(out),
        "summary_csv": str(summary_out), "elapsed_s": time.time() - started,
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
