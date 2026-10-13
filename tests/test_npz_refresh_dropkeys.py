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
