import hashlib
import json

from tools import run_exact_exit_factorial_finalists as runner


def test_ttd_finalist_is_family_isolated_and_preserves_bottom_bounce():
    values = runner.finalist_overrides("TTD_SHORT")
    assert values["EXIT_STRUCT_TF"] == "None"
    assert values["SHORT_STRUCT_EXIT_TF"] == "None"
    assert values["MTF_EXIT_USE_COMPOUND"] is True
    assert values["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert values["MTF_BB_REJECT_EXIT_ENABLED"] is False
    assert values["WT_CROSSUNDER_FINAL_ENABLED"] is False
    assert values["STRUCTURAL_RANGE_SHIFT_EXIT"] is False
    assert values["DELTA_ENGINE_ENABLED"] is True
    assert values["DELTA_EXIT_ENABLED"] is True
    assert values["RZ_EXIT_ENABLED"] is True
    assert values["TRADIER_MIN_HOLD_MINUTES"] == 1440.0


def test_acn_finalist_is_dc_plus_wt_and_preserves_bottom_bounce():
    values = runner.finalist_overrides("ACN_SHORT")
    assert values["EXIT_STRUCT_TF"] == "None"
    assert values["SHORT_STRUCT_EXIT_TF"] == "None"
    assert values["MTF_EXIT_USE_COMPOUND"] is True
    assert values["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert values["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert values["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert values["MTF_BB_REJECT_EXIT_ENABLED"] is False
    assert values["MTF_ATR_TRAIL_ENABLED"] is False
    assert values["MTF_GR_EXIT_GATE_ENABLED"] is False
    assert values["MTF_WT_CROSS_EXIT_ENABLED"] is False
    assert values["WT_CROSSUNDER_FINAL_ENABLED"] is True
    assert values["STRUCTURAL_RANGE_SHIFT_EXIT"] is False
    assert values["DELTA_ENGINE_ENABLED"] is True
    assert values["DELTA_EXIT_ENABLED"] is True
    assert values["RZ_EXIT_ENABLED"] is True
    assert values["TRADIER_MIN_HOLD_MINUTES"] == 240.0


def test_acn_dc_only_variant_changes_only_wt_final():
    selected = runner.finalist_overrides("ACN_SHORT")
    dc_only = runner.finalist_overrides("ACN_SHORT", "dc_only")
    changed = {
        key
        for key in selected
        if selected.get(key) != dc_only.get(key)
    }
    assert changed == {"WT_CROSSUNDER_FINAL_ENABLED"}
    assert dc_only["WT_CROSSUNDER_FINAL_ENABLED"] is False


def test_acn_wt_only_variant_disables_dc_and_its_compound_master():
    selected = runner.finalist_overrides("ACN_SHORT")
    wt_only = runner.finalist_overrides("ACN_SHORT", "wt_only")
    changed = {
        key
        for key in selected
        if selected.get(key) != wt_only.get(key)
    }
    assert changed == {
        "MTF_DC_REJECT_EXIT_ENABLED",
        "MTF_EXIT_USE_COMPOUND",
    }
    assert wt_only["MTF_EXIT_USE_COMPOUND"] is False
    assert wt_only["MTF_DC_REJECT_EXIT_ENABLED"] is False
    assert wt_only["WT_CROSSUNDER_FINAL_ENABLED"] is True
    assert wt_only["TRADIER_MIN_HOLD_MINUTES"] == 240.0
    assert wt_only["DELTA_ENGINE_ENABLED"] is True
    assert wt_only["DELTA_EXIT_ENABLED"] is True
    assert wt_only["RZ_EXIT_ENABLED"] is True


def test_non_acn_variants_are_rejected():
    for variant in ("dc_only", "wt_only"):
        try:
            runner.finalist_overrides("TTD_SHORT", variant)
        except ValueError as exc:
            assert "only defined for ACN_SHORT" in str(exc)
        else:
            raise AssertionError(f"{variant} unexpectedly accepted for TTD")


def test_finalist_identity_is_deterministic_and_hashes_full_override():
    values, override_sha, tag = runner.finalist_identity(
        "TTD_SHORT", "selected"
    )
    assert (values, override_sha, tag) == runner.finalist_identity(
        "TTD_SHORT", "selected"
    )
    encoded = json.dumps(
        values, sort_keys=True, separators=(",", ":")
    ).encode()
    assert override_sha == hashlib.sha256(encoded).hexdigest()
    assert tag.endswith(override_sha[:12])
