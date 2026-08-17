#!/usr/bin/env python3
"""Build a compact, truthful progress digest for SWITCH_MATRIX_TRB.

This is a monitoring report, never a promotion signal.  It reads the same SQLite store as
``export_switch_matrix_xls.py`` and highlights the three current pilot keys, matrix coverage,
campaign freshness, and ladder/entry/exit interaction results.  Compute suspension must not
suspend this report.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from tools.stock_capacity_contract import (
        is_expected_stock_capacity_saturation,
    )
    from tools.trb_tim_contract import (
        ranked_symbols,
        tim_band_for_key,
        tim_contract,
        tim_in_band,
    )
except ModuleNotFoundError:
    from stock_capacity_contract import is_expected_stock_capacity_saturation
    from trb_tim_contract import (
        ranked_symbols,
        tim_band_for_key,
        tim_contract,
        tim_in_band,
    )

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "param_results_stocks.db"
REPORTS = BASE / "data" / "reports"
DEFAULT_KEYS = (
    "MU_LONG",
    "NVDA_LONG",
    "VT_LONG",
    "TTD_SHORT",
    "ACN_SHORT",
    "LAC_SHORT",
)


def _set_local_immutable(path: Path, enabled: bool) -> None:
    import stat
    import sys

    if sys.platform != "darwin" or not path.exists():
        return
    flag = getattr(stat, "UF_IMMUTABLE", 0)
    if flag:
        current = path.stat().st_flags
        os.chflags(path, (current | flag) if enabled else (current & ~flag))
CURRENT_ENGINE_CAMPAIGN = "stocks_repaired_20260730_c5"
CURRENT_ENGINE_CUTOFF = "2026-07-30T03:30:10Z"
PROVISIONAL_ACTIONABLE_CATEGORIES = frozenset(
    {
        "ACTIONABLE_EXACT_ONLY",
        "ACTIONABLE_VECTOR_THEN_EXACT",
        "ACTIONABLE_BINDING_PROBE",
    }
)


def current_contract_fingerprints(keys: tuple[str, ...]) -> dict[str, set[str]]:
    """Admissible exact code+NPZ+side fingerprints for each key."""
    try:
        try:
            from tools import persym_baseline_campaign as psc
        except ModuleNotFoundError:
            # Direct ``python tools/switch_matrix_digest.py`` puts tools/, not its parent,
            # on sys.path.
            import persym_baseline_campaign as psc

        return {
            key: psc.matrix_contract_fingerprints(*parse_key(key))
            for key in keys
        }
    except Exception:
        # Reporting must still render when a symbol's frozen NPZ is absent on
        # this checkout (a common Mac/S1 split-brain condition).  Empty
        # allowlists deliberately produce no exact rows; the digest exposes
        # the unavailable contract through its zero-current-row/freshness
        # fields instead of crashing and emitting no email at all.
        return {key: set() for key in keys}


def norm_val(value) -> str:
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
        if number != number or number in (float("inf"), float("-inf")):
            return low
        return str(int(number)) if number == int(number) else str(number)
    except (TypeError, ValueError, OverflowError):
        return low


def manifest_rows() -> set[tuple[str, str]]:
    path = BASE / "data" / "param_sweep_manifest_tradier.json"
    if not path.exists():
        return set()
    params = json.loads(path.read_text()).get("params", {})
    rows: set[tuple[str, str]] = set()
    for name, spec in params.items():
        if isinstance(spec, dict) and not bool(spec.get("sweepable")):
            continue
        if isinstance(spec, dict):
            values = None
            for field in ("test_values", "values", "sweep_values", "range"):
                if isinstance(spec.get(field), (list, tuple)) and spec[field]:
                    values = list(spec[field])
                    break
            if values is None:
                values = [spec.get("default")]
        elif isinstance(spec, (list, tuple)):
            values = list(spec)
        else:
            values = [spec]
        for value in values:
            if "OPTION" not in name:
                rows.add((name, norm_val(value)))
    return rows


def parse_key(key: str) -> tuple[str, str]:
    symbol, side = key.rsplit("_", 1)
    return symbol.upper(), side.upper()


def fmt(value, digits=3, suffix="") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def historical_integration_section() -> list[str]:
    """Render the provenance-bound BASELINE_V2 sidecar in the digest.

    This makes useful pre-7/25 observations visible without allowing them to
    masquerade as current c5 ENGINE rows or promotion candidates.
    """
    path = REPORTS / "SWITCH_MATRIX_TRB_HISTORICAL_INTEGRATED.jsonl"
    if not path.is_file():
        return ["## Historical BASELINE_V2_S4H integration", "", "Sidecar not generated.", ""]
    rows = []
    try:
        with path.open() as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") == "OK" and float(row.get("delta_gain_mo_vs_bh_historical") or 0) > 0:
                    rows.append(row)
    except OSError:
        rows = []
    lines = [
        "## Historical BASELINE_V2_S4H integration",
        "",
        f"{len(rows):,} positive historical OK logical cells are visible from the provenance sidecar. "
        "They fill only blank canonical cells, retain the original source hash, and remain excluded from exact completion, current ranking, and promotion.",
        "",
        "| key | path/value | historical Δ/mo vs B&H | provenance |",
        "|---|---|---:|---|",
    ]
    for row in rows[:40]:
        lines.append(
            f"| {row.get('key') or '—'} | `{row.get('main_switch') or ''}/{row.get('sub_setting') or ''}={row.get('value_json') or ''}` | "
            f"{float(row.get('delta_gain_mo_vs_bh_historical') or 0):+.4f} | HISTORICAL_BASELINE_V2 (not current) |"
        )
    lines.append("")
    return lines


def iso_age(ts: str | None, now: datetime) -> str:
    if not ts:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        seconds = max(0, int((now - parsed).total_seconds()))
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        return f"{seconds / 86400:.1f}d"
    except ValueError:
        return "unknown"


def strategy_where() -> str:
    return """(
        campaign LIKE '%ladder%' OR campaign LIKE '%combo%' OR
        param IN ('STOP_PACK','TF_EXCLUDE') OR
        param LIKE '%ENTRY%' OR param LIKE '%EXIT%' OR param LIKE '%REENTRY%' OR
        param LIKE '%LADDER%' OR param LIKE 'GR_%' OR param LIKE 'MTF_%' OR
        param LIKE 'WT_3M_FORCE_OPEN%' OR
        source_file LIKE 'exposure_ladder/%' OR
        source_file LIKE 'band_ladder_sweep/%' OR
        source_file LIKE 'combo_search/%' OR
        source_file LIKE '%interaction%'
    )"""


def load_classified_pilot_coverage(
    keys: tuple[str, ...],
) -> dict:
    """Use matrix_guard's disjoint executable-cell contract.

    The manifest's raw 3,267 rows include intentional controls, helpers,
    wrong-account knobs, no-live-reader rows, and opposite-side cells.  It is
    not an actionable per-key denominator.
    """
    try:
        try:
            from tools import matrix_guard
        except ModuleNotFoundError:
            import matrix_guard  # type: ignore
        header, rows = matrix_guard.load()
        contract = matrix_guard.load_uniqueness_contract()
        if contract is None:
            raise RuntimeError("matrix uniqueness contract unavailable")
        active_keys = list(contract["tim_policy"]["keys"])
        columns = header[12:]
        missing = sorted(set(keys) - set(columns))
        if missing:
            raise RuntimeError(
                "digest pilot columns missing: " + ",".join(missing)
            )
        # Match the canonical guard exactly.  Physical CSV values can include
        # preserved/recovered rows that lack the current receipt and capital
        # contract; they are display evidence, never exact-completion credit.
        progress = matrix_guard.classified_progress(
            header,
            rows,
            contract["rows"],
            active_keys,
            filled_logical_cells=matrix_guard.receipt_validated_scalar_cells(),
        )
        result = {
            "available": True,
            "matrix_rows": len(rows),
            "active_keys": len(active_keys),
            "keys": {},
        }
        for key in keys:
            pilot = progress["pilot"][key]
            counts = {
                "differential_filled": int(pilot["filled"]),
                "differential_empty": int(pilot["empty"]),
                "plateau_filled": int(pilot["plateau_filled"]),
                "plateau_empty": int(pilot["plateau_empty"]),
            }
            result["keys"][key] = counts
        return result
    except Exception as exc:
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "keys": {},
        }


_TIM_FAILURE = re.compile(
    r"(?:^|:)tim_not_(-?[0-9]+(?:\.[0-9]+)?)_"
    r"(-?[0-9]+(?:\.[0-9]+)?)$"
)


def receipt_tim_contract_state(receipt: dict, key: str) -> str:
    """CURRENT, HISTORICAL, or UNBOUND for one vector receipt TIM gate."""
    expected = tuple(float(value) for value in tim_band_for_key(key))
    gates = (
        ((receipt.get("preflight_identity") or {}).get("payload") or {}).get(
            "gates"
        )
        or {}
    )
    if "tim_min" in gates and "tim_max" in gates:
        try:
            observed = (float(gates["tim_min"]), float(gates["tim_max"]))
        except (TypeError, ValueError):
            return "UNBOUND"
        return "CURRENT" if observed == expected else "HISTORICAL"
    # Older isolated lanes did not persist preflight identity. Their rejection
    # reason is the only auditable gate identity.
    observed_failures = []
    for failure in receipt.get("failures") or []:
        match = _TIM_FAILURE.search(str(failure))
        if match:
            observed_failures.append(
                (float(match.group(1)), float(match.group(2)))
            )
    if observed_failures:
        return (
            "CURRENT"
            if all(observed == expected for observed in observed_failures)
            else "HISTORICAL"
        )
    return "UNBOUND"


def load_c4_vector_first_progress(
    keys: tuple[str, ...],
    *,
    screen_root: Path | None = None,
    runner_path: Path | None = None,
    status_path: Path | None = None,
) -> dict:
    """Load only receipts produced by today's vector-first runner source.

    Older attempts remain on disk as gray evidence.  Matching the runner hash
    prevents a prior accounting/control contract from being reported as the
    current cycle merely because it used the same bundle name.
    """
    screen_root = screen_root or (
        REPORTS / "c4_vector_bundle_screen" / "receipts"
    )
    runner_path = runner_path or (
        BASE / "tools" / "c4_vector_bundle_screen.py"
    )
    status_path = status_path or (
        REPORTS / "c4_vector_first" / "status.json"
    )
    if not runner_path.exists():
        return {"available": False, "reason": "runner missing", "keys": {}}
    runner_sha = hashlib.sha256(runner_path.read_bytes()).hexdigest()
    latest_by_bundle: dict[tuple[str, str], dict] = {}
    if screen_root.exists():
        # The runner currently emits ``attempt_c4_*.json``.  Accept any
        # attempt receipt here and let the exact runner hash below provide the
        # contract boundary; this also keeps the digest compatible with
        # deterministic test/replay attempt names.
        for path in screen_root.glob("*/*/attempt_*.json"):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if (
                (payload.get("code_contract") or {}).get("runner_sha256")
                != runner_sha
            ):
                continue
            key = str(payload.get("position_key") or "")
            bundle_id = str((payload.get("bundle") or {}).get("bundle_id") or "")
            if key not in keys or not bundle_id:
                continue
            identity = (key, bundle_id)
            if str(payload.get("finished_at") or "") >= str(
                latest_by_bundle.get(identity, {}).get("finished_at") or ""
            ):
                latest_by_bundle[identity] = payload

    try:
        status = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        status = {}
    grouped: dict[str, list[dict]] = {key: [] for key in keys}
    for (key, _bundle_id), payload in latest_by_bundle.items():
        grouped[key].append(payload)

    result = {
        "available": bool(latest_by_bundle),
        "runner_sha256": runner_sha,
        "latest": max(
            (
                str(row.get("finished_at") or "")
                for row in latest_by_bundle.values()
            ),
            default=None,
        ),
        "status": status,
        "keys": {},
    }
    for key in keys:
        all_receipts = grouped[key]
        receipts = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "CURRENT"
        ]
        historical_tim = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "HISTORICAL"
        ]
        unbound_tim = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "UNBOUND"
        ]
        tim_low, tim_high = tim_band_for_key(key)
        tim_mid = (tim_low + tim_high) / 2.0
        scored = []
        for receipt in receipts:
            folds = receipt.get("folds") or []
            if not folds:
                continue
            fold = folds[0]
            metrics = fold.get("metrics") or {}
            control = fold.get("same_fold_vec_control") or {}
            try:
                strategy_return = float(metrics["strategy_return_pct"])
                bh_return = float(metrics["bh_return_pct"])
                tim = float(metrics["tim_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            floor = max(bh_return, 0.0)
            scored.append(
                {
                    "bundle": (receipt.get("bundle") or {}).get("bundle_id"),
                    "status": receipt.get("status"),
                    "strategy_return_pct": strategy_return,
                    "bh_return_pct": bh_return,
                    "capture_vs_bh": (
                        strategy_return / bh_return if bh_return > 0 else None
                    ),
                    "same_entry_control_pct": control.get(
                        "strategy_return_pct"
                    ),
                    "tim_pct": tim,
                    "trades": metrics.get("trades"),
                    "performance_gap_to_2x_floor": (
                        strategy_return - 2.0 * floor
                    ),
                    "failures": list(receipt.get("failures") or []),
                }
            )
        scored.sort(
            key=lambda row: (
                row["performance_gap_to_2x_floor"],
                -abs(row["tim_pct"] - tim_mid),
            ),
            reverse=True,
        )
        in_tim = [
            row
            for row in scored
            if tim_low <= row["tim_pct"] <= tim_high
        ]
        result["keys"][key] = {
            "screened": len(receipts),
            "historical_tim_contract": len(historical_tim),
            "unbound_tim_contract": len(unbound_tim),
            "exact_pending": sum(
                row.get("status") == "EXACT_PENDING" for row in receipts
            ),
            "top_performance": scored[0] if scored else None,
            "best_in_tim": in_tim[0] if in_tim else None,
        }
    result["available"] = any(
        int(row.get("screened", 0) or 0) > 0
        for row in result["keys"].values()
    )
    result["historical_tim_contract_receipts"] = sum(
        int(row.get("historical_tim_contract", 0) or 0)
        for row in result["keys"].values()
    )
    result["unbound_tim_contract_receipts"] = sum(
        int(row.get("unbound_tim_contract", 0) or 0)
        for row in result["keys"].values()
    )
    return result


def load_isolated_vector_progress(
    keys: tuple[str, ...],
    *,
    screen_root: Path,
    status_path: Path,
    contract_kind: str,
    runner_path: Path | None = None,
) -> dict:
    """Summarize a sealed, non-matrix vector lane by its own contract.

    ``targeted_cycle3`` receipts bind the preregistered catalog hash; the
    short-native lane binds its named recovery-state contract. This keeps
    either lane visible in the digest without pretending it shares the safe
    runner identity or contributes ENGINE coverage.
    """
    try:
        status = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        status = {}
    catalog_sha = str(status.get("catalog_sha256") or "")
    named_contract = str(status.get("contract") or "")
    latest_by_bundle: dict[tuple[str, str], dict] = {}
    if screen_root.exists():
        for path in screen_root.glob("receipts/*/*/attempt_*.json"):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if contract_kind == "targeted_cycle3":
                observed = str(
                    (payload.get("code_contract") or {}).get(
                        "targeted_cycle3_catalog_sha256"
                    )
                    or ""
                )
                if not catalog_sha or observed != catalog_sha:
                    continue
            elif contract_kind == "short_native":
                if (
                    not named_contract
                    or str(payload.get("research_contract") or "")
                    != named_contract
                ):
                    continue
            elif contract_kind == "breakout_cycle4":
                expected = (
                    hashlib.sha256(runner_path.read_bytes()).hexdigest()
                    if runner_path is not None and runner_path.is_file()
                    else ""
                )
                observed = str(
                    (payload.get("code_contract") or {}).get(
                        "breakout_cycle4_catalog_sha256"
                    )
                    or ""
                )
                if not expected or observed != expected:
                    continue
            else:
                raise ValueError(f"unknown isolated vector contract {contract_kind}")
            key = str(payload.get("position_key") or "")
            bundle = str((payload.get("bundle") or {}).get("bundle_id") or "")
            if key not in keys or not bundle:
                continue
            identity = (key, bundle)
            if str(payload.get("finished_at") or "") >= str(
                latest_by_bundle.get(identity, {}).get("finished_at") or ""
            ):
                latest_by_bundle[identity] = payload

    result = {
        "available": bool(latest_by_bundle),
        "latest": max(
            (
                str(row.get("finished_at") or "")
                for row in latest_by_bundle.values()
            ),
            default=None,
        ),
        "status": status,
        "keys": {},
    }
    for key in keys:
        tim_low, tim_high = tim_band_for_key(key)
        tim_mid = (tim_low + tim_high) / 2.0
        all_receipts = [
            payload
            for (receipt_key, _), payload in latest_by_bundle.items()
            if receipt_key == key
        ]
        receipts = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "CURRENT"
        ]
        historical_tim = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "HISTORICAL"
        ]
        unbound_tim = [
            receipt
            for receipt in all_receipts
            if receipt_tim_contract_state(receipt, key) == "UNBOUND"
        ]
        scored = []
        for receipt in receipts:
            folds = receipt.get("folds") or []
            if not folds:
                continue
            fold = folds[0]
            metrics = fold.get("metrics") or {}
            control = fold.get("same_fold_vec_control") or {}
            try:
                strategy_return = float(metrics["strategy_return_pct"])
                bh_return = float(metrics["bh_return_pct"])
                tim = float(metrics["tim_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            scored.append(
                {
                    "bundle": (receipt.get("bundle") or {}).get("bundle_id"),
                    "status": receipt.get("status"),
                    "strategy_return_pct": strategy_return,
                    "bh_return_pct": bh_return,
                    "capture_vs_bh": (
                        strategy_return / bh_return if bh_return > 0 else None
                    ),
                    "same_entry_control_pct": control.get(
                        "strategy_return_pct"
                    ),
                    "tim_pct": tim,
                    "trades": metrics.get("trades"),
                    "performance_gap_to_2x_floor": (
                        strategy_return - 2.0 * max(bh_return, 0.0)
                    ),
                    "failures": list(receipt.get("failures") or []),
                }
            )
        scored.sort(
            key=lambda row: (
                row["performance_gap_to_2x_floor"],
                -abs(row["tim_pct"] - tim_mid),
            ),
            reverse=True,
        )
        in_tim = [
            row
            for row in scored
            if tim_low <= row["tim_pct"] <= tim_high
        ]
        result["keys"][key] = {
            "screened": len(receipts),
            "historical_tim_contract": len(historical_tim),
            "unbound_tim_contract": len(unbound_tim),
            "exact_pending": sum(
                row.get("status") == "EXACT_PENDING" for row in receipts
            ),
            "top_performance": scored[0] if scored else None,
            "best_in_tim": in_tim[0] if in_tim else None,
        }
    result["available"] = any(
        int(row.get("screened", 0) or 0) > 0
        for row in result["keys"].values()
    )
    result["historical_tim_contract_receipts"] = sum(
        int(row.get("historical_tim_contract", 0) or 0)
        for row in result["keys"].values()
    )
    result["unbound_tim_contract_receipts"] = sum(
        int(row.get("unbound_tim_contract", 0) or 0)
        for row in result["keys"].values()
    )
    return result


def format_c4_vector_candidate(row: dict | None) -> str:
    if not row:
        return "—"
    capture = row.get("capture_vs_bh")
    benchmark = (
        f"{fmt(capture, 3)}× B&H"
        if capture is not None
        else "positive cash floor required"
    )
    failures = "; ".join(
        str(value).split(":", 1)[-1]
        for value in (row.get("failures") or [])
    )
    return (
        f"`{row.get('bundle')}` "
        f"{fmt(row.get('strategy_return_pct'), 3, '%')} vs "
        f"{fmt(row.get('bh_return_pct'), 3, '%')} ({benchmark}); "
        f"control {fmt(row.get('same_entry_control_pct'), 3, '%')}; "
        f"TIM {fmt(row.get('tim_pct'), 2, '%')}; "
        f"trades {row.get('trades', '—')}; "
        f"{failures or row.get('status') or 'gray'}"
    )


def verdict(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    expected_capacity_saturation = (
        "result_audit_json" in keys
        and is_expected_stock_capacity_saturation(row["result_audit_json"])
    )
    if (
        "validation_status" in keys
        and row["validation_status"] == "PASS_WITH_CAPACITY_CLAMPS"
        and not expected_capacity_saturation
    ):
        return "RED: CAPACITY CLAMPS"
    if (
        "validation_status" in keys
        and row["validation_status"] != "PASS"
        and not expected_capacity_saturation
    ):
        return "INCOMPLETE: NO REAL CLOSE"
    if "reentry_violations" in keys and (row["reentry_violations"] or 0) > 0:
        return "INVALID REENTRY"
    if "inert" in keys and (row["inert"] or 0) == 1:
        return "INERT / RECONNECT"
    trades = row["trades"]
    gain = row["gain_per_mo"]
    delta = row["delta_gain_mo_vs_bh"]
    tim = row["time_in_mkt_pct"]
    if gain is None or delta is None:
        return "INCOMPLETE METRICS"
    if trades is None or trades < 1:
        return "ZERO-TRADE / INVALID"
    if row["param"] in {
        "DC_LOW4_STOP_ENABLED",
        "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
    }:
        if delta <= 0:
            return "GRAY: ENTRY-QUALITY FAILURE / LOSING CHURN"
        return "DIAGNOSTIC ONLY: FAILED-ENTRY FILTER"
    if delta > 0:
        position_key = f"{row['symbol']}_{row['side']}"
        tim_low, tim_high = tim_band_for_key(position_key)
        if trades <= 1 or (tim is not None and tim >= 99.5):
            return "B&H FLOOR ONLY"
        if tim is None or not tim_low <= float(tim) <= tim_high:
            return (
                f"GRAY: BEATS B&H OUTSIDE "
                f"{tim_low:g}-{tim_high:g}% TIM"
            )
        if expected_capacity_saturation:
            return "EXPECTED $16K/8X CAP SATURATION"
        bh = gain - delta
        capture = gain / bh if bh > 0 else None
        if capture is None or capture < 2.0:
            return "GRAY: ABOVE B&H BUT BELOW 2X TARGET"
        return "MEETS 2X B&H RESEARCH BAR"
    if gain <= 0:
        return "REJECT"
    return "BELOW B&H"


def matrix_description_stats() -> tuple[int, int]:
    path = REPORTS / "SWITCH_MATRIX_TRB.csv.gz"
    if not path.exists():
        return 0, 0
    total = described = 0
    try:
        with gzip.open(path, "rt", newline="") as handle:
            reader = csv.DictReader(line for line in handle if not line.startswith("#"))
            for row in reader:
                total += 1
                described += bool((row.get("description") or "").strip())
    except (OSError, csv.Error):
        return 0, 0
    return total, described


def artifact_line(path: Path, now: datetime) -> str:
    if not path.exists():
        return f"`{path.name}`: MISSING"
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return (
        f"`{path.name}`: {modified.strftime('%Y-%m-%d %H:%M:%SZ')} "
        f"({iso_age(modified.isoformat(), now)} old, {path.stat().st_size:,} bytes)"
    )


def load_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def current_vector_runtime() -> dict[str, object]:
    """Keep physical matrix and research-compute freshness visibly separate."""
    campaign = (
        REPORTS
        / "vec_research"
        / "coupled_qualification_r6_tim50_source_complete_20260803T1555Z"
    )
    status_path = campaign / "results" / "runtime" / "watchdog_status.json"
    status = load_json_object(status_path)
    results = list((campaign / "results").glob("*/QUALIFICATION_RESULT.json"))
    candidates = [
        path
        for path in (campaign / "plan.json", campaign / "priority_overlay.json", status_path)
        if path.is_file()
    ] + results
    latest_mtime = max((path.stat().st_mtime for path in candidates), default=None)
    active: list[str] = []
    proc = Path("/proc")
    if proc.is_dir():
        for cmdline in proc.glob("[0-9]*/cmdline"):
            try:
                argv = cmdline.read_bytes().decode(errors="ignore").split("\0")
            except OSError:
                continue
            command = " ".join(value for value in argv if value)
            if "/home/niels/binance-sandbox" not in command:
                continue
            if any(
                marker in command
                for marker in (
                    "vec_entry_exit_beam_adapter.py",
                    "run_behavior_distinct_vector_frontier.py",
                    "run_path_productivity_hotlist.py",
                    "run_coupled_path_qualification_executor.py",
                )
            ):
                active.append(command)
    return {
        "campaign": str(campaign.relative_to(BASE)),
        "watchdog_status": status.get("status") or "UNAVAILABLE",
        "watchdog_key": status.get("key"),
        "completed_receipts": len(results),
        "latest_utc": (
            datetime.fromtimestamp(latest_mtime, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if latest_mtime is not None
            else None
        ),
        "active_processes": len(active),
    }


def load_vec_research(keys: tuple[str, ...]) -> dict[str, list[dict]]:
    """Load the latest isolated top-exit screen for each distinct test window.

    These artifacts are deliberately *not* SQLite/matrix evidence.  Surfacing them here
    makes fast-screen progress visible without allowing a VEC_RESEARCH result to paint a
    Tier-2 cell green.
    """
    root = REPORTS / "vec_research"
    out: dict[str, list[dict]] = {key: [] for key in keys}
    if not root.exists():
        return out
    for key in keys:
        symbol, side = parse_key(key)
        newest_by_window: dict[tuple[str, str], tuple[float, dict]] = {}
        patterns = (
            f"top_exit_*_{symbol}_{side}",
            f"top_exit_*_{symbol}_{side}_QUARANTINE",
        )
        seen: set[Path] = set()
        for pattern in patterns:
            for directory in root.glob(pattern):
                if directory in seen or not directory.is_dir():
                    continue
                seen.add(directory)
                digest_path = directory / f"digest_{symbol}_{side}.json"
                if not digest_path.exists():
                    quarantine_path = directory / "quarantine.json"
                    if quarantine_path.exists():
                        try:
                            payload = json.loads(quarantine_path.read_text())
                        except (OSError, json.JSONDecodeError):
                            continue
                        payload.setdefault("start", "unknown")
                        payload.setdefault("end_exclusive", None)
                        payload["contract_valid"] = False
                        payload["invalid_data_diagnostic"] = True
                        payload["_artifact"] = str(directory.relative_to(BASE))
                        payload["_mtime"] = datetime.fromtimestamp(
                            quarantine_path.stat().st_mtime, timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        window = (
                            str(payload.get("start") or "unknown"),
                            str(payload.get("end_exclusive") or "present"),
                        )
                        stamp = quarantine_path.stat().st_mtime
                        if window not in newest_by_window or stamp > newest_by_window[window][0]:
                            newest_by_window[window] = (stamp, payload)
                    continue
                try:
                    payload = json.loads(digest_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                window = (
                    str(payload.get("start") or "unknown"),
                    str(payload.get("end_exclusive") or "present"),
                )
                stamp = digest_path.stat().st_mtime
                if window not in newest_by_window or stamp > newest_by_window[window][0]:
                    payload["_artifact"] = str(directory.relative_to(BASE))
                    payload["_mtime"] = datetime.fromtimestamp(
                        stamp, timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    newest_by_window[window] = (stamp, payload)
        out[key] = [
            pair[1]
            for pair in sorted(
                newest_by_window.values(),
                key=lambda pair: (
                    str(pair[1].get("start") or ""),
                    str(pair[1].get("end_exclusive") or "9999"),
                ),
            )
        ]
    return out


def load_exit_factorial() -> dict[str, dict]:
    """Parity-corrected causal exit-family factorial evidence per pilot key.

    This is vector research only.  It is intentionally loaded into a separate
    digest section and never participates in ENGINE coverage or cell coloring.
    TTD/ACN's original receipts used prior-channel/completed-1h DC semantics,
    so they are never a fallback: without the correction artifact those keys
    remain missing instead of resurfacing superseded headline numbers.
    """
    root = REPORTS / "vec_research" / "exit_factorial_20260729"
    out = {}
    mu_path = root / "MU_LONG.json"
    try:
        payload = json.loads(mu_path.read_text())
    except (OSError, json.JSONDecodeError):
        payload = None
    if payload:
        rows = payload.get("rows") or []
        if rows:
            payload["_best"] = rows[0]
            payload["_artifact"] = str(mu_path.relative_to(BASE))
            payload["_parity_corrected"] = False
            out["MU_LONG"] = payload

    correction_path = root / "DC_PARITY_CORRECTION.json"
    try:
        correction = json.loads(correction_path.read_text())
    except (OSError, json.JSONDecodeError):
        correction = None
    if not correction or correction.get("contract") != "MTF_DC_EXACT_VECTOR_PARITY_V1":
        return out
    corrected = correction.get("corrected_best_by_key") or {}
    for key in ("TTD_SHORT", "ACN_SHORT"):
        best = corrected.get(key)
        if not isinstance(best, dict):
            continue
        payload = {
            "tier": "VEC_RESEARCH_EXIT_FACTORIAL_PARITY_CORRECTED",
            "promotion_allowed": False,
            "_best": best,
            "_artifact": str(correction_path.relative_to(BASE)),
            "_parity_corrected": True,
            "_supersedes": list(correction.get("supersedes") or []),
            "_semantics": dict(correction.get("semantics") or {}),
        }
        out[key] = payload
    return out


def load_superseded_exit_factorial_packs() -> dict[str, dict]:
    """Load gray audit rows for the old DC packs, never as candidates."""
    path = (
        REPORTS
        / "vec_research"
        / "exit_factorial_20260729"
        / "DC_PARITY_CORRECTION.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("contract") != "MTF_DC_EXACT_VECTOR_PARITY_V1":
        return {}
    rows = payload.get("superseded_pack_results") or {}
    return rows if isinstance(rows, dict) else {}


def load_exact_exit_factorial() -> list[dict]:
    """Load exact c4 finalist receipts without treating them as matrix cells."""
    root = REPORTS / "exact_exit_factorial_20260729"
    choices = (
        ("TTD_SHORT", "dc_only_exact", root / "TTD_SHORT.json"),
        (
            "ACN_SHORT",
            "superseded_dc_plus_wt",
            root / "ACN_SHORT.json",
        ),
        ("ACN_SHORT", "dc_only", root / "ACN_SHORT__DC_ONLY.json"),
        ("ACN_SHORT", "wt_only", root / "ACN_SHORT__WT_ONLY.json"),
    )
    out = []
    for key, variant, path in choices:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        payload["_key"] = key
        payload["_variant"] = variant
        payload["_artifact"] = str(path.relative_to(BASE))
        payload["_superseded"] = variant == "superseded_dc_plus_wt"
        out.append(payload)
    return out


def vec_candidate(payload: dict) -> dict | None:
    candidates = payload.get("policy_top20") or []
    if candidates:
        return candidates[0]
    candidates = payload.get("overall_top20") or []
    return candidates[0] if candidates else None


def load_top_exit_walk_forward(key: str) -> dict | None:
    path = REPORTS / "vec_research" / f"top_exit_walk_forward_summary_{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("kind") != "VEC_RESEARCH_TOP_EXIT_WALK_FORWARD":
        return None
    return payload


def load_exact_replays() -> dict[str, dict]:
    """Return the newest exact-engine execution replay for each vector artifact."""
    root = REPORTS / "vec_research"
    latest: dict[str, tuple[float, dict]] = {}
    if not root.exists():
        return {}
    for summary_path in root.glob("v8_exact_replay_*/run_summary.json"):
        try:
            payload = json.loads(summary_path.read_text())
            source = str(Path(payload["source_artifact"]).resolve())
            stamp = summary_path.stat().st_mtime
        except (KeyError, OSError, json.JSONDecodeError, TypeError):
            continue
        if source not in latest or stamp > latest[source][0]:
            latest[source] = (stamp, payload)
    return {source: pair[1] for source, pair in latest.items()}


def load_latest_robust_walk_forward(keys: tuple[str, ...]) -> dict[str, dict | None]:
    """Newest nested walk-forward digest for the newer E03/E06/E08/E09 lane."""
    root = REPORTS / "vec_research"
    out: dict[str, dict | None] = {key: None for key in keys}
    for key in keys:
        symbol, side = parse_key(key)
        digest_name = f"walkforward_digest_{symbol}_{side}.json"
        matches = sorted(
            root.glob(f"walkforward_top_exit_*_{symbol}_{side}"),
            key=lambda directory: (
                (directory / digest_name).stat().st_mtime_ns
                if (directory / digest_name).exists()
                else directory.stat().st_mtime_ns
            ),
            reverse=True,
        ) if root.exists() else []
        for directory in matches:
            path = directory / digest_name
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key] = payload
            break
    return out


def load_latest_partial_regime_walk_forward(
    keys: tuple[str, ...],
) -> dict[str, dict | None]:
    """Newest E12 partial-runner / E13 regime-switch frozen-OOS digest."""
    root = REPORTS / "vec_research"
    out: dict[str, dict | None] = {key: None for key in keys}
    for key in keys:
        symbol, side = parse_key(key)
        matches = sorted(
            root.glob(f"partial_regime_walkforward_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if root.exists() else []
        for directory in matches:
            path = directory / f"digest_{symbol}_{side}.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key] = payload
            break
    return out


def load_latest_ladder_walk_forward(
    keys: tuple[str, ...],
) -> dict[str, dict[str, dict | None]]:
    """Newest causal ladder selection plus its exact-engine replay, when present."""
    root = REPORTS / "vec_research"
    out: dict[str, dict[str, dict | None]] = {
        key: {"research": None, "exact": None} for key in keys
    }
    if not root.exists():
        return out
    for key in keys:
        symbol, side = parse_key(key)
        research_dirs = sorted(
            root.glob(f"band_ladder_walkforward_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in research_dirs:
            path = directory / "result.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key]["research"] = payload
            break
        exact_dirs = sorted(
            root.glob(f"v8_exact_ladder_replay_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in exact_dirs:
            path = directory / "run_summary.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key]["exact"] = payload
            break
    return out


def load_mu_daily_deep_pareto_holdout() -> dict | None:
    """Load the sealed MU Pareto holdout receipt as gray research evidence."""
    path = (
        REPORTS
        / "vec_research"
        / "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_hao_short_native_phase3() -> dict | None:
    """Load the corrected, sealed HAO SHORT-native phase-3 receipt."""
    path = REPORTS / "vec_research" / "HAO_SHORT_NATIVE_PHASE3_RECEIPT_20260727.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_hao_short_exposure_phase4() -> dict | None:
    """Load gray HAO phase-4 persistence evidence; never imply promotion."""
    path = (
        REPORTS
        / "vec_research"
        / "HAO_SHORT_EXPOSURE_PHASE4_RECEIPT_20260727.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_other_pilot_survivor_audit() -> dict | None:
    """Load the leak-safe deployed-alpha pilot receipt.

    This is fleet research, never a source of matrix-green or live promotion.
    The receipt itself requires ordinary-engine/live parity after any vector
    survivor, so the digest must preserve its gray verdict verbatim.
    """
    path = (
        REPORTS
        / "vec_research"
        / "OTHER_PILOT_SURVIVOR_RECEIPT_20260729.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_latest_ladder_retunes() -> list[dict]:
    """Latest frozen exposure-retune artifact for every discovered symbol/side.

    These are vector-first research rows.  They are deliberately kept outside
    the authoritative matrix tables, but exposing them here prevents a useful
    top/bottom-cohort campaign from disappearing from the progress digest.
    """
    root = REPORTS / "vec_research"
    if not root.exists():
        return []
    newest: dict[str, dict] = {}
    directories = sorted(
        root.glob("ladder_exposure_retune_*_*_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in directories:
        path = directory / "result.json"
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        manifest = payload.get("manifest") or {}
        symbol = str(manifest.get("symbol") or "").upper()
        side = str(manifest.get("side") or "").upper()
        if not symbol or side not in {"LONG", "SHORT"}:
            continue
        key = f"{symbol}_{side}"
        if key in newest:
            continue
        payload["_artifact"] = str(directory.relative_to(BASE))
        payload["_key"] = key
        newest[key] = payload

    exact_by_key: dict[str, dict] = {}
    exact_dirs = sorted(
        root.glob("v8_exact_ladder_replay_*_*_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in exact_dirs:
        parts = directory.name.rsplit("_", 2)
        if len(parts) != 3:
            continue
        key = f"{parts[-2].upper()}_{parts[-1].upper()}"
        if key in exact_by_key:
            continue
        try:
            exact_by_key[key] = json.loads(
                (directory / "run_summary.json").read_text()
            )
        except (OSError, json.JSONDecodeError):
            continue
    for key, payload in newest.items():
        exact = exact_by_key.get(key)
        source_name = Path(str((exact or {}).get("source_artifact") or "")).name
        artifact_name = Path(str(payload.get("_artifact") or "")).name
        payload["_exact"] = (
            exact if exact and source_name and source_name == artifact_name else None
        )
    return [newest[key] for key in sorted(newest)]


def load_tradier_5m_coverage() -> dict:
    """Native/interpolated execution provenance produced by the retention audit."""
    path = REPORTS / "tradier_5m_coverage_latest.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_path_fleet_progress() -> dict:
    """Current claimable entry/exit fleet state and its latest result rows.

    The fleet database is a separate research ledger.  Surfacing it in the
    email digest must not let vector controls fill or color exact matrix cells.
    """
    root = REPORTS / "path_fleet"
    db_path = root / "queue.db"
    universe_path = root / "universe.json"
    if not db_path.exists():
        return {}
    try:
        fleet = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        fleet.row_factory = sqlite3.Row
        states = {
            row["status"]: row["n"]
            for row in fleet.execute(
                "SELECT status,COUNT(*) n FROM jobs GROUP BY status ORDER BY status"
            )
        }
        jobs = fleet.execute(
            "SELECT COUNT(*) n,MAX(heartbeat_at) latest FROM jobs"
        ).fetchone()
        rows = [
            dict(row)
            for row in fleet.execute(
                """SELECT j.path_id,r.symbol,r.side,r.stage,r.status,
                          r.strategy_return_pct,r.bh_return_pct,
                          r.same_entry_control_return_pct,r.alpha_vs_bh_pp,
                          r.alpha_vs_control_pp,r.tim_pct,r.trades,r.created_at
                          ,r.payload_json
                   FROM results r JOIN jobs j ON j.id=r.job_id
                   ORDER BY r.created_at DESC LIMIT 30"""
            )
        ]
        fleet.close()
    except (OSError, sqlite3.Error):
        return {}
    for row in rows:
        try:
            payload = json.loads(row.pop("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        row["metric_scope"] = payload.get("metric_scope") or "LEGACY_UNSCOPED"
        row["return_unit"] = payload.get("return_unit") or "LEGACY_UNSCOPED"
        row["return_aggregation"] = (
            payload.get("return_aggregation") or "LEGACY_UNSCOPED"
        )
        row["tim_unit"] = payload.get("tim_unit") or "LEGACY_UNSCOPED"
        row["tim_aggregation"] = (
            payload.get("tim_aggregation") or "LEGACY_UNSCOPED"
        )
        row["tim_metric"] = payload.get("tim_metric")
        row["tim_binary_pct"] = payload.get("tim_binary_pct")
        row["tim_weighted_pct"] = payload.get("tim_weighted_pct")
        row["capital_base_usd"] = payload.get("capital_base_usd")
        row["fold"] = payload.get("fold")
    try:
        universe = json.loads(universe_path.read_text())
    except (OSError, json.JSONDecodeError):
        universe = {}
    return {
        "root": root,
        "states": states,
        "job_count": int(jobs["n"] or 0),
        "latest_heartbeat": jobs["latest"],
        "rows": rows,
        "universe": universe,
    }


def load_recent_bundle_progress() -> dict:
    """Finite recent-trade bundle screen; separate from exact matrix cells."""
    path = REPORTS / "recent_tradier_90_10" / "status.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def format_fleet_metric_scope(row: dict) -> str:
    """Human-readable scope without hiding binary/weighted exact TIM."""
    scope = row.get("metric_scope") or "LEGACY_UNSCOPED"
    ret = row.get("return_unit") or "LEGACY_UNSCOPED"
    ret_agg = row.get("return_aggregation") or "LEGACY_UNSCOPED"
    tim_unit = row.get("tim_unit") or "LEGACY_UNSCOPED"
    tim_agg = row.get("tim_aggregation") or "LEGACY_UNSCOPED"
    parts = [
        f"{scope}",
        f"return={ret} ({ret_agg})",
        f"TIM={tim_unit} ({tim_agg})",
    ]
    binary = row.get("tim_binary_pct")
    weighted = row.get("tim_weighted_pct")
    if binary is not None or weighted is not None:
        parts.append(
            f"binary={fmt(binary, 3, '%')}; weighted={fmt(weighted, 3, '%')}"
        )
    if row.get("fold"):
        parts.append(f"fold={row['fold']}")
    return "; ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS),
                        help="comma-separated SYMBOL_SIDE pilot keys")
    parser.add_argument("--output", default=str(REPORTS / "SWITCH_MATRIX_TRB_DIGEST.md"))
    parser.add_argument("--recent", type=int, default=8,
                        help="recent strategy rows per pilot key")
    args = parser.parse_args()
    keys = tuple(k.strip().upper() for k in args.keys.split(",") if k.strip())
    out = Path(args.output)
    now = datetime.now(timezone.utc)
    matrix_pause = load_json_object(REPORTS / "MATRIX_INTEGRITY_PAUSE_STATUS.json")
    vector_runtime = current_vector_runtime()

    if not DB.exists():
        raise SystemExit(f"missing result database: {DB}")
    try:
        try:
            from tools import matrix_guard
        except ModuleNotFoundError:
            import matrix_guard  # type: ignore
        matrix_guard.load()
        canonical_matrix = matrix_guard.MATRIX
    except Exception as exc:
        raise SystemExit(
            "canonical current matrix provenance failed; refusing digest: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")

    tables = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    cell_columns = {
        row[1] for row in con.execute("PRAGMA table_info(param_cells)")
    }
    contract_columns = {
        "validation_status",
        "contract_fingerprint",
        "real_closes",
        "reentry_violations",
        "inert",
    }.issubset(cell_columns)
    contract_fps = current_contract_fingerprints(keys)
    latest_parts = [
        f"SELECT MAX(ts) ts FROM {name}"
        for name in ("param_cells", "key_baseline", "stage_results")
        if name in tables
    ]
    db_latest = con.execute(
        "SELECT MAX(ts) FROM (" + " UNION ALL ".join(latest_parts) + ")"
    ).fetchone()[0]
    engine_total = con.execute(
        "SELECT COUNT(*) FROM param_cells WHERE COALESCE(tier,'ENGINE')='ENGINE'"
    ).fetchone()[0]
    current_engine = 0
    quarantined_engine = engine_total
    vec_total = con.execute(
        "SELECT COUNT(*) FROM param_cells WHERE tier='VEC'"
    ).fetchone()[0]
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_engine = 0

    all_raw_current = (
        con.execute(
            "SELECT symbol,side,param,value_json,ts,contract_fingerprint,"
            "validation_status FROM param_cells "
            "WHERE COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts>=? "
            "ORDER BY ts",
            (CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF),
        ).fetchall()
        if contract_columns
        else []
    )
    all_current_keys = tuple(
        sorted(
            {
                f"{row['symbol']}_{row['side']}"
                for row in all_raw_current
            }
        )
    )
    all_contract_fps = current_contract_fingerprints(all_current_keys)
    # C5 is deliberately fail closed. Capacity-clamped and incomplete lifecycle
    # rows remain historical diagnostics, never current matrix evidence.
    valid_statuses = {"PASS"}
    accepted_current_rows = [
        row
        for row in all_raw_current
        if row["validation_status"] in valid_statuses
        and row["contract_fingerprint"]
        in all_contract_fps.get(
            f"{row['symbol']}_{row['side']}", set()
        )
    ]
    current_engine = len(accepted_current_rows)
    current_matrix_latest = max(
        (str(row["ts"] or "") for row in accepted_current_rows),
        default=None,
    )
    recent_engine = sum(
        str(row["ts"] or "") >= max(cutoff, CURRENT_ENGINE_CUTOFF)
        for row in accepted_current_rows
    )
    quarantined_engine = max(0, engine_total - current_engine)

    pilot_coverage = load_classified_pilot_coverage(keys)
    try:
        try:
            from tools import vector_scalar_gap_reporting
        except ModuleNotFoundError:
            import vector_scalar_gap_reporting  # type: ignore
        vector_scalar_gap = vector_scalar_gap_reporting.load(BASE)
        vector_scalar_mapping = (
            vector_scalar_gap_reporting.scalar_grid_mapping(
                BASE, vector_scalar_gap
            )
            if vector_scalar_gap.get("available")
            else {}
        )
    except Exception as exc:
        vector_scalar_gap = {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "per_key": {},
        }
        vector_scalar_mapping = {}

    # USER OVERRIDE 2026-08-01: amber VEC rows are provisional matrix fills.
    # They remain explicitly non-exact/non-promotable, but count toward the
    # provisional completion denominator until an exact V8 replay supersedes
    # them.  Count only logical actionable blanks when the receipt-bound
    # classifier is available; never let physical helper rows inflate MU 826.
    def _amber_norm(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        text = str(value).strip().lower()
        if text in {"true", "yes", "on"}:
            return "true"
        if text in {"false", "no", "off"}:
            return "false"
        try:
            number = float(text)
            return str(int(number)) if number == int(number) else str(number)
        except (TypeError, ValueError):
            return text

    amber_overlay_by_key = {key: set() for key in keys}
    amber_overlay_path = (
        REPORTS / "full_trb_blank_matrix_vec_approx_20260801" / "vector_overlay_index.jsonl"
    )
    if not amber_overlay_path.is_file() or not amber_overlay_path.stat().st_size:
        amber_overlay_path = (
            REPORTS / "full_trb_blank_matrix_vec_approx_20260801" / "all_cells.jsonl"
        )
    amber_paths = [amber_overlay_path] if amber_overlay_path.is_file() and amber_overlay_path.stat().st_size else []
    overlay_dir = REPORTS / "full_trb_blank_matrix_vec_approx_20260801"
    key_file = re.compile(r"^[A-Z0-9.\-]+_(?:LONG|SHORT)\.jsonl$")
    amber_paths.extend(
        path for path in sorted(overlay_dir.glob("*.jsonl"))
        if key_file.match(path.name) and path not in amber_paths
    )
    if amber_paths:
        try:
            for path in amber_paths:
                with path.open() as handle:
                    for line in handle:
                        row = json.loads(line)
                        key = str(row.get("key") or "")
                        if (
                            key in amber_overlay_by_key
                            and row.get("vector_evidence_class") == "VEC_APPROX"
                            and row.get("exact_completion_credit") is False
                            and row.get("protected_exact_present") is not True
                        ):
                            amber_overlay_by_key[key].add(
                                (str(row.get("param") or ""), _amber_norm(row.get("value_json")), key)
                            )
        except (OSError, json.JSONDecodeError, TypeError):
            amber_overlay_by_key = {key: set() for key in keys}
    amber_actionable_by_key = {key: 0 for key in keys}
    existing_actionable_by_key = {key: 0 for key in keys}
    try:
        from tools import matrix_guard

        mh, mrows = matrix_guard.load()
        contract = matrix_guard.load_uniqueness_contract() or {}
        active = set((contract.get("tim_policy") or {}).get("keys") or [])
        actionable_categories = PROVISIONAL_ACTIONABLE_CATEGORIES
        for mrow, record in zip(mrows, contract.get("rows") or []):
            for key in keys:
                # The 826-cell differential denominator is the union of all
                # three actionable categories.  Counting only EXACT_ONLY
                # reproduces the disproven 565/826 monitor bug (§16.12).
                if matrix_guard.cell_contract_category(record, key, active) not in actionable_categories:
                    continue
                index = mh.index(key)
                if str(mrow[index] if index < len(mrow) else "").strip():
                    existing_actionable_by_key[key] += 1
                    continue
                logical = (str(record.get("canonical_param") or ""), _amber_norm(mrow[2] if len(mrow) > 2 else ""), key)
                if logical in amber_overlay_by_key.get(key, set()):
                    amber_actionable_by_key[key] += 1
    except Exception:
        # Keep raw counts visible if the local provenance hash is stale; the
        # digest labels them physical until S1 refreshes the classifier.
        amber_actionable_by_key = {
            key: len(values) for key, values in amber_overlay_by_key.items()
        }
        existing_actionable_by_key = {key: 0 for key in keys}
    amber_total = sum(amber_actionable_by_key.values())
    target_predicate = " OR ".join("(symbol=? AND side=?)" for _ in keys)
    target_args = [part for key in keys for part in parse_key(key)]
    raw_cells = (
        con.execute(
            "SELECT symbol,side,param,value_json,ts,contract_fingerprint,"
            "validation_status FROM param_cells "
            "WHERE COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts>=? AND ("
            + target_predicate
            + ") ORDER BY ts",
            [CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF, *target_args],
        ).fetchall()
        if contract_columns
        else []
    )
    latest_by_key: dict[str, str | None] = {key: None for key in keys}
    for row in raw_cells:
        key = f"{row['symbol']}_{row['side']}"
        if (
            row["validation_status"] != "PASS"
            or row["contract_fingerprint"] not in contract_fps.get(key, set())
        ):
            continue
        latest_by_key[key] = row["ts"]
    stale_fingerprint_rows = sum(
        1
        for row in raw_cells
        if row["validation_status"] in valid_statuses
        and row["contract_fingerprint"]
        not in contract_fps.get(f"{row['symbol']}_{row['side']}", set())
    )
    invalid_status_rows = sum(
        1 for row in raw_cells if row["validation_status"] not in valid_statuses
    )
    strategy_rows: dict[str, list[sqlite3.Row]] = {}
    baseline_rows: dict[str, sqlite3.Row | None] = {}
    for key in keys:
        symbol, side = parse_key(key)
        baseline_candidates = con.execute(
            "SELECT symbol,side,gain_per_mo,bh_per_mo,time_in_mkt_pct,"
            "real_closes,size_clamp_count,validation_status,"
            "contract_fingerprint,ts "
            "FROM key_baseline WHERE mode='tradier' AND symbol=? AND side=? "
            "AND campaign=? ORDER BY ts DESC",
            (symbol, side, CURRENT_ENGINE_CAMPAIGN),
        ).fetchall()
        baseline_rows[key] = next(
            (
                row
                for row in baseline_candidates
                if row["contract_fingerprint"] in contract_fps.get(key, set())
            ),
            None,
        )
        strategy_rows[key] = con.execute(
            "SELECT symbol,side,campaign,param,value_json,acc_gain_pct,gain_per_mo,"
            "delta_gain_mo_vs_bh,trades,time_in_mkt_pct,pool_sharpe,source_file,ts,"
            "validation_status,contract_fingerprint,real_closes,reentry_violations,inert,"
            "result_audit_json "
            "FROM param_cells WHERE COALESCE(tier,'ENGINE')='ENGINE' "
            "AND symbol=? AND side=? AND campaign=? AND ts>=? AND "
            "validation_status='PASS' AND "
            + strategy_where()
            + " ORDER BY ts DESC",
            (symbol, side, CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF),
        ).fetchall() if contract_columns else []
        strategy_rows[key] = [
            row for row in strategy_rows[key]
            if row["contract_fingerprint"] in contract_fps.get(key, set())
        ]

    # Canonical scalar reporting is a merged receipt hierarchy, not c5-only.
    # Full c5 wins first, then validated 2+year c2/c3/c4 and c1, then c5_1yr
    # fills a genuinely missing logical cell. Re-select the full DB rows by the
    # adapter's accepted rowids so the detailed digest table retains audit,
    # lifecycle and TIM fields without reimplementing receipt validation here.
    try:
        try:
            from tools import current_matrix_reporting as reporting
        except ModuleNotFoundError:
            import current_matrix_reporting as reporting  # type: ignore
        merged_receipts = [
            row
            for row in reporting.merged_rows(BASE)
            if str(row.get("param") or "")
            not in {"GROUP_COMBO", "STOP_PACK", "TF_EXCLUDE"}
        ]
        current_engine = len(merged_receipts)
        current_matrix_latest = max(
            (str(row.get("ts") or "") for row in merged_receipts),
            default=None,
        )
        recent_engine = sum(
            str(row.get("ts") or "") >= cutoff
            for row in merged_receipts
        )
        for key in keys:
            accepted_ids = [
                int(row["result_rowid"])
                for row in merged_receipts
                if row.get("key") == key and row.get("result_rowid")
            ]
            latest_by_key[key] = max(
                (
                    str(row.get("ts") or "")
                    for row in merged_receipts
                    if row.get("key") == key
                ),
                default=None,
            )
            if not accepted_ids:
                strategy_rows[key] = []
                continue
            placeholders = ",".join("?" for _ in accepted_ids)
            strategy_rows[key] = con.execute(
                "SELECT rowid AS result_rowid,symbol,side,campaign,param,"
                "value_json,acc_gain_pct,gain_per_mo,delta_gain_mo_vs_bh,"
                "trades,time_in_mkt_pct,pool_sharpe,source_file,ts,"
                "validation_status,contract_fingerprint,real_closes,"
                "reentry_violations,inert,result_audit_json "
                "FROM param_cells WHERE rowid IN ("
                + placeholders
                + ") ORDER BY ts DESC",
                accepted_ids,
            ).fetchall()
    except Exception as exc:
        print(
            "WARN: merged receipt strategy rows unavailable; retaining "
            f"c5-only diagnostic rows: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    campaign_rows = con.execute(
        "SELECT COALESCE(tier,'ENGINE') tier,campaign,COUNT(*) n,MAX(ts) latest "
        "FROM param_cells WHERE campaign=? AND ts>=? "
        "GROUP BY COALESCE(tier,'ENGINE'),campaign ORDER BY latest DESC LIMIT 12",
        (CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF),
    ).fetchall()
    desc_total, desc_filled = matrix_description_stats()
    vec_research = load_vec_research(keys)
    exit_factorial = load_exit_factorial()
    superseded_exit_factorial = load_superseded_exit_factorial_packs()
    exact_exit_factorial = load_exact_exit_factorial()
    walk_forward = {
        key: load_top_exit_walk_forward(key) for key in keys
    }
    exact_replays = load_exact_replays()
    robust_walk_forward = load_latest_robust_walk_forward(keys)
    partial_regime_walk_forward = load_latest_partial_regime_walk_forward(keys)
    ladder_walk_forward = load_latest_ladder_walk_forward(keys)
    mu_pareto_holdout = load_mu_daily_deep_pareto_holdout()
    hao_short_phase3 = load_hao_short_native_phase3()
    hao_short_phase4 = load_hao_short_exposure_phase4()
    other_pilot_audit = load_other_pilot_survivor_audit()
    ladder_retunes = load_latest_ladder_retunes()
    coverage_5m = load_tradier_5m_coverage()
    path_fleet = load_path_fleet_progress()
    recent_bundles = load_recent_bundle_progress()
    c4_vector_first = load_c4_vector_first_progress(keys)
    c4_vector_adaptive = load_c4_vector_first_progress(
        keys,
        screen_root=REPORTS / "c4_vector_bundle_screen_adaptive" / "receipts",
        status_path=REPORTS / "c4_vector_first_adaptive" / "status.json",
    )
    c4_vector_targeted = load_isolated_vector_progress(
        keys,
        screen_root=REPORTS / "c4_vector_bundle_screen_targeted_cycle3",
        status_path=(
            REPORTS / "c4_vector_bundle_screen_targeted_cycle3" / "status.json"
        ),
        contract_kind="targeted_cycle3",
    )
    c4_short_native = load_isolated_vector_progress(
        keys,
        screen_root=REPORTS / "c4_short_native_bundle_screen",
        status_path=REPORTS / "c4_short_native_bundle_screen" / "status.json",
        contract_kind="short_native",
    )
    c4_breakout_cycle4 = load_isolated_vector_progress(
        keys,
        screen_root=REPORTS / "c4_vector_bundle_screen_breakout_cycle4",
        status_path=(
            REPORTS / "c4_vector_bundle_screen_breakout_cycle4" / "status.json"
        ),
        contract_kind="breakout_cycle4",
        runner_path=BASE / "tools/c4_vector_breakout_cycle4.py",
    )
    workbook_audits = []
    workbook_audit_error = None
    try:
        try:
            from tools.audit_stock_matrix_workbooks import (
                audit_param_workbook,
                audit_switch_workbook,
            )
        except ModuleNotFoundError:
            from audit_stock_matrix_workbooks import (  # type: ignore
                audit_param_workbook,
                audit_switch_workbook,
            )
        workbook_audits = [
            audit_switch_workbook(
                REPORTS / "SWITCH_MATRIX_TRB.xlsx",
                BASE,
                require_fresh=True,
            ),
            audit_param_workbook(
                REPORTS / "PARAM_BASELINE_STOCKS.xlsx",
                BASE,
                require_fresh=True,
                require_matrix_top=True,
            ),
        ]
    except Exception as exc:
        workbook_audit_error = str(exc)
    coverage_symbols = coverage_5m.get("symbols", {})
    covered_native_symbols = sum(
        int(row.get("native_source", {}).get("rows", 0) or 0) > 0
        for row in coverage_symbols.values()
    )
    covered_native_rows = sum(
        int(row.get("native_source", {}).get("rows", 0) or 0)
        for row in coverage_symbols.values()
    )

    lines = [
        f"# SWITCH_MATRIX_TRB progress digest — {now.strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 "
        "replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.",
        "",
        "## Freshness",
        "",
        f"- Physical fixed-cell matrix latest accepted ENGINE row: "
        f"`{current_matrix_latest or 'none'}` "
        f"({iso_age(current_matrix_latest, now)} old). This clock advances only "
        "after a current-contract cell is accepted; vector research cannot write it.",
        f"- Physical matrix write admission: **"
        f"{'ALLOWED' if matrix_pause.get('matrix_write_admission_allowed') is True else 'PAUSED'}**"
        f" — `{matrix_pause.get('reason') or 'status unavailable'}`.",
        f"- Current vector research heartbeat: "
        f"`{vector_runtime.get('latest_utc') or 'none'}` "
        f"({iso_age(vector_runtime.get('latest_utc'), now)} old); "
        f"active vector processes: **{int(vector_runtime.get('active_processes') or 0):,}**; "
        f"R5 safety-parity receipts: **{int(vector_runtime.get('completed_receipts') or 0):,}**; "
        f"R5 watchdog: **{vector_runtime.get('watchdog_status')}**"
        f"{(' (`' + str(vector_runtime.get('watchdog_key')) + '`)') if vector_runtime.get('watchdog_key') else ''}.",
        f"- Generic DB activity (includes historical/stage tables): `{db_latest or 'none'}` "
        f"({iso_age(db_latest, now)} old); it is not matrix freshness.",
        f"- Current repaired-contract ENGINE rows: **{current_engine:,}**; "
        f"new current rows in 24h: **{recent_engine:,}**.",
        f"- Raw repaired-campaign pilot rows since cutoff: **{len(raw_cells):,}**; "
        f"**{stale_fingerprint_rows:,}** are preserved but invalidated by the newer "
        f"code+NPZ+side fingerprint, and **{invalid_status_rows:,}** fail validation status. "
        "Blank current cells must be regenerated; they are not silently backfilled from old code.",
        f"- Historical/pre-fix ENGINE rows quarantined from current rankings: "
        f"**{quarantined_engine:,}/{engine_total:,}**. They remain preserved as evidence.",
        f"- Current contract: campaign `{CURRENT_ENGINE_CAMPAIGN}`, cutoff "
        f"`{CURRENT_ENGINE_CUTOFF}`, exact code+NPZ+side fingerprint required.",
        "- Current stock cost contract: ordinary exact ENGINE **0.05% round trip**; "
        "new vector/research manifests **0 bps commission + 2.5 bps adverse "
        "slippage one way** (also 0.05% round trip). Historical 5+2 bps "
        "research receipts remain quarantined under their declared 14 bps cost.",
        "- Crypto cost remains **0.08% round trip** in the engine; it was not "
        "changed to 0.8% because repository/live history does not support that "
        "tenfold value.",
        f"- VEC diagnostic rows: **{vec_total:,}**; provisional amber overlay rows: **{amber_total:,}**. "
        "Amber rows are valid provisional results: they count toward provisional fill and vector combination ranking; exact ENGINE credit and live promotion remain separate gates.",
        (
            "- Separate vector scalar-gap receipt: "
            f"**{int(vector_scalar_gap.get('screened_cells') or 0):,}** "
            "amber/italic hypotheses; exact completion credit **0**; "
            "ENGINE ranking and promotion **forbidden**."
            if vector_scalar_gap.get("available")
            else "- Separate vector scalar-gap receipt: **UNAVAILABLE** — "
            + str(vector_scalar_gap.get("reason") or "unknown")
        ),
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.xlsx", now),
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.csv.gz", now),
        f"- " + artifact_line(
            REPORTS / "switch_lab_catalog_20260729.json  # alias: SWITCH_MATRIX_INTERDEPENDENCY_20260729.json kept for backwards compat", now
        ),
        f"- " + artifact_line(
            REPORTS / "switch_lab_catalog_20260729.csv", now
        ),
        f"- Description coverage in current CSV: **{desc_filled:,}/{desc_total:,}** rows.",
        (
            "- Workbook axis audit: **ERROR** — "
            + workbook_audit_error
            if workbook_audit_error
            else "- Workbook axis audit: "
            + "; ".join(
                f"**{report.kind}={'PASS' if report.ok else 'FAIL'}** "
                f"({report.active_keys} active + {report.historical_keys} historical "
                f"= {report.configured_keys} visible keys; "
                f"fresh={'yes' if report.fresh_vs_symbol_sources else 'NO'}; "
                f"missing axes={sum(len(v) for v in report.missing_keys_by_sheet.values())}; "
                f"blank descriptions={len(report.blank_path_descriptions)}; "
                f"formula errors={len(report.formula_errors)})"
                for report in workbook_audits
            )
        ),
        "- Coverage contract: every current `symbols_trb_long/short` key must remain visible "
        "on every relevant path sheet. Blank/white result cells are valid; a missing row or "
        "column is an export failure.",
        "",
        "## Stocks 5m execution provenance",
        "",
        "Historical native 5m availability is provider-limited. Older rows use the disclosed "
        "containing-15m interpolation; native bars replace it permanently as they are collected.",
        f"Retention report scope: **{len(coverage_symbols):,} symbols**, "
        f"**{covered_native_symbols:,} native archives**, **{covered_native_rows:,} native rows**, "
        f"**{len(coverage_5m.get('warnings') or []):,} availability warnings**, "
        f"**{len(coverage_5m.get('errors') or []):,} hard errors**.",
        "",
        "| key | native rows | native coverage | native range | interpolated rows | interpolated coverage | retention |",
        "|---|---:|---:|---|---:|---:|---|",
    ]
    lines += historical_integration_section()
    for key in keys:
        symbol, _side = parse_key(key)
        row = coverage_symbols.get(symbol, {})
        source = row.get("native_source", {})
        npz = row.get("npz", {})
        native = npz.get("native", {})
        interpolated = npz.get("interpolated", {})
        errors = row.get("errors") or []
        if not row:
            lines.append(f"| {key} | — | — | — | — | — | MISSING REPORT |")
            continue
        native_range = (
            f"{str(source.get('start') or '—')[:10]} → "
            f"{str(source.get('end') or '—')[:10]}"
        )
        lines.append(
            f"| {key} | {source.get('rows', '—')} | "
            f"{fmt(native.get('pct'), 2, '%')} | {native_range} | "
            f"{interpolated.get('rows', '—')} | "
            f"{fmt(interpolated.get('pct'), 2, '%')} | "
            f"{'PASS' if not errors else 'FAIL: ' + '; '.join(errors[:2])} |"
        )
    lines += [
        "",
        "The append/merge ledger blocks any refresh that shrinks native row count, advances "
        "the first timestamp, regresses the last timestamp, or introduces duplicate/out-of-order "
        "timestamps. `synthetic_5m_parent_close_ts` records the bounded 0/5/10-minute parent lag.",
        "",
        "## Ordinary baseline actionability",
        "",
        "| key | gain/mo | side B&H/mo | TIM | real closes | clamps | scheduler state |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        row = baseline_rows.get(key)
        if row is None:
            lines.append(
                f"| {key} | — | — | — | — | — | MISSING CURRENT CONTRACT |"
            )
            continue
        closes = int(row["real_closes"] or 0)
        tim = float(row["time_in_mkt_pct"] or 0.0)
        tim_low, tim_high = tim_band_for_key(key)
        if tim < 1.0:
            state = "BASELINE_ENTRY_INERT — ENTRY SOURCE BINDING ONLY"
        elif tim < tim_low:
            state = f"ACTIVE BUT BELOW {tim_low:g}% TARGET"
        elif tim > tim_high:
            state = f"ACTIVE BUT ABOVE {tim_high:g}% TARGET"
        else:
            state = "ACTIVE IN TARGET BAND"
        lines.append(
            f"| {key} | {fmt(row['gain_per_mo'], 4)} | "
            f"{fmt(row['bh_per_mo'], 4)} | "
            f"{fmt(row['time_in_mkt_pct'], 4, '%')} | {closes:,} | "
            f"{int(row['size_clamp_count'] or 0):,} | {state} |"
        )
    lines += [
        "",
        "An all-exits-off ladder floor is expected to have zero real closes; "
        "high exposure makes it actionable because the exit family under test "
        "must create the closes. Only a floor under 1% TIM is entry-inert. Its exact worker is "
        "dependency-gated to source-backed ENTRY masters and ENTRY_SOURCE "
        "binding probes; helper packs never count as matrix coverage.",
        "",
        "## Pilot-key matrix coverage (exact + provisional amber)",
        "",
        "| key | exact differential | amber provisional | provisional completion | plateau cross-key | latest Tier-2 row | age | strategy tests |",
        "|---|---:|---:|---:|---:|---|---:|---:|",
    ]
    for key in keys:
        coverage = (pilot_coverage.get("keys") or {}).get(key, {})
        diff_filled = int(coverage.get("differential_filled", 0) or 0)
        diff_empty = int(coverage.get("differential_empty", 0) or 0)
        diff_total = diff_filled + diff_empty
        plateau_filled = int(coverage.get("plateau_filled", 0) or 0)
        plateau_empty = int(coverage.get("plateau_empty", 0) or 0)
        plateau_total = plateau_filled + plateau_empty
        combined_filled = diff_filled + plateau_filled
        combined_total = diff_total + plateau_total
        amber_filled = int(amber_actionable_by_key.get(key, 0))
        existing_filled = int(existing_actionable_by_key.get(key, diff_filled))
        provisional_filled = min(diff_total, existing_filled + amber_filled)
        lines.append(
            f"| {key} | {diff_filled:,}/{diff_total:,} "
            f"({100.0 * diff_filled / max(diff_total, 1):.1f}%) | "
            f"{amber_filled:,} | "
            f"{provisional_filled:,}/{diff_total:,} "
            f"({100.0 * provisional_filled / max(diff_total, 1):.1f}%) | "
            f"{plateau_filled:,}/{plateau_total:,} | "
            f"{latest_by_key[key] or '—'} | {iso_age(latest_by_key[key], now)} | "
            f"{len(strategy_rows[key]):,} |"
        )

    lines += [
        "",
        (
            "Coverage uses the same disjoint classifier as `tools/matrix_guard.py`: "
            "only active-key, side-applicable, exact-executable differential rows "
            "enter the denominator. Plateau cross-key probes are shown separately. "
            "Historical axes, no-live-reader rows, wrong-account rows, helpers, "
            "intentional controls, and opposite-side cells are not actionable empties."
            if pilot_coverage.get("available")
            else "Coverage classifier unavailable: "
            + str(pilot_coverage.get("reason") or "unknown error")
        ),
        "Amber VEC rows fill provisional blanks and are valid vector-combination ranking evidence; they never become exact ENGINE rows or exact promotion evidence. Duplicate campaigns do not inflate coverage.",
        "",
        "## Vector scalar-gap diagnostic (separate amber layer)",
        "",
        "> `VEC_DIAGNOSTIC` / `VEC_APPROX` amber rows are valid provisional fills and "
        "may be ranked for vector combination search. Exact completion credit, "
        "ENGINE/database writes, and live promotion remain separate gates. "
        "Candidates above the configured escalation threshold queue for exact V8 replay.",
        "",
        "| key | executable scalar | parity-eligible | screened | moved | inert | zero | VEC_NATIVE | VEC_PARITY | VEC_APPROX | approx H/M/L | exact replay priority | exact credit |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not vector_scalar_gap.get("available"):
        lines += [
            f"| UNAVAILABLE | — | — | — | — | — | — | — | — | — | — | — | 0 |",
            "",
            f"Fail-closed reason: `{vector_scalar_gap.get('reason') or 'unknown'}`.",
            "",
        ]
    else:
        for key in keys:
            item = (vector_scalar_gap.get("per_key") or {}).get(key, {})
            mapped = (vector_scalar_mapping.get("per_key") or {}).get(
                key, {}
            )
            executable = mapped.get("executable_scalar_cells")
            eligible = mapped.get("eligible_vector_cells")
            confidence = item.get("approximation_confidence") or {}
            lines.append(
                f"| {key} | {executable if executable is not None else '—'} | "
                f"{eligible if eligible is not None else '—'} | "
                f"{int(item.get('screened_cells') or 0)} | "
                f"{int(item.get('moved') or 0)} | "
                f"{int(item.get('inert') or 0)} | "
                f"{int(item.get('zero_trade') or 0)} | "
                f"{int(item.get('vec_native_cells') or 0)} | "
                f"{int(item.get('vec_parity_cells') or 0)} | "
                f"{int(item.get('vec_approx_cells') or 0)} | "
                f"{int(confidence.get('HIGH') or 0)}/"
                f"{int(confidence.get('MEDIUM') or 0)}/"
                f"{int(confidence.get('LOW') or 0)} | "
                f"{int(item.get('exact_replay_priority_cells') or 0)} | 0 |"
            )
        lines += [
            "",
            "Why the fast denominator is smaller: the old 346 candidates were "
            "316 reviewed native values plus 30 values from six EMA200 aliases. "
            "T1_MULT, T1_PCT and T3_MULT failed sampled exact parity, removing "
            "15 values; the approved vector denominator is 331 (316 native plus "
            "15 T2_MULT/T2_PCT/T3_PCT values with 5/5 VT MOVED/INERT agreement). "
            "VEC_APPROX expands discovery coverage using explicit proxy "
            "mappings; those cells remain exact-only and are reported by "
            "confidence and mismatch class, never as ENGINE completion. "
            "The classification-aware executable total is 1,609 for LONG and "
            "1,600 for SHORT after side applicability; it also includes "
            "exact-only and plateau cross-key work. GROUP_COMBO, STOP_PACK and "
            "TF_EXCLUDE are not scalar completion.",
            "",
        ]
    lines += [
        "## Current c5 vector-first bundle cycle",
        "",
        "This is the fast discovery lane, not matrix coverage or promotion. It uses a "
        "$10,000 stock account denominator, $2,000 side-specific B&H capital, the "
        "$16,000/8× strategy ceiling and 0.05% round-trip cost. Each row must beat "
        "both 2× B&H/cash and its same-entry/all-exits-off control on every frozen "
        "fold before exact replay.",
        (
            f"Current runner `{str(c4_vector_first.get('runner_sha256') or '—')[:12]}`; "
            f"latest receipt `{c4_vector_first.get('latest') or 'none'}`; "
            f"exact queue **{int((c4_vector_first.get('status') or {}).get('exact_queued', 0) or 0)}**; "
            f"historical/unbound TIM receipts quarantined "
            f"**{int(c4_vector_first.get('historical_tim_contract_receipts', 0) or 0)}/"
            f"{int(c4_vector_first.get('unbound_tim_contract_receipts', 0) or 0)}**."
        ),
        "",
        "| key | current bundles | top performance probe | best probe inside key TIM band |",
        "|---|---:|---|---|",
    ]
    for key in keys:
        vector_row = (c4_vector_first.get("keys") or {}).get(key, {})
        lines.append(
            f"| {key} | {int(vector_row.get('screened', 0) or 0)} | "
            f"{format_c4_vector_candidate(vector_row.get('top_performance'))} | "
            f"{format_c4_vector_candidate(vector_row.get('best_in_tim'))} |"
        )
    lines += [
        "",
        "All rejected receipts remain gray so identical code/data/bundle identities are "
        "not blindly retested. `EXACT_PENDING` is still only a queue entry; the exact "
        "same-entry control, route attribution, reentry and capacity audits decide whether "
        "a result can enter the ENGINE matrix.",
        "",
        "### Key-scoped adaptive vector cycle",
        "",
        "This second lane tests slower/smaller profit locks and daily-only selective "
        "R3 settings only for MU_LONG, NVDA_LONG and LAC_SHORT. VT, TTD and ACN are "
        "intentionally excluded from this generic catalog.",
        (
            f"Current runner `{str(c4_vector_adaptive.get('runner_sha256') or '—')[:12]}`; "
            f"latest receipt `{c4_vector_adaptive.get('latest') or 'none'}`; "
            f"exact queue **{int((c4_vector_adaptive.get('status') or {}).get('exact_queued', 0) or 0)}**; "
            f"historical/unbound TIM receipts quarantined "
            f"**{int(c4_vector_adaptive.get('historical_tim_contract_receipts', 0) or 0)}/"
            f"{int(c4_vector_adaptive.get('unbound_tim_contract_receipts', 0) or 0)}**."
        ),
        "",
        "| key | adaptive bundles | top performance probe | best probe inside key TIM band |",
        "|---|---:|---|---|",
    ]
    for key in ("MU_LONG", "NVDA_LONG", "LAC_SHORT"):
        vector_row = (c4_vector_adaptive.get("keys") or {}).get(key, {})
        lines.append(
            f"| {key} | {int(vector_row.get('screened', 0) or 0)} | "
            f"{format_c4_vector_candidate(vector_row.get('top_performance'))} | "
            f"{format_c4_vector_candidate(vector_row.get('best_in_tim'))} |"
        )
    lines += [
        "",
        "### Targeted entry-replenishment cycle 3",
        "",
        "This isolated 38-arm lane added the registered WT force-open entry source "
        "to the strongest MU/NVDA/LAC exits. It is diagnostic only; identical "
        "returns expose an inert or absent entry condition rather than exit alpha.",
        (
            f"Latest receipt `{c4_vector_targeted.get('latest') or 'none'}`; "
            f"strict vector survivors **{sum(int(((c4_vector_targeted.get('keys') or {}).get(key) or {}).get('exact_pending', 0) or 0) for key in ('MU_LONG', 'NVDA_LONG', 'LAC_SHORT'))}**; "
            f"historical/unbound TIM-contract receipts quarantined "
            f"**{int(c4_vector_targeted.get('historical_tim_contract_receipts', 0) or 0)}/"
            f"{int(c4_vector_targeted.get('unbound_tim_contract_receipts', 0) or 0)}**. "
            "Quarantined receipts are retained as gray history and never enter either current-ranked column."
        ),
        "",
        "| key | targeted bundles | top performance probe | best probe inside key TIM band |",
        "|---|---:|---|---|",
    ]
    for key in ("MU_LONG", "NVDA_LONG", "LAC_SHORT"):
        vector_row = (c4_vector_targeted.get("keys") or {}).get(key, {})
        lines.append(
            f"| {key} | {int(vector_row.get('screened', 0) or 0)} | "
            f"{format_c4_vector_candidate(vector_row.get('top_performance'))} | "
            f"{format_c4_vector_candidate(vector_row.get('best_in_tim'))} |"
        )
    lines += [
        "",
        "### SHORT-native Donchian recovery cycle",
        "",
        "This 31-arm TTD/ACN lane covers only after a causally completed lower "
        "Donchian downside extension and upward re-cross, with optional side-aware "
        "partial banking or winner-velocity decay. It does not invert LONG stops.",
        (
            f"Latest receipt `{c4_short_native.get('latest') or 'none'}`; "
            f"strict vector survivors **{sum(int(((c4_short_native.get('keys') or {}).get(key) or {}).get('exact_pending', 0) or 0) for key in ('TTD_SHORT', 'ACN_SHORT'))}**; "
            f"historical/unbound TIM-contract receipts quarantined "
            f"**{int(c4_short_native.get('historical_tim_contract_receipts', 0) or 0)}/"
            f"{int(c4_short_native.get('unbound_tim_contract_receipts', 0) or 0)}**. "
            "Quarantined receipts are retained as gray history and never enter either current-ranked column."
        ),
        "",
        "| key | short-native bundles | top performance probe | best probe inside key TIM band |",
        "|---|---:|---|---|",
    ]
    for key in ("TTD_SHORT", "ACN_SHORT"):
        vector_row = (c4_short_native.get("keys") or {}).get(key, {})
        lines.append(
            f"| {key} | {int(vector_row.get('screened', 0) or 0)} | "
            f"{format_c4_vector_candidate(vector_row.get('top_performance'))} | "
            f"{format_c4_vector_candidate(vector_row.get('best_in_tim'))} |"
        )
    lines += [
        "",
        "### BB breakout entry-replenishment cycle 4",
        "",
        "This sealed two-arm lane adds the registered BB pullback entry to the "
        "strongest prior MU and NVDA exit bundles. It is vector research only "
        "and does not fill ENGINE matrix cells.",
        (
            f"Latest receipt `{c4_breakout_cycle4.get('latest') or 'none'}`; "
            f"strict vector survivors **{sum(int(((c4_breakout_cycle4.get('keys') or {}).get(key) or {}).get('exact_pending', 0) or 0) for key in ('MU_LONG', 'NVDA_LONG'))}**; "
            f"historical/unbound TIM-contract receipts quarantined "
            f"**{int(c4_breakout_cycle4.get('historical_tim_contract_receipts', 0) or 0)}/"
            f"{int(c4_breakout_cycle4.get('unbound_tim_contract_receipts', 0) or 0)}**."
        ),
        "",
        "| key | cycle-4 bundles | top performance probe | best probe inside key TIM band |",
        "|---|---:|---|---|",
    ]
    for key in ("MU_LONG", "NVDA_LONG"):
        vector_row = (c4_breakout_cycle4.get("keys") or {}).get(key, {})
        lines.append(
            f"| {key} | {int(vector_row.get('screened', 0) or 0)} | "
            f"{format_c4_vector_candidate(vector_row.get('top_performance'))} | "
            f"{format_c4_vector_candidate(vector_row.get('best_in_tim'))} |"
        )
    lines += [
        "",
        "## Path interpretation guardrails",
        "",
        "- `DC_LOW4_STOP_ENABLED` and stock `R1_DC_LOW4_3M_EMERGENCY_ENABLED` "
        "(legacy name; actual stock level is `dc_low4_5m`/`dc_high4_5m`) are "
        "**ENTRY-QUALITY FAILURE DIAGNOSTICS / LOSING-CHURN EXIT EVIDENCE**. They close a "
        "recently failed entry at a tight loss; they are not top/profit-taking exits. "
        "Below-B&H observations stay gray and preserved so they are not blindly retested.",
        "- The current `LONG_STRUCT_EXIT_TF` / `SHORT_STRUCT_EXIT_TF` path closes immediately "
        "on its selected structural break. The first armed-break → lower price/WT1 rebound-top "
        "baseline is **VEC-REJECTED / NO Tier-2 result**: "
        "`data/reports/vec_research/structural_wt_rebound_20260726T062654Z` scored 0/6 "
        "contract-valid LONG folds above side-and-hold, median alpha -4.70pp, mean TIM "
        "77.9%, and 50 exits / 30 losing. HAO_SHORT's +20.18pp is "
        "quarantined because its NPZ contract is invalid. Profit/MFE-gated variants remain "
        "research candidates and do not fill matrix cells.",
        "- The nested profit-gated grid "
        "`structural_wt_profit_grid_20260726T063646Z` selected one universal setting on "
        "MU+VT 2025Q4, then froze it. Validation had 9/9 winning exits but only 1/4 folds "
        "above B&H (median alpha -1.04pp). The remaining loss is reentry execution: delayed "
        "E10 reclaim filled 10.01% worse on recent MU and 1.46% worse on recent VT. A "
        "persistent resting-reclaim model is now the priority; this grid remains VEC-only.",
        "- Frozen exit settings with causal resting reclaim "
        "(`resting_reclaim_compare_20260726T064428Z`) improved median validation alpha to "
        "+1.94pp and cut mean reclaim overshoot from 0.80% to 0.02%. Recent MU returned "
        "+13.59% versus B&H +2.60%, but VT still trailed; this is evidence for the execution "
        "fix, not a universal promotion.",
        "- MU ladder discovery `struct_wt_resting_reclaim_probe_20260726T070000Z_MU_LONG` "
        "reached +409.93% versus B&H +204.90% (2.0006×) at 52.3% weighted exposure, but "
        "is **REJECTED versus the same-entry research control**. The source frozen 2026 "
        "ladder + 4h N=30 E02 fold returned +1,316.02% versus B&H +205.25% (6.4117×) "
        "at 77.1% exposure. The structural candidate discarded 906.09pp of return; beating "
        "B&H alone is not sufficient. Both paths remain VEC-only pending exact replay.",
        "- Nested/frozen same-ladder validation "
        "(`struct_wt_nested_frozen_20260726T073000Z`) selected only on MU Jan-Mar, "
        "then scored MU Apr-Jul without reselection. Structural returned +359.24% "
        "(2.191× B&H) but the same ladder returned +1,380.56% (8.420× B&H): "
        "-1,021.32pp and only 0.260× of ladder return. The universal VT score improved "
        "a losing ladder (-8.90% versus -61.85%) but remained below B&H (+1.57%). "
        "Verdict **REJECT / NO MATRIX**; every exit candidate must beat both B&H and "
        "the strongest same-entry/same-window causal control.",
        "- The complete registered `EXIT_STRUCTURAL_WT_LOWER_TOP` screen now supersedes "
        "the exploratory structural grids: 20 frozen top/bottom symbol-sides × 768 "
        "settings, with completed HTF bars, side isolation, resting reclaim and a "
        "compiled-to-Python parity gate. All 20 selected winners reproduced exactly, "
        "but **0/20 passed** both B&H + same-entry E02 and the 70–80% discovery/validation "
        "TIM gates. MU validated +1,127.00pp over B&H but -44.324pp versus E02 at "
        "65.21% TIM; SNDK's headline used 98.70% TIM. Path-fleet job 37 preserves all "
        "rows gray and correctly queues no exact replay.",
        "- `EXIT_PARTIAL_RUNNER` completed the corrected 192-setting registry grid on "
        "20 frozen keys (the listed 4×4×3×2×2 fields cannot equal 128; all clip sums "
        "are valid). It produced **0 strict survivors**. Five selected validation "
        "winners had zero fast partial fills; E05/E06 filled in only 25%/40% of their "
        "validation settings versus WT 100%. The winners created 919 per-exit reclaim "
        "obligations, filled 633 and left 286 open. MU made one partial (-$107.98 net) "
        "versus ten slow full exits and lost 122.07pp to E02. Job 39 preserves all "
        "20 rows gray and queues no exact replay.",
        "- `EXIT_E06_REGRESSION_RETEST` completed 3,840 same-entry candidates with "
        "**0 strict survivors**. The registry is stale: `rebound_atr` is actually "
        "passed to `corr_gate`, active code has no ATR rebound/later retest state, and "
        "there is no live Tradier E06 config key. Correlation gate 1.0 was 960/960 "
        "zero-signal/zero-fill (red); all 20 frozen winners had actual signals/exits. "
        "MU was in-band at 73.46% but lost 303.33pp to E02 and left one reclaim open. "
        "Job 46 preserves 20 gray rows and queues no exact replay.",
        "- Reentry invariant for structural research: after an exit, the stored exit/top level and "
        "reopen obligation remain latched. WT/stochastic vetoes may postpone reopening but "
        "must never erase it or allow price to outrun the stored level without reopening. "
        "This is a design requirement, not a measured performance claim.",
        "",
        "### Preserved dc_low4 diagnostic evidence (pre-repair; gray/quarantined)",
        "",
        "| key | switch | gain/mo | TIM | trades | status |",
        "|---|---|---:|---:|---:|---|",
        "| MU_LONG | False | 0.0275 | 0.13% | 17 | PRE-REPAIR / validation NULL |",
        "| MU_LONG | True | -0.1045 | 0.21% | 103 | LOSING-CHURN SIGNATURE; validation NULL |",
        "| MU_SHORT | False | -0.4670 | 6.89% | 48 | PRE-REPAIR / validation NULL |",
        "| MU_SHORT | True | -0.1061 | 0.15% | 155 | NEGATIVE + HIGH-CHURN; validation NULL |",
        "",
        "These historical rows are retained for diagnosis and excluded from the automatic "
        "matrix-fill queues. They are not valid proof because the repaired contract fields "
        "were absent.",
        "",
        "## New ladder / entry / exit / interaction strategy results",
        "",
    ]
    for key in keys:
        rows = strategy_rows[key]
        lines += [
            f"### {key}",
            "",
            "| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        if not rows:
            lines.append("| — | — | — | — | — | — | — | — | — | NO STRATEGY RESULTS |")
            lines.append("")
            continue
        for row in rows[: args.recent]:
            gain = row["gain_per_mo"]
            delta = row["delta_gain_mo_vs_bh"]
            bh = gain - delta if gain is not None and delta is not None else None
            capture = gain / bh if gain is not None and bh not in (None, 0) else None
            campaign = (row["campaign"] or "—").replace("stocks_baseline_v2_s4h__", "")
            lines.append(
                f"| {row['ts']} | {campaign} | `{row['param']}={row['value_json']}` | "
                f"{fmt(gain, 4)} | {fmt(bh, 4)} | {fmt(delta, 4)} | "
                f"{fmt(capture, 3)}× | {fmt(row['time_in_mkt_pct'], 2, '%')} | "
                f"{row['trades'] if row['trades'] is not None else '—'} | {verdict(row)} |"
            )
        lines.append("")

        candidates = [
            row for row in rows
            if row["delta_gain_mo_vs_bh"] is not None
            and row["trades"] is not None and row["trades"] >= 2
            and row["gain_per_mo"] is not None
            and (
                row["validation_status"] == "PASS"
                or is_expected_stock_capacity_saturation(row["result_audit_json"])
            )
            and (row["real_closes"] or 0) >= 3
            and (row["reentry_violations"] or 0) == 0
            and (row["inert"] or 0) == 0
            and row["time_in_mkt_pct"] is not None
            and tim_in_band(key, float(row["time_in_mkt_pct"]))
            and row["delta_gain_mo_vs_bh"] > 0
            and (
                row["gain_per_mo"]
                - row["delta_gain_mo_vs_bh"]
            )
            > 0
            and (
                row["gain_per_mo"]
                / (
                    row["gain_per_mo"]
                    - row["delta_gain_mo_vs_bh"]
                )
            )
            >= 2.0
        ]
        best = max(candidates, key=lambda row: row["delta_gain_mo_vs_bh"], default=None)
        if best is None:
            lines.append("Best trading candidate: **none with at least two trades and complete return metrics**.")
        else:
            lines.append(
                "Best measured trading candidate: "
                f"`{best['param']}={best['value_json']}` — gain/mo {fmt(best['gain_per_mo'], 4)}, "
                f"vs B&H/mo {fmt(best['delta_gain_mo_vs_bh'], 4)}, "
                f"TIM {fmt(best['time_in_mkt_pct'], 2, '%')}, trades {best['trades']} "
                f"(**{verdict(best)}**)."
            )
        lines.append("")

    lines += [
        "## Frozen walk-forward verdict",
        "",
        "| key | policy | discovery | frozen validation | validation TIM | verdict |",
        "|---|---|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | PENDING |")
            continue
        selection = payload["selection_test"]
        policy = selection["frozen_policy"]
        discovery = selection["discovery"]
        validation = selection["validation"]
        lines.append(
            f"| {key} | `{policy['strategy']} + {policy['reentry']}` | "
            f"{fmt(discovery.get('strategy_bh_multiple'), 3)}× B&H | "
            f"{fmt(validation.get('strategy_bh_multiple'), 3)}× B&H | "
            f"{fmt(validation.get('tim_rth_pct'), 2, '%')} | "
            f"{'PASS' if selection.get('pass') else 'FAIL / NO PROMOTION'} |"
        )
    lines += [
        "",
        "This table freezes the discovery choice before reading validation. It takes "
        "precedence over each window's separately re-optimized best row.",
        "",
        "## New causal-exit nested walk-forward",
        "",
        "| key | families | strict frozen result | strict TIM | clean frozen result | stability | verdict |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = robust_walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | — | PENDING |")
            continue
        strict = payload.get("strict_policy_frozen_validation")
        clean = payload.get("data_clean_frozen_validation")
        stability = payload.get("parameter_stability") or {}
        families = "/".join(payload.get("families") or [])
        if strict:
            strict_result = (
                f"{fmt(strict.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(strict.get('bh_compounded_gain_pct'), 3, '%')} B&H "
                f"({fmt(strict.get('gain_bh_multiple'), 3)}×)"
            )
            strict_tim = fmt(strict.get("weighted_tim_rth_pct"), 2, "%")
        else:
            strict_result = "NO STRICT FOLDS"
            strict_tim = "—"
        if clean:
            clean_result = (
                f"{fmt(clean.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(clean.get('bh_compounded_gain_pct'), 3, '%')} B&H "
                f"({fmt(clean.get('gain_bh_multiple'), 3)}×)"
            )
        else:
            clean_result = "—"
        repeat = fmt(100.0 * float(stability.get("exact_strategy_repeat_rate") or 0), 1, "%")
        promoted = bool(
            strict
            and strict.get("gain_bh_multiple") is not None
            and float(strict["gain_bh_multiple"]) > 1.0
            and bool(strict.get("tim_target_70_80"))
            and bool(strict.get("all_mandatory_reclaim"))
        )
        verdict_text = "SCREEN PASS; REPLAY REQUIRED" if promoted else "REJECT / NO PROMOTION"
        lines.append(
            f"| {key} | {families or '—'} | {strict_result} | {strict_tim} | "
            f"{clean_result} | {repeat} exact repeat | {verdict_text} |"
        )
    lines += [
        "",
        "This lane uses nested 12-month discovery with frozen three-month validation, "
        "faithful-engine cost semantics, and excludes VT folds overlapping its known source gap.",
        "",
        "## Partial-runner and regime nested walk-forward",
        "",
        "| key | family | frozen strategy vs B&H | weighted TIM | partial P&L / exits | stability | verdict |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = partial_regime_walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | — | PENDING |")
            continue
        results = payload.get("results") or {}
        for family in ("E12_PARTIAL_THEN_RUNNER", "E13_REGIME_SWITCHED"):
            row = results.get(family) or {}
            aggregate = row.get("aggregate_gap_clean_frozen") or {}
            stability = row.get("stability") or {}
            ratio = aggregate.get("strategy_to_bh_equity_ratio")
            comparison = (
                f"{fmt(aggregate.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(aggregate.get('bh_compounded_gain_pct'), 3, '%')} "
                f"({fmt(ratio, 3)}× equity)"
            )
            partial_pnl = aggregate.get("realized_partial_pnl_net_equity")
            partial_exits = aggregate.get("partial_exit_count")
            partial_text = (
                f"{fmt(partial_pnl, 4)} / {partial_exits}"
                if partial_pnl is not None and partial_exits is not None
                else "—"
            )
            stable = bool(stability.get("stable"))
            accepted = (
                row.get("status") == "ACCEPTED"
                and ratio is not None
                and float(ratio) > 1.0
            )
            lines.append(
                f"| {key} | {family.replace('_', ' ')} | {comparison} | "
                f"{fmt(aggregate.get('weighted_exposure_time_pct'), 2, '%')} | "
                f"{partial_text} | {'STABLE' if stable else 'UNSTABLE'} | "
                f"{'SCREEN PASS; REPLAY REQUIRED' if accepted else 'REJECT / NO PROMOTION'} |"
            )
    lines += [
        "",
        "E12 reports net realized partial P&L separately. Positive partial clips do not "
        "constitute edge when lost runner exposure and re-add timing leave compounded equity "
        "below B&H.",
        "",
        "## Causal exit-family factorial — parity-corrected vector research",
        "",
        "| key | best isolated pack | 3-fold strategy sum | side B&H sum | multiple | vs all-exits-off floor | TIM | fills | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key in ("MU_LONG", "TTD_SHORT", "ACN_SHORT"):
        payload = exit_factorial.get(key)
        if not payload:
            status = (
                "MISSING PARITY-CORRECTED ARTIFACT; OLD DC HEADLINE SUPPRESSED"
                if key in {"TTD_SHORT", "ACN_SHORT"}
                else "MISSING"
            )
            lines.append(f"| {key} | — | — | — | — | — | — | — | {status} |")
            continue
        best = payload["_best"]
        factors = best.get("factors") or {}
        metrics = best.get("metrics") or {}
        pack = ", ".join(
            (
                f"hybrid={'ON' if factors.get('hybrid') else 'OFF'}",
                f"DC={'ON' if factors.get('mtf_dc_reject') else 'OFF'}",
                f"SRS={'ON' if factors.get('srs') else 'OFF'}",
                (
                    f"WT={'ON' if factors.get('wt_final') else 'OFF'}"
                    if "wt_final" in factors
                    else "WT=not screened"
                ),
                f"hold={factors.get('min_hold_minutes')}m",
            )
        )
        gain = metrics.get("capital_return_pct_sum")
        bh = metrics.get("bh_capital_return_pct_sum")
        multiple = (
            float(gain) / float(bh)
            if gain is not None and bh is not None and abs(float(bh)) > 1e-12
            else None
        )
        alpha_floor = best.get("alpha_vs_same_entry_floor_pp")
        lead = (
            best.get("classification") == "SCREENING_LEAD_NOT_PROMOTABLE"
            and alpha_floor is not None
            and float(alpha_floor) > 0
        )
        lines.append(
            f"| {key} | `{pack}` | {fmt(gain, 3, '%')} | {fmt(bh, 3, '%')} | "
            f"{fmt(multiple, 3)}× | {fmt(alpha_floor, 3, 'pp')} | "
            f"{fmt(metrics.get('exposure_weighted_tim_pct_row_weighted'), 2, '%')} | "
            f"{metrics.get('exit_fills', '—')} | "
            f"{'VECTOR LEAD; EXACT c4 RUNNING/REQUIRED' if lead else 'GRAY: EXIT DOES NOT BEAT FLOOR'} |"
        )
    lines += [
        "",
        "TTD/ACN rows above come only from `MTF_DC_EXACT_VECTOR_PARITY_V1`: "
        "execution-row decisions against the latest causally completed current "
        "Donchian channel. The older completed-1h/prior-channel receipts are "
        "superseded and cannot be used as a digest fallback.",
        "",
        "### Superseded pre-parity DC packs — retained gray, never headline",
        "",
        "| key | old pack | corrected strategy sum | same-entry floor | corrected alpha | verdict |",
        "|---|---|---:|---:|---:|---|",
    ]
    for key in ("TTD_SHORT", "ACN_SHORT"):
        row = superseded_exit_factorial.get(key) or {}
        if not row:
            lines.append(f"| {key} | — | — | — | — | MISSING CORRECTION AUDIT |")
            continue
        lines.append(
            f"| {key} | `{row.get('pack') or '—'}` | "
            f"{fmt(row.get('capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(row.get('same_entry_floor_pct_sum'), 3, '%')} | "
            f"{fmt(row.get('alpha_vs_same_entry_floor_pp'), 3, 'pp')} | "
            "SUPERSEDED / GRAY |"
        )
    lines += [
        "",
        "These returns are sums over the same three frozen OOS folds, not one compounded "
        "holdout. Every arm uses the 0.05% stock round trip and completed HTF bars. "
        "The vector adapter does not model stateful DELTA/RZ bottom-bounce, so even an "
        "8× row is research-only until its isolated exact c4 replay passes route "
        "attribution, B&H, same-entry control, reentry, drawdown and fingerprint gates.",
        "",
        "### Exact c4 finalist replays",
        "",
        "Exact TTD is reported independently from the corrected vector table: "
        "its 1h DC result includes the exact exit/reentry lifecycle and does not "
        "resurrect the superseded prior-channel vector alpha claim.",
        "",
        "| key/variant | strategy | side B&H | capture | TIM | real closes | cost contract | exact verdict |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    if not exact_exit_factorial:
        lines.append("| — | — | — | — | — | — | — | EXACT REPLAYS RUNNING/MISSING |")
    for payload in exact_exit_factorial:
        metrics = payload.get("metrics") or {}
        audit = payload.get("audit") or {}
        result = audit.get("result") or {}
        fill_ratio = float(result.get("requested_fill_ratio", 0) or 0)
        expected_cap = bool(
            audit.get("status") == "PASS_WITH_CAPACITY_CLAMPS"
            and audit.get("capacity_respected")
            and fill_ratio >= 0.99
            and float(result.get("max_requested_mult", 0) or 0) <= 8.0
        )
        capture = metrics.get("capture_vs_bh")
        tim = metrics.get("time_in_mkt_pct")
        exact_key = str(payload.get("_key") or "")
        meets_perf = bool(
            capture is not None
            and float(capture) >= 2.0
            and tim is not None
            and exact_key
            and tim_in_band(exact_key, float(tim))
            and int(float(result.get("real_closes", 0) or 0)) >= 3
            and int(float(result.get("reentry_violations", 0) or 0)) == 0
            and (audit.get("status") == "PASS" or expected_cap)
        )
        exact_verdict = (
            "SUPERSEDED PRE-PARITY PACK / GRAY"
            if payload.get("_superseded")
            else (
            "EXACT >=2X RESEARCH EDGE; CAP SATURATION / OOS BLOCKED"
            if meets_perf and expected_cap
            else (
                "EXACT >=2X RESEARCH EDGE; OOS/PROMOTION BLOCKED"
                if meets_perf
                else "GRAY DISCARD / STRUCTURAL OR PERFORMANCE FAIL"
            )
            )
        )
        lines.append(
            f"| {payload['_key']}/{payload['_variant']} | "
            f"{fmt(metrics.get('acc_gain_pct'), 4, '%')} | "
            f"{fmt(metrics.get('bh_pct'), 4, '%')} | "
            f"{fmt(capture, 4)}× | {fmt(tim, 2, '%')} | "
            f"{int(float(result.get('real_closes', 0) or 0))} | "
            "0.05% stock RT | "
            f"{exact_verdict} |"
        )
    lines += [
        "",
        "A large dollar cost in these rows is turnover at 0.05%, not a restored "
        "crypto/legacy rate. TTD's 237 DC closes cost $1,169.80 and still netted "
        "$11,721.07. ACN's rejected DC+WT pack cost $2,357.64 across 553 closes; "
        "405 real WT-final closes accounted for -$9,505.41 net while DC itself "
        "contributed +$6,661.65.",
        "",
        "## Causal ladder multiplier walk-forward",
        "",
        "| key | frozen OOS strategy | B&H | multiple | weighted TIM | exact-engine parity | verdict |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for key in keys:
        payload = ladder_walk_forward.get(key) or {}
        research = payload.get("research")
        exact = payload.get("exact")
        if not research:
            status = "QUARANTINED / INVALID DATA" if key == "HAO_SHORT" else "PENDING"
            lines.append(f"| {key} | — | — | — | — | — | {status} |")
            continue
        aggregate = research.get("frozen_oos_aggregate") or {}
        promotion_allowed = bool(
            (research.get("manifest") or {}).get("promotion_allowed")
        )
        exact_ok = bool(
            exact
            and exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and (exact.get("audit") or {}).get("status") == "PASS"
        )
        exact_text = "PASS" if exact_ok else ("FAIL" if exact else "PENDING")
        multiple = aggregate.get("strategy_bh_multiple")
        accepted = bool(
            exact_ok
            and promotion_allowed
            and multiple is not None
            and float(multiple) > 1.0
        )
        verdict_text = (
            "PROMOTION ELIGIBLE"
            if accepted
            else (
                "RESEARCH EDGE; PROMOTION BLOCKED"
                if exact_ok and multiple is not None and float(multiple) > 1.0
                else "REJECT / NO PROMOTION"
            )
        )
        lines.append(
            f"| {key} | {fmt(aggregate.get('capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('bh_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(multiple, 3)}× | "
            f"{fmt(aggregate.get('exposure_weighted_tim_pct_row_weighted'), 2, '%')} | "
            f"{exact_text} | {verdict_text} |"
        )
    lines += [
        "",
        "The MU exact replay covers the latest frozen fold: 34/34 actions, "
        "+1,316.021% return, 77.09% weighted TIM, zero future HTF sources, zero clamps, "
        "and exact signal/fill/accounting parity. It remains research-only because the "
        "campaign explicitly sets `promotion_allowed=false`. VT fails frozen OOS; HAO "
        "remains data-quarantined.",
        "",
        "## MU stable-ladder Pareto holdout",
        "",
        "| candidate | discovery | untouched holdout | B&H | multiple | TIM | exact | verdict |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not mu_pareto_holdout:
        lines.append("| — | — | — | — | — | — | — | NO RECEIPT |")
    else:
        candidate = mu_pareto_holdout.get("candidate") or {}
        discovery = mu_pareto_holdout.get("discovery") or {}
        final = mu_pareto_holdout.get("final") or {}
        exact = mu_pareto_holdout.get("exact_v3") or {}
        exact_ok = bool(
            exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and int(exact.get("actions_scheduled") or 0)
            == int(exact.get("actions_executed") or -1)
            and int(exact.get("future_htf_count") or 0) == 0
        )
        failures = ", ".join(final.get("failures") or []) or "none"
        verdict_text = (
            "PROMOTION ELIGIBLE"
            if bool(final.get("pass"))
            and exact_ok
            and bool(mu_pareto_holdout.get("promotion_allowed"))
            else f"GRAY / HOLDOUT REJECT ({failures})"
        )
        lines.append(
            f"| C{candidate.get('number', '—')} `{candidate.get('label', '—')}` | "
            f"{'PASS' if discovery.get('pass') else 'FAIL'} | "
            f"{fmt(final.get('return_pct'), 3, '%')} | "
            f"{fmt(final.get('bh_return_pct'), 3, '%')} | "
            f"{fmt(final.get('bh_multiple'), 3)}× | "
            f"{fmt(final.get('weighted_tim_pct'), 2, '%')} | "
            f"{'PASS' if exact_ok else 'FAIL/PENDING'} | {verdict_text} |"
        )
    lines += [
        "",
        "This lane used a preregistered Pareto contract before opening the final fold. "
        "A strong return cannot repair a missed exposure gate after the result is known; "
        "the row therefore remains gray and is not written to the promotion matrix.",
        "",
        "## HAO SHORT-native phase 3",
        "",
        "| contract | candidates | E02 beat side benchmark | E05 beat side benchmark | "
        "TIM-valid | final | exact | verdict |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    if not hao_short_phase3:
        lines.append("| — | — | — | — | — | — | — | NO RECEIPT |")
    else:
        exits = hao_short_phase3.get("exit_summary") or {}
        e02 = exits.get("EXIT_E02_DONCHIAN") or {}
        e05 = exits.get("EXIT_E05_DIVERGENCE_RETEST") or {}
        tim_valid = sum(
            int((row or {}).get("weighted_tim_70_80_both_discovery_folds") or 0)
            for row in (e02, e05)
        )
        lines.append(
            f"| `{hao_short_phase3.get('contract', '—')}` | "
            f"{hao_short_phase3.get('candidate_count', '—')} | "
            f"{e02.get('beats_short_bh_or_cash_both_discovery_folds', '—')} | "
            f"{e05.get('beats_short_bh_or_cash_both_discovery_folds', '—')} | "
            f"{tim_valid} | "
            f"{hao_short_phase3.get('final_fold_status', '—')} | "
            f"{hao_short_phase3.get('exact_replay_status', '—')} | "
            f"{hao_short_phase3.get('status', '—')} |"
        )
    lines += [
        "",
        "V1–V3 were explicitly invalidated. V4 fixes the persistent-reentry state "
        "contract, uses the shared completed-parent clock, and keeps the final fold sealed "
        "because no discovery candidate met every exposure/control gate.",
        "",
        "## HAO SHORT exposure/persistence phase 4",
        "",
        "| contract | discovery strict | D1 strategy / B&H / TIM | "
        "D2 strategy / B&H / TIM | final strategy / B&H / TIM | exact | verdict |",
        "|---|---:|---|---|---|---|---|",
    ]
    if not hao_short_phase4:
        lines.append("| — | — | — | — | — | — | NO RECEIPT |")
    else:
        folds = hao_short_phase4.get("frozen_discovery") or []
        final = hao_short_phase4.get("final") or {}

        def phase4_fold_text(index: int) -> str:
            if index >= len(folds):
                return "—"
            row = folds[index] or {}
            return (
                f"{fmt(row.get('strategy_return_pct'), 2, '%')} / "
                f"{fmt(row.get('bh_return_pct'), 2, '%')} / "
                f"{fmt(row.get('weighted_tim_pct'), 2, '%')}"
            )

        final_text = (
            f"{fmt(final.get('strategy_return_pct'), 2, '%')} / "
            f"{fmt(final.get('bh_return_pct'), 2, '%')} / "
            f"{fmt(final.get('weighted_tim_pct'), 2, '%')}"
        )
        lines.append(
            f"| `{hao_short_phase4.get('contract', '—')}` | "
            f"{hao_short_phase4.get('discovery_strict_count', '—')} | "
            f"{phase4_fold_text(0)} | {phase4_fold_text(1)} | {final_text} | "
            f"{hao_short_phase4.get('exact_replay_status', '—')} | "
            f"{hao_short_phase4.get('status', '—')} |"
        )
    lines += [
        "",
        "The frozen phase-4 E02 policy achieved 70–80% weighted exposure and beat "
        "side-aware B&H plus the phase-3 same-exit control in both discovery folds. "
        "Its untouched final return remained strong but weighted TIM fell to 36.05%; "
        "the row is gray, exact did not run, and no matrix/live state changed.",
        "",
        "## Non-MU pilot deployed-alpha rescreen",
        "",
        "> Selection uses return per pre-cost committed dollar-time against the "
        "better of side-aware B&H and cash. Ratios require positive B&H >=20pp. "
        "Fold 3 stays sealed unless both discovery folds pass; private schedule "
        "replay is not ordinary-engine/live parity.",
        "",
        "| key | source | profile | fold 1 deployed alpha / TIM | "
        "fold 2 deployed alpha / TIM | fills / clamps | final | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    if not other_pilot_audit:
        lines.append("| — | — | — | — | — | — | — | NO RECEIPT |")
    else:
        for row in other_pilot_audit.get("rows") or []:
            nearest = row.get("nearest_discovery_profile") or {}
            gates = nearest.get("discovery_gates") or []

            def pilot_fold(index: int) -> str:
                if index >= len(gates):
                    return "—"
                gate = gates[index]
                return (
                    f"{fmt(gate.get('deployed_alpha_vs_bh_or_cash_pp'), 3, 'pp')} / "
                    f"{fmt(gate.get('tim_pct'), 2, '%')}"
                )

            fills = sum(int(gate.get("fills") or 0) for gate in gates)
            clamps = sum(int(gate.get("clamps") or 0) for gate in gates)
            profile = (
                f"${nearest.get('capacity_usd')}/N{nearest.get('exit_n')}"
                if nearest
                else "—"
            )
            source_text = (
                "PASS"
                if row.get("source_valid")
                else "BLOCKED: "
                + ",".join(row.get("source_errors") or ["unknown"])
            )
            final_text = (
                "SEALED"
                if row.get("vector_holdout") is None
                else str(
                    (row.get("vector_holdout") or {})
                    .get("gate", {})
                    .get("pass", "ERROR")
                )
            )
            lines.append(
                f"| {row.get('key', '—')} | {source_text} | `{profile}` | "
                f"{pilot_fold(0)} | {pilot_fold(1)} | {fills}/{clamps} | "
                f"{final_text} | {row.get('verdict', '—')} |"
            )
    lines += [
        "",
        "No key may advance from this section without stable discovery, a real "
        "ordinary `backtest_v8_engine` decision-path run, and live state-machine "
        "parity. A vector/private-schedule pass remains gray.",
        "",
        "## Top-10 ladder exposure retune",
        "",
        "> Frozen nested-OOS research. Aggregate exposure can hide unstable folds; a row "
        "is not promotable unless every fold also passes the fixed-control, causality, "
        "capacity, reclaim, and exposure gates.",
        "",
        "| key | strategy | B&H | multiple | identical control | alpha control | TIM | "
        "fold TIM | fills | clamps | exact | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|",
    ]
    if not ladder_retunes:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | NO ARTIFACTS |")
    for payload in ladder_retunes:
        aggregate = payload.get("frozen_oos_aggregate") or {}
        folds = payload.get("outer_folds") or []
        fold_tim = " / ".join(
            fmt(
                (row.get("validation_metrics") or row).get(
                    "exposure_weighted_tim_pct_row_weighted",
                    (row.get("validation_metrics") or row).get(
                        "exposure_weighted_tim_pct",
                        (row.get("validation_metrics") or row).get("weighted_tim_pct"),
                    ),
                ),
                2,
                "%",
            )
            for row in folds
        ) or "—"
        exact = payload.get("_exact")
        exact_ok = bool(
            exact
            and exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and (exact.get("audit") or {}).get("status") == "PASS"
        )
        strict = bool(aggregate.get("vector_survivor"))
        relaxed = bool(aggregate.get("relaxed_bh_survivor"))
        if strict and exact_ok:
            ladder_verdict = "EXACT PASS; PROMOTION POLICY CHECK"
        elif strict:
            ladder_verdict = "VECTOR SURVIVOR; EXACT REQUIRED"
        elif relaxed and exact_ok:
            ladder_verdict = "RESEARCH EDGE; FOLD/CONTROL BLOCKED"
        elif relaxed:
            ladder_verdict = "RESEARCH EDGE; EXACT/FOLD BLOCKED"
        else:
            ladder_verdict = "REJECT / NO PROMOTION"
        lines.append(
            f"| {payload.get('_key', '—')} | "
            f"{fmt(aggregate.get('capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('bh_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('strategy_bh_multiple'), 3)}× | "
            f"{fmt(aggregate.get('same_control_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('delta_vs_same_control_pp_sum'), 3, 'pp')} | "
            f"{fmt(aggregate.get('exposure_weighted_tim_pct_row_weighted'), 2, '%')} | "
            f"{fold_tim} | {fmt((aggregate.get('fill_ratio') or 0) * 100, 1, '%')} | "
            f"{aggregate.get('clamp_count', '—')} | "
            f"{'PASS' if exact_ok else ('FAIL' if exact else '—')} | "
            f"{ladder_verdict} |"
        )
    lines += [
        "",
        "The retune keeps exits fixed at completed-4h E02 N=30. It changes only the "
        "bounded ladder curve/trigger semantics, so alpha against the identical control "
        "does not come from a different exit or a different B&H budget.",
        "",
        "## Isolated vector research — not matrix evidence",
        "",
        "> Fast causal screens only. These rows never fill or color Tier-2 cells. Promotion "
        "requires an independent audit, a faithful-engine replay, stable out-of-sample behavior, "
        "real closes, and a changed trade fingerprint.",
        "",
        "| key | window | best visible candidate | gain | B&H | multiple | TIM | data/policy status |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payloads = vec_research.get(key) or []
        if not payloads:
            lines.append(f"| {key} | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |")
            continue
        for payload in payloads:
            candidate = vec_candidate(payload)
            window = (
                f"{payload.get('start') or 'unknown'} → "
                f"{payload.get('end_exclusive') or 'present'}"
            )
            contract_ok = bool(payload.get("contract_valid"))
            diagnostic = bool(payload.get("invalid_data_diagnostic"))
            if not candidate:
                status = "QUARANTINED / INVALID DATA" if diagnostic or not contract_ok else "NO CANDIDATE"
                lines.append(
                    f"| {key} | {window} | — | — | — | — | — | {status} |"
                )
                continue
            gain = candidate.get("gain_pct")
            bh = candidate.get("bh_net_side_pct")
            multiple = candidate.get("strategy_bh_multiple")
            tim = candidate.get("tim_rth_pct")
            policy = bool(candidate.get("policy_compliant"))
            artifact_path = str((BASE / payload.get("_artifact", "")).resolve())
            replay = exact_replays.get(artifact_path)
            if diagnostic or not contract_ok:
                status = "QUARANTINED / INVALID DATA"
            elif replay and replay.get("status") == "FAIL":
                status = "EXACT ENGINE REPLAY FAIL; REJECT"
            elif (
                replay
                and replay.get("status") == "PASS"
                and multiple is not None
                and float(multiple) > 1.0
            ):
                status = "EXACT EXECUTION REPLAY PASS; ROBUSTNESS/PROMOTION BLOCKED"
            elif policy and multiple is not None and float(multiple) > 1.0:
                status = "EXPOSURE/RECLAIM PASS; FAITHFUL REPLAY PENDING"
            elif multiple is not None and float(multiple) <= 1.0:
                status = "BELOW B&H; REJECT"
            else:
                status = "RESEARCH ONLY / POLICY FAIL"
            label = f"{candidate.get('strategy', '—')} + {candidate.get('reentry', '—')}"
            lines.append(
                f"| {key} | {window} | `{label}` | {fmt(gain, 3, '%')} | "
                f"{fmt(bh, 3, '%')} | {fmt(multiple, 3)}× | {fmt(tim, 2, '%')} | {status} |"
            )

    lines += [
        "",
        "The full-period MU multiple is an optimization-screen headline, not a robust claim. "
        "Read the holdout row beside it: exposure drift or sub-B&H holdout performance blocks "
        "promotion even when the full-period row is above B&H.",
        "",
    ]

    lines += [
        "## Recent-trade coherent-bundle screen",
        "",
        "> Finite vector research companion, not another daemon. It cannot write live "
        "configuration or canonical ENGINE cells; only strict frozen-fold survivors "
        "can become exact-replay candidates.",
        "",
    ]
    if not recent_bundles:
        lines += ["No recent-trade bundle campaign receipt yet.", ""]
    else:
        attempts = recent_bundles.get("attempts") or {}
        priority_n = sum((attempts.get("PRIORITY") or {}).values())
        explore_n = sum((attempts.get("EXPLORE") or {}).values())
        lines += [
            f"- Campaign: `{recent_bundles.get('campaign_id') or '—'}`; "
            f"freshness {iso_age(recent_bundles.get('generated_at'), now)}.",
            f"- Cohort: **{recent_bundles.get('distinct_keys', 0):,} symbol-side keys**; "
            f"buckets: `{recent_bundles.get('cohort_buckets') or {}}`.",
            f"- Handles: **{recent_bundles.get('handles', 0):,}**; "
            f"states: `{recent_bundles.get('states') or {}}`.",
            f"- Claims: **{recent_bundles.get('claim_sequence', 0):,}** "
            f"(priority {priority_n:,}, exploration {explore_n:,}; "
            f"realized exploration {float(recent_bundles.get('realized_explore_share') or 0):.1%}).",
            "- Historical scheduler contract: deterministic 9:1 allocation and "
            "35/35/30 frozen folds. Current repaired TRB handoff instead uses "
            "ranked key bands (top 10 per side 50–80%; remainder 20–60%).",
            f"- Exact-pending vector survivors: "
            f"`{recent_bundles.get('exact_pending') or []}`.",
            "",
            "| key | bundle | lane | state | fold | return | benchmark | alpha | TIM | trades |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
        recent_rows = recent_bundles.get("latest_receipts") or []
        if not recent_rows:
            lines.append("| — | — | — | — | — | — | — | — | — | — |")
        for row in recent_rows[:20]:
            lines.append(
                f"| {row.get('position_key') or '—'} | "
                f"`{row.get('bundle_id') or '—'}` | {row.get('lane') or '—'} | "
                f"{row.get('status') or '—'} | {row.get('final_fold') or '—'} | "
                f"{fmt(row.get('strategy_return_pct'), 3, '%')} | "
                f"{fmt(row.get('side_benchmark_pct'), 3, '%')} | "
                f"{fmt(row.get('alpha_vs_benchmark_pp'), 3, 'pp')} | "
                f"{fmt(row.get('tim_pct'), 2, '%')} | "
                f"{row.get('trades') if row.get('trades') is not None else '—'} |"
            )
        lines.append("")

    lines += [
        "## Top/bottom-10 entry/exit path fleet",
        "",
        "> Claimable vector-first research queue. Control rows establish the frozen benchmark "
        "that later paths must beat; they are not exact-engine promotion evidence.",
        "",
    ]
    if not path_fleet:
        lines += [
            "Path fleet ledger is missing.",
            "",
        ]
    else:
        universe = path_fleet.get("universe") or {}
        snapshot = universe.get("tradeable_snapshot") or {}
        top_long = ", ".join(
            row.get("symbol", "—") for row in universe.get("top_long") or []
        )
        bottom_short = ", ".join(
            row.get("symbol", "—") for row in universe.get("bottom_short") or []
        )
        states = ", ".join(
            f"{name}={count:,}"
            for name, count in sorted((path_fleet.get("states") or {}).items())
        )
        lines += [
            f"- Jobs: **{path_fleet.get('job_count', 0):,}**; states: {states or '—'}.",
            f"- Frozen tradeable hashes: LONG `{snapshot.get('long_sha256') or '—'}`; "
            f"SHORT `{snapshot.get('short_sha256') or '—'}`.",
            f"- Top LONG cohort: {top_long or '—'}.",
            f"- Bottom SHORT cohort: {bottom_short or '—'}.",
            "",
            "| path | key | stage | metric scope / units | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        fleet_rows = path_fleet.get("rows") or []
        if not fleet_rows:
            lines.append("| — | — | — | — | — | — | — | — | — | — | — | — |")
        for row in fleet_rows:
            strategy = row.get("strategy_return_pct")
            bh = row.get("bh_return_pct")
            multiple = (
                float(strategy) / float(bh)
                if strategy is not None and bh is not None and float(bh) > 0
                else None
            )
            lines.append(
                f"| `{row.get('path_id') or '—'}` | "
                f"{row.get('symbol') or '—'}_{row.get('side') or '—'} | "
                f"{row.get('stage') or '—'} | "
                f"{format_fleet_metric_scope(row)} | "
                f"{row.get('status') or '—'} | "
                f"{fmt(strategy, 3, '%')} | {fmt(bh, 3, '%')} | "
                f"{fmt(multiple, 3)}× | {fmt(row.get('alpha_vs_bh_pp'), 3, 'pp')} | "
                f"{fmt(row.get('alpha_vs_control_pp'), 3, 'pp')} | "
                f"{fmt(row.get('tim_pct'), 2, '%')} | {row.get('trades', '—')} |"
            )
        short_rows_present = any(row.get("side") == "SHORT" for row in fleet_rows)
        lines += [
            "",
            "Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of "
            "outer-validation-fold capital-return percentages and are not a "
            "single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` "
            "rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` "
            "rows are historical evidence and must not drive promotion.",
            "",
            (
                "SHORT vector controls are present but remain research-only until exact replay "
                "and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-"
                "reclaim gates pass. No LONG result is inverted or pooled."
                if short_rows_present
                else
                "The SHORT cohort remains blocked until its causal ladder mirror passes the same "
                "completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim "
                "contract. No LONG result is inverted or pooled to manufacture SHORT evidence."
            ),
            "",
        ]

    lines += [
        "## Campaign activity",
        "",
        "| tier | campaign | rows | latest | age |",
        "|---|---|---:|---|---:|",
    ]
    for row in campaign_rows:
        lines.append(
            f"| {row['tier']} | {row['campaign']} | {row['n']:,} | "
            f"{row['latest']} | {iso_age(row['latest'], now)} |"
        )

    lines += [
        "",
        "## Reading the matrix",
        "",
        "- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.",
        "- White: B&H comparison unavailable; measured evidence only, never promotion.",
        "- Gray: every below-B&H or intentionally discarded result; retain so it is not blindly retested.",
        "- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.",
        "- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, "
        "not an optimized strategy.",
        "",
    ]
    con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text("\n".join(lines))
    _set_local_immutable(out, False)
    tmp.replace(out)
    _set_local_immutable(out, True)
    active_keys = tuple(
        f"{symbol}_{side}"
        for side in ("LONG", "SHORT")
        for symbol in ranked_symbols(side)
    )
    tim_policy = {
        key: {
            field: value
            for field, value in tim_contract(*parse_key(key)).items()
            if field
            in {
                "rank",
                "cohort",
                "tim_min_pct",
                "tim_max_pct",
                "source",
            }
        }
        for key in active_keys
    }
    contract_versions = sorted(
        {
            fingerprint.split(":", 1)[0]
            for values in all_contract_fps.values()
            for fingerprint in values
            if fingerprint
        }
    )
    # A Mac checkout may not carry every c5 frozen NPZ.  The report has
    # already failed closed to zero exact rows in that case; do not discard
    # the rendered digest or skip the email entirely merely because a
    # provenance fingerprint cannot be computed locally.
    if len(contract_versions) != 1:
        # The exported gzip carries the contract identity even when this
        # checkout cannot compute the current NPZ fingerprints.  Reuse that
        # identity so the digest sidecar remains hash/metadata-consistent;
        # absence of local NPZs is disclosed by the zero exact-row counts and
        # the matrix guard, not by corrupting the provenance contract.
        header_contract = None
        try:
            import gzip as _gzip
            with _gzip.open(canonical_matrix, "rt") as handle:
                for line in handle:
                    if not line.startswith("#"):
                        break
                    if line.startswith("# CURRENT_CONTRACT_VERSION="):
                        header_contract = line.split("=", 1)[1].strip()
                        break
        except (OSError, EOFError):
            header_contract = None
        contract_versions = [header_contract or "UNAVAILABLE_NO_LOCAL_NPZ"]
    provenance = {
        "schema": "switch-matrix-trb-current-digest-v1",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "campaign": CURRENT_ENGINE_CAMPAIGN,
        "engine_cutoff": CURRENT_ENGINE_CUTOFF,
        "contract_version": contract_versions[0],
        "matrix_scope": (
            "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
        ),
        "canonical_matrix": "data/reports/SWITCH_MATRIX_TRB.csv.gz",
        "canonical_matrix_sha256": hashlib.sha256(
            canonical_matrix.read_bytes()
        ).hexdigest(),
        "digest": "data/reports/SWITCH_MATRIX_TRB_DIGEST.md",
        "digest_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "accepted_current_engine_rows": current_engine,
        "active_key_count": len(active_keys),
        "tim_policy": tim_policy,
    }
    provenance_path = out.with_suffix(out.suffix + ".provenance.json")
    provenance_tmp = provenance_path.with_suffix(
        provenance_path.suffix + ".tmp"
    )
    provenance_tmp.write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n"
    )
    _set_local_immutable(provenance_path, False)
    provenance_tmp.replace(provenance_path)
    _set_local_immutable(provenance_path, True)
    print(out)


if __name__ == "__main__":
    main()
