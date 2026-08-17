import json
import hashlib
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import current_matrix_reporting as cmr
from tools import param_results_store as prs
import chart_server


def _hash_json(value):
    return hashlib.sha256(cmr._canonical_json(value).encode()).hexdigest()


SCHEMA = """
CREATE TABLE param_cells (
    symbol TEXT, side TEXT, campaign TEXT, param TEXT, value_json TEXT,
    pool_sharpe REAL, trades INTEGER, acc_gain_pct REAL, gain_per_mo REAL,
    delta_gain_mo_vs_bh REAL, delta_vs_baseline_gain_mo REAL, ts TEXT,
    overrides_json TEXT, source_file TEXT, time_in_mkt_pct REAL, tier TEXT,
    trades_fingerprint TEXT, validation_status TEXT,
    contract_fingerprint TEXT, real_closes INTEGER,
    capital_accounting_version TEXT, benchmark_deployed_usd REAL,
    average_deployed_usd REAL, capital_normalization_factor REAL,
    raw_pnl_usd REAL, normalized_pnl_usd REAL, max_dd_pct REAL
)
"""


def _write_row(root, *, campaign, tag, gain, delta, ts, fp, trades=1):
    con = sqlite3.connect(root / "data" / "param_results_stocks.db")
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ABC",
            "LONG",
            campaign,
            "TEST_ENABLED",
            "true",
            0.1,
            trades,
            gain,
            gain,
            delta,
            delta,
            ts,
            "{}",
            f"param_matrix_daemon/{campaign}/{tag}",
            40.0,
            "ENGINE",
            "trade-fp",
            "PASS",
            fp,
            trades,
            cmr.CAPITAL_ACCOUNTING_VERSION,
            2000.0,
            2000.0,
            1.0,
            gain * 20.0,
            gain * 20.0,
            0.0,
        ),
    )
    con.commit()
    con.close()
    cell = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / tag
    )
    cell.mkdir(parents=True, exist_ok=True)
    trade = {
        "symbol": "ABC",
        "side": "LONG",
        "entry_ts": 1,
        "exit_ts": 2,
        "entry_price": 10,
        "exit_price": 11,
        "pnl_pct": 10.0,
    }
    (cell / "cell__ABC.jsonl").write_text(json.dumps(trade) + "\n")
    (cell / "audit__ABC.json").write_text(
        json.dumps({"status": "PASS", "contract_fingerprint": fp})
    )
    (cell / "override__ABC.json").write_text("{}")
    con = sqlite3.connect(root / "data" / "param_results_stocks.db")
    con.execute(
        "UPDATE param_cells SET trades_fingerprint=? "
        "WHERE campaign=? AND source_file=?",
        (
            prs.trades_fingerprint(
                [trade],
                contract_version=fp if str(fp).startswith(
                    "tradier-matrix-exec-c5"
                ) else None,
            ),
            campaign,
            f"param_matrix_daemon/{campaign}/{tag}",
        ),
    )
    con.commit()
    con.close()


def _root(tmp_path):
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "param_results_stocks.db")
    con.execute(SCHEMA)
    con.commit()
    con.close()
    return tmp_path


def test_reporting_root_prefers_populated_sandbox_over_empty_live_db(
    tmp_path, monkeypatch
):
    live = tmp_path / "live"
    sandbox = tmp_path / "sandbox"
    (live / "data").mkdir(parents=True)
    (live / "data" / "param_results_stocks.db").write_bytes(b"")
    sandbox.mkdir()
    _root(sandbox)
    con = sqlite3.connect(sandbox / "data" / "param_results_stocks.db")
    con.execute(
        "INSERT INTO param_cells "
        "(symbol,side,campaign,param,value_json) VALUES (?,?,?,?,?)",
        ("ABC", "LONG", cmr.CURRENT_CAMPAIGNS[0], "TEST", "true"),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("MATRIX_REPORTING_SANDBOX_ROOT", str(sandbox))
    assert cmr.reporting_root(live) == sandbox


def test_reporting_root_honors_populated_explicit_base_over_sandbox(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "explicit"
    sandbox = tmp_path / "sandbox"
    for root, symbol in ((explicit, "EXPLICIT"), (sandbox, "SANDBOX")):
        root.mkdir()
        _root(root)
        con = sqlite3.connect(root / "data" / "param_results_stocks.db")
        con.execute(
            "INSERT INTO param_cells "
            "(symbol,side,campaign,param,value_json) VALUES (?,?,?,?,?)",
            (symbol, "LONG", cmr.CURRENT_CAMPAIGNS[0], "TEST", "true"),
        )
        con.commit()
        con.close()
    monkeypatch.setenv("MATRIX_REPORTING_SANDBOX_ROOT", str(sandbox))
    assert cmr.reporting_root(explicit) == explicit


def test_reporting_root_honors_lifecycle_only_explicit_base_over_sandbox(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "explicit"
    result = (
        explicit / "data/reports/vec_research/abc_lifecycle_test/result.json"
    )
    result.parent.mkdir(parents=True)
    result.write_text("{}")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    _root(sandbox)
    con = sqlite3.connect(sandbox / "data" / "param_results_stocks.db")
    con.execute(
        "INSERT INTO param_cells "
        "(symbol,side,campaign,param,value_json) VALUES (?,?,?,?,?)",
        ("SANDBOX", "LONG", cmr.CURRENT_CAMPAIGNS[0], "TEST", "true"),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("MATRIX_REPORTING_SANDBOX_ROOT", str(sandbox))
    assert cmr.reporting_root(explicit) == explicit


def test_reporting_root_honors_canonical_matrix_bundle_over_sandbox(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "explicit"
    reports = explicit / "data/reports"
    reports.mkdir(parents=True)
    (reports / "SWITCH_MATRIX_TRB.csv.gz").write_bytes(b"fixture")
    digest = reports / "SWITCH_MATRIX_TRB_DIGEST.md"
    digest.write_text("fixture")
    digest.with_suffix(digest.suffix + ".provenance.json").write_text(
        json.dumps({
            "schema": "switch-matrix-trb-current-digest-v1",
            "canonical_matrix": "data/reports/SWITCH_MATRIX_TRB.csv.gz",
            "canonical_matrix_sha256": hashlib.sha256(
                (reports / "SWITCH_MATRIX_TRB.csv.gz").read_bytes()
            ).hexdigest(),
            "digest_sha256": hashlib.sha256(digest.read_bytes()).hexdigest(),
        })
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    _root(sandbox)
    con = sqlite3.connect(sandbox / "data" / "param_results_stocks.db")
    con.execute(
        "INSERT INTO param_cells "
        "(symbol,side,campaign,param,value_json) VALUES (?,?,?,?,?)",
        ("SANDBOX", "LONG", cmr.CURRENT_CAMPAIGNS[0], "TEST", "true"),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("MATRIX_REPORTING_SANDBOX_ROOT", str(sandbox))
    assert cmr.reporting_root(explicit) == explicit
    digest.write_text("tampered")
    assert cmr.reporting_root(explicit) == sandbox


def test_best_row_preserves_full_window_for_the_same_logical_cell(
    tmp_path, monkeypatch
):
    root = _root(tmp_path)
    full = "stocks_repaired_20260730_c5"
    one = "stocks_repaired_20260730_c5_1yr"
    _write_row(
        root,
        campaign=full,
        tag="full",
        gain=99,
        delta=98,
        ts="2026-07-30T01:00:00Z",
        fp="full-fp",
    )
    _write_row(
        root,
        campaign=one,
        tag="stale",
        gain=50,
        delta=49,
        ts="2026-07-30T02:00:00Z",
        fp="old-1yr-fp",
    )
    _write_row(
        root,
        campaign=one,
        tag="current",
        gain=3,
        delta=2,
        ts="2026-07-30T03:00:00Z",
        fp="current-1yr-fp",
    )
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"current-1yr-fp", "full-fp"}},
    )
    rows = cmr.best_rows(root, symbol="ABC", side="LONG")
    assert len(rows) == 1
    assert rows[0]["campaign"] == full
    assert rows[0]["gain_per_mo"] == 99
    assert rows[0]["window"] == "full"


def test_one_year_fills_a_logical_cell_missing_from_full_window(
    tmp_path, monkeypatch
):
    root = _root(tmp_path)
    full = "stocks_repaired_20260730_c5"
    one = "stocks_repaired_20260730_c5_1yr"
    _write_row(
        root,
        campaign=full,
        tag="full",
        gain=2,
        delta=1,
        ts="2026-07-30T01:00:00Z",
        fp="full-fp",
    )
    _write_row(
        root,
        campaign=one,
        tag="one",
        gain=4,
        delta=3,
        ts="2026-07-30T02:00:00Z",
        fp="one-fp",
    )
    con = sqlite3.connect(root / "data" / "param_results_stocks.db")
    con.execute(
        "UPDATE param_cells SET param='OTHER_EXIT_ENABLED' "
        "WHERE campaign=?",
        (one,),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"full-fp", "one-fp"}},
    )
    rows = cmr.best_rows(root, symbol="ABC", side="LONG")
    assert len(rows) == 1
    assert rows[0]["campaign"] == one
    assert rows[0]["param"] == "OTHER_EXIT_ENABLED"
    assert rows[0]["window"] == "1yr"


def test_best_trade_payload_returns_the_complete_selected_ledger(
    tmp_path, monkeypatch
):
    root = _root(tmp_path)
    campaign = "stocks_repaired_20260730_c5_1yr"
    _write_row(
        root,
        campaign=campaign,
        tag="current",
        gain=3,
        delta=2,
        ts="2026-07-30T03:00:00Z",
        fp="current-fp",
    )
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"current-fp"}},
    )
    payload = cmr.read_best_trades("ABC", "LONG", root)
    assert payload["n"] == 1
    assert payload["rows"][0]["ledger_count_matches_db"] is True
    assert payload["trades"][0]["_campaign"] == campaign
    assert payload["trades"][0]["_side"] == "LONG"
    assert payload["rows"][0]["trades_path_resolved"].startswith(str(root))
    assert payload["n_actions"] == 2
    assert payload["action_counts"] == {
        "ENTRY": 1, "REENTRY": 0, "AUGMENT": 0, "REDUCE": 0, "EXIT": 1,
    }
    assert {action["event_source"] for action in payload["actions"]} == {
        "exact_round_endpoint"
    }
    assert payload["marker_diagnostics"][0]["artifact_status"] == "PASS"


def test_exact_action_event_markers_preserve_augment_reduce_and_reentry():
    row = {
        "symbol": "ABC", "side": "LONG", "campaign": "current",
        "param": "EXIT", "value_json": "true",
        "receipt_identity_sha256": "r" * 64,
        "trades_fingerprint": "trade-fp",
    }
    trade = {
        "symbol": "ABC", "side": "LONG",
        "action_events": [
            {"timestamp": 10, "action": "OPEN", "price": 100,
             "reason": "MANDATORY_REENTRY_PRICE_CROSS", "executed_qty": 2},
            {"timestamp": 20, "action": "AUGMENT", "price": 99,
             "reason": "DC5M_BOUNCE", "executed_qty": 1},
            {"timestamp": 30, "action": "REDUCE", "price": 103,
             "reason": "WT_TOP", "executed_qty": 1},
            {"timestamp": 40, "action": "FULL_CLOSE", "price": 104,
             "reason": "STRUCTURAL_BREAK", "executed_qty": 2},
        ],
    }
    actions = cmr._exact_ledger_actions(trade, row=row, trade_index=7)
    assert [action["action"] for action in actions] == [
        "REENTRY", "AUGMENT", "REDUCE", "EXIT"
    ]
    assert {action["event_source"] for action in actions} == {
        "ledger_action_event"
    }
    assert all(action["receipt_identity_sha256"] == "r" * 64 for action in actions)
    assert all(action["trade_index"] == 7 for action in actions)


def test_malformed_declared_action_events_do_not_invent_round_endpoints():
    actions = cmr._exact_ledger_actions(
        {
            "symbol": "ABC", "side": "LONG",
            "entry_ts": 10, "entry_price": 100,
            "exit_ts": 20, "exit_price": 101,
            "action_events": [{"action": "OPEN", "timestamp": 10}],
        },
        row={"symbol": "ABC", "side": "LONG"},
        trade_index=0,
    )
    assert actions == []


def test_s1_absolute_receipt_path_maps_only_to_relative_campaign_bundle(tmp_path):
    campaign = "stocks_repaired_20260730_c5_1yr"
    row = {
        "campaign": campaign,
        "symbol": "VT",
        "side": "LONG",
        "source_file": f"param_matrix_daemon/{campaign}/WT_DC_EXIT_ENABLED__true",
        "trades_path": "/home/niels/binance-sandbox/should-not-be-used.jsonl",
    }
    paths = cmr.resolve_local_artifact_paths(tmp_path, row)
    assert paths is not None
    assert paths["ledger"] == (
        tmp_path / "data" / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / "WT_DC_EXIT_ENABLED__true" / "cell__VT.jsonl"
    )
    assert "/home/niels/" not in str(paths["ledger"])


def test_override_match_allows_only_matrix_side_context_flags():
    artifact = {"EXIT_ENABLED": True, "LOOKBACK": 20}
    long_receipt = {**artifact, "LONG_ENABLED": True, "SHORT_ENABLED": False}
    assert cmr._override_recipe_matches_side_context(artifact, long_receipt, "LONG")
    assert not cmr._override_recipe_matches_side_context(artifact, long_receipt, "SHORT")
    assert not cmr._override_recipe_matches_side_context(
        artifact, {**long_receipt, "LOOKBACK": 21}, "LONG"
    )


def test_missing_or_mismatched_audit_fails_closed(tmp_path, monkeypatch):
    root = _root(tmp_path)
    campaign = "stocks_repaired_20260730_c5"
    _write_row(
        root,
        campaign=campaign,
        tag="bad",
        gain=3,
        delta=2,
        ts="2026-07-30T03:00:00Z",
        fp="row-fp",
    )
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"row-fp"}},
    )
    audit = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / "bad"
        / "audit__ABC.json"
    )
    audit.write_text(
        json.dumps({"status": "PASS", "contract_fingerprint": "other-fp"})
    )
    assert cmr.best_rows(root) == []
    rows = cmr.current_rows(root, require_ledger=False)
    assert rows[0]["artifact_status"] == "AUDIT_CONTRACT_MISMATCH"


def test_receipt_matching_stale_runtime_fingerprint_fails_closed(
    tmp_path, monkeypatch
):
    root = _root(tmp_path)
    campaign = "stocks_repaired_20260730_c5"
    _write_row(
        root,
        campaign=campaign,
        tag="stale",
        gain=9,
        delta=8,
        ts="2026-07-30T03:00:00Z",
        fp="old-code-and-npz-fp",
    )
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"today-code-and-npz-fp"}},
    )

    assert cmr.current_rows(root) == []
    assert cmr.best_rows(root) == []


def test_ledger_count_mismatch_fails_closed(tmp_path, monkeypatch):
    root = _root(tmp_path)
    campaign = "stocks_repaired_20260730_c5"
    _write_row(
        root,
        campaign=campaign,
        tag="count-mismatch",
        gain=4,
        delta=3,
        ts="2026-07-30T03:00:00Z",
        fp="current-fp",
        trades=1,
    )
    ledger = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / "count-mismatch"
        / "cell__ABC.jsonl"
    )
    ledger.write_text(ledger.read_text() * 2)
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"current-fp"}},
    )

    assert cmr.best_rows(root) == []
    rows = cmr.current_rows(root, require_ledger=False)
    assert rows[0]["artifact_status"] == "LEDGER_COUNT_MISMATCH_DB_1_LEDGER_2"


def test_mismatched_deployed_capital_identity_is_never_ranked(tmp_path, monkeypatch):
    root = _root(tmp_path)
    campaign = "stocks_repaired_20260730_c5"
    _write_row(
        root,
        campaign=campaign,
        tag="bad-capital",
        gain=4,
        delta=3,
        ts="2026-07-30T03:00:00Z",
        fp="current-fp",
        trades=11,
    )
    con = sqlite3.connect(root / "data" / "param_results_stocks.db")
    con.execute(
        "UPDATE param_cells SET capital_normalization_factor=0.5 "
        "WHERE campaign=?",
        (campaign,),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(
        cmr,
        "_current_contract_fingerprints",
        lambda rows: {("ABC", "LONG"): {"current-fp"}},
    )

    assert cmr.best_rows(root) == []


def test_lifecycle_vector_receipt_is_hash_verified_and_kept_separate(tmp_path):
    artifact = tmp_path / "data/reports/vec_research/abc_lifecycle_test"
    artifact.mkdir(parents=True)
    npz = artifact / "ABC.npz"
    npz.write_bytes(b"causal-npz")
    raw = artifact / "raw_results.jsonl"
    raw.write_text('{"row":1}\n')
    manifest = {
        "schema": "TRB_LIFECYCLE_COMBO_BEAM_V1",
        "symbol": "ABC",
        "side": "SHORT",
        "source_npz": {"path": str(npz), "sha256": hashlib.sha256(npz.read_bytes()).hexdigest()},
        "window": {"start": "2026-01-01", "end_exclusive": "2026-02-01"},
        "commission_bps_one_way": 0,
        "slippage_bps_one_way": 5,
    }
    manifest["manifest_sha256"] = _hash_json(manifest)
    (artifact / "campaign_manifest.json").write_text(json.dumps(manifest))
    best = {
        "symbol": "ABC", "side": "SHORT", "tier": "VECTOR_LIFECYCLE",
        "manifest_sha256": manifest["manifest_sha256"],
        "gain_pct_per_month": 10.0, "bh_gain_pct_per_month": 2.0,
        "delta_gain_mo_vs_bh": 8.0, "no_lookahead_future_htf_count": 0,
        "recipe": {"exit": 1}, "paths": {"exit": {"label": "WT"}},
    }
    best["result_sha256"] = _hash_json(best)
    result = {
        "status": "COMPLETE", "symbol": "ABC", "side": "SHORT",
        "manifest_sha256": manifest["manifest_sha256"], "best": best,
        "raw_results_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "rows_evaluated": 12,
    }
    result["receipt_sha256"] = _hash_json(result)
    (artifact / "result.json").write_text(json.dumps(result))

    rows = cmr.lifecycle_vector_rows(tmp_path)
    assert [row["key"] for row in rows] == ["ABC_SHORT"]
    assert rows[0]["evidence_tier"] == "VECTOR_LIFECYCLE_HASH_VERIFIED"
    assert cmr.best_rows(tmp_path) == []

    raw.write_text('{"tampered":true}\n')
    assert cmr.lifecycle_vector_rows(tmp_path) == []


def test_trb_review_owns_the_trade_ui_and_endpoint_uses_current_selector(
    monkeypatch,
):
    expected = {
        "symbol": "ABC",
        "side": "LONG",
        "rows": [{"key": "ABC_LONG"}],
        "trades": [{"side": "LONG", "pnl_pct": 1.0}],
        "n": 1,
    }
    monkeypatch.setattr(
        chart_server.current_matrix_reporting,
        "read_best_trades",
        lambda symbol, side, base: expected,
    )
    client = chart_server.app.test_client()
    page = client.get("/trb_review")
    assert page.status_code == 200
    assert "best current BT" in page.get_data(as_text=True)
    response = client.get(
        "/trb_best_backtest_trades?sym=abc&side=LONG"
    )
    assert response.status_code == 200
    assert response.get_json() == expected


def test_html_section_renders_all_accounting_columns(monkeypatch):
    row = {
        "key": "ABC_LONG",
        "window": "full",
        "param": "EXIT_ENABLED",
        "value_json": "true",
        "gain_per_mo": 3.0,
        "delta_gain_mo_vs_bh": 1.0,
        "pool_sharpe": 0.5,
        "max_dd_pct": 12.0,
        "average_deployed_usd": 2000.0,
        "time_in_mkt_pct": 50.0,
        "trades": 9,
        "campaign": "stocks_repaired_20260725_c2",
        "capital_accounting_version": "avg-trade-deployed-2000-v1",
        "ts": "2026-07-31T00:00:00Z",
    }
    monkeypatch.setattr(
        cmr,
        "summary",
        lambda base=None: {
            "best_rows": [row],
            "qualifiers": [row],
            "qualifier_rule": "gain/month >2%",
            "campaigns": {
                row["campaign"]: {
                    "rows": 1,
                    "keys": 1,
                    "latest_ts": row["ts"],
                }
            },
        },
    )

    rendered = cmr.html_section()
    assert "avg deployed/trade" in rendered
    assert "$2000.00" in rendered
    assert "avg-trade-deployed-2000-v1" in rendered
    assert "2026-07-31T00:00:00Z" in rendered


def test_recipe_hash_mismatch_is_explicit_and_withheld_from_html(monkeypatch):
    row = cmr._annotate_recipe({
        "overrides_json": json.dumps({"EXIT_ENABLED": True}),
        "overrides_sha256": "0" * 64,
    })
    assert row["recipe_status"] == "RECIPE_HASH_MISMATCH"
    row.update({
        "key": "ABC_LONG", "window": "full", "param": "EXIT_ENABLED",
        "value_json": "true", "gain_per_mo": 3.0, "delta_gain_mo_vs_bh": 1.0,
        "bh_comparison_valid": True, "campaign": "stocks_repaired_20260730_c5",
        "artifact_status": "PASS", "ts": "2026-08-01T00:00:00Z",
    })
    monkeypatch.setattr(cmr, "summary", lambda base=None: {
        "best_rows": [row], "qualifiers": [row], "qualifier_rule": "test",
        "campaigns": {row["campaign"]: {"rows": 1, "keys": 1, "latest_ts": row["ts"]}},
    })
    rendered = cmr.html_section()
    assert "Recipe withheld: RECIPE_HASH_MISMATCH" in rendered
    assert "EXIT_ENABLED&quot;:true" not in rendered


def test_dashboard_payload_is_canonical_and_labels_remote_receipts(monkeypatch):
    row = {
        "key": "ABC_LONG", "symbol": "ABC", "side": "LONG", "param": "X",
        "value_json": "true", "campaign": "stocks_repaired_20260730_c5",
        "artifact_status": "REMOTE_RECEIPT_UNVERIFIED", "recipe_overrides": {"X": True},
    }
    monkeypatch.setattr(cmr, "summary", lambda base=None: {
        "best_rows": [row], "reporting_root": "/matrix", "qualifiers": [],
        "campaigns": {}, "qualifier_rule": "test",
    })
    payload = cmr.dashboard_payload()
    assert payload["schema"] == "current-matrix-best-v2"
    assert payload["rows"][0]["evidence_tier"] == "ENGINE_RECEIPT_REMOTE_LEDGER_UNAVAILABLE"
    assert "vector" in payload["selection_rule"].lower()


def test_evidence_hash_is_cached_only_for_unchanged_file_identity(tmp_path):
    evidence = tmp_path / "shared_source.npz"
    evidence.write_bytes(b"first immutable payload")
    cmr._sha256_file_at_identity.cache_clear()

    first = cmr._sha256_file(evidence)
    assert cmr._sha256_file(evidence) == first
    assert cmr._sha256_file_at_identity.cache_info().hits == 1

    evidence.write_bytes(b"replacement payload with a different size")
    second = cmr._sha256_file(evidence)
    assert second != first
    assert cmr._sha256_file_at_identity.cache_info().misses == 2


def test_large_lifecycle_snapshot_returns_warming_without_blocking(
    tmp_path, monkeypatch
):
    research = tmp_path / "data/reports/vec_research"
    for index in range(9):
        artifact = research / f"abc{index}_lifecycle_test"
        artifact.mkdir(parents=True)
        (artifact / "result.json").write_text("{}")
        (artifact / "campaign_manifest.json").write_text("{}")
        (artifact / "raw_results.jsonl").write_text("")

    started = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.append((target, args, name, daemon))

        def start(self):
            return None

    cmr._LIFECYCLE_SNAPSHOTS.clear()
    monkeypatch.setattr(cmr.threading, "Thread", FakeThread)
    rows, status = cmr.lifecycle_dashboard_snapshot(tmp_path)
    assert rows == []
    assert status == "WARMING"
    assert len(started) == 1
    assert started[0][2] == "current-matrix-lifecycle-verifier"
    assert started[0][3] is True


def test_artifact_validation_cache_invalidates_on_evidence_change(
    tmp_path, monkeypatch
):
    paths = {}
    for name in ("ledger", "audit", "override"):
        path = tmp_path / name
        path.write_text(name)
        paths[name] = path
    calls = []
    monkeypatch.setattr(cmr, "_artifact_paths", lambda root, row: paths)
    monkeypatch.setattr(
        cmr,
        "_artifact_valid_uncached",
        lambda root, row: (calls.append(dict(row)) or (True, "PASS")),
    )
    cmr._artifact_valid_at_identity.cache_clear()
    cmr._ARTIFACT_VALIDATION_RESULTS.clear()
    row = {"symbol": "ABC", "side": "LONG", "trades": 1}
    assert cmr._artifact_valid(tmp_path, row) == (True, "PASS")
    assert cmr._artifact_valid(tmp_path, row) == (True, "PASS")
    assert len(calls) == 1
    paths["ledger"].write_text("ledger changed")
    assert cmr._artifact_valid(tmp_path, row) == (True, "PASS")
    assert len(calls) == 2
