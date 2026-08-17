import json
import sqlite3
from pathlib import Path

import numpy as np

from tools import path_fleet_campaign as fleet


def _npz(path: Path, symbol: str, first: float, last: float) -> None:
    ts = np.array([1_700_000_000, 1_710_000_000], dtype=np.int64)
    np.savez(path / f"{symbol}.npz", timestamps=ts, close_5m=np.array([first, last]))


def test_rank_init_claim_and_fail_closed_result(tmp_path):
    npz = tmp_path / "npz"
    root = tmp_path / "fleet"
    npz.mkdir()
    _npz(npz, "WIN", 10.0, 15.0)
    _npz(npz, "LOSE", 20.0, 12.0)
    _npz(npz, "BTCUSDC", 1.0, 5.0)
    universe = fleet.rank_universe(
        npz,
        5000,
        1,
        1,
        max_data_age_days=5000,
        min_history_days=0,
        tradeable_long={"WIN", "LOSE"},
        tradeable_short={"WIN", "LOSE"},
    )
    assert universe["top_long"][0]["symbol"] == "WIN"
    assert universe["bottom_short"][0]["symbol"] == "LOSE"
    fleet.init_campaign(root, universe, replace=True)
    assert (root / "PATH_FLEET_REGISTRY.csv").read_text().startswith("path_id,kind")
    job = fleet.claim(root, "worker-1", 3600)
    assert job and job["status"] == "RUNNING"
    assert job["contract"]["matrix_eligible"] is False
    payload = {
        "job_id": job["id"],
        "symbol": "WIN",
        "side": "LONG",
        "stage": "VEC_DISCOVERY",
        "status": "PASS",
        "strategy_return_pct": 30.0,
        "bh_return_pct": 20.0,
        "same_entry_control_return_pct": 25.0,
        "tim_pct": 70.0,
        "trades": 10,
        "untouched_oos": False,
        "exact_replay": False,
        "future_htf_count": 0,
        "artifact": "diagnostic",
    }
    p = tmp_path / "result.json"
    p.write_text(json.dumps(payload))
    fleet.add_result(root, p)
    con = sqlite3.connect(root / "queue.db")
    status = con.execute("SELECT status FROM results").fetchone()[0]
    con.close()
    assert status == "RESEARCH_ONLY"
    assert "ENTRY_WT_DC" in (root / "PROGRESS.md").read_text()


def test_registry_requires_same_entry_control():
    assert fleet.PATHS
    assert all(p.fixed_entry_control and p.fixed_exit_control for p in fleet.PATHS)
    assert all(p.kind in {"ENTRY", "EXIT", "FILTER"} for p in fleet.PATHS)
    assert (
        sum(
            source_row.count(" | event=")
            for path in fleet.PATHS
            for source_row in path.source_rows
        )
        == 64
    )
    assert any(p.config_keys == ("SENT_STRAT_DIVERGENCE_ENABLED",) for p in fleet.PATHS)


def test_reversal_dc_variants_are_registered_research_only():
    by_id = {path.path_id: path for path in fleet.PATHS}
    expected = {
        "ENTRY_REVERSAL_DC_BREAK_BOUNCE_ROLLOVER",
        "EXIT_REVERSAL_HHHL",
        "EXIT_REVERSAL_WT_CROSS",
        "ENTRY_OVERBOUGHT_FLIP_SHORT",
    }
    assert expected <= set(by_id)
    reversal = by_id["ENTRY_REVERSAL_DC_BREAK_BOUNCE_ROLLOVER"]
    assert reversal.adapter_status == "ADAPTER_REQUIRED_RESEARCH_ONLY"
    assert reversal.settings["sides"] == ["SHORT", "LONG"]
    assert reversal.settings["timeframes"] == ["5m", "15m", "1h", "4h", "D"]
    assert by_id["EXIT_REVERSAL_HHHL"].settings["confirmation_tf"] == ["5m"]
    assert by_id["EXIT_REVERSAL_WT_CROSS"].settings["confirmation_tf"] == [
        "5m", "15m", "1h", "4h", "D"
    ]
