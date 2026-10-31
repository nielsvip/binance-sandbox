from tools import v15_nextgen_runtime_candidates as guard
from tools import v15_v18_planner as planner
from tools import v15_v20_selector as selector


def test_current_runtime_field_and_template_are_both_required(tmp_path):
    for filename in ("config.py", "config_tradier.py"):
        (tmp_path / filename).write_text('NEW_ENABLED = False\nFILTER_TF = "OFF"\n')
    (tmp_path / "v12_quick_engine.py").write_text('class QuickConfig:\n    NEW_ENABLED = False\n    FILTER_TF = "OFF"\n')
    template = {"tabs": {"ENTRY": {"rows": [[3, "NEW_ENABLED", "True", False], [4, "REMOVED_ENABLED", "True", False]], "yellows": ["FILTER_TF=1h"]}}}
    proposals = [{"switch": switch, "cand": "True", "filter": "FILTER_TF", "opt": option} for switch, option in (("NEW_ENABLED", "1h"), ("REMOVED_ENABLED", "1h"), ("NEW_ENABLED", "4h"))]
    result = guard.filter_current_cells(proposals, "CRYPTO_LONG", template, tmp_path)
    assert result["accepted"] == [proposals[0]]
    assert "unknown_or_invalid_current_field" in result["rejected"][0]["reason"]
    assert result["rejected"][1]["reason"] == "outside_exact_current_template"


def test_old_unwired_zero_cannot_override_untried_floor_or_count_policy_rescue():
    switch, header = "NEW_ENABLED", "FILTER_TF=1h"
    registry = {switch: {"new_or_changed": True, "wiring_sha256": "a" * 64}}
    evidence = {header: {"n": 100000, "positive": 0, "reason": "UNWIRED_CALCULATED", "wiring_sha256_by_field": {}}}
    category, symbol, tab, key = "CRYPTO_LONG", "BTCUSDC_LONG", "ENTRY", switch + "=True"
    rid = next(str(i) for i in range(10000) if 0.005 <= planner.floor_draw(category, symbol, tab, key + "|" + header, str(i)) < 0.05)
    info = {"kind": "skip", "reason": "ZERO_FORMULA_ROW historical counts", "hdrs": [], "sampled_filters": [header]}
    rescued = guard.protect_policy_skips(info, switch, [header], registry, evidence, category, symbol, tab, key, rid)
    assert rescued["info"]["kind"] == "eval" and rescued["info"]["hdrs"] == [header]
    assert info["kind"] == "skip"
    options = dict(cat_side=category, symside=symbol, rid=rid, model_p={header: 0.005}, floor_p=0.005, untried_p=0.05, dead_end_row=True, predicted_noop_cells=[header], exhausted_cells={header: 0.005})
    before = selector.decide_cells([header], None, tab, switch, "True", {}, **options)
    after = guard.decide_cells_review([header], None, tab, switch, "True", {}, wiring_registry=registry, evidence_by_cell=evidence, **options)
    assert before["keep"] == [] and after["keep"] == [header]
    assert after["settled"][header]["p"] == 0.05
    assert options["model_p"] == {header: 0.005}


def test_actual_current_era_measurements_release_guard_and_structural_skips_remain():
    registry = {"NEW_ENABLED": {"new_or_changed": True, "wiring_sha256": "a" * 64}}
    evidence = {"FILTER_TF=1h": {"reason": "MEASURED", "wiring_sha256_by_field": {"NEW_ENABLED": "a" * 64}}}
    assert guard.protected_headers("NEW_ENABLED", ["FILTER_TF=1h"], registry, evidence) == set()
    info = {"kind": "skip", "reason": "TYPE_MISMATCH", "hdrs": [], "sampled_filters": ["FILTER_TF=1h"]}
    result = guard.protect_policy_skips(info, "NEW_ENABLED", ["FILTER_TF=1h"], registry, {}, "CRYPTO_LONG", "BTCUSDC_LONG", "ENTRY", "NEW_ENABLED=True", "round")
    assert result["info"] == info
