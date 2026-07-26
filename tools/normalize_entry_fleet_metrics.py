#!/usr/bin/env python3
"""Normalize entry-overlay fleet rows without rerunning vector campaigns.

The entry overlay artifacts contain three chronological outer-validation
folds.  Their ``aggregate`` return fields are *sums* of fold capital returns,
while TIM is a row-weighted mean and exits are summed.  Earlier ingestion
incorrectly labeled those mixed-unit aggregates ``VEC_UNTOUCHED_OOS``.

This migration is append-only:

* the historical row is retained unchanged;
* an explicitly scoped ``VEC_NESTED_FOLD_AGGREGATE`` row is appended; and
* the final chronological outer-validation fold is appended as the actual
  ``VEC_UNTOUCHED_OOS`` row.

It is safe to run repeatedly.  Corrected rows carry a normalization version
and the source historical result id, which form the idempotency key.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

NORMALIZATION_VERSION = "entry_fleet_metric_scope_v1"
AGGREGATE_STAGE = "VEC_NESTED_FOLD_AGGREGATE"
FINAL_STAGE = "VEC_UNTOUCHED_OOS"


def _artifact_path(fleet_root: Path, artifact: str) -> Path:
    path = Path(artifact)
    if path.is_absolute():
        return path
    # <repo>/data/reports/path_fleet -> <repo>
    return fleet_root.resolve().parents[2] / path


def _final_fold(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if not folds:
        raise ValueError("result artifact has no outer_folds")
    return max(
        folds,
        key=lambda fold: (
            int((fold.get("validation_metrics") or {}).get("end_ts") or -1),
            int(fold.get("fold") or -1),
        ),
    )


def normalized_payloads(
    source: dict[str, Any],
    artifact_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build aggregate and final-OOS payloads for one legacy result row."""
    aggregate = artifact_result["aggregate"]
    folds = artifact_result["outer_folds"]
    final = _final_fold(folds)
    metrics = final["validation_metrics"]
    control = final["same_frozen_ladder_e02_control"]
    source_id = (
        int(source["id"]) if source.get("id") is not None else None
    )
    common = {
        "job_id": int(source["job_id"]),
        "symbol": str(source["symbol"]).upper(),
        "side": source["side"],
        "status": source["status"],
        "exact_replay": False,
        "future_htf_count": int(aggregate.get("future_htf_count") or 0),
        "artifact": source["artifact"],
        "normalization_version": NORMALIZATION_VERSION,
        "source_legacy_stage": source["stage"],
        "capital_base_usd": 2000,
    }
    if source_id is not None:
        common["source_legacy_result_id"] = source_id
    aggregate_payload = {
        **common,
        "stage": AGGREGATE_STAGE,
        "strategy_return_pct": aggregate["candidate_capital_return_pct_sum"],
        "bh_return_pct": aggregate["bh_capital_return_pct_sum"],
        "same_entry_control_return_pct": aggregate[
            "control_capital_return_pct_sum"
        ],
        "tim_pct": aggregate["weighted_tim_pct"],
        "trades": sum(
            int(fold["validation_metrics"].get("exit_count") or 0)
            for fold in folds
        ),
        "untouched_oos": False,
        "metric_scope": "NESTED_OUTER_VALIDATION_FOLD_AGGREGATE",
        "return_unit": "SUM_OF_FOLD_CAPITAL_RETURN_PCT",
        "return_aggregation": "SUM_ACROSS_OUTER_VALIDATION_FOLDS",
        "tim_unit": "PCT",
        "tim_aggregation": "ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS",
        "trades_unit": "EXIT_FILLS",
        "trades_aggregation": "SUM_ACROSS_OUTER_VALIDATION_FOLDS",
        "fold_count": len(folds),
    }
    final_payload = {
        **common,
        "stage": FINAL_STAGE,
        "strategy_return_pct": metrics["capital_return_pct"],
        "bh_return_pct": metrics["bh_capital_return_pct"],
        "same_entry_control_return_pct": control["capital_return_pct"],
        "tim_pct": metrics["exposure_weighted_tim_pct"],
        "trades": int(metrics.get("exit_count") or 0),
        "untouched_oos": True,
        "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "return_unit": "CAPITAL_RETURN_PCT",
        "return_aggregation": "NONE_SINGLE_FOLD",
        "tim_unit": "PCT",
        "tim_aggregation": "NONE_SINGLE_FOLD",
        "trades_unit": "EXIT_FILLS",
        "trades_aggregation": "NONE_SINGLE_FOLD",
        "fold_count": len(folds),
        "fold_index": final.get("fold"),
        "validation_window": final.get("validation"),
        "beats_bh": bool(final.get("beats_bh")),
        "beats_control": bool(final.get("beats_control")),
    }
    return aggregate_payload, final_payload


def _insert(con: sqlite3.Connection, payload: dict[str, Any]) -> None:
    strategy = float(payload["strategy_return_pct"])
    bh = float(payload["bh_return_pct"])
    control = float(payload["same_entry_control_return_pct"])
    payload = dict(payload)
    payload["alpha_vs_bh_pp"] = strategy - bh
    payload["alpha_vs_control_pp"] = strategy - control
    payload["promotion_candidate"] = False
    con.execute(
        """INSERT INTO results
           (job_id,symbol,side,stage,status,strategy_return_pct,bh_return_pct,
            same_entry_control_return_pct,alpha_vs_bh_pp,alpha_vs_control_pp,
            tim_pct,trades,untouched_oos,exact_replay,future_htf_count,artifact,
            payload_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            payload["job_id"],
            payload["symbol"],
            payload["side"],
            payload["stage"],
            payload["status"],
            strategy,
            bh,
            control,
            payload["alpha_vs_bh_pp"],
            payload["alpha_vs_control_pp"],
            float(payload["tim_pct"]),
            int(payload["trades"]),
            int(bool(payload["untouched_oos"])),
            0,
            int(payload["future_htf_count"]),
            str(payload["artifact"]),
            json.dumps(payload, sort_keys=True),
            time.time(),
        ),
    )


def _already_normalized(
    con: sqlite3.Connection, source_id: int, stage: str
) -> bool:
    marker = f'%"normalization_version": "{NORMALIZATION_VERSION}"%'
    source_marker = f'%"source_legacy_result_id": {int(source_id)}%'
    return (
        con.execute(
            """SELECT 1 FROM results
               WHERE stage=? AND payload_json LIKE ? AND payload_json LIKE ?
               LIMIT 1""",
            (stage, marker, source_marker),
        ).fetchone()
        is not None
    )


def migrate(
    fleet_root: Path,
    job_ids: Iterable[int],
    *,
    apply: bool,
) -> dict[str, Any]:
    """Audit or append corrected rows for the selected fleet jobs."""
    fleet_root = fleet_root.resolve()
    con = sqlite3.connect(fleet_root / "queue.db")
    con.row_factory = sqlite3.Row
    selected = tuple(sorted(set(int(job_id) for job_id in job_ids)))
    if not selected:
        raise ValueError("at least one job id is required")
    placeholders = ",".join("?" for _ in selected)
    rows = con.execute(
        f"""SELECT r.*,j.path_id
            FROM results r JOIN jobs j ON j.id=r.job_id
            WHERE r.job_id IN ({placeholders})
              AND r.stage='VEC_UNTOUCHED_OOS'
            ORDER BY r.job_id,r.id""",
        selected,
    ).fetchall()
    legacy = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        if payload.get("normalization_version") == NORMALIZATION_VERSION:
            continue
        legacy.append(row)

    report: dict[str, Any] = {
        "normalization_version": NORMALIZATION_VERSION,
        "apply": bool(apply),
        "job_ids": list(selected),
        "legacy_rows": len(legacy),
        "inserted_aggregate_rows": 0,
        "inserted_final_oos_rows": 0,
        "already_normalized_rows": 0,
        "errors": [],
        "jobs": {},
    }
    for row in legacy:
        job_report = report["jobs"].setdefault(
            str(row["job_id"]),
            {"path_id": row["path_id"], "legacy": 0, "aggregate": 0, "final": 0},
        )
        job_report["legacy"] += 1
        try:
            artifact_path = _artifact_path(fleet_root, row["artifact"])
            artifact_result = json.loads(
                (artifact_path / "result.json").read_text()
            )
            aggregate_payload, final_payload = normalized_payloads(
                dict(row), artifact_result
            )
            for payload, counter, job_counter in (
                (
                    aggregate_payload,
                    "inserted_aggregate_rows",
                    "aggregate",
                ),
                (final_payload, "inserted_final_oos_rows", "final"),
            ):
                if _already_normalized(con, row["id"], payload["stage"]):
                    report["already_normalized_rows"] += 1
                    continue
                if apply:
                    _insert(con, payload)
                report[counter] += 1
                job_report[job_counter] += 1
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            report["errors"].append(
                {
                    "result_id": row["id"],
                    "job_id": row["job_id"],
                    "artifact": row["artifact"],
                    "error": str(exc),
                }
            )
    if apply and not report["errors"]:
        con.commit()
    else:
        con.rollback()
    con.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fleet-root", type=Path, required=True)
    parser.add_argument(
        "--job-ids",
        required=True,
        help="comma-separated entry-overlay fleet job ids",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()
    report = migrate(
        args.fleet_root,
        (int(value) for value in args.job_ids.split(",") if value.strip()),
        apply=args.apply,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(encoded)
    print(encoded, end="")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
