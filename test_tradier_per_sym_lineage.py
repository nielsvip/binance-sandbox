import json

from tools.tradier_per_sym_lineage import build_receipt


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_live_lineage_uses_side_lists_and_respects_enable_precedence(tmp_path):
    _write(tmp_path / "symbols_trb_long.json", ["AAA", "CCC"])
    _write(tmp_path / "symbols_trb_short.json", ["BBB"])
    _write(tmp_path / "data/persym_final_book.json", {
        "tradeable": {"AAA_LONG": {"size_cap": 2.0}},
        "disabled": ["BBB_SHORT"],
        "note": "legacy book",
    })
    _write(tmp_path / "data/hourly_reconfig/trb/active_config.json", {
        "AAA_LONG": {"sample_tag": "CURRENT", "overrides": {"WT_DC_ENTRY_THRESHOLD": 35}},
    })
    _write(tmp_path / "data/hourly_reconfig/per_sym_active_config.json", {
        "BBB_SHORT": {"sample_tag": "DIAGNOSTIC", "overrides": {"WT_DC_ENTRY_THRESHOLD": 55}},
    })
    _write(tmp_path / "data/full_recipe_live_config.json", {
        "schema": "full-recipe-live-config-v1",
        "entries": {"BBB_SHORT": {"overrides": {"SHORT_ENABLED": True}}},
    })

    receipt = build_receipt(tmp_path)
    rows = {row["key"]: row for row in receipt["rows"]}
    assert list(rows) == ["AAA_LONG", "CCC_LONG", "BBB_SHORT"]
    assert rows["AAA_LONG"]["enable_precedence_source"] == "PERSYM_FINAL_BOOK_TRADEABLE"
    assert rows["AAA_LONG"]["effective_enable"] is True
    assert rows["CCC_LONG"]["effective_enable"] is None
    assert rows["BBB_SHORT"]["enable_precedence_source"] == "FULL_RECIPE_EXACT"
    assert rows["BBB_SHORT"]["effective_enable"] is True
    assert receipt["summary"]["allowlist_rows_with_diagnostic_layer"] == 1
