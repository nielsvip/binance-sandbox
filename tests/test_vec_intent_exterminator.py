"""Zombie exterminator: stale spool files archive, fresh flow untouched."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live_twins"))
import vec_intent_exterminator as EX  # noqa: E402
import vec_driven as VD  # noqa: E402

NOW = 1792617600.0


def _spool(tmp_path, bars):
    spool = tmp_path / "intents"
    spool.mkdir()
    for name, bar in bars:
        recs = [
            {"intent_id": f"id-{name}", "ss": "X_LONG", "type": "OPEN", "bar_ts": bar}
        ]
        (spool / f"{name}.json").write_text(json.dumps({"intents": recs}))
    (spool / "broken.json").write_text("{not json")
    (spool / "nobar.json").write_text(
        json.dumps({"intents": [{"intent_id": "x", "ss": "Y_LONG", "type": "OPEN"}]})
    )
    return spool


def test_classify_splits_by_age(tmp_path):
    spool = _spool(tmp_path, [("old", NOW - 100000), ("fresh", NOW - 100)])
    stale, fresh, odd = EX.classify(spool=spool, max_age_s=86400, now=NOW)
    assert [Path(f).name for f, _ in stale] == ["old.json"]
    assert [Path(f).name for f in fresh] == ["fresh.json"]
    assert sorted(Path(f).name for f in odd) == ["broken.json", "nobar.json"]


def test_apply_moves_and_manifests(tmp_path):
    spool = _spool(tmp_path, [("old", NOW - 100000), ("fresh", NOW - 100)])
    arch = tmp_path / "archive"
    res = EX.exterminate(max_age_s=86400, apply=True, spool=spool, archive=arch)
    assert res["moved"] == ["old.json"]
    assert not (spool / "old.json").exists()
    assert (spool / "fresh.json").exists()
    assert (arch / "old.json").exists()
    man = (arch / "MANIFEST.jsonl").read_text()
    assert "old.json" in man


def test_dry_run_moves_nothing(tmp_path):
    spool = _spool(tmp_path, [("old", NOW - 100000)])
    res = EX.exterminate(
        max_age_s=86400, apply=False, spool=spool, archive=tmp_path / "archive"
    )
    assert res["moved"] == []
    assert (spool / "old.json").exists()


def test_load_intents_horizon(tmp_path, monkeypatch):
    spool = tmp_path / "intents"
    spool.mkdir()
    ancient = {
        "intent_id": "zz",
        "ss": "A_LONG",
        "type": "OPEN",
        "bar_ts": NOW - 30 * 86400,
    }
    recent = {"intent_id": "rr", "ss": "B_LONG", "type": "OPEN", "bar_ts": NOW - 60}
    nobar = {"intent_id": "nn", "ss": "C_LONG", "type": "OPEN"}
    (spool / "z.json").write_text(json.dumps(ancient))
    (spool / "r.json").write_text(json.dumps(recent))
    (spool / "n.json").write_text(json.dumps(nobar))
    monkeypatch.setenv("VEC_DRIVEN_INTENT_HORIZON_S", "0")
    assert sorted(i["intent_id"] for i in VD.load_intents(spool)) == ["nn", "rr", "zz"]
    monkeypatch.setenv("VEC_DRIVEN_INTENT_HORIZON_S", "86400")
    got = sorted(i["intent_id"] for i in VD.load_intents(spool))
    assert got == ["nn", "rr"]
