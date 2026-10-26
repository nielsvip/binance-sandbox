import json
import time

from tools import v15_best_first as best


def test_order_is_frozen_per_round_but_side_permission_is_live(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "v15_symside_ledger.json").write_text(json.dumps({"sym_sides": {
        "AAAUSDT_LONG": {"final_gain": 80}, "BBBUSDT_LONG": {"final_gain": 4}}}))
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(["men:AAAUSDT_LONG", "men:BBBUSDT_LONG"]))
    (tmp_path / "symbols_men_long.json").write_text(json.dumps(["AAAUSDT", "BBBUSDT"]))
    (tmp_path / "symbols_men_short.json").write_text("[]")
    first = best.publish(tmp_path, "run37")
    assert first["allowed"] == ["AAAUSDT_LONG", "BBBUSDT_LONG"]
    assert sorted(first["allowed"], key=lambda ss: best.rank_key(ss, first))[0] == "AAAUSDT_LONG"
    (tmp_path / "data" / "v15_symside_ledger.json").write_text(json.dumps({"sym_sides": {
        "AAAUSDT_LONG": {"final_gain": -50}, "BBBUSDT_LONG": {"final_gain": 99}}}))
    (tmp_path / "symbols_men_long.json").write_text(json.dumps(["BBBUSDT"]))
    again = best.publish(tmp_path, "run37")
    assert again["gains"] == first["gains"]
    assert again["allowed"] == ["BBBUSDT_LONG"]


def test_next_round_uses_completed_gain_from_immediately_previous_run(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/v15_symside_ledger.json").write_text(json.dumps({"sym_sides": {
        "AAAUSDT_LONG": {"final_gain": 3}, "BBBUSDT_LONG": {"final_gain": 7}}}))
    (tmp_path / "data/v15_best_first_latest_gains.json").write_text(json.dumps({
        "round": "v15_run36_20261024", "gains": {"AAAUSDT_LONG": {"gain": 80, "mtime": 4}}}))
    snap = best.publish(tmp_path, "v15_run37_20261026")
    assert snap["gains"]["AAAUSDT_LONG"] == 80
    assert snap["gains"]["BBBUSDT_LONG"] == 7
