"""trb_size_guardian thresholds (USER 2026-10-11): WARN at 3x base, CRIT at 4x, HARD at symbol cap."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trb_size_guardian as g


def test_classify_bands():
    assert g.classify(500, 500, 2500)[0] == "OK"
    assert g.classify(1499, 500, 2500)[0] == "OK"
    lvl, m = g.classify(1500, 500, 2500)
    assert (lvl, m) == ("WARN", 3.0)
    lvl, m = g.classify(2000, 500, 2500)
    assert (lvl, m) == ("CRIT", 4.0)
    lvl, m = g.classify(2500, 500, 2500)
    assert (lvl, m) == ("HARD", 5.0)
    lvl, m = g.classify(7200, 500, 2500)
    assert lvl == "HARD" and m == 14.4


def test_classify_bad_inputs_fail_quiet():
    assert g.classify(None, 500)[0] == "OK"
    assert g.classify(1500, 0)[0] == "OK"
    assert g.classify("x", 500)[0] == "OK"


def test_cfg_reads_live_tradier_values():
    cfg = g._load_cfg()
    assert cfg["base"] == 500.0
    assert cfg["symbol_cap"] == 2500.0
    assert cfg["max_long"] == 12500.0
    assert {"META", "MSTR", "VT"} <= cfg["mandatory"]
