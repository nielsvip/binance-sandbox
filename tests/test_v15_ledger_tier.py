"""USER 2026-10-14: pilot WINNER = previous round's best gainers (ledger W), a near-free lookup — no progress parsing."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import v15_pilot as P


def test_ledger_tier_name():
    assert P.ledger_tier_name("W", 0.5) == "WINNER"
    assert P.ledger_tier_name("M", 30) == "MEDIOCRE"
    assert P.ledger_tier_name("L", 0.5) == "LOST_CAUSE"
    assert P.ledger_tier_name("L", 12) == "LOSER"


def test_ledger_entry(tmp_path, monkeypatch):
    f = tmp_path / "l.json"
    f.write_text(json.dumps({"sym_sides": {"BTCUSDC_LONG": {"sched_tier": "W", "potential": 31.2, "tier_why": "x"}, "BAD_LONG": {"sched_tier": "?"}}}))
    monkeypatch.setenv("V15_LEDGER_JSON", str(f))
    P._LEDGER_CACHE.clear()
    assert P._ledger_entry("BTCUSDC_LONG")["potential"] == 31.2
    assert P._ledger_entry("BAD_LONG") is None
    assert P._ledger_entry("NOPE_SHORT") is None
    tier, meta = P.symside_tier(5.0, 1.0, 40, 31.2, force=P.ledger_tier_name("W", 1.0))
    assert tier == "WINNER"
    P._LEDGER_CACHE.clear()
