#!/usr/bin/env python3
"""Audit the current wide SWITCH_MATRIX_TRB export for MU_LONG.

This is a read-only monitoring audit.  It deliberately does not treat a fresh
or populated matrix as live-promotion evidence: identical fingerprints,
account-private knobs and out-of-domain values are counted explicitly.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "data/reports/SWITCH_MATRIX_TRB.csv.gz"
DEFAULT_DIGEST = ROOT / "data/reports/SWITCH_MATRIX_TRB_DIGEST.md"
DEFAULT_OUTPUT = ROOT / "data/reports/MU_MATRIX_FILL_AUDIT_20260729.json"
SYMBOL_KEY = "MU_LONG"
ACCOUNT = "trb"
OTHER_ACCOUNT_PREFIXES = ("TRA_", "TRC_")

# These indicators have a natural [0, 100] domain.  Budget/size fields that
# merely contain RSI/MFI in their path name are intentionally not included.
BOUNDED_0_100_SUFFIXES = (
    "_K_MAX_LONG",
    "_K_EXHAUSTED_LONG",
    "_MFI_MAX_TRADIER",
    "_MFI_ENTRY_LONG_TRADIER",
    "_MFI_FLIP_EXIT_LONG_THRESHOLD",
    "_RSI_EXIT_THRESHOLD",
    "_RSI_EXIT_THRESHOLD_LONG",
    "_RSI2_EXIT_THRESHOLD_LONG",
    "_RSI_EXIT_LONG_TRADIER",
    "RSI_EXIT_LONG_TRADIER",
    "CONNORS_RSI_EXIT_THRESHOLD",
    "REENTRY_STOCH_K_MAX_LONG",
    "RSI2_EXIT_THRESHOLD_LONG",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_wide_matrix(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as handle:
        non_comments = (line for line in handle if not line.startswith("#"))
        return list(csv.DictReader(non_comments))


def _effective_key(row: dict[str, str]) -> str:
    return (row.get("sub_setting") or row.get("main_switch") or "").strip()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _out_of_domain(key: str, raw_value: str) -> bool:
    if not any(key.endswith(suffix) for suffix in BOUNDED_0_100_SUFFIXES):
        return False
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return False
    return not 0.0 <= value <= 100.0


def audit(matrix_path: Path, digest_path: Path, as_of: datetime) -> dict[str, Any]:
    rows = _read_wide_matrix(matrix_path)
    filled = [row for row in rows if (row.get(SYMBOL_KEY) or "").strip()]
    values = [float(row[SYMBOL_KEY]) for row in filled]
    value_counts = Counter(round(value, 4) for value in values)

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in filled:
        groups[_effective_key(row)].append(row)
    multi = {key: group for key, group in groups.items() if len(group) > 1}
    identical = {
        key: group
        for key, group in multi.items()
        if len({round(float(row[SYMBOL_KEY]), 8) for row in group}) == 1
    }

    wrong_account = [
        row for row in filled if _effective_key(row).startswith(OTHER_ACCOUNT_PREFIXES)
    ]
    out_of_domain = [
        row
        for row in filled
        if _out_of_domain(_effective_key(row), row.get("value", ""))
    ]
    best = sorted(filled, key=lambda row: float(row[SYMBOL_KEY]), reverse=True)[:5]

    matrix_age_hours = max(
        0.0,
        (
            as_of
            - datetime.fromtimestamp(matrix_path.stat().st_mtime, timezone.utc)
        ).total_seconds()
        / 3600.0,
    )
    dominant_value, dominant_count = value_counts.most_common(1)[0] if value_counts else (None, 0)
    positive = [row for row in filled if float(row[SYMBOL_KEY]) > 0.0]

    return {
        "audit": "MU_SWITCH_MATRIX_FILL_SNAPSHOT_V1",
        "as_of_utc": as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {"account": ACCOUNT, "symbol_side": SYMBOL_KEY},
        "sources": {
            "matrix": _display_path(matrix_path),
            "matrix_sha256": sha256(matrix_path),
            "digest": _display_path(digest_path),
            "digest_sha256": sha256(digest_path),
            "matrix_age_hours": matrix_age_hours,
        },
        "counts": {
            "manifest_rows": len(rows),
            "mu_filled_rows": len(filled),
            "status": dict(sorted(Counter(row.get("status", "") for row in filled).items())),
            "distinct_effective_keys": len(groups),
            "multi_value_keys": len(multi),
            "identical_output_multi_value_keys": len(identical),
            "wrong_account_rows": len(wrong_account),
            "out_of_domain_rows": len(out_of_domain),
            "positive_delta_rows": len(positive),
        },
        "fingerprint_concentration": {
            "dominant_delta_gain_per_month_vs_bh": dominant_value,
            "rows": dominant_count,
            "share_pct": 100.0 * dominant_count / len(filled) if filled else 0.0,
        },
        "best_visible_rows": [
            {
                "key": _effective_key(row),
                "value": row.get("value"),
                "delta_gain_per_month_vs_bh": float(row[SYMBOL_KEY]),
                "status": row.get("status"),
            }
            for row in best
        ],
        "wrong_account_examples": [
            {"key": _effective_key(row), "value": row.get("value")}
            for row in wrong_account[:10]
        ],
        "out_of_domain_examples": [
            {"key": _effective_key(row), "value": row.get("value")}
            for row in out_of_domain[:20]
        ],
        "interpretation": {
            "fresh_monitoring_export": matrix_age_hours <= 1.0,
            "truthful_status_labels": all(
                row.get("status") in {"RECONNECT", "INERT_AT_VALUE", "OK"}
                for row in filled
            ),
            "beats_bh_anywhere": bool(positive),
            "suitable_as_live_promotion_evidence": False,
            "blockers": [
                "ordinary live decision-path parity is not established",
                "per-symbol/account overlays have higher _cfg precedence than V8_OVERRIDE_FILE globals, so tested cells can be shadowed",
                "TRB matrix contains TRA_/TRC_ account-private rows",
                "generic scaled ranges emitted values outside natural oscillator domains",
                "no current MU_LONG cell beats the same-key B&H floor",
            ],
        },
        "required_fix": {
            "override_precedence": (
                "add a backtest-only explicit-cell layer above per-account and "
                "global per-symbol overlays while retaining every untargeted baseline field"
            ),
            "range_generation": "use account-aware semantic domains, then regenerate affected cells",
            "promotion_rule": "require changed decision/trade fingerprints and ordinary-live parity",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--digest", type=Path, default=DEFAULT_DIGEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", help="ISO-8601 UTC audit time (default: now)")
    args = parser.parse_args()
    as_of = (
        datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        if args.as_of
        else datetime.now(timezone.utc)
    )
    result = audit(args.matrix.resolve(), args.digest.resolve(), as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
