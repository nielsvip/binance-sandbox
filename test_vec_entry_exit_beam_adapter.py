import json

from tools import vec_entry_exit_beam_adapter as adapter


def test_dc_candidate_restores_tuple_and_optional_maturity():
    row = adapter._dc_candidate(
        {
            "min_gain_pct": 3.0,
            "buffer_fraction": 0.001,
            "tier_profile": [1.0, 2.0, 3.0, 5.0],
            "target_fill_ratio": 0.75,
            "maturity_atr": None,
        }
    )
    assert row.tier_profile == (1.0, 2.0, 3.0, 5.0)
    assert row.maturity_atr is None


def test_overlay_manifest_inherits_control_accounting_contract(tmp_path):
    control = tmp_path / "control"
    overlay = tmp_path / "overlay"
    control.mkdir()
    overlay.mkdir()
    control_payload = {
        "manifest": {
            "symbol": "MU",
            "side": "LONG",
            "npz": "/tmp/MU.npz",
            "npz_sha256": "abc",
            "commission_bps_one_way": 5.0,
            "slippage_bps_one_way": 2.0,
            "exit": {"n": 30},
        }
    }
    (control / "result.json").write_text(json.dumps(control_payload))
    overlay_payload = {
        "manifest": {
            "symbol": "MU",
            "side": "LONG",
            "control_artifact": str(control),
            "control_npz_sha256": "abc",
        }
    }
    (overlay / "result.json").write_text(json.dumps(overlay_payload))
    source, loaded, path = adapter._source_and_control(overlay)
    assert path == control
    assert loaded == control_payload
    assert source["manifest"]["npz_sha256"] == "abc"
    assert source["manifest"]["commission_bps_one_way"] == 5.0
    assert source["frozen_oos_aggregate"] == {}
