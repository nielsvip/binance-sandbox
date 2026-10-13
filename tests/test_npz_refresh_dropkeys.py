"""npz_bar_refresh drop-list pins.

The tail builder cannot extend every legacy NPZ key; keys proven reader-free
are dropped on refresh instead of raising ValueError (which aborts the whole
symbol and freezes its NPZ = stock-lag disaster). Regression pins so the
dropped families are never accidentally removed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import npz_bar_refresh as R


def test_tup_grid_droppable():
    grid = ["t_up_1h", "tco_1h", "tcu_1h", "t_up_4h", "tco_4h", "tcu_4h", "t_up_D", "tco_D", "tcu_D"]
    for k in grid:
        assert k in R.REFRESH_DROP_KEYS, f"{k} missing -> COE/DHR/EOG-class stocks ERROR every cycle"


def test_legacy_families_still_droppable():
    for k in ["macd_M", "adx_M", "stdev_edge_15m", "stdev_slope_D", "tco_15m", "linearity_1h", "slope_close_4h"]:
        assert k in R.REFRESH_DROP_KEYS


def test_pilot_syms_extend_universes(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    (tmp_path / "data" / "daily_universe").mkdir(parents=True)
    (tmp_path / "data" / "daily_universe" / "last_good.json").write_text(json.dumps({"crypto": ["ONDOUSDT_SHORT", "ADBEUSDT"], "stocks": ["FOO", "BAR_LONG"]}))
    (tmp_path / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "lifecycle_pilot" / "PLTR_LONG_v14_progress.json").write_text("{}")
    (tmp_path / "symbols_tradier.json").write_text(json.dumps(["FOO", "AAPL"]))
    (tmp_path / "symbols_active.json").write_text(json.dumps([]))
    monkeypatch.setattr(R, "ROOT", Path(tmp_path))
    ps = R.pilot_syms()
    assert {"ONDOUSDT", "ADBEUSDT", "FOO", "BAR", "PLTR"} <= ps
    assert "ONDOUSDT" in R.universe() and "ADBEUSDT" in R.universe()
    assert "FOO" not in R.universe()
    st = R.universe_stocks()
    assert "FOO" in st and "BAR" in st and "PLTR" in st and "AAPL" in st
    assert "ONDOUSDT" not in st
    assert len(st) == len(set(st))
    monkeypatch.setattr(R, "ROOT", Path(tmp_path / "emptyroot"))
    assert R.pilot_syms() == set()
