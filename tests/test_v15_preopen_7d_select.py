import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v15_preopen_7d_select import select


def test_only_traded_and_tradeable_published_sides_enter_7d_queue(tmp_path):
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(["fin:AAAUSDT_SHORT", "fin:BBBUSD_SHORT"]))
    (tmp_path / "stock_symbols.json").write_text(json.dumps(["XYZ"]))
    for side in ("long", "short"):
        (tmp_path / f"symbols_trb_{side}.json").write_text("[]")
    for account, ss, age in (("fin", "AAAUSDT_SHORT", 1), ("fin", "BBBUSD_SHORT", 8),
                             ("trc", "XYZ_LONG", 2)):
        folder = tmp_path / "data" / "history" / account
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.fromtimestamp(1_800_000_000 - age * 86400, dt.timezone.utc).isoformat()
        (folder / f"{ss}.jsonl").write_text(json.dumps({"ts": stamp, "type": "OPEN"}) + "\n")
    source = {"sym_sides": {ss: {"overrides": {"X": True}, "manifest_ok": True}
                            for ss in ("AAAUSDT_SHORT", "BBBUSD_SHORT", "XYZ_LONG", "ABSENT_LONG")}}
    got = select(tmp_path, 1_800_000_000, source)
    assert got["accounts"] == {"fin": ["AAAUSDT_SHORT"], "trc": ["XYZ_LONG"]}
    assert got["_meta"]["n"] == 2
