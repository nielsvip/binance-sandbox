import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v15_preopen_7d_select import select


def test_only_traded_and_tradeable_published_sides_enter_7d_queue(tmp_path):
    now = dt.datetime(2026, 10, 26, 2, tzinfo=dt.timezone.utc).timestamp()
    (tmp_path / "symbols_men_long.json").write_text("[]")
    (tmp_path / "symbols_men_short.json").write_text(json.dumps(["AAAUSDT", "BBBUSD", "CCCUSDT"]))
    (tmp_path / "symbols_fin_long.json").write_text("[]")
    (tmp_path / "symbols_fin_short.json").write_text(json.dumps(["OTHERUSDT"]))
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(["XYZ"]))
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(["fin:AAAUSDT_SHORT", "fin:BBBUSD_SHORT", "fin:CCCUSDT_SHORT"]))
    for account, ss, age in (("fin", "AAAUSDT_SHORT", 1), ("fin", "BBBUSD_SHORT", 8),
                             ("fin", "CCCUSDT_SHORT", 1),
                             ("trc", "XYZ_LONG", 2)):
        folder = tmp_path / "data" / "history" / account
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.fromtimestamp(now - age * 86400, dt.timezone.utc).isoformat()
        (folder / f"{ss}.jsonl").write_text(json.dumps({"ts": stamp, "type": "OPEN"}) + "\n")
    source = {"sym_sides": {ss: {"overrides": {"X": True}, "manifest_ok": True}
                            for ss in ("AAAUSDT_SHORT", "BBBUSD_SHORT", "XYZ_LONG", "ABSENT_LONG")}}
    source["sym_sides"]["CCCUSDT_SHORT"] = {"overrides": {"X": True}}
    got = select(tmp_path, now, source)
    assert got["accounts"] == {"fin": ["AAAUSDT_SHORT"], "trc": ["XYZ_LONG"]}
    assert got["_meta"]["n"] == 2


def test_empty_trb_side_does_not_invent_trc_tradeability(tmp_path):
    (tmp_path / "symbols_men_long.json").write_text("[]")
    (tmp_path / "symbols_men_short.json").write_text("[]")
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(["ABC"]))
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    folder = tmp_path / "data" / "history" / "trc"
    folder.mkdir(parents=True)
    (folder / "XYZ_SHORT.jsonl").write_text(json.dumps({"ts": 1_800_000_000, "type": "OPEN"}) + "\n")
    source = {"sym_sides": {"XYZ_SHORT": {"overrides": {"X": True}, "manifest_ok": True},
                             "XYZ_LONG": {"overrides": {"X": True}}}}
    got = select(tmp_path, dt.datetime(2026, 10, 26, 2, tzinfo=dt.timezone.utc).timestamp(), source)
    assert got["accounts"]["trc"] == []
