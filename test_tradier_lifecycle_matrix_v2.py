import importlib.util
from pathlib import Path
import sys

import numpy as np

spec = importlib.util.spec_from_file_location("v2", Path("tools/tradier_lifecycle_matrix_v2.py"))
v2 = importlib.util.module_from_spec(spec); sys.modules["v2"] = v2; spec.loader.exec_module(v2)


def fixture():
    n = 80
    close = np.linspace(100, 118, n); op = close.copy(); op[3:] += .2
    wt2 = np.zeros(n); wt1 = np.full(n, -1.0); wt1[2:] = 1
    return {"open": op, "high": close + 1, "low": close - 1, "close": close,
            "wt1": wt1, "wt2": wt2, "wt1h": wt1, "wt2h": wt2,
            "dch": np.full(n, 99.), "dcl": np.full(n, 90.)}


def test_whole_share_and_next_bar_execution():
    x = v2.simulate(fixture(), "LONG", v2.RECIPES[0])
    assert x["whole_share"] is True
    assert x["execution"] == "NEXT_5M_OPEN"
    assert x["trades"] >= 1


def test_side_aware_bh():
    close = fixture()["close"]
    assert v2.side_bh(close, "LONG") > 0
    assert v2.side_bh(close, "SHORT") < 0


def test_recipes_cover_lifecycle_paths():
    assert len(v2.RECIPES) >= 10
    assert any(r.augment for r in v2.RECIPES)
    assert any(r.reduce for r in v2.RECIPES)
    assert any(r.reentry for r in v2.RECIPES)
