import json

from tools.ensure_trb_live_symbol_side_floor import enforce


def test_floor_repairs_with_label_and_rollback(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(["A", "B"]))
    (tmp_path / "symbols_trb_short.json").write_text(json.dumps(["C", "D"]))
    (tmp_path / "data/persym_final_book.json").write_text(json.dumps({
        "tradeable": {"A_LONG": {"strategy": "keep"}},
        "disabled": ["B_LONG", "C_SHORT"],
    }))
    receipt = enforce(tmp_path, floor=3, repair=True)
    book = json.loads((tmp_path / "data/persym_final_book.json").read_text())
    assert receipt["status"] == "PASS"
    assert receipt["enabled_before"] == 1
    assert receipt["enabled_after"] == 3
    assert book["tradeable"]["A_LONG"]["strategy"] == "keep"
    assert len(receipt["added_bh_fallback_keys"]) == 2
    assert all(book["tradeable"][key]["gate_src"].startswith("USER_") for key in receipt["added_bh_fallback_keys"])
    assert (tmp_path / receipt["rollback_path"]).is_file()


def test_floor_observation_does_not_modify_book(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "symbols_trb_long.json").write_text('["A"]')
    (tmp_path / "symbols_trb_short.json").write_text('[]')
    path = tmp_path / "data/persym_final_book.json"
    path.write_text('{"tradeable": {}, "disabled": []}')
    before = path.read_bytes()
    receipt = enforce(tmp_path, floor=1, repair=False)
    assert receipt["status"] == "BELOW_FLOOR"
    assert path.read_bytes() == before
