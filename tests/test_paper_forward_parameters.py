import hashlib

import openpyxl

from tools.paper_forward_parameters import resolve_catalog


def test_resolves_only_values_present_in_matching_active_template(tmp_path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    path = template_dir / "TEMPLATE_CRYPTO_LONG.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "ENTRY_TEST"
    sheet.append(["Switch", "Default"])
    sheet.append([None, None])
    sheet.append(["NUMERIC_SWITCH", 2.5])
    sheet.append(["NUMERIC_SWITCH", 5])
    sheet.append(["OTHER_SWITCH", 99])
    sheet.append(["TEXT_BOOL_SWITCH", "False"])
    sheet.append(["GREY_SWITCH", 7])
    sheet["A7"].font = openpyxl.styles.Font(color="FFBFBFBF")
    workbook.save(path)
    workbook.close()
    catalog = {"scenarios": [
        {"scenario_id": "numeric", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "NUMERIC_SWITCH", "default_kind": "non_boolean"},
        {"scenario_id": "missing", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "NO_ROW", "default_kind": "unresolved"},
        {"scenario_id": "bool", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "BOOL", "default_kind": "boolean"},
        {"scenario_id": "text_bool", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "TEXT_BOOL_SWITCH", "default_kind": "unresolved"},
        {"scenario_id": "grey", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "GREY_SWITCH", "default_kind": "non_boolean"},
    ]}

    result = resolve_catalog(catalog, template_dir)
    numeric, missing, boolean, text_bool, grey = result["scenarios"]
    assert numeric["candidate_values"] == [2.5, 5]
    assert numeric["candidate_value_types"] == ["float", "int"]
    assert numeric["candidate_source"] == "TEMPLATE_FINAL_NORM"
    assert numeric["candidate_source_xlsx_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert missing["candidate_values"] == []
    assert missing["candidate_source_xlsx_sha256"] == numeric["candidate_source_xlsx_sha256"]
    assert "candidate_values" not in boolean
    assert text_bool["candidate_values"] == [False]
    assert text_bool["candidate_value_evidence"] == [{"value": False, "effective_type": "bool", "raw_cell_types": ["str"]}]
    assert grey["candidate_values"] == []


def test_catalog_resolver_does_not_mutate_input():
    catalog = {"scenarios": [{"scenario_id": "baseline", "kind": "baseline", "default_kind": "unresolved"}]}
    result = resolve_catalog(catalog, "unused")
    assert "candidate_values" not in catalog["scenarios"][0]
    assert result["scenarios"][0] == catalog["scenarios"][0]
