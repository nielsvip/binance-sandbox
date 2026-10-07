#!/usr/bin/env python3
"""Read-only reporting for the separate vector scalar-gap evidence lane.

Vector rows are diagnostic hypotheses. They never enter exact ENGINE
completion, ranking, persisted matrix cells, or promotion. VEC_APPROX may be
rendered as an amber/italic overlay in otherwise blank workbook cells, while
the underlying exact matrix remains blank. This module validates that
contract, filters retired name adapters, and provides one shared payload for
the XLS and digest surfaces.
"""
from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COVERAGE_REL = Path("data/reports/VECTOR_SCALAR_GAP_COVERAGE_20260731.json")
PARITY_REL = Path("data/reports/VECTOR_ADAPTER_PARITY_AUDIT_20260731.json")
DETAIL_REL = Path("data/reports/vectorized_scalar_gap")
ALLOWED_STATUSES = {"MOVED", "INERT", "ZERO_TRADE"}
EVIDENCE_CLASSES = {"VEC_NATIVE", "VEC_PARITY", "VEC_APPROX"}
APPROX_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
DISPLAY_CONTRACTS = {
    "SEPARATE_AMBER_ITALIC",
    "SEPARATE_AMBER_ITALIC_WITH_MAIN_BLANK_OVERLAY",
}
NON_SCALAR_PARAMS = {"GROUP_COMBO", "STOP_PACK", "TF_EXCLUDE"}


def norm_value(value: Any) -> str:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = value
        value = decoded
    text = str(value).strip()
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return "true"
    if low in {"false", "no", "off"}:
        return "false"
    if low in {"none", "null", ""}:
        return "none"
    try:
        number = float(text)
        if not math.isfinite(number):
            return low
        return str(int(number)) if number == int(number) else str(number)
    except (TypeError, ValueError, OverflowError):
        return low


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def _contract_ok(payload: dict[str, Any]) -> bool:
    return (
        payload.get("tier") == "VEC_DIAGNOSTIC"
        and payload.get("exact_completion_credit") is False
        and payload.get("promotion_allowed") is False
    )


def _detail_contract_ok(payload: dict[str, Any]) -> bool:
    if payload.get("vector_evidence_class") == "VEC_APPROX":
        return (
            payload.get("tier") == "VEC_APPROX"
            and payload.get("exact_completion_credit") is False
            and payload.get("engine_ranking_allowed") is False
            and payload.get("promotion_allowed") is False
            and payload.get("live_config_write_allowed") is False
            and payload.get("db_engine_write_allowed") is False
        )
    return _contract_ok(payload)


def _normalise_detail_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten raw VEC_APPROX runner rows into the reporting contract."""
    clean = dict(row)
    # The resume-safe scalar gap filler writes an intentionally sparse
    # VEC_DIAGNOSTIC receipt.  Its reviewed name adapters are causal vector
    # hypotheses, but the workbook renderer only accepts the fully isolated
    # VEC_APPROX display contract.  Canonicalise *only* that named campaign
    # and only rows carrying an explicit reviewed adapter.  Native/general
    # VEC_DIAGNOSTIC rows retain their stronger VEC_NATIVE/VEC_PARITY class
    # in the coverage report and are not silently widened into this overlay.
    if (
        clean.get("campaign") == "stocks_repaired_20260730_c5_1yr_vec_gap"
        and clean.get("tier") == "VEC_DIAGNOSTIC"
        and clean.get("vector_adapter")
        and clean.get("exact_completion_credit") is False
        and clean.get("promotion_allowed") is False
    ):
        clean.update(
            {
                "source_tier": "VEC_DIAGNOSTIC",
                "tier": "VEC_APPROX",
                "vector_evidence_class": "VEC_APPROX",
                "approximation_confidence": "HIGH",
                "approximation_mismatch_class": (
                    "REVIEWED_EXPLICIT_VECTOR_ADAPTER"
                ),
                "source_group": "SCALAR_CAUSAL_ADAPTER",
                "action_group": "SCALAR_CAUSAL_ADAPTER",
                "source_rank": 1.0,
                "proxy_field": str(clean["vector_adapter"]),
                "proxy_value": clean.get("value_json"),
                "required_next_stage": "EXACT_V8",
                "engine_ranking_allowed": False,
                "db_engine_write_allowed": False,
                "live_config_write_allowed": False,
            }
        )
    if clean.get("vector_evidence_class") != "VEC_APPROX":
        return clean
    approximation = clean.get("approximation")
    if not isinstance(approximation, dict):
        approximation = {}
    for field in (
        "approximation_confidence",
        "approximation_mismatch_class",
        "source_group",
        "action_group",
        "source_live_readers",
        "source_exact_readers",
        "vector_target_fields",
        "approximation_formula",
        "limitations",
        "source_rank",
        "proxy_field",
        "proxy_value",
        "required_next_stage",
        "exact_completion_credit",
        "engine_ranking_allowed",
        "promotion_allowed",
        "live_config_write_allowed",
    ):
        if field not in clean and field in approximation:
            clean[field] = approximation[field]
    aliases = {
        "approximation_confidence": "confidence",
        "approximation_mismatch_class": "mismatch_class",
        "approximation_formula": "formula",
    }
    for target, source in aliases.items():
        if target not in clean and source in clean:
            clean[target] = clean[source]
    if not clean.get("proxy_field"):
        target_fields = (
            clean.get("vector_target_fields")
            or approximation.get("vector_target_fields")
            or []
        )
        proxy_overrides = (
            clean.get("proxy_overrides")
            or approximation.get("proxy_overrides")
            or {}
        )
        if target_fields:
            clean["proxy_field"] = ",".join(
                sorted(str(item) for item in target_fields)
            )
        elif isinstance(proxy_overrides, dict) and proxy_overrides:
            clean["proxy_field"] = ",".join(
                sorted(str(item) for item in proxy_overrides)
            )
        elif clean.get("proxy_kind"):
            clean["proxy_field"] = clean["proxy_kind"]
    metric_aliases = {
        "gain_per_mo_diagnostic": "gain_per_mo_approx",
        "delta_gain_mo_vs_bh_diagnostic": "delta_gain_mo_vs_bh_approx",
        "single_key_trade_sharpe_diagnostic":
            "single_key_trade_sharpe_approx",
        "max_dd_pct": "max_dd_pct_approx",
    }
    for target, source in metric_aliases.items():
        if target not in clean and source in clean:
            clean[target] = clean[source]
    return clean


def _evidence_class(
    row: dict[str, Any], approved: set[str]
) -> tuple[str | None, str | None]:
    declared = row.get("vector_evidence_class")
    param = str(row.get("param") or "")
    adapter = row.get("vector_adapter")
    if declared is None:
        declared = "VEC_PARITY" if adapter else "VEC_NATIVE"
    if declared not in EVIDENCE_CLASSES:
        return None, "unknown_evidence_class"
    if declared == "VEC_NATIVE" and adapter:
        return None, "native_row_has_adapter"
    if declared == "VEC_PARITY" and (not adapter or param not in approved):
        return None, "parity_rejected_adapter"
    if declared == "VEC_APPROX":
        confidence = str(row.get("approximation_confidence") or "").upper()
        mismatch = str(
            row.get("approximation_mismatch_class") or ""
        )
        try:
            source_rank = float(row.get("source_rank"))
        except (TypeError, ValueError):
            source_rank = float("nan")
        if confidence not in APPROX_CONFIDENCE:
            return None, "approx_confidence_missing"
        if not mismatch:
            return None, "approx_mismatch_class_missing"
        if not math.isfinite(source_rank) or not 0.0 <= source_rank <= 1.0:
            return None, "approx_source_rank_invalid"
        if not row.get("proxy_field") or "proxy_value" not in row:
            return None, "approx_proxy_mapping_missing"
        if row.get("required_next_stage") not in {
            "EXACT_V8",
            "PROVISIONAL_AMBER",
            "NONE_UNLESS_THRESHOLD_MET",
        }:
            return None, "approx_exact_stage_missing"
        if row.get("engine_ranking_allowed") is not False:
            return None, "approx_engine_ranking_not_false"
        if row.get("live_config_write_allowed") is not False:
            return None, "approx_live_config_write_not_false"
        if row.get("db_engine_write_allowed") is not False:
            return None, "approx_db_engine_write_not_false"
    return str(declared), None


def load(base: Path | str = ROOT) -> dict[str, Any]:
    """Return validated snapshot rows and coverage; fail closed on drift."""
    base = Path(base)
    coverage_path = base / COVERAGE_REL
    parity_path = base / PARITY_REL
    if not coverage_path.is_file():
        return {
            "available": False,
            "reason": f"missing {coverage_path}",
            "rows": [],
            "per_key": {},
        }
    try:
        coverage = _read_json(coverage_path)
        parity = _read_json(parity_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "rows": [],
            "per_key": {},
        }
    if (
        not _contract_ok(coverage)
        or coverage.get("engine_ranking_allowed") is not False
        or coverage.get("display_contract") not in DISPLAY_CONTRACTS
    ):
        return {
            "available": False,
            "reason": "coverage receipt violates VEC_DIAGNOSTIC isolation",
            "rows": [],
            "per_key": {},
        }
    approved = set(coverage.get("approved_vector_adapters") or [])
    if approved != set(parity.get("approved_params") or []):
        return {
            "available": False,
            "reason": "coverage/parity approved-adapter mismatch",
            "rows": [],
            "per_key": {},
        }

    snapshot_at = str(coverage.get("generated_at") or "")
    rows: list[dict[str, Any]] = []
    per_key: dict[str, dict[str, Any]] = {}
    excluded = Counter()
    for key, expected in sorted((coverage.get("per_key") or {}).items()):
        source_values = expected.get("sources") or [expected.get("source")]
        detail_paths = []
        for source in source_values:
            if not source:
                continue
            candidate = Path(str(source))
            if candidate.is_absolute():
                candidate = base / DETAIL_REL / candidate.name
            else:
                candidate = base / candidate
            if candidate not in detail_paths:
                detail_paths.append(candidate)
        if not detail_paths:
            detail_paths = [base / DETAIL_REL / f"{key}.jsonl"]
        missing_paths = [path for path in detail_paths if not path.is_file()]
        if missing_paths:
            return {
                "available": False,
                "reason": f"missing detail ledger(s) {missing_paths}",
                "rows": [],
                "per_key": {},
            }
        latest: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        for detail_path in detail_paths:
            with detail_path.open() as handle:
                detail_lines = list(handle)
            for line in detail_lines:
                try:
                    row = _normalise_detail_row(json.loads(line))
                except json.JSONDecodeError:
                    excluded["invalid_json"] += 1
                    continue
                if str(row.get("generated_at") or "") > snapshot_at:
                    excluded["after_coverage_snapshot"] += 1
                    continue
                if row.get("key") != key:
                    excluded["key_mismatch"] += 1
                    continue
                if row.get("status") not in ALLOWED_STATUSES:
                    excluded["failed_or_unknown_status"] += 1
                    continue
                if not _detail_contract_ok(row):
                    excluded["contract_violation"] += 1
                    continue
                param = str(row.get("param") or "")
                if param in NON_SCALAR_PARAMS:
                    excluded["non_scalar"] += 1
                    continue
                if row.get("protected_exact_replay_diagnostic") is True:
                    excluded["protected_parity_only"] += 1
                    continue
                if row.get("protected_exact_present") is True:
                    excluded["protected_exact_present"] += 1
                    continue
                evidence_class, class_error = _evidence_class(row, approved)
                if class_error:
                    excluded[class_error] += 1
                    continue
                clean = dict(row)
                clean["vector_evidence_class"] = evidence_class
                logical = (param, norm_value(row.get("value_json")))
                priority = (
                    2
                    if evidence_class in {"VEC_NATIVE", "VEC_PARITY"}
                    else 1
                )
                previous = latest.get(logical)
                if previous is None or priority >= previous[0]:
                    latest[logical] = (priority, clean)
        accepted = [item[1] for item in latest.values()]
        counts = Counter(row["status"].lower() for row in accepted)
        class_counts = Counter(
            row["vector_evidence_class"] for row in accepted
        )
        confidence_counts = Counter(
            str(row.get("approximation_confidence") or "").upper()
            for row in accepted
            if row["vector_evidence_class"] == "VEC_APPROX"
        )
        mismatch_counts = Counter(
            str(row.get("approximation_mismatch_class") or "")
            for row in accepted
            if row["vector_evidence_class"] == "VEC_APPROX"
        )
        native_n = class_counts["VEC_NATIVE"]
        adapter_n = class_counts["VEC_PARITY"]
        approx_moved = sum(
            row["vector_evidence_class"] == "VEC_APPROX"
            and row["status"] == "MOVED"
            for row in accepted
        )
        exact_replay_priority = sum(
            row["status"] == "MOVED" for row in accepted
        )
        observed = {
            "screened_cells": len(accepted),
            "moved": counts["moved"],
            "inert": counts["inert"],
            "zero_trade": counts["zero_trade"],
            "native_vector_cells": native_n,
            "sampled_exact_parity_adapter_cells": adapter_n,
            "vec_native_cells": native_n,
            "vec_parity_cells": adapter_n,
            "vec_approx_cells": class_counts["VEC_APPROX"],
            "vec_approx_moved": approx_moved,
            "exact_replay_priority_cells": exact_replay_priority,
        }
        mismatch = {
            field: (expected.get(field), value)
            for field, value in observed.items()
            if field in expected
            and int(expected.get(field) or 0) != int(value)
        }
        expected_confidence = expected.get("approximation_confidence") or {}
        if expected_confidence and {
            key: int(value) for key, value in expected_confidence.items()
        } != dict(confidence_counts):
            mismatch["approximation_confidence"] = (
                expected_confidence,
                dict(confidence_counts),
            )
        expected_mismatch = (
            expected.get("approximation_mismatch_classes") or {}
        )
        if expected_mismatch and {
            key: int(value) for key, value in expected_mismatch.items()
        } != dict(mismatch_counts):
            mismatch["approximation_mismatch_classes"] = (
                expected_mismatch,
                dict(mismatch_counts),
            )
        if mismatch:
            return {
                "available": False,
                "reason": f"{key} detail/coverage mismatch: {mismatch}",
                "rows": [],
                "per_key": {},
            }
        for row in accepted:
            clean = dict(row)
            clean["display_contract"] = coverage.get("display_contract")
            clean["engine_ranking_allowed"] = False
            clean["db_engine_write_allowed"] = False
            clean["live_config_write_allowed"] = False
            rows.append(clean)
        per_key[key] = {
            **expected,
            **observed,
            "approximation_confidence": dict(confidence_counts),
            "approximation_mismatch_classes": dict(mismatch_counts),
        }
    if len(rows) != int(coverage.get("screened_cells") or 0):
        return {
            "available": False,
            "reason": (
                "detail total does not match coverage receipt: "
                f"{len(rows)} != {coverage.get('screened_cells')}"
            ),
            "rows": [],
            "per_key": {},
        }
    class_totals = Counter(row["vector_evidence_class"] for row in rows)
    for evidence_class, receipt_field in (
        ("VEC_NATIVE", "vec_native_cells"),
        ("VEC_PARITY", "vec_parity_cells"),
        ("VEC_APPROX", "vec_approx_cells"),
    ):
        if (
            receipt_field in coverage
            and int(coverage.get(receipt_field) or 0)
            != class_totals[evidence_class]
        ):
            return {
                "available": False,
                "reason": (
                    f"top-level {receipt_field} mismatch: "
                    f"{coverage.get(receipt_field)} != "
                    f"{class_totals[evidence_class]}"
                ),
                "rows": [],
                "per_key": {},
            }
    return {
        "available": True,
        "generated_at": coverage.get("generated_at"),
        "tier": "VEC_DIAGNOSTIC",
        "display_contract": coverage.get("display_contract"),
        "exact_completion_credit": False,
        "promotion_allowed": False,
        "engine_ranking_allowed": False,
        "db_engine_write_allowed": False,
        "live_config_write_allowed": False,
        "approved_vector_adapters": sorted(approved),
        "rejected_vector_adapters": sorted(
            parity.get("rejected_params") or []
        ),
        "adapter_parity": parity.get("per_param") or {},
        "screened_cells": len(rows),
        "rows": sorted(
            rows,
            key=lambda row: (
                str(row.get("key") or ""),
                str(row.get("param") or ""),
                norm_value(row.get("value_json")),
            ),
        ),
        "per_key": per_key,
        "excluded_detail_rows": dict(sorted(excluded.items())),
        "coverage_path": str(coverage_path),
        "parity_path": str(parity_path),
    }


def scalar_grid_mapping(
    base: Path | str = ROOT,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain the 346-of-~1609 fast-screen boundary without credit inflation."""
    base = Path(base)
    payload = payload or load(base)
    mapping: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "tier": "VEC_DIAGNOSTIC",
        "exact_completion_credit": False,
        "promotion_allowed": False,
        "engine_ranking_allowed": False,
        "source_available": bool(payload.get("available")),
        "source_reason": payload.get("reason"),
        "approved_vector_adapters": payload.get(
            "approved_vector_adapters", []
        ),
        "per_key": {},
    }
    if not payload.get("available"):
        return mapping
    denominators: dict[str, int] = {}
    differential_denominators: dict[str, int] = {}
    plateau_denominators: dict[str, int] = {}
    category_counts: dict[str, Counter] = {}
    try:
        try:
            from tools import matrix_guard
        except ModuleNotFoundError:
            import matrix_guard  # type: ignore
        header, matrix_rows = matrix_guard.load()
        contract = matrix_guard.load_uniqueness_contract()
        if contract is None:
            raise RuntimeError("matrix uniqueness contract unavailable")
        active_keys = set(contract["tim_policy"]["keys"])
        for key in payload["per_key"]:
            counts = Counter(
                matrix_guard.cell_contract_category(record, key, active_keys)
                for record in contract["rows"]
            )
            category_counts[key] = counts
            differential_denominators[key] = sum(
                counts[name]
                for name in matrix_guard.ACTIONABLE_CATEGORIES
            )
            plateau_denominators[key] = counts[
                "PLATEAU_CROSS_KEY_PROBE"
            ]
            denominators[key] = (
                differential_denominators[key]
                + plateau_denominators[key]
            )
    except Exception as exc:
        mapping["denominator_error"] = f"{type(exc).__name__}: {exc}"

    detail_root = base / DETAIL_REL
    for key, row in payload["per_key"].items():
        summary_path = detail_root / f"{key}_summary.json"
        try:
            summary = _read_json(summary_path)
        except (OSError, json.JSONDecodeError, ValueError):
            summary = {}
        eligible = int(summary.get("eligible_vector_cells") or 0)
        executable = denominators.get(key)
        rejected_adapter_cells = sum(
            int((payload.get("adapter_parity") or {}).get(param, {}).get(
                "compared", 0
            ) or 0)
            for param in payload.get("rejected_vector_adapters") or []
        )
        pre_parity_candidates = eligible + rejected_adapter_cells
        native_eligible = max(
            0,
            eligible
            - len(payload.get("approved_vector_adapters") or []) * 5,
        )
        exact_only = (
            max(0, executable - eligible)
            if executable is not None
            else None
        )
        mapping["per_key"][key] = {
            "executable_scalar_cells": executable,
            "differential_actionable_cells": differential_denominators.get(
                key
            ),
            "plateau_cross_key_cells": plateau_denominators.get(key),
            "pre_parity_vector_candidate_cells": pre_parity_candidates,
            "parity_rejected_adapter_cells": rejected_adapter_cells,
            "eligible_vector_cells": eligible,
            "native_vector_eligible_cells": native_eligible,
            "approved_adapter_eligible_cells": eligible - native_eligible,
            "not_vector_eligible_requires_exact": exact_only,
            "protected_exact_cells_at_screen_time": int(
                summary.get("protected_exact_cells") or 0
            ),
            "receipt_valid_vector_diagnostic_cells": int(
                row.get("screened_cells") or 0
            ),
            "vec_native_cells": int(row.get("vec_native_cells") or 0),
            "vec_parity_cells": int(row.get("vec_parity_cells") or 0),
            "vec_approx_cells": int(row.get("vec_approx_cells") or 0),
            "vec_approx_moved": int(row.get("vec_approx_moved") or 0),
            "approximation_confidence": row.get(
                "approximation_confidence"
            ) or {},
            "approximation_mismatch_classes": row.get(
                "approximation_mismatch_classes"
            ) or {},
            "exact_replay_priority_cells": int(
                row.get("exact_replay_priority_cells") or 0
            ),
            "moved": int(row.get("moved") or 0),
            "inert": int(row.get("inert") or 0),
            "zero_trade": int(row.get("zero_trade") or 0),
            "contract_categories": dict(
                sorted(category_counts.get(key, Counter()).items())
            ),
            "why_not_all_scalar_cells": (
                "Only reviewed native VECTOR_DIAGNOSTIC+EXACT_V8 fields and "
                "three sampled-parity EMA200 aliases are executable in the "
                "parity-preserving fast vector model. VEC_APPROX expands "
                "discovery visibility through explicitly mismatched proxies "
                "but grants no exact completion or ranking authority. The old "
                "346 count included 15 values from "
                "three EMA200 aliases that failed sampled exact parity; the "
                "approved count is now 331. All other executable scalar cells "
                "remain exact-only, exact binding, or plateau cross-key probes."
            ),
        }
    mapping["screened_cells"] = payload["screened_cells"]
    mapping["excluded_detail_rows"] = payload.get(
        "excluded_detail_rows", {}
    )
    return mapping


def html_section(base: Path | str = ROOT) -> str:
    payload = load(base)
    if not payload.get("available"):
        return (
            "<div class='box'><h3>VEC scalar-gap diagnostic</h3>"
            "<p class='r'>Unavailable; no ENGINE credit: %s</p></div>"
            % html.escape(str(payload.get("reason") or "unknown"))
        )
    rows = []
    for key, item in payload["per_key"].items():
        confidence = item.get("approximation_confidence") or {}
        mismatch = item.get("approximation_mismatch_classes") or {}
        mismatch_text = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(
                mismatch.items(), key=lambda pair: (-pair[1], pair[0])
            )[:2]
        )
        rows.append(
            f"<tr><td>{html.escape(key)}</td>"
            f"<td>{int(item.get('screened_cells') or 0)}</td>"
            f"<td>{int(item.get('moved') or 0)}</td>"
            f"<td>{int(item.get('inert') or 0)}</td>"
            f"<td>{int(item.get('zero_trade') or 0)}</td>"
            f"<td>{int(item.get('vec_native_cells') or 0)}</td>"
            f"<td>{int(item.get('vec_parity_cells') or 0)}</td>"
            f"<td>{int(item.get('vec_approx_cells') or 0)}</td>"
            f"<td>{int(confidence.get('HIGH') or 0)}/"
            f"{int(confidence.get('MEDIUM') or 0)}/"
            f"{int(confidence.get('LOW') or 0)}</td>"
            f"<td>{html.escape(mismatch_text or '—')}</td>"
            f"<td>{int(item.get('exact_replay_priority_cells') or 0)}</td>"
            "</tr>"
        )
    return (
        "<div class='box' style='color:#9a6b00;font-style:italic'>"
        "<h3>VEC scalar-gap diagnostic — separate, nonpromotable</h3>"
        "<p><b>%d</b> screened hypotheses as of %s. "
        "<b>Exact completion credit: none.</b> ENGINE/database writes, live "
        "config writes, ranking, and promotion are forbidden; MOVED means "
        "queue for exact replay, not a winner.</p>"
        "<table><tr><th>key</th><th>screened</th><th>moved</th>"
        "<th>inert</th><th>zero trade</th><th>VEC_NATIVE</th>"
        "<th>VEC_PARITY</th><th>VEC_APPROX</th>"
        "<th>approx H/M/L</th><th>approx mismatch classes</th>"
        "<th>exact replay priority</th></tr>%s</table></div>"
        % (
            int(payload["screened_cells"]),
            html.escape(str(payload.get("generated_at") or "unknown")),
            "".join(rows),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(
            "data/reports/VECTOR_SCALAR_GRID_MAPPING_20260731.json"
        ),
    )
    parser.add_argument(
        "--md-output",
        type=Path,
        default=Path(
            "data/reports/VECTOR_SCALAR_GRID_MAPPING_20260731.md"
        ),
    )
    args = parser.parse_args()
    payload = load(args.root)
    mapping = scalar_grid_mapping(args.root, payload)
    json_output = (
        args.json_output
        if args.json_output.is_absolute()
        else args.root / args.json_output
    )
    md_output = (
        args.md_output
        if args.md_output.is_absolute()
        else args.root / args.md_output
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Vector scalar-grid mapping — 2026-07-31",
        "",
        "> VEC_DIAGNOSTIC only. Exact completion credit, ENGINE ranking, and "
        "promotion are all false.",
        "",
        "| key | executable scalar | old vector candidates | parity rejected "
        "| approved parity-eligible | native | parity | approx discovery "
        "| approx H/M/L | exact replay priority | requires exact "
        "| screened receipt | moved | inert | zero |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, row in mapping["per_key"].items():
        lines.append(
            f"| {key} | {row.get('executable_scalar_cells') or '—'} | "
            f"{row['pre_parity_vector_candidate_cells']} | "
            f"{row['parity_rejected_adapter_cells']} | "
            f"{row['eligible_vector_cells']} | "
            f"{row['vec_native_cells']} | "
            f"{row['vec_parity_cells']} | "
            f"{row['vec_approx_cells']} | "
            f"{int(row['approximation_confidence'].get('HIGH') or 0)}/"
            f"{int(row['approximation_confidence'].get('MEDIUM') or 0)}/"
            f"{int(row['approximation_confidence'].get('LOW') or 0)} | "
            f"{row['exact_replay_priority_cells']} | "
            f"{row.get('not_vector_eligible_requires_exact') if row.get('not_vector_eligible_requires_exact') is not None else '—'} | "
            f"{row['receipt_valid_vector_diagnostic_cells']} | "
            f"{row['moved']} | {row['inert']} | {row['zero_trade']} |"
        )
    lines += [
        "",
        "The original 346 vector-candidate count was 316 native values plus 30 "
        "values from six EMA200 aliases. Sampled exact parity rejected all 15 "
        "values of T1_MULT, T1_PCT and T3_MULT, leaving 331 approved vector "
        "cells: 316 native plus 15 values from T2_MULT, T2_PCT and T3_PCT. "
        "VEC_APPROX cells are separately counted discovery proxies, with "
        "confidence and mismatch recorded per row; they do not reduce the "
        "requires-exact denominator. "
        "The classification-aware executable denominator is 1,609 for LONG "
        "and 1,600 for SHORT after side applicability; it also includes "
        "exact-only and plateau cross-key work. Grouped combinations, packs "
        "and TF helpers are not scalar cells.",
        "",
    ]
    md_output.write_text("\n".join(lines))
    print(json.dumps({"available": payload.get("available"), "screened": payload.get("screened_cells"), "json": str(json_output), "md": str(md_output)}))
    return 0 if payload.get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
