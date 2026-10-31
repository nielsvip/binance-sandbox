import hashlib
import json
import pytest
from tools import v15_nextgen_runtime_candidates as guard
from tools import v15_v18_planner as planner
from tools import v15_v20_selector as selector


def test_current_runtime_field_and_template_are_both_required(tmp_path):
    for filename, name in (("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")):
        (tmp_path / filename).write_text(f'class {name}:\n    NEW_ENABLED: bool = False\n    FILTER_TF: str = "OFF"\n')
    template = {"tabs": {"ENTRY": {"rows": [[3, "NEW_ENABLED", "True", False], [4, "REMOVED_ENABLED", "True", False]], "yellows": ["FILTER_TF=1h"]}}}
    proposals = [{"switch": switch, "cand": "True", "filter": "FILTER_TF", "opt": option} for switch, option in (("NEW_ENABLED", "1h"), ("REMOVED_ENABLED", "1h"), ("NEW_ENABLED", "4h"))]
    result = guard.filter_current_cells(proposals, "CRYPTO_LONG", template, tmp_path, cell_eligible=lambda tab, row, switch, candidate, header: (tab, row, header) == ("ENTRY", 3, "FILTER_TF=1h"))
    assert result["accepted"] == [dict(proposals[0], tab="ENTRY", row=3)]
    assert "unknown_or_invalid_current_field" in result["rejected"][0]["reason"]
    assert result["rejected"][1]["reason"] == "outside_exact_current_template_or_wrong_tab"


def test_old_unwired_zero_cannot_override_untried_floor_or_count_policy_rescue(tmp_path):
    switch, header = "NEW_ENABLED", "FILTER_TF=1h"
    registry = verified_registry(tmp_path)
    evidence = {header: {"n": 100000, "positive": 0, "reason": "UNWIRED_CALCULATED", "wiring_sha256_by_field": {}}}
    category, symbol, tab, key = "CRYPTO_LONG", "BTCUSDC_LONG", "ENTRY", switch + "=True"
    rid = next(str(i) for i in range(100000) if 0.005 <= planner.floor_draw(category, symbol, tab, key + "|" + header, str(i)) < 0.05 and 0.005 <= selector.floor_draw(category, symbol, tab, selector.row_key(tab, switch, "True") + "|" + header, str(i)) < 0.05)
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


def test_actual_current_era_measurements_release_guard_and_structural_skips_remain(tmp_path):
    registry = verified_registry(tmp_path)
    evidence = {"FILTER_TF=1h": {"reason": "MEASURED", "wiring_sha256_by_field": {"NEW_ENABLED": registry["NEW_ENABLED"]["wiring_sha256"]}}}
    assert guard.protected_headers("NEW_ENABLED", ["FILTER_TF=1h"], registry, evidence) == set()
    info = {"kind": "skip", "reason": "TYPE_MISMATCH", "hdrs": [], "sampled_filters": ["FILTER_TF=1h"]}
    result = guard.protect_policy_skips(info, "NEW_ENABLED", ["FILTER_TF=1h"], registry, {}, "CRYPTO_LONG", "BTCUSDC_LONG", "ENTRY", "NEW_ENABLED=True", "round")
    assert result["info"] == info


def verified_registry(tmp_path):
    previous = tmp_path / "previous.py"
    current = tmp_path / "current.py"
    previous.write_text("def decide(cfg): return False\n")
    current.write_text("def decide(cfg): return cfg.NEW_ENABLED\n")
    assets = lambda path: [{"path": str(path), "asset": "engine.py", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    old, new = assets(previous), assets(current)
    fields = {"NEW_ENABLED": {"new_or_changed": True, "wiring_sha256": guard.source_fingerprint(new), "previous_sources": old, "current_sources": new}}
    path = tmp_path / "wiring.json"
    path.write_text(json.dumps({"schema": "wiring_epochs/1", "fields": fields}))
    return guard.load_wiring_registry(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_wrong_tab_and_row_non_yellow_are_rejected(tmp_path):
    for filename, name in (("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")):
        (tmp_path / filename).write_text(f'class {name}:\n    NEW_ENABLED: bool = False\n    FILTER_TF: str = "OFF"\n')
    template = {"tabs": {"ENTRY": {"rows": [[3, "NEW_ENABLED", "True", False]], "yellows": ["FILTER_TF=1h"]}, "EXIT": {"rows": [[4, "NEW_ENABLED", "True", False]], "yellows": ["FILTER_TF=1h"]}}}
    prototype = {"switch": "NEW_ENABLED", "cand": "True", "filter": "FILTER_TF", "opt": "1h"}
    rows = [dict(prototype, tab="WRONG"), dict(prototype, tab="EXIT"), prototype, dict(prototype, tab="ENTRY", row=999)]
    callback = lambda tab, row, switch, candidate, header: tab == "ENTRY" and row == 3
    result = guard.filter_current_cells(rows, "CRYPTO_LONG", template, tmp_path, callback)
    assert not result["accepted"]
    assert [row["reason"] for row in result["rejected"]] == ["outside_exact_current_template_or_wrong_tab", "not_an_eligible_yellow_cell_of_actual_row", "ambiguous_tab_requires_explicit_identity", "outside_exact_current_template_or_wrong_tab"]


def test_unverified_registry_is_not_claimed_as_protected_and_tampering_refused(tmp_path):
    with pytest.raises(ValueError, match="content-verified"):
        guard.protected_headers("NEW_ENABLED", ["FILTER_TF=1h"], {"NEW_ENABLED": {"new_or_changed": True}}, {})
    verified_registry(tmp_path)
    path = tmp_path / "wiring.json"
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "current.py").write_text("changed after registry snapshot")
    with pytest.raises(ValueError, match="source content hash"):
        guard.load_wiring_registry(path, expected)
