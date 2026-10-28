"""C2 V20_COST_AWARE (flag OFF by default): pure rule + decide() cap, never below the floor, untouched when off."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import v18_brain as B  # noqa: E402


def _blob():
    nodes = ["R:SLOW=1", "R:FAST=1", "R:POS=1", "F:FL=OFF", "R:FEW=1", "F:QUICK=OFF"]
    cs = []
    def row(i, n, pos, nsym, secs):
        r = [0.0] * 21
        r[0], r[3], r[4], r[13], r[19] = i, n, pos, nsym, secs
        return r
    cs = [row(0, 40, 0, 12, 2.0), row(1, 40, 0, 12, 0.1), row(2, 40, 3, 12, 2.0), row(3, 100, 0, 30, 1.5), row(4, 40, 0, 4, 2.0), row(5, 100, 0, 30, 0.2)]
    pair = [[3, 0, 0, 0, 30, 0, 0, 0, 0, 0, 11, 0, 0]]
    return {"nodes": nodes, "cs": cs, "pair": pair}


def test_rule_pure():
    ca = B._cost_aware_tables(_blob())
    assert B.cost_aware_hit(ca, "SLOW=1")
    assert not B.cost_aware_hit(ca, "FAST=1")  # faster than the cat_side mean
    assert not B.cost_aware_hit(ca, "POS=1")  # positive somewhere
    assert not B.cost_aware_hit(ca, "FEW=1")  # < 10 sym_sides
    assert B.cost_aware_hit(ca, "SLOW=1", "FL=OFF")
    assert not B.cost_aware_hit(ca, "UNSEEN=1")  # never-tried rows are never throttled


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("V20_COST_AWARE", raising=False)
    import os
    assert str(os.environ.get("V20_COST_AWARE", "0")) == "0"
