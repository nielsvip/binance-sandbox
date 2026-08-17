import json

from tools import param_results_store as store


def _row(
    contract: str,
    *,
    tier: str = "ENGINE",
    gain: float = 1.0,
    overrides: str = "{}",
) -> dict:
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
        "overrides_json": overrides,
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


def test_opt_in_same_contract_receipt_refresh_replaces_stale_mtf_row(
    tmp_path,
):
    con = store.connect(tmp_path / "results.db")
    stale = _row(
        "same-contract",
        gain=-1.0,
        overrides='{"MTF_DC_REJECT_EXIT_TF":"None"}',
    )
    store.insert_cell(con, stale)
    repaired = _row(
        "same-contract",
        gain=3.0,
        overrides=(
            '{"MTF_DC_REJECT_EXIT_TF":"1h",'
            '"MTF_DC_REJECT_EXIT_LOOKBACK":5,'
            '"MTF_EXIT_USE_COMPOUND":true}'
        ),
    )
    repaired["replace_same_contract_receipt"] = True
    store.insert_cell(con, repaired)

    assert con.execute(
        "SELECT gain_per_mo,overrides_json FROM param_cells"
    ).fetchone() == (3.0, repaired["overrides_json"])
    assert con.execute(
        "SELECT archive_reason FROM param_cell_history"
    ).fetchone()[0] == "SAME_CONTRACT_RECEIPT_REFRESH"


def test_same_contract_receipt_refresh_is_explicit_and_semantic(tmp_path):
    con = store.connect(tmp_path / "results.db")
    original = _row(
        "same-contract",
        gain=1.0,
        overrides='{"A":1,"B":true}',
    )
    store.insert_cell(con, original)

    no_opt_in = _row(
        "same-contract",
        gain=2.0,
        overrides='{"A":2,"B":true}',
    )
    store.insert_cell(con, no_opt_in)
    assert con.execute(
        "SELECT gain_per_mo FROM param_cells"
    ).fetchone()[0] == 1.0

    formatting_only = _row(
        "same-contract",
        gain=3.0,
        overrides='{"B": true, "A": 1}',
    )
    formatting_only["replace_same_contract_receipt"] = True
    store.insert_cell(con, formatting_only)
    assert con.execute(
        "SELECT gain_per_mo FROM param_cells"
    ).fetchone()[0] == 1.0
    assert con.execute(
        "SELECT COUNT(*) FROM param_cell_history"
    ).fetchone()[0] == 0


def test_valid_rerun_can_replace_same_contract_invalid_row(tmp_path):
    con = store.connect(tmp_path / "results.db")
    invalid = _row("same-contract", gain=-1.0)
    invalid["validation_status"] = "FAIL_REENTRY"
    store.insert_cell(con, invalid)

    repaired = _row("same-contract", gain=4.0)
    repaired["replace_same_contract_invalid"] = True
    store.insert_cell(con, repaired)

    assert con.execute(
        "SELECT gain_per_mo,validation_status FROM param_cells"
    ).fetchone() == (4.0, "PASS")
    assert con.execute(
        "SELECT archive_reason FROM param_cell_history"
    ).fetchone()[0] == "SAME_CONTRACT_VALIDATION_REFRESH"


def test_invalid_rerun_cannot_displace_valid_same_contract_row(tmp_path):
    con = store.connect(tmp_path / "results.db")
    store.insert_cell(con, _row("same-contract", gain=4.0))

    invalid = _row("same-contract", gain=-9.0)
    invalid["validation_status"] = "FAIL_REENTRY"
    invalid["replace_same_contract_invalid"] = True
    store.insert_cell(con, invalid)

    assert con.execute(
        "SELECT gain_per_mo,validation_status FROM param_cells"
    ).fetchone() == (4.0, "PASS")
    assert con.execute(
        "SELECT COUNT(*) FROM param_cell_history"
    ).fetchone()[0] == 0


def test_same_contract_capital_accounting_upgrade_archives_old_metrics(tmp_path):
    con = store.connect(tmp_path / "results.db")
    old = _row("same-contract", gain=9.0)
    store.insert_cell(con, old)

    normalized = _row("same-contract", gain=2.0)
    normalized.update(
        {
            "capital_accounting_version": "avg-trade-deployed-2000-v1",
            "benchmark_deployed_usd": 2000.0,
            "average_deployed_usd": 9000.0,
            "capital_normalization_factor": 2 / 9,
            "raw_pnl_usd": 180.0,
            "normalized_pnl_usd": 40.0,
        }
    )
    store.insert_cell(con, normalized)

    assert con.execute(
        "SELECT gain_per_mo,capital_accounting_version,average_deployed_usd "
        "FROM param_cells"
    ).fetchone() == (2.0, "avg-trade-deployed-2000-v1", 9000.0)
    reason, archived = con.execute(
        "SELECT archive_reason,row_json FROM param_cell_history"
    ).fetchone()
    assert reason == "CAPITAL_ACCOUNTING_REFRESH"
    assert json.loads(archived)["gain_per_mo"] == 9.0
