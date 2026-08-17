import hashlib
import json

from tools import persym_baseline_campaign as psc
from tools import run_exact_ttd_dc_timing_probes as runner


def test_probe_grid_has_activation_sentinels_and_hold_values():
    assert set(runner.PROBES) == {
        "control_off_off_h1440",
        "master_only_h1440",
        "orphan_dc_h1440",
        "dc_h100",
        "dc_h240",
        "dc_h1440",
        "dc_h4320",
    }


def test_every_probe_is_family_isolated_and_has_real_dc_tf():
    for name in runner.PROBES:
        values = runner.probe_overrides(name)
        assert values["MTF_DC_REJECT_EXIT_TF"] == "1h"
        assert values["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
        assert values["MTF_ATR_TRAIL_ENABLED"] is False
        assert values["MTF_BB_REJECT_EXIT_ENABLED"] is False
        assert values["MTF_GR_EXIT_GATE_ENABLED"] is False
        assert values["MTF_WT_CROSS_EXIT_ENABLED"] is False
        assert values["WT_CROSSUNDER_FINAL_ENABLED"] is False
        assert values["EXIT_STRUCT_TF"] == "None"
        assert values["SHORT_STRUCT_EXIT_TF"] == "None"


def test_activation_sentinels_change_only_requested_switches():
    control = runner.probe_overrides("control_off_off_h1440")
    master = runner.probe_overrides("master_only_h1440")
    orphan = runner.probe_overrides("orphan_dc_h1440")
    assert {
        key for key in control if control[key] != master[key]
    } == {"MTF_EXIT_USE_COMPOUND"}
    assert {
        key for key in control if control[key] != orphan[key]
    } == {"MTF_DC_REJECT_EXIT_ENABLED"}


def test_hold_grid_changes_only_recorded_hold():
    baseline = runner.probe_overrides("dc_h1440")
    for name, hold in (
        ("dc_h100", 100.0),
        ("dc_h240", 240.0),
        ("dc_h4320", 4320.0),
    ):
        values = runner.probe_overrides(name)
        changed = {
            key for key in baseline if baseline[key] != values[key]
        }
        assert changed == {"TRADIER_MIN_HOLD_MINUTES"}
        assert values["TRADIER_MIN_HOLD_MINUTES"] == hold


def test_probe_identity_hashes_full_override_and_is_unique():
    identities = {}
    for name in runner.PROBES:
        values, sha, tag = runner.probe_identity(name)
        encoded = json.dumps(
            values, sort_keys=True, separators=(",", ":")
        ).encode()
        assert sha == hashlib.sha256(encoded).hexdigest()
        assert tag.endswith(sha[:12])
        identities[name] = (sha, tag)
    assert len(set(identities.values())) == len(runner.PROBES)
    known = runner.manifest()["known_results"]["dc_h1440_same_override_sha"]
    assert identities["dc_h1440"][0] == known["overrides_sha256"]


def test_runner_does_not_change_matrix_contract_fingerprint_inputs():
    assert "tools/run_exact_ttd_dc_timing_probes.py" not in (
        psc.MATRIX_CONTRACT_FILES
    )


def test_audit_gates_require_capacity_reentry_bh_and_tim():
    audit = {
        "status": "PASS_WITH_CAPACITY_CLAMPS",
        "capacity_respected": True,
        "result": {
            "max_requested_mult": 8.0,
            "requested_fill_ratio": 0.9959,
            "real_closes": 237,
            "reentry_violations": 0,
        },
    }
    metrics = {"capture_vs_bh": 7.2898, "time_in_mkt_pct": 73.5987}
    gates = runner.audit_gates(
        audit, metrics, {"MTF_DC_REJECT": 237}
    )
    assert gates["all"] is True

    audit["result"]["reentry_violations"] = 1
    assert runner.audit_gates(
        audit, metrics, {"MTF_DC_REJECT": 237}
    )["all"] is False
