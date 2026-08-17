from __future__ import annotations

import json
from pathlib import Path

from tools.build_mu_amber_merge_package import build


def _row(**overrides):
    row = {
        "key": "MU_LONG",
        "tier": "VEC_APPROX",
        "vector_evidence_class": "VEC_APPROX",
        "status": "MOVED",
        "param": "MU_WT_PRICE_LADDER_ENTRY_TF",
        "value_json": "\"4h\"",
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "db_engine_write_allowed": False,
        "live_config_write_allowed": False,
        "promotion_allowed": False,
        "source_hash": "abc123",
        "telemetry_2000_usd": {"starting_notional_usd": 2000.0},
        "benchmark_deployed_usd": 2000.0,
        "average_deployed_usd": 4000.0,
        "capital_normalization_factor": 0.5,
        "normalized_pnl_usd": 125.0,
    }
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_capital_alias_and_owner_receipt_accept(tmp_path):
    source = tmp_path / "mu.jsonl"
    _write_jsonl(source, [_row()])
    owner = tmp_path / "owner.json"
    owner.write_text(json.dumps({"exact_owned_cells": []}))
    receipt = build([source], tmp_path / "out", owner)
    assert receipt["status"] == "READY_TO_MERGE"
    assert receipt["accepted_amber_rows"] == 1
    assert receipt["safe_to_merge"] is True


def test_missing_owner_receipt_fails_closed(tmp_path):
    source = tmp_path / "mu.jsonl"
    _write_jsonl(source, [_row()])
    receipt = build([source], tmp_path / "out", None)
    assert receipt["status"] == "BLOCKED"
    assert receipt["accepted_amber_rows"] == 0
    assert receipt["rejection_reasons"]["EXACT_OWNER_SOURCE_UNAVAILABLE"] == 1


def test_exact_owned_cell_is_suppressed(tmp_path):
    source = tmp_path / "mu.jsonl"
    _write_jsonl(source, [_row()])
    owner = tmp_path / "owner.json"
    owner.write_text(
        json.dumps(
            {
                "exact_owned_cells": [
                    {
                        "param": "MU_WT_PRICE_LADDER_ENTRY_TF",
                        "value_json": "\"4h\"",
                        "key": "MU_LONG",
                    }
                ]
            }
        )
    )
    receipt = build([source], tmp_path / "out", owner)
    assert receipt["accepted_amber_rows"] == 0
    assert receipt["rejection_reasons"]["PROTECTED_EXACT_OWNER"] == 1
