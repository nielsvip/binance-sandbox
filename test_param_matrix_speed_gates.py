import json

from tools import exact_wiring_gate
from tools import param_matrix_daemon as daemon


def test_trb_does_not_exact_test_other_account_namespaces():
    assert daemon.wrong_account_namespace("TRA_MIN_HOLD_MINUTES", "trb")
    assert daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trb")
    assert not daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trb")
    assert not daemon.wrong_account_namespace("WT_3M_FORCE_OPEN_ENABLED", "trb")


def test_trc_does_not_exact_test_trb_namespace():
    assert daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trc")
    assert not daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trc")


def test_bool_cells_keep_canonical_json_type_across_two_passes(monkeypatch, tmp_path):
    name = "WT_3M_FORCE_OPEN_ENABLED"
    monkeypatch.setattr(
        daemon.psc,
        "load_params",
        lambda _path, _limit: [(name, [False, True])],
    )
    monkeypatch.setattr(daemon, "useless_knobs", lambda: set())
    monkeypatch.setattr(daemon.wiring_gate.TradierConfig, name, False)

    cells = [
        cell
        for cell in daemon.all_cells(tmp_path / "missing.json", all_tiers=True)
        if cell[0] == name
    ]
    assert [value_json for _, value_json, _ in cells] == ["true", "false"]
    assert [json.loads(value_json) for _, value_json, _ in cells] == [True, False]
    assert [next(iter(override.values())) for _, _, override in cells] == [True, False]

    # The default false pass is represented by the accepted baseline and is
    # intentionally not rerun as a duplicate exact cell.  The one non-default
    # boolean exact pass must compare directly with that baseline.
    rows = [
        {
            "value": True,
            "value_json": "true",
            "fingerprint": "fp-true",
            "inert": False,
            "validation_status": "PASS",
        }
    ]
    verdict = exact_wiring_gate.classify_param(
        name,
        rows,
        "fp-baseline",
        tmp_path / "missing.npz",
        symbol="MU",
        side="LONG",
    )
    assert verdict["verdict"] == "WIRED_DIFFERENT"
    assert not verdict["skip_remaining_exact"]

    rows[0]["fingerprint"] = "fp-baseline"
    reconnect = exact_wiring_gate.classify_param(
        name,
        rows,
        "fp-baseline",
        tmp_path / "missing.npz",
        symbol="MU",
        side="LONG",
    )
    assert reconnect["verdict"] == "RED_RECONNECT"
    assert reconnect["range_binding"]["status"] == "BINDING_BY_TYPE"
    assert reconnect["skip_remaining_exact"]
