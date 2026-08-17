import json
import hashlib
import sqlite3

from tools import param_results_store as prs
from tools import revalue_exact_capital_accounting as revalue


def _trade(notional, pnl, entry_ts):
    return {
        "symbol": "ABC",
        "side": "LONG",
        "entry_ts": entry_ts,
        "exit_ts": entry_ts + 60,
        "entry_price": 100.0,
        "executed_open_qty": notional / 100.0,
        "pnl_pct": pnl / notional * 100.0,
        "pnl_usd": pnl,
        "round_trip_cost_pct": 0.05,
        "action_events": [
            {
                "action": "OPEN",
                "executed_qty": notional / 100.0,
                "price": 100.0,
                "cash_flow": -1.0,
            },
            {
                "action": "CLOSE",
                "executed_qty": notional / 100.0,
                "price": 100.0 + pnl / (notional / 100.0),
            },
        ],
    }


def _artifacts(
    root, tag, trades, status, fingerprint, campaign=revalue.FULL_CAMPAIGN
):
    cell = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / tag
    )
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "cell__ABC.jsonl").write_text(
        "".join(json.dumps(trade) + "\n" for trade in trades)
    )
    (cell / "audit__ABC.json").write_text(
        json.dumps(
            {
                "status": status,
                "contract_fingerprint": fingerprint,
                "result": {"time_in_mkt_long_pct": 50.0},
            }
        )
    )
    (cell / "override__ABC.json").write_text("{}")


def test_apply_revalues_ledgers_and_archives_old_measurements(
    tmp_path, monkeypatch
):
    root = tmp_path
    (root / "data").mkdir()
    db = root / "data" / "param_results_stocks.db"
    con = prs.connect(db)
    fingerprint = "tradier-matrix-exec-c5-20260730:test-fingerprint"
    prs.upsert_baseline(
        con,
        {
            "mode": "tradier",
            "account": "trb",
            "symbol": "ABC",
            "side": "LONG",
            "campaign": revalue.FULL_CAMPAIGN,
            "ts": "old",
            "window_start": "2024-01-01",
            "years": 2.5,
            "pool_sharpe": 0.0,
            "trades": 1,
            "acc_gain_pct": 2.0,
            "gain_per_mo": 0.1,
            "bh_pct": 10.0,
            "bh_per_mo": 0.3,
            "delta_gain_mo_vs_bh": -0.2,
            "overrides_json": "{}",
            "stamp": "old",
            "source_file": (
                f"param_matrix_daemon/{revalue.FULL_CAMPAIGN}/"
                "__BASELINE_SAFE__LONG"
            ),
            "tier": "ENGINE",
            "trades_fingerprint": "base-trades",
            "validation_status": "PASS_CONTROL_NO_CLOSE",
            "contract_fingerprint": fingerprint,
            "real_closes": 0,
        },
    )
    con.commit()
    prs.insert_cell(
        con,
        {
            "mode": "tradier",
            "symbol": "ABC",
            "side": "LONG",
            "campaign": revalue.FULL_CAMPAIGN,
            "param": "TEST_ENABLED",
            "value_json": "true",
            "value_num": 1.0,
            "pool_sharpe": 0.0,
            "trades": 2,
            "acc_gain_pct": 12.0,
            "gain_per_mo": 0.4,
            "delta_gain_mo_vs_bh": 0.1,
            "delta_vs_baseline_gain_mo": 0.3,
            "ts": "old",
            "overrides_json": "{}",
            "stamp": "old",
            "source_file": (
                f"param_matrix_daemon/{revalue.FULL_CAMPAIGN}/TEST_ENABLED__true"
            ),
            "time_in_mkt_pct": 50.0,
            "tier": "ENGINE",
            "baseline_stamp": "base-trades",
            "trades_fingerprint": "cell-trades",
            "inert": 0,
            "validation_status": "PASS",
            "contract_fingerprint": fingerprint,
            "real_closes": 2,
        },
    )
    con.close()

    _artifacts(
        root,
        "__BASELINE_SAFE__LONG",
        [_trade(2000.0, 200.0, 1)],
        "PASS_CONTROL_NO_CLOSE",
        fingerprint,
    )
    check_fp = sqlite3.connect(db)
    check_fp.execute(
        "UPDATE key_baseline SET trades_fingerprint=?",
        (
            prs.trades_fingerprint(
                [_trade(2000.0, 200.0, 1)],
                contract_version=fingerprint,
            ),
        ),
    )
    check_fp.execute(
        "UPDATE param_cells SET trades_fingerprint=?",
        (
            prs.trades_fingerprint(
                [_trade(2000.0, 200.0, 1), _trade(10000.0, 1000.0, 2)],
                contract_version=fingerprint,
            ),
        ),
    )
    check_fp.commit()
    check_fp.close()
    _artifacts(
        root,
        "TEST_ENABLED__true",
        [_trade(2000.0, 200.0, 1), _trade(10000.0, 1000.0, 2)],
        "PASS",
        fingerprint,
    )
    monkeypatch.setattr(
        revalue.psc,
        "matrix_contract_fingerprints",
        lambda _symbol, _side: {fingerprint},
    )
    monkeypatch.setattr(
        revalue.psc,
        "sym_years_and_bh",
        lambda _symbol, _start: (2.5, 50.0),
    )

    report = revalue.run(
        root=root,
        db_path=db,
        apply=True,
        min_years=2.0,
    )
    assert len(report["baselines"]) == 1
    assert len(report["cells"]) == 1
    assert report["rejected"] == []
    assert report["backup"]

    check = sqlite3.connect(db)
    assert check.execute(
        "SELECT average_deployed_usd,acc_gain_pct,bh_pct,"
        "capital_accounting_version FROM key_baseline"
    ).fetchone() == (
        2000.0,
        10.0,
        50.0,
        revalue.psc.CAPITAL_ACCOUNTING_VERSION,
    )
    assert check.execute(
        "SELECT average_deployed_usd,acc_gain_pct,normalized_pnl_usd,"
        "capital_accounting_version FROM param_cells"
    ).fetchone() == (
        6000.0,
        20.0,
        400.0,
        revalue.psc.CAPITAL_ACCOUNTING_VERSION,
    )
    assert check.execute(
        "SELECT archive_reason FROM key_baseline_history"
    ).fetchone()[0] == "CAPITAL_ACCOUNTING_REFRESH"
    assert check.execute(
        "SELECT archive_reason FROM param_cell_history"
    ).fetchone()[0] == "CAPITAL_ACCOUNTING_REFRESH"
    check.close()


def test_c2_incomplete_mtm_baseline_is_valid_for_ledger_revaluation(
    tmp_path, monkeypatch
):
    root = tmp_path
    (root / "data").mkdir()
    db = root / "data" / "param_results_stocks.db"
    con = prs.connect(db)
    campaign = "stocks_repaired_20260725_c2"
    fingerprint = "tradier-matrix-exec-c4-20260729:accepted-c2"
    prs.upsert_baseline(
        con,
        {
            "mode": "tradier",
            "account": "trb",
            "symbol": "ABC",
            "side": "LONG",
            "campaign": campaign,
            "ts": "old",
            "window_start": "2024-01-01",
            "years": 2.2,
            "pool_sharpe": 0.0,
            "trades": 1,
            "acc_gain_pct": 1.0,
            "gain_per_mo": 0.1,
            "bh_pct": 4.0,
            "bh_per_mo": 0.2,
            "delta_gain_mo_vs_bh": -0.1,
            "overrides_json": "{}",
            "stamp": "old",
            "source_file": f"param_matrix_daemon/{campaign}/__BASELINE_SAFE__LONG",
            "tier": "ENGINE",
            "trades_fingerprint": "base",
            "validation_status": "INCOMPLETE_NO_REAL_CLOSE",
            "contract_fingerprint": fingerprint,
            "real_closes": 0,
            "mtm_count": 1,
        },
    )
    con.commit()
    prs.insert_cell(
        con,
        {
            "mode": "tradier",
            "symbol": "ABC",
            "side": "LONG",
            "campaign": campaign,
            "param": "TEST_ENABLED",
            "value_json": "true",
            "value_num": 1.0,
            "pool_sharpe": 0.0,
            "trades": 1,
            "acc_gain_pct": 1.0,
            "gain_per_mo": 0.1,
            "delta_gain_mo_vs_bh": -0.1,
            "delta_vs_baseline_gain_mo": 0.0,
            "ts": "old",
            "overrides_json": "{}",
            "stamp": "old",
            "source_file": f"param_matrix_daemon/{campaign}/TEST_ENABLED__true",
            "tier": "ENGINE",
            "baseline_stamp": "base",
            "trades_fingerprint": "cell",
            "inert": 1,
            "validation_status": "PASS",
            "contract_fingerprint": fingerprint,
            "real_closes": 1,
        },
    )
    con.close()
    trade = _trade(4000.0, 400.0, 1)
    trade["round_trip_cost_pct"] = 0.06
    _artifacts(
        root,
        "__BASELINE_SAFE__LONG",
        [trade],
        "INCOMPLETE_NO_REAL_CLOSE",
        fingerprint,
        campaign,
    )
    check_fp = sqlite3.connect(db)
    check_fp.execute(
        "UPDATE key_baseline SET trades_fingerprint=?",
        (prs.trades_fingerprint([trade]),),
    )
    check_fp.execute(
        "UPDATE param_cells SET trades_fingerprint=?",
        (prs.trades_fingerprint([trade]),),
    )
    check_fp.commit()
    check_fp.close()
    baseline_audit = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / "__BASELINE_SAFE__LONG"
        / "audit__ABC.json"
    )
    audit = json.loads(baseline_audit.read_text())
    audit["result"] = {"mtm_count": 1, "time_in_mkt_long_pct": 100.0}
    baseline_audit.write_text(json.dumps(audit))
    _artifacts(
        root,
        "TEST_ENABLED__true",
        [trade],
        "PASS",
        fingerprint,
        campaign,
    )
    monkeypatch.setattr(
        revalue.psc,
        "matrix_contract_fingerprints",
        lambda _symbol, _side: {fingerprint},
    )
    npz_dir = root / "matrix_npz"
    npz_dir.mkdir()
    (npz_dir / "ABC.npz").write_bytes(b"frozen")
    monkeypatch.setattr(revalue.psc, "MATRIX_NPZ_DIR", npz_dir)
    monkeypatch.setattr(
        revalue.psc,
        "_C2_ACCEPTED_NPZ_SHA256",
        {"ABC": hashlib.sha256(b"frozen").hexdigest()},
    )
    monkeypatch.setattr(
        revalue,
        "C4_ACCEPTED_FINGERPRINTS",
        {("ABC", "LONG"): fingerprint},
    )

    report = revalue.run(
        root=root,
        db_path=db,
        apply=False,
        min_years=2.0,
    )
    assert len(report["baselines"]) == 1
    assert len(report["cells"]) == 1
    assert report["rejected"] == []
    assert report["baselines"][0]["after"]["average_deployed_usd"] == 4000.0
