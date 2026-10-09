"""Go-live sym-side blocklist (USER 2026-10-09): corrupt-data boards must REFUSE even under --all-finished."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_persym_golive as G

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_blocklist_file_schema():
    d = json.loads((ROOT / "data" / "golive_blocklist.json").read_text())
    assert isinstance(d, dict) and d, "blocklist must be a non-empty dict"
    for ss, b in d.items():
        assert isinstance(b, dict), ss
        assert b.get("reason"), ss
        assert b.get("added"), ss
        assert b.get("until"), ss


def test_load_blocklist_matches_file():
    b = G.load_blocklist()
    assert isinstance(b, dict) and "LEXX_SHORT" in b
    assert "SAWTOOTH" in b["LEXX_SHORT"]["reason"]


def test_load_blocklist_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "ROOT", tmp_path)
    assert G.load_blocklist() == {}
