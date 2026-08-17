from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _write_state(root: Path, valid: int) -> None:
    reports = root / "data/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "SWITCH_MATRIX_UNIQUE_NONZERO_AUDIT_CURRENT.json").write_text(json.dumps({
        "zero_cells": 0,
        "duplicate_cells": 0,
        "non_numeric_or_nonfinite_cells": 0,
        "formula_cells": 0,
        "uniqueness_scope": "WITHIN_SYMBOL_SIDE_ACROSS_FOUR_PATH_SHEETS",
        "per_symbol_side": [{"key": "MU_LONG", "valid": valid, "expected": 3618}],
    }))
    (reports / "MATRIX_SYMBOL_ADVANCEMENT_GATE_CURRENT.json").write_text(json.dumps({
        "current_symbol_side": "MU_LONG",
    }))


def _run(root: Path, now: float) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/matrix_vector_throughput_guard.py"), "--root", str(root), "--now", str(now)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode, json.loads((root / "data/reports/MATRIX_VECTOR_THROUGHPUT_CURRENT.json").read_text())


def test_counts_only_net_new_accepted_cells(tmp_path: Path) -> None:
    _write_state(tmp_path, 100)
    code, first = _run(tmp_path, 1000.0)
    assert code == 0 and first["status"] == "BOOTSTRAP_RATE_WINDOW"
    _write_state(tmp_path, 2100)
    code, second = _run(tmp_path, 4600.0)
    assert code == 0
    assert second["status"] == "PASS_RATE"
    assert second["net_new_accepted_cells"] == 2000
    assert second["accepted_cells_per_hour"] == 2000.0


def test_fails_when_accepted_rate_is_below_contract(tmp_path: Path) -> None:
    _write_state(tmp_path, 100)
    _run(tmp_path, 1000.0)
    _write_state(tmp_path, 150)
    code, second = _run(tmp_path, 4600.0)
    assert code == 2
    assert second["status"] == "FAIL_THROUGHPUT"
