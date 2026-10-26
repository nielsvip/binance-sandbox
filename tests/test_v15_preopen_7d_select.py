import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v15_preopen_7d_select import select


def test_only_traded_and_tradeable_published_sides_enter_7d_queue(tmp_path):
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(["fin:AAAUSDT_SHORT", "fin:BBBUSD_SHORT", "fin:CCCUSDT_SHORT"]))
    (tmp_path / "stock_symbols.json").write_text(json.dumps(["XYZ"]))
    for side in ("long", "short"):
        (tmp_path / f"symbols_trb_{side}.json").write_text("[]")
    for account, ss, age in (("fin", "AAAUSDT_SHORT", 1), ("fin", "BBBUSD_SHORT", 8),
                             ("fin", "CCCUSDT_SHORT", 1),
                             ("trc", "XYZ_LONG", 2)):
        folder = tmp_path / "data" / "history" / account
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.fromtimestamp(1_800_000_000 - age * 86400, dt.timezone.utc).isoformat()
        (folder / f"{ss}.jsonl").write_text(json.dumps({"ts": stamp, "type": "OPEN"}) + "\n")
    source = {"sym_sides": {ss: {"overrides": {"X": True}, "manifest_ok": True}
                            for ss in ("AAAUSDT_SHORT", "BBBUSD_SHORT", "XYZ_LONG", "ABSENT_LONG")}}
    source["sym_sides"]["CCCUSDT_SHORT"] = {"overrides": {"X": True}}
    got = select(tmp_path, 1_800_000_000, source)
    assert got["accounts"] == {"fin": ["AAAUSDT_SHORT"], "trc": ["XYZ_LONG"]}
    assert got["_meta"]["n"] == 2


def test_empty_stock_side_uses_universe_even_if_other_side_has_symbols(tmp_path):
    (tmp_path / "tradeable_keys.json").write_text("[]")
    (tmp_path / "stock_symbols.json").write_text(json.dumps(["XYZ"]))
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(["ABC"]))
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    folder = tmp_path / "data" / "history" / "trc"
    folder.mkdir(parents=True)
    (folder / "XYZ_SHORT.jsonl").write_text(json.dumps({"ts": 1_800_000_000, "type": "OPEN"}) + "\n")
    source = {"sym_sides": {"XYZ_SHORT": {"overrides": {"X": True}, "manifest_ok": True},
                             "XYZ_LONG": {"overrides": {"X": True}}}}
    got = select(tmp_path, 1_800_000_000, source)
    assert got["accounts"]["trc"] == ["XYZ_SHORT"]
