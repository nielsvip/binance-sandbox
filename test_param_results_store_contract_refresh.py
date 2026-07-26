import json

from tools import param_results_store as store


def _row(contract: str, *, tier: str = "ENGINE", gain: float = 1.0) -> dict:
    return {
        "mode": "tradier",
        "symbol": "MU",
        "side": "LONG",
        "campaign": "repaired",
        "param": "EXAMPLE",
        "value_json": "true",
        "value_num": 1.0,
        "pool_sharpe": 0.0,
        "trades": 1,
        "acc_gain_pct": gain,
        "gain_per_mo": gain,
        "delta_gain_mo_vs_bh": gain,
        "delta_vs_baseline_gain_mo": gain,
        "ts": f"{contract}-ts",
        "overrides_json": "{}",
        "stamp": contract,
        "source_file": contract,
        "time_in_mkt_pct": 100.0,
        "tier": tier,
        "baseline_stamp": contract,
        "trades_fingerprint": contract,
        "inert": 0,
        "validation_status": "PASS",
        "contract_fingerprint": contract,
        "real_closes": 1,
        "mtm_count": 0,
        "opens_long": 1,
        "opens_short": 0,
        "requested_fill_ratio": 1.0,
        "size_clamp_count": 0,
        "reentry_pending": 0,
        "reentry_violations": 0,
        "result_audit_json": "{}",
    }


def test_new_engine_contract_replaces_cell_and_archives_old_row(tmp_path):
    con = store.connect(tmp_path / "results.db")
    store.insert_cell(con, _row("old-contract", gain=-1.0))
    store.insert_cell(con, _row("new-contract", gain=3.0))

    current = con.execute(
        "SELECT contract_fingerprint,gain_per_mo FROM param_cells"
    ).fetchone()
    history = con.execute(
        "SELECT archive_reason,row_json,replacement_contract_fingerprint "
        "FROM param_cell_history"
    ).fetchone()

    assert current == ("new-contract", 3.0)
    assert history[0] == "CONTRACT_REFRESH"
    assert json.loads(history[1])["contract_fingerprint"] == "old-contract"
    assert history[2] == "new-contract"


def test_vec_result_cannot_overwrite_engine_cell(tmp_path):
    con = store.connect(tmp_path / "results.db")
    store.insert_cell(con, _row("engine-contract", gain=2.0))
    store.insert_cell(
        con,
        _row("vec-contract", tier="VEC", gain=99.0),
    )

    assert con.execute(
        "SELECT tier,contract_fingerprint,gain_per_mo FROM param_cells"
    ).fetchone() == ("ENGINE", "engine-contract", 2.0)
    assert con.execute("SELECT COUNT(*) FROM param_cell_history").fetchone()[0] == 0
