"""PARITY LANE C 2026-10-06 — BB_BOUNCE_ENTRY_TF entry SOURCE (vec compute_reentry_blocks B BB_BOUNCE_ENTRY_{tf}).

Vec: real v12_quick_engine.compute_reentry_blocks. Live: tradier_filter_tf_twins.bb_bounce_source_fires on the new
tradier_indicators key _lc_bb_pct_b_{tf}_prev (= vec np.roll prev row). Opens through the real OPEN path (no tuple return).
"""
import numpy as np
import pytest

import v12_quick_engine as V
from tests._laneC_parity_helpers import L, TFS, rng_npz_p3, prev_row, tm_source


def _cfg(tf):
    c = V.QuickConfig()
    c.MODE = "tradier"
    c.apply_tradier_defaults()
    c.BB_BOUNCE_ENTRY_TF = tf
    c.BB_BREAKOUT_ENTRY_TF = "OFF"
    return c


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_live_equals_vec(tf, is_long):
    n = 400
    npz = rng_npz_p3(n, 33)
    npz[f"bb_pct_b_{tf}"] = np.clip(np.random.default_rng(5).uniform(-0.1, 1.1, n), 0, 1)
    blocks = V.compute_reentry_blocks(npz, n, is_long, _cfg(tf))
    vm = blocks[f"BB_BOUNCE_ENTRY_{tf}"]
    pv = prev_row(npz[f"bb_pct_b_{tf}"])
    fires = 0
    for i in range(1, n):
        d = {f"bb_pct_b_{tf}": npz[f"bb_pct_b_{tf}"][i], f"_lc_bb_pct_b_{tf}_prev": pv[i], f"bb_lower_{tf}": npz[f"bb_lower_{tf}"][i], f"bb_upper_{tf}": npz[f"bb_upper_{tf}"][i]}
        lf, _ = L.bb_bounce_source_fires(d.get, is_long, tf)
        assert lf == bool(vm[i]), (i, d)
        fires += int(lf)
    assert 0 < fires < n


def test_off_inert_and_open_path():
    assert L.bb_bounce_source_fires({"bb_pct_b_1h": 0.3, "_lc_bb_pct_b_1h_prev": 0.1, "bb_lower_1h": 1.0}.get, True, "OFF") == (False, "")
    src = tm_source()
    assert "_ftf_twins.bb_bounce_source_fires(_lc_get, is_long" in src
    assert "_twin_entry_ports_b.bb_bounce_entry(lambda _k, _d: _cfg_auto(_k, _d)" not in src
