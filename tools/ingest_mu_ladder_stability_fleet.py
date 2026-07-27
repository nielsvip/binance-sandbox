#!/usr/bin/env python3
"""Idempotently ingest rejected MU ladder/exit discovery rows to path-fleet."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


LADDER_STAGE = "VEC_MU_LADDER_STABILITY_DISCOVERY"
EXIT_STAGE = "VEC_MU_LADDER_TOP_EXIT_DISCOVERY"


def _weighted_tim(folds: list[dict[str, Any]]) -> float:
    rows = sum(int(row["metrics"]["rows"]) for row in folds)
    return sum(
        float(row["metrics"]["exposure_weighted_tim_pct"])
        * int(row["metrics"]["rows"])
        for row in folds
    ) / max(1, rows)


def _insert(
    con: sqlite3.Connection,
    *,
    job_id: int,
    stage: str,
    payload: dict[str, Any],
) -> None:
    strategy = float(payload["strategy_return_pct"])
    bh = float(payload["bh_return_pct"])
    control = float(payload["same_entry_control_return_pct"])
    payload.update(
        {
            "job_id": job_id,
            "symbol": "MU",
            "side": "LONG",
            "stage": stage,
            "status": "GRAY_RESEARCH_REJECTED",
            "alpha_vs_bh_pp": strategy - bh,
            "alpha_vs_control_pp": strategy - control,
            "untouched_oos": False,
            "exact_replay": False,
            "promotion_candidate": False,
        }
    )
    con.execute(
        """INSERT INTO results
           (job_id,symbol,side,stage,status,strategy_return_pct,bh_return_pct,
            same_entry_control_return_pct,alpha_vs_bh_pp,alpha_vs_control_pp,
            tim_pct,trades,untouched_oos,exact_replay,future_htf_count,artifact,
            payload_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id,
            "MU",
            "LONG",
            stage,
            payload["status"],
            strategy,
            bh,
            control,
            payload["alpha_vs_bh_pp"],
            payload["alpha_vs_control_pp"],
            float(payload["tim_pct"]),
            int(payload["trades"]),
            0,
            0,
            int(payload["future_htf_count"]),
            str(payload["artifact"]),
            json.dumps(payload, sort_keys=True),
            time.time(),
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder-result", type=Path, required=True)
    ap.add_argument("--exit-result", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    ladder_path = args.ladder_result.resolve()
    exit_path = args.exit_result.resolve()
    root = args.root.resolve()
    ladder_data = json.loads(ladder_path.read_text())
    exit_data = json.loads(exit_path.read_text())
    campaign_id = ladder_path.parent.name
    exit_campaign_id = exit_path.parent.name

    con = sqlite3.connect(root / "queue.db")
    job_ids = dict(
        con.execute(
            """SELECT path_id,id FROM jobs
               WHERE path_id IN ('ENTRY_LADDER_GREEN',
                                 'EXIT_E06_REGRESSION_RETEST')"""
        ).fetchall()
    )
    if set(job_ids) != {
        "ENTRY_LADDER_GREEN",
        "EXIT_E06_REGRESSION_RETEST",
    }:
        raise RuntimeError("required fleet jobs are absent")
    existing_ladder = {
        json.loads(raw).get("candidate_number")
        for raw, in con.execute(
            "SELECT payload_json FROM results WHERE stage=?",
            (LADDER_STAGE,),
        )
        if json.loads(raw).get("campaign_id") == campaign_id
    }
    existing_exit = {
        (
            json.loads(raw).get("ladder_candidate_number"),
            json.loads(raw).get("exit_label"),
        )
        for raw, in con.execute(
            "SELECT payload_json FROM results WHERE stage=?",
            (EXIT_STAGE,),
        )
        if json.loads(raw).get("campaign_id") == exit_campaign_id
    }
    appended_ladder = appended_exit = 0
    for row in ladder_data["discovery"]:
        number = int(row["candidate_number"])
        if number in existing_ladder:
            continue
        folds = row["folds"]
        _insert(
            con,
            job_id=job_ids["ENTRY_LADDER_GREEN"],
            stage=LADDER_STAGE,
            payload={
                "candidate_number": number,
                "campaign_id": campaign_id,
                "candidate": row["candidate"],
                "strategy_return_pct": sum(
                    float(x["metrics"]["capital_return_pct"])
                    for x in folds
                ),
                "bh_return_pct": sum(
                    float(x["metrics"]["bh_capital_return_pct"])
                    for x in folds
                ),
                "same_entry_control_return_pct": sum(
                    float(
                        x["metrics"]["capital_return_pct"]
                        - x["metrics"]["alpha_vs_source_control_pp"]
                    )
                    for x in folds
                ),
                "tim_pct": _weighted_tim(folds),
                "trades": sum(
                    int(x["metrics"]["exit_count"]) for x in folds
                ),
                "future_htf_count": int(row["future_htf_count"]),
                "artifact": str(ladder_path.parent),
                "strict_discovery_survivor": row[
                    "strict_discovery_survivor"
                ],
                "folds": folds,
                "gray_reason": (
                    "no every-discovery-fold ladder candidate passed B&H, "
                    "source-control, TIM, solvency, capacity and reclaim gates"
                ),
            },
        )
        appended_ladder += 1

    for row in exit_data["results"]:
        if row["exit"]["family"] != "E06_REGRESSION_REENTRY":
            continue
        key = (
            int(row["ladder_candidate_number"]),
            str(row["exit"]["label"]),
        )
        if key in existing_exit:
            continue
        folds = row["folds"]
        same_entry = sum(
            float(
                x["metrics"]["capital_return_pct"]
                - x["metrics"]["alpha_vs_same_entry_e02_pp"]
            )
            for x in folds
        )
        _insert(
            con,
            job_id=job_ids["EXIT_E06_REGRESSION_RETEST"],
            stage=EXIT_STAGE,
            payload={
                "campaign_id": exit_campaign_id,
                "ladder_candidate_number": key[0],
                "ladder": row["ladder"],
                "exit_label": key[1],
                "exit": row["exit"],
                "strategy_return_pct": sum(
                    float(x["metrics"]["capital_return_pct"])
                    for x in folds
                ),
                "bh_return_pct": sum(
                    float(x["metrics"]["bh_capital_return_pct"])
                    for x in folds
                ),
                "same_entry_control_return_pct": same_entry,
                "tim_pct": _weighted_tim(folds),
                "trades": sum(
                    int(x["metrics"]["exit_count"]) for x in folds
                ),
                "future_htf_count": int(row["future_htf_count"]),
                "artifact": str(exit_path.parent),
                "strict_discovery_survivor": row[
                    "strict_discovery_survivor"
                ],
                "folds": folds,
                "gray_reason": (
                    "no every-discovery-fold same-entry top-exit candidate "
                    "passed B&H, E02, TIM, solvency, capacity and reclaim gates"
                ),
            },
        )
        appended_exit += 1
    con.commit()
    con.close()
    fleet.write_report(root)
    summary = {
        "ladder_campaign_id": campaign_id,
        "exit_campaign_id": exit_campaign_id,
        "appended_ladder_rows": appended_ladder,
        "appended_e06_rows": appended_exit,
        "unregistered_exit_rows_left_artifact_only": sum(
            row["exit"]["family"] != "E06_REGRESSION_REENTRY"
            for row in exit_data["results"]
        ),
    }
    out = root / "mu_ladder_stability_ingest" / campaign_id
    fleet.atomic_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
