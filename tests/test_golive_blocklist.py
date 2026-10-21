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


def test_catastrophic_loser_never_arms_until_replaced():
    # USER 2026-10-21: 1000FLOKIUSDT_LONG Oct-4 board (gain -32.40, 352
    # trades, max_dd 33.27) must never arm live until a qualified gain>0
    # slow replacement publishes.
    b = G.load_blocklist()["1000FLOKIUSDT_LONG"]
    assert "-32.40" in b["reason"] and "917890022535f17a3df33c404317c448" in b["reason"]
    assert "gain>0 replacement" in b["until"]
