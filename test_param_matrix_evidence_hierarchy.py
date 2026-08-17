import datetime as dt
import json
import sqlite3

import pytest

from tools import param_matrix_daemon as daemon


def _evidence_db():
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE param_cells (
          mode TEXT, symbol TEXT, side TEXT, campaign TEXT, param TEXT,
          value_json TEXT, tier TEXT, validation_status TEXT,
          contract_fingerprint TEXT, real_closes INTEGER, ts TEXT,
          source_file TEXT, trades_fingerprint TEXT, trades INTEGER,
          overrides_json TEXT
        );
        CREATE TABLE key_baseline (
          mode TEXT, symbol TEXT, side TEXT, campaign TEXT, years REAL,
          validation_status TEXT, contract_fingerprint TEXT
        );
        """
    )
    return con


def _insert_evidence(
    con,
    root,
    *,
    campaign="stocks_repaired_20260725_c2",
    years=2.2,
    tier="ENGINE",
    status="PASS",
    fingerprint="exact-old-fingerprint",
    closes=20,
    ts="2026-07-15T00:00:00Z",
):
    tag = "TEST_ENABLED__true"
    source_file = f"param_matrix_daemon/{campaign}/{tag}"
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "tradier",
            "MU",
            "LONG",
            campaign,
            "TEST_ENABLED",
            "true",
            tier,
            status,
            fingerprint,
            closes,
            ts,
            source_file,
            "trade-fingerprint",
            closes,
            json.dumps({"TEST_ENABLED": True}),
        ),
    )
    con.execute(
        "INSERT INTO key_baseline VALUES (?,?,?,?,?,?,?)",
        (
            "tradier",
            "MU",
            "LONG",
            campaign,
            years,
            status,
            fingerprint,
        ),
    )
    cell_dir = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / tag
    )
    cell_dir.mkdir(parents=True)
    ledger = [
        {
            "symbol": "MU",
            "side": "LONG",
            "entry_ts": index,
            "exit_ts": index + 1,
            "pnl_pct": 1.0,
        }
        for index in range(closes)
    ]
    (cell_dir / "cell__MU.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in ledger)
    )
    (cell_dir / "audit__MU.json").write_text(
        json.dumps(
            {
                "status": status,
                "contract_fingerprint": fingerprint,
                "trade_fingerprint": "trade-fingerprint",
            }
        )
    )
    (cell_dir / "override__MU.json").write_text(
        json.dumps({"TEST_ENABLED": True})
    )


def test_one_year_lane_skips_recent_valid_full_exact_evidence(
    tmp_path, monkeypatch
):
    con = _evidence_db()
    _insert_evidence(con, tmp_path)
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", daemon.TEMP_ONE_YEAR_C5_CAMPAIGN
    )

    assert daemon.has_recent_full_exact_evidence(
        con,
        "MU",
        "LONG",
        "TEST_ENABLED",
        "true",
        now=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"years": 1.99},
        {"tier": "VEC"},
        {"status": "FAIL_REENTRY"},
        {"fingerprint": None},
        {"closes": 0},
        {"ts": "2026-06-01T00:00:00Z"},
        {"campaign": "stocks_repaired_20260725_c2_1yr"},
    ],
)
def test_one_year_lane_does_not_skip_incomplete_or_invalid_evidence(
    tmp_path, monkeypatch, changes
):
    con = _evidence_db()
    _insert_evidence(con, tmp_path, **changes)
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", daemon.TEMP_ONE_YEAR_C5_CAMPAIGN
    )

    assert not daemon.has_recent_full_exact_evidence(
        con,
        "MU",
        "LONG",
        "TEST_ENABLED",
        "true",
        now=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
    )


def test_full_window_lane_never_uses_short_lane_dedupe(
    tmp_path, monkeypatch
):
    con = _evidence_db()
    _insert_evidence(con, tmp_path)
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(daemon.psc, "CAMPAIGN", daemon.FULL_C5_CAMPAIGN)

    assert not daemon.has_recent_full_exact_evidence(
        con,
        "MU",
        "LONG",
        "TEST_ENABLED",
        "true",
        now=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
    )


def test_missing_or_mismatched_receipt_does_not_suppress_one_year_work(
    tmp_path, monkeypatch
):
    con = _evidence_db()
    _insert_evidence(con, tmp_path)
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", daemon.TEMP_ONE_YEAR_C5_CAMPAIGN
    )
    audit = next(tmp_path.rglob("audit__MU.json"))
    payload = json.loads(audit.read_text())
    payload["contract_fingerprint"] = "mismatch"
    audit.write_text(json.dumps(payload))

    assert not daemon.has_recent_full_exact_evidence(
        con,
        "MU",
        "LONG",
        "TEST_ENABLED",
        "true",
        now=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
    )


def test_historical_override_provenance_accepts_artifact_superset():
    provenance = daemon._historical_override_provenance(
        "TEST_ENABLED",
        "true",
        json.dumps({"TEST_ENABLED": True, "LIMIT": 2}),
        {
            "TEST_ENABLED": True,
            "LIMIT": 2,
            "LR_BAND_HARVEST_FRAC": 0.5,
        },
    )

    assert provenance == {
        "valid": True,
        "reason": "LEGACY_ARTIFACT_SUPERSET",
        "artifact_only_keys": ("LR_BAND_HARVEST_FRAC",),
    }


@pytest.mark.parametrize(
    ("db_overrides", "artifact_overrides", "reason"),
    [
        (
            {"TEST_ENABLED": True, "LIMIT": 2},
            {"TEST_ENABLED": True},
            "ARTIFACT_MISSING_DB_KEY:LIMIT",
        ),
        (
            {"TEST_ENABLED": True, "LIMIT": 2},
            {"TEST_ENABLED": True, "LIMIT": 3},
            "DB_ARTIFACT_VALUE_MISMATCH:LIMIT",
        ),
        (
            {"TEST_ENABLED": 1},
            {"TEST_ENABLED": 1},
            "TESTED_PARAM_VALUE_MISMATCH",
        ),
    ],
)
def test_historical_override_provenance_rejects_any_db_mismatch(
    db_overrides, artifact_overrides, reason
):
    provenance = daemon._historical_override_provenance(
        "TEST_ENABLED",
        "true",
        json.dumps(db_overrides),
        artifact_overrides,
    )

    assert provenance["valid"] is False
    assert provenance["reason"] == reason


def test_receipt_rejects_db_override_mismatch_even_when_artifact_param_matches(
    tmp_path, monkeypatch
):
    con = _evidence_db()
    _insert_evidence(con, tmp_path)
    con.execute(
        "UPDATE param_cells SET overrides_json=?",
        (json.dumps({"TEST_ENABLED": True, "LIMIT": 2}),),
    )
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", daemon.TEMP_ONE_YEAR_C5_CAMPAIGN
    )

    assert not daemon.has_recent_full_exact_evidence(
        con,
        "MU",
        "LONG",
        "TEST_ENABLED",
        "true",
        now=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
    )
