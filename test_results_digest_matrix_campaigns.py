import json
import sqlite3
from pathlib import Path

from tools import results_digest_email as digest


def test_matrix_guard_script_prefers_explicit_override(tmp_path, monkeypatch):
    guard = tmp_path / "matrix_guard.py"
    monkeypatch.setenv("MATRIX_GUARD_PATH", str(guard))
    assert digest._matrix_guard_script() == guard


def test_matrix_guard_script_falls_back_to_checkout(monkeypatch):
    monkeypatch.delenv("MATRIX_GUARD_PATH", raising=False)
    real_exists = Path.exists

    def fake_exists(path):
        if path == Path("/home/niels/binance-sandbox/tools/matrix_guard.py"):
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    assert digest._matrix_guard_script() == digest.MATRIX_GUARD_SCRIPT


def test_provisional_completion_uses_guard_strict_amber_audit(monkeypatch):
    monkeypatch.setattr(
        digest.smd if hasattr(digest, "smd") else __import__("tools.switch_matrix_digest", fromlist=["x"]),
        "load_classified_pilot_coverage",
        lambda keys: {
            "available": True,
            "keys": {key: {"differential_filled": 0, "differential_empty": 826} for key in keys},
        },
        raising=False,
    )
    from tools import matrix_guard
    monkeypatch.setattr(
        matrix_guard,
        "strict_provisional_vector_overlay",
        lambda: {"available": True, "keys": {"MU_LONG": {"amber": 1}}},
    )
    body = digest._provisional_matrix_completion_section()
    assert "MU_LONG" in body
    assert "1/826" in body
    assert "amber provisional" in body


def _fleet_db(path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE results (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        strategy_return_pct REAL,
        bh_return_pct REAL,
        same_entry_control_return_pct REAL,
        alpha_vs_bh_pp REAL,
        alpha_vs_control_pp REAL,
        tim_pct REAL,
        trades INTEGER,
        untouched_oos INTEGER NOT NULL DEFAULT 0,
        exact_replay INTEGER NOT NULL DEFAULT 0,
        future_htf_count INTEGER,
        artifact TEXT,
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL
        )"""
    )
    return con


def _payload(campaign, symbol, side, gates, tims):
    return {
        "campaign_id": campaign,
        "entry_family": "ENTRY_TEST",
        "exit_family": "EXIT_TEST",
        "discovery_fold_gate_pass": gates,
        "discovery_fold_evidence": [
            {
                "fold": idx + 1,
                "weighted_tim_pct": tim,
                "alpha_vs_bh_pp": 10.0,
                "alpha_vs_same_entry_e02_pp": 5.0,
                "exit_fills": 2,
            }
            for idx, tim in enumerate(tims)
        ],
        "symbol": symbol,
        "side": side,
        "promotion_allowed": False,
    }


def _insert(con, row_id, stage, symbol, side, campaign, gates, tims):
    payload = _payload(campaign, symbol, side, gates, tims)
    con.execute(
        """INSERT INTO results VALUES (
        ?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )""",
        (
            row_id,
            symbol,
            side,
            stage,
            "GRAY_REJECTED",
            125.0,
            100.0,
            150.0,
            25.0,
            -25.0,
            75.0,
            4,
            1,
            0,
            0,
            "/artifact",
            json.dumps(payload),
            1000.0 + row_id,
        ),
    )


def test_latest_matrix_campaign_section_is_manifest_driven_and_truthful(
    tmp_path, monkeypatch
):
    db = tmp_path / "queue.db"
    con = _fleet_db(db)
    regime = "regime_campaign"
    immutable = "immutable_campaign"
    state_aware = "state_aware_campaign"
    _insert(
        con,
        4,
        "VEC_STATE_AWARE_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
        "PBF",
        "LONG",
        state_aware,
        [True, True],
        [71.0, 75.0],
    )
    _insert(
        con,
        1,
        "VEC_REGIME_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
        "ARM",
        "LONG",
        regime,
        [True, False],
        [78.0, 35.0],
    )
    _insert(
        con,
        2,
        "VEC_REGIME_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
        "MU",
        "LONG",
        regime,
        [True, True],
        [76.0, 74.0],
    )
    _insert(
        con,
        3,
        "VEC_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
        "TTD",
        "SHORT",
        immutable,
        [False, True],
        [20.0, 72.0],
    )
    con.commit()
    con.close()

    root = tmp_path / "vec_research"
    for campaign, results, exact in (
        (
            state_aware,
            [
                {
                    "symbol": "PBF",
                    "side": "LONG",
                    "candidate_count": 4608,
                }
            ],
            [],
        ),
        (
            regime,
            [
                {
                    "symbol": "MU",
                    "side": "LONG",
                    "candidate_count": 7280,
                },
                {
                    "symbol": "ARM",
                    "side": "LONG",
                    "candidate_count": 7280,
                },
            ],
            [],
        ),
        (
            immutable,
            [
                {
                    "symbol": "TTD",
                    "side": "SHORT",
                    "candidate_count": 1456,
                }
            ],
            [],
        ),
    ):
        directory = root / campaign
        directory.mkdir(parents=True)
        (directory / "campaign_manifest.json").write_text(
            json.dumps(
                {
                    "contract": {
                        "every_fold_tim_gate_pct": [70.0, 80.0],
                        "strategy_capacity_usd": 16000.0,
                        "bh_capital_usd": 2000.0,
                    },
                    "results": results,
                    "exact_replay_queue": exact,
                }
            )
        )

    monkeypatch.setenv("PATH_FLEET_DB_PATH", str(db))
    monkeypatch.setenv("VEC_RESEARCH_ROOT", str(root))
    fixture_bands = {
        "MU_LONG": (50.0, 80.0),
        "PBF_LONG": (50.0, 80.0),
        "ARM_LONG": (20.0, 60.0),
        "TTD_SHORT": (20.0, 60.0),
    }
    monkeypatch.setattr(digest, "tim_band_for_key", fixture_bands.__getitem__)
    section = digest._latest_matrix_filling_campaigns_section()

    assert "RESEARCH ONLY" in section
    assert "VEC_STATE_AWARE_ENTRY_EXIT_BEAM_UNTOUCHED_OOS" in section
    assert "VEC_REGIME_ENTRY_EXIT_BEAM_UNTOUCHED_OOS" in section
    assert "VEC_ENTRY_EXIT_BEAM_UNTOUCHED_OOS" in section
    assert section.index("MU_LONG") < section.index("ARM_LONG")
    assert "TTD_SHORT" in section
    assert "F1&#10003; 76.0%" in section
    assert "F1&#10007; 78.0% (TIM&gt;60)" in section
    assert "F2&#10003; 35.0%" in section
    assert "F1&#10003; 20.0%" in section
    assert "F2&#10007; 72.0% (TIM&gt;60)" in section
    assert "125.00% vs B&amp;H 100.00%" in section
    assert "vs control 150.00%" in section
    assert "final &le; control" in section
    assert "not exact-validated" in section
    assert "14,560" in section
    assert "1,456" in section
    assert "exact queue" in section


def test_latest_matrix_campaign_section_reports_missing_data(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(digest, "_usable_fleet_db", lambda: None)
    section = digest._latest_matrix_filling_campaigns_section()
    assert "MISSING" in section
